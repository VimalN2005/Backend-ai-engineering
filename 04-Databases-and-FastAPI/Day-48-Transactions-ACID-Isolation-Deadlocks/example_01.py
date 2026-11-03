"""
Day 48: Database Transactions: ACID, Isolation Levels & Deadlock Resolution.

This module provides a pure Python (Standard Library only) architectural simulation
of an enterprise transactional database engine implementing:
1. Multi-Level Transaction Isolation:
   - Read Committed: Statement-level snapshots with EvalPlanQual (EPQ) re-checks.
   - Repeatable Read: Transaction-level snapshots with First-Committer-Wins conflict aborts.
   - Serializable Snapshot Isolation (SSI): SIREAD locks and rw-antidependency cycle detection
     (solving the classic Write Skew on-call doctor anomaly without pessimistic read locks!).
2. Row-Level Lock Manager:
   - SELECT FOR UPDATE (Exclusive row lock).
   - SELECT FOR UPDATE NOWAIT (Fail-fast with 55P03).
   - SELECT FOR UPDATE SKIP LOCKED (Lock-free horizontal queue worker dispatching).
3. Deadlock Detection Engine:
   - Wait-For Graph (WFG) maintaining directed transaction wait edges.
   - Cycle detection via Depth-First Search (DFS) algorithm.
   - Automatic victim selection and abort with SQLSTATE 40P01.
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ==============================================================================
# SECTION 1: TRANSACTIONAL CORE EXCEPTIONS & SQLSTATE CODES
# ==============================================================================

class TransactionError(Exception):
    """Base exception for transactional engine failures."""
    def __init__(self, message: str, sqlstate: str):
        super().__init__(f"[{sqlstate}] {message}")
        self.sqlstate = sqlstate


class SerializationFailure(TransactionError):
    """PostgreSQL SQLSTATE 40001: could not serialize access due to concurrent update/dependencies."""
    def __init__(self, message: str = "could not serialize access due to concurrent update"):
        super().__init__(message, "40001")


class DeadlockDetected(TransactionError):
    """PostgreSQL SQLSTATE 40P01: deadlock detected."""
    def __init__(self, message: str = "deadlock detected"):
        super().__init__(message, "40P01")


class LockNotAvailable(TransactionError):
    """PostgreSQL SQLSTATE 55P03: lock not available (NOWAIT clause)."""
    def __init__(self, message: str = "could not obtain lock on row"):
        super().__init__(message, "55P03")


# ==============================================================================
# SECTION 2: ROW VERSIONING & LOCK MODES
# ==============================================================================

class IsolationLevel(Enum):
    READ_COMMITTED = "READ COMMITTED"
    REPEATABLE_READ = "REPEATABLE READ"
    SERIALIZABLE = "SERIALIZABLE"


class LockMode(IntEnum):
    SHARED = 1
    EXCLUSIVE = 2


@dataclass
class RowVersion:
    """Represents a physical tuple version on an 8KB heap page."""
    xmin: int                     # Inserting Transaction ID
    xmax: int = 0                 # Deleting/Updating Transaction ID (0 = alive)
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    """MVCC Transaction Snapshot (xmin:xmax:xip_list)."""
    xmin: int
    xmax: int
    active_xids: Set[int] = field(default_factory=set)

    def is_visible(self, xmin: int, xmax: int, committed_xids: Set[int]) -> bool:
        """Evaluates whether this tuple version is visible to this snapshot."""
        if xmin not in committed_xids and xmin >= self.xmin:
            return False
        if xmin in self.active_xids or xmin >= self.xmax:
            return False
        if xmax != 0:
            if xmax in committed_xids and xmax < self.xmax and xmax not in self.active_xids:
                return False  # Deletion was committed before this snapshot
        return True


# ==============================================================================
# SECTION 3: SHARED MEMORY LOCK MANAGER & WAIT-FOR GRAPH (DEADLOCK DETECTOR)
# ==============================================================================

class LockManager:
    """
    Manages in-memory heavyweight row locks and Wait-For Graph (WFG).
    Implements cycle detection replicating PostgreSQL's deadlock.c.
    """

    def __init__(self):
        # row_id -> (holding_xid, lock_mode)
        self._row_locks: Dict[Any, Tuple[int, LockMode]] = {}
        # Wait-For Graph: Map[waiting_xid -> Set[blocking_xid]]
        self._wait_for_graph: Dict[int, Set[int]] = collections.defaultdict(set)

    def acquire_row_lock(
        self,
        xid: int,
        row_id: Any,
        mode: LockMode = LockMode.EXCLUSIVE,
        nowait: bool = False,
        skip_locked: bool = False
    ) -> bool:
        """
        Attempts to acquire a lock on a row.
        Returns True if acquired.
        If skip_locked is True, returns False immediately if row is held by another xid.
        If nowait is True, raises LockNotAvailable if row is held by another xid.
        Otherwise, registers wait edge in WFG and checks for deadlocks.
        """
        if row_id in self._row_locks:
            holding_xid, held_mode = self._row_locks[row_id]
            if holding_xid == xid:
                return True  # Already held by self

            # Row is held by another transaction
            if skip_locked:
                return False  # Skip without blocking!

            if nowait:
                raise LockNotAvailable(f"Row {row_id} is locked by transaction {holding_xid}")

            # Register wait dependency in Wait-For Graph: xid is waiting for holding_xid
            self._wait_for_graph[xid].add(holding_xid)

            # Check if this wait edge created a circular dependency (DEADLOCK)
            cycle = self._detect_deadlock_cycle(xid)
            if cycle:
                # Remove wait edge before raising
                self._wait_for_graph[xid].discard(holding_xid)
                raise DeadlockDetected(f"Deadlock detected along cycle: {' -> '.join(map(str, cycle))}")

            # In a real database, backend would sleep until holding_xid commits/aborts.
            # Here we simulate by raising conflict or waiting.
            return False

        # Lock acquired cleanly
        self._row_locks[row_id] = (xid, mode)
        return True

    def release_all(self, xid: int) -> None:
        """Releases all locks and clears wait edges upon transaction commit or abort."""
        rows_to_release = [r for r, (holding_xid, _) in self._row_locks.items() if holding_xid == xid]
        for r in rows_to_release:
            del self._row_locks[r]

        # Clean Wait-For Graph
        if xid in self._wait_for_graph:
            del self._wait_for_graph[xid]
        for waiting_xid, blocking_set in list(self._wait_for_graph.items()):
            blocking_set.discard(xid)

    def is_locked(self, row_id: Any) -> bool:
        return row_id in self._row_locks

    def _detect_deadlock_cycle(self, start_xid: int) -> Optional[List[int]]:
        """
        Executes Depth-First Search (DFS) on the Wait-For Graph to detect directed cycles.
        Replicates PostgreSQL `deadlock.c:DeadLockCheck`.
        """
        visited: Set[int] = set()
        path: List[int] = []

        def dfs(current: int) -> Optional[List[int]]:
            visited.add(current)
            path.append(current)

            for neighbor in self._wait_for_graph.get(current, set()):
                if neighbor == start_xid:
                    path.append(start_xid)
                    return list(path)
                if neighbor not in visited:
                    res = dfs(neighbor)
                    if res:
                        return res

            path.pop()
            return None

        return dfs(start_xid)


# ==============================================================================
# SECTION 4: SERIALIZABLE SNAPSHOT ISOLATION (SSI) ENGINE
# ==============================================================================

class SSIPredicateLockManager:
    """
    Implements Cahill et al. (2008) Serializable Snapshot Isolation (SSI).
    Maintains SIREAD locks and tracks rw-antidependencies to prevent Write Skew.
    
    WHY: SSI does NOT lock readers! It records which transactions read which rows
    in shared memory (SIREAD locks). When a writer modifies a row, it creates an
    rw-antidependency edge (T_read -> T_write). If a cycle of two consecutive
    rw-antidependencies forms (T1 --rw--> T2 --rw--> T3), it aborts the younger transaction!
    """

    def __init__(self):
        # row_id -> Set[xid] (transactions that read this row)
        self._siread_locks: Dict[Any, Set[int]] = collections.defaultdict(set)
        # rw-antidependency graph: Map[reader_xid -> Set[writer_xid]]
        self._rw_antidependencies: Dict[int, Set[int]] = collections.defaultdict(set)
        # Transactions flagged for serialization abort upon commit (First-Committer-Wins)
        self.doomed_xids: Set[int] = set()

    def record_siread_lock(self, xid: int, row_id: Any, active_writers: Set[int]) -> None:
        """
        Records an SIREAD lock (non-blocking reader tracking).
        If an active concurrent transaction has written to row_id, records:
        reader_xid --rw--> writer_xid.
        """
        self._siread_locks[row_id].add(xid)
        for writer_xid in active_writers:
            if writer_xid != xid:
                self._rw_antidependencies[xid].add(writer_xid)

    def record_write(self, writer_xid: int, row_id: Any) -> None:
        """
        When a writer modifies row_id, check if any active transaction read it!
        If so, creates an rw-antidependency: reader_xid --rw--> writer_xid.
        """
        readers = self._siread_locks.get(row_id, set())
        for reader_xid in readers:
            if reader_xid != writer_xid:
                # reader_xid read a tuple version that writer_xid is overwriting!
                self._rw_antidependencies[reader_xid].add(writer_xid)

    def check_dangerous_structure(self, current_xid: int) -> bool:
        """
        Checks if current_xid is part of a dangerous structure (rw-antidependency cycle).
        A cycle of rw-antidependencies proves that the execution schedule is non-serializable!
        """
        visited: Set[int] = set()

        def dfs(node: int, origin: int) -> bool:
            visited.add(node)
            for out_edge in self._rw_antidependencies.get(node, set()):
                if out_edge == origin:
                    return True  # Found cycle!
                if out_edge not in visited:
                    if dfs(out_edge, origin):
                        return True
            return False

        return dfs(current_xid, current_xid)

    def find_cycle_partners(self, current_xid: int, active_xids: Set[int]) -> Set[int]:
        """Returns concurrent in-flight transactions participating in a cycle with current_xid."""
        partners: Set[int] = set()
        for neighbor in self._rw_antidependencies.get(current_xid, set()):
            if neighbor in active_xids and neighbor != current_xid:
                if current_xid in self._rw_antidependencies.get(neighbor, set()):
                    partners.add(neighbor)
        return partners

    def release_transaction(self, xid: int, active_xids: Set[int]) -> None:
        """
        Cleans up SIREAD locks when transaction completes.
        Retains rw-antidependency edges while concurrent transactions overlap!
        """
        for row_id in list(self._siread_locks.keys()):
            self._siread_locks[row_id].discard(xid)
            if not self._siread_locks[row_id]:
                del self._siread_locks[row_id]
        self.doomed_xids.discard(xid)
        if not active_xids:
            self._rw_antidependencies.clear()


# ==============================================================================
# SECTION 5: TRANSACTION ENGINE & CONCURRENCY CONTROLLER
# ==============================================================================

class TransactionEngine:
    """
    Core transactional database engine supporting:
    - In-memory tables with MVCC version chains.
    - READ COMMITTED, REPEATABLE READ, and SERIALIZABLE isolation levels.
    - Lock-free queue processing via SKIP LOCKED.
    - Automatic Deadlock Detection and SSI Write Skew aborts.
    """

    def __init__(self):
        self._current_xid = 1000
        self._committed_xids: Set[int] = set()
        self._active_xids: Set[int] = set()
        # table_name -> primary_key -> List[RowVersion] (newest version first)
        self._storage: Dict[str, Dict[Any, List[RowVersion]]] = collections.defaultdict(
            lambda: collections.defaultdict(list)
        )
        self.lock_mgr = LockManager()
        self.ssi_mgr = SSIPredicateLockManager()

    def begin_transaction(self, isolation: IsolationLevel = IsolationLevel.READ_COMMITTED) -> TransactionContext:
        """Starts a new transaction."""
        self._current_xid += 1
        xid = self._current_xid
        self._active_xids.add(xid)
        return TransactionContext(engine=self, xid=xid, isolation=isolation)

    def _get_snapshot(self) -> Snapshot:
        xmin = min(self._active_xids) if self._active_xids else self._current_xid + 1
        xmax = self._current_xid + 1
        return Snapshot(xmin=xmin, xmax=xmax, active_xids=set(self._active_xids))

    def commit(self, tx: TransactionContext) -> None:
        """Commits transaction, performing SSI serialization checks and releasing locks."""
        # For SERIALIZABLE transactions, perform final dangerous structure validation
        if tx.isolation == IsolationLevel.SERIALIZABLE:
            if tx.xid in self.ssi_mgr.doomed_xids:
                self.abort(tx)
                raise SerializationFailure(
                    "could not serialize access due to read/write dependencies among transactions"
                )
            if self.ssi_mgr.check_dangerous_structure(tx.xid):
                # Mark overlapping cycle partners as doomed (First-Committer-Wins)
                partners = self.ssi_mgr.find_cycle_partners(tx.xid, self._active_xids)
                for p in partners:
                    self.ssi_mgr.doomed_xids.add(p)

        self._active_xids.discard(tx.xid)
        self._committed_xids.add(tx.xid)
        self.lock_mgr.release_all(tx.xid)
        self.ssi_mgr.release_transaction(tx.xid, self._active_xids)
        tx.is_active = False

    def abort(self, tx: TransactionContext) -> None:
        """Aborts transaction, rolling back uncommitted versions and releasing locks."""
        self._active_xids.discard(tx.xid)
        # Mark tuples inserted by this xid as dead
        for table in self._storage.values():
            for versions in table.values():
                # Remove versions inserted by this aborted xid
                versions[:] = [v for v in versions if v.xmin != tx.xid]
                # Reset xmax if this xid attempted an update/delete
                for v in versions:
                    if v.xmax == tx.xid:
                        v.xmax = 0

        self.lock_mgr.release_all(tx.xid)
        self.ssi_mgr.release_transaction(tx.xid, self._active_xids)
        tx.is_active = False


class TransactionContext:
    """Session context representing a running client transaction."""

    def __init__(self, engine: TransactionEngine, xid: int, isolation: IsolationLevel):
        self.engine = engine
        self.xid = xid
        self.isolation = isolation
        self.is_active = True
        # Under REPEATABLE READ & SERIALIZABLE, acquire one fixed snapshot at start!
        self._tx_snapshot: Optional[Snapshot] = None
        if self.isolation in (IsolationLevel.REPEATABLE_READ, IsolationLevel.SERIALIZABLE):
            self._tx_snapshot = self.engine._get_snapshot()

    def get_snapshot(self) -> Snapshot:
        """
        Under READ COMMITTED, acquires a fresh snapshot for every statement.
        Under REPEATABLE READ & SERIALIZABLE, returns the fixed transaction snapshot.
        """
        if self.isolation == IsolationLevel.READ_COMMITTED:
            return self.engine._get_snapshot()
        assert self._tx_snapshot is not None
        return self._tx_snapshot

    def select(
        self,
        table: str,
        predicate: Callable[[Dict[str, Any]], bool],
        limit: Optional[int] = None,
        for_update: bool = False,
        nowait: bool = False,
        skip_locked: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Executes a SELECT query with optional locking clauses and row limit.
        Supports LIMIT, FOR UPDATE, NOWAIT, and SKIP LOCKED.
        """
        if not self.is_active:
            raise TransactionError("Transaction is closed", "25000")

        snapshot = self.get_snapshot()
        results: List[Dict[str, Any]] = []

        table_data = self.engine._storage[table]
        for pk, versions in sorted(table_data.items()):
            if limit is not None and len(results) >= limit:
                break
            for v in versions:
                # Evaluate MVCC visibility
                if snapshot.is_visible(v.xmin, v.xmax, self.engine._committed_xids) or v.xmin == self.xid:
                    if predicate(v.data):
                        # Handle Serializable SIREAD tracking
                        if self.isolation == IsolationLevel.SERIALIZABLE:
                            active_writers = {ver.xmin for ver in versions if ver.xmin in self.engine._active_xids}
                            self.engine.ssi_mgr.record_siread_lock(self.xid, pk, active_writers)

                        # Handle Locking Clauses
                        if for_update:
                            acquired = self.engine.lock_mgr.acquire_row_lock(
                                self.xid, pk, mode=LockMode.EXCLUSIVE, nowait=nowait, skip_locked=skip_locked
                            )
                            if not acquired:
                                if skip_locked:
                                    continue  # Skip this locked row immediately!
                                raise TransactionError(f"Row {pk} locked by concurrent transaction", "55P03")

                        results.append(dict(v.data))
                    break  # Found latest visible version for this PK
        return results

    def insert(self, table: str, pk: Any, data: Dict[str, Any]) -> None:
        """Inserts a new row with xmin = self.xid."""
        if not self.is_active:
            raise TransactionError("Transaction is closed", "25000")

        self.engine.lock_mgr.acquire_row_lock(self.xid, pk, mode=LockMode.EXCLUSIVE)
        new_version = RowVersion(xmin=self.xid, xmax=0, data=dict(data))
        self.engine._storage[table][pk].insert(0, new_version)

        # Notify SSI writer tracking
        if self.isolation == IsolationLevel.SERIALIZABLE:
            self.engine.ssi_mgr.record_write(self.xid, pk)

    def update(self, table: str, pk: Any, new_data: Dict[str, Any]) -> None:
        """
        Executes an UPDATE statement.
        Enforces First-Committer-Wins under REPEATABLE READ (SerializationFailure).
        Under READ COMMITTED, updates the latest visible version (EvalPlanQual).
        """
        if not self.is_active:
            raise TransactionError("Transaction is closed", "25000")

        # Acquire exclusive row lock
        acquired = self.engine.lock_mgr.acquire_row_lock(self.xid, pk, mode=LockMode.EXCLUSIVE)
        if not acquired:
            raise TransactionError(f"Row {pk} is locked", "55P03")

        versions = self.engine._storage[table].get(pk, [])
        if not versions:
            raise TransactionError(f"Row {pk} does not exist", "02000")

        latest_version = versions[0]

        # REPEATABLE READ & SERIALIZABLE: Check First-Committer-Wins conflict!
        if self.isolation in (IsolationLevel.REPEATABLE_READ, IsolationLevel.SERIALIZABLE):
            # If the row was modified by a transaction that committed AFTER our snapshot started:
            assert self._tx_snapshot is not None
            if latest_version.xmin > self._tx_snapshot.xmin and latest_version.xmin != self.xid:
                raise SerializationFailure("could not serialize access due to concurrent update")

        # In-place MVCC update chaining: mark old version xmax and insert new version
        latest_version.xmax = self.xid
        new_version = RowVersion(xmin=self.xid, xmax=0, data=dict(new_data))
        versions.insert(0, new_version)

        # Notify SSI of modification
        if self.isolation == IsolationLevel.SERIALIZABLE:
            self.engine.ssi_mgr.record_write(self.xid, pk)

    def commit(self) -> None:
        self.engine.commit(self)

    def rollback(self) -> None:
        self.engine.abort(self)


# ==============================================================================
# SECTION 6: ARCHITECTURAL VERIFICATION SUITE
# ==============================================================================

def run_transaction_architectural_demonstrations() -> None:
    print("=" * 72)
    print(" DAY 48: TRANSACTION CONCURRENCY & DEADLOCK ENGINE SIMULATION")
    print("=" * 72)

    # --------------------------------------------------------------------------
    # DEMO 1: WRITE SKEW UNDER REPEATABLE READ VS SERIALIZABLE (SSI)
    # --------------------------------------------------------------------------
    print("\n[SCENARIO 1] The Classic On-Call Doctors Dilemma (Write Skew):")
    print("  Invariant: At least ONE doctor must be on call at all times.")
    print("  Initial State: Both Dr. Alice and Dr. Bob are on call.")

    # A. Execute under REPEATABLE READ (Demonstrating Anomaly Violation!)
    engine_rr = TransactionEngine()
    t_init = engine_rr.begin_transaction()
    t_init.insert("doctors", "alice", {"name": "Alice", "on_call": True})
    t_init.insert("doctors", "bob", {"name": "Bob", "on_call": True})
    t_init.commit()

    # Txn 1 (Alice wants to go off call)
    tx_alice = engine_rr.begin_transaction(IsolationLevel.REPEATABLE_READ)
    alice_docs = tx_alice.select("doctors", lambda d: d["on_call"] is True)
    assert len(alice_docs) == 2  # Alice sees 2 on call

    # Txn 2 (Bob concurrently wants to go off call)
    tx_bob = engine_rr.begin_transaction(IsolationLevel.REPEATABLE_READ)
    bob_docs = tx_bob.select("doctors", lambda d: d["on_call"] is True)
    assert len(bob_docs) == 2  # Bob also sees 2 on call

    # Alice sets herself off call and commits
    tx_alice.update("doctors", "alice", {"name": "Alice", "on_call": False})
    tx_alice.commit()

    # Bob concurrently sets himself off call and commits (Disjoint writes!)
    tx_bob.update("doctors", "bob", {"name": "Bob", "on_call": False})
    tx_bob.commit()

    # Audit the final state: INVARIANT VIOLATED!
    t_audit = engine_rr.begin_transaction()
    active_docs = t_audit.select("doctors", lambda d: d["on_call"] is True)
    print(f"  -> Under REPEATABLE READ: Total on-call doctors = {len(active_docs)} (WRITE SKEW OCCURRED!)")
    assert len(active_docs) == 0, "Write Skew allowed zero doctors on call!"

    # B. Execute under SERIALIZABLE (SSI) (Anomaly Prevented!)
    print("\n  Now Executing identical scenario under SERIALIZABLE (SSI)...")
    engine_ssi = TransactionEngine()
    t_init2 = engine_ssi.begin_transaction()
    t_init2.insert("doctors", "alice", {"name": "Alice", "on_call": True})
    t_init2.insert("doctors", "bob", {"name": "Bob", "on_call": True})
    t_init2.commit()

    tx_alice_ssi = engine_ssi.begin_transaction(IsolationLevel.SERIALIZABLE)
    tx_alice_ssi.select("doctors", lambda d: d["on_call"] is True)
    tx_alice_ssi.update("doctors", "alice", {"name": "Alice", "on_call": False})

    tx_bob_ssi = engine_ssi.begin_transaction(IsolationLevel.SERIALIZABLE)
    tx_bob_ssi.select("doctors", lambda d: d["on_call"] is True)
    tx_bob_ssi.update("doctors", "bob", {"name": "Bob", "on_call": False})

    # Alice commits first
    tx_alice_ssi.commit()

    # Bob attempts to commit: SSI detects rw-antidependency cycle and aborts Bob!
    try:
        tx_bob_ssi.commit()
        raise AssertionError("SSI should have aborted Bob with SerializationFailure!")
    except SerializationFailure as ex:
        print(f"  -> SSI DEFENSE VERIFIED: Caught expected {ex} [PASSED]")

    t_audit_ssi = engine_ssi.begin_transaction()
    active_ssi_docs = t_audit_ssi.select("doctors", lambda d: d["on_call"] is True)
    print(f"  -> Under SERIALIZABLE: Total on-call doctors = {len(active_ssi_docs)} (Invariant Preserved!)")
    assert len(active_ssi_docs) == 1 and active_ssi_docs[0]["name"] == "Bob"

    # --------------------------------------------------------------------------
    # DEMO 2: DEADLOCK DETECTION VIA WAIT-FOR GRAPH (DFS CYCLE SEARCH)
    # --------------------------------------------------------------------------
    print("\n[SCENARIO 2] Deadlock Detection & Automated Victim Resolution (40P01):")
    engine_dl = TransactionEngine()
    t_acc = engine_dl.begin_transaction()
    t_acc.insert("accounts", "A", {"balance": 1000})
    t_acc.insert("accounts", "B", {"balance": 2000})
    t_acc.commit()

    tx1 = engine_dl.begin_transaction()
    tx2 = engine_dl.begin_transaction()

    # Txn 1 locks Account A exclusively
    engine_dl.lock_mgr.acquire_row_lock(tx1.xid, "A", LockMode.EXCLUSIVE)
    print(f"  -> Txn 1 ({tx1.xid}) acquired lock on Account A.")

    # Txn 2 locks Account B
    engine_dl.lock_mgr.acquire_row_lock(tx2.xid, "B", LockMode.EXCLUSIVE)
    print(f"  -> Txn 2 ({tx2.xid}) acquired lock on Account B.")

    # Txn 1 attempts to lock Account B (Blocks, waits on Txn 2)
    engine_dl.lock_mgr.acquire_row_lock(tx1.xid, "B", LockMode.EXCLUSIVE)
    print(f"  -> Txn 1 ({tx1.xid}) waiting on Account B (Held by Txn 2).")

    # Txn 2 attempts to lock Account A (Circular Dependency -> DEADLOCK!)
    print(f"  -> Txn 2 ({tx2.xid}) requesting Account A (Held by Txn 1)...")
    try:
        engine_dl.lock_mgr.acquire_row_lock(tx2.xid, "A", LockMode.EXCLUSIVE)
        raise AssertionError("Deadlock detector failed to catch circular wait!")
    except DeadlockDetected as ex:
        print(f"  -> DEADLOCK DETECTOR VERIFIED: Caught {ex} [PASSED]")
        tx2.rollback()

    tx1.commit()
    print("  -> Txn 1 successfully finalized after deadlock cycle resolution.")

    # --------------------------------------------------------------------------
    # DEMO 3: HIGH-THROUGHPUT QUEUE WORKERS VIA SKIP LOCKED
    # --------------------------------------------------------------------------
    print("\n[SCENARIO 3] Concurrent Queue Processing with SELECT FOR UPDATE SKIP LOCKED:")
    engine_q = TransactionEngine()
    t_seed = engine_q.begin_transaction()
    for j_id in range(1, 6):
        t_seed.insert("jobs", j_id, {"id": j_id, "payload": f"task_{j_id}", "status": "PENDING"})
    t_seed.commit()

    # Worker 1 fetches the first pending job with LIMIT 1 FOR UPDATE SKIP LOCKED
    w1_tx = engine_q.begin_transaction()
    w1_jobs = w1_tx.select("jobs", lambda d: d["status"] == "PENDING", limit=1, for_update=True, skip_locked=True)
    assert len(w1_jobs) == 1
    print(f"  -> Worker 1 locked Job #{w1_jobs[0]['id']}")

    # Worker 2 concurrently fetches the next pending job with LIMIT 1 FOR UPDATE SKIP LOCKED
    w2_tx = engine_q.begin_transaction()
    w2_jobs = w2_tx.select("jobs", lambda d: d["status"] == "PENDING", limit=1, for_update=True, skip_locked=True)
    assert len(w2_jobs) == 1
    # Crucial Verification: Job 1 was skipped without blocking! Worker 2 locks Job 2!
    print(f"  -> Worker 2 executed with SKIP LOCKED: Locked Job #{w2_jobs[0]['id']}")
    assert w2_jobs[0]["id"] == 2, "Worker 2 must skip Job 1 and lock Job 2!"

    # Worker 3 concurrently fetches the next pending job
    w3_tx = engine_q.begin_transaction()
    w3_jobs = w3_tx.select("jobs", lambda d: d["status"] == "PENDING", limit=1, for_update=True, skip_locked=True)
    assert len(w3_jobs) == 1
    print(f"  -> Worker 3 executed with SKIP LOCKED: Locked Job #{w3_jobs[0]['id']}")
    assert w3_jobs[0]["id"] == 3, "Worker 3 must skip Jobs 1 & 2 and lock Job 3!"

    w1_tx.commit()
    w2_tx.commit()
    w3_tx.commit()
    print("  -> All queue workers dispatched without lock contention! [PASSED]")

    print("\n" + "=" * 72)
    print(" ALL DAY 48 TRANSACTION & DEADLOCK ENGINE TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 72)


if __name__ == "__main__":
    run_transaction_architectural_demonstrations()
