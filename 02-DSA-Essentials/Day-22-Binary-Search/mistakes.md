# Day 22: 7 Critical Antipatterns in Binary Search

Binary search is conceptually one of the simplest algorithms in computer science, yet historical studies (including Jon Bentley's famous analysis in *Programming Pearls*) revealed that over 90% of professional software engineers implement it with subtle bugs.

Below are 7 critical antipatterns, their low-level runtime consequences, and enterprise remediations.

---

## 1. The Integer Overflow Bug in Midpoint Calculation

### The Mistake
Calculating the midpoint using naive addition:

```python
# WRONG: Dangerous in C/C++/Cython extensions and foreign function interfaces!
mid = (left + right) // 2
```

### The Consequence
While pure Python handles arbitrary-precision integers without overflow, production backend services frequently interface with C/C++ shared libraries, Cython, NumPy, PyTorch C++ kernels, or Rust bindings. If `left + right > 2,147,483,647` (the maximum signed 32-bit integer), the sum overflows into a negative number, resulting in an immediate `IndexError` or segmentation fault.

### The Correct Approach
Always write the algebraically equivalent, overflow-safe midpoint calculation:

```python
# CORRECT: Overflow-safe across all compiled languages and runtimes
mid = left + (right - left) // 2
```

---

## 2. The Infinite Loop Trap (`left = mid` without Rounding Up)

### The Mistake
Using `left = mid` in a `while left < right` loop when integer division rounds down:

```python
# WRONG: Freezes in an infinite loop when right - left == 1!
def find_boundary_broken(nums: list[int]) -> int:
    left, right = 0, len(nums) - 1
    while left < right:
        mid = (left + right) // 2
        if condition(nums[mid]):
            left = mid  # BUG: When left = 0, right = 1 => mid = 0. left remains 0 forever!
        else:
            right = mid - 1
    return left
```

### The Consequence
When two elements remain (`left = 4`, `right = 5`):
- `mid = 4 + (5 - 4) // 2 = 4`.
- If the branch sets `left = mid`, `left` is reassigned to `4`.
- The state does not advance; the loop executes infinitely, consuming 100% CPU on the production node.

### The Correct Approach
Either use `left = mid + 1`, or if you must assign `left = mid`, you **must round up** the midpoint calculation:

```python
# CORRECT Option A: Standard right-shrinking template
while left < right:
    mid = left + (right - left) // 2
    if condition(nums[mid]):
        right = mid      # Safe: mid can never equal right
    else:
        left = mid + 1   # Safe: strictly advances left

# CORRECT Option B: Ceiling midpoint when left = mid
mid = left + (right - left + 1) // 2  # Rounds UP
if condition(nums[mid]):
    left = mid           # Safe: mid > left when right > left
else:
    right = mid - 1
```

---

## 3. Mismatched Loop Bounds and Interval Termination

### The Mistake
Using `while left < right` for an exact search on `[0, len(nums) - 1]`:

```python
# WRONG: Misses the target when target is at the very last remaining index!
def search_exact_broken(nums: list[int], target: int) -> int:
    left, right = 0, len(nums) - 1
    while left < right:  # BUG: Exits when left == right, leaving 1 element uninspected!
        mid = left + (right - left) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1  # If target is at nums[left], returns -1!
```

### The Consequence
For `nums = [5]`, `target = 5`:
- `left = 0`, `right = 0`.
- Loop condition `left < right` (`0 < 0`) is `False`.
- The loop never runs, and the function returns `-1` even though the target is right at index 0!

### The Correct Approach
For exact searches over the closed interval `[0, len(nums) - 1]`, the condition must be `while left <= right`:

```python
# CORRECT: Exhausts every candidate element
left, right = 0, len(nums) - 1
while left <= right:
    mid = left + (right - left) // 2
    if nums[mid] == target:
        return mid
    elif nums[mid] < target:
        left = mid + 1
    else:
        right = mid - 1
return -1
```

---

## 4. Overlooking $\mathcal{O}(N)$ Degradation in Rotated Arrays with Duplicates

### The Mistake
Assuming binary search on rotated arrays is guaranteed $\mathcal{O}(\log N)$ when duplicates exist:

```python
# FLAWED ASSUMPTION: Believing this is always O(log N)
def search_rotated_duplicates(nums: list[int], target: int) -> bool:
    # If nums = [1, 1, 1, 1, 1, 0, 1] and target = 0:
    # nums[left] == nums[mid] == nums[right] == 1!
```

### The Consequence
When `nums[left] == nums[mid] == nums[right]`, it is mathematically impossible to know whether the pivot is in the left half or right half. You cannot discard half the search space; you can only trim `left += 1` and `right -= 1`. In the worst case (e.g. an array of 1,000,000 identical numbers with a single zero), the algorithm degrades to $\mathcal{O}(N)$ linear time.

### The Correct Approach
Acknowledge the worst-case degradation and trim duplicate boundaries explicitly:

```python
# CORRECT: Explicit duplicate trimming with O(N) worst-case awareness
if nums[left] == nums[mid] == nums[right]:
    left += 1
    right -= 1
```

---

## 5. Floating-Point Inaccuracies in Optimization Boundary Search

### The Mistake
Using floating-point division `math.ceil(w / r)` in monotonic rate calculations:

```python
# WRONG: Floating-point precision errors corrupt boundary decisions!
import math

def can_finish_floating(workloads: list[int], rate: int, max_hours: int) -> bool:
    total_hours = sum(math.ceil(w / rate) for w in workloads)
    return total_hours <= max_hours
```

### The Consequence
IEEE 754 floating-point division is imprecise. For large numbers, `w / rate` can produce values like `3.0000000000000004`, causing `math.ceil()` to return `4` instead of `3`. Across thousands of aggregated workloads, these off-by-one errors accumulate, causing binary search to select an incorrect suboptimal rate.

### The Correct Approach
Use pure integer ceiling arithmetic:

```python
# CORRECT: Exact integer ceiling division without floating-point inaccuracies
# Formula: ceil(a / b) == (a + b - 1) // b
total_hours = sum((w + rate - 1) // rate for w in workloads)
```

---

## 6. Performing Binary Search on Unsorted or Dynamically Mutated Data

### The Mistake
Calling `bisect` on a list that was not sorted, or appending to a sorted list without re-establishing sorting invariants:

```python
# WRONG: Garbage results when input violates monotonic invariant!
raw_logs = [45, 12, 89, 3, 102]
idx = bisect.bisect_left(raw_logs, 89)  # Returns completely bogus index!
```

### The Consequence
Binary search relies strictly on the monotonic invariant ($A[i] \le A[i+1]$). If even a single inversion exists, binary search may discard the half of the array containing the target, returning false negatives or corrupt insertion indices.

### The Correct Approach
Ensure the dataset is sorted prior to search, or maintain sorted order during insertion using `bisect.insort`:

```python
# CORRECT: Maintain sorted invariant at ingestion time
import bisect

sorted_logs = []
for event in incoming_events:
    bisect.insort(sorted_logs, event)  # Maintains ascending order
```

---

## 7. Off-by-One in Virtual 2D Matrix Flattening

### The Mistake
Swapping rows and columns when converting a 1D mid index to 2D coordinates:

```python
# WRONG: Swaps row and column dimensions!
rows = len(matrix)
cols = len(matrix[0])
mid = left + (right - left) // 2

# BUG: Dividing by rows instead of cols!
r = mid // rows  # WRONG!
c = mid % rows   # WRONG!
val = matrix[r][c]  # Raises IndexError or fetches wrong cell!
```

### The Consequence
In row-major order (standard 2D array representation in Python and C), a row consists of `cols` elements. Therefore, the row number is how many full groups of `cols` fit into `mid` (`mid // cols`), and the column number is the remainder (`mid % cols`). Dividing by `rows` yields invalid coordinates and crashes with `IndexError: list index out of range`.

### The Correct Approach
Always map 1D indices using the number of columns (`cols`):

```python
# CORRECT: Standard row-major coordinate transformation
r = mid // cols
c = mid % cols
val = matrix[r][c]
```
