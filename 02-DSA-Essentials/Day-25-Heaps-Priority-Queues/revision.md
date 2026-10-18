# Day 25: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Binary Heaps, Priority Queues, Top-K Filtering, Dual-Heap Medians, and Tombstone Cancellations for backend engineers.

---

## 1. Heap Core Invariants & Index Arithmetic

```
Complete Binary Tree -> Contiguous Dynamic Array (Zero Pointer Overhead)

Parent Index:      parent(i) = (i - 1) // 2
Left Child Index:  left(i)   = 2 * i + 1
Right Child Index: right(i)  = 2 * i + 2
```

- **Min-Heap**: `heap[0]` is the Global Minimum.
- **Max-Heap (Python)**: Invert values via `-val`; `heap[0]` is `-Global Maximum`.

---

## 2. Top-K Selection Rule (The Counter-Intuitive Principle)

| Objective | Heap Type | Heap Size Bound | Ingestion Mechanism |
| :--- | :--- | :--- | :--- |
| **Top-$K$ Largest Elements** | **Min-Heap** | Cap at $K$ | If $x > \text{heap}[0] \implies \text{heappushpop}(\text{heap}, x)$ |
| **Top-$K$ Smallest Elements**| **Max-Heap** | Cap at $K$ | If $x < \text{heap}[0] \implies \text{heappushpop}(\text{heap}, -x)$ |
| **Running Median (P50)** | **Dual-Heaps** | Balanced ($\le 1$ diff) | Max-Heap (smaller half) + Min-Heap (larger half) |

---

## 3. High-Yield Code Templates

### Template A: Top-K Largest Elements ($\mathcal{O}(N \log K)$ Time, $\mathcal{O}(K)$ Space)
```python
import heapq

def top_k_largest(nums: list[int], k: int) -> list[int]:
    min_heap = nums[:k]
    heapq.heapify(min_heap)  # O(K) linear heapify
    for x in nums[k:]:
        if x > min_heap[0]:
            heapq.heappushpop(min_heap, x)
    return sorted(min_heap, reverse=True)
```

### Template B: Dual-Heap Running Median Finder
```python
import heapq

class MedianFinder:
    def __init__(self):
        self.max_h = []  # Smaller half (negated)
        self.min_h = []  # Larger half (normal)

    def add_num(self, num: float):
        heapq.heappush(self.max_h, -num)
        if self.min_h and (-self.max_h[0] > self.min_h[0]):
            heapq.heappush(self.min_h, -heapq.heappop(self.max_h))
        if len(self.max_h) < len(self.min_h):
            heapq.heappush(self.max_h, -heapq.heappop(self.min_h))
        elif len(self.max_h) > len(self.min_h) + 1:
            heapq.heappush(self.min_h, -heapq.heappop(self.max_h))

    def find_median(self) -> float:
        if len(self.max_h) > len(self.min_h):
            return -self.max_h[0]
        return (-self.max_h[0] + self.min_h[0]) / 2.0
```

### Template C: Priority Queue with Tombstone Cancellation ($\mathcal{O}(\log N)$)
```python
class PriorityScheduler:
    def __init__(self):
        self.heap = []
        self.entry_map = {}
        self.counter = 0

    def push(self, task_id: str, priority: int, task: Any):
        if task_id in self.entry_map:
            self.cancel(task_id)
        entry = [priority, self.counter, task_id, task, False]
        self.counter += 1
        self.entry_map[task_id] = entry
        heapq.heappush(self.heap, entry)

    def cancel(self, task_id: str) -> bool:
        if task_id in self.entry_map:
            entry = self.entry_map.pop(task_id)
            entry[4] = True  # Mark tombstone
            return True
        return False

    def pop(self) -> Optional[Any]:
        while self.heap:
            priority, _, task_id, task, is_cancelled = heapq.heappop(self.heap)
            if not is_cancelled:
                self.entry_map.pop(task_id, None)
                return task
        return None
```

---

## 4. 5 Non-Negotiable Engineering Rules

1. **Always Use a Tie-Breaker Counter**: Store `(priority, seq_id, task)` to prevent `TypeError` when priorities collide.
2. **Never Call `heap.remove()`**: Deleting arbitrary elements is $\mathcal{O}(N)$ and breaks heap invariants. Use the Tombstone pattern.
3. **Prefer `heappushpop()` over Push-then-Pop**: Cuts execution time by ~40% by performing a single sift-down pass.
4. **Use `heapify()` for Batch Initialization**: Builds a heap in linear $\mathcal{O}(N)$ time via Floyd's algorithm (vs $\mathcal{O}(N \log N)$ for iterative pushes).
5. **Separate Delayed from Ready Tasks**: Mixing future tasks into a priority heap causes head-of-line blocking. Maintain a dual-heap architecture.

---

## 5. Complexity Quick Reference Table

| Operation / Pattern | Time Complexity | Auxiliary Space | Key Mechanism |
| :--- | :--- | :--- | :--- |
| **`heapify(arr)`** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Floyd's bottom-up sift-down |
| **`heappush` / `heappop`** | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | C-level `_siftdown` / `_siftup` |
| **`heappushpop`** | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ | Single downward sift pass |
| **Top-$K$ Elements** | $\mathcal{O}(N \log K)$ | $\mathcal{O}(K)$ | Bounded Min-Heap of size $K$ |
| **Stream Median** | $\mathcal{O}(1)$ read, $\mathcal{O}(\log N)$ write | $\mathcal{O}(N)$ | Balanced Dual-Heaps |
| **K-Way Stream Merge** | $\mathcal{O}(N \log K)$ | $\mathcal{O}(K)$ | Multi-way Min-Heap |
