# Day 16: Two Pointers Technique (Sorted Arrays, Trapping Water & In-Place Manipulation)

Welcome to **Phase 2: Practical Backend DSA Patterns**. In high-throughput distributed backends, algorithmic efficiency is not an academic exercise—it is the difference between a microservice that comfortably handles 100,000 requests per second within 200 MB of RAM, and one that crashes under Out-Of-Memory (OOM) errors and thrashing CPU caches.

The **Two Pointers Technique** is one of the most foundational and computationally efficient algorithmic paradigms in software engineering. It transforms naive quadratic $\mathcal{O}(N^2)$ brute-force iterations into linear $\mathcal{O}(N)$ sweeps while preserving $\mathcal{O}(1)$ auxiliary memory.

---

## 1. The Two Pointers Paradigm in Backend Engineering

In naive algorithms, searching pairs or subarrays requires nested loops:
$$\text{Comparisons} = \frac{N(N - 1)}{2} = \mathcal{O}(N^2)$$

For a dataset of $N = 100{,}000$ telemetry events:
- $\mathcal{O}(N^2)$ requires $10{,}000{,}000{,}000$ operations (~10 seconds in C, >60 seconds in Python).
- $\mathcal{O}(N)$ requires $100{,}000$ operations (< 5 milliseconds).

```
                      NAIVE NESTED LOOPS: O(N^2)
          Outer loop: [i] ──────────────────────────►
          Inner loop:      [j] ─────────────────────►
          Repeats inner scan for every single element!

                     TWO POINTERS PATTERN: O(N)
          [Left ->]                               [<- Right]
          Both pointers move inward based on invariant conditions.
          Each element is visited AT MOST ONCE!
```

By leveraging structural properties of the data (such as monotonicity in sorted arrays or bounded physical constraints), two pointers eliminate vast subtrees of redundant calculations in constant time.

---

## 2. Memory Models & Hardware Cache Locality

Why are Two Pointer array algorithms preferred in production databases and search engines over Hash Maps, even when both achieve $\mathcal{O}(N)$ time?

### 1. CPU Cache Lines (L1/L2/L3) & Spatial Locality
- Modern CPU cores load data in **64-byte Cache Lines**.
- Contiguous arrays (like C arrays or Python list pointer buffers) allow hardware prefetchers to anticipate sequential reads.
- When two pointers move monotonically across contiguous memory (`left += 1` or `right -= 1`), hardware prefetching yields near-100% L1 cache hit rates.

### 2. Hash Map Overhead & Cache Misses
- A hash table lookup entails hashing, bucket indexing, and pointer chasing.
- Every lookup triggers a **random memory jump**, frequently causing L1/L2 cache misses (taking 50–200 CPU cycles per miss).
- Hash tables introduce a 3x–8x memory amplification factor due to sparse bucket arrays and collision linked-nodes.
- In memory-constrained backend systems (e.g., Redis, RocksDB, Kafka consumer pipelines), Two Pointer array manipulation operates with **zero memory allocation ($\mathcal{O}(1)$ space)**.

---

## 3. Pattern 1: Opposite-Direction (Inward-Converging) Pointers

In this pattern, pointers start at opposing boundaries of a sorted collection and advance toward one another until they cross:

```
Index:    0    1    2    3    4    5    6
Array:  [ 2,   5,   8,  11,  15,  19,  24 ]
          ▲                             ▲
        Left                          Right
```

### Invariant & Direction Decision
At each step, we evaluate the state:
$$\text{Current Sum} = \text{Array}[\text{Left}] + \text{Array}[\text{Right}]$$
- If $\text{Current Sum} == \text{Target}$: Match found!
- If $\text{Current Sum} < \text{Target}$: Because the array is sorted, incrementing `Left` is the *only* way to increase the sum.
- If $\text{Current Sum} > \text{Target}$: Decrementing `Right` is the *only* way to decrease the sum.

**Loop Termination**: `while left < right:` (guarantees pointers never cross).

---

## 4. Pattern 2: Fast & Slow Runners (Read & Write Heads)

Used predominantly for **in-place array mutation, deduplication, and compaction**:

```
Index:    0    1    2    3    4    5    6
Array:  [ 1,   1,   2,   2,   3,   4,   4 ]
          ▲    ▲
        Slow  Fast
       (Write)(Read)
```

- **Slow Pointer (`write_idx`)**: Maintains the boundary of valid, compacted data.
- **Fast Pointer (`read_idx`)**: Scans through incoming elements.
- When `nums[read_idx] != nums[slow_idx]`, advance `slow_idx += 1` and write `nums[slow_idx] = nums[read_idx]`.

**Benefit**: Compacts an array in $\mathcal{O}(N)$ time and $\mathcal{O}(1)$ space without creating a secondary list or triggering garbage collector deallocations.

---

## 5. Pattern 3: Dual Pointers Across Two Distinct Sequences

Used extensively in database **Sort-Merge Joins** and **LSM-Tree SSTable Compaction**:

```
Stream A: [ 10,  25,  40,  65 ]  -> Pointer i
Stream B: [ 15,  25,  35,  70 ]  -> Pointer j
```

- If `Stream_A[i] == Stream_B[j]`: Emit join record; advance both `i` and `j`.
- If `Stream_A[i] < Stream_B[j]`: Advance `i` (catch up to B).
- If `Stream_A[i] > Stream_B[j]`: Advance `j` (catch up to A).

Both streams are consumed in a single pass: $\mathcal{O}(N + M)$ time and $\mathcal{O}(1)$ extra memory.

---

## 6. In-Place Array Compaction: Deep Dive

In high-concurrency Python services, creating new lists (`new_list = list(set(old_list))`) incurs severe performance penalties:
1. Allocates a temporary hash set.
2. Allocates a new contiguous Python list.
3. Destroys original ordering unless sorted again ($\mathcal{O}(N \log N)$).
4. Creates GC pressure by leaving the old list for cyclical garbage collection.

### The Fast/Slow In-Place Compaction Algorithm
```python
def remove_duplicates(nums: list[int]) -> int:
    if not nums:
        return 0
    
    write_ptr = 0
    for read_ptr in range(1, len(nums)):
        if nums[read_ptr] != nums[write_ptr]:
            write_ptr += 1
            nums[write_ptr] = nums[read_ptr]
            
    # Return count of unique elements
    return write_ptr + 1
```

After execution, elements from index `0` through `write_ptr` represent unique values in original sorted order.

---

## 7. Two Sum on Sorted Array: Two Pointers vs. Hash Map

| Metric | Hash Map Approach | Two Pointers Approach |
| :--- | :--- | :--- |
| **Time Complexity** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ (if sorted) / $\mathcal{O}(N \log N)$ (if unsorted) |
| **Space Complexity**| $\mathcal{O}(N)$ auxiliary memory | $\mathcal{O}(1)$ auxiliary memory |
| **L1 Cache Hits**   | Low (random pointer hashing) | High (sequential memory traversal) |
| **Memory Footprint**| ~8 MB per 100k integers in Python | 0 additional bytes allocated |
| **Applicability**   | Arbitrary unsorted collections | Monotonic / sorted collections |

### Two Pointers Implementation
```python
def two_sum_sorted(numbers: list[int], target: int) -> tuple[int, int] | None:
    left = 0
    right = len(numbers) - 1
    
    while left < right:
        current_sum = numbers[left] + numbers[right]
        if current_sum == target:
            return left, right
        elif current_sum < target:
            left += 1  # Need larger value
        else:
            right -= 1 # Need smaller value
            
    return None
```

---

## 8. Container With Most Water: Greedy Shrinkage Proof

### Problem Formulation
Given $N$ vertical lines at positions $i$ with heights $H[i]$, find two lines that together with the x-axis form a container holding the maximum water volume:
$$\text{Area}(i, j) = (j - i) \times \min(H[i], H[j])$$

```
     8 │      █                       █
     7 │      █   ════════════════════█
     6 │      █   █                   █
     5 │      █   █   █               █
     4 │      █   █   █       █       █
       └──────┴───┴───┴───────┴───────┴────
       Idx:   1   2   3       6       8
              ▲                       ▲
            Left                    Right
```

### Mathematical Proof of the Greedy Step
Suppose $H[\text{Left}] < H[\text{Right}]$.
- The current width is $W = \text{Right} - \text{Left}$.
- The height is constrained by $\min(H[\text{Left}], H[\text{Right}]) = H[\text{Left}]$.
- If we keep `Left` and move `Right` inward to any `k` ($\text{Left} < k < \text{Right}$):
  - The new width is $k - \text{Left} < W$ (strictly smaller).
  - The new height cannot exceed $H[\text{Left}]$.
  - Therefore: $\text{Area}(\text{Left}, k) \le (k - \text{Left}) \times H[\text{Left}] < \text{Area}(\text{Left}, \text{Right})$.
- **Conclusion**: Any pair containing `Left` with an inward `Right` is mathematically guaranteed to be strictly smaller than the current container!
- Thus, we can safely discard `Left` by executing `Left += 1` without missing the optimal solution.

---

## 9. 3Sum Problem: Sorting + Two Pointers + Duplicate Pruning

Find all unique triplets $[a, b, c]$ such that $a + b + c = 0$.

### Algorithmic Breakdown ($\mathcal{O}(N^2)$ Time, $\mathcal{O}(1)$ Space)
1. **Sort the array**: Allows monotonicity and enables two-pointer convergence.
2. **Anchor Loop**: Iterate `i` from $0$ to $N - 3$:
   - If `nums[i] > 0`, break early (three positive numbers can never sum to 0).
   - If `i > 0` and `nums[i] == nums[i-1]`, skip to avoid duplicate triplets.
3. **Internal Two-Pointer Search**:
   - `left = i + 1`, `right = len(nums) - 1`.
   - Calculate `total = nums[i] + nums[left] + nums[right]`.
   - If `total == 0`: Record triplet; advance `left` past duplicates and `right` past duplicates.
   - If `total < 0`: `left += 1`.
   - If `total > 0`: `right -= 1`.

---

## 10. Trapping Rain Water: 2-Pointer Dynamic Elevation Boundaries

### The Problem
Given an elevation map represented by non-negative integers $H$, compute how much water it can trap after raining.

```
       3 │               █
       2 │       █ ~ ~ ~ █ █ ~ █
       1 │   █ ~ █ █ ~ █ █ █ █ █ █
         └───┴───┴─┴───┴─┴─┴─┴─┴─┴──
       Water trapped = Σ min(max_left[i], max_right[i]) - H[i]
```

### Why Naive Approaches Fall Short
- **Prefix & Suffix Arrays**: Precomputing `max_left` and `max_right` requires two passes and $\mathcal{O}(N)$ extra memory.
- **Two Pointers Optimization ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Space)**:
  - Maintain `left = 0`, `right = N - 1`.
  - Maintain running `left_max = 0` and `right_max = 0`.
  - Key Invariant: If `height[left] < height[right]`:
    - We know for certain that the right side has a boundary $\ge \text{height}[left]$.
    - Thus, the water trapped at `left` is determined solely by `left_max`!
    - If `height[left] >= left_max`, update `left_max = height[left]`.
    - Else, `water += left_max - height[left]`.
    - Increment `left += 1`.
  - Symmetric logic applies when `height[left] >= height[right]`.

---

## 11. Backspace String Compare: Right-to-Left Two Pointers

Given two strings $S$ and $T$ containing backspace characters `'#'`, determine if they evaluate to identical strings.

- **Naive approach**: Use two stacks $\mathcal{O}(N + M)$ space.
- **Two Pointers optimal approach**:
  - Scan both strings **from right to left** (from last index to 0).
  - Track `skip_S` and `skip_T` counts.
  - When seeing `'#'`, increment skip counter.
  - When seeing a regular character with `skip > 0`, decrement skip counter and bypass character.
  - Compare the effective visible characters at both pointers.
  - Achieves $\mathcal{O}(N + M)$ time with **$\mathcal{O}(1)$ extra space**.

---

## 12. Backend Application 1: In-Memory Audit Log Compaction

In high-throughput logging subsystems (e.g., event sourcing engines), duplicate telemetry entries within the same millisecond bucket are compacted in-place to reduce network serialization bandwidth:

```
[Event 1: 10:00:01.001]
[Event 1: 10:00:01.001]  <- Redundant duplicate
[Event 2: 10:00:01.004]
```
Using fast/slow pointer compaction, logs are deduplicated directly in buffer memory before being dispatched to Kafka or S3.

---

## 13. Backend Application 2: Time-Series Metric Stream Alignment

When aligning two asynchronous time-series metric streams (e.g. CPU load vs HTTP latency) arriving from separate Prometheus nodes:
- Pointers advance along timestamps.
- If timestamps match within a tolerance threshold $\Delta$, emit an aligned metric point.
- If stream A is older, advance pointer A. If stream B is older, advance pointer B.

---

## 14. Backend Application 3: Database Sort-Merge Joins & LSM Compaction

Relational databases (PostgreSQL, SQLite) and LSM key-value stores (RocksDB, Cassandra) use Two Pointer merges:
- **Sort-Merge Join**: When two database tables are sorted by join key, the database engine uses dual pointers to compute the join in $\mathcal{O}(M + N)$ time without building hash tables.
- **LSM Compaction**: Multiple on-disk sorted runs (SSTables) are merged into a single sorted SSTable using a multi-way pointer merge, purging deleted keys in-place.

---

## 15. Complexity Comparison Matrix

| Problem / Pattern | Brute Force | Two Pointers | Space Complexity | Sorted Required? |
| :--- | :--- | :--- | :--- | :--- |
| **Two Sum Sorted** | $\mathcal{O}(N^2)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Yes |
| **Remove Duplicates** | $\mathcal{O}(N^2)$ (via `del`) | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Yes |
| **Container Most Water** | $\mathcal{O}(N^2)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | No |
| **3Sum** | $\mathcal{O}(N^3)$ | $\mathcal{O}(N^2)$ | $\mathcal{O}(1)$ | Yes (sorts first) |
| **Trapping Rain Water** | $\mathcal{O}(N^2)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | No |
| **Stream Log Merge** | $\mathcal{O}((N+M) \log(N+M))$ | $\mathcal{O}(N + M)$ | $\mathcal{O}(1)$ | Yes |

---

## Summary Checklist for Day 16
- [x] Understand how Two Pointers eliminate redundant searches by exploiting monotonicity.
- [x] Master CPU cache benefits of sequential array traversal over Hash Map pointer chasing.
- [x] Implement opposite-direction convergence (`left < right`).
- [x] Implement fast/slow runner pointer compaction in-place.
- [x] Prove the greedy shrinkage invariant for Container With Most Water.
- [x] Solve Trapping Rain Water in $\mathcal{O}(N)$ time and $\mathcal{O}(1)$ space using dynamic boundary elevation pointers.
- [x] Apply dual-pointer merging to backend time-series and database stream alignment.
