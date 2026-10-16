# Day 22: Practical Hands-On DSA Challenges

Master Binary Search, Rotated Invariants, and Monotonic Optimization Boundaries with these 3 production-level challenges.

---

## Challenge 1: Find Peak Element in Unsorted Terrain ($\mathcal{O}(\log N)$ Time)

### Objective
In high-frequency telemetry monitoring and algorithmic trading anomaly detection, identifying local peaks (e.g., sudden load spikes or price inflection points) must execute in sub-millisecond logarithmic time over millions of data points.

Given an integer array `nums` where `nums[i] != nums[i + 1]` for all valid $i$, find a peak element and return its index. An element is a peak if it is strictly greater than its neighbors. (You may imagine `nums[-1] = nums[n] = -\infty`).

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(\log N)$. A linear $\mathcal{O}(N)$ scan is strictly unacceptable.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$ auxiliary space.
3. **The Upward Gradient Invariant**:
   - Compare `nums[mid]` with `nums[mid + 1]`:
     - If `nums[mid] < nums[mid + 1]`, we are on an upward slope. A peak is mathematically guaranteed to exist to the right of `mid` $\implies$ `left = mid + 1`.
     - If `nums[mid] > nums[mid + 1]`, we are on a downward slope (or `mid` is a peak). A peak is guaranteed to exist at `mid` or to its left $\implies$ `right = mid`.
4. **Test Cases**:
   - `[1, 2, 3, 1]` $\to$ Index `2` (`nums[2] = 3`)
   - `[1, 2, 1, 3, 5, 6, 4]` $\to$ Index `1` or `5` (Both are valid peaks)
   - `[1]` $\to$ Index `0`
   - `[1, 2]` $\to$ Index `1`

---

## Challenge 2: Minimum Worker Fleet Sizing / Capacity to Ship Packages Within D Days

### Objective
In cloud infrastructure and asynchronous task runners (e.g. Celery / AWS SQS batch workers), a sequence of jobs with varying compute weights must be processed in order over $D$ contiguous time windows (or days). Each worker can process any contiguous block of jobs up to its maximum capacity $C$ per day.

Given a list of job `weights` and an integer `days`, find the **minimum worker capacity $C$** required to ship all jobs within `days`.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N \log(\text{sum}(weights) - \max(weights)))$.
2. **Space Complexity**: $\mathcal{O}(1)$ auxiliary space.
3. **Monotonic Search Space Invariant**:
   - Minimum possible capacity: $\text{left} = \max(weights)$ (a worker must at least be able to carry the heaviest individual job).
   - Maximum possible capacity: $\text{right} = \text{sum}(weights)$ (one worker processes everything in a single day).
   - Monotonic Predicate `can_ship(capacity: int) -> bool`:
     Iterate through weights greedily packing into the current day. If adding a weight exceeds `capacity`, start a new day. Return `required_days <= days`.
4. **Test Cases**:
   - `weights = [1,2,3,4,5,6,7,8,9,10]`, `days = 5` $\to$ `15`
     - Day 1: [1,2,3,4,5] (weight 15)
     - Day 2: [6,7] (weight 13)
     - Day 3: [8] (weight 8)
     - Day 4: [9] (weight 9)
     - Day 5: [10] (weight 10)
   - `weights = [3,2,2,4,1,4]`, `days = 3` $\to$ `6`
   - `weights = [1,2,3,1,1]`, `days = 4` $\to$ `3`

---

## Challenge 3: Median of Two Sorted Arrays in $\mathcal{O}(\log(\min(M, N)))$ Time

### Objective
In distributed database partition mergers and federated telemetry analytics, finding the global median across two independent sorted streams without merging them into memory is a fundamental benchmark of algorithmic design.

Given two sorted arrays `nums1` of size $M$ and `nums2` of size $N$, return the global median of the two sorted arrays in logarithmic time.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(\log(\min(M, N)))$. Merging the two arrays in $\mathcal{O}(M + N)$ time or allocating an auxiliary merged array is strictly prohibited.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$ auxiliary space.
3. **The Dual-Partition Invariant**:
   - Always binary search over the **smaller** array (swap so that $\text{len}(nums1) \le \text{len}(nums2)$) to ensure $\mathcal{O}(\log(\min(M, N)))$ bounds.
   - Partition both arrays into Left and Right halves such that:
     $$\text{Total Left Count} = \frac{M + N + 1}{2}$$
   - Let partition index in `nums1` be $i$, and partition index in `nums2` be $j = \frac{M + N + 1}{2} - i$.
   - Valid Partition Condition:
     $$\text{nums1}[i - 1] \le \text{nums2}[j] \quad \text{and} \quad \text{nums2}[j - 1] \le \text{nums1}[i]$$
     (Use $-\infty$ and $+\infty$ for edge boundaries).
   - If `nums1[i - 1] > nums2[j]`: $i$ is too large; search left $\implies$ `right = i - 1`.
   - If `nums2[j - 1] > nums1[i]`: $i$ is too small; search right $\implies$ `left = i + 1`.
   - Once valid partition is found:
     - If total length is odd: $\text{Median} = \max(\text{nums1}[i-1], \text{nums2}[j-1])$.
     - If total length is even: $\text{Median} = \frac{\max(\text{nums1}[i-1], \text{nums2}[j-1]) + \min(\text{nums1}[i], \text{nums2}[j])}{2.0}$.
4. **Test Cases**:
   - `nums1 = [1, 3]`, `nums2 = [2]` $\to$ `2.0`
   - `nums1 = [1, 2]`, `nums2 = [3, 4]` $\to$ `2.5`
   - `nums1 = [0, 0]`, `nums2 = [0, 0]` $\to$ `0.0`
   - `nums1 = []`, `nums2 = [1]` $\to$ `1.0`
   - `nums1 = [2]`, `nums2 = []` $\to$ `2.0`
