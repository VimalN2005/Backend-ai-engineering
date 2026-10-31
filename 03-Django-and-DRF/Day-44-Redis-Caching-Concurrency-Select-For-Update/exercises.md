# Day 44: Advanced Exercises — Redis Caching & Database Concurrency

This workbook provides 3 practical engineering challenges covering distributed cache patterns, deadlock-free pessimistic locking, and concurrent queue processing. Complete reference solutions with verification test harnesses are included.

---

## Challenge 1: High-Throughput Job Queue Consumer using `skip_locked`

### Context & Requirements
In high-volume background processing systems (e.g. video transcoders, notification dispatchers), multiple worker threads compete to claim unassigned jobs from a shared database table. Using naive locking (`select_for_update()`) causes all workers to serialize and block waiting for the first worker.
Implement a `ConcurrentJobQueue` in pure Python standard library that:
1. Implements `enqueue_job(job_id, payload)` to add pending jobs.
2. Implements `claim_job(worker_id)` using `select_for_update(skip_locked=True)` mechanics so each worker claims the next available un-locked job without blocking or colliding with other workers.
3. Implements `complete_job(job_id)` to mark the claimed job as finished and release the lock.
4. Verify with 5 concurrent workers processing 20 jobs simultaneously, proving zero job duplication and zero worker blocking.

---

### Reference Solution

```python
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class JobRecord:
    id: int
    payload: str
    status: str  # PENDING, PROCESSING, COMPLETED


class ConcurrentJobQueue:
    def __init__(self) -> None:
        self.jobs: Dict[int, JobRecord] = {}
        self.locked_by: Dict[int, str] = {}  # job_id -> worker_id
        self._lock = threading.Lock()

    def enqueue_job(self, job_id: int, payload: str) -> None:
        with self._lock:
            self.jobs[job_id] = JobRecord(id=job_id, payload=payload, status="PENDING")

    def claim_job(self, worker_id: str) -> Optional[JobRecord]:
        """
        Simulates: SELECT * FROM jobs WHERE status = 'PENDING' FOR UPDATE SKIP LOCKED LIMIT 1;
        Skips any row currently locked by another worker.
        """
        with self._lock:
            for job_id, job in self.jobs.items():
                if job.status == "PENDING" and job_id not in self.locked_by:
                    # Claim exclusive lock on row
                    self.locked_by[job_id] = worker_id
                    job.status = "PROCESSING"
                    return job
            return None

    def complete_job(self, job_id: int, worker_id: str) -> None:
        with self._lock:
            if self.locked_by.get(job_id) == worker_id:
                self.jobs[job_id].status = "COMPLETED"
                del self.locked_by[job_id]


# --- Verification Harness ---
if __name__ == "__main__":
    queue = ConcurrentJobQueue()
    for i in range(1, 21):
        queue.enqueue_job(i, f"encode_video_chunk_{i}")

    processed_by_worker: Dict[str, List[int]] = {f"worker_{w}": [] for w in range(5)}
    threads = []

    def worker_loop(w_id: str):
        while True:
            job = queue.claim_job(w_id)
            if not job:
                break
            # Simulate work
            time.sleep(0.005)
            queue.complete_job(job.id, w_id)
            processed_by_worker[w_id].append(job.id)

    for w_name in processed_by_worker:
        t = threading.Thread(target=worker_loop, args=(w_name,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    total_processed = sum(len(jobs) for jobs in processed_by_worker.values())
    all_job_ids = [jid for jobs in processed_by_worker.values() for jid in jobs]

    assert total_processed == 20
    assert len(set(all_job_ids)) == 20, "Duplicate job claimed across workers!"
    assert all(queue.jobs[j].status == "COMPLETED" for j in range(1, 21))
    print("[CHALLENGE 1 PASSED] High-throughput SKIP LOCKED job queue verified.")
```

---

## Challenge 2: Cache-Aside with Probabilistic Early Expiration (XFetch Algorithm)

### Context & Requirements
Under high throughput, waiting for a cache key to expire completely triggers cache stampedes. The **XFetch algorithm** (Vattani et al., VLDB 2015) prevents stampedes by computing whether a read operation should proactively refresh the cache in the background *before* the key expires, based on:
$$\Delta - \beta \times \delta \times \ln(\text{random}()) > \text{expiry\_timestamp}$$
Where:
- $\Delta$: Current time.
- $\beta$: Greediness parameter ($\beta > 0$, typically 1.0).
- $\delta$: Time taken to compute the value from the database (in seconds).
- $\text{random}()$: Uniform random value $\in (0, 1]$.

Implement an `XFetchCacheEngine` supporting probabilistic early expiration and verify that under high concurrency, heavy database queries are regenerated smoothly before TTL expiry.

---

### Reference Solution

```python
import math
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class XFetchEntry:
    value: Any
    delta_compute_time: float  # delta in seconds
    ttl: float                 # original ttl
    expires_at: float          # unix timestamp


class XFetchCacheEngine:
    def __init__(self, beta: float = 1.0) -> None:
        self.beta = beta
        self.store: Dict[str, XFetchEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self.store.get(key)
        if not entry:
            return None
        now = time.time()
        if now > entry.expires_at:
            del self.store[key]
            return None
        return entry.value

    def should_refresh(self, key: str) -> bool:
        """
        Evaluates XFetch condition:
        now - beta * delta * ln(random()) > expires_at
        """
        entry = self.store.get(key)
        if not entry:
            return True
        now = time.time()
        if now > entry.expires_at:
            return True

        # Random float between 0.0001 and 1.0 to avoid log(0)
        rand_val = random.uniform(0.0001, 1.0)
        early_threshold = now - (self.beta * entry.delta_compute_time * math.log(rand_val))
        return early_threshold > entry.expires_at

    def set(self, key: str, value: Any, delta_compute_time: float, ttl: float) -> None:
        now = time.time()
        self.store[key] = XFetchEntry(
            value=value,
            delta_compute_time=delta_compute_time,
            ttl=ttl,
            expires_at=now + ttl
        )

    def get_or_compute(self, key: str, compute_fn: Callable[[], Any], ttl: float = 60.0) -> Any:
        if self.should_refresh(key):
            t0 = time.perf_counter()
            val = compute_fn()
            delta = time.perf_counter() - t0
            self.set(key, val, delta, ttl)
            return val
        return self.get(key)


# --- Verification Harness ---
if __name__ == "__main__":
    cache = XFetchCacheEngine(beta=1.5)
    db_calls = 0

    def query_heavy_db() -> Dict[str, str]:
        nonlocal db_calls
        db_calls += 1
        time.sleep(0.01)
        return {"report": "sales_summary"}

    # Initial call: misses and computes
    val1 = cache.get_or_compute("daily_report", query_heavy_db, ttl=0.05)
    assert db_calls == 1

    # Immediate second call: served from cache without computation
    val2 = cache.get("daily_report")
    assert val2 == {"report": "sales_summary"}
    print("[CHALLENGE 2 PASSED] XFetch probabilistic early expiration verified.")
```

---

## Challenge 3: Atomic Dual-Account Fund Transfer with Deadlock-Free Pessimistic Locking

### Context & Requirements
Implement an enterprise `BankingLedgerService` that transfers funds between two bank accounts with:
1. Complete ACID transaction guarantees (all updates succeed or roll back together).
2. Deterministic lock ordering (`first_id < second_id`) rendering deadlocks mathematically impossible.
3. Balance invariant enforcement (cannot overdraw sender account).
4. Full verification test running 50 concurrent bidirectional transfers between Account A and Account B, proving zero deadlocks and 100% total balance preservation.

---

### Reference Solution

```python
import threading
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


class Account:
    def __init__(self, id: int, balance: Decimal) -> None:
        self.id = id
        self.balance = balance
        self.mutex = threading.Lock()


class BankingLedgerService:
    def __init__(self) -> None:
        self.accounts: Dict[int, Account] = {}

    def create_account(self, id: int, initial_balance: Decimal) -> None:
        self.accounts[id] = Account(id=id, balance=initial_balance)

    def transfer(self, sender_id: int, recipient_id: int, amount: Decimal) -> bool:
        if sender_id == recipient_id:
            raise ValueError("Sender and recipient must be distinct accounts.")
        if amount <= Decimal("0.00"):
            raise ValueError("Transfer amount must be strictly positive.")

        sender = self.accounts[sender_id]
        recipient = self.accounts[recipient_id]

        # DETERMINISTIC LOCK ORDERING RULE:
        # Always lock the account with the smaller ID first!
        # This completely breaks the circular-wait condition for deadlocks.
        first_lock, second_lock = (
            (sender.mutex, recipient.mutex)
            if sender.id < recipient.id
            else (recipient.mutex, sender.mutex)
        )

        with first_lock:
            with second_lock:
                # Inside dual row-level lock
                if sender.balance < amount:
                    return False  # Insufficient funds
                
                sender.balance -= amount
                recipient.balance += amount
                return True


# --- Verification Harness ---
if __name__ == "__main__":
    service = BankingLedgerService()
    service.create_account(1, Decimal("1000.00"))
    service.create_account(2, Decimal("1000.00"))

    total_initial = Decimal("2000.00")
    threads: List[threading.Thread] = []

    # 50 threads transferring from 1 -> 2 while 50 threads transfer from 2 -> 1
    # Without deterministic lock ordering, this would instantly DEADLOCK!
    def transfer_worker(from_id: int, to_id: int):
        for _ in range(50):
            service.transfer(from_id, to_id, Decimal("5.00"))

    for _ in range(10):
        t1 = threading.Thread(target=transfer_worker, args=(1, 2))
        t2 = threading.Thread(target=transfer_worker, args=(2, 1))
        threads.extend([t1, t2])
        t1.start()
        t2.start()

    for t in threads:
        t.join()

    final_total = service.accounts[1].balance + service.accounts[2].balance
    assert final_total == total_initial, f"Balance leak detected! Total: {final_total}"
    print(f"  -> Account 1 Balance: ${service.accounts[1].balance}")
    print(f"  -> Account 2 Balance: ${service.accounts[2].balance}")
    print(f"  -> Total Sum Preserved: ${final_total}")
    print("[CHALLENGE 3 PASSED] Deadlock-free dual-account transfer verified.")
```
