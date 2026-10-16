# Day 22: Binary Search & Variants: Rotated Arrays, Search Range & Optimization Boundaries

In distributed databases, storage engines, and high-throughput backend services, sequential scanning ($\mathcal{O}(N)$) over millions of log records or time-series telemetry events is unacceptably slow. Finding a target key in $10{,}000{,}000$ records with linear scan requires up to $10{,}000{,}000$ comparisons; **Binary Search** resolves the query in at most $\lceil \log_2(10^7) \rceil = 24$ comparisons.

From **Kafka log segment index files (`.index`)** and **RocksDB / Cassandra SSTable sparse block indexes** to **B-Tree page lookups** and **Kubernetes autoscaling optimization boundaries**, binary search is the backbone of high-performance lookup engines.

Mastering binary search requires transcending simple array lookups and grasping the **Monotonic Predicate Theorem**: transforming any problem with a monotonic boolean property ($F \dots F \mid T \dots T$) into an optimal $\mathcal{O}(\log N)$ boundary search.

---

## 1. Binary Search in Distributed Databases & Storage Systems

```
DATABASE LOG SEGMENT LOOKUP (e.g. Apache Kafka / RocksDB SSTable):
Index File: Sorted list of [Offset -> Physical Byte Position]

Target Offset: 4,520
Sparse Index:
┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ Offset: 1000 │ Offset: 2000 │ Offset: 3000 │ Offset: 4000 │ Offset: 5000 │
│ Pos: 0x0000  │ Pos: 0x0400  │ Pos: 0x0800  │ Pos: 0x0C00  │ Pos: 0x1000  │
└──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
Binary search finds the closest lower bound (Offset 4000 at Pos 0x0C00) in O(log N).
The database seeks directly to 0x0C00 on disk and scans only a small local block!
```

Without binary search on sparse indexes, databases would need to read hundreds of gigabytes from disk for every single query.

---

## 2. The Monotonic Predicate Theorem ("Binary Search on Answer")

Binary search is **not limited to sorted numeric arrays**. It applies to ANY search space $S$ where a function $f(x)$ maps elements monotonically to boolean values:

```
Search Space:  [ x0,   x1,   x2,   x3,   x4,   x5,   x6,   x7 ]
Predicate P(x): False False False False  True  True  True  True
                                      ▲
                                Boundary Point
```

### The Theorem:
If a predicate $P(x)$ satisfies the monotonicity invariant:
$$\forall x \le y: P(x) \implies P(y) \quad \text{(or } P(y) \implies P(x) \text{)}$$
Then the transition point (first $x$ where $P(x)$ is True, or last $x$ where $P(x)$ is False) can be found in $\mathcal{O}(\log(\text{range})) \times \mathcal{O}(\text{evaluate } P)$.

### Backend Engineering Applications:
- **Rate Limit Optimization**: What is the minimum worker count $K$ to process 1,000,000 tasks within 60 seconds without exceeding API rate limits?
- **AI Batch Inference Sizing**: What is the maximum batch size $B$ that fits into 80 GB GPU VRAM without triggering CUDA OOM?
- **Network Bandwidth Bottlenecks**: What is the maximum throughput $R$ before packet drop rate exceeds 0.01%?

---

## 3. Midpoint Calculation & Integer Overflow Hygiene

In languages like C, C++, and Java:
```c
int mid = (left + right) / 2; // BUG: If left + right > 2^31 - 1, integer overflows to negative!
```

In Python, integers have arbitrary precision (unbounded `long`), so `(left + right) // 2` does not crash with numeric overflow. However, in enterprise systems engineering, maintaining portability across CPython extensions, Cython, and Rust/C bindings requires adhering to the universal safe idiom:

```python
# PRODUCTION SAFE MIDPOINT:
mid = left + (right - left) // 2
```

---

## 4. The 3 Canonical Loop Invariant Templates

There are three distinct loop structures in binary search. Selecting the wrong variant is the #1 source of infinite loops and off-by-one bugs:

### Template 1: Closed Interval `[left, right]` (Exact Match)
```python
left, right = 0, len(nums) - 1
while left <= right:
    mid = left + (right - left) // 2
    if nums[mid] == target:
        return mid
    elif nums[mid] < target:
        left = mid + 1
    else:
        right = mid - 1
return -1  # Not found
```
- **Loop Condition**: `left <= right`
- **Termination State**: `left = right + 1` (search space completely exhausted).
- **Best For**: Direct element lookup in a sorted list.

### Template 2: Half-Open Interval `[left, right)` (Boundary / Insertion Point)
```python
left, right = 0, len(nums)
while left < right:
    mid = left + (right - left) // 2
    if condition(nums[mid]):
        right = mid      # Target lies in left half including mid
    else:
        left = mid + 1   # Target lies strictly to the right
return left  # left == right is the boundary
```
- **Loop Condition**: `left < right`
- **Termination State**: `left == right` (single boundary element).
- **Best For**: `bisect_left`, first element $\ge \text{target}$, or monotonic optimization boundaries.

### Template 3: Open Interval `(left, right)` with Two Elements Remaining
```python
left, right = 0, len(nums) - 1
while left + 1 < right:
    mid = left + (right - left) // 2
    # Adjust left = mid or right = mid without skipping
# Post-process: Explicitly check left and right
```
- **Best For**: Finding peaks, local extrema, or complex boundary conditions requiring neighbor checks.

---

## 5. Bisect-Left vs. Bisect-Right (Lower Bound vs. Upper Bound)

Given sorted array with duplicates: `nums = [1, 2, 4, 4, 4, 5, 8]`, `target = 4`:

```
Array:        [ 1,  2,  4,  4,  4,  5,  8 ]
Index:          0   1   2   3   4   5   6
                        ▲           ▲
                   bisect_left   bisect_right
                   (First >= 4)   (First > 4)
                   Index: 2       Index: 5
```

- **`bisect_left`**: Returns the index of the **first** element that is $\ge \text{target}$. (Insertion index before existing duplicates).
- **`bisect_right`**: Returns the index of the **first** element that is $> \text{target}$. (Insertion index after existing duplicates).
- **Number of Occurrences of Target**: $\text{count} = \text{bisect\_right} - \text{bisect\_left}$.

---

## 6. Find First and Last Position (`search_range`)

To find the range `[start, end]` of a target in $\mathcal{O}(\log N)$ time:
1. Run `bisect_left(nums, target)` to find `start`.
2. If `start == len(nums)` or `nums[start] != target`, target does not exist; return `[-1, -1]`.
3. Run `bisect_right(nums, target) - 1` to find `end`.
4. Return `[start, end]`.

---

## 7. Search in Rotated Sorted Array I (Unique Elements)

An array sorted in ascending order is rotated at an unknown pivot (e.g. `[4, 5, 6, 7, 0, 1, 2]`):

```
       7 
     6   
   5     
 4                    2
                    1  
                  0    
 └──────┬──────┘  └────┬────┘
  Left Sorted      Right Sorted
```

### The Invariant:
**At least one half of the array is ALWAYS strictly sorted!**
1. Compute `mid = left + (right - left) // 2`.
2. Check if `nums[mid] == target`: return `mid`.
3. Determine which half is sorted:
   - If `nums[left] <= nums[mid]`: The **Left Half** `[left .. mid]` is sorted!
     - If `nums[left] <= target < nums[mid]`: Target lies inside left half $\implies$ `right = mid - 1`.
     - Else: Target lies in right half $\implies$ `left = mid + 1`.
   - Else: The **Right Half** `[mid .. right]` is sorted!
     - If `nums[mid] < target <= nums[right]`: Target lies inside right half $\implies$ `left = mid + 1`.
     - Else: Target lies in left half $\implies$ `right = mid - 1`.

---

## 8. Search in Rotated Sorted Array II (With Duplicates)

When duplicates are allowed (e.g. `[1, 0, 1, 1, 1]`), if `nums[left] == nums[mid] == nums[right]`, we cannot determine which half is sorted!

```
Array:   [ 1,  0,  1,  1,  1 ]
          ▲        ▲        ▲
        left      mid     right
Both nums[left] and nums[right] equal nums[mid]!
Is the left half sorted, or the right half? IMPOSSIBLE to know!
```

### The Solution:
Trim the boundaries: increment `left += 1` and decrement `right -= 1`.
- In the worst case (all elements identical except one), time complexity degrades from $\mathcal{O}(\log N)$ to **$\mathcal{O}(N)$**.

---

## 9. Find Minimum in Rotated Sorted Array

To find the inflection point / minimum value:
Compare `nums[mid]` against `nums[right]`:
- If `nums[mid] > nums[right]`: The minimum MUST be strictly to the right of `mid` $\implies$ `left = mid + 1`.
- If `nums[mid] <= nums[right]`: The minimum could be `mid` or to its left $\implies$ `right = mid`.

```python
def find_min(nums: list[int]) -> int:
    left, right = 0, len(nums) - 1
    while left < right:
        mid = left + (right - left) // 2
        if nums[mid] > nums[right]:
            left = mid + 1
        else:
            right = mid
    return nums[left]
```

---

## 10. Search in 2D Matrix (Virtual 1D Mapping)

Given an $M \times N$ matrix where each row is sorted and the first element of each row is greater than the last of the previous row:

```
Row 0: [  1,   3,   5,   7 ]
Row 1: [ 10,  11,  16,  20 ]
Row 2: [ 23,  30,  34,  60 ]
```

Treat the 2D grid as a single contiguous virtual array of length $M \times N$:
$$\text{row} = \text{mid} // N, \quad \text{col} = \text{mid} \% N$$
Perform standard binary search in $\mathcal{O}(\log(M \times N))$ time with zero extra memory!

---

## 11. Optimization Boundaries ("Binary Search on Answer")

### The Koko Eating Bananas Pattern:
Given piles of work and an hourly deadline $H$, find the **minimum speed $K$** to finish all work within $H$.

```python
def min_eating_speed(piles: list[int], h: int) -> int:
    def can_finish(speed: int) -> bool:
        # Total hours = sum of ceil(pile / speed)
        return sum((pile + speed - 1) // speed for pile in piles) <= h

    left, right = 1, max(piles)
    while left < right:
        mid = left + (right - left) // 2
        if can_finish(mid):
            right = mid      # Try slower speed
        else:
            left = mid + 1   # Too slow, must speed up
    return left
```

---

## 12. Python `bisect` Module Architecture

CPython provides the standard library module `bisect`:
- Written in C (`_bisectmodule.c`) for maximum CPU speed.
- `bisect.bisect_left(a, x, lo=0, hi=len(a), *, key=None)`
- `bisect.bisect_right(a, x, lo=0, hi=len(a), *, key=None)`
- `bisect.insort_left(a, x)`: Performs binary search then `list.insert(idx, x)` (Note: `insert` is still $\mathcal{O}(N)$ due to pointer shifts!).

---

## 13. Interpolation vs. Exponential vs. Binary Search

| Algorithm | Best For | Time Complexity | Requirement |
| :--- | :--- | :--- | :--- |
| **Binary Search** | General sorted data | $\mathcal{O}(\log N)$ | Random access |
| **Exponential Search** | Unbounded / infinite streams | $\mathcal{O}(\log K)$ ($K$ is target position) | Monotonic stream |
| **Interpolation Search** | Uniformly distributed data | $\mathcal{O}(\log \log N)$ average | Known distribution |

---

## 14. 7 Golden Invariant Rules for Binary Search

1. **Calculate Safe Midpoint**: Always use `mid = left + (right - left) // 2`.
2. **Match Loop Condition to Updates**:
   - `while left <= right`: Update `left = mid + 1` and `right = mid - 1`.
   - `while left < right`: Update `left = mid + 1` and `right = mid` (or vice-versa with ceiling mid).
3. **Check Monotonicity First**: Never apply binary search to an unsorted or fluctuating dataset without verifying the monotonic invariant.
4. **Beware of Duplicates**: Duplicates in rotated arrays collapse $\mathcal{O}(\log N)$ to $\mathcal{O}(N)$.
5. **Use Integer Ceiling Division for Rates**: Compute `ceil(a / b)` via `(a + b - 1) // b` to avoid floating-point inaccuracies.
6. **Virtual 2D Mapping**: Avoid row-by-row searching in sorted matrices; map flat 1D indices via `mid // cols` and `mid % cols`.
7. **Favor `bisect` for Production**: Use standard library `bisect_left` / `bisect_right` in production Python code for C-speed execution.

---

## 15. Complexity Matrix

| Problem / Variant | Time Complexity (Best) | Time Complexity (Worst) | Auxiliary Space |
| :--- | :--- | :--- | :--- |
| **Exact Binary Search** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| **Search Range (First & Last)** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| **Rotated Array I (Unique)** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| **Rotated Array II (Duplicates)**| $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ |
| **Find Minimum in Rotated** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| **Search 2D Matrix** | $\mathcal{O}(1)$ | $\mathcal{O}(\log(M \times N))$ | $\mathcal{O}(1)$ |
| **Binary Search on Answer** | $\mathcal{O}(1)$ | $\mathcal{O}(N \log(\text{range}))$ | $\mathcal{O}(1)$ |
