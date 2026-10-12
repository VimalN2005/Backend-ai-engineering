# Day 17: Senior Backend Interview Mastery - Sliding Window & Rate Limiting

Master these 5 senior-level technical interview questions covering algorithmic time complexity proofs, distributed rate limiting architectures, and stream processing memory models.

---

### Q1: How does the Sliding Window technique reduce time complexity from $\mathcal{O}(N \times K)$ to $\mathcal{O}(N)$? How do you prove the amortized time complexity of dynamic windows?

#### Answer:
The performance difference between brute-force subarray evaluation and sliding window stems from **overlapping subproblem elimination**:

```
Brute Force: Evaluates (N - K + 1) windows of size K from scratch -> O(N * K)
Window 1: [ a0, a1, ..., aK-1 ] -> Sums K elements
Window 2: [ a1, a2, ..., aK   ] -> Re-sums K - 1 identical elements!
```

By recognizing that Window 2 shares $K - 1$ elements with Window 1, we replace full summation with an **incremental state transition**:
$$\text{Sum}_{\text{new}} = \text{Sum}_{\text{old}} + a_{K} - a_{0}$$
This requires exactly 1 addition and 1 subtraction ($\mathcal{O}(1)$), reducing total operations across $N$ elements to $N \times \mathcal{O}(1) = \mathcal{O}(N)$.

#### Amortized Analysis of Dynamic (Variable-Size) Sliding Windows:
Junior developers often assume that a nested `while` loop inside a `for` loop implies $\mathcal{O}(N^2)$ time:
```python
for right in range(N):        # Outer loop
    while constraint_broken: # Inner loop
        left += 1
```

**The Aggregate Method Proof**:
1. The `right` pointer starts at index 0 and advances to index $N - 1$. It increments exactly $N$ times.
2. The `left` pointer starts at index 0 and can only move forward (`left += 1`). It can never move backward.
3. Therefore, across the *entire lifetime* of the algorithm, the `left` pointer increments **at most $N$ times**.
4. The total number of inner loop executions over all outer loop iterations combined is bounded by $N$:
   $$\text{Total Operations} = N_{\text{right moves}} + N_{\text{left moves}} \le 2N = \mathcal{O}(N)$$
5. The amortized cost per outer loop iteration is $\frac{2N}{N} = \mathcal{O}(1)$.

---

### Q2: Compare the four major API rate limiting algorithms in production backends: Fixed Window Counter, Sliding Window Log, Token Bucket, and Leaky Bucket. What are the trade-offs?

#### Answer:

| Rate Limiter | Mechanism | Memory Complexity | Burst Tolerance | Major Flaw / Advantage |
| :--- | :--- | :--- | :--- | :--- |
| **Fixed Window Counter** | Increments a counter keyed by timestamp bucket (e.g. `user:10:00`). | $\mathcal{O}(1)$ (1 integer per user) | Poor | **Boundary Burst Flaw**: A client can send $2\times$ limit across bucket boundary (e.g. 100 at 10:00:59, 100 at 10:01:00). |
| **Sliding Window Log** | Stores exact timestamp of every request in a sorted set / deque. | $\mathcal{O}(\text{Limit})$ (Stores timestamps) | Zero burst loophole | **100% Precision**, but high memory usage under heavy traffic. Vulnerable to memory exhaustion. |
| **Token Bucket** | Tokens refill at steady rate $R$; requests consume tokens; capacity $B$. | $\mathcal{O}(1)$ (2 floats: tokens & last_refill) | **High** (Allows bursts up to capacity $B$) | **Industry Standard (AWS, Stripe)**: Smooth rate with controlled burst capacity. Low memory. |
| **Leaky Bucket** | Requests enter a FIFO buffer and leak out at a constant outbound rate. | $\mathcal{O}(\text{Queue Size})$ | None (Smooths bursts into constant flow) | Ideal for protecting sensitive downstream services from any sudden traffic spikes. |

#### Architectural Recommendation:
- For **API Gateways with burst tolerance** (e.g. public developer APIs): **Token Bucket** (Redis with Lua script).
- For **Strict Security & Anti-Fraud** (e.g. login brute-force, SMS OTP limits): **Sliding Window Log** or **Sliding Window Counter** (guarantees zero boundary-burst bypass).

---

### Q3: Explain the Monotonic Deque algorithm for Sliding Window Maximum. Why is it strictly $\mathcal{O}(N)$, whereas a Max-Heap is $\mathcal{O}(N \log K)$?

#### Answer:
In a sliding window of size $K$, we want the maximum element at every step:
- **Naive Search**: Scans the window of size $K$ on every slide $\to \mathcal{O}(N \times K)$.
- **Max-Heap (`heapq`)**: Stores pairs `(-value, index)`. When sliding, pushes new elements in $\mathcal{O}(\log K)$. However, Python's heap cannot remove leaving elements in $\mathcal{O}(1)$; it must lazily pop elements from the top if `index <= i - K`. In the worst case, the heap grows to size $N$, yielding **$\mathcal{O}(N \log K)$ or $\mathcal{O}(N \log N)$** time.

#### The Monotonic Decreasing Deque Invariant:
A double-ended queue (`collections.deque`) stores indices of candidate maximums in strictly non-increasing value order:
1. **Eviction from Front**: If the index at the front of the deque has slid out of the window (`deque[0] <= i - K`), pop it from the left.
2. **Pruning from Back**: Before inserting incoming index `i` with value $V$, remove all indices from the back of the deque whose values $\le V$:
   ```python
   while deque and nums[deque[-1]] <= V:
       deque.pop()
   ```
   **The Elimination Invariant**: Any element smaller than $V$ that appears to the left of $V$ will *never* be the maximum of the current or any future window, because $V$ is both larger and will remain in the window longer!
3. **Append New Element**: Append `i` to the back.
4. **Current Maximum**: The front of the deque (`deque[0]`) is mathematically guaranteed to be the index of the maximum element for the current window!

#### Time Complexity Proof:
Every array index is added to the deque **exactly once** and removed **at most once**. Total deque operations across all $N$ elements are at most $2N$. Therefore, the time complexity is **strictly $\mathcal{O}(N)$** with bounded $\mathcal{O}(K)$ space.

---

### Q4: How do high-throughput stream processing engines (e.g. Prometheus, Flink) maintain sliding window aggregations over millions of events without memory exhaustion or GC stalls?

#### Answer:
At scale (millions of metrics/sec), naive data structures cause garbage collection thrashing and memory bloat. Production engines employ two core architectural patterns:

#### 1. Ring Buffers (Circular Fixed Arrays)
Instead of allocating linked-list nodes or dynamic deques that trigger Python/JVM memory allocation and garbage collection:
- Pre-allocate a contiguous memory buffer of size $K$ (e.g. in C or NumPy).
- Use modular indexing: `write_idx = (head + count) % K`.
- Evictions overwrite the oldest slot directly in contiguous memory.
- Hardware CPU prefetchers read sequential cache lines with near-zero cache misses.

#### 2. Incremental Statistical State (Welford's Algorithm)
Instead of storing raw samples to recompute rolling standard deviations:
- Engines maintain running scalar accumulators:
  $$\text{Count } n, \quad \text{Sum } S = \sum x_i, \quad \text{Sum of Squares } SS = \sum x_i^2$$
- When sliding out sample $x_{\text{out}}$ and sliding in $x_{\text{in}}$:
  $$S_{\text{new}} = S_{\text{old}} + x_{\text{in}} - x_{\text{out}}$$
  $$SS_{\text{new}} = SS_{\text{old}} + x_{\text{in}}^2 - x_{\text{out}}^2$$
- Running variance is computed in $\mathcal{O}(1)$ time:
  $$\text{Variance} = \frac{SS}{n} - \left(\frac{S}{n}\right)^2$$
- Memory footprint is **$\mathcal{O}(1)$** regardless of whether the stream processes 100 or 100,000,000 events.

---

### Q5: Why does the dynamic sliding window pattern fail on arrays containing negative numbers? How does Prefix Sums + Hash Map resolve this?

#### Answer:

#### The Failure of Sliding Window with Negatives:
The dynamic sliding window algorithm relies on **monotonicity**:
- Expanding `right` ($+ \text{nums}[\text{right}]$) must monotonically increase (or maintain) the accumulated sum.
- Contracting `left` ($- \text{nums}[\text{left}]$) must monotonically decrease the accumulated sum.

When negative numbers exist (e.g. `nums = [1, -1, 5, -2, 3]`, target = 3):
- Adding a negative number *decreases* the sum.
- Subtracting a negative number *increases* the sum.
- If current sum is 5 (> 3), contracting `left` might subtract $-1$, *increasing* the sum to 6!
- The algorithm cannot decide whether to advance `left` or `right` greedily, breaking the invariant and missing valid subarrays.

#### The Prefix Sum + Hash Map Solution ($\mathcal{O}(N)$ Time, $\mathcal{O}(N)$ Space):
Define prefix sum $P[i] = \sum_{j=0}^{i} \text{nums}[j]$.
The sum of any contiguous subarray from index $j$ to $i$ is:
$$\text{Sum}(j, i) = P[i] - P[j - 1]$$
To find if any subarray equals target $K$:
$$P[i] - P[j - 1] = K \iff P[j - 1] = P[i] - K$$

By storing historical prefix sums in a Hash Map:
1. As we iterate through index $i$, calculate running prefix sum $P[i]$.
2. Check if $P[i] - K$ exists in the hash map.
3. If it exists, a subarray summing to $K$ has been found in $\mathcal{O}(1)$ lookup time!
4. Works with 100% accuracy across positive, negative, and zero values.
