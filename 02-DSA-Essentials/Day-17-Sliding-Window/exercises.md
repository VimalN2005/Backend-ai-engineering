# Day 17: Practical Hands-On DSA Challenges

Master the Sliding Window technique, Monotonic Deques, and Rate Limiting architectures with these 3 production-level challenges.

---

## Challenge 1: Minimum Window Substring ($\mathcal{O}(|S| + |T|)$ Time)

### Objective
In search indexing and NLP token parsing, finding the smallest contiguous context window that contains a complete set of required search query terms is a fundamental operation.

Implement `min_window_substring(s: str, t: str) -> str` that finds the shortest substring of `s` containing all characters of `t` (including duplicate frequencies).

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(|s| + |t|)$.
2. **Space Complexity**: $\mathcal{O}(|\Sigma|)$ where $\Sigma$ is the alphabet size of character frequencies.
3. **Matched Count Optimization**:
   - Do NOT compare full character frequency dictionaries on each iteration (which costs $\mathcal{O}(26)$ or $\mathcal{O}(128)$ per step).
   - Maintain a single integer scalar `matched_unique_chars`:
     - When `window_counts[char] == target_counts[char]`, increment `matched_unique_chars += 1`.
     - Window is valid when `matched_unique_chars == len(target_counts)`.
4. **Greedy Contraction**:
   - While the window is valid, contract the `left` pointer to find the minimal span, updating the best result coordinates `(best_left, best_right)`.
5. **Edge Cases**:
   - `len(s) < len(t)` $\to$ return `""`.
   - `t` contains duplicates (e.g. `t = "AABC"` requires two `'A'`s).
   - Target string matches entire `s`.

---

## Challenge 2: Streaming Telemetry Window Maximum with Monotonic Deque

### Objective
In real-time infrastructure monitoring (e.g. tracking peak CPU utilization or max request duration over the last 60 seconds), telemetry samples arrive one at a time via a streaming socket.

Build a class `StreamingWindowMaxTracker`:

```python
class StreamingWindowMaxTracker:
    def __init__(self, k: int) -> None:
        """Initializes tracker with fixed window size k."""
        ...
        
    def push(self, sample: float) -> Optional[float]:
        """
        Ingests a new metric sample.
        Returns the maximum value in the current window of size k once k samples
        have been received, or None if fewer than k samples have arrived.
        """
        ...
```

### Requirements
1. **Amortized Time Complexity**: Strictly $\mathcal{O}(1)$ per `.push()` invocation.
2. **Space Complexity**: Strictly bounded by $\mathcal{O}(k)$ (the deque must never grow beyond $k$ elements).
3. **Monotonic Invariant**:
   - Store tuples `(sample_index, sample_value)` in a double-ended queue.
   - Maintain strictly decreasing values from front to back of the deque.
   - Evict stale indices that are older than `current_index - k`.
4. **Validation**:
   - Push 100,000 streaming floating-point values and verify 100% agreement with `max()` on full sliding window slices while achieving >500,000 ops/sec.

---

## Challenge 3: Distributed Sliding Window Counter Rate Limiter (Cloudflare Algorithm)

### Objective
The Sliding Window Log (from `example_02.py`) stores individual timestamps in memory, consuming $\mathcal{O}(\text{Requests})$ RAM. In distributed edge environments (Cloudflare, AWS CloudFront), storing millions of timestamps per client IP is unsustainable.

Implement the **Sliding Window Counter** rate limiter, which approximates a rolling window in **$\mathcal{O}(1)$ memory** using dual-bucket linear interpolation.

### Mathematical Model
For a window of duration $W$ seconds (e.g. 60 seconds):
1. Maintain two integer counters per client:
   - `prev_bucket_count`: Request count in the previous time block $[T - W, T)$.
   - `curr_bucket_count`: Request count in the current time block $[T, T + W)$.
2. When a request arrives at offset $t$ seconds into the current bucket:
   $$\text{Weight}_{\text{prev}} = \frac{W - t}{W}$$
   $$\text{Estimated Requests} = (\text{prev\_bucket\_count} \times \text{Weight}_{\text{prev}}) + \text{curr\_bucket\_count}$$
3. If $\text{Estimated Requests} < \text{max\_requests}$, allow the request and increment `curr_bucket_count += 1`.
4. If the clock rolls past $T + W$, advance the window: `prev_bucket_count = curr_bucket_count`, `curr_bucket_count = 0`.

### Requirements
1. Class `SlidingWindowCounterRateLimiter(max_requests: int, window_seconds: float = 60.0)`.
2. Method `acquire(client_id: str, current_time: Optional[float] = None) -> bool`.
3. Memory footprint: Exactly 2 integers and 1 timestamp per client ($\mathcal{O}(1)$ space).
4. Automated verification:
   - Demonstrate rejection of burst traffic exceeding `max_requests`.
   - Demonstrate bucket rollover across time boundaries.
   - Prove memory consumption is strictly constant regardless of request volume.

---

## Solution Guidelines & Architecture Hints
- For Challenge 1, remember to only update `best_len` when all target frequencies are satisfied.
- For Challenge 2, test with both monotonically increasing streams (where the deque only holds 1 element) and monotonically decreasing streams (where the deque holds $k$ elements).
- For Challenge 3, test edge conditions at $t = 0.0$ (start of bucket) and $t = 0.99 \times W$ (end of bucket).
