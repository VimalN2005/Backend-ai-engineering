# Day 68: Distributed Caching — 5-Minute Revision & Cheat Sheet

A high-density reference sheet covering caching mathematics, eviction algorithms, stampede prevention, Bloom filter formulas, and cache failure defense patterns.

---

## 1. Core Mathematical Formulas Cheat Sheet

### Cache Hit Ratio & Effective Latency
$$L_{\text{effective}} = (H \times L_{\text{cache}}) + ((1 - H) \times L_{\text{database}})$$
- $H$ = Cache Hit Ratio ($0.0 \le H \le 1.0$)
- **Database Load Amplification Factor** when hit ratio drops from $H_1$ to $H_2$:
  $$\text{Amplification} = \frac{1 - H_2}{1 - H_1} \quad (\text{e.g. } 99\% \to 90\% = 10\times \text{ surge!})$$

### XFetch Probabilistic Early Expiration (VLDB 2015)
$$\text{Recompute if: } -\beta \times \Delta \times \ln(U) > (\text{Expiry} - \text{Now})$$
- $\Delta$ = Execution time taken to compute value from database (seconds).
- $\beta$ = Eagerness multiplier ($\beta \ge 1.0$).
- $U$ = Uniform random variable in $(0, 1]$.
- Mathematically guarantees exactly 1 background refresh before key expires under high QPS.

### Bloom Filter Sizing Formulas
- **Optimal Bit Array Size ($m$)**:
  $$m = -\frac{n \times \ln(p)}{(\ln 2)^2} \approx -2.08 \times n \times \ln(p)$$
- **Optimal Hash Function Count ($k$)**:
  $$k = \frac{m}{n} \times \ln 2 \approx 0.693 \times \frac{m}{n}$$
- Example: $10\text{M}$ keys with $1\%$ false-positive rate requires only $\mathbf{\approx 11.4 \text{ MB}}$ of RAM!

---

## 2. Cache Failure Modes & Production Defenses

| Failure Mode | Trigger / Root Cause | Catastrophic Impact | Production Defense |
| :--- | :--- | :--- | :--- |
| **Cache Stampede (Thundering Herd)** | Single hot key ($50\text{k QPS}$) expires | Thousands of concurrent DB queries | **XFetch Algorithm** or **Single-Flight Mutex** |
| **Cache Avalanche** | Millions of keys expire at the same second | 100% traffic falls onto DB | **TTL Jitter**: $\text{TTL}_{\text{base}} \pm \text{Random Uniform}$ |
| **Cache Penetration** | Attacker queries non-existent IDs | Every request bypasses cache to DB | **Bloom Filter** + **Null Object Caching** (60s) |
| **Dual-Write Race** | Updating cache directly on write | Stale cache permanently desynced | **Delete from Cache AFTER DB commit** |

---

## 3. Eviction Policies Matrix

```text
┌───────────┬──────────────────────────────────┬──────────────────────────────────────┐
│ Algorithm │ Data Structure                   │ Best Use Case                        │
├───────────┼──────────────────────────────────┼──────────────────────────────────────┤
│ LRU       │ Hash Map + Doubly Linked List    │ General recency-biased traffic       │
│ LFU       │ Hash Map + Frequency Lists       │ Skewed Zipfian traffic; resists scan │
│ FIFO      │ Queue / Circular Buffer          │ Simple buffers; poor under skew      │
│ TTL       │ Passive Check + Active Sampling  │ Session stores; time-bound tokens    │
└───────────┴──────────────────────────────────┴──────────────────────────────────────┘
```

---

## 4. Rapid-Fire Flashcards (10 Q&A)

1. **Why does dropping from 99% to 90% cache hit ratio cause a 10x database load surge?**  
   *Answer*: Miss traffic jumps from $1\%$ to $10\%$. The database must handle $\frac{10}{1} = 10\times$ more requests.

2. **What is a Cache Stampede (Thundering Herd)?**  
   *Answer*: When an ultra-hot key expires, thousands of concurrent requests miss simultaneously and crush the database.

3. **How does the XFetch algorithm prevent cache stampedes?**  
   *Answer*: It uses probability ($-\beta \Delta \ln(U) > \text{Expiry} - \text{Now}$) to trigger a background refresh *before* the key physically expires.

4. **What is a Cache Avalanche, and how do you prevent it?**  
   *Answer*: Mass simultaneous expiration of bulk-loaded keys; prevented by adding random uniform TTL jitter ($\pm 10-20\%$).

5. **What is Cache Penetration, and how do you defend against it?**  
   *Answer*: Repeated queries for non-existent keys bypassing cache; defended by Bloom filters and caching null tombstones (60s).

6. **Why should you "Delete from Cache" instead of "Update Cache" on writes?**  
   *Answer*: Concurrent updates can arrive out-of-order, leaving stale data in cache permanently. Deletion is safe and idempotent.

7. **What is Delayed Double Deletion?**  
   *Answer*: Deleting a cache key before a write and again $500\text{ms}$ later to purge stale entries repopulated by lagging read replicas.

8. **Why can standard Bloom filters not delete elements?**  
   *Answer*: Bits are shared by multiple keys due to hash collisions; clearing a bit causes false negatives. Counting Bloom filters solve this.

9. **What is Cache Pollution in pure LRU?**  
   *Answer*: A large sequential scan of cold keys flushes all frequently accessed hot keys out of cache memory.

10. **Why is Write-Behind (Write-Back) caching dangerous for financial transactions?**  
    *Answer*: It acknowledges writes from volatile RAM; a crash before flushing to disk permanently erases uncommitted financial state.
