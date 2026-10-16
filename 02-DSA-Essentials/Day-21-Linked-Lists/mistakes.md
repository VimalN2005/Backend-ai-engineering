# Day 21: 7 Critical Antipatterns in Linked List Engineering

Linked lists appear conceptually straightforward, but in production systems, subtle pointer errors trigger catastrophic memory leaks, segmentation faults, infinite loops, and microservice crashes.

Below are 7 critical antipatterns, their low-level runtime consequences, and enterprise remediations.

---

## 1. Lost Pointer Reference During In-Place Mutation

### The Mistake
Overwriting `curr.next` before caching the reference to the remainder of the list:

```python
# WRONG: Loses access to the entire rest of the list!
def reverse_broken(head: Optional[ListNode]) -> Optional[ListNode]:
    prev = None
    curr = head
    while curr:
        curr.next = prev       # The original curr.next is now permanently ERASED!
        curr = curr.next       # BUG: curr.next is now prev! Infinite loop or immediate exit!
        prev = curr
    return prev
```

### The Consequence
Because Python references are overwritten in place, reassigning `curr.next = prev` severs the only pointer to the downstream nodes. All remaining nodes in the list become unreferenced and orphaned in memory, terminating traversal prematurely or trapping execution in an immediate two-node cycle.

### The Correct Approach
Always cache the forward reference in a temporary variable (`next_node = curr.next`) *before* modifying any pointer:

```python
# CORRECT: Cache forward reference before pointer redirect
def reverse_correct(head: Optional[ListNode]) -> Optional[ListNode]:
    prev = None
    curr = head
    while curr:
        next_node = curr.next  # 1. Safely store downstream pointer
        curr.next = prev       # 2. Redirect arrow backwards
        prev = curr            # 3. Step prev forward
        curr = next_node       # 4. Step curr forward
    return prev
```

---

## 2. Unchecked Double Dereferencing in Fast & Slow Pointers

### The Mistake
Advancing `fast` by two steps without verifying that both `fast` and `fast.next` are non-null:

```python
# WRONG: Crashes on even-length lists or empty lists!
def find_middle_broken(head: Optional[ListNode]) -> Optional[ListNode]:
    slow = fast = head
    while fast.next:  # BUG: What if fast is None, or fast.next is the last node?
        slow = slow.next
        fast = fast.next.next  # If fast.next is None, this raises AttributeError!
    return slow
```

### The Consequence
For an even-length list (e.g. `[1, 2]`), in the first iteration `fast` starts at node 1. `fast.next` is node 2. Then `fast.next.next` is `None`. In the next loop evaluation, if the condition checks `fast.next`, Python attempts `None.next`, crashing the service with `AttributeError: 'NoneType' object has no attribute 'next'`.

### The Correct Approach
Always enforce the dual guard `while fast and fast.next:`. Python's short-circuit boolean evaluation guarantees that `fast.next` is only evaluated if `fast` is non-null:

```python
# CORRECT: Dual guard ensures safe double dereferencing
def find_middle_correct(head: Optional[ListNode]) -> Optional[ListNode]:
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
    return slow
```

---

## 3. Omitting `__slots__` in High-Throughput Node Allocation

### The Mistake
Using standard Python classes with dynamic dictionaries (`__dict__`) for millions of node instances:

```python
# WRONG: Massive memory bloat (344 bytes per node in Python 3.12+ / 3.14)
class NaiveNode:
    def __init__(self, val, next=None):
        self.val = val
        self.next = next
```

### The Consequence
Each `NaiveNode` instance allocates an internal `PyDictObject` to support dynamic attribute assignment. In an in-memory buffer, cache, or graph holding 1,000,000 nodes, unoptimized instances consume ~34.4 MB of memory compared to only ~4.8 MB for `__slots__` instances—a **~86% wasted RAM penalty** that triggers frequent garbage collection pauses.

### The Correct Approach
Always declare `__slots__` on node classes in production systems:

```python
# CORRECT: Compact C-level struct layout without __dict__ overhead
class CompactNode:
    __slots__ = ("val", "next")

    def __init__(self, val: int, next: Optional[CompactNode] = None) -> None:
        self.val = val
        self.next = next
```

---

## 4. Forgetting to Sever Sublists Before Merging or Reordering

### The Mistake
Splitting a list into two halves without severing the pointer connecting the end of the first half:

```python
# WRONG: Creates an infinite cycle during zig-zag merge!
def reorder_list_flawed(head: Optional[ListNode]) -> None:
    slow = fast = head
    while fast.next and fast.next.next:
        slow = slow.next
        fast = fast.next.next

    second_half = reverse_list(slow.next)
    # BUG: Forgot slow.next = None!
    # The first half [1 -> 2 -> 3] still points to [4]!
    # Merging will result in an infinite loop!
```

### The Consequence
The tail of the first half remains connected to the head of the second half. When the merge loop interleaves nodes, a cycle is created between the middle nodes, hanging worker threads in infinite loops with 100% CPU utilization.

### The Correct Approach
Always explicitly sever the link by setting `slow.next = None`:

```python
# CORRECT: Explicitly disconnect the two sublists
second_half = slow.next
slow.next = None  # Sever connection! First half now cleanly terminates with None.
second_half = reverse_list(second_half)
```

---

## 5. Using Call-Stack Recursion on Long Lists (`RecursionError`)

### The Mistake
Implementing recursive solutions for list traversal or reversal:

```python
# WRONG: Crashes with RecursionError for N >= 1,000!
def reverse_recursive(head: Optional[ListNode]) -> Optional[ListNode]:
    if not head or not head.next:
        return head
    new_head = reverse_recursive(head.next)  # Pushes stack frame!
    head.next.next = head
    head.next = None
    return new_head
```

### The Consequence
Python's call stack default limit is `sys.getrecursionlimit() == 1000`. Processing an API payload or database query with 1,500 records triggers `RecursionError: maximum recursion depth exceeded while calling a Python object`, immediately failing the request.

### The Correct Approach
Always use iterative algorithms with strict $\mathcal{O}(1)$ auxiliary space for linked lists in production:

```python
# CORRECT: Iterative pointer manipulation scales to millions of nodes
def reverse_iterative(head: Optional[ListNode]) -> Optional[ListNode]:
    prev, curr = None, head
    while curr:
        curr.next, prev, curr = prev, curr, curr.next
    return prev
```

---

## 6. Corrupting Shared State in Read-Only Methods (Missing Restoration)

### The Mistake
Mutating pointers in a helper function like `is_palindrome` without restoring the list to its original state:

```python
# WRONG: Permanently breaks caller's list structure!
def is_palindrome_destructive(head: Optional[ListNode]) -> bool:
    # 1. Find middle
    # 2. Reverse second half
    # 3. Compare values
    # Returns True/False, but second half remains REVERSED!
    # Any downstream function iterating over head will observe corrupted data!
    return is_equal
```

### The Consequence
A function that implies a read-only query (`is_palindrome(...) -> bool`) silently mutates the shared in-memory data structure. Subsequent database writes or serializations persist truncated or reversed records.

### The Correct Approach
Always restore modified pointer arrows before returning:

```python
# CORRECT: Restore list structure before returning result
second_half_start = reverse_list(slow.next)
# ... perform comparison ...
# Restore:
slow.next = reverse_list(second_half_start)
return is_valid
```

---

## 7. Memory Leaks from Circular References in Doubly Linked Lists

### The Mistake
Assuming CPython's reference counting will immediately garbage collect doubly linked structures:

```python
# WRONG: Circular references prevent immediate deallocation!
class Node:
    def __init__(self, val):
        self.val = val
        self.prev = None
        self.next = None

node_a = Node(1)
node_b = Node(2)
node_a.next = node_b
node_b.prev = node_a  # Circular reference: node_a <-> node_b!

del node_a
del node_b
# Neither node is freed immediately by ref-counting!
# They must wait for cyclic GC generation scans, bloating memory.
```

### The Consequence
In CPython, reference cycles cannot be freed by immediate `Py_DECREF`. They survive until the cyclic garbage collector runs (Generation 1/2), increasing memory pressure and causing unpredictable GC latency spikes in high-throughput services.

### The Correct Approach
When unlinking nodes from an LRU cache or clearing a list, explicitly sever both `prev` and `next` pointers:

```python
# CORRECT: Explicitly sever bidirectional pointers upon eviction
def evict_node(self, node: DoublyLinkedNode) -> None:
    node.prev.next = node.next
    node.next.prev = node.prev
    node.prev = None  # Break circular reference!
    node.next = None  # Break circular reference!
```
