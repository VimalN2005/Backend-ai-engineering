# Day 21: Linked Lists: Fast & Slow Pointers, In-Place Reversal & Low-Level Memory Mechanics

In modern backend and AI systems, dynamic arrays (`list` in Python, `std::vector` in C++) dominate cache-conscious sequential workloads. However, when constant-time $\mathcal{O}(1)$ insertions, deletions, and structural reorganizations are required without triggering expensive $\mathcal{O}(N)$ contiguous memory reallocations, **Linked Lists** remain foundational.

From the **Linux Kernel runqueue** (`struct list_head`) and **Redis quicklists/ziplists** to **LRU cache eviction policies** and **lock-free concurrent task queues**, linked data structures power low-latency infrastructure.

Mastering linked lists requires deep fluency with pointer manipulation, cycle detection invariants, in-place reordering, and the memory layout trade-offs of heap-allocated nodes.

---

## 1. Linked Lists in Backend Architecture & Low-Level Systems

```
Array / Dynamic List: Contiguous Memory (High Cache Locality, O(N) Deletions)
┌──────────────┬──────────────┬──────────────┬──────────────┐
│  Pointer 0   │  Pointer 1   │  Pointer 2   │  Pointer 3   │  [0x1000 - 0x1020]
└──────────────┴──────────────┴──────────────┴──────────────┘
Fast index-based access O(1), but deleting index 0 forces C memmove() of all elements.

Singly Linked List: Non-Contiguous Heap Nodes (Pointers Link Memory Addresses)
┌──────────┬──────┐      ┌──────────┬──────┐      ┌──────────┬──────┐
│  Data A  │ 0x4B ├─────►│  Data B  │ 0x8F ├─────►│  Data C  │ NULL │
└──────────┴──────┘      └──────────┴──────┘      └──────────┴──────┘
[Heap: 0x12A0]           [Heap: 0x4B00]           [Heap: 0x8F40]
Constant-time insertion/splicing O(1), but pointer traversal incurs CPU L1/L2 cache misses.
```

### Why Linked Lists in Infrastructure?
1. **Predictable $\mathcal{O}(1)$ Splice/Erase**: Inserting or removing a node requires updating two pointers ($A \to C$). In an array, inserting at the front requires shifting millions of elements.
2. **LRU Cache Eviction Queues**: Combining a Hash Map with a Doubly Linked List gives $\mathcal{O}(1)$ key lookups and $\mathcal{O}(1)$ node promotion to the head.
3. **Operating System Schedulers**: The Linux Completely Fair Scheduler (CFS) and task runqueues link process descriptors using embedded circular doubly linked lists (`list_head`).
4. **Lock-Free Concurrency**: Atomic Compare-And-Swap (CAS) instructions can swing a single `head` or `next` pointer atomically, building thread-safe lock-free queues without mutex contention.

---

## 2. CPython Memory Model & Pointer Overhead

In CPython, everything is a `PyObject`. A naive Python class representing a node carries massive hidden memory bloat:

```python
# NAIVE NODE: Carries __dict__ and __weakref__ overhead (~152 bytes in 64-bit Python!)
class BadNode:
    def __init__(self, val):
        self.val = val
        self.next = None
```

```
PyObject Header (16 bytes: ob_refcnt + ob_type)
+ Attribute Dict Pointer (__dict__: 8 bytes)
+ Dict Table Allocation (~96+ bytes)
+ Attribute references (val: 8 bytes, next: 8 bytes)
Total Memory Per Node: ~152 bytes!
```

### The Production Optimization: `__slots__`
By declaring `__slots__ = ("val", "next")`, we instruct CPython to eliminate the instance `__dict__` and allocate a compact C struct:

```python
class Node:
    __slots__ = ("val", "next")  # Drops memory per node from ~152 bytes to ~48 bytes!
    def __init__(self, val, next=None):
        self.val = val
        self.next = next
```

For a linked list of 1,000,000 elements:
- Without `__slots__`: ~152 MB RAM.
- With `__slots__`: ~48 MB RAM (a **~68% memory reduction**).

---

## 3. Structural Variations & The Sentinel Pattern

```
1. Singly Linked List:
   [Head] ──► [Node A] ──► [Node B] ──► [Node C] ──► None

2. Doubly Linked List:
   None ◄── [Head] ◄────► [Node A] ◄────► [Node B] ◄────► [Tail] ──► None

3. Circular Linked List:
   ┌──► [Node A] ──► [Node B] ──► [Node C] ──┐
   └─────────────────────────────────────────┘
```

### The Sentinel (Dummy Head) Pattern
A **Sentinel Node** is a dummy node positioned before the head of a list. It eliminates branch conditions for head updates:

```python
# WITHOUT SENTINEL: Messy conditional branches for empty list / head mutation
if head is None:
    head = new_node
elif head.val > new_node.val:
    new_node.next = head
    head = new_node
else:
    # Traversal loop...

# WITH SENTINEL: Uniform pointer manipulation across all nodes!
dummy = Node(0, head)
curr = dummy
while curr.next and curr.next.val < new_node.val:
    curr = curr.next
new_node.next = curr.next
curr.next = new_node
return dummy.next  # New head cleanly retrieved
```

---

## 4. The Fast & Slow Pointer Pattern (Floyd's Tortoise and Hare)

The Fast & Slow pointer pattern uses two pointers moving through the list at different velocities:
- `slow`: Advances **1 step** per iteration.
- `fast`: Advances **2 steps** per iteration.

### Primitive A: Finding the Exact Middle of a Linked List
```python
def find_middle(head: Optional[Node]) -> Optional[Node]:
    slow = head
    fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
    return slow  # For [1, 2, 3, 4, 5], returns 3. For [1, 2, 3, 4], returns 3 (second middle).
```

### Primitive B: Detecting Cycles in $\mathcal{O}(N)$ Time & $\mathcal{O}(1)$ Space
```python
def has_cycle(head: Optional[Node]) -> bool:
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow is fast:
            return True
    return False
```

---

## 5. Mathematical Proof of Cycle Entry Detection

How do we find the **exact start node** of a cycle?

```
    L (Non-cycle prefix)              Cycle of length C
[Head] ──────► ... ──────► [Cycle Entry] ──────► ... ──────► [Meeting Point]
                                ▲                                  │
                                └──────────────────────────────────┘
                                          C - k (Remainder)
```

### The Derivation:
1. Let $L$ = distance from `head` to the cycle entry node.
2. Let $C$ = circumference (length) of the cycle.
3. Let $k$ = distance from cycle entry to the meeting point of `slow` and `fast`.
4. At the meeting point:
   - Distance traveled by `slow`: $D_{\text{slow}} = L + k$
   - Distance traveled by `fast`: $D_{\text{fast}} = L + nC + k$ (where $n \ge 1$ is the number of full cycle loops).
5. Since `fast` travels twice as fast as `slow`:
   $$D_{\text{fast}} = 2 \cdot D_{\text{slow}}$$
   $$L + nC + k = 2(L + k) = 2L + 2k$$
   $$L = nC - k = (n - 1)C + (C - k)$$
6. **The Breakthrough Intuition**:
   - $L$ is exactly equal to $(n - 1)$ full cycle loops plus $(C - k)$.
   - $(C - k)$ is the remaining distance from the **Meeting Point** to the **Cycle Entry**!
7. **The Algorithm**:
   - Keep `fast` at the meeting point.
   - Reset `slow` to `head`.
   - Advance BOTH pointers by **1 step at a time**.
   - They will collide at the **Cycle Entry Node** after exactly $L$ steps!

```python
def detect_cycle_entry(head: Optional[Node]) -> Optional[Node]:
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow is fast:
            # Phase 2: Reset slow to head, advance both by 1 step
            slow = head
            while slow is not fast:
                slow = slow.next
                fast = fast.next
            return slow  # Exact cycle entry node!
    return None
```

---

## 6. In-Place Linked List Reversal

Reversing a linked list in-place requires redirecting pointer arrows using three pointers: `prev`, `curr`, and `next_node`.

```
Initial State:
None ◄── [prev]       [curr] ──► [Node B] ──► [Node C]
                        │
                        ▼
Save next:          next_node = curr.next
Redirect arrow:     curr.next = prev
Advance prev:       prev = curr
Advance curr:       curr = next_node

Final State:
None ◄── [Node A] ◄── [Node B] ◄── [Node C] ◄── [prev]       [curr = None]
```

### Iterative Implementation ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Space):
```python
def reverse_list(head: Optional[Node]) -> Optional[Node]:
    prev = None
    curr = head
    while curr:
        next_node = curr.next
        curr.next = prev
        prev = curr
        curr = next_node
    return prev  # New head
```

---

## 7. Palindrome Linked List Verification

Verifying whether a linked list is a palindrome in $\mathcal{O}(N)$ time and $\mathcal{O}(1)$ auxiliary space requires orchestrating three primitives:

1. **Find Middle**: Use Fast & Slow pointers to locate the center.
2. **Reverse Second Half**: Invert the pointer arrows of the right half in-place.
3. **Compare**: Compare values from the head and the reversed half.
4. **Restore (Crucial)**: Reverse the second half back to preserve data integrity for downstream consumers.

```python
def is_palindrome(head: Optional[Node]) -> bool:
    if not head or not head.next:
        return True

    # 1. Find end of first half
    slow = fast = head
    while fast.next and fast.next.next:
        slow = slow.next
        fast = fast.next.next

    # 2. Reverse second half
    second_half_start = reverse_list(slow.next)

    # 3. Compare values
    first = head
    second = second_half_start
    is_valid = True
    while is_valid and second:
        if first.val != second.val:
            is_valid = False
        first = first.next
        second = second.next

    # 4. Restore original list structure
    slow.next = reverse_list(second_half_start)
    return is_valid
```

---

## 8. Reorder List (Fold List Pattern: $L_0 \to L_n \to L_1 \to L_{n-1} \dots$)

The fold list transformation requires interleaving nodes from the start and end of the list:

```
Input:   1 -> 2 -> 3 -> 4 -> 5
Split:   [1 -> 2 -> 3]   and   [4 -> 5]
Reverse: [1 -> 2 -> 3]   and   [5 -> 4]
Merge:   1 -> 5 -> 2 -> 4 -> 3
```

```python
def reorder_list(head: Optional[Node]) -> None:
    if not head or not head.next:
        return

    # Step 1: Find middle (split into two halves)
    slow, fast = head, head
    while fast.next and fast.next.next:
        slow = slow.next
        fast = fast.next.next
    
    second_half = slow.next
    slow.next = None  # Sever connection between halves

    # Step 2: Reverse second half
    second_half = reverse_list(second_half)

    # Step 3: Zig-zag interleave
    first = head
    second = second_half
    while second:
        tmp1, tmp2 = first.next, second.next
        first.next = second
        second.next = tmp1
        first, second = tmp1, tmp2
```

---

## 9. Merging Sorted Lists: Two-Way & K-Way

### Two-Way Merge ($\mathcal{O}(N + M)$ Time, $\mathcal{O}(1)$ Space):
```python
def merge_two_sorted(l1: Optional[Node], l2: Optional[Node]) -> Optional[Node]:
    dummy = Node(0)
    tail = dummy
    while l1 and l2:
        if l1.val <= l2.val:
            tail.next = l1
            l1 = l1.next
        else:
            tail.next = l2
            l2 = l2.next
        tail = tail.next
    tail.next = l1 if l1 else l2
    return dummy.next
```

### K-Way Merge ($\mathcal{O}(N \log K)$ Time using Min-Heap):
In distributed database query engines and log aggregators, $K$ sorted streams must be merged. Using a Min-Heap of size $K$ achieves $\mathcal{O}(N \log K)$ runtime.

---

## 10. Removing the $N$-th Node From End in One Pass

To delete the $N$-th node from the end without knowing the list length:
1. Advance a `leader` pointer $N + 1$ steps ahead.
2. Advance `leader` and `follower` simultaneously until `leader` hits `None`.
3. `follower.next = follower.next.next` deletes the target node.

```python
def remove_nth_from_end(head: Optional[Node], n: int) -> Optional[Node]:
    dummy = Node(0, head)
    leader = follower = dummy

    # Advance leader n + 1 steps
    for _ in range(n + 1):
        leader = leader.next

    while leader:
        leader = leader.next
        follower = follower.next

    follower.next = follower.next.next
    return dummy.next
```

---

## 11. Production LRU Cache: Hash Map + Doubly Linked List

```
                       DOUBLY LINKED LIST
  ┌───────────┐      ┌───────────┐      ┌───────────┐      ┌───────────┐
  │ DummyHead ├─────►│  Node A   ├─────►│  Node B   ├─────►│ DummyTail │
  │           │◄─────┤ (Most Rec)│◄─────┤ (Least Rec│◄─────┤           │
  └───────────┘      └───────────┘      └───────────┘      └───────────┘
        ▲                  ▲                  ▲
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                HASH MAP { key: NodePtr }
               (O(1) Direct Memory Pointer)
```

Why arrays fail for LRU caches:
- Accessing a cached item requires moving it to the front. In an array, moving an item from index $K$ to index $0$ takes $\mathcal{O}(N)$ pointer copies.
- In a Doubly Linked List, unlinking and re-inserting takes 4 pointer updates in **strict $\mathcal{O}(1)$ time**.

---

## 12. Lock-Free Linked Lists & Concurrent Architectures

In multi-threaded backend services, traditional mutex locks cause thread contention and context-switching overhead.

A **Treiber Stack** or **Michael-Scott Lock-Free Queue** uses atomic hardware instructions:
- **CAS (Compare-And-Swap)**: `atomic_compare_exchange_weak(target, expected, new_value)`.
- If another thread modified the pointer in the interim, CAS fails and retries in a user-space spin-loop without sleeping the kernel thread.

---

## 13. CPython `collections.deque` Internal Architecture

Python's `collections.deque` does not use standard dynamic arrays or pure singly linked nodes. It is implemented as a **Doubly Linked Block List**:
- Each node is a fixed-size C array of 64 object pointers.
- Provides $\mathcal{O}(1)$ push/pop at both boundaries with zero contiguous array reallocations, while maintaining the L1 cache locality of contiguous blocks.

---

## 14. 7 Golden Invariant Rules for Linked List Engineering

1. **Always Use Sentinel Nodes**: Prepend `dummy = Node(0, head)` to eliminate edge-case conditionals for head insertions and deletions.
2. **Save `curr.next` Before Overwriting**: Pointer updates destroy downstream references. Always cache `next_node = curr.next` before mutating.
3. **Guard Double Dereferencing**: Always write `while fast and fast.next:` before accessing `fast.next.next` to prevent `AttributeError: 'NoneType' object has no attribute 'next'`.
4. **Sever Sublist Connections**: When splitting lists for merge sort or fold reordering, explicitly assign `slow.next = None` to avoid creating infinite loops during merges.
5. **Restore Modified Lists**: If a read method (like `is_palindrome`) temporarily mutates pointers, always restore the original layout before returning.
6. **Use `__slots__` on Node Classes**: Eliminate instance dictionary bloat and save 68% of heap memory in high-volume node allocation.
7. **Watch Out for Reference Cycles**: Circular doubly linked lists prevent CPython's reference counting from freeing memory immediately. Rely on weakrefs or break cycles explicitly during teardown.

---

## 15. Complexity Reference Matrix

| Algorithm / Operation | Time Complexity | Auxiliary Space | Key Technique |
| :--- | :--- | :--- | :--- |
| **Middle of List** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Fast & Slow Pointers (1-step & 2-step) |
| **Cycle Detection** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Floyd's Tortoise and Hare |
| **Cycle Entry Node** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Floyd's Equation ($L = (n-1)C + (C-k)$) |
| **In-Place Reversal** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Three-pointer swapping (`prev`, `curr`, `next`) |
| **Palindrome Verification** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Middle $\to$ Reverse $\to$ Compare $\to$ Restore |
| **Reorder / Fold List** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Split $\to$ Reverse $\to$ Zig-zag Merge |
| **Remove N-th From End** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Lead-Follower Two Pointers (N-step lead) |
| **Merge Two Sorted Lists** | $\mathcal{O}(N + M)$ | $\mathcal{O}(1)$ | Dummy Head + Tail Pointer |
| **Merge K Sorted Lists** | $\mathcal{O}(N \log K)$ | $\mathcal{O}(K)$ | Min-Heap Priority Queue |
| **LRU Cache (Get / Put)** | $\mathcal{O}(1)$ | $\mathcal{O}(\text{Capacity})$ | Hash Map + Doubly Linked List |
