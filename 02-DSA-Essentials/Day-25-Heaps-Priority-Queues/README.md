# Day 25: Heaps & Priority Queues: Top-K Elements & Task Scheduling

In high-throughput backend services and AI systems, requests and events rarely arrive in the exact order they should be processed. A database must execute high-priority transactions before background maintenance jobs; an API rate limiter must prioritize tier-1 customers; an LLM serving engine must sample the Top-$K$ token probabilities; and an LSM-tree storage engine (RocksDB, Cassandra) must merge $K$ sorted disk segments during compaction.

A naive sorted array requires $\mathcal{O}(N)$ time to insert new items; an unsorted array requires $\mathcal{O}(N)$ time to find the minimum/maximum. **Binary Heaps** and **Priority Queues** provide the optimal trade-off: **strict $\mathcal{O}(\log N)$ insertions and deletions** with **guaranteed $\mathcal{O}(1)$ peak inspection**.

---

## 1. Heaps in Backend Infrastructure & AI Systems

```
1. Distributed Event Schedulers (Celery / RabbitMQ / Temporal):
   Tasks are assigned dynamic priorities and scheduled execution timestamps.
   A Min-Heap sorted by execution_timestamp fires ready tasks in O(1) time.

2. Heavy Hitters & Top-K Streaming Telemetry (Datadog / Prometheus):
   Tracking the Top-100 highest-latency API endpoints across 10,000,000 requests.
   Instead of sorting all 10,000,000 entries (O(N log N)), a bounded Min-Heap of size 100
   maintains the exact Top-K in O(N log K) time with tiny constant memory!

3. Database Storage Engines (LSM-Tree SSTable Compaction):
   Merging K sorted SSTable files on disk into a single sequential segment using
   a K-way Min-Heap merge in O(N log K) time.
```

---

## 2. Binary Heap Invariants & Zero-Pointer Array Representation

A **Binary Heap** is a Complete Binary Tree represented as a **contiguous dynamic array**. It eliminates all child and parent pointers, achieving exceptional CPU L1/L2 cache locality:

```
               [ 1 ]
             /       \
          [ 3 ]     [ 6 ]
         /    \     /
       [ 5 ]  [ 9 ][ 8 ]

ARRAY REPRESENTATION:
Index:    0    1    2    3    4    5
Values: [ 1,   3,   6,   5,   9,   8 ]
```

### The Index Arithmetic Invariants:
For any node at index $i$:
- **Parent Index**: $\text{parent}(i) = \lfloor (i - 1) / 2 \rfloor \implies \text{`(i - 1) // 2`}$
- **Left Child Index**: $\text{left}(i) = 2i + 1$
- **Right Child Index**: $\text{right}(i) = 2i + 2$

Because the tree is strictly complete (filled left-to-right at all levels), there are **zero gaps** in the underlying array.

---

## 3. Min-Heap vs. Max-Heap

- **Min-Heap Invariant**: Every parent node is smaller than or equal to its children:
  $$A[\text{parent}(i)] \le A[i] \quad \implies \text{Root } A[0] \text{ is the global minimum}$$
- **Max-Heap Invariant**: Every parent node is greater than or equal to its children:
  $$A[\text{parent}(i)] \ge A[i] \quad \implies \text{Root } A[0] \text{ is the global maximum}$$

### Python's `heapq` Design:
Python's standard library `heapq` implements **strictly Min-Heaps**.
To simulate a Max-Heap in Python, negate the numerical keys:
$$\text{Max-Heap Key} = -x$$

---

## 4. CPython `heapq` Internals & Operation Primitives

In CPython, `heapq` is implemented in C (`Modules/_heapqmodule.c`) for maximum execution speed:

| Function | CPython Mechanism | Time Complexity | Auxiliary Space |
| :--- | :--- | :--- | :--- |
| `heappush(heap, item)` | Appends to array end, runs `_siftdown()` (bubble-up) | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| `heappop(heap)` | Swaps root with last element, pops last, runs `_siftup()` | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| `heapify(array)` | In-place Floyd's bottom-up heap construction | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ |
| `heappushpop(h, item)` | Compares item with root; replaces root if larger | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| `heapreplace(h, item)` | Pops root and inserts item without comparison | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |

> [!TIP]
> **Performance Optimization**: `heappushpop(heap, item)` is significantly faster than calling `heappush()` followed by `heappop()`. It performs a single sift-down pass instead of two separate traversals and avoids reallocating list memory.

---

## 5. The $\mathcal{O}(N)$ Linear Heapify Proof (Floyd's Algorithm)

Engineers often mistakenly assume `heapify(arr)` takes $\mathcal{O}(N \log N)$ because it processes $N$ elements:

```python
# WHY IS THIS O(N) AND NOT O(N log N)?
heapq.heapify(arr)
```

### The Mathematical Proof:
1. In a tree of $N$ nodes, there are $\frac{N}{2}$ leaves (height $0$), $\frac{N}{4}$ nodes at height $1$, $\frac{N}{8}$ nodes at height $2$, and generally $\frac{N}{2^{h+1}}$ nodes at height $h$.
2. Floyd's algorithm starts from the lowest internal nodes and calls `_siftup()` downwards.
3. The total work performed across all nodes is:
   $$W(N) = \sum_{h=0}^{\lfloor \log N \rfloor} \frac{N}{2^{h+1}} \times \mathcal{O}(h) = \frac{N}{2} \sum_{h=0}^{\infty} \frac{h}{2^h}$$
4. The infinite series $\sum_{h=0}^{\infty} \frac{h}{2^h}$ converges to exactly $2$:
   $$W(N) = \frac{N}{2} \times 2 = \mathcal{O}(N)$$
5. **Conclusion**: Most nodes in a tree reside at the bottom where height $h$ is very small. The leaves require 0 swaps! Building a heap via `heapify()` is strictly linear $\mathcal{O}(N)$.

---

## 6. The Top-K Elements Pattern ($\mathcal{O}(N \log K)$ Time, $\mathcal{O}(K)$ Space)

To find the $K$ largest elements in a stream of $N$ numbers:

```
Common Anti-pattern:
Sort entire array O(N log N), slice arr[-K:] -> Massive memory and CPU waste!

Production Top-K Pattern:
Maintain a MIN-HEAP of size K!
1. For each number x in stream:
2.   If len(heap) < K:
3.       heappush(heap, x)
4.   Elif x > heap[0]:
5.       heappushpop(heap, x)  # Evicts the smallest of the top-K!
```

```
Stream: [ 10, 2, 8, 15, 20, 1 ], K = 3
Target: 3 Largest Elements

Step 1 (First 3): Heap = [ 2, 8, 10 ]  (Root 2 is current threshold)
Step 2 (Item 15): 15 > 2 -> heappushpop -> Heap = [ 8, 15, 10 ]
Step 3 (Item 20): 20 > 8 -> heappushpop -> Heap = [ 10, 15, 20 ]
Step 4 (Item 1) : 1 < 10 -> Ignore!
Final Heap: [ 10, 15, 20 ] (Exact 3 largest elements!)
```

- **Time Complexity**: $\mathcal{O}(N \log K)$ — when $K \ll N$ (e.g. Top 100 out of 10,000,000), $\log K \approx 7$, requiring < 0.1s in Python!
- **Space Complexity**: Strictly $\mathcal{O}(K)$ memory.

---

## 7. Dual-Heap Architecture: Continuous Median from Data Stream

Finding the median of an unbounded incoming stream in real-time requires balancing two heaps:

```
        SMALLER HALF (Max-Heap)              LARGER HALF (Min-Heap)
   Holds numbers <= Median              Holds numbers >= Median
   Root = Largest of Smaller Half       Root = Smallest of Larger Half
       ┌─────────────────┐                  ┌─────────────────┐
       │ Top:   5        │                  │ Top:   8        │
       │        4, 2, 1  │                  │        9, 12    │
       └─────────────────┘                  └─────────────────┘
```

### The Invariants:
1. `max_heap` size is either equal to `min_heap` size, or exactly 1 element larger:
   $$0 \le \text{len}(\text{max\_heap}) - \text{len}(\text{min\_heap}) \le 1$$
2. Every element in `max_heap` $\le$ every element in `min_heap`.
3. **Median Retrieval in $\mathcal{O}(1)$**:
   - If odd total elements: $\text{Median} = \text{max\_heap.top}$
   - If even total elements: $\text{Median} = \frac{\text{max\_heap.top} + \text{min\_heap.top}}{2.0}$

---

## 8. K-Way Stream Merge via Min-Heap ($\mathcal{O}(N \log K)$)

In distributed database log aggregators and LSM-tree compactions, $K$ sorted streams must be merged into a single sequential output:

```python
import heapq

def merge_k_sorted_streams(streams: list[list[int]]) -> list[int]:
    heap = []
    # Initialize heap with first element of each stream: (val, stream_id, next_idx)
    for i, s in enumerate(streams):
        if s:
            heap.append((s[0], i, 1))

    heapq.heapify(heap)
    result = []

    while heap:
        val, stream_idx, next_idx = heapq.heappop(heap)
        result.append(val)

        # If stream has more elements, push next
        if next_idx < len(streams[stream_idx]):
            next_val = streams[stream_idx][next_idx]
            heapq.heappush(heap, (next_val, stream_idx, next_idx + 1))

    return result
```

---

## 9. Priority Queue with Dynamic Updates: The Tombstone Pattern

In real-world priority schedulers, pending tasks must frequently be cancelled or reprioritized. However, deleting an arbitrary item from a binary heap takes $\mathcal{O}(N)$ time.

### The Production Solution: Lazy Deletion (Tombstones)
1. Maintain an internal hash map `entry_finder: Dict[task_id, list]`.
2. Each heap entry is a mutable list: `entry = [priority, task_id, is_active]`.
3. When cancelling or reprioritizing a task:
   - Mark `entry[2] = False` (the Tombstone).
   - If reprioritizing, push a new entry with the updated priority.
4. When `heappop()` retrieves an entry:
   - Check if `is_active is False`. If so, discard it and pop again!
- **Amortized Time Complexity**: $\mathcal{O}(\log N)$ cancellation and reprioritization.

---

## 10. Tie-Breaking Mechanics in Python Tuples

When storing `(priority, task)` tuples in a heap, Python compares tuples index-by-index:
```python
# BUG: If priorities are equal, Python compares Task!
# Raises TypeError: '<' not supported between instances of 'Task' and 'Task'!
heapq.heappush(heap, (1, task_a))
heapq.heappush(heap, (1, task_b))
```

### The Production Solution: Monotonic Sequence Counter
Insert a unique monotonically incrementing integer counter as a tie-breaker:
```python
entry = (priority, sequence_counter, task)
```
Because `sequence_counter` is guaranteed unique, Python will **never** compare `task`, ensuring deterministic FIFO execution among equal priorities.

---

## 11. 7 Golden Invariant Rules for Heap Engineering

1. **Use Min-Heap of Size $K$ for Largest Elements**: Preserves top items while discarding smaller values in $\mathcal{O}(\log K)$.
2. **Always Use a Tie-Breaker Counter**: Store `(priority, seq_id, payload)` to prevent `TypeError` when priorities collide.
3. **Prefer `heappushpop()`**: Replaces two $\mathcal{O}(\log N)$ passes with a single pass, saving ~40% execution time.
4. **Use `heapify()` for Batch Data**: Transforms raw lists into heaps in linear $\mathcal{O}(N)$ time instead of $\mathcal{O}(N \log N)$.
5. **Use Tombstones for Cancellations**: Avoid $\mathcal{O}(N)$ arbitrary removals; mark items as cancelled and purge them lazily upon pop.
6. **Negate Keys for Max-Heaps**: In Python, insert `-priority` to simulate a Max-Heap.
7. **Bound Streaming Heap Memory**: In real-time sliding windows, evict stale elements to prevent unbounded heap memory leaks.

---

## 12. Complexity Reference Matrix

| Algorithm / Operation | Time Complexity (Best) | Time Complexity (Worst) | Auxiliary Space | Key Technique |
| :--- | :--- | :--- | :--- | :--- |
| **`heapify(arr)`** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ (In-place) | Floyd's bottom-up sifting |
| **`heappush` / `heappop`** | $\mathcal{O}(1)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | Bubble-up / Sift-down |
| **Top-K Elements** | $\mathcal{O}(N)$ | $\mathcal{O}(N \log K)$ | $\mathcal{O}(K)$ | Bounded Min-Heap of size $K$ |
| **Median from Stream** | $\mathcal{O}(1)$ read | $\mathcal{O}(\log N)$ insert | $\mathcal{O}(N)$ | Balanced Dual Heaps |
| **K-Way Merge** | $\mathcal{O}(N)$ | $\mathcal{O}(N \log K)$ | $\mathcal{O}(K)$ | Min-Heap of size $K$ |
| **Task Scheduler (Cooldown)**| $\mathcal{O}(N)$ | $\mathcal{O}(N \log M)$ | $\mathcal{O}(M)$ | Max-Heap + Cooldown Deque |
