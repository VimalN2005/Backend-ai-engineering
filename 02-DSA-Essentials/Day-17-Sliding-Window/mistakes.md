# Day 17: 7 Critical Antipatterns in Sliding Window Algorithms & Rate Limiters

Even experienced engineers introduce subtle complexity regressions, off-by-one errors, and memory leaks when implementing Sliding Window patterns. Below are 7 common antipatterns, their consequences in production backends, and enterprise-grade remediations.

---

## 1. Re-Slicing or Re-Summing the Window on Every Step

### The Mistake
Using Python's slice syntax and `sum()` inside the sliding loop:
```python
# WRONG: Slicing allocates a new list every step; sum() re-iterates K elements!
def max_sum_subarray_wrong(nums: list[int], k: int) -> int:
    max_sum = 0
    for i in range(len(nums) - k + 1):
        window_sum = sum(nums[i:i + k])  # O(K) allocation and summation every time!
        max_sum = max(max_sum, window_sum)
    return max_sum
```

### The Consequence
For $N = 100{,}000$ and $K = 5{,}000$, this algorithm creates $95{,}000$ temporary Python list objects and performs $475{,}000{,}000$ operations ($\mathcal{O}(N \times K)$). It triggers severe garbage collection pauses and degrades response times from milliseconds to seconds.

### The Correct Approach
Use **incremental state transitions** ($\text{State} = \text{State} + \text{In} - \text{Out}$):
```python
# CORRECT: O(1) arithmetic updates per slide; O(N) overall time, zero memory allocation
def max_sum_subarray_correct(nums: list[int], k: int) -> int:
    window_sum = sum(nums[:k])
    max_sum = window_sum
    for i in range(k, len(nums)):
        window_sum += nums[i] - nums[i - k]
        if window_sum > max_sum:
            max_sum = window_sum
    return max_sum
```

---

## 2. Incrementing `left` by 1 Instead of Leaping in Substring Problems

### The Mistake
In "Longest Substring Without Repeating Characters", contracting the window one element at a time with a set:
```python
# SUBOPTIMAL: Incurs redundant O(2N) pointer iterations
def length_of_longest_substring_slow(s: str) -> int:
    seen = set()
    left = 0
    max_len = 0
    for right in range(len(s)):
        while s[right] in seen:
            seen.remove(s[left])
            left += 1  # Crawling one step at a time!
        seen.add(s[right])
        max_len = max(max_len, right - left + 1)
    return max_len
```

### The Consequence
If the string is `"abcdefghza"`, when the duplicate `'a'` is encountered, the left pointer crawls through 9 redundant steps (`b`, `c`, `d`...) before reaching the position past `'a'`.

### The Correct Approach
Store character indices in a Hash Map and **jump the left pointer directly**:
```python
# OPTIMAL: O(N) single-pass with O(1) index leap
def length_of_longest_substring_optimal(s: str) -> int:
    last_seen = {}
    left = 0
    max_len = 0
    for right, char in enumerate(s):
        if char in last_seen and last_seen[char] >= left:
            left = last_seen[char] + 1  # Leap directly past duplicate!
        last_seen[char] = right
        max_len = max(max_len, right - left + 1)
    return max_len
```

---

## 3. Off-by-One Calculation: `right - left` vs `right - left + 1`

### The Mistake
Computing subarray or window length as `right - left`:
```python
# WRONG: Drops an element due to 0-based indexing
window_len = right - left
```

### The Consequence
For indices `left = 2` and `right = 5`, the window contains indices `[2, 3, 4, 5]`, which is **4 elements**. Computing `5 - 2 = 3` produces an off-by-one error, leading to truncated rate limits, incorrect buffer sizes, and flawed test assertions.

### The Correct Approach
Between two inclusive indices in 0-indexed arrays, always add 1:
```python
# CORRECT: Accurate window length
window_len = right - left + 1
```

---

## 4. Applying Dynamic Sliding Window to Arrays with Negative Numbers

### The Mistake
Attempting to solve "Subarray Sum Equals K" or "Shortest Subarray with Sum $\ge K$" using a two-pointer sliding window on an array with negative integers:
```python
# WRONG: Monotonicity assumption is broken by negative values!
def subarray_sum_broken(nums: list[int], k: int) -> int:
    # Fails completely on nums = [1, -1, 5, -2, 3], k = 3
    left = 0
    curr_sum = 0
    for right in range(len(nums)):
        curr_sum += nums[right]
        while curr_sum > k:  # INVALID: Adding future negative numbers might decrease curr_sum!
            curr_sum -= nums[left]
            left += 1
```

### The Consequence
When numbers can be negative, expanding `right` does not monotonically increase the sum, and shrinking `left` does not monotonically decrease it. The sliding window produces false negatives and misses optimal subarrays.

### The Correct Approach
When negative numbers exist, you must use **Prefix Sums + Hash Map** ($\mathcal{O}(N)$ time and $\mathcal{O}(N)$ space):
```python
# CORRECT: Handles positive, negative, and zero values reliably
def subarray_sum_equals_k(nums: list[int], k: int) -> int:
    prefix_counts = {0: 1}
    current_sum = 0
    match_count = 0
    for x in nums:
        current_sum += x
        match_count += prefix_counts.get(current_sum - k, 0)
        prefix_counts[current_sum] = prefix_counts.get(current_sum, 0) + 1
    return match_count
```

---

## 5. Memory Leaks in Sliding Window Log Rate Limiters

### The Mistake
Building a sliding window rate limiter that records client request timestamps in an in-memory dictionary without pruning inactive client keys:
```python
# WRONG: Memory leak in production API Gateway!
class LeakyRateLimiter:
    def __init__(self):
        self.logs = {}  # Keys grow indefinitely!
        
    def allow(self, ip: str, now: float) -> bool:
        q = self.logs.setdefault(ip, deque())
        while q and q[0] <= now - 60:
            q.popleft()
        # Even if q becomes empty, the 'ip' key remains permanently in self.logs!
```

### The Consequence
If millions of unique IP addresses send one request each day (e.g. web crawlers, mobile devices), `self.logs` retains millions of empty `deque` objects. Over days of operation, the API Gateway worker consumes gigabytes of heap memory and eventually crashes due to Out-Of-Memory (OOM).

### The Correct Approach
Periodically prune empty client records or use a Time-To-Live (TTL) cache (e.g. Redis `ZREMRANGEBYSCORE` with key expiration):
```python
# CORRECT: Memory-safe with periodic idle key pruning
def prune_idle_clients(self, now: float, window_seconds: float) -> None:
    idle_keys = [
        ip for ip, q in self.logs.items()
        if not q or q[-1] <= now - window_seconds
    ]
    for ip in idle_keys:
        del self.logs[ip]
```

---

## 6. Using a Max-Heap for Sliding Window Maximum Instead of Monotonic Deque

### The Mistake
Using Python's standard `heapq` module to compute maximums in a sliding window of size $K$:
```python
# SUBOPTIMAL: O(N log K) time with stale heap pollution
import heapq
def max_sliding_window_heap(nums: list[int], k: int) -> list[int]:
    heap = [(-nums[i], i) for i in range(k)]
    heapq.heapify(heap)
    res = [-heap[0][0]]
    for i in range(k, len(nums)):
        heapq.heappush(heap, (-nums[i], i))
        while heap[0][1] <= i - k:  # Lazy deletion of expired elements
            heapq.heappop(heap)
        res.append(-heap[0][0])
    return res
```

### The Consequence
1. In the worst case, the heap size can grow to $\mathcal{O}(N)$, consuming excess memory.
2. Every insertion and deletion takes $\mathcal{O}(\log K)$, resulting in $\mathcal{O}(N \log K)$ overall time.

### The Correct Approach
Use a **Monotonic Decreasing Deque** for strictly **$\mathcal{O}(N)$ time and $\mathcal{O}(K)$ space**:
```python
# OPTIMAL: O(N) time with O(K) bounded space
from collections import deque
def max_sliding_window_deque(nums: list[int], k: int) -> list[int]:
    d = deque()
    res = []
    for i, n in enumerate(nums):
        while d and d[0] <= i - k:
            d.popleft()
        while d and nums[d[-1]] <= n:
            d.pop()
        d.append(i)
        if i >= k - 1:
            res.append(nums[d[0]])
    return res
```

---

## 7. Shrinking Dynamic Windows with `if` Instead of `while`

### The Mistake
Using an `if` statement to shrink the window when an invariant is violated:
```python
# WRONG: Only shrinks window by 1 element even if multiple elements must be evicted!
def min_subarray_len_flawed(target: int, nums: list[int]) -> int:
    left = 0
    curr_sum = 0
    min_len = float("inf")
    for right in range(len(nums)):
        curr_sum += nums[right]
        if curr_sum >= target:  # BUG: Must be 'while'!
            min_len = min(min_len, right - left + 1)
            curr_sum -= nums[left]
            left += 1
```

### The Consequence
If `nums[right]` is a large integer, the window sum may remain $\ge \text{target}$ even after evicting `nums[left]`. Because `if` only executes once, the window fails to find smaller valid sub-windows, returning an incorrect larger length.

### The Correct Approach
Always use a `while` loop to greedily contract the window until the invariant condition no longer holds:
```python
# CORRECT: Greedily shrinks until constraint is broken
while curr_sum >= target:
    min_len = min(min_len, right - left + 1)
    curr_sum -= nums[left]
    left += 1
```

---

## Summary Checklist
- [x] Use incremental updates ($\text{Sum} = \text{Sum} + \text{In} - \text{Out}$); never re-slice arrays.
- [x] Leap the left pointer past duplicate indices using Hash Map lookups.
- [x] Remember $\text{Length} = \text{right} - \text{left} + 1$.
- [x] Switch to Prefix Sums + Hash Map when arrays contain negative numbers.
- [x] Prune idle client keys in sliding window rate limiters to prevent memory exhaustion.
- [x] Use a Monotonic Deque for $\mathcal{O}(N)$ sliding window maximums, avoiding heap overhead.
- [x] Use `while` loops for dynamic window contraction, never a single `if`.
