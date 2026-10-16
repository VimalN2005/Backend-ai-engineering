# Day 20: 7 Critical Antipatterns in Monotonic Stacks

The Monotonic Stack pattern is one of the most powerful algorithms for turning $\mathcal{O}(N^2)$ brute-force boundary scans into linear $\mathcal{O}(N)$ passes. However, subtle bugs in stack invariants, index handling, and circular indexing frequently lead to silent data corruption, index out-of-bounds crashes, and memory leaks.

Below are 7 critical antipatterns, their architectural consequences, and enterprise remediations.

---

## 1. Storing Raw Values Instead of Array Indices on the Stack

### The Mistake
Storing the element values directly on the stack when solving distance or span problems:

```python
# WRONG: Cannot calculate distance; crashes or fails on duplicate values!
def daily_temperatures_flawed(temperatures: list[int]) -> list[int]:
    stack = []  # Stores temperature values: e.g. [73, 74, ...]
    result = [0] * len(temperatures)
    for t in temperatures:
        while stack and stack[-1] < t:
            prev_t = stack.pop()
            # How many days passed between prev_t and t? IMPOSSIBLE to know!
            # And if there are duplicate values (e.g. 71 occurs twice),
            # value-to-index reverse hash lookups become ambiguous and corrupted.
        stack.append(t)
    return result
```

### The Consequence
1. Distance calculation is completely lost because values carry no positional context.
2. Attempting to compensate by storing a hash map `{value: index}` fails catastrophically whenever duplicate values appear in the telemetry stream.

### The Correct Approach
**Always store array indices on the stack**. Indices give you constant-time access to both:
- The value: `temperatures[stack[-1]]`
- The relative distance / span: `current_idx - prev_idx`

```python
# CORRECT: Indices provide both value and constant-time span resolution
def daily_temperatures_correct(temperatures: list[int]) -> list[int]:
    n = len(temperatures)
    result = [0] * n
    stack = []  # Stores INDICES

    for curr_idx, curr_temp in enumerate(temperatures):
        while stack and temperatures[stack[-1]] < curr_temp:
            prev_idx = stack.pop()
            result[prev_idx] = curr_idx - prev_idx  # Exact span in O(1)
        stack.append(curr_idx)

    return result
```

---

## 2. Strictly Monotonic vs. Non-Strictly Monotonic Confusion (`<` vs. `<=`)

### The Mistake
Mixing up strict inequalities (`<` / `>`) and non-strict inequalities (`<=` / `>=`) in the stack eviction condition:

```python
# WRONG: Using '<=' for Next Greater Element
def next_greater_element_broken(nums: list[int]) -> list[int]:
    n = len(nums)
    res = [-1] * n
    stack = []
    for i in range(n):
        # BUG: '<=' pops equal elements! Equal is NOT strictly greater!
        while stack and nums[stack[-1]] <= nums[i]:
            prev_idx = stack.pop()
            res[prev_idx] = nums[i]  # If nums[prev_idx] == nums[i], this is false!
        stack.append(i)
    return res
```

### The Consequence
If `nums = [2, 2, 2]`, the correct Next Greater Element for all entries is `[-1, -1, -1]` (since no element is strictly greater than 2). Using `<=` incorrectly resolves the first `2` with the second `2`, yielding `[2, 2, -1]`.

### The Correct Approach
Carefully distinguish the problem specification:
- **Next Greater Element**: Must be *strictly greater*. Use `nums[stack[-1]] < current_val`.
- **Next Greater or Equal Element**: Non-strict. Use `nums[stack[-1]] <= current_val`.
- **Next Smaller Element**: Must be *strictly smaller*. Use `nums[stack[-1]] > current_val`.

```python
# CORRECT: Strict comparison for Next Greater Element
while stack and nums[stack[-1]] < nums[i]:
    prev_idx = stack.pop()
    res[prev_idx] = nums[i]
```

---

## 3. Failing to Flush Histogram Stack with a Sentinel Value (`0`)

### The Mistake
Omitting stack flush logic or sentinel values in `largest_rectangle_in_histogram`:

```python
# WRONG: Forgets to flush remaining bars after the loop terminates!
def largest_rectangle_buggy(heights: list[int]) -> int:
    stack = []
    max_area = 0
    for i, h in enumerate(heights):
        while stack and heights[stack[-1]] > h:
            idx = stack.pop()
            w = i if not stack else i - stack[-1] - 1
            max_area = max(max_area, heights[idx] * w)
        stack.append(i)
    
    # If heights is strictly increasing, e.g. [1, 2, 3, 4, 5],
    # NO element was ever popped! stack still contains [0, 1, 2, 3, 4]!
    return max_area  # RETURNS 0! Entire calculation lost!
```

### The Consequence
For monotonic increasing inputs like `[1, 2, 3, 4, 5]`, every bar is taller than the previous bar. No bar is ever popped inside the loop. The function returns `0` instead of the correct answer `9` (width 3 $\times$ height 3).

### The Correct Approach
Either append a `0` sentinel bar (`heights + [0]`) which is guaranteed to be $\le$ every non-negative height, forcing every pending bar out of the stack, or write a secondary post-loop flush:

```python
# CORRECT: Append 0 sentinel to guarantee clean flush without duplicate code
def largest_rectangle_correct(heights: list[int]) -> int:
    extended = heights + [0]  # Sentinel 0 flushes all remaining bars
    stack = []
    max_area = 0
    for i, h in enumerate(extended):
        while stack and extended[stack[-1]] > h:
            idx = stack.pop()
            w = i if not stack else i - stack[-1] - 1
            max_area = max(max_area, extended[idx] * w)
        stack.append(i)
    return max_area
```

---

## 4. Physical Array Duplication in Circular Arrays (`nums + nums`)

### The Mistake
Allocating a full duplicate array to simulate circular traversal:

```python
# WRONG: Redundant O(N) heap allocation doubles memory footprint
def next_greater_circular_inefficient(nums: list[int]) -> list[int]:
    extended = nums + nums  # Allocates an extra 2N list in memory!
    n = len(nums)
    stack = []
    res = [-1] * (2 * n)
    for i in range(2 * n):
        while stack and extended[stack[-1]] < extended[i]:
            res[stack.pop()] = extended[i]
        stack.append(i)
    return res[:n]  # Slices another O(N) array!
```

### The Consequence
For high-volume telemetry buffers with $N = 1{,}000{,}000$ integers, `nums + nums` allocates an extra $2{,}000{,}000$ pointers (~16 MB heap) plus temporary slice allocations, triggering unnecessary Python Garbage Collection pauses.

### The Correct Approach
Use **Virtual $2N$ Modulo Unrolling**. Loop $2N$ times and access elements using `nums[i % n]`. Only push indices during the first pass (`i < n`):

```python
# CORRECT: Virtual 2N unrolling with zero auxiliary array allocations
def next_greater_circular_optimal(nums: list[int]) -> list[int]:
    n = len(nums)
    res = [-1] * n
    stack = []  # Stores original indices 0 .. n-1
    for i in range(2 * n):
        val = nums[i % n]
        while stack and nums[stack[-1]] < val:
            res[stack.pop()] = val
        if i < n:
            stack.append(i)
    return res
```

---

## 5. Off-by-One Errors in Histogram Width Calculation

### The Mistake
Calculating histogram width as `i - stack[-1]` instead of `i - stack[-1] - 1`:

```python
# WRONG: Off-by-one width formula inflates rectangle dimensions
while stack and heights[stack[-1]] > current_h:
    popped_idx = stack.pop()
    h = heights[popped_idx]
    # BUG: stack[-1] is the left boundary EXCLUSIVE!
    # Using 'i - stack[-1]' overcounts the width by 1!
    w = i if not stack else (i - stack[-1])
    max_area = max(max_area, h * w)
```

### The Consequence
Consider `heights = [2, 1, 2]`:
- When index 0 (`height 2`) is popped at index 1 (`height 1`):
  - Stack is now empty. Width is correctly `1`. Area = $2 \times 1 = 2$.
- At the end (index 3 with sentinel 0), index 2 (`height 2`) is popped:
  - Stack top is index 1 (`height 1`).
  - Incorrect formula: `w = 3 - 1 = 2`. Area = $2 \times 2 = 4$ (impossible, bar of height 2 only has width 1!).
  - Correct formula: `w = 3 - 1 - 1 = 1`. Area = $2 \times 1 = 2$.

### The Correct Approach
Remember the boundary theorem:
- **Right boundary**: `current_idx` (exclusive).
- **Left boundary**: `stack[-1]` (exclusive) if stack non-empty, or `-1` (exclusive) if empty.
- **Width**: $\text{Right} - \text{Left} - 1 = i - \text{stack}[-1] - 1$ (or $i - (-1) - 1 = i$).

```python
# CORRECT: Exact exclusive width derivation
w = i if not stack else i - stack[-1] - 1
```

---

## 6. Blindly Popping From Stack Without Empty Stack Guard

### The Mistake
Attempting to inspect `stack[-1]` without checking if the stack contains elements:

```python
# WRONG: Raises IndexError: list index out of range on empty stack
while heights[stack[-1]] > current_h:  # Crashes if stack is empty!
    popped_idx = stack.pop()
```

### The Consequence
In Python, evaluating `stack[-1]` on an empty list immediately raises `IndexError: list index out of range`, abruptly crashing the microservice request handler.

### The Correct Approach
Leverage Python's short-circuit `and` evaluation: put the truthiness check `stack` *first*:

```python
# CORRECT: Short-circuit evaluation guards against IndexError
while stack and heights[stack[-1]] > current_h:
    popped_idx = stack.pop()
```

---

## 7. Unbounded Memory Growth in Long-Running Streaming Watchdogs

### The Mistake
Using an unbounded monotonic stack in a 24/7 streaming service without time-based eviction:

```python
# WRONG: Memory leak on monotonically decreasing streams!
class FlawedStreamingWatchdog:
    def __init__(self):
        self.stack = []  # Monotonic decreasing stack

    def ingest(self, metric: float):
        while self.stack and self.stack[-1] < metric:
            self.stack.pop()
        self.stack.append(metric)
        # If metrics continuously decrease (e.g. steady cooling or degrading load),
        # NO items are ever popped! Stack grows by millions of items until OOM kill!
```

### The Consequence
In production AI inference servers or API gateways, if latencies remain steady or decline during off-peak night hours, incoming requests are strictly smaller than previous requests. Nothing is popped. The stack retains millions of objects over days, eventually causing an Out-Of-Memory (OOM) Kubernetes pod restart.

### The Correct Approach
Enforce a **Sliding Retention Horizon** or max-depth bound:
- Periodically prune items whose timestamps exceed the SLA retention window.
- Alternatively, use a bounded deque or drop items older than TTL.

```python
# CORRECT: Sliding retention window prevents unbounded memory leak
class SafeStreamingWatchdog:
    def __init__(self, retention_window_seconds: float = 300.0):
        self.retention_window = retention_window_seconds
        self.stack: list[tuple[float, float]] = []  # (timestamp, metric)

    def ingest(self, timestamp: float, metric: float):
        while self.stack and self.stack[-1][1] < metric:
            self.stack.pop()
        
        self.stack.append((timestamp, metric))

        # Evict stale items older than the retention horizon
        cutoff = timestamp - self.retention_window
        self.stack = [item for item in self.stack if item[0] >= cutoff]
```
