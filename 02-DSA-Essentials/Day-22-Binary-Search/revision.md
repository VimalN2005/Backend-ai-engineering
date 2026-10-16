# Day 22: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Binary Search, Rotated Invariants, Monotonic Predicates, and Boundary Optimization for backend engineers.

---

## 1. Binary Search Loop Variant Decision Matrix

| Target Use Case | Interval Model | Loop Condition | Left Update | Right Update | Exit State |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Exact Match** | `[left, right]` | `while left <= right:` | `left = mid + 1` | `right = mid - 1` | `left == right + 1` (Not found) |
| **Lower Bound (`bisect_left`)** | `[left, right)` | `while left < right:` | `left = mid + 1` | `right = mid` | `left == right` (First $\ge \text{target}$) |
| **Upper Bound (`bisect_right`)**| `[left, right)` | `while left < right:` | `left = mid + 1` | `right = mid` | `left == right` (First $> \text{target}$) |
| **Binary Search on Answer** | `[min_ans, max_ans)` | `while left < right:` | `left = mid + 1` | `right = mid` | `left == right` (Optimal answer) |
| **Peak / Neighbor Comparison** | `(left, right)` | `while left + 1 < right:` | `left = mid` | `right = mid` | Evaluate both `left` and `right` |

---

## 2. Essential Code Templates

### Template A: Exact Binary Search ($\mathcal{O}(\log N)$ Time)
```python
def binary_search(nums: list[int], target: int) -> int:
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

### Template B: Lower Bound (`bisect_left`)
```python
def bisect_left_custom(nums: list[int], target: int) -> int:
    left, right = 0, len(nums)
    while left < right:
        mid = left + (right - left) // 2
        if nums[mid] >= target:
            right = mid
        else:
            left = mid + 1
    return left
```

### Template C: Binary Search on Answer (Monotonic Predicate)
```python
def min_feasible_value(low: int, high: int, is_valid: Callable[[int], bool]) -> int:
    left, right = low, high
    while left < right:
        mid = left + (right - left) // 2
        if is_valid(mid):
            right = mid      # mid is valid; try finding smaller valid value
        else:
            left = mid + 1   # mid is invalid; must increase value
    return left
```

---

## 3. Rotated Sorted Array Invariant Cheat Sheet

```
Is nums[left] <= nums[mid]?
   ├── YES: Left half [left .. mid] is SORTED!
   │         └── Is nums[left] <= target < nums[mid]?
   │               ├── YES: right = mid - 1
   │               └── NO:  left  = mid + 1
   │
   └── NO:  Right half [mid .. right] is SORTED!
             └── Is nums[mid] < target <= nums[right]?
                   ├── YES: left  = mid + 1
                   └── NO:  right = mid - 1
```

---

## 4. 5 Non-Negotiable Binary Search Rules

1. **Overflow-Proof Midpoint**: Always compute `mid = left + (right - left) // 2`.
2. **Ceiling Mid When `left = mid`**: If you ever write `left = mid`, midpoint MUST round up (`mid = left + (right - left + 1) // 2`) to avoid infinite loops.
3. **Pure Integer Ceiling Division**: Compute $\lceil a / b \rceil$ as `(a + b - 1) // b` to eliminate floating-point precision bugs.
4. **Virtual 2D Matrix Flattening**: Map 1D index to 2D coordinates via `r = mid // cols` and `c = mid % cols`.
5. **Beware Rotated Duplicates**: When `nums[left] == nums[mid] == nums[right]`, binary division collapses to $\mathcal{O}(N)$ linear trimming.

---

## 5. Complexity Quick Reference Table

| Algorithm | Best Time | Worst Time | Auxiliary Space | Key Technique |
| :--- | :--- | :--- | :--- | :--- |
| **Exact Search** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | Closed Interval `[left, right]` |
| **Search Range** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | `bisect_left` & `bisect_right` |
| **Rotated Array Search** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | Identify sorted half |
| **Rotated Search (Duplicates)**| $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Boundary duplicate trimming |
| **Find Minimum in Rotated** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | Compare `nums[mid]` vs `nums[right]` |
| **Find Peak Element** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | Upward gradient following |
| **Ship Packages in D Days** | $\mathcal{O}(1)$ | $\mathcal{O}(N \log(\Sigma W))$ | $\mathcal{O}(1)$ | Monotonic Predicate Theorem |
| **Median of Two Sorted** | $\mathcal{O}(1)$ | $\mathcal{O}(\log(\min(M, N)))$ | $\mathcal{O}(1)$ | Dual-array balanced partition |
