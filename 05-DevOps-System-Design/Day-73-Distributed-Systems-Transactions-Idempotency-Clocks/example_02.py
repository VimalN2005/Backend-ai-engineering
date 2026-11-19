"""
Day 73: Two-Phase Commit (2PC), Idempotency & Fencing Token Engine
==================================================================
Architecture & Principles Demonstrated:
1. Two-Phase Commit (2PC) Protocol:
   - Coordinator and Cohort Participants.
   - Phase 1 (Prepare / Vote) and Phase 2 (Global Commit or Global Abort).
   - Atomic multi-resource consistency with automatic rollback on cohort veto.
2. Distributed Idempotency Barrier:
   - IETF Idempotency-Key implementation with status state machine:
     NOT_FOUND -> PROCESSING -> COMPLETED.
   - Cached response return for retried requests; concurrency conflict handling (409).
3. Monotonic Fencing Tokens:
   - Defense against distributed lock expiration during GC pauses / network stalls.
   - Storage-side fencing token validation preventing split-brain overwrites.
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# =====================================================================
# 1. Two-Phase Commit (2PC) Protocol Engine
# =====================================================================

class Vote(str, Enum):
    VOTE_COMMIT = "VOTE_COMMIT"
    VOTE_ABORT = "VOTE_ABORT"


class CohortParticipant:
    """Simulated transactional participant (e.g., Database or Service)."""
    def __init__(self, name: str, can_commit: bool = True):
        self.name = name
        self.can_commit = can_commit
        self.prepared_transactions: Dict[str, Any] = {}
        self.committed_transactions: Dict[str, Any] = {}
        self.aborted_transactions: Dict[str, Any] = {}

    def prepare(self, tx_id: str, data: Any) -> Vote:
        """Phase 1: Validate constraints and acquire locks."""
        if not self.can_commit:
            return Vote.VOTE_ABORT
        self.prepared_transactions[tx_id] = data
        return Vote.VOTE_COMMIT

    def commit(self, tx_id: str) -> bool:
        """Phase 2: Finalize commit."""
        data = self.prepared_transactions.pop(tx_id, None)
        if data is not None:
            self.committed_transactions[tx_id] = data
            return True
        return False

    def abort(self, tx_id: str) -> bool:
        """Phase 2: Rollback."""
        data = self.prepared_transactions.pop(tx_id, None)
        self.aborted_transactions[tx_id] = data
        return True


class TwoPhaseCommitCoordinator:
    """2PC Coordinator executing Phase 1 (Prepare) and Phase 2 (Commit/Abort)."""
    def __init__(self, participants: List[CohortParticipant]):
        self.participants = participants
        self.coordinator_log: List[str] = []

    def execute_transaction(self, tx_id: str, tx_payload: Dict[str, Any]) -> Tuple[bool, str]:
        self.coordinator_log.clear()
        self.coordinator_log.append(f"START_2PC: {tx_id}")

        # Phase 1: PREPARE (Voting)
        votes: Dict[str, Vote] = {}
        for p in self.participants:
            self.coordinator_log.append(f"PREPARE_REQUEST -> {p.name}")
            vote = p.prepare(tx_id, tx_payload)
            votes[p.name] = vote
            self.coordinator_log.append(f"VOTE_RECEIVED from {p.name}: {vote.value}")

        # Evaluate Consensus: ALL must vote VOTE_COMMIT
        all_voted_commit = all(v == Vote.VOTE_COMMIT for v in votes.values())

        # Phase 2: GLOBAL DECISION
        if all_voted_commit:
            self.coordinator_log.append("DECISION: GLOBAL_COMMIT")
            for p in self.participants:
                p.commit(tx_id)
            return True, "Transaction globally committed"
        else:
            self.coordinator_log.append("DECISION: GLOBAL_ABORT")
            for p in self.participants:
                p.abort(tx_id)
            return False, "Transaction globally aborted"


# =====================================================================
# 2. Distributed Idempotency Barrier with Response Caching
# =====================================================================

class IdempotencyStatus(str, Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


@dataclass
class IdempotencyRecord:
    status: IdempotencyStatus
    response_payload: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)


class DistributedIdempotencyBarrier:
    """
    Implements IETF Idempotency-Key protocol (e.g., Redis-backed lock and cache).
    Protects against duplicate execution from retries.
    """
    def __init__(self, ttl_sec: float = 86400.0):
        self.ttl_sec = ttl_sec
        self.store: Dict[str, IdempotencyRecord] = {}

    def start_or_fetch(self, idempotency_key: str) -> Tuple[bool, Optional[Dict[str, Any]], int]:
        """
        Returns: (is_new_request, cached_response, http_status_code)
        """
        record = self.store.get(idempotency_key)
        if record is None:
            # Atomic lock (SET idemp_key 'PROCESSING' NX EX ttl)
            self.store[idempotency_key] = IdempotencyRecord(status=IdempotencyStatus.PROCESSING)
            return True, None, 200

        if record.status == IdempotencyStatus.PROCESSING:
            # Concurrent in-flight request with same key
            return False, {"error": "Conflict: Request currently processing"}, 409

        if record.status == IdempotencyStatus.COMPLETED:
            # Idempotent replay: return cached response
            return False, record.response_payload, 200

        return False, {"error": "Unknown status"}, 500

    def complete(self, idempotency_key: str, response_payload: Dict[str, Any]) -> None:
        """Mark idempotency record as completed and cache final payload."""
        self.store[idempotency_key] = IdempotencyRecord(
            status=IdempotencyStatus.COMPLETED,
            response_payload=response_payload
        )


# =====================================================================
# 3. Monotonic Fencing Tokens (Lock Lease Expiration Defense)
# =====================================================================

class DistributedLockManager:
    """Lock manager that issues monotonically increasing fencing tokens."""
    def __init__(self):
        self.current_token: int = 0
        self.active_owner: Optional[str] = None

    def acquire_lock(self, client_id: str) -> int:
        """Grants lock and increments fencing token."""
        self.current_token += 1
        self.active_owner = client_id
        return self.current_token


class FencedStorageService:
    """
    Storage layer verifying fencing tokens to prevent split-brain writes
    from clients that experienced Stop-The-World GC pauses.
    """
    def __init__(self):
        self.highest_token_seen: int = 0
        self.data_store: Dict[str, Any] = {}

    def write(self, key: str, value: Any, fencing_token: int) -> bool:
        """Only accepts writes if fencing_token >= highest_token_seen."""
        if fencing_token < self.highest_token_seen:
            print(f"[FENCING REJECTION] Rejected write with stale token {fencing_token}! Highest is {self.highest_token_seen}.")
            return False

        self.highest_token_seen = fencing_token
        self.data_store[key] = value
        print(f"[FENCING ACCEPTED] Accepted write with valid token {fencing_token}. Data updated.")
        return True


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_two_phase_commit():
    print("\n--- 1. TWO-PHASE COMMIT (2PC) PROTOCOL DEMO ---")
    p1 = CohortParticipant("AccountDB", can_commit=True)
    p2 = CohortParticipant("InventoryDB", can_commit=True)
    p3 = CohortParticipant("AuditLog", can_commit=True)

    coordinator = TwoPhaseCommitCoordinator([p1, p2, p3])

    # 1. Happy Path: All vote commit
    success, msg = coordinator.execute_transaction("tx_101", {"transfer": 500})
    print(f"2PC Happy Path Result: {success} - {msg}")
    assert success is True
    assert "tx_101" in p1.committed_transactions
    assert "tx_101" in p2.committed_transactions
    assert "tx_101" in p3.committed_transactions

    # 2. Veto Path: 1 cohort votes abort (e.g., Inventory out of stock)
    p2.can_commit = False  # Inventory fails
    abort_success, abort_msg = coordinator.execute_transaction("tx_102", {"transfer": 200})
    print(f"2PC Veto Path Result: {abort_success} - {abort_msg}")
    assert abort_success is False
    assert "tx_102" in p1.aborted_transactions
    assert "tx_102" not in p1.committed_transactions
    assert "tx_102" not in p3.committed_transactions
    print("2PC successfully rolled back all cohorts on participant veto!")


def demo_idempotency_barrier():
    print("\n--- 2. DISTRIBUTED IDEMPOTENCY KEY DEMO ---")
    barrier = DistributedIdempotencyBarrier()
    key = "idem-order-uuid-99"

    # 1. Initial request -> must be allowed to execute
    is_new, cached, status = barrier.start_or_fetch(key)
    assert is_new is True
    assert status == 200
    print(f"Initial request {key} allowed to execute.")

    # 2. Concurrent duplicate request while first is still PROCESSING -> 409 Conflict
    is_new_dup, conflict_resp, status_dup = barrier.start_or_fetch(key)
    assert is_new_dup is False
    assert status_dup == 409
    print(f"Concurrent duplicate intercepted: HTTP {status_dup} ({conflict_resp['error']})")

    # 3. First request finishes and caches response
    final_payload = {"order_id": "ord_99", "amount": 120.0, "status": "CONFIRMED"}
    barrier.complete(key, final_payload)
    print("First request completed and cached response payload.")

    # 4. Subsequent network retry -> returns cached response (skipping side effects)
    is_retry_new, retry_resp, retry_status = barrier.start_or_fetch(key)
    assert is_retry_new is False
    assert retry_status == 200
    assert retry_resp == final_payload
    print(f"Retried request cleanly returned cached payload without re-executing side effects: {retry_resp}")


def demo_fencing_tokens():
    print("\n--- 3. MONOTONIC FENCING TOKENS (GC-PAUSE DEFENSE) DEMO ---")
    lock_mgr = DistributedLockManager()
    storage = FencedStorageService()

    # Client 1 acquires lock -> gets Token 1
    t1 = lock_mgr.acquire_lock("Client1")
    assert t1 == 1
    print(f"Client 1 acquired lock with Fencing Token {t1}")

    # Client 1 suffers 15-second Stop-The-World GC Pause!
    print("Client 1 enters long JVM Stop-The-World GC pause... (Lock lease expires in background)")

    # Client 2 acquires lock -> gets Token 2
    t2 = lock_mgr.acquire_lock("Client2")
    assert t2 == 2
    print(f"Client 2 acquired lock with Fencing Token {t2}")

    # Client 2 writes to storage with Token 2
    write_2 = storage.write("balance", 200, fencing_token=t2)
    assert write_2 is True

    # Client 1 wakes up from GC pause, unaware its lock expired, and attempts to write with Token 1
    print("Client 1 wakes up from GC pause and attempts to write with stale Token 1...")
    write_1 = storage.write("balance", 100, fencing_token=t1)
    assert write_1 is False, "Storage must reject stale fencing token"

    # Verify storage value is Client 2's write (200), not overwritten by Client 1 (100)
    assert storage.data_store["balance"] == 200
    print(f"Split-brain write prevented! Storage data preserved at: {storage.data_store['balance']}")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING 2PC, IDEMPOTENCY & FENCING TOKEN DISTRIBUTED SIMULATOR")
    print("=" * 70)
    demo_two_phase_commit()
    demo_idempotency_barrier()
    demo_fencing_tokens()
    print("\n" + "=" * 70)
    print("ALL 2PC, IDEMPOTENCY & FENCING TOKEN TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
