# Day 68: Distributed Caching — Principal / Staff Engineering Interview Questions

In-depth technical analyses for Principal, Staff, and Senior Distributed Systems Engineers covering caching mathematics, stampede defense, probabilistic algorithms, Bloom filters, and cache consistency.

---

## Question 1: How does the Cache Hit Ratio mathematically govern backend database load, and why does a seemingly minor drop from 99% to 90% hit ratio cause a catastrophic 10x surge in database query throughput?

### Candidate Answer
In distributed systems, the backend database tier is dimensioned to absorb only the **residual miss traffic** leaking through the caching layer.

### The Mathematical Model
Let:
- $Q$: Total incoming client request rate (e.g., $100,000\text{ QPS}$).
- $H$: Cache Hit Ratio ($0.0 \le H \le 1.0$).
- $M = (1 - H)$: Cache Miss Ratio.
- $Q_{\text{DB}}$: Resulting Query Rate reaching the database.

$$Q_{\text{DB}} = Q \times (1 - H)$$

```
                     HIT RATIO DROP VS. DATABASE LOAD SURGE
Cache Hit Ratio (H)       Miss Ratio (1 - H)       Database QPS (Q = 100k)
──────────────────────────────────────────────────────────────────────────
99.0% (0.99)              1.0% (0.01)               1,000 QPS (Baseline)
98.0% (0.98)              2.0% (0.02)               2,000 QPS (2x Surge)
95.0% (0.95)              5.0% (0.05)               5,000 QPS (5x Surge)
90.0% (0.90)             10.0% (0.10)              10,000 QPS (10x Surge!)
80.0% (0.80)             20.0% (0.20)              20,000 QPS (20x Surge!)
```

### The Amplification Factor
The ratio of new database load to original database load when hit ratio drops from $H_1$ to $H_2$ is:

$$\text{Amplification Factor} = \frac{1 - H_2}{1 - H_1}$$

Substituting $H_1 = 0.99$ and $H_2 = 0.90$:

$$\text{Amplification Factor} = \frac{1 - 0.90}{1 - 0.99} = \frac{0.10}{0.01} = \mathbf{10.0 \times \text{ (1,000\% increase!)}}$$

### Production SRE Implication
- While an engineer might view a drop from 99% to 90% as an "acceptable 9% dip in efficiency", to the relational database (PostgreSQL/MySQL), it represents an instantaneous **ten-fold traffic tsunami**.
- If the database was provisioned for 1,000 QPS at 30% CPU utilization, 10,000 QPS immediately saturates CPU to 100%, exhausts connection pools (`max_connections = 500`), triggers queueing latency explosions, and causes cascading 504 Gateway Timeouts across the entire application.

---

## Question 2: Deep-dive into the XFetch algorithm (Vattani et al., VLDB 2015). How does its probabilistic early expiration formula work, and why is it superior to static cron refreshes or mutex locking for hot keys?

### Candidate Answer
When an ultra-hot cache key expires under heavy traffic ($50,000\text{ QPS}$), thousands of concurrent requests simultaneously miss the cache and overwhelm the database (**Cache Stampede / Thundering Herd**).

### 1. Traditional Defenses and Their Weaknesses
1. **Static Background Cron Refresh**: Pre-refreshing keys every $N$ seconds requires maintaining an exhaustive list of all active keys, wasting compute on cold keys that nobody is reading.
2. **Distributed Mutex Locking (Single-Flight)**: Only one thread queries the database while other threads block. However, blocked threads consume worker threads and socket buffers; if the database takes $500\text{ms}$ to compute, 25,000 requests are stalled in memory waiting for the lock.

---

### 2. The XFetch Algorithm (Optimal Probabilistic Refresh)
In their seminal 2015 VLDB paper *"Optimal Probabilistic Cache Stampede Prevention"*, Babak Vattani, Flavio Chierichetti, and Kevin Low formulated **XFetch**.

Instead of waiting for a key to reach its physical expiration timestamp, **each read request probabilistically decides whether to refresh the key early**:

$$\text{Background Recompute if: } -\beta \times \Delta \times \ln(U) > (\text{Expiry} - \text{Now})$$

Where:
- $\Delta$ (Delta): The wall-clock execution time taken to compute the value from the database (in seconds).
- $\beta$ (Beta): An eagerness constant ($\beta > 0$, typically $1.0$). Higher $\beta$ triggers earlier refresh.
- $U$: A uniform random variable drawn from $(0, 1]$.
- $\ln(U)$: The natural logarithm of $U$, yielding a negative number ranging from $0$ down to $-\infty$.
- $\text{Expiry} - \text{Now}$: The remaining lifetime of the key (time to physical expiration).

```
                            XFETCH DYNAMICS OVER TIME
                                 Key Expiry Point
                                        ▼
Timeline: ────────────[Far from Expiry]──────────[Near Expiry]────────► Expiry
Time to Expiry:              50s                      0.5s                0s
ln(U) term:             Typically -0.5 to -3     Typically -0.5 to -3
-beta * Delta * ln(U):   ~ 0.05s to 0.3s          ~ 0.05s to 0.3s
Condition:              0.3s > 50s? (FALSE)      0.3s > 0.5s? (TRUE! REFRESH!)
```

### Why XFetch is Mathematically Optimal:
1. **Zero Client Latency**: The user whose request triggers the early refresh receives the **current valid cached value immediately**. The recomputation happens asynchronously in the background.
2. **Self-Regulating with Traffic**: The higher the request rate, the greater the statistical guarantee that *exactly one request* will hit the probabilistic trigger before the key expires.
3. **No Distributed Locks Required**: Because requests refresh the key *before* expiration, the key **never physically expires in the cache**, completely eliminating stampedes without distributed mutex coordination.

---

## Question 3: Compare Cache-Aside, Read-Through, Write-Through, and Write-Behind. What is the exact failure scenario of Write-Behind under a cache node crash?

### Candidate Answer

| Pattern | Write Path | Read Path | Latency Profile | Primary Vulnerability |
| :--- | :--- | :--- | :--- | :--- |
| **Cache-Aside** | App writes DB; App deletes Cache key. | App reads Cache; on miss reads DB & populates. | Fast reads; moderate writes. | Dual-write race conditions; cold miss latency. |
| **Read-Through** | App writes DB directly. | App queries Cache; Cache transparently loads DB on miss. | Simplifies app code; encapsulated. | Cache layer must maintain DB drivers and schemas. |
| **Write-Through**| App writes Cache; Cache synchronously writes DB before ACK. | App queries Cache. | Higher write latency (2 hops); zero stale data. | Uncached write penalty; write amplification. |
| **Write-Behind** | App writes Cache; Cache ACKs immediately; batches writes to DB async. | App queries Cache. | **Lowest write latency** (wire-speed NVMe RAM). | **Permanent data loss on crash/reboot.** |

### The Write-Behind Catastrophic Failure Scenario
Write-Behind (Write-Back) is designed for massive write throughput (e.g. logging 500,000 IoT metrics/sec or gaming leaderboards):
1. A client submits a balance transfer: *"Credit User 101 with \$5,000"*.
2. The application writes to Redis. Redis appends the mutation to an in-memory queue (`dirty_keys`) and **immediately returns HTTP 200 Success** to the client.
3. A background daemon is configured to flush `dirty_keys` to PostgreSQL every 5 seconds.
4. **The Crash Event**: At second 3, the Redis host experiences a physical power failure, hypervisor crash, or Linux Out-Of-Memory (OOM) panic.
5. **The Impact**:
   - The volatile RAM containing the un-flushed `dirty_keys` is **permanently lost**.
   - The client received a valid receipt confirming their \$5,000 credit.
   - The PostgreSQL database has zero record of the transaction.
   - **Financial and Audit Disaster**: The ledger is irreparably out of balance.
- **Rule of Thumb**: Never use Write-Behind for non-reproducible financial, audit, or user account state. Use it exclusively for loss-tolerant analytics counters or ephemeral presence.

---

## Question 4: How do Bloom Filters work mathematically ($m, k, p$), and how do they defend against Cache Penetration attacks? Why can standard Bloom filters not support element deletions?

### Candidate Answer

### 1. Mathematical Mechanics of Bloom Filters
A **Bloom Filter** (Burton Howard Bloom, 1970) is a space-efficient probabilistic data structure used to test set membership with **zero false negatives**:
- It can return:
  1. *"Element is definitely NOT in set"* ($100\%$ confidence).
  2. *"Element is POSSIBLY in set"* (with configurable false-positive probability $p$).

Given $n$ expected items and desired false-positive probability $p$:
1. **Optimal Bit Array Size ($m$)**:
   $$m = -\frac{n \times \ln(p)}{(\ln 2)^2}$$
2. **Optimal Number of Independent Hash Functions ($k$)**:
   $$k = \frac{m}{n} \times \ln 2$$

For $n = 10,000,000$ users and $p = 0.01$ (1% false positive):
- $m \approx 95,850,583 \text{ bits} \approx \mathbf{11.4 \text{ Megabytes of RAM}}$!
- $k \approx 7 \text{ hash functions}$.

### 2. Defending Against Cache Penetration Attacks
A **Cache Penetration attack** occurs when an attacker queries millions of non-existent keys (e.g. `GET /user/-9999999` or random UUIDs).
- Without a Bloom filter: Every request misses the cache, hits the database, scans the B-Tree index, and returns `404 Not Found`. 50,000 QPS of non-existent keys crashes the database.
- With a Bloom filter: The filter is seeded with all existing user IDs.
  - When the attacker queries `/user/-9999999`, the Bloom filter evaluates the $k$ bit positions.
  - At least one bit is `0`.
  - The API gateway returns **HTTP 404 in $<1\mu\text{s}$**, without executing a single Redis call or database query!

### 3. Why Standard Bloom Filters Cannot Support Deletions
- In a standard Bloom filter, setting a bit is an irreversible OR operation (`bit_array[pos] |= 1`).
- Multiple distinct elements share common bit positions due to hash collisions.
- If you attempt to delete `"user_101"` by clearing its bit positions to `0`, you will inadvertently clear bits belonging to `"user_202"`, causing subsequent queries for `"user_202"` to return `False` (creating a **false negative**, which violates the fundamental invariant of Bloom filters!).
- **Solution (Counting Bloom Filter)**: Replaces single bits with 4-bit counters. Inserting increments the counter; deleting decrements the counter.

---

## Question 5: What is the Dual-Write Cache Consistency race condition between Database and Cache, and why is "Delete from Cache after Commit" the enterprise standard?

### Candidate Answer

### 1. The Dual-Write Race Condition
When an application updates data under Cache-Aside, developers often try to "Update the Cache" directly:

```
                  THE "UPDATE CACHE" RACE CONDITION
Client A (Update bal to $100)          Client B (Update bal to $200)
─────────────────────────────          ─────────────────────────────
Step 1: Write DB = $100
                                       Step 2: Write DB = $200
                                       Step 3: Write Cache = $200
Step 4: Write Cache = $100 (Delayed!)
────────────────────────────────────────────────────────────────────
Result: Database holds $200 (Authoritative), but Cache holds $100!
All subsequent user reads return stale $100 data permanently!
```
Due to unpredictable thread scheduling and network transport jitter, cache write arrivals can invert the database commit order.

---

### 2. Why "Delete from Cache AFTER Commit" is Superior
Instead of updating the cache, the application **invalidates (deletes)** the cache key:

```python
async with db.transaction():
    await db.update(...)  # Commit to authoritative source
await cache.delete(key)   # Invalidate
```

1. **Idempotence**: Deleting a key is idempotent. If two deletions arrive out of order, the key is simply deleted.
2. **Lazy Reconciliation**: The next read request experiences a clean cache miss, reads the latest committed value from the database, and repopulates the cache with the correct state.

---

### 3. The Edge Case: Lagging Read Replicas & Delayed Double Deletion
In a database cluster with asynchronous read replicas, a subtle race condition remains:
1. Thread 1 deletes cache key.
2. Thread 1 commits update to Primary.
3. Thread 2 reads from a **lagging read replica** (which has not yet applied the WAL commit), reads old data, and repopulates the cache with the stale value!
4. **Remediation: Delayed Double Deletion**:
   - The application deletes the cache key before the write.
   - Commits the write to the database.
   - Spawns an asynchronous task to **delete the cache key a second time after a delay** (e.g., $500\text{ms}$, exceeding maximum replica lag).
   - This purges any stale data that was erroneously repopulated during the replication window.
