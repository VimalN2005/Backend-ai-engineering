# Day 73: Enterprise Practical Exercises: Distributed Transactions, Idempotency & Clocks

This workbook contains **3 enterprise-grade distributed systems exercises**. Each exercise presents a real-world scenario, precise architectural requirements, and a complete, tested reference implementation.

---

## Exercise 1: Vector Clock Conflict Resolver with Sibling Reconciliation

### Scenario
An active-active multi-region distributed document store allows simultaneous offline and online edits across 3 regions (US-East, EU-West, AP-South). You must build a **Vector Clock Conflict Resolver** that detects when two updates branch concurrently ($V_1 \parallel V_2$) and applies an application-level reconciliation merge rather than silently dropping data via naive Last-Write-Wins.

### Requirements
1. Implement a `VectorClock` model supporting pairwise comparison:
   - `PRECEDES` ($V_1 < V_2$)
   - `SUCCEEDS` ($V_1 > V_2$)
   - `EQUAL` ($V_1 == V_2$)
   - `CONCURRENT` ($V_1 \parallel V_2$)
2. Implement a `DocumentStore`:
   - If an incoming write dominates the stored version (`SUCCEEDS`), overwrite stored version.
   - If incoming write is older (`PRECEDES`), ignore it.
   - If incoming write is `CONCURRENT`, store both as **conflicting siblings** and trigger a 3-way merge that unions item sets and merges vector clocks.

### Solution

```python
"""
Exercise 1 Solution: Vector Clock Conflict Resolver & Sibling Reconciliation
"""
from __future__ import annotations
import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Set, Tuple


class CausalOrder(str, Enum):
    PRECEDES = "PRECEDES"
    SUCCEEDS = "SUCCEEDS"
    EQUAL = "EQUAL"
    CONCURRENT = "CONCURRENT"


@dataclass
class DocumentVersion:
    doc_id: str
    tags: Set[str]
    vector_clock: Dict[str, int]


class VectorClockEngine:
    @staticmethod
    def compare(v1: Dict[str, int], v2: Dict[str, int]) -> CausalOrder:
        all_nodes = set(v1.keys()).union(set(v2.keys()))
        v1_has_less = False
        v2_has_less = False

        for node in all_nodes:
            val1 = v1.get(node, 0)
            val2 = v2.get(node, 0)
            if val1 < val2:
                v1_has_less = True
            elif val1 > val2:
                v2_has_less = True

        if v1_has_less and not v2_has_less:
            return CausalOrder.PRECEDES
        elif v2_has_less and not v1_has_less:
            return CausalOrder.SUCCEEDS
        elif not v1_has_less and not v2_has_less:
            return CausalOrder.EQUAL
        else:
            return CausalOrder.CONCURRENT

    @staticmethod
    def merge_clocks(v1: Dict[str, int], v2: Dict[str, int]) -> Dict[str, int]:
        all_nodes = set(v1.keys()).union(set(v2.keys()))
        return {node: max(v1.get(node, 0), v2.get(node, 0)) for node in all_nodes}


class ReplicatedDocumentStore:
    def __init__(self):
        # doc_id -> list of active siblings (1 if clean, >1 if concurrent conflict)
        self.documents: Dict[str, List[DocumentVersion]] = {}

    def write_document(self, incoming: DocumentVersion) -> str:
        doc_id = incoming.doc_id
        if doc_id not in self.documents:
            self.documents[doc_id] = [incoming]
            return "INITIAL_WRITE"

        current_siblings = self.documents[doc_id]
        new_siblings: List[DocumentVersion] = []
        is_concurrent_with_all = True

        for existing in current_siblings:
            order = VectorClockEngine.compare(incoming.vector_clock, existing.vector_clock)
            if order == CausalOrder.SUCCEEDS:
                # Incoming dominates existing; drop existing
                is_concurrent_with_all = False
            elif order == CausalOrder.PRECEDES or order == CausalOrder.EQUAL:
                # Incoming is obsolete; do not store
                return "OBSOLETE_IGNORED"
            else:
                # Concurrent with this sibling
                new_siblings.append(existing)

        new_siblings.append(incoming)
        self.documents[doc_id] = new_siblings

        if len(new_siblings) > 1:
            return "CONFLICT_DETECTED_SIBLINGS_STORED"
        return "DOMINANT_UPDATE_APPLIED"

    def reconcile_siblings(self, doc_id: str, reconciling_node: str) -> DocumentVersion:
        """Merges all conflicting siblings by unioning tags and merging vector clocks."""
        siblings = self.documents.get(doc_id, [])
        if len(siblings) <= 1:
            return siblings[0] if siblings else None

        merged_tags: Set[str] = set()
        merged_clock: Dict[str, int] = {}

        for sib in siblings:
            merged_tags.update(sib.tags)
            merged_clock = VectorClockEngine.merge_clocks(merged_clock, sib.vector_clock)

        # Increment reconciling node's clock entry
        merged_clock[reconciling_node] = merged_clock.get(reconciling_node, 0) + 1
        resolved = DocumentVersion(doc_id=doc_id, tags=merged_tags, vector_clock=merged_clock)
        self.documents[doc_id] = [resolved]
        return resolved


# --- Verification Test ---
if __name__ == "__main__":
    store = ReplicatedDocumentStore()

    # Step 1: Base document written at Node A: tags={'python'}
    doc_v1 = DocumentVersion("doc_42", tags={"python"}, vector_clock={"A": 1, "B": 0})
    store.write_document(doc_v1)
    assert len(store.documents["doc_42"]) == 1

    # Step 2: Node B concurrently adds 'distributed' without knowing Node A's next write
    # Node A concurrently adds 'systems' without knowing Node B's write
    doc_from_b = DocumentVersion("doc_42", tags={"python", "distributed"}, vector_clock={"A": 1, "B": 1})
    doc_from_a = DocumentVersion("doc_42", tags={"python", "systems"},     vector_clock={"A": 2, "B": 0})

    status_b = store.write_document(doc_from_b)
    status_a = store.write_document(doc_from_a)

    print(f"Write from B status: {status_b}")
    print(f"Write from A status: {status_a}")

    # Both siblings must be retained because clocks are concurrent!
    assert len(store.documents["doc_42"]) == 2
    assert status_a == "CONFLICT_DETECTED_SIBLINGS_STORED"
    print("Conflict successfully detected! Storing both active siblings.")

    # Step 3: Reconcile siblings
    resolved_doc = store.reconcile_siblings("doc_42", reconciling_node="A")
    print(f"Reconciled Document: Tags={resolved_doc.tags}, Vector={resolved_doc.vector_clock}")

    assert resolved_doc.tags == {"python", "distributed", "systems"}
    assert resolved_doc.vector_clock == {"A": 3, "B": 1}
    assert len(store.documents["doc_42"]) == 1
    print("\nExercise 1: Vector Clock conflict detection and sibling reconciliation verified!")
```

---

## Exercise 2: Two-Phase Commit (2PC) with Timeout & Participant Rollback

### Scenario
A multi-database financial settlement transaction transfers \$10,000 between Bank A and Bank B. The coordinator must execute a Two-Phase Commit. If any participant experiences a network timeout or vetoes during Phase 1 (Prepare), the coordinator must reliably broadcast a `GLOBAL_ABORT` and verify that all prepared resources are safely rolled back without leaking locks.

### Requirements
1. Implement a `TwoPhaseCommitManager` that manages 3 participant databases: `LedgerDB`, `AccountDB`, `AuditDB`.
2. Implement Phase 1 (`PREPARE`):
   - Query each participant with a `timeout_sec`.
   - If any participant fails or times out, abort the entire transaction.
3. Implement Phase 2:
   - If all participants vote `COMMIT`: send `GLOBAL_COMMIT` and confirm.
   - If any participant votes `ABORT` or times out: send `GLOBAL_ABORT` to all participants that prepared.
4. Verify that in the event of an abort, zero uncommitted ledger rows remain.

### Solution

```python
"""
Exercise 2 Solution: Two-Phase Commit Coordinator with Timeout & Rollback
"""
from __future__ import annotations
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple


class ParticipantVote(str, Enum):
    COMMIT = "COMMIT"
    ABORT = "ABORT"
    TIMEOUT = "TIMEOUT"


class TransactionalParticipant:
    def __init__(self, name: str, should_timeout: bool = False, should_veto: bool = False):
        self.name = name
        self.should_timeout = should_timeout
        self.should_veto = should_veto
        self.held_locks: Dict[str, dict] = {}
        self.committed_records: Dict[str, dict] = {}
        self.rolled_back: List[str] = []

    def prepare(self, tx_id: str, payload: dict) -> ParticipantVote:
        if self.should_timeout:
            return ParticipantVote.TIMEOUT
        if self.should_veto:
            return ParticipantVote.ABORT
        # Acquire local row lock and prepare
        self.held_locks[tx_id] = payload
        return ParticipantVote.COMMIT

    def commit(self, tx_id: str) -> None:
        payload = self.held_locks.pop(tx_id, None)
        if payload:
            self.committed_records[tx_id] = payload

    def rollback(self, tx_id: str) -> None:
        self.held_locks.pop(tx_id, None)
        self.rolled_back.append(tx_id)


class TwoPhaseCommitCoordinator:
    def __init__(self, participants: List[TransactionalParticipant]):
        self.participants = participants
        self.execution_trace: List[str] = []

    def execute_transaction(self, tx_id: str, payload: dict) -> Tuple[bool, str]:
        self.execution_trace.clear()
        self.execution_trace.append(f"START_TX: {tx_id}")

        votes: Dict[str, ParticipantVote] = {}
        # Phase 1: Prepare
        for p in self.participants:
            vote = p.prepare(tx_id, payload)
            votes[p.name] = vote
            self.execution_trace.append(f"PREPARE: {p.name} voted {vote.value}")

        # Check Unanimity
        all_committed = all(v == ParticipantVote.COMMIT for v in votes.values())

        # Phase 2: Decision
        if all_committed:
            self.execution_trace.append("DECISION: GLOBAL_COMMIT")
            for p in self.participants:
                p.commit(tx_id)
            return True, "2PC Transaction Committed"
        else:
            self.execution_trace.append("DECISION: GLOBAL_ABORT")
            for p in self.participants:
                p.rollback(tx_id)
            return False, "2PC Transaction Aborted due to participant failure/timeout"


# --- Verification Test ---
if __name__ == "__main__":
    # Test Scenario: AuditDB experiences network timeout during Prepare
    ledger = TransactionalParticipant("LedgerDB", should_timeout=False, should_veto=False)
    account = TransactionalParticipant("AccountDB", should_timeout=False, should_veto=False)
    audit = TransactionalParticipant("AuditDB", should_timeout=True, should_veto=False)  # Times out

    coordinator = TwoPhaseCommitCoordinator([ledger, account, audit])
    success, msg = coordinator.execute_transaction("tx_settle_999", {"amount": 10000})

    print("2PC Execution Result:", success, "-", msg)
    print("Coordinator Trace:")
    for t in coordinator.execution_trace:
        print(f"  {t}")

    # Assertions
    assert success is False
    assert "tx_settle_999" in ledger.rolled_back
    assert "tx_settle_999" in account.rolled_back
    assert len(ledger.committed_records) == 0, "No records must be committed on abort"
    assert len(account.held_locks) == 0, "Locks must be released on abort"

    print("\nExercise 2: 2PC Timeout and Global Rollback verified successfully!")
```

---

## Exercise 3: Production Idempotent API Middleware with Fencing Tokens

### Scenario
An e-commerce payment endpoint must handle unstable mobile network retries. You must build an **Idempotent API Middleware** with an atomic lock, cached response return, and monotonic **Fencing Tokens** that prevent stale workers from executing writes after lock timeouts.

### Requirements
1. Implement an `IdempotencyMiddleware`:
   - Inspects `Idempotency-Key` header.
   - Atomically transitions key state: `EMPTY` $\to$ `IN_PROGRESS` $\to$ `COMPLETED`.
   - Returns cached response on retries.
   - Returns `HTTP 409 Conflict` if the same key is received while `IN_PROGRESS`.
2. Implement a `FencedDatabase`:
   - Increments a monotonic integer fencing token on lock acquisition.
   - Rejects writes that provide a token strictly less than the highest token processed.

### Solution

```python
"""
Exercise 3 Solution: Idempotent Middleware with Fencing Tokens
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class KeyState(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


@dataclass
class IdempotencyEntry:
    state: KeyState
    response: Optional[dict] = None
    created_at: float = field(default_factory=time.time)


class FencedDataStore:
    def __init__(self):
        self.highest_token: int = 0
        self.records: Dict[str, Any] = {}

    def commit(self, key: str, value: Any, token: int) -> bool:
        if token < self.highest_token:
            return False  # Stale token: rejected!
        self.highest_token = token
        self.records[key] = value
        return True


class ProductionIdempotentService:
    def __init__(self, datastore: FencedDataStore):
        self.datastore = datastore
        self.idempotency_table: Dict[str, IdempotencyEntry] = {}
        self.token_counter: int = 0

    def process_payment(self, idempotency_key: str, order_id: str, amount: float) -> Tuple[int, dict]:
        # 1. Atomic Check & Set Lock
        entry = self.idempotency_table.get(idempotency_key)
        if entry is not None:
            if entry.state == KeyState.IN_PROGRESS:
                return 409, {"error": "Concurrent request in progress"}
            if entry.state == KeyState.COMPLETED:
                return 200, entry.response  # Idempotent replay

        # Lock acquired
        self.idempotency_table[idempotency_key] = IdempotencyEntry(state=KeyState.IN_PROGRESS)

        # 2. Acquire Monotonic Fencing Token
        self.token_counter += 1
        current_fencing_token = self.token_counter

        # 3. Execute business logic & write to storage with fencing token
        success = self.datastore.commit(
            key=order_id,
            value={"amount": amount, "status": "PAID"},
            token=current_fencing_token
        )

        if not success:
            return 500, {"error": "Write rejected due to stale fencing token"}

        # 4. Cache final response and mark COMPLETED
        final_response = {"order_id": order_id, "amount": amount, "status": "SUCCESS"}
        self.idempotency_table[idempotency_key] = IdempotencyEntry(
            state=KeyState.COMPLETED,
            response=final_response
        )
        return 200, final_response


# --- Verification Test ---
if __name__ == "__main__":
    db = FencedDataStore()
    service = ProductionIdempotentService(db)
    idem_key = "idemp-txn-88192"

    # 1. Initial Call -> Succeeded
    code1, resp1 = service.process_payment(idem_key, "ord_55", 250.0)
    assert code1 == 200
    assert resp1["status"] == "SUCCESS"
    print(f"First request succeeded: {resp1}")

    # 2. Duplicate retry with same idempotency key -> returns cached response
    code2, resp2 = service.process_payment(idem_key, "ord_55", 250.0)
    assert code2 == 200
    assert resp2 == resp1
    print(f"Duplicate request returned cached payload: {resp2}")

    # 3. Simulate Stale Fencing Token attempt
    # Advance token to 10
    db.highest_token = 10
    # Attempt write with stale token 3
    stale_write = db.commit("ord_56", {"amount": 100}, token=3)
    assert stale_write is False
    print("Fenced database successfully rejected stale token write!")

    print("\nExercise 3: Production Idempotent Middleware & Fencing Tokens verified successfully!")
```
