# 7 Database Transaction & Deadlock Antipatterns

This document dissects the top seven architectural mistakes made in high-throughput PostgreSQL production environments, analyzing the exact low-level failure mechanisms, system blast radius, and enterprise-grade remediations.

---

## 1. Network I/O and External API Calls Inside Open Database Transactions

### Failure Mechanism
Executing third-party HTTP requests (e.g. Stripe, Twilio, SendGrid), microservice RPC calls, or file uploads inside an open database transaction block (`BEGIN ... COMMIT` or `@transaction.atomic`):
```python
# ANTIPATTERN:
@transaction.atomic
def process_order(order_id: int):
    order = Order.objects.select_for_update().get(id=order_id)
    # DISASTER: Network round-trip inside open DB transaction!
    payment_response = stripe.Charge.create(amount=order.total, ...)
    order.status = "PAID"
    order.save()
```
If the external payment API experiences a transient 5-second latency spike, the database connection **holds exclusive row locks on `orders` for 5 full seconds**. In high-traffic systems, dozens of concurrent requests block waiting for those locks. Database connection pools exhaust in seconds, CPU spikes due to lock waits, and the entire backend platform experiences a cascading HTTP 504 gateway timeout outage!

```
Normal Txn Latency:         [DB Query: 2ms] -> Total: 2ms
Network Polluted Txn:       [DB Query: 2ms] --- [Stripe API: 5,000ms (Locks Held!)] ---> Total: 5,002ms
Blast Radius:               Connection pool starvation, thread exhaustion, global outage!
```

### Remediation
1. **The Transactional Outbox Pattern**: Commit local state changes first, writing an event to an `outbox` table within the transaction, and dispatch external network calls asynchronously from a background worker:
   ```python
   # ENTERPRISE PATTERN:
   with transaction.atomic():
       order = Order.objects.select_for_update().get(id=order_id)
       order.status = "PAYMENT_PENDING"
       order.save()
       OutboxEvent.objects.create(event_type="CHARGE_CARD", payload={"order_id": order.id})
   # External network I/O executes completely outside the DB transaction!
   ```
2. **Defensive Timeouts**:
   ```sql
   SET idle_in_transaction_session_timeout = '5s';
   SET statement_timeout = '10s';
   ```

---

## 2. Inverted Lock Acquisition Ordering Causing Deadlocks

### Failure Mechanism
When multi-resource updates acquire row locks in arbitrary or opposing orders:
```python
# Thread 1: Transfer from Account 1 to Account 2
def transfer_t1():
    acct_1 = Account.objects.select_for_update().get(id=1) # Locks 1
    acct_2 = Account.objects.select_for_update().get(id=2) # Demands 2 (Blocks on T2)

# Thread 2: Transfer from Account 2 to Account 1
def transfer_t2():
    acct_2 = Account.objects.select_for_update().get(id=2) # Locks 2
    acct_1 = Account.objects.select_for_update().get(id=1) # Demands 1 (Blocks on T1)
```
Thread 1 holds Account 1 and waits for Account 2. Thread 2 holds Account 2 and waits for Account 1. This creates a circular wait cycle in the Wait-For Graph (WFG). PostgreSQL stalls until `deadlock_timeout` (default 1 second), runs DFS cycle detection, and aborts one transaction with `SQLSTATE 40P01: deadlock detected`. Under high concurrency, 10%–30% of transactions fail repeatedly.

```
Thread 1: Holds 1 --------> Demands 2 (BLOCKED)
    ^                             |
    |                             v
Thread 2: Demands 1 (BLOCKED) <--- Holds 2
```

### Remediation
**Strict Resource Ordering Invariant**: Always sort primary keys before acquiring row locks!
```python
def transfer_safe(from_id: int, to_id: int, amount: Decimal):
    # Enforce global acquisition order: min(id) -> max(id)
    first_id, second_id = sorted([from_id, to_id])
    with transaction.atomic():
        first_acct = Account.objects.select_for_update().get(id=first_id)
        second_acct = Account.objects.select_for_update().get(id=second_id)
        # Circular wait is mathematically impossible!
```

---

## 3. Assuming `REPEATABLE READ` Prevents Write Skew

### Failure Mechanism
Engineers often assume `REPEATABLE READ` provides complete serializability. While Repeatable Read prevents Dirty Reads, Non-Repeatable Reads, and Phantom Reads, **it does not prevent Write Skew**!

**The Classic On-Call Doctors Dilemma**:
- System Invariant: At least one doctor must be on call at all times.
- Both Dr. Alice and Dr. Bob are on call. Both simultaneously attempt to go off call.
```sql
-- Transaction 1 (Alice):
SELECT count(*) FROM doctors WHERE on_call = true; -- Returns 2
UPDATE doctors SET on_call = false WHERE name = 'Alice'; -- Modifies Alice

-- Transaction 2 (Bob):
SELECT count(*) FROM doctors WHERE on_call = true; -- Returns 2 (Snapshots are isolated!)
UPDATE doctors SET on_call = false WHERE name = 'Bob'; -- Modifies Bob (Disjoint row!)
```
Under `REPEATABLE READ`:
- Alice’s transaction only modifies row `alice`.
- Bob’s transaction only modifies row `bob`.
- Because the modified rows are completely disjoint, **no row lock conflicts occur**! Both transactions commit successfully!
- Final State: `on_call = 0` (Hospital has zero doctors on call!).

### Remediation
1. **Upgrade to `SERIALIZABLE` Isolation**:
   Under PostgreSQL's Serializable Snapshot Isolation (SSI), the engine tracks `SIREAD` locks, detects the $rw$-antidependency cycle (`Alice --rw--> Bob --rw--> Alice`), and aborts Bob with `SQLSTATE 40001: could not serialize access`.
2. **Materialize the Conflict via Explicit Row Locks**:
   Lock a common parent/mutex row (e.g. `SELECT * FROM hospital_departments WHERE id = 1 FOR UPDATE`).

---

## 4. Polling Task Queues via Bare `SELECT FOR UPDATE` (Lock Convoying)

### Failure Mechanism
Using relational tables as task queues by having multiple concurrent background workers query for pending tasks:
```sql
-- ANTIPATTERN (Worker 1, 2, 3... all run concurrently):
SELECT id, payload FROM task_queue 
WHERE status = 'PENDING' 
ORDER BY id ASC 
LIMIT 1 
FOR UPDATE;
```
1. Worker 1 locks Job #1 and starts processing a 500ms task.
2. Worker 2 executes the query, requests Job #1, and **blocks** waiting for Worker 1 to commit.
3. Worker 3 executes the query, requests Job #1, and **blocks** behind Worker 2.
4. **Lock Convoying**: Despite having 1,000 pending tasks, all $N$ workers serialize behind the single task at the head of the queue! Worker pool utilization drops to 1 concurrent thread, causing queue processing latency to collapse.

### Remediation
Use `SELECT FOR UPDATE SKIP LOCKED`:
```sql
-- ENTERPRISE PATTERN:
WITH next_job AS (
    SELECT id 
    FROM task_queue 
    WHERE status = 'PENDING' 
    ORDER BY priority DESC, id ASC 
    LIMIT 1 
    FOR UPDATE SKIP LOCKED
)
UPDATE task_queue 
SET status = 'PROCESSING', started_at = NOW() 
FROM next_job 
WHERE task_queue.id = next_job.id 
RETURNING task_queue.id, task_queue.payload;
```
Each worker instantly skips rows locked by concurrent peers, achieving 100% horizontal scaling across worker threads with zero lock waits!

---

## 5. Blind Overwrites / Lost Updates Under `READ COMMITTED`

### Failure Mechanism
In Read Committed mode, executing read-modify-write patterns in application memory without pessimistic locks or atomic SQL expressions:
```python
# ANTIPATTERN (Lost Update):
account = Account.objects.get(id=42) # balance = 100
# ... application logic takes 20ms ...
account.balance += 50
account.save() # Emits: UPDATE accounts SET balance = 150 WHERE id = 42;
```
If another transaction concurrently deposits $20:
- Thread 2 reads balance $100.
- Thread 2 saves balance $120.
- Thread 1 overwrites balance with $150.
- Thread 2's $20 deposit is completely obliterated!

### Remediation
1. **Atomic F-Expressions (In-Database Calculation)**:
   ```python
   from django.db.models import F
   Account.objects.filter(id=42).update(balance=F('balance') + 50)
   ```
2. **Pessimistic Locking**:
   ```python
   account = Account.objects.select_for_update().get(id=42)
   account.balance += 50
   account.save()
   ```

---

## 6. Orphaned Two-Phase Commit (2PC) Transactions

### Failure Mechanism
In distributed microservice setups using PostgreSQL Two-Phase Commit:
```sql
PREPARE TRANSACTION 'tx_order_payment_90210';
```
When `PREPARE TRANSACTION` completes, the client disconnects, and the transaction state is persisted to disk under `$PGDATA/pg_twophase/`.
**Crucially, all row-level locks, table locks, and MVCC snapshot horizons remain held indefinitely!**
If the distributed coordinator (e.g., orchestrator, temporal, or custom sagas) crashes or loses its state machine before issuing `COMMIT PREPARED` or `ROLLBACK PREPARED`:
1. The locks remain held for days or weeks, blocking all future updates to those rows.
2. Autovacuum is blocked from cleaning any dead tuples created after the prepared transaction's `xmin`.
3. The cluster-wide transaction horizon freezes, eventually triggering emergency database shutdown due to **Transaction ID (XID) Wraparound**!

### Remediation
1. **Audit Prepared Transactions in CI/CD & Monitoring Alerts**:
   ```sql
   SELECT gid, prepared, owner, database 
   FROM pg_prepared_xacts 
   WHERE prepared < NOW() - INTERVAL '5 minutes';
   ```
2. **Automated Deadlock Sweep / Reaper Cron**:
   Force rollback of orphaned prepared transactions older than a safety threshold:
   ```sql
   ROLLBACK PREPARED 'tx_order_payment_90210';
   ```

---

## 7. Retrying Without Jitter on Serialization Failures (`40001`)

### Failure Mechanism
When running under `REPEATABLE READ` or `SERIALIZABLE`, transactions frequently abort with `SQLSTATE 40001: could not serialize access`.
If the application catches `SerializationFailure` and immediately retries without exponential backoff and randomized jitter:
```python
# ANTIPATTERN (Thundering Herd / Lock Convoy):
while True:
    try:
        with transaction.atomic():
            execute_settlement()
        break
    except SerializationFailure:
        time.sleep(0.01) # Synchronized retry!
```
Under high concurrency (e.g. flash sales or ticket booking), 50 concurrent transactions collide, abort simultaneously, sleep for 10ms, and retry **at the exact same millisecond**. They collide again, abort again, and enter a self-sustaining livelock cycle, driving database CPU to 100% without completing any useful work!

### Remediation
Always implement Exponential Backoff with Full Random Jitter:
```python
import random
import time

def execute_with_jitter_retry(max_retries: int = 5, base_delay: float = 0.05):
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                return execute_settlement()
        except SerializationFailure:
            if attempt == max_retries - 1:
                raise
            # Exponential Backoff with Full Jitter: Uniform(0, base_delay * 2^attempt)
            sleep_duration = random.uniform(0, base_delay * (2 ** attempt))
            time.sleep(sleep_duration)
```
