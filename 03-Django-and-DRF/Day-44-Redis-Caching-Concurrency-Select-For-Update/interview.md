# Day 44: Senior Backend Engineer Interview Q&A — Redis Caching & Concurrency

This guide provides deep technical responses to senior and staff backend engineering interview questions regarding Redis caching architectures, high-concurrency race conditions, and database pessimistic locking mechanics.

---

### Question 1: What is the exact difference between Cache Stampede (Thundering Herd) and Cache Avalanche, and what are their production mitigation strategies?

#### Answer:
While both anomalies result in massive, unexpected query spikes hitting the relational database, their underlying root causes and physical mechanics differ:

#### 1. Cache Stampede (Thundering Herd):
- **Mechanism**: A **single, highly-requested key** (or a small set of hot keys, e.g. `homepage_feed` or `product:iphone-16`) expires or is evicted. Within milliseconds, hundreds or thousands of concurrent requests experience a cache miss simultaneously. Each thread independently attempts to execute the database query and compute the cached object.
- **Consequences**: Database CPU spikes to 100%, query latency increases by orders of magnitude, and database connection pools are exhausted.
- **Production Defenses**:
  1. **Distributed Mutex Lock**: The first worker encountering the miss executes an atomic lock (`cache.add(f"lock:{key}", "1", timeout=10)`). Only this worker queries PostgreSQL and populates Redis; all other workers wait or receive a fallback response.
  2. **Probabilistic Early Expiration (XFetch Algorithm)**: Workers probabilistically recalculate and refresh the cache in the background *before* the key expires, based on read frequency and computation time:
     $$\text{now} - \beta \times \delta \times \ln(\text{random}()) > \text{expires\_at}$$

#### 2. Cache Avalanche:
- **Mechanism**: **Massive numbers of disparate keys** expire at the **exact same point in time**. This typically happens when batch jobs warm the cache using identical TTLs (e.g., setting 500,000 keys with `timeout=3600`). When the 1-hour timer elapses, all 500,000 keys vanish simultaneously.
- **Consequences**: General database read traffic spikes globally across entire tables, overwhelming read replicas and primary instances.
- **Production Defenses**:
  1. **TTL Jittering**: Add a pseudo-random deviation to every TTL:
     $$\text{TTL} = \text{base\_timeout} + \text{uniform}(-0.15 \times \text{base\_timeout}, +0.15 \times \text{base\_timeout})$$
  2. **Multi-Tiered Caching / Read Replicas**: Route cache-miss traffic through read replicas with strict concurrency limits.

---

### Question 2: Explain the internal mechanics of `select_for_update()`, `nowait=True`, and `skip_locked=True` in PostgreSQL and Django ORM.

#### Answer:
In Django ORM, `.select_for_update()` appends an SQL row-level locking clause (`FOR UPDATE`) to the generated `SELECT` statement.

#### 1. Standard Blocking (`select_for_update()`):
- **SQL**: `SELECT ... FOR UPDATE;`
- **Behavior**: Acquires an exclusive row lock on every matched row. If another transaction holds a conflicting lock on any of those rows, the executing thread **blocks and waits** until the holding transaction commits or rolls back, or until the database `statement_timeout` triggers.
- **Use Case**: Financial balance deductions, stock reservations where requests must succeed in serial order.

#### 2. Fail-Fast Non-Blocking (`select_for_update(nowait=True)`):
- **SQL**: `SELECT ... FOR UPDATE NOWAIT;`
- **Behavior**: Attempts to acquire exclusive row locks immediately. If any row is currently locked by another transaction, PostgreSQL does not wait; it **immediately raises an `OperationalError`**.
- **Use Case**: Interactive user interfaces where waiting 5 seconds for a lock would freeze the UI. The application catches `OperationalError` and returns `409 Conflict` ("Resource busy, please retry").

#### 3. Queue Processor (`select_for_update(skip_locked=True)`):
- **SQL**: `SELECT ... FOR UPDATE SKIP LOCKED;`
- **Behavior**: PostgreSQL inspects matching rows in index order. Any row currently locked by another transaction is **silently skipped and omitted from the result set**.
- **Use Case**: Multi-worker job queues. Multiple Celery or background workers query the `JobQueue` table concurrently:
  ```python
  job = JobQueue.objects.select_for_update(skip_locked=True).filter(status='PENDING').first()
  ```
  Every worker claims an un-locked row instantaneously with **zero lock contention and zero blocking**.

---

### Question 3: How does a database deadlock occur when locking multiple rows, and how do you mathematically guarantee deadlock freedom?

#### Answer:
A database deadlock occurs when two or more transactions establish a **circular wait condition** for mutually held locks.

#### Deadlock Scenario:
- **Transaction 1** locks Account A (`id=10`) and needs Account B (`id=20`).
- **Transaction 2** locks Account B (`id=20`) and needs Account A (`id=10`).
- Neither transaction can proceed. PostgreSQL detects the cycle via `deadlock_timeout` (typically 1,000 ms) and forcibly aborts one of the transactions with `DeadlockDetected`.

#### Mathematical Guarantee of Deadlock Freedom:
According to the Coffman conditions, a deadlock cannot occur if the **Circular Wait condition is broken**.

To break circular wait across distributed or concurrent transactions:
1. **Global Linear Ordering**: Establish a strict, global total ordering relation over all resource keys before lock acquisition.
2. For database rows, this is achieved by **sorting primary keys**:
   $$\text{order\_to\_lock} = \text{sorted}([id_1, id_2, \dots, id_n])$$
3. If all transactions in the entire codebase acquire locks in strictly increasing primary key order ($id_1 < id_2 < id_3$), it is mathematically impossible for Transaction 2 to hold $id_2$ and wait for $id_1$ while Transaction 1 holds $id_1$ and waits for $id_2$.
4. **Code Implementation**:
   ```python
   def transfer(src_id: int, dest_id: int, amount: Decimal):
       first_id, second_id = sorted([src_id, dest_id])
       with transaction.atomic():
           first_acc = Account.objects.select_for_update().get(id=first_id)
           second_acc = Account.objects.select_for_update().get(id=second_id)
           ...
   ```

---

### Question 4: Compare Pessimistic Locking (`select_for_update`) vs Optimistic Locking (versioning). In what scenarios is each preferred?

#### Answer:

| Feature | Pessimistic Locking (`select_for_update`) | Optimistic Locking (`version` field) |
|:---|:---|:---|
| **Underlying Mechanism** | Database-level exclusive row lock (`FOR UPDATE`) | Application-level version check (`UPDATE ... WHERE version = V`) |
| **Lock Duration** | Held for the entire duration of `transaction.atomic()` | No locks held; evaluated only at the moment of update |
| **Concurrency Profile** | **High Contention** (e.g. Flash sales, airline seats) | **Low to Medium Contention** (e.g. CMS article edits) |
| **Failure Mode** | Contended requests block or raise `nowait` error | Contended requests fail immediately; require retry loops |
| **Connection Overhead** | Holds database connections open while processing | Extremely low connection holding time |
| **Deadlock Risk** | Possible if ordering is non-deterministic | **Zero deadlock risk** |

#### Decision Framework:
- **Choose Pessimistic Locking when**:
  - The resource is fiercely contended (e.g. 1,000 requests competing for 1 concert ticket).
  - The cost of retrying an operation is expensive (financial ledger updates, payment processing).
  - Invariant violations cannot be tolerated under any circumstances.
- **Choose Optimistic Locking when**:
  - Contention is rare (e.g. two administrators editing the same blog post).
  - Operations involve long think-times (user fills out a 5-minute form in browser). Holding a database row lock across human think-time is an anti-pattern.

---

### Question 5: What is the Cache Invalidation Race Condition that occurs when pairing Redis with relational database writes, and how do you resolve it?

#### Answer:
A severe data corruption anomaly occurs when cache keys are deleted *inside* an uncommitted database transaction:

#### The Failure Sequence:
1. **Transaction A (Writer)** begins an atomic transaction and updates row 42:
   ```sql
   UPDATE product SET price = 90 WHERE id = 42;
   ```
2. **Transaction A** immediately executes `cache.delete("product:42")` to evict stale data.
3. **Transaction B (Reader)** receives a customer request and checks Redis for `product:42`. It misses.
4. **Transaction B** reads from PostgreSQL:
   ```sql
   SELECT price FROM product WHERE id = 42;
   ```
   Because Transaction A has **not committed yet**, PostgreSQL's MVCC visibility rules dictate that Transaction B reads the **old committed price ($100)**!
5. **Transaction B** writes the old price back to Redis: `cache.set("product:42", 100)`.
6. **Transaction A** commits its database transaction ($90).
7. **The Corruption**: PostgreSQL now has price \$90, but Redis is **permanently poisoned with stale price \$100** until the TTL expires!

#### The Production Remediation:
Always defer cache invalidation until **after the database transaction has committed** using Django's `transaction.on_commit()`:

```python
from django.db import transaction
from django.core.cache import cache

def update_product_price(product_id: int, new_price: Decimal):
    with transaction.atomic():
        product = Product.objects.select_for_update().get(id=product_id)
        product.price = new_price
        product.save()

        # Invalidation occurs strictly AFTER PostgreSQL writes and commits the WAL!
        transaction.on_commit(lambda: cache.delete(f"product:{product_id}"))
```
Because the cache is deleted only after the new value is committed and visible to all connections, concurrent readers will never fetch and re-cache uncommitted stale state.
