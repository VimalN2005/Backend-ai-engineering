# Day 16: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to the Two Pointers Technique and In-Place Array Manipulation for backend engineers.

---

## 1. Two Pointers Pattern Taxonomy

```
                      TWO POINTERS PATTERNS
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
  Opposite Direction       Fast & Slow Runners     Dual Stream Merge
  (Inward Converging)      (Read & Write Heads)    (Sort-Merge Joins)
  • Two Sum Sorted         • Remove Duplicates     • Database Joins
  • Container Most Water   • Move Zeroes In-Place  • LSM SSTable Merge
  • Trapping Rain Water    • Linked List Cycles    • Stream Alignment
```

---

## 2. Core Algorithmic Templates

### Template A: Opposite-Direction Convergence
```python
def two_pointer_converge(arr: list[int], target: int):
    left, right = 0, len(arr) - 1
    while left < right:
        current = arr[left] + arr[right]
        if current == target:
            return left, right
        elif current < target:
            left += 1   # Increase sum
        else:
            right -= 1  # Decrease sum
    return None
```

### Template B: Fast & Slow In-Place Compaction
```python
def in_place_compaction(arr: list[int]) -> int:
    if not arr:
        return 0
    write_idx = 0
    for read_idx in range(1, len(arr)):
        if arr[read_idx] != arr[write_idx]:
            write_idx += 1
            arr[write_idx] = arr[read_idx]
    return write_idx + 1  # Return new valid length
```

### Template C: Trapping Rain Water ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Space)
```python
def trap_water(heights: list[int]) -> int:
    left, right = 0, len(heights) - 1
    left_max, right_max = 0, 0
    water = 0
    while left < right:
        if heights[left] < heights[right]:
            if heights[left] >= left_max:
                left_max = heights[left]
            else:
                water += left_max - heights[left]
            left += 1
        else:
            if heights[right] >= right_max:
                right_max = heights[right]
            else:
                water += right_max - heights[right]
            right -= 1
    return water
```

---

## 3. Complexity Reference Table

| Algorithm | Time Complexity | Space Complexity | Sorted Required? | Key Invariant |
| :--- | :--- | :--- | :--- | :--- |
| **Two Sum Sorted** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | **Yes** | Monotonicity dictates sum change. |
| **Remove Duplicates** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | **Yes** | `nums[read] != nums[write]` |
| **Container Most Water**| $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | No | Move shorter boundary inward. |
| **3Sum** | $\mathcal{O}(N^2)$ | $\mathcal{O}(1)$ | **Yes** | Fix anchor `i`, dual pointer for remainder. |
| **Trapping Water** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | No | Bottleneck is strictly on smaller side. |
| **Stream Merge** | $\mathcal{O}(N + M)$ | $\mathcal{O}(1)$ | **Yes** | Monotonic chronological advancement. |

---

## 4. The Golden Rules for Backend Engineers
1. **Always verify sorting** before applying opposite-direction pointer convergence.
2. **Never delete in a loop** via `del` or `remove()`; use fast/slow read-write pointers for $\mathcal{O}(N)$ in-place mutation.
3. **Use `left < right`** for distinct pair problems to avoid pairing an element with itself.
4. **Skip duplicates in-line** during 3Sum/4Sum to prevent duplicate outputs without allocating memory for sets.
5. **Favor Two Pointers over Hash Maps** on sorted data to eliminate heap allocations and leverage 100% L1 CPU cache hits.
