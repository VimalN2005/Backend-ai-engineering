# Day 58: Redis Cache Patterns, Cache-Aside, Write-Through & Distributed Locks

A production-grade architectural guide to distributed caching architectures, cache consistency patterns, thundering herd mitigation, and distributed mutual exclusion (distributed locking) in modern Python and FastAPI systems.

---

## 1. Redis Architecture & The Memory Hierarchy

In high-throughput architectures, relational databases (PostgreSQL, MySQL) are constrained by disk I/O, write-ahead logs (WAL), complex relational joins, and B-Tree index traversal ($O(\log N)$). 

Redis is an in-memory key-value data structure store delivering sub-millisecond retrieval ($O(1)$ amortized) by serving requests directly from RAM.

```
+-------------------------------------------------------------------------+
| L1 Cache: In-Process Memory (Cachetools / LRU)   ~10-50 nanoseconds    |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| L2 Cache: Distributed Redis Cluster (TCP Network) ~200-800 microseconds |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Primary Data Store: PostgreSQL / MySQL (SSD / NVMe) ~2-20 milliseconds  |
+-------------------------------------------------------------------------+
```

### The Single-Threaded Event Loop Model
Redis processes commands sequentially using a non-blocking I/O multiplexing event loop (`epoll` on Linux, `kqueue` on macOS). Because all data structures reside entirely in memory, operations execute in nanoseconds, eliminating multi-threaded lock contention, race conditions, and context-switching overhead. Modern Redis (v6+) offloads socket read/write networking to auxiliary I/O threads while maintaining a single thread for command execution.

---

## 2. The 4 Fundamental Caching Patterns

```
Pattern 1: Cache-Aside (Lazy Loading) - Most Common
App ---> [Read Cache] --- (Hit) ---> Return Data
  |
  +---- (Miss) ---> [Read DB] ---> [Write Cache] ---> Return Data

Pattern 2: Write-Through
App ---> [Write Cache] ---> (Synchronous) ---> [Write DB]

Pattern 3: Write-Behind (Write-Back)
App ---> [Write Cache] ---> Return 200 OK
              |
              +---> (Async Queue/Worker) ---> [Batch Write to DB]

Pattern 4: Refresh-Ahead
Cache Engine / Cron ---> [Evaluates Access Frequency] ---> [Re-warms before TTL]
```

### Comparative Analysis Matrix

| Pattern | Read Path | Write Path | Consistency | Fault Tolerance | Ideal Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Cache-Aside** | Cache $\to$ DB fallback | DB first $\to$ Invalidate Cache | Eventual (short window) | High (cache drop degrades to DB) | General API endpoints, user profiles |
| **Write-Through** | Cache only | Cache $\to$ DB synchronously | Strong | Medium (write fails if either fails) | Financial ledgers, real-time counters |
| **Write-Behind** | Cache only | Cache only (DB updated async) | Weak / Eventual | Low (data loss if cache crashes before flush) | Audit telemetry, click tracking |
| **Refresh-Ahead** | Cache only | Background refresh | Strong | High | Hot analytics dashboards, top products |

---

## 3. Cache Invalidation Strategies: The Hardest Problem

> *"There are only two hard things in Computer Science: cache invalidation and naming things."* — Phil Karlton

### TTL vs. Event-Driven Invalidation
1. **Time-To-Live (TTL)**: Keys expire automatically after a set duration. Simple, self-cleaning, but tolerates stale data until expiration.
2. **Event-Driven Invalidation**: Upon data modification (`UPDATE`, `DELETE`), the application explicitly purges the cached key. Guarantees near-instant consistency.

### Cache Delete vs. Cache Update (Overwrite)
When data changes in the database, **should you update the cache or delete it?**

```
The Cache Update Race Condition (Why Overwriting is Flawed):
Thread A: Writes new value V1 to DB
Thread B: Writes new value V2 to DB
Thread B: Updates Cache with V2
Thread A: Updates Cache with V1 (Delayed network packet arrives out-of-order)
Result  : Database has V2, but Cache permanently stores stale V1!
```

**Production Rule**: Always **Delete (Invalidate)** the cache on write rather than updating it. If the cache is deleted, subsequent reads trigger Cache-Aside, fetching the latest committed value from the database.

---

## 4. Cache Stampede (Thundering Herd) & Mitigations

### The Failure Mechanism
A high-traffic hot key (e.g., product details for an iPhone launch with 10,000 requests/sec) expires. 
At that microsecond, 5,000 concurrent requests encounter a cache miss simultaneously. All 5,000 requests bypass the cache and hammer the primary relational database with the identical heavy query. The database CPU spikes to 100%, connection pools exhaust, and cascading outages crash upstream services.

```
                                  +---> [Query DB] --+
[5,000 Concurrent Requests] ----> +---> [Query DB] --+---> DATABASE CRASH!
(All miss expired key)            +---> [Query DB] --+
```

### Mitigation 1: Distributed Mutex / Single-Flight Pattern
Only the first request to encounter a cache miss acquires a distributed lock to query the database and re-populate the cache. All other concurrent requests wait, retry, or receive the previous stale value.

```python
async def get_with_mutex(key: str, db_fetch_fn):
    val = await redis.get(key)
    if val is not None:
        return val

    # Acquire lock to rebuild cache
    lock_key = f"lock:{key}"
    if await redis.set(lock_key, "1", nx=True, ex=5):
        try:
            val = await db_fetch_fn()
            await redis.set(key, val, ex=300)
            return val
        finally:
            await redis.delete(lock_key)
    else:
        # Another worker is rebuilding; wait and fetch
        await asyncio.sleep(0.05)
        return await get_with_mutex(key, db_fetch_fn)
```

### Mitigation 2: Probabilistic Early Expiration (XFetch Algorithm)
Instead of waiting for the key to expire, requests probabilistically recompute the cache *before* it expires based on computation cost and access frequency:

$$\Delta \cdot \beta \cdot \ln(\text{rand}()) > \text{TTL}_{\text{remaining}}$$

- $\Delta$: Time taken to compute the value.
- $\beta$: Aggressiveness coefficient ($\beta > 0$, typically 1.0).
- $\text{rand}()$: Uniform random number between 0 and 1.

As $\text{TTL}_{\text{remaining}}$ approaches 0, the probability of a background refresh approaches 100%, guaranteeing the key is refreshed before it expires without any downtime.

---

## 5. Cache Penetration & Bloom Filter Defenses

### Failure Mechanism
An attacker floods the system with requests for non-existent entities (e.g., `GET /users/-999999` or random UUIDs). 
Because the data does not exist in the database, it is never cached. Every request misses the cache and forces a full database query, creating an application-level Denial of Service (DoS).

### Remediation Strategies
1. **Cache Null Values**: When a database query returns empty, cache a placeholder with a short TTL (e.g., 30–60 seconds):
   `SET user:-999999 "NULL" EX 60`
2. **In-Memory Bloom Filter**: A space-efficient probabilistic data structure placed in front of Redis. If the Bloom filter returns `False`, the entity **definitely does not exist**, and the request is rejected immediately without touching Redis or the database.

---

## 6. Cache Breakdown & Hotspot Sharding

When a single key receives millions of operations per second (e.g., flash sales, breaking news), a single Redis node's network interface card (NIC) or single-threaded event loop saturates.

### Production Mitigations
1. **Two-Tier (L1/L2) Caching**: Store hot keys in an in-process LRU cache (e.g., `cachetools.LRUCache` in Python memory) with a 5-second TTL. 99.9% of reads hit local RAM in 50 nanoseconds, bypassing Redis completely.
2. **Key Sharding / Salted Replicas**: Distribute reads across multiple Redis keys:
   `hot_product:101:shard_1`, `hot_product:101:shard_2`, `hot_product:101:shard_N`.
   Clients randomly select a shard: `key = f"hot_product:101:shard_{random.randint(1, N)}"`.

---

## 7. Cache Avalanche & TTL Jitter

### Failure Mechanism
If a nightly cron job or bulk import sets 1,000,000 keys with an identical expiration (`ex=3600`), all 1,000,000 keys expire at the **exact same second**. The sudden drop in cache hit ratio creates an "avalanche" of queries to the database.

### Production Remediation: Randomized TTL Jitter
Never set uniform TTLs on bulk datasets. Always add deterministic random jitter:

$$\text{TTL}_{\text{effective}} = \text{Base TTL} \pm \text{Random}(\text{Jitter})$$

```python
import random

def get_jittered_ttl(base_ttl: int = 3600, jitter_pct: float = 0.15) -> int:
    delta = int(base_ttl * jitter_pct)
    return base_ttl + random.randint(-delta, delta)
```

---

## 8. Distributed Locking: Primitive `SETNX` vs. Production Leases

A distributed lock guarantees mutual exclusion across multiple stateless API server instances.

### The Naive `SETNX` Trap
```python
# CRITICAL BUG: The Unbreakable Deadlock
if await redis.setnx("lock:billing", "1"):
    # If the process crashes or gets OOM-killed HERE...
    process_billing()
    await redis.delete("lock:billing")  # NEVER EXECUTED! Lock hangs permanently!
```

### Production Primitive: Atomic `SET ... NX PX`
Modern Redis supports atomic lease acquisition in a single command:

$$\text{SET resource\_name unique\_token NX PX lease\_time\_ms}$$

- `NX`: Set only if the key does **not** already exist.
- `PX 10000`: Set automatic expiry to 10,000 milliseconds (10 seconds).
- `unique_token`: Cryptographically random UUID unique to this client instance.

---

## 9. Atomic Lock Release via Lua Scripts

Why can't you simply call `redis.delete("lock:billing")` when work completes?

```
The Split-Brain Release Catastrophe:
1. Client A acquires lock with 10s TTL.
2. Client A experiences a 12s stop-the-world garbage collection pause or network freeze.
3. The lock's 10s TTL expires. Redis automatically deletes the lock.
4. Client B acquires the lock with its own 10s lease.
5. Client A wakes up from the pause, finishes work, and calls redis.delete("lock:billing").
6. DISASTER: Client A just deleted Client B's active lock!
7. Client C immediately acquires the lock -> Both Client B and Client C are now in the critical section!
```

### Production Remediation: Atomic Lua Release
A client must only delete the lock if the key's value **matches its unique token**. This check-and-delete must execute **atomically** using a Lua script:

```lua
-- Lua script executed atomically on Redis server
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

---

## 10. The Martin Kleppmann vs. Salvatore Sanfilippo Redlock Debate

The **Redlock algorithm** (proposed by Salvatore Sanfilippo, creator of Redis) acquires locks across $N$ independent Redis master nodes (typically 5) using majority quorum ($N/2 + 1$).

### Martin Kleppmann's Critique
Distributed systems researcher Martin Kleppmann published a famous analysis proving that Redlock cannot guarantee mutual exclusion in asynchronous networks:
1. **Unbounded Process Pauses**: Stop-the-world GC, OS paging, hypervisor pauses can halt a client for longer than the lock lease.
2. **Clock Drift**: Redis node hardware clocks can step forward due to NTP adjustments, expiring locks prematurely.

### The Solution: Fencing Tokens
To guarantee safety against stale lock holders, the storage engine must enforce **Fencing Tokens** (monotonically increasing sequence numbers generated by the lock manager):

```
Client 1 acquires lock -> Receives Fencing Token = 31
Client 1 pauses... (Lock expires)
Client 2 acquires lock -> Receives Fencing Token = 32
Client 2 writes to DB: (Data, Token = 32) -> DB accepts (32 > 30)
Client 1 wakes up and attempts write: (Data, Token = 31) -> DB rejects (31 < 32)!
```

---

## 11. Dual-Write Consistency & Cache Invalidation

When updating an entity in a relational database, what is the exact execution order?

| Strategy | Execution Order | Failure Mode |
| :--- | :--- | :--- |
| **Update Cache Then DB** | 1. Cache Update $\to$ 2. DB Update | If DB update fails, cache holds dirty uncommitted phantom data. |
| **Update DB Then Cache** | 1. DB Update $\to$ 2. Cache Update | Concurrent writes result in out-of-order cache updates (stale cache). |
| **Delete Cache Then DB** | 1. Cache Delete $\to$ 2. DB Update | A concurrent read re-populates the cache with old DB data before DB update commits! |
| **Update DB Then Delete Cache** | 1. DB Update $\to$ 2. Cache Delete | **Production Standard**: Smallest possible staleness window. Combined with short TTL. |

---

## 12. Redis Data Structures for High-Performance Caching

| Data Structure | Primary Commands | Ideal Use Case |
| :--- | :--- | :--- |
| **Strings** | `GET`, `SET`, `INCRBY` | Serialized JSON payloads, session objects, atomic counters |
| **Hashes** | `HGET`, `HSET`, `HGETALL` | Entity partial updates (e.g., updating user status without rewriting entire profile) |
| **Sorted Sets (ZSET)** | `ZADD`, `ZRANGEBYSCORE`, `ZREMRANGEBYSCORE` | Sliding window rate limiters, leaderboards, delayed task queues |
| **Bitmaps / Bitfields** | `SETBIT`, `GETBIT`, `BITCOUNT` | High-density boolean tracking (e.g., user active on day $X$) |
| **HyperLogLog** | `PFADD`, `PFCOUNT` | Approximate unique visitor cardinality with 0.81% error in 12 KB memory |

---

## 13. Memory Eviction Policies (`maxmemory-policy`)

When Redis reaches its allocated RAM limit (`maxmemory`), it evicts keys based on its configured policy:

- `noeviction` (Default): Returns Out-Of-Memory errors on write operations. (Required for queues / brokers).
- `allkeys-lru`: Evicts least-recently-used keys across all keys. (Best for general-purpose caching).
- `volatile-lru`: Evicts least-recently-used keys among those with an explicit TTL.
- `allkeys-lfu`: Evicts least-frequently-used keys. (Ideal for distinguishing persistent hot keys from transient spikes).
- `volatile-ttl`: Evicts keys with the shortest remaining TTL.

---

## 14. Redis Pipelines & Multi/Exec Transactions

Network Round-Trip Time (RTT) dominates performance when issuing multiple Redis commands sequentially.

### Pipelining
Pipelining batches multiple commands and transmits them in a single network packet, reducing latency from $N \times \text{RTT}$ to $1 \times \text{RTT}$:

```python
async with redis.pipeline(transaction=True) as pipe:
    pipe.set("user:101", json_data, ex=300)
    pipe.incr("stats:user_updates")
    pipe.zadd("recent_active", {"user:101": time.time()})
    results = await pipe.execute()
```

---

## 15. Production Readiness & Observability Checklist

- [x] **Randomized TTL Jitter**: Every cached key applies a $\pm 10\text{–}15\%$ randomized TTL to prevent cache avalanches.
- [x] **Safe Distributed Locks**: Every lock uses `SET ... NX PX` with a unique UUID and releases via an atomic Lua script.
- [x] **Cache-Aside Invalidation**: Writes commit to the primary database first and immediately delete the corresponding Redis key.
- [x] **Thundering Herd Shield**: Hot queries implement single-flight mutexes or probabilistic early expiration.
- [x] **Graceful Degradation**: Application implements fallback logic so if Redis is temporarily unreachable, requests fall back directly to the primary database with circuit breaking.
- [x] **Metrics Tracking**: Prometheus counters track `cache_hits_total` and `cache_misses_total` to monitor cache hit ratios ($> 95\%$ target).
