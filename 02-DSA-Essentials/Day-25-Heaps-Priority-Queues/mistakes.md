# Day 25: 7 Critical Antipatterns in Heaps & Priority Queues

Binary heaps and priority queues are the backbone of event schedulers, top-K streaming analytics, and rate limiters. However, subtle bugs in tuple comparisons, heap sizing, and deletion strategies lead to runtime type errors, quadratic slowdowns, and memory leaks.

Below are 7 critical antipatterns, their low-level runtime consequences, and enterprise remediations.

---

## 1. Missing Tie-Breakers in Python Heap Tuples (`TypeError`)

### The Mistake
Pushing `(priority, task_object)` tuples directly to a heap when the task object is a class, dictionary, or dataclass that does not implement `__lt__`:

```python
# WRONG: Crashes with TypeError whenever two tasks share the same priority!
import heapq

class Task:
    def __init__(self, name):
        self.name = name

heap = []
heapq.heappush(heap, (1, Task("Audit Logs")))
heapq.heappush(heap, (1, Task("Send Email")))  # CRASH! Compares Task with Task!
```

### The Consequence
When priorities collide (both are `1`), Python proceeds to compare the second element of the tuple. Because `Task` does not define `__lt__`, Python immediately raises:
`TypeError: '<' not supported between instances of 'Task' and 'Task'`, crashing the worker thread.

### The Correct Approach
Insert a monotonically incrementing integer counter as an unambiguous tie-breaker:

```python
# CORRECT: Sequence counter guarantees Task is never compared
seq_counter = 0

def add_task(priority: int, task: Task):
    global seq_counter
    heapq.heappush(heap, (priority, seq_counter, task))
    seq_counter += 1
```

---

## 2. Using Full Array Sorting Instead of a Bounded Min-Heap for Top-K

### The Mistake
Sorting an entire dataset or telemetry log just to extract the Top-$K$ outliers:

```python
# WRONG: O(N log N) time and O(N) memory allocation!
def top_k_slow(telemetry: list[int], k: int) -> list[int]:
    return sorted(telemetry, reverse=True)[:k]  # Sorts all 10,000,000 items!
```

### The Consequence
For $N = 10{,}000{,}000$ telemetry metrics and $K = 50$:
- `sorted()` requires $\approx 10^7 \times \log_2(10^7) \approx 230{,}000{,}000$ operations (~2.5 seconds in Python) and allocates a duplicate 10M pointer list in memory.
- A **Bounded Min-Heap of size 50** requires $\approx 10^7 \times \log_2(50) \approx 56{,}000{,}000$ operations (~0.15 seconds) and allocates memory for only 50 elements!

### The Correct Approach
Use a Min-Heap capped at size $K$ using `heappushpop()`:

```python
# CORRECT: O(N log K) time and O(K) space
def top_k_optimal(telemetry: list[int], k: int) -> list[int]:
    heap = telemetry[:k]
    heapq.heapify(heap)
    for x in telemetry[k:]:
        if x > heap[0]:
            heapq.heappushpop(heap, x)
    return sorted(heap, reverse=True)
```

---

## 3. Inverting the Heap Type in Top-K Selection (Max-Heap vs. Min-Heap Confusion)

### The Mistake
Using a Max-Heap of size $K$ when searching for the Top-$K$ **largest** elements:

```python
# FLAWED INTUITION: "I want the largest elements, so I must use a Max-Heap!"
# BUG: The root of a Max-Heap is the GLOBAL MAXIMUM.
# Comparing incoming x against root discards elements smaller than the absolute max,
# completely missing the remaining 2nd, 3rd, ... Kth largest elements!
```

### The Consequence
The root of a Max-Heap is the absolute largest element seen so far. An incoming element that is smaller than the maximum (e.g. 99 vs 100) will be rejected by the root comparison, even if 99 is legitimately the 2nd largest element in the entire stream!

### The Correct Approach
Remember the **Counter-Intuitive Rule**:
- To find the **$K$ Largest Elements**: Use a **Min-Heap** of size $K$. (The root is the smallest of the top-$K$; incoming elements must beat the minimum threshold to enter).
- To find the **$K$ Smallest Elements**: Use a **Max-Heap** of size $K$. (The root is the largest of the bottom-$K$).

---

## 4. Attempting Arbitrary Deletions with `list.remove()`

### The Mistake
Using `heap.remove(task)` to cancel an in-flight task from a priority queue:

```python
# WRONG: O(N) search + breaks heap structural invariant!
def cancel_task_broken(heap: list, task_entry):
    heap.remove(task_entry)  # O(N) linear search through contiguous memory!
    # BUG: heap.remove() breaks the binary heap invariant!
    # Elements are no longer sorted by heap properties!
```

### The Consequence
1. `list.remove()` takes $\mathcal{O}(N)$ linear scan to locate the element and $\mathcal{O}(N)$ pointer shift to close the gap.
2. Removing an internal element corrupts the binary heap shape property. Subsequent `heappop()` calls return incorrect non-minimum elements unless `heapq.heapify(heap)` is invoked again ($\mathcal{O}(N)$).

### The Correct Approach
Use the **Tombstone (Lazy Deletion) Pattern**:
- Mark the task as cancelled in a lookup hash map.
- Leave the entry in the heap and discard it in $\mathcal{O}(\log N)$ when it reaches the root during `heappop()`.

```python
# CORRECT: O(1) cancellation, O(log N) lazy eviction
entry_map[task_id][is_active_idx] = False  # Mark tombstone
```

---

## 5. Calling `heappush()` Followed Immediately by `heappop()`

### The Mistake
Calling separate push and pop functions in a tight stream loop:

```python
# WRONG: Two separate tree traversals and dynamic array resizing
heapq.heappush(heap, item)  # Sifts up from end O(log N)
heapq.heappop(heap)         # Sifts down from root O(log N)
```

### The Consequence
`heappush` appends to the list (potentially triggering an array resize reallocation) and bubbles up from the bottom leaf. `heappop` replaces the root with the last element and bubbles down. This performs two distinct traversals and twice the pointer swaps.

### The Correct Approach
Use `heapq.heappushpop()`:

```python
# CORRECT: Single sift-down pass without array reallocation (~40% faster)
heapq.heappushpop(heap, item)
```

---

## 6. Using $N$ Individual `heappush()` Calls Instead of `heapify()`

### The Mistake
Building a heap from an existing list of $N$ elements by calling `heappush` in a loop:

```python
# WRONG: O(N log N) heap construction
heap = []
for item in raw_items:
    heapq.heappush(heap, item)
```

### The Consequence
Inserting $N$ elements individually takes $\sum_{i=1}^N \log i = \log(N!) \approx \mathcal{O}(N \log N)$ operations. For $N = 1{,}000{,}000$, this executes $\approx 20{,}000{,}000$ operations.

### The Correct Approach
Call `heapq.heapify()` directly on the list:

```python
# CORRECT: Strict O(N) linear-time Floyd construction
heapq.heapify(raw_items)  # In-place, 0 extra memory, linear time
```

---

## 7. Head-of-Line Blocking by Mixing Delayed Tasks in a Single Priority Heap

### The Mistake
Storing both ready tasks and future delayed tasks in a single heap ordered by priority:

```python
# WRONG: Future high-priority tasks block ready lower-priority tasks!
# Heap: [ (Priority: CRITICAL, Execute_At: Tomorrow), (Priority: LOW, Execute_At: Now) ]
```

### The Consequence
Because `CRITICAL` is the smallest priority integer (`0`), the future task sits permanently at the root of the heap. When `pop()` inspects the root and sees `Execute_At > Now`, it returns `None`, causing **Head-Of-Line Blocking**—all lower-priority tasks that are ready to run right now are starved and blocked from executing!

### The Correct Approach
Use a **Dual-Heap Architecture**:
1. Store delayed tasks in a `delayed_heap` ordered by `execute_at`.
2. Store ready tasks in a `ready_heap` ordered by `priority`.
3. When checking for ready work, promote matured tasks from `delayed_heap` into `ready_heap`, then dispatch strictly from `ready_heap`.
