# Day 21: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Linked Lists, Fast & Slow Pointers, In-Place Reversals, and Low-Level Memory Mechanics for backend engineers.

---

## 1. Dynamic Array vs. Linked List Comparison

| Dimension | Dynamic Array (`list`) | Singly Linked List | Doubly Linked List |
| :--- | :--- | :--- | :--- |
| **Index Access** | $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
| **Insert / Delete at Head** | $\mathcal{O}(N)$ (`memmove` shift) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| **Insert / Delete at Tail** | Amortized $\mathcal{O}(1)$ | $\mathcal{O}(1)$ (with tail ptr) | $\mathcal{O}(1)$ |
| **Delete Arbitrary Node** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ (needs predecessor) | $\mathcal{O}(1)$ (has `prev` pointer) |
| **CPU Cache Locality** | **Superb** (contiguous L1 prefetch) | **Poor** (pointer chasing / cache misses) | **Poor** (pointer chasing) |
| **Memory Overhead** | 8 bytes / element ptr | ~48 bytes (`__slots__`) | ~56 bytes (`__slots__`) |

---

## 2. Fast & Slow Pointer Invariants

```
Middle of List:               Cycle Detection:              Cycle Entry Point:
slow = 1 step                 slow = 1 step                 After collision:
fast = 2 steps                fast = 2 steps                Reset slow to head.
When fast hits end:           If slow is fast:              Advance both 1 step.
slow is at the exact middle!  Cycle exists!                 Collision = Cycle Entry!
```

- **Floyd's Equation**:
  $$L = (n - 1)C + (C - k)$$
  Where $L$ is distance to cycle entry, $C$ is cycle circumference, and $k$ is meeting offset.

---

## 3. High-Yield Code Templates

### Template A: In-Place Reversal ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Space)
```python
def reverse_list(head: Optional[ListNode]) -> Optional[ListNode]:
    prev, curr = None, head
    while curr:
        next_node = curr.next  # Cache next node
        curr.next = prev       # Reverse arrow
        prev = curr            # Advance prev
        curr = next_node       # Advance curr
    return prev
```

### Template B: Cycle Detection & Entry Node ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Space)
```python
def find_cycle_entry(head: Optional[ListNode]) -> Optional[ListNode]:
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow is fast:
            slow = head
            while slow is not fast:
                slow, fast = slow.next, fast.next
            return slow
    return None
```

### Template C: Sentinel Dummy Head Pattern
```python
def remove_elements(head: Optional[ListNode], val: int) -> Optional[ListNode]:
    dummy = ListNode(0, head)  # Sentinel eliminates head-deletion conditionals
    curr = dummy
    while curr.next:
        if curr.next.val == val:
            curr.next = curr.next.next
        else:
            curr = curr.next
    return dummy.next
```

---

## 4. 5 Non-Negotiable Engineering Rules

1. **Always Use `__slots__`**: Cuts node allocation memory from ~344 bytes to ~48 bytes (an 86% RAM savings).
2. **Never Dereference Unchecked Pointers**: Always write `while fast and fast.next:` before accessing `fast.next.next`.
3. **Cache Next Before Mutation**: Any `curr.next = ...` assignment destroys downstream references. Cache `next_node = curr.next` first.
4. **Sever Sublists When Splitting**: Always explicitly set `slow.next = None` before merging or reversing sublists to avoid infinite loops.
5. **Restore Mutated Structures**: If a read method (like `is_palindrome`) inverts pointers, always invert them back before returning to maintain referential integrity.

---

## 5. Complexity Quick Reference

| Problem | Time Complexity | Auxiliary Space | Key Technique |
| :--- | :--- | :--- | :--- |
| **Reverse Linked List** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | 3-pointer swapping (`prev`, `curr`, `next`) |
| **Middle of Linked List** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Fast & Slow Pointers (1-step & 2-step) |
| **Linked List Cycle I & II** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Floyd's Tortoise & Hare Algorithm |
| **Palindrome Linked List** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Find Middle $\to$ Reverse $\to$ Compare $\to$ Restore |
| **Reorder List** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Middle Split $\to$ Reverse $\to$ Zig-zag Merge |
| **Remove N-th From End** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Lead-Follower Two Pointers (N-step lead) |
| **Merge Two Sorted Lists** | $\mathcal{O}(N + M)$ | $\mathcal{O}(1)$ | Sentinel Dummy Head + Tail Pointer |
| **LRU Cache (Get / Put)** | $\mathcal{O}(1)$ | $\mathcal{O}(\text{Cap})$ | Hash Map + Doubly Linked List with Sentinels |
| **Merge K Sorted Lists** | $\mathcal{O}(N \log K)$ | $\mathcal{O}(K)$ | Min-Heap Priority Queue |
| **Copy List with Random Ptr**| $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Interweaving Cloned Nodes |
