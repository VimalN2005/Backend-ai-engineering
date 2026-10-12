# Day 17: Sliding Window Technique (Fixed & Dynamic Subarrays, Rate Limiting & Rolling Metrics)

In modern backend and AI systems engineering, high-throughput event processing, moving averages, streaming log analytics, and API rate limiters rely on a shared foundational algorithm: the **Sliding Window Pattern**.

The Sliding Window technique optimizes calculations over contiguous subarrays or subsegments of data. It eliminates repetitive recalculations by maintaining a running state, transforming naive $\mathcal{O}(N \times K)$ or $\mathcal{O}(N^2)$ brute-force iterations into optimal **$\mathcal{O}(N)$ linear-time sweeps**.

---

## 1. The Sliding Window Paradigm in Backend Engineering

Consider calculating the maximum sum of $K$ consecutive HTTP request latency measurements across $N$ stream events:

```
Naive Approach: Recompute sum from scratch at every step -> O(N * K)
[ 12,  45,  30,  80,  15,  60,  90 ]   (K = 3)
 [ 12 + 45 + 30 ]                  = 87     (3 operations)
      [ 45 + 30 + 80 ]             = 155    (3 operations)
           [ 30 + 80 + 15 ]        = 125    (3 operations)
Total operations for N elements: N * K

Sliding Window Approach: Add incoming, subtract outgoing -> O(N)
Window 0: [ 12,  45,  30 ]         = 87
Window 1:   87 - 12 + 80           = 155    (1 subtract, 1 add = O(1))
Window 2:  155 - 45 + 15           = 125    (1 subtract, 1 add = O(1))
Total operations for N elements: N * 1 = O(N)
```

At scale ($N = 1{,}000{,}000$, $K = 1{,}000$):
- Naive algorithm: $1{,}000{,}000 \times 1{,}000 = 1{,}000{,}000{,}000$ operations (~1–5 seconds).
- Sliding window: $1{,}000{,}000 \times 1 = 1{,}000{,}000$ operations (< 10 milliseconds).

---

## 2. The Mathematics of Incremental Computation

The core mathematical principle underlying sliding window is **incremental state transition**:
$$\text{State}_{t} = \text{State}_{t-1} + \text{Enter}(t) - \text{Exit}(t)$$

Where:
- $\text{State}_{t-1}$ is the accumulated state of the window at step $t-1$.
- $\text{Enter}(t)$ is the contribution of the newly admitted element at the right window boundary.
- $\text{Exit}(t)$ is the contribution of the evicted element leaving the left window boundary.

Because updating the state requires only constant $\mathcal{O}(1)$ arithmetic or hash map updates, the per-element processing cost remains constant regardless of window width $K$.

---

## 3. Pattern 1: Fixed-Size Sliding Window

Used when the window length $K$ is invariant and predetermined (e.g. 5-minute moving averages, 100-packet network buffers).

```
Index:    0    1    2    3    4    5    6
Array:  [ 4,   2,   1,   7,   8,   1,   2 ]   (K = 3)
        [───────────]                           Initial Window (sum = 7)
             [───────────]                      Slide 1: 7 - 4 + 7 = 10
                  [───────────]                 Slide 2: 10 - 2 + 8 = 16 (Max)
```

### Generic Fixed Window Template
```python
def fixed_sliding_window(arr: list[int], k: int) -> int:
    if len(arr) < k:
        return 0
    
    # 1. Initialize first window
    window_state = sum(arr[:k])
    max_state = window_state
    
    # 2. Slide window from index k to N - 1
    for i in range(k, len(arr)):
        window_state += arr[i] - arr[i - k]  # Add right, subtract left
        max_state = max(max_state, window_state)
        
    return max_state
```

---

## 4. Pattern 2: Dynamic (Variable-Size) Sliding Window

Used when looking for the longest, shortest, or optimal contiguous subarray satisfying an arbitrary constraint (e.g., maximum sum $\le \text{Threshold}$, minimum length subarray with sum $\ge S$).

```
            DYNAMIC WINDOW LIFECYCLE
               Right Pointer Expands
            ──────────────────────────►
   [ L .............................. R ]   (Window violates constraint!)
      ───►
      Left Pointer Shrinks to restore validity
```

### Invariant Mechanics
1. **Expansion Phase**: The `right` pointer advances, incorporating new elements into `window_state`.
2. **Contraction Phase**: While the window violates the target constraint (or while optimizing a minimum window), increment `left` and subtract `arr[left]` from `window_state`.
3. **Evaluation Phase**: Record valid window length: $\text{Length} = \text{right} - \text{left} + 1$.

Both `left` and `right` traverse the array at most once. Amortized time complexity is **$\mathcal{O}(N)$**.

---

## 5. Pattern 3: Sliding Window with Auxiliary Hash Maps

When tracking uniqueness, frequencies, or permutations (e.g. *Longest Substring Without Repeating Characters*), an auxiliary Hash Map or Frequency Array maps characters/keys to their most recent seen index:

```
String: "p w w k e w"
Idx:     0 1 2 3 4 5

At idx 2 ('w'): 'w' was seen at idx 1.
Instead of incrementing left by 1 (left = 0 -> 1 -> 2),
we jump 'left' directly to seen_map['w'] + 1 = 2!
```

### O(1) Left Pointer Leap Implementation
```python
def length_of_longest_substring(s: str) -> int:
    seen_map: dict[str, int] = {}
    left = 0
    max_len = 0
    
    for right, char in enumerate(s):
        if char in seen_map and seen_map[char] >= left:
            # Leap left pointer directly past the previous occurrence
            left = seen_map[char] + 1
            
        seen_map[char] = right
        max_len = max(max_len, right - left + 1)
        
    return max_len
```

---

## 6. Pattern 4: Exact Constraint Minimization (Minimum Window Substring)

The classic hard sliding window problem: Given string $S$ and target string $T$, find the shortest substring in $S$ containing all characters of $T$.

```
S = "A D O B E C O D E B A N C",  T = "A B C"
Expand until window contains 'A', 'B', 'C'.
Once valid: Greedily contract 'left' to eliminate non-essential prefixes.
```

### Key Optimization: `matched_count` Invariant
Instead of comparing full character frequency dictionaries ($\mathcal{O}(26)$ on every iteration), maintain a single scalar `matched_count`:
- When frequency of incoming char in window equals its required count in $T$, increment `matched_count += 1`.
- Window is valid when `matched_count == len(required_char_map)`.
- Shrink `left` until window is no longer valid; record the minimum length.

---

## 7. Subarray Sum Thresholds: Minimum Size Subarray Sum

Given an array of positive integers and a target $S$, find the minimal length of a contiguous subarray whose sum is $\ge S$:

```python
def min_subarray_len(target: int, nums: list[int]) -> int:
    left = 0
    current_sum = 0
    min_length = float("inf")
    
    for right in range(len(nums)):
        current_sum += nums[right]
        
        # Greedily shrink while sum constraint is satisfied
        while current_sum >= target:
            min_length = min(min_length, right - left + 1)
            current_sum -= nums[left]
            left += 1
            
    return 0 if min_length == float("inf") else int(min_length)
```

> [!WARNING]
> This dynamic two-pointer sliding window **only works if all elements are non-negative**. If negative numbers exist, increasing `right` does not monotonically increase the sum, breaking the invariant. For arrays with negative values, you must use **Prefix Sums + Hash Map** ($\mathcal{O}(N)$ space).

---

## 8. Monotonic Deque Optimized Sliding Window Maximum

Finding the maximum element in every sliding window of size $K$:
- **Naive approach**: Recalculate max at each step $\to \mathcal{O}(N \times K)$.
- **Max-Heap (`heapq`)**: $\mathcal{O}(N \log K)$ time (requires removing stale heap elements).
- **Monotonic Decreasing Deque (`collections.deque`)**: **Strictly $\mathcal{O}(N)$ time and $\mathcal{O}(K)$ space**.

```
Array: [ 1,  3, -1, -3,  5,  3,  6,  7 ],  K = 3

Window 0: [ 1,  3, -1 ] -> Deque stores indices: [1, 2] (vals: 3, -1). Max = 3
When 5 arrives: 5 > -1 and 5 > 3. All smaller elements in deque will NEVER
be the maximum again! Pop them from back.
Deque becomes: [4] (val: 5). Max = 5
```

Every element index is pushed to the deque once and popped at most once, yielding **amortized $\mathcal{O}(1)$ per element**.

---

## 9. Memory Footprint: Ring Buffers (Circular Arrays) vs Deques

In high-frequency low-latency microservices (e.g. 500,000 req/sec telemetry streams), allocating Python `deque` nodes generates heap fragmentation and garbage collection pauses.

### Ring Buffer (Circular Array)
A fixed-capacity array with modular indexing:
$$\text{Write Index} = (\text{head} + \text{count}) \pmod K$$
$$\text{Evict Index} = \text{head} \pmod K$$

- Pre-allocates a contiguous block of memory once.
- Zero heap re-allocation during sliding.
- L1 cache-friendly sequential access.

---

## 10. Backend Application 1: Sliding Window Log Rate Limiter

In production API gateways, preventing brute-force attacks and abuse requires rolling rate limiters.

```
Request Stream:
──[10:00:00.100]──[10:00:00.400]──[10:00:00.900]──► [Current: 10:00:01.050]
                     | <────────── 1-Second Rolling Window ──────────> |
```

- Maintain a double-ended queue (`deque`) of request timestamps per API key/IP.
- When a request arrives at timestamp $T$:
  1. Evict all timestamps older than $T - \text{WindowSize}$ from the front: `while q and q[0] <= T - window: q.popleft()`.
  2. If `len(q) < max_requests`: Allow request, append $T$ to queue.
  3. Else: Reject request with HTTP 429 (Too Many Requests).
- **Accuracy**: 100% precision with no boundary-edge burst loopholes.

---

## 11. Backend Application 2: Sliding Window Counter (Memory-Optimized)

The Sliding Window Log consumes memory proportional to request volume ($\mathcal{O}(\text{Requests})$). For high-scale services handling millions of requests per second, Cloudflare and Redis use the **Sliding Window Counter**:

```
Window = 1 Minute.
Previous Minute Bucket: [ 84 requests ]
Current Minute Bucket:  [ 36 requests ]
Current time is 18 seconds into current minute (30% through).

Estimated Rolling Requests = (Previous_Count * (1 - 0.30)) + Current_Count
                           = (84 * 0.70) + 36 = 58.8 + 36 = 94.8 requests
```

- Memory: Only **2 integers** per user/IP ($\mathcal{O}(1)$ space).
- Precision: Within 0.05% of exact sliding window log, but uses 99% less memory.

---

## 12. Backend Application 3: Rolling Latency Percentiles (p50, p95, p99)

APM systems (Prometheus, Datadog) compute rolling percentiles over sliding 60-second windows:
- As new request durations arrive, older durations fall out of the sliding window.
- The window continuously recomputes running stats (mean, standard deviation, p99) to trigger automated alerts on latency degradation.

---

## 13. Backend Application 4: Real-Time Financial Fraud Anomaly Detection

Credit card payment gateways analyze transaction velocity over rolling time windows:
- Rule: "Flag account if total transactions exceed \$5,000 within any rolling 10-minute window across more than 3 distinct merchant categories."
- A sliding window dynamically tracks running transaction sums and unique merchant category frequencies, emitting fraud alerts in under 2 milliseconds.

---

## 14. Complexity Comparison Matrix

| Problem / Pattern | Naive Approach | Sliding Window | Space Complexity | Constraint Prerequisite |
| :--- | :--- | :--- | :--- | :--- |
| **Fixed Window Max Sum** | $\mathcal{O}(N \times K)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Fixed window size $K$ |
| **Longest Substring No Repeats** | $\mathcal{O}(N^3)$ | $\mathcal{O}(N)$ | $\mathcal{O}(\min(N, \Sigma))$ | Dynamic hash map index leap |
| **Min Subarray Sum $\ge S$** | $\mathcal{O}(N^2)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Non-negative numbers only |
| **Sliding Window Maximum** | $\mathcal{O}(N \times K)$ | $\mathcal{O}(N)$ | $\mathcal{O}(K)$ | Monotonic Decreasing Deque |
| **Sliding Window Log Rate Limiter** | N/A | $\mathcal{O}(1)$ amortized | $\mathcal{O}(\text{Rate Limit})$ | FIFO timestamp deque |
| **Sliding Window Counter Limiter**| N/A | $\mathcal{O}(1)$ strict | $\mathcal{O}(1)$ | Dual-bucket linear interpolation |

---

## 15. The Golden Rules & Heuristics for Sliding Window
1. **Contiguity Requirement**: Sliding window applies strictly to **contiguous subarrays, substrings, or time streams**.
2. **Monotonicity**: Dynamic window expansion/contraction requires that expanding the window monotonically increases (or maintains) the state metric, and shrinking monotonically decreases it.
3. **Beware Negative Numbers**: For problems like "Subarray Sum Equals $K$" with negative integers, sliding window fails; use Prefix Sums with Hash Maps.
4. **Formula for Window Size**: Between 0-indexed bounds `left` and `right`, the window size is always:
   $$\text{Size} = \text{right} - \text{left} + 1$$
5. **Shrink with `while`, not `if`**: Dynamic window contraction often requires evicting multiple elements before the constraint is restored.

---

## Summary Checklist for Day 17
- [x] Master incremental computation ($\text{State} = \text{State} + \text{In} - \text{Out}$).
- [x] Implement Fixed-Size Sliding Window without re-summing slices.
- [x] Implement Dynamic-Size Sliding Window with left/right two-pointer expansion and contraction.
- [x] Use hash map index jumping for $\mathcal{O}(1)$ leaps in substring problems.
- [x] Master the Monotonic Deque for Sliding Window Maximum in $\mathcal{O}(N)$ time.
- [x] Design a production-grade Sliding Window Log Rate Limiter with sub-millisecond precision.
- [x] Implement memory-optimized Sliding Window Counter approximation for high-scale API gateways.
