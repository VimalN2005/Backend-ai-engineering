# Day 16: 7 Critical Antipatterns in Two Pointers & In-Place Array Algorithms

Even experienced backend engineers make subtle architectural and indexing errors when implementing Two Pointer algorithms. Below are 7 common mistakes, their algorithmic consequences, and production-grade remediations.

---

## 1. Applying Opposite-Direction Pointers to Unsorted Collections

### The Mistake
Assuming that opposite-direction two pointers work on arbitrary unsorted arrays:
```python
# WRONG: Unsorted input breaks monotonicity
def two_sum_naive(nums: list[int], target: int):
    # nums = [3, 1, 4, 1, 5, 9], target = 5
    left = 0
    right = len(nums) - 1
    while left < right:
        if nums[left] + nums[right] == target:
            return left, right
        elif nums[left] + nums[right] < target:
            left += 1  # INVALID ASSUMPTION: nums[left+1] is not guaranteed to be larger!
        else:
            right -= 1
```

### The Consequence
The algorithm silently skips valid pairs because the monotonicity invariant ($nums[i] \le nums[i+1]$) does not hold. It produces wrong answers or returns `None`.

### The Correct Approach
Either verify the array is sorted upfront, or sort it before applying the two-pointer sweep:
```python
# CORRECT: Verify preconditions or sort upfront
def two_sum_sorted(nums: list[int], target: int):
    # If array is unsorted, either use a Hash Map (O(N) time, O(N) space)
    # or sort first if indices do not need to be preserved (O(N log N) time, O(1) space).
    left, right = 0, len(nums) - 1
    while left < right:
        s = nums[left] + nums[right]
        if s == target:
            return left, right
        elif s < target:
            left += 1
        else:
            right -= 1
    return None
```

---

## 2. Using `del` or `list.remove()` During In-Place Array Mutation

### The Mistake
Attempting in-place deduplication by deleting items directly inside a loop:
```python
# WRONG: Catastrophic O(N^2) time complexity + skips elements due to index shifting
def remove_duplicates_wrong(nums: list[int]):
    for i in range(len(nums) - 1, 0, -1):
        if nums[i] == nums[i - 1]:
            del nums[i]  # O(N) memory shift for EVERY deletion!
```

### The Consequence
In CPython, deleting an element from a list requires `memmove()` to shift all subsequent elements to the left by one position. For $N = 100{,}000$ elements, this triggers up to $10^{10}$ memory copy operations, freezing the CPU for seconds and corrupting index tracking.

### The Correct Approach
Use the **Fast & Slow Runner (Read/Write Head)** pattern:
```python
# CORRECT: Pure O(N) single-pass in-place compaction with zero memory shifts
def remove_duplicates_correct(nums: list[int]) -> int:
    if not nums:
        return 0
    write_idx = 0
    for read_idx in range(1, len(nums)):
        if nums[read_idx] != nums[write_idx]:
            write_idx += 1
            nums[write_idx] = nums[read_idx]
    return write_idx + 1
```

---

## 3. Boundary Condition Flaws: `while left <= right` vs `while left < right`

### The Mistake
Using the wrong loop condition in Two Sum or Container With Most Water:
```python
# WRONG: Allows left and right to point to the exact same element!
while left <= right:
    if nums[left] + nums[right] == target:
        return left, right  # Bug: If target = 2 * nums[left], returns duplicate index!
```

### The Consequence
When `left == right`, the algorithm pairs an element with **itself**. For example, in `nums = [2, 5, 8]` with `target = 10`, when both pointers reach index `1` (`nums[1] = 5`), the condition `5 + 5 == 10` evaluates to true, mistakenly returning `(1, 1)` when no distinct pair exists.

### The Correct Approach
Use `left < right` when searching for pairs of distinct elements:
```python
# CORRECT: Guarantees left and right reference two distinct indices
while left < right:
    current_sum = nums[left] + nums[right]
    # ...
```

---

## 4. Omitting Duplicate Skipping in 3Sum Triplet Searches

### The Mistake
Finding matching triplets without skipping duplicate values:
```python
# WRONG: Yields duplicate triplets and suffers quadratic bloat
if current_sum == 0:
    results.append([nums[i], nums[left], nums[right]])
    left += 1
    right -= 1  # If nums[left] == nums[left-1], duplicate triplet will be added!
```

### The Consequence
For `nums = [-2, 0, 0, 2, 2]`, the triplet `[-2, 0, 2]` will be emitted multiple times. Attempting to deduplicate post-hoc via `set(tuple(x))` allocates significant heap memory and slows down execution.

### The Correct Approach
Advance pointers past identical values immediately after recording a valid triplet:
```python
# CORRECT: In-line duplicate pruning at zero allocation cost
if current_sum == 0:
    results.append([nums[i], nums[left], nums[right]])
    while left < right and nums[left] == nums[left + 1]:
        left += 1
    while left < right and nums[right] == nums[right - 1]:
        right -= 1
    left += 1
    right -= 1
```

---

## 5. Advancing the Wrong Pointer in Container With Most Water

### The Mistake
Moving the pointer with the taller line or choosing pointers arbitrarily:
```python
# WRONG: Moving the taller pointer discards potential larger containers
if heights[left] > heights[right]:
    left += 1  # Disastrous error!
else:
    right -= 1
```

### The Consequence
The maximum container area is constrained by $\min(H[\text{left}], H[\text{right}])$. If you discard the taller line, the new container has smaller width AND cannot possibly have a larger height than the remaining shorter line. The optimal solution is permanently missed.

### The Correct Approach
Always advance the pointer pointing to the **shorter line**:
```python
# CORRECT: Greedy proof dictates discarding the bottleneck (shorter line)
if heights[left] < heights[right]:
    left += 1
else:
    right -= 1
```

---

## 6. Returning `slow` Instead of `slow + 1` in Fast/Slow Compaction

### The Mistake
Returning the final write pointer index instead of the count of unique elements:
```python
# WRONG: Off-by-one error in length return
def remove_duplicates(nums: list[int]) -> int:
    slow = 0
    for fast in range(1, len(nums)):
        if nums[fast] != nums[slow]:
            slow += 1
            nums[slow] = nums[fast]
    return slow  # BUG: slow is 0-indexed! Length should be slow + 1!
```

### The Consequence
If an array has 5 unique elements, `slow` will finish at index `4`. Returning `slow` reports 4 elements instead of 5, causing callers slicing `nums[:length]` to drop the final unique element.

### The Correct Approach
```python
# CORRECT: Return number of valid items (index + 1)
return slow + 1
```

---

## 7. Using Hash Maps for Large Sorted Datasets in Memory-Constrained Systems

### The Mistake
Defaulting to Hash Maps (`dict` / `set`) for pair lookups when data is already sorted:
```python
# SUBOPTIMAL: Allocates O(N) heap memory and pollutes CPU cache
def find_pairs_hashmap(sorted_logs: list[int], target_latency: int):
    seen = set()
    for latency in sorted_logs:
        complement = target_latency - latency
        if complement in seen:
            return complement, latency
        seen.add(latency)
```

### The Consequence
For 10,000,000 telemetry events, a Python `set` consumes ~300 MB of heap memory and triggers random pointer jumps across RAM, causing high L1/L2 cache misses.

### The Correct Approach
Leverage Two Pointers to achieve **$\mathcal{O}(1)$ auxiliary memory** with sequential memory access:
```python
# OPTIMAL: Zero heap allocations and 100% sequential cache line prefetching
def find_pairs_twopointer(sorted_logs: list[int], target_latency: int):
    left, right = 0, len(sorted_logs) - 1
    while left < right:
        s = sorted_logs[left] + sorted_logs[right]
        if s == target_latency:
            return sorted_logs[left], sorted_logs[right]
        elif s < target_latency:
            left += 1
        else:
            right -= 1
    return None
```

---

## Summary Checklist
- [x] Verify data is sorted before using opposite-direction pointers.
- [x] Never delete elements via `del` or `remove()` during array iteration; use fast/slow pointers.
- [x] Use `left < right` for distinct element pair searches.
- [x] Skip duplicate values in-line during 3Sum to maintain uniqueness without sets.
- [x] Always advance the shorter line's pointer in Container With Most Water.
- [x] Remember `write_idx + 1` when returning lengths from in-place compactions.
- [x] Favor Two Pointers over Hash Maps on sorted streams to eliminate heap allocations.
