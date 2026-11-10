# Senior & Staff Engineering Interview Q&A: Redis Caching & Distributed Locks

This document compiles the top 5 advanced architectural and systems-design interview questions regarding distributed caching, cache consistency, the Redlock debate, and distributed locking mechanisms, formulated for Senior and Staff Backend & AI Systems Engineers.

---

### Question 1: Why should an enterprise architecture almost always choose "Cache Delete on Write" over "Cache Overwrite", and what is the exact dual-write race condition?

#### Conceptual Answer
When data is updated in the primary database, developers face a choice:
1. **Cache Overwrite**: Update the database, then write the new value to Redis.
2. **Cache Invalidation (Delete on Write)**: Update the database, then delete the key in Redis.

In concurrent distributed environments, **Cache Overwrite is an architectural defect** because concurrent writes can arrive out-of-order at the cache, causing permanent data inconsistency.

#### The Out-of-Order Overwrite Race Condition
Consider two concurrent requests updating the same user entity:

```
Timeline of the Out-of-Order Race:
Time  | Thread 1 (User updates name to "Alice") | Thread 2 (User updates name to "Bob")
------+-----------------------------------------+---------------------------------------
T1    | Writes "Alice" to DB                    | 
T2    |                                         | Writes "Bob" to DB
T3    |                                         | Writes "Bob" to Redis Cache
T4    | Writes "Alice" to Redis (Delayed Net)   | 
------+-----------------------------------------+---------------------------------------
State | DB: "Bob"                               | Redis Cache: "Alice" (PERMANENTLY STALE!)
```

Because network transit times are non-deterministic, Thread 1's packet to Redis can be delayed by a few milliseconds and arrive *after* Thread 2's packet. Thread 1 overwrites Thread 2's newer value. The database contains `"Bob"`, but the cache permanently stores `"Alice"` until the key's TTL expires.

#### Why Cache Invalidation (Delete on Write) Prevents This
If both threads execute `redis.delete("user:101")`:
- Regardless of the order in which the `DEL` commands arrive at Redis, the key is removed.
- The next subsequent read triggers the **Cache-Aside** flow: it queries the database, reads the latest committed value (`"Bob"`), and repopulates the cache correctly.

#### Execution Order: Database First, Cache Delete Second
Always update and commit to the primary database **before** deleting the cache key:
- If you delete the cache first, a concurrent read can query the database *before* the update commits, reading old data and repopulating the cache with stale data.
- Updating the DB first and deleting the cache second limits the staleness window to a few milliseconds, which is further mitigated by short TTLs.

---

### Question 2: Explain the Thundering Herd (Cache Stampede) problem. Contrast Mutex Single-Flight vs. Probabilistic Early Expiration (XFetch), detailing their latency and CPU tradeoffs.

#### Conceptual Answer
A **Cache Stampede (Thundering Herd)** occurs when a high-traffic key (receiving hundreds or thousands of requests per second) expires or is evicted from the cache. At the millisecond of expiration, all concurrent incoming requests encounter a cache miss simultaneously. Every request bypasses the cache and floods the relational database with the identical heavy query, causing database connection exhaustion and cascading system outages.

#### Comparison of Mitigation Strategies

| Dimension | Mutex Single-Flight (Lock-Protected) | Probabilistic Early Expiration (XFetch) |
| :--- | :--- | :--- |
| **Mechanical Principle** | First request acquires distributed lock; remaining requests wait. | Evaluates probability formula to recompute in background *before* TTL expires. |
| **Database Protection** | **100% Protected**: Exactly 1 DB query executed. | **100% Protected**: Background worker pre-warms key before expiration. |
| **User-Facing Latency** | **Elevated Tail Latency (P99)**: Waiting requests wait for leader to finish. | **Zero Latency Penalty**: Client receives existing cached data instantly while background task refreshes. |
| **Complexity** | Requires distributed lock manager and retry/poll loops. | Requires storing computation duration ($\Delta$) and expiry metadata inside key. |
| **Best Used For** | Infrequently updated heavy queries, reports, cold starts. | Super-hot keys with continuous read traffic (e.g., homepage feeds, product details). |

#### The XFetch Algorithm Formula
The XFetch algorithm evaluates:

$$\Delta \cdot \beta \cdot \ln(\text{rand}()) > \text{TTL}_{\text{remaining}}$$

- As $\text{TTL}_{\text{remaining}} \to 0$, $-\ln(\text{rand}())$ ensures the probability of a background refresh approaches 1.0.
- $\beta > 1$ increases refresh aggressiveness for keys with expensive compute times ($\Delta$).
- The caller always receives an instant cache hit, while a detached background task refreshes Redis.

---

### Question 3: What was Martin Kleppmann's critique of Salvatore Sanfilippo's Redlock algorithm, and how do Fencing Tokens guarantee mutual exclusion in distributed storage?

#### Conceptual Answer
The **Redlock algorithm** was proposed by Salvatore Sanfilippo (creator of Redis) to provide fault-tolerant distributed locking across $N$ independent Redis master nodes (typically 5) using majority quorum ($N/2 + 1$).

In 2016, distributed systems researcher **Martin Kleppmann** published a detailed critique proving that Redlock is unsafe for systems where locking protects critical data from corruption (e.g., financial transactions, inventory deduplication).

#### Kleppmann's Two Core Critiques
1. **Unbounded Process Pauses**: In an asynchronous network, a client can experience a Stop-The-World (STW) garbage collection pause, OS page fault, or hypervisor suspension that lasts longer than the lock's lease time.
   - Client A acquires lock with 10s lease.
   - Client A pauses for 12 seconds due to GC.
   - The lock expires; Client B acquires the lock.
   - Client A resumes execution believing it still holds the lock, and performs a write.
   - **Both Client A and Client B modify the protected resource simultaneously.**
2. **Clock Drift and Jump**: Redlock relies on physical system clocks to determine lease expiration. If an NTP daemon steps the clock forward by a few seconds on one or more Redis nodes, locks expire prematurely.

#### The Solution: Fencing Tokens
Kleppmann proved that a lock manager **cannot guarantee mutual exclusion by itself**; the storage engine must cooperate by validating **Fencing Tokens**.

```
Client 1 acquires lock -> Receives Monotonic Token = 31
Client 1 pauses (STW GC)... Lock expires in Redis.
Client 2 acquires lock -> Receives Monotonic Token = 32
Client 2 writes to DB: (Data, Fencing Token = 32) -> DB accepts (32 > 30)
Client 1 wakes up and attempts write: (Data, Fencing Token = 31)
DB checks: 31 < 32 -> REJECTS WRITE!
```

By enforcing a database constraint (`WHERE version < :fencing_token`), stale lock holders can never corrupt state.

---

### Question 4: How does atomic Lua script execution prevent split-brain lock release in Redis, and what happens if a naive `DEL` command is used?

#### Conceptual Answer
In distributed locking, **lock release is just as critical as lock acquisition**. Using a naive `await redis.delete(lock_key)` creates a critical split-brain failure:

#### The Failure Scenario
1. Worker A acquires `lock:payroll` with a 5-second lease.
2. Worker A's payroll job unexpectedly stalls (e.g., slow database query or network partition) and takes 7 seconds.
3. At second 5, Redis evicts the key due to lease expiration.
4. Worker B acquires `lock:payroll` with its own 5-second lease.
5. At second 7, Worker A finishes its job and executes `await redis.delete("lock:payroll")`.
6. **DISASTER**: Worker A has just deleted **Worker B's active lock**!
7. Worker C immediately requests the lock, successfully acquires it, and enters the critical section while Worker B is still actively executing!

#### The Lua Solution
To prevent this, every lock acquisition writes a **cryptographically unique token** (e.g., `UUIDv4`) as the key's value. 
Releasing the lock requires checking: *"Does the value currently stored in Redis match my UUID token? If yes, delete it; if no, do nothing."*

Because this check-and-delete must be 100% atomic, it is executed via a Lua script:

```lua
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

Redis executes Lua scripts as a single, uninterrupted transaction on its single-threaded event loop. No other Redis command can interleave between the `GET` and the `DEL`, completely eliminating split-brain release.

---

### Question 5: When would you design a Two-Tier (L1 Local Memory + L2 Redis) caching architecture, and how do you solve the cache coherence problem across distributed instances?

#### Conceptual Answer
In ultra-high-scale platforms (e.g., 500,000+ requests per second), querying Redis over the network introduces overhead:
- Network round-trip time (RTT) adds **0.2 to 0.8 ms** per request.
- A single Redis cluster node can bottleneck on network bandwidth or CPU serialization.

A **Two-Tier (L1/L2) Caching Architecture** pairs:
- **L1 In-Process Cache**: Local RAM inside the Python application (e.g., `cachetools.LRUCache`). Retrieves data in **10 to 50 nanoseconds** ($O(1)$ RAM access).
- **L2 Distributed Cache**: Central Redis cluster. Retrieves data in **0.5 milliseconds**.

```
Request ---> [Check L1 (Local RAM)] --- (Hit: ~50ns) ---> Return
                 |
                 +---- (Miss) ---> [Check L2 (Redis)] --- (Hit: ~500µs) ---> Populate L1 ---> Return
                                       |
                                       +---- (Miss) ---> [Query DB] ---> Populate L2 & L1 ---> Return
```

#### The Cache Coherence Problem
If Server 1 updates a record, it updates the DB and deletes the key in Redis (L2). However, Server 2 still has the old value cached in its local memory (L1), serving stale data for minutes.

#### Production Solution: Pub/Sub Invalidation Bus
1. When Server 1 updates data, it commits to the DB, invalidates L2 Redis, and **publishes an invalidation message** to a Redis Pub/Sub channel:
   `redis.publish("cache:invalidation", "user:101")`
2. Every application server runs an asynchronous background subscriber task listening to `cache:invalidation`.
3. When Server 2 receives the message, it immediately evicts `"user:101"` from its local L1 cache.
4. Alternatively, in Redis 6+, **Client-Side Caching (Tracking Mode)** enables Redis to track keys read by client connections and automatically push invalidation messages when those keys are modified by other clients.
