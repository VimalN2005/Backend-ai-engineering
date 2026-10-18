# Day 25: Practical Hands-On DSA Challenges

Master Binary Heaps, Top-K Filtering, Dual-Heap Median Tracking, and Cooldown Schedulers with these 3 production-level challenges.

---

## Challenge 1: CPU Task Scheduler with Mandatory Cooldown Intervals

### Objective
In distributed worker clusters (e.g. Celery / Kubernetes batch workers), executing identical task types in rapid succession causes lock contention or external rate limit throttling (e.g. hammering a third-party payment API). A cooldown constraint requires that between any two tasks of the same type, there must be at least `n` units of idle time or other intervening tasks.

Given a list of task identifiers `tasks` (represented by characters `A` through `Z`) and a non-negative integer `n`, return the **minimum number of CPU intervals** required to execute all tasks. (Each CPU interval can either execute one task or remain idle).

### Requirements
1. **Time Complexity**: $\mathcal{O}(N)$ where $N$ is total tasks (since alphabet size is bounded to 26).
2. **Space Complexity**: $\mathcal{O}(1)$ auxiliary space (at most 26 frequency entries).
3. **Max-Heap + Cooldown Queue Architecture**:
   - Count frequencies of all tasks.
   - Insert frequencies into a **Max-Heap** (simulated via negative values).
   - In each unit of time:
     - Pop the most frequent available task from the Max-Heap.
     - Decrement its remaining count. If count > 0, place it into a cooldown queue along with its `ready_at_time = current_time + n + 1`.
     - Check the cooldown queue: if any task is ready, re-insert its count back into the Max-Heap.
     - Increment `current_time`.
4. **Test Cases**:
   - `tasks = ["A","A","A","B","B","B"], n = 2` $\to$ `8`
     - Execution order: `A -> B -> idle -> A -> B -> idle -> A -> B`
   - `tasks = ["A","A","A","B","B","B"], n = 0` $\to$ `6`
   - `tasks = ["A","A","A","A","A","A","B","C","D","E","F","G"], n = 2` $\to$ `16`

---

## Challenge 2: Reorganize Stream / No Two Adjacent Identical Elements

### Objective
In distributed round-robin dispatchers and network packet interleavers, consecutive requests to the same partition or database shard must be separated to prevent hotspotting.

Given a string `s`, rearrange the characters of `s` so that any two adjacent characters are not equal. If no valid rearrangement is possible, return an empty string `""`.

### Requirements
1. **Time Complexity**: $\mathcal{O}(N \log M)$ where $N$ is string length and $M$ is unique characters ($M \le 26 \implies \mathcal{O}(N)$).
2. **Space Complexity**: $\mathcal{O}(M)$ auxiliary space.
3. **Pigeonhole Principle Invariant**:
   - If the most frequent character appears more than `(N + 1) // 2` times, it is mathematically impossible to separate them; return `""` immediately.
4. **Dual-Pop Max-Heap Greedy Invariant**:
   - Maintain a Max-Heap of `(frequency, char)` tuples.
   - In each step, pop the **two** most frequent distinct characters.
   - Append both to the result string, decrement their frequencies, and push back any character whose frequency remains $> 0$.
   - If only one character remains with frequency 1, append it to the end.
5. **Test Cases**:
   - `s = "aab"` $\to$ `"aba"`
   - `s = "aaab"` $\to$ `""` (Impossible: 3 'a's out of 4 characters)
   - `s = "vvvlo"` $\to$ `"vlvov"` (or any valid adjacent-distinct permutation)

---

## Challenge 3: Sliding Window Median with Lazy Removal ($\mathcal{O}(N \log K)$ Time)

### Objective
In high-frequency financial market microstructure and real-time network SLA monitoring, tracking the sliding median latency over a moving window of size $K$ across a live stream of $N$ data points is a standard requirement.

Given an array of numbers `nums` and an integer `k`, return an array of median values for each sliding window of size `k` moving from left to right.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N \log K)$. A naive $\mathcal{O}(N \times K \log K)$ approach sorting each window is strictly unacceptable.
2. **Space Complexity**: $\mathcal{O}(K)$ auxiliary memory.
3. **Dual-Heap with Lazy Deletion Invariant**:
   - Maintain `max_heap` (smaller half) and `min_heap` (larger half).
   - Maintain an in-memory hash map `tombstones: Dict[int, int]` tracking elements that have slid out of the window but remain in the heap.
   - Maintain active element counts `max_heap_size` and `min_heap_size` excluding tombstones.
   - Prune dead tombstones from heap roots lazily whenever they bubble to the top.
4. **Test Cases**:
   - `nums = [1,3,-1,-3,5,3,6,7], k = 3`
     - Window 1: `[1, 3, -1]` $\to$ Median: `1.0`
     - Window 2: `[3, -1, -3]` $\to$ Median: `-1.0`
     - Window 3: `[-1, -3, 5]` $\to$ Median: `-1.0`
     - Window 4: `[-3, 5, 3]` $\to$ Median: `3.0`
     - Window 5: `[5, 3, 6]` $\to$ Median: `5.0`
     - Window 6: `[3, 6, 7]` $\to$ Median: `6.0`
     - Result: `[1.0, -1.0, -1.0, 3.0, 5.0, 6.0]`
   - `nums = [1,2,3,4,2,3,1,4,2], k = 3`
     - Result: `[2.0, 3.0, 3.0, 3.0, 2.0, 3.0, 2.0]`
