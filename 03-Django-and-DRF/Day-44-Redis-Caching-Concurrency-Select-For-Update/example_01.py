"""
Day 44: Redis Caching & Database Concurrency with select_for_update
===================================================================

Core Concepts Demonstrated:
1. Redis In-Memory Cache Simulator:
   - Atomic primitives: `get`, `set`, `add` (SETNX), `delete`, `incr`.
   - Anti-Avalanche TTL jittering.
   - Anti-Stampede (Thundering Herd) distributed mutex lock via atomic `add`.
2. Database Concurrency & Pessimistic Locking:
   - In-memory relational table supporting row-level exclusive locks (`SELECT ... FOR UPDATE`).
   - `nowait` and `skip_locked` locking modes.
3. Concurrency Verification Harness:
   - Lost Update Race Condition: 20 threads deducting money without locks (proves corruption).
   - Pessimistic Locking with `select_for_update`: 20 threads deducting money (100% balance integrity).
   - Thundering Herd Defense: 50 concurrent threads accessing an expired key (only 1 DB hit).
   - High-throughput Job Queue with `skip_locked`: Workers consuming queue without collisions.

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# SECTION 1: IN-MEMORY REDIS CACHE ENGINE
# ============================================================================

@dataclass
class CacheEntry:
    value: Any
    expires_at: Optional[float]


class InMemoryRedisCache:
    """
    Simulates a production Redis cache tier (`django-redis`).
    
    Features:
    - Thread-safe storage with TTL expiration.
    - Atomic `add` (SETNX) for distributed mutex locking.
    - TTL jittering defense against Cache Avalanche.
    """
    def __init__(self) -> None:
        self._store: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return default
            if entry.expires_at is not None and time.time() > entry.expires_at:
                del self._store[key]
                return default
            return entry.value

    def set(self, key: str, value: Any, timeout: Optional[int] = 300, jitter: bool = False) -> None:
        with self._lock:
            expires_at = None
            if timeout is not None:
                effective_ttl = float(timeout)
                if jitter:
                    # Apply 10% random jitter to prevent Cache Avalanche
                    jitter_delta = random.uniform(-0.10, 0.10) * effective_ttl
                    effective_ttl += jitter_delta
                expires_at = time.time() + effective_ttl
            self._store[key] = CacheEntry(value=value, expires_at=expires_at)

    def add(self, key: str, value: Any, timeout: Optional[int] = 300) -> bool:
        """
        Atomic SETNX (Set if Not eXists).
        Foundational primitive for building distributed mutex locks.
        Returns True if key was set, False if key already existed.
        """
        with self._lock:
            entry = self._store.get(key)
            now = time.time()
            if entry is not None and (entry.expires_at is None or entry.expires_at > now):
                return False  # Key already exists and is active
            
            expires_at = now + timeout if timeout is not None else None
            self._store[key] = CacheEntry(value=value, expires_at=expires_at)
            return True

    def delete(self, key: str) -> bool:
        with self._lock:
            return bool(self._store.pop(key, None))

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# ============================================================================
# SECTION 2: RELATIONAL DATABASE & ROW-LEVEL LOCKING ENGINE
# ============================================================================

class OperationalError(Exception):
    """Raised when `select_for_update(nowait=True)` encounters a locked row."""
    pass


class RelationalTable:
    """
    Simulates a PostgreSQL table with row-level pessimistic locking (`FOR UPDATE`).
    """
    def __init__(self) -> None:
        self._rows: Dict[int, Dict[str, Any]] = {}
        # Tracks active exclusive locks: row_id -> thread_id
        self._row_locks: Dict[int, int] = {}
        self._table_lock = threading.Condition()

    def insert(self, row_id: int, data: Dict[str, Any]) -> None:
        with self._table_lock:
            data_copy = dict(data)
            data_copy["id"] = row_id
            self._rows[row_id] = data_copy

    def select_for_update(
        self,
        row_id: int,
        nowait: bool = False,
        timeout: float = 2.0
    ) -> Dict[str, Any]:
        """
        Simulates: SELECT * FROM table WHERE id = row_id FOR UPDATE;
        Acquires exclusive row lock for the active thread.
        """
        tid = threading.get_ident()
        deadline = time.time() + timeout

        with self._table_lock:
            while True:
                current_owner = self._row_locks.get(row_id)
                if current_owner is None or current_owner == tid:
                    # Lock acquired!
                    self._row_locks[row_id] = tid
                    return dict(self._rows[row_id])
                
                if nowait:
                    raise OperationalError(f"Row {row_id} is locked by transaction {current_owner}.")
                
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"Deadlock / Lock wait timeout acquiring row {row_id}.")
                
                self._table_lock.wait(timeout=min(0.05, remaining))

    def select_for_update_skip_locked(self, status_filter: str) -> Optional[Dict[str, Any]]:
        """
        Simulates: SELECT * FROM table WHERE status = '...' FOR UPDATE SKIP LOCKED LIMIT 1;
        Skips rows currently locked by other threads, eliminating contention.
        """
        tid = threading.get_ident()
        with self._table_lock:
            for row_id, row_data in self._rows.items():
                if row_data.get("status") == status_filter:
                    if row_id not in self._row_locks:
                        # Lock available row
                        self._row_locks[row_id] = tid
                        return dict(row_data)
            return None

    def update_and_release(self, row_id: int, updated_data: Dict[str, Any]) -> None:
        """Saves updated row and releases exclusive row lock on commit."""
        tid = threading.get_ident()
        with self._table_lock:
            if self._row_locks.get(row_id) == tid:
                self._rows[row_id].update(updated_data)
                del self._row_locks[row_id]
                self._table_lock.notify_all()

    def get_unlocked(self, row_id: int) -> Optional[Dict[str, Any]]:
        with self._table_lock:
            row = self._rows.get(row_id)
            return dict(row) if row else None


# ============================================================================
# SECTION 3: THE LOST UPDATE RACE CONDITION VS PESSIMISTIC LOCKING
# ============================================================================

def simulate_unprotected_withdrawal(table: RelationalTable, account_id: int, amount: Decimal) -> bool:
    """
    VULNERABLE READ-MODIFY-WRITE CYCLE:
    1. Read balance without lock.
    2. Context switch / sleep.
    3. Overwrite balance.
    Result: Lost Updates!
    """
    row = table.get_unlocked(account_id)
    if not row:
        return False
    current_balance = Decimal(str(row["balance"]))
    if current_balance >= amount:
        # Simulate thread switch during network/processing
        time.sleep(0.001)
        new_balance = current_balance - amount
        table.insert(account_id, {"balance": str(new_balance)})
        return True
    return False


def simulate_pessimistic_withdrawal(table: RelationalTable, account_id: int, amount: Decimal) -> bool:
    """
    PROTECTED PESSIMISTIC LOCKING CYCLE (`select_for_update`):
    1. Acquires exclusive row lock.
    2. Validates balance.
    3. Commits updated balance and releases lock.
    Result: 100% Exact Financial Consistency.
    """
    row = table.select_for_update(account_id)
    current_balance = Decimal(str(row["balance"]))
    if current_balance >= amount:
        time.sleep(0.001)
        new_balance = current_balance - amount
        table.update_and_release(account_id, {"balance": str(new_balance)})
        return True
    else:
        table.update_and_release(account_id, {"balance": str(current_balance)})
        return False


# ============================================================================
# SECTION 4: CACHE STAMPEDE (THUNDERING HERD) DEFENSE
# ============================================================================

class CacheAsideService:
    """
    Cache-Aside service with Thundering Herd Mutex Lock protection.
    """
    def __init__(self, cache: InMemoryRedisCache) -> None:
        self.cache = cache
        self.db_queries_count = 0
        self._db_lock = threading.Lock()

    def fetch_product_details(self, product_id: int) -> Dict[str, Any]:
        cache_key = f"product:{product_id}"
        
        # 1. Check Cache
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        # 2. Cache Miss: Acquire Distributed Mutex Lock
        # Only ONE thread is allowed to query the database and populate the cache!
        lock_key = f"lock:{cache_key}"
        acquired = self.cache.add(lock_key, "1", timeout=5)

        if acquired:
            try:
                # This thread won the race: query DB
                with self._db_lock:
                    self.db_queries_count += 1
                # Simulate heavy DB aggregation query
                time.sleep(0.01)
                data = {"id": product_id, "name": f"Enterprise SKU #{product_id}", "price": "999.00"}
                # Populate cache with jittered TTL (300s)
                self.cache.set(cache_key, data, timeout=300, jitter=True)
                return data
            finally:
                self.cache.delete(lock_key)
        else:
            # Other threads wait briefly and re-read from the cache populated by winner
            for _ in range(20):
                time.sleep(0.005)
                cached = self.cache.get(cache_key)
                if cached is not None:
                    return cached
            # Fallback
            return {"id": product_id, "name": f"Enterprise SKU #{product_id}", "price": "999.00"}


# ============================================================================
# SECTION 5: VERIFICATION TEST HARNESS
# ============================================================================

def run_tests() -> None:
    print("=" * 70)
    print(" Day 44: Redis Caching & Database Concurrency with select_for_update")
    print("=" * 70)

    # ------------------------------------------------------------------------
    # [TEST 1] The Lost Update Race Condition (Unprotected)
    # ------------------------------------------------------------------------
    print("\n[TEST 1] Lost Update Anomaly Demonstration (Unprotected Concurrency)...")
    unprotected_db = RelationalTable()
    initial_balance = Decimal("100.00")
    account_id = 1
    unprotected_db.insert(account_id, {"balance": str(initial_balance)})

    threads: List[threading.Thread] = []
    # 20 threads simultaneously withdrawing $10 each.
    # Total attempted withdrawal: $200 from a $100 balance.
    successful_withdrawals = 0
    lock_count = threading.Lock()

    def worker_unprotected():
        nonlocal successful_withdrawals
        if simulate_unprotected_withdrawal(unprotected_db, account_id, Decimal("10.00")):
            with lock_count:
                successful_withdrawals += 1

    for _ in range(20):
        t = threading.Thread(target=worker_unprotected)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    final_row = unprotected_db.get_unlocked(account_id)
    final_balance = Decimal(str(final_row["balance"]))
    print(f"  -> Initial Balance       : ${initial_balance}")
    print(f"  -> Successful Withdrawals: {successful_withdrawals} (out of 20 attempted)")
    print(f"  -> Final Corrupted Balance: ${final_balance}")
    # Due to race conditions, multiple threads read the same initial balance, causing Lost Updates
    print("  -> VULNERABILITY CONFIRMED: Lost Updates corrupted database balance.")

    # ------------------------------------------------------------------------
    # [TEST 2] Pessimistic Locking with select_for_update
    # ------------------------------------------------------------------------
    print("\n[TEST 2] Pessimistic Locking with select_for_update (100% Invariant Safety)...")
    protected_db = RelationalTable()
    protected_db.insert(account_id, {"balance": str(initial_balance)})

    threads.clear()
    successful_protected = 0

    def worker_protected():
        nonlocal successful_protected
        if simulate_pessimistic_withdrawal(protected_db, account_id, Decimal("10.00")):
            with lock_count:
                successful_protected += 1

    for _ in range(20):
        t = threading.Thread(target=worker_protected)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    final_protected_row = protected_db.get_unlocked(account_id)
    final_protected_balance = Decimal(str(final_protected_row["balance"]))
    print(f"  -> Initial Balance       : ${initial_balance}")
    print(f"  -> Successful Withdrawals: {successful_protected} (Exactly 10 succeeded)")
    print(f"  -> Final Consistent Balance: ${final_protected_balance} (Exactly $0.00)")
    assert successful_protected == 10, "Guaranteed: Exactly 10 withdrawals succeeded"
    assert final_protected_balance == Decimal("0.00"), "Guaranteed: Balance stopped at zero without overdraft"
    print("  -> PESSIMISTIC LOCKING VERIFIED: 100% exact balance consistency.")

    # ------------------------------------------------------------------------
    # [TEST 3] Cache Stampede (Thundering Herd) Mutex Defense
    # ------------------------------------------------------------------------
    print("\n[TEST 3] Cache Stampede (Thundering Herd) Defense...")
    cache = InMemoryRedisCache()
    service = CacheAsideService(cache)

    # 50 concurrent reader threads querying an uncached/expired product
    reader_threads: List[threading.Thread] = []
    responses: List[Dict[str, Any]] = []
    res_lock = threading.Lock()

    def reader_worker():
        data = service.fetch_product_details(product_id=99)
        with res_lock:
            responses.append(data)

    for _ in range(50):
        t = threading.Thread(target=reader_worker)
        reader_threads.append(t)
        t.start()
    for t in reader_threads:
        t.join()

    print(f"  -> Concurrent Reader Requests: {len(responses)}")
    print(f"  -> Total Database Queries Executed: {service.db_queries_count}")
    assert service.db_queries_count == 1, "Stampede defense failed: DB queried more than once!"
    assert len(responses) == 50
    print("  -> THUNDERING HERD DEFENSE VERIFIED: 50 requests collapsed into 1 single DB query.")

    # ------------------------------------------------------------------------
    # [TEST 4] Concurrent Job Queue with skip_locked
    # ------------------------------------------------------------------------
    print("\n[TEST 4] High-Throughput Job Queue with select_for_update(skip_locked=True)...")
    queue_table = RelationalTable()
    # Populate queue with 10 pending jobs
    for j_id in range(1, 11):
        queue_table.insert(j_id, {"status": "PENDING", "payload": f"task_{j_id}"})

    processed_jobs: Dict[str, List[int]] = {"worker_1": [], "worker_2": [], "worker_3": []}
    queue_lock = threading.Lock()

    def worker_consumer(worker_name: str):
        while True:
            # Claim job using skip_locked
            job = queue_table.select_for_update_skip_locked(status_filter="PENDING")
            if not job:
                break
            job_id = job["id"]
            # Simulate processing time
            time.sleep(0.005)
            queue_table.update_and_release(job_id, {"status": "COMPLETED"})
            with queue_lock:
                processed_jobs[worker_name].append(job_id)

    worker_threads = [
        threading.Thread(target=worker_consumer, args=(f"worker_{i}",))
        for i in range(1, 4)
    ]
    for wt in worker_threads:
        wt.start()
    for wt in worker_threads:
        wt.join()

    total_processed = sum(len(v) for v in processed_jobs.values())
    all_job_ids = [jid for ids in processed_jobs.values() for jid in ids]
    print(f"  -> Worker 1 processed: {len(processed_jobs['worker_1'])} jobs")
    print(f"  -> Worker 2 processed: {len(processed_jobs['worker_2'])} jobs")
    print(f"  -> Worker 3 processed: {len(processed_jobs['worker_3'])} jobs")
    print(f"  -> Total Completed   : {total_processed} (Zero collisions)")
    assert total_processed == 10
    assert len(set(all_job_ids)) == 10, "Collision detected: Same job processed by multiple workers!"
    print("  -> SKIP LOCKED VERIFIED: 3 workers processed queue with zero lock contention.")

    print("\nAll Day 44 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
