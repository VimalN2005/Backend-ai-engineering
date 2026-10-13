# Day 18: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to HashMaps, Sets, CPython Internals, and Hash Join Algorithms for backend engineers.

---

## 1. CPython Compact Dict Architecture (PEP 468)

```
        SPARSE INDICES ARRAY (dk_indices: int8/int16)
        [ -1,   0,  -1,  -1,   1,  -1,   2,  -1 ]
                │             │          │
                ▼             ▼          ▼
        DENSE ENTRIES ARRAY (dk_entries: PyDictKeyEntry)
        [0] -> (hash_0, key_0, val_0)
        [1] -> (hash_1, key_1, val_1)   <-- Appended in chronological insertion order!
        [2] -> (hash_2, key_2, val_2)
```
- **Saves 20–25% RAM** by storing small byte indices in the sparse table instead of full entry pointers.
- **Guarantees insertion order** because `dk_entries` is appended sequentially.

---

## 2. Core Algorithmic Templates

### Template A: Subarray Sum Equals K (Prefix Sums + Hash Map)
```python
def subarray_sum_k(nums: list[int], k: int) -> int:
    counts = {0: 1}  # Base case: empty prefix sum
    running_sum = 0
    total = 0
    for x in nums:
        running_sum += x
        complement = running_sum - k
        if complement in counts:
            total += counts[complement]
        counts[running_sum] = counts.get(running_sum, 0) + 1
    return total
```

### Template B: Longest Consecutive Sequence ($\mathcal{O}(N)$ Time)
```python
def longest_consecutive(nums: list[int]) -> int:
    num_set = set(nums)
    best = 0
    for x in num_set:
        # Crucial guard clause: ONLY start if x is sequence head!
        if (x - 1) not in num_set:
            curr = x
            streak = 1
            while (curr + 1) in num_set:
                curr += 1
                streak += 1
            best = max(best, streak)
    return best
```

### Template C: Group Anagrams via 26-Element Frequency Tuple
```python
from collections import defaultdict

def group_anagrams(strs: list[str]) -> list[list[str]]:
    groups = defaultdict(list)
    for s in strs:
        counts = [0] * 26
        for ch in s:
            counts[ord(ch) - ord('a')] += 1
        groups[tuple(counts)].append(s)  # O(K) without sorting!
    return list(groups.values())
```

---

## 3. Data Structure Complexity Comparison

| Operation | Hash Table (`dict` / `set`) | Contiguous List (`list`) | Binary Search Tree (`BST`) |
| :--- | :--- | :--- | :--- |
| **Lookup / Search** | **$\mathcal{O}(1)$ average** / $\mathcal{O}(N)$ worst | $\mathcal{O}(N)$ linear scan | $\mathcal{O}(\log N)$ |
| **Insertion** | **$\mathcal{O}(1)$ amortized** | $\mathcal{O}(1)$ append / $\mathcal{O}(N)$ insert | $\mathcal{O}(\log N)$ |
| **Deletion** | **$\mathcal{O}(1)$ average** | $\mathcal{O}(N)$ shift | $\mathcal{O}(\log N)$ |
| **Memory Overhead**| High (66.7% load factor limit) | Low (Contiguous array) | High (2 child pointers per node) |
| **Ordering** | Insertion order (Python 3.7+) | Index order | Sorted key order |

---

## 4. Hash Join (Database Build & Probe)

```python
# Build Phase: Hash smaller table O(M)
lookup = {c.id: c for c in customers}

# Probe Phase: Stream larger table O(N)
for order in orders:
    cust = lookup.get(order.customer_id)
    if cust:
        emit(order, cust)
```
- Total complexity: **$\mathcal{O}(M + N)$ linear time** with zero cartesian product waste.

---

## 5. The Golden Rules for Backend Engineers
1. **Never use `item in my_list` inside loops**; convert to `set` for instant $1{,}000\times+$ lookup speedup.
2. **Never mutate a dictionary or set key**; keys must be strictly immutable to avoid permanent bucket stranding.
3. **Always implement both `__hash__` and `__eq__`** together (or use `@dataclass(frozen=True)`).
4. **Seed `prefix_counts = {0: 1}`** when counting subarrays to catch matches starting at index 0.
5. **Add `(x - 1) not in num_set`** in Longest Consecutive Sequence to prevent $\mathcal{O}(N^2)$ degeneration.
