# Day 44: Redis Caching in Django & Concurrency with `select_for_update`

## 1. Executive Architectural Overview

At enterprise scale, database systems face two existential challenges:
1. **Read Saturation**: Relational databases buckle under repetitive read traffic unless shielded by an in-memory caching tier such as **Redis**.
2. **Write Concurrency Anomalies**: Concurrent transactions competing for shared resources (account balances, inventory counts, booking seats) produce race conditions, lost updates, and double-spending unless guarded by strict database-level row locks (`select_for_update`).

This module breaks down the mechanics of caching topologies, cache-invalidation strategies, cache breakdown failure modes (**Stampede**, **Avalanche**, **Penetration**), ACID transaction isolation levels, SQL row-level pessimistic locking (`SELECT ... FOR UPDATE`), `nowait`, and `skip_locked`.

```
HIGH-CONCURRENCY ARCHITECTURAL PIPELINE:
=========================================
Incoming Concurrent Requests (e.g. Flash Sale / Fund Transfer)
                         │
                         ▼
             +───────────────────────+
             | Redis In-Memory Tier  |
             +───────────────────────+
                         │
        ┌────────────────┴────────────────┐
        ▼ (Cache Hit)                     ▼ (Cache Miss / Mutation)
[Return Cached Data]              +───────────────────────────────────+
(Sub-millisecond)                 | Distributed Mutex (Lock Stampede) |
                                  +───────────────────────────────────+
                                                  │
                                                  ▼
                                  +───────────────────────────────────+
                                  | Django Service Layer              |
                                  | with transaction.atomic():        |
                                  |   SELECT ... FOR UPDATE           |
                                  +───────────────────────────────────+
                                                  │
                                                  ▼
                                  +───────────────────────────────────+
                                  | PostgreSQL Row-Level Lock         |
                                  | - Prevents Lost Updates           |
                                  | - Guarantees Exact Balance        |
                                  +───────────────────────────────────+
                                                  │
                                                  ▼
                                  +───────────────────────────────────+
                                  | Post-Commit Cache Invalidation    |
                                  | transaction.on_commit()           |
                                  +───────────────────────────────────+
```

---

## 2. Redis in the Django Ecosystem (`django-redis`)

Redis (Remote Dictionary Server) is an in-memory, key-value data structure store running in $O(1)$ RAM access time. In Django, `django-redis` is the industry-standard backend providing connection pooling, sentinel support, and Redis native operations.

### 2.1 Django Cache Framework Configuration
```python
# settings.py
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 100,
                "retry_on_timeout": True,
            },
            "SOCKET_CONNECT_TIMEOUT": 5,  # seconds
            "SOCKET_TIMEOUT": 5,
            # Security: Use JSON or MessagePack rather than default Python pickle
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
        },
        "KEY_PREFIX": "enterprise_api",
        "TIMEOUT": 300,  # 5 minutes default TTL
    }
}
```

### 2.2 Core Django Cache API Primitives
| Method | Description | Atomic Guarantee |
|:---|:---|:---|
| `cache.get(key, default=None)` | Fetches key value. Returns `None` on cache miss. | Read-only |
| `cache.set(key, value, timeout=300)` | Overwrites key with TTL. | Atomic in Redis (`SETEX`) |
| `cache.add(key, value, timeout=300)` | Sets key **only if it does not already exist**. | **Atomic (`SETNX`)** — basis for locks |
| `cache.delete(key)` | Evicts key. | Atomic (`DEL`) |
| `cache.incr(key, delta=1)` | Increments integer value in place. | **Atomic (`INCRBY`)** |
| `cache.get_or_set(key, default_fn, timeout)` | Fetches or populates via callable. | Not atomic in naive implementations |

---

## 3. Caching Design Patterns: Aside, Write-Through & Write-Behind

How data moves between the database and the cache determines consistency and read performance:

```
1. CACHE-ASIDE (Lazy Loading):
   App ──► Read Cache ──(Miss)──► Read DB ──► Populate Cache with TTL ──► Return

2. WRITE-THROUGH:
   App ──► Write DB ──► Synchronously Write Cache ──► Return

3. WRITE-BEHIND (Write-Back):
   App ──► Write Cache ──► Return Immediate ──(Async Queue)──► Flush to DB
```

### 3.1 Cache-Aside (Lazy Loading)
- **Mechanism**: The application first queries Redis. If the key exists, it returns the value (Cache Hit). If the key is absent (Cache Miss), the application reads from PostgreSQL, stores the result in Redis with a Time-To-Live (TTL), and returns the payload.
- **Pros**: Cache only contains requested data; node failure does not cause data loss.
- **Cons**: Cache misses incur a penalty (3 network hops); vulnerable to cache stampede without locking.

### 3.2 Write-Through
- **Mechanism**: When writing or updating data, the application writes to the database and immediately updates the cache in the same workflow.
- **Pros**: Cache is never stale; reads are always fast.
- **Cons**: Write penalty; infrequenty read data pollutes memory unless strict LRU eviction is configured.

### 3.3 Write-Behind (Write-Back)
- **Mechanism**: Writes update Redis immediately and acknowledge the client. An asynchronous worker flushes batched writes to SQL later.
- **Pros**: Maximum write throughput (thousands of writes/sec in RAM).
- **Cons**: High risk of data loss if Redis crashes before flushing to disk.

---

## 4. Cache Disasters: Stampede, Avalanche & Penetration

At high scale, naive caching architectures suffer catastrophic breakdown anomalies:

### 4.1 Cache Stampede (Thundering Herd)
- **Failure**: A popular cache key (e.g., `product:iphone-16`) expires. Within 50 milliseconds, 5,000 concurrent client requests miss the cache simultaneously. All 5,000 requests hit PostgreSQL with identical heavy aggregation queries, exhausting database connections and driving CPU to 100%.
- **Defenses**:
  1. **Distributed Mutex Lock**: The first worker that encounters a miss acquires an atomic lock (`cache.add("lock:key", 1, timeout=10)`). Only this worker queries PostgreSQL and populates the cache; all other workers wait or receive a stale value.
  2. **XFetch Probabilistic Early Expiration**: Workers recalculate the cache asynchronously *before* it officially expires based on read frequency and computation time.

### 4.2 Cache Avalanche
- **Failure**: A batch job writes 100,000 cache keys with the exact same TTL of 3,600 seconds (1 hour). Exactly 60 minutes later, all 100,000 keys expire simultaneously, exposing the database to massive traffic surges.
- **Defense**: **TTL Jittering**. Introduce a pseudo-random delta to every TTL:
  $$\text{effective\_ttl} = \text{base\_ttl} + \text{random}(-0.1 \times \text{base\_ttl}, +0.1 \times \text{base\_ttl})$$

### 4.3 Cache Penetration
- **Failure**: Malicious clients query IDs that do not exist in the database (e.g., `GET /users/uuid-not-found`). The cache misses, the database queries and returns `None`, and nothing is cached. The attacker repeats this in a loop, forcing every request to bypass Redis and hit SQL.
- **Defenses**:
  1. **Null Object Caching**: Cache `None` or a sentinel value with a short TTL (e.g., 30–60 seconds).
  2. **Bloom Filter**: Maintain an in-memory Bloom filter of all valid primary keys in Redis. If the Bloom filter returns `False`, reject the request immediately without touching SQL.

---

## 5. Database Concurrency Anomalies & Isolation Levels

When multiple database transactions run concurrently, race conditions corrupt shared data:

| Concurrency Anomaly | Description | Minimum Isolation Required |
|:---|:---|:---|
| **Dirty Read** | Transaction A reads uncommitted modifications made by Transaction B. | **Read Committed** (Default in PG) |
| **Non-Repeatable Read** | Transaction A reads a row, Transaction B updates that row and commits; Transaction A re-reads and gets different data. | **Repeatable Read** |
| **Phantom Read** | Transaction A queries a range of rows; Transaction B inserts rows matching the range and commits; Transaction A re-reads and sees new rows. | **Serializable** |
| **Lost Update** | Two transactions read balance \$100 simultaneously; both subtract \$20 and write back \$80. One subtraction is completely erased. | **`select_for_update`** or **Serializable** |

---

## 6. SQL Pessimistic Locking: `SELECT ... FOR UPDATE`

To prevent Lost Updates without paying the severe transaction abort penalties of `Serializable` isolation, relational databases provide **Pessimistic Row Locking**.

```sql
BEGIN;
SELECT id, balance FROM accounts_account 
WHERE id = 42 
FOR UPDATE;

-- Row is now locked! Any other transaction attempting SELECT ... FOR UPDATE 
-- on row 42 will BLOCK until this transaction executes COMMIT or ROLLBACK.

UPDATE accounts_account SET balance = balance - 20.00 WHERE id = 42;
COMMIT;
```

### 6.1 Lock Modes in PostgreSQL
- **`FOR UPDATE`**: Strongest exclusive lock. Prevents concurrent `FOR UPDATE`, `FOR NO KEY UPDATE`, `FOR SHARE`, `FOR KEY SHARE`, `UPDATE`, and `DELETE`.
- **`FOR NO KEY UPDATE`**: Weaker exclusive lock. Allows concurrent `FOR KEY SHARE` (e.g., foreign key checks).
- **`FOR SHARE`**: Shared lock. Concurrent transactions can read and acquire shared locks, but cannot update or delete.

---

## 7. Django ORM: `select_for_update()` Deep Dive

In Django, pessimistic locking is invoked on a QuerySet via `.select_for_update()`:

```python
from django.db import transaction
from apps.accounts.models import Account

with transaction.atomic():
    # Acquires exclusive row lock
    account = Account.objects.select_for_update().get(id=42)
    if account.balance >= withdrawal_amount:
        account.balance -= withdrawal_amount
        account.save()
```

### 7.1 Critical Parameters: `nowait` and `skip_locked`

#### 1. Blocking (Default: `nowait=False`)
```python
account = Account.objects.select_for_update().get(id=42)
```
- If another transaction holds the lock, the worker **blocks indefinitely** (or until database `statement_timeout`).
- Suitable for financial transactions where every request must be processed.

#### 2. Non-blocking (`nowait=True`)
```python
try:
    account = Account.objects.select_for_update(nowait=True).get(id=42)
except django.db.OperationalError:
    # Row is locked by another thread!
    raise ConflictError("Resource is currently being modified by another request. Try again.")
```
- Fails fast immediately if the lock is contested. Eliminates worker thread blocking.

#### 3. Queue Processing (`skip_locked=True`)
```python
# Multiple Celery workers processing unassigned jobs concurrently:
job = JobQueue.objects.select_for_update(skip_locked=True).filter(status='PENDING').first()
if job:
    job.status = 'PROCESSING'
    job.save()
```
- Instructs PostgreSQL to silently skip any rows currently locked by other transactions.
- Enables building **lock-free concurrent job queues** with zero lock contention.

---

## 8. Deadlock Mechanics & Deterministic Ordering

A deadlock occurs when two transactions hold locks on separate rows and each attempts to acquire the lock held by the other:

```
Transaction 1:                         Transaction 2:
BEGIN;                                 BEGIN;
LOCK Account 1 (Success)               LOCK Account 2 (Success)
LOCK Account 2 (BLOCKS on Tx 2...)    LOCK Account 1 (BLOCKS on Tx 1...)
               ▲                                      ▲
               └──────────── DEADLOCK! ───────────────┘
```
PostgreSQL detects circular wait graphs via its `deadlock_timeout` (default: 1 second) and forcibly terminates one transaction with `DeadlockDetected`.

### The Deterministic Ordering Rule
To make deadlocks mathematically impossible, **transactions must always acquire multiple locks in identical sorted order**:

```python
# SAFE MULTI-ACCOUNT LOCKING:
def transfer_funds(sender_id: int, recipient_id: int, amount: Decimal):
    # Sort IDs deterministically! Always lock smaller ID first!
    first_id, second_id = sorted([sender_id, recipient_id])

    with transaction.atomic():
        # Locks acquired in strictly deterministic order
        account_a = Account.objects.select_for_update().get(id=first_id)
        account_b = Account.objects.select_for_update().get(id=second_id)
        ...
```

---

## 9. Pessimistic Locking vs Optimistic Locking

| Metric | Pessimistic Locking (`select_for_update`) | Optimistic Locking (Version Field) |
|:---|:---|:---|
| **Mechanism** | Database row-level exclusive locks | Application version check: `UPDATE ... WHERE version = V` |
| **Database Load** | Holds lock; holds DB connection open | Zero locks held during processing |
| **Contention Profile** | **High Contention** (Flash sales, shared bank accounts) | **Low to Medium Contention** (User profile edits) |
| **Failure Mode** | Blocking / `OperationalError` | Retries required on version mismatch |
| **Deadlock Risk** | Possible (if ordering is non-deterministic) | **Zero deadlock risk** |

---

## 10. Cache Invalidation and Concurrency Interaction

A subtle and catastrophic concurrency bug occurs when combining Redis caching with database updates:

### The Cache Invalidation Race Condition:
1. **Thread A** updates account in DB: `UPDATE accounts SET balance = 80`.
2. **Thread A** evicts cache: `cache.delete("account:42")`.
3. Meanwhile, **Thread B** reads uncommitted state, fetches old balance \$100, and repopulates Redis.
4. Thread A commits.
5. **Result**: Redis is now permanently poisoned with stale balance \$100!

### The Remediation:
Always defer cache invalidation to `transaction.on_commit()`:
```python
with transaction.atomic():
    account = Account.objects.select_for_update().get(id=42)
    account.balance -= 20
    account.save()
    
    # Invalidate ONLY after the database transaction has committed!
    transaction.on_commit(lambda: cache.delete(f"account:{account.id}"))
```

---

## 11. Production Redis & Concurrency Checklist

| Category | Requirement | Verification Method |
|:---|:---|:---|
| **Redis** | Use JSON or MsgPack serializer; avoid default Pickle | Audit `settings.CACHES` for `JSONSerializer` |
| **Redis** | Apply random jitter (10–20%) to all TTLs | Unit tests verifying TTL variation |
| **Redis** | Guard cache misses with distributed mutex or XFetch | Load test with 1,000 concurrent requests on expired key |
| **Concurrency** | `select_for_update()` must execute inside `transaction.atomic()` | Static analysis / code review verification |
| **Concurrency** | Multi-row locks must be sorted deterministically | Verification that `id1 < id2` ordering is enforced |
| **Concurrency** | Never make external HTTP calls while holding a DB lock | Connection pool monitor under simulated network latency |
| **Integration** | Invalidate cache exclusively inside `transaction.on_commit()` | Concurrent race-condition integration tests |
