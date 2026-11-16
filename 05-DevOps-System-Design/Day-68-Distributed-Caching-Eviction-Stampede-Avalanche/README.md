# Day 68: Distributed Caching: Eviction Policies, Stampede & Avalanche Defense

An exhaustive architectural blueprint for Principal, Staff, and Senior Distributed Systems Engineers: memory latency hierarchy, caching topologies (Cache-Aside, Read-Through, Write-Through, Write-Behind), eviction algorithms ($O(1)$ LRU, LFU, FIFO, TTL), cache failure modes (Stampede, Avalanche, Penetration), the XFetch probabilistic early expiration algorithm, Bloom filters, and dual-write consistency.

---

## 1. The Distributed Memory Hierarchy & Caching Economics

In distributed system design, the cache is not an optimization—it is the **primary survival barrier protecting the stateful database tier**.

```
                           THE MEMORY LATENCY HIERARCHY
+─────────────────────────+──────────────────────────+─────────────────────────+
| Tier                    | Access Latency           | Relative Scale          |
+─────────────────────────+──────────────────────────+─────────────────────────+
| CPU L1/L2 Cache         | 0.5 – 7.0 ns             | Fastest (Single Core)   |
| Main Memory (RAM)       | 100 ns (0.1 µs)          | Baseline Speed          |
| In-Memory Cache (Redis) | 500 µs – 1.5 ms          | 10,000x faster than DB  |
| NVMe SSD Sequential     | 10 – 50 µs               | Disk IO                 |
| Relational DB Query     | 5 – 50 ms                | Network + Locks + Disk  |
| Cross-Region DB Replica | 50 – 150 ms              | WAN Round Trip          |
+─────────────────────────+──────────────────────────+─────────────────────────+
```

### The Cache Hit Ratio Sensitivity Equation
Overall effective read latency is governed by the **Cache Hit Ratio ($H$)**:

$$L_{\text{effective}} = (H \times L_{\text{cache}}) + ((1 - H) \times L_{\text{database}})$$

Consider a cluster receiving $100,000\text{ QPS}$:
- At **$99\%$ Hit Ratio** ($H = 0.99$):
  Database receives $1\% \implies 1,000\text{ QPS}$. (Database handles easily).
- At **$90\%$ Hit Ratio** ($H = 0.90$):
  Database receives $10\% \implies 10,000\text{ QPS}$.
- **The Non-Linear Surge**: A minor drop of only $9\%$ in cache hit ratio causes a **$10\times$ ($1,000\%$) surge in traffic to the database**, instantly exhausting connection pools and crashing read replicas!

---

## 2. Caching Topologies & Write Strategies

```
                             CACHING PATTERNS MATRIX
  1. CACHE-ASIDE (Lazy Loading)                  2. WRITE-THROUGH
┌─────────────┐   (1) Read Cache   ┌───────┐   ┌─────────────┐  (1) Write Cache  ┌───────┐
│ Application ├───────────────────►│ Cache │   │ Application ├──────────────────►│ Cache │
└──────┬──────┘                    └───────┘   └─────────────┘                   └───┬───┘
       │ (2) Miss: Read DB                           (2) Synchronous Write DB        │
       ▼                                              (Before ACK to Client)         ▼
┌─────────────┐                                                                ┌─────────┐
│  Database   │                                                                │Database │
└─────────────┘                                                                └─────────┘
  3. WRITE-BEHIND (Write-Back)                   4. READ-THROUGH
┌─────────────┐  (1) Write Cache   ┌───────┐   ┌─────────────┐  (1) Read Request ┌───────┐
│ Application ├───────────────────►│ Cache │   │ Application ├──────────────────►│ Cache │
└─────────────┘ (Instant ACK!)     └───┬───┘   └─────────────┘                   └───┬───┘
                                       │                                             │
             (2) Async Batched Writes  ▼                               (2) Miss:     ▼
             (Risk of data loss on OOM)┌───────┐                        Cache reads  ┌───────┐
                                       │  DB   │                        transparently│  DB   │
                                       └───────┘                                     └───────┘
```

### 1. Cache-Aside (Lazy Loading)
- **Workflow**: Application queries cache. If data exists (Hit), returns. If absent (Miss), reads from database, populates cache, and returns.
- **Pros**: Only requested data is cached (RAM efficient); resilient to cache outages (falls back to DB).
- **Cons**: Cache miss penalty on cold reads; vulnerable to stale data without explicit eviction.

### 2. Read-Through & Write-Through
- **Workflow**: The application treats the cache as the single source of truth. The cache library or proxy (e.g. AWS DAX for DynamoDB) transparently reads from or synchronously writes to the database.
- **Pros**: Clean application logic; zero stale read window on writes.
- **Cons**: Write latency increases because every write must wait for both cache and database persistence.

### 3. Write-Behind (Write-Back)
- **Workflow**: Writes are written to cache only and immediately acknowledged. A background worker periodically flushes batched dirty entries to the database.
- **Pros**: Wire-speed write latency; absorbs massive write spikes (e.g. game leaderboards, IoT streams).
- **Cons**: **Risk of permanent data loss** if the cache node crashes or reboots before flushing dirty memory buffers to disk.

---

## 3. Cache Eviction Policies: LRU vs. LFU vs. FIFO

When cache memory fills to capacity (`maxmemory`), the engine must evict existing keys to accommodate new writes.

```
                           EVICTION ALGORITHMS COMPARISON
+─────────────────────────────────────────────────────────────────────────────+
| 1. LRU (Least Recently Used)                                                |
|    Evicts items not accessed for the longest time.                          |
|    Data Structure: Hash Map + Doubly Linked List (O(1) Get / Put / Evict)   |
|    [HEAD (Most Recent)] ◄──► [Node A] ◄──► [Node B] ◄──► [TAIL (Evict!)]    |
+─────────────────────────────────────────────────────────────────────────────+
| 2. LFU (Least Frequently Used)                                              |
|    Evicts items with lowest access count.                                   |
|    Data Structure: Hash Map + Frequency Doubly Linked Lists (O(1) O'Neil)   |
|    Freq=1: [Node C] ──► Freq=2: [Node B] ──► Freq=50: [Node A (Hot Key)]   |
+─────────────────────────────────────────────────────────────────────────────+
| 3. FIFO (First In, First Out)                                               |
|    Evicts oldest inserted item regardless of access frequency or recency.   |
|    Data Structure: Queue / Circular Buffer. (High miss ratio under skew).   |
+─────────────────────────────────────────────────────────────────────────────+
```

### The Vulnerability of Pure LRU: Cache Pollution
Under pure LRU, an analytical batch script or database dump that reads $1,000,000$ cold keys once will flush all hot, frequently-accessed items out of cache memory (Cache Pollution).

### LFU Frequency Decay
LFU prevents pollution by tracking access frequency. However, legacy items accessed $100,000$ times in the past but never accessed today can become permanent "cache ghosts." Production LFU engines (e.g., Redis LFU) implement **frequency decay**: decrementing the counter proportionally to the time elapsed since the last access.

---

## 4. Cache Failure Mode 1: Cache Stampede (Thundering Herd)

### The Catastrophe Scenario
A celebrity with 80 million followers tweets breaking news. The tweet payload is cached under key `tweet:9999` with 50,000 QPS.
At time $T = 12:00:00$, the key's TTL expires.

```
                      THE CACHE STAMPEDE DISASTER
                         50,000 Requests/sec
                                  │
                                  ▼
                     ┌────────────────────────┐
                     │ Cache: Key Expired!    │
                     └────────────┬───────────┘
                                  │ (50,000 Concurrent Misses!)
                                  ▼
                     ┌────────────────────────┐
                     │ DATABASE TIER (CRASH!) │
                     │ 50,000 concurrent DB   │
                     │ complex SQL queries!   │
                     └────────────────────────┘
```

Within 10 milliseconds:
1. 50,000 concurrent worker threads all observe a cache miss.
2. All 50,000 threads simultaneously issue the expensive database query.
3. Database CPU spikes to 100%, query latency balloons from $5\text{ms}$ to $30\text{s}$, and connection pools drop dead.

---

## 5. Stampede Defense: Mutex Locking vs. Probabilistic Early Expiration (XFetch)

### Defense 1: Distributed Mutex Locking (Single-Flight)
Only the first thread that experiences a cache miss is allowed to acquire a distributed lock (e.g. via `SET resource_lock my_random_token NX PX 5000` in Redis) to recompute the value. All other threads wait or return a stale cached value.

```python
# Mutex Single-Flight Invalidation
val = cache.get(key)
if val is None:
    if redis.set(f"lock:{key}", "1", nx=True, ex=5):
        # Only ONE thread queries the DB
        val = db_query(key)
        cache.set(key, val, ttl=300)
        redis.delete(f"lock:{key}")
    else:
        # Other 49,999 threads sleep briefly and retry cache
        time.sleep(0.05)
        val = cache.get(key)
```

---

### Defense 2: The Optimal Solution — XFetch Algorithm (Vattani et al., VLDB 2015)
Instead of waiting for the key to physically expire, **probabilistically recompute the key before it expires**.

The XFetch algorithm evaluates:

$$\text{Recompute if: } -\beta \times \Delta \times \ln(\text{random}()) > (\text{Expiry} - \text{Current Time})$$

Where:
- $\Delta$: The time taken to compute the value from the database (in seconds).
- $\beta$: Eagerness parameter ($\beta > 0$, default $1.0$).
- $\text{random}()$: Uniform random floating point in $(0, 1]$.
- $\ln(\text{random}())$: Negative logarithm, producing an exponential distribution tail.

```
                           XFETCH PROBABILITY PROFILE
Probability of Background Refresh
  1.0 ▲                                            / Physically Expired (P=1.0)
      │                                           /
      │                                          /
      │                                    _____/
      │                              _____/
      │                        _____/
      │                  _____/
      │            _____/
  0.0 └───────────/─────────────────────────────────────────────► Time to Expiry
      T - 60s           T - 10s       T - 2s      T (Expiry)
```

#### Why XFetch is Mathematically Superior:
- When a key is far from expiration, the probability of recomputation is $0\%$.
- As expiration nears ($T - 5\text{s}$), the probability smoothly rises.
- If request traffic is heavy (e.g., $10,000\text{ QPS}$), **statistically exactly one request will trigger the early background refresh** while returning the current valid cached data to the user.
- **The key NEVER expires in the cache, and the database NEVER receives more than 1 concurrent query!**

---

## 6. Cache Failure Mode 2: Cache Avalanche & TTL Jitter

### The Catastrophe Scenario
During a nightly batch synchronization or system boot, 2,000,000 product catalog entries are bulk-loaded into Redis at 03:00:00 AM with a fixed TTL: `TTL = 3600 seconds` (1 hour).

```
                        THE CACHE AVALANCHE COLLAPSE
Memory Key Count
  2M ▲──────────────────────────────────────────┐
     │                                          │
     │   2 Million Keys Active in RAM           │  AT 04:00:00 AM (1 HOUR LATER):
     │                                          │  ALL 2M KEYS EXPIRE SIMULTANEOUSLY!
     │                                          │
   0 └──────────────────────────────────────────┴─────────────────────────────► Time
                                                04:00:00 AM (Database Death)
```

At 04:00:00 AM, all 2,000,000 keys expire at the exact same physical second. Every incoming user query across the entire website produces an immediate cache miss, transferring 100% of global web traffic to the database tier.

### Production Remediation: TTL Jitter
Never set identical static TTLs on batch keys. Always add **pseudorandom uniform jitter**:

$$\text{TTL}_{\text{actual}} = \text{TTL}_{\text{base}} + \text{random\_uniform}(-\text{Jitter}, +\text{Jitter})$$

Example:
```python
import random

BASE_TTL = 3600   # 1 hour
JITTER = 600      # +- 10 minutes

# Keys now expire evenly distributed between 50 and 70 minutes
actual_ttl = BASE_TTL + random.randint(-JITTER, JITTER)
cache.set(key, value, ex=actual_ttl)
```

---

## 7. Cache Failure Mode 3: Cache Penetration & Bloom Filter Defense

### The Catastrophe Scenario
A malicious attacker scripts a botnet to query 50,000 non-existent user IDs per second (`/api/users/-999999`, `/api/users/deadbeef-random-uuid`).
- The key is **not in cache**.
- The key is **not in database**.
- Every single request punches directly through the cache and forces a full database index scan.

```
                         CACHE PENETRATION ATTACK
[Attacker Botnet] ──GET /user/-99999──► [Cache: Miss!] ──► [Database: Scans table!]
                                                                │
                                                                ▼ (Row Not Found)
[Attacker Botnet] ◄──── HTTP 404 ◄──────────────────────────────┘
(Key is never cached because value is null! Repeated queries continue hammering DB!)
```

---

### Defense 1: Cache Null Objects with Short TTL
When the database returns `None` / `NotFound`, cache a dummy tombstone value with a tight expiration (e.g. $60\text{ seconds}$):

```python
val = cache.get(user_id)
if val == "NULL_TOMBSTONE":
    return None  # Instant 404 from cache memory!
if val is None:
    val = db.find_user(user_id)
    if val is None:
        cache.set(user_id, "NULL_TOMBSTONE", ex=60)
        return None
    cache.set(user_id, val, ex=3600)
return val
```

---

### Defense 2: Probabilistic Bloom Filter Guard
A **Bloom Filter** is a space-efficient probabilistic data structure that tests whether an element is a member of a set:
- **Possible Outcomes**:
  1. *"Element is DEFINITELY NOT in the set"* ($100\%$ accuracy $\implies$ Instant rejection without hitting Cache or DB).
  2. *"Element MIGHT be in the set"* (Small false-positive probability $p \approx 0.01$).

```
                         BLOOM FILTER PRE-FLIGHT GUARD
Incoming Request: GET /user/998822
                 │
                 ▼
     ┌────────────────────────┐
     │      BLOOM FILTER      │
     └───────────┬────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
   "Definitely No"   "Possibly Yes"
        │                 │
        ▼                 ▼
   Return 404!      Check Cache / DB
 (0 DB queries!)
```

A Bloom filter containing 100,000,000 keys with a $1\%$ false-positive rate consumes only **$\approx 114\text{ MB}$ of RAM**!

---

## 8. Dual-Write Consistency & Cache Invalidation

When updating state in a system with Cache-Aside, developers face two decisions:
1. Should you **Update the Cache** or **Delete from the Cache**?
2. Should you update the Database **First** or the Cache **First**?

### The Race Condition of "Update Cache"
```
❌ UPDATING CACHE CREATES PERMANENT DATA CORRUPTION
Thread 1 (Writes $100)           Thread 2 (Writes $200)
─────────────────────           ─────────────────────
(1) Update DB to $100
                                (2) Update DB to $200
                                (3) Update Cache to $200
(4) Update Cache to $100 (Delayed!)
──────────────────────────────────────────────────────
Result: DB is $200, but Cache is PERMANENTLY $100!
```

### The Production Standard: Delete from Cache AFTER Database Commit
Always **delete (invalidate)** the cache entry after the database transaction successfully commits:

```python
# ✅ PRODUCTION SAFE CACHE-ASIDE WRITE
async def update_user_email(user_id: int, new_email: str):
    async with db.transaction():
        # 1. Update authoritative source of truth
        await db.execute("UPDATE users SET email = :e WHERE id = :id", {"e": new_email, "id": user_id})
    
    # 2. Invalidate cache AFTER transaction commit
    await cache.delete(f"user:{user_id}")
```

### Delayed Double Deletion (Defending Asynchronous Replicas)
If your database has read replicas, a concurrent read during an update could read stale data from a lagging replica and repopulate the cache with the old value.
- **Solution**: Execute a **Delayed Double Deletion**:
  1. Delete cache key.
  2. Commit DB update.
  3. Schedule a second deletion in background after $500\text{ms}$ (exceeding replication lag) to purge any stale repopulated entries.

---

## 9. Architectural Decision Matrix: Caching Solutions

| Scenario / Constraint | Optimal Strategy | Primary Defense Mechanism |
| :--- | :--- | :--- |
| **Hot Key Read Burst (50k QPS)** | Cache-Aside + XFetch | Probabilistic Early Expiration (zero downtime refresh) |
| **Mass Key Expiration at Midnight**| TTL Randomization | Base TTL + $\pm 20\%$ Uniform Jitter |
| **Malicious Botnet 404 Probing** | Pre-flight Filtering | Bloom Filter + Null Object Caching (60s TTL) |
| **Heavy Write Burst Absorption** | Write-Behind (Write-Back) | Batched async DB flush (Accepts data loss risk) |
| **Zero Tolerance for Stale Data**| Write-Through | Synchronous dual-commit to Cache and DB |
| **High Memory Pressure with Skew**| LFU with Decay | Protects against single-pass batch scan pollution |

---

## 10. Summary: Core Distributed Caching Tenets

1. **A cache miss is an existential threat**: A drop in hit ratio translates to a multi-fold exponential amplification in database load.
2. **Never allow hot keys to hard-expire**: Use the XFetch algorithm to background-refresh high-traffic keys before they elapse.
3. **Always jitter batch TTLs**: Static expirations synchronize traffic spikes into destructive avalanches.
4. **Invalidate, never update**: Purge cache entries on write mutations rather than updating them, preventing concurrent out-of-order race conditions.
5. **Guard the perimeter with Bloom filters**: Stop non-existent key penetrations before they consume a single database thread.
