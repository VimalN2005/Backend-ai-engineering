"""
Day 21: Example 01 - Core Linked List Algorithms & Memory Mechanics

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
While contiguous dynamic arrays (`list` in Python) dominate general computing due to CPU
cache line prefetching, Linked Lists are indispensable when constant-time O(1) mutations
(insertions, deletions, and splice operations) are required without triggering expensive
O(N) memory shifts.

Furthermore, in systems programming and caching (e.g., Linux kernel task queues, Redis
quicklists, and LRU cache eviction engines), linked structures allow direct pointer updates
across non-contiguous heap blocks.

This module implements:
1. `ListNode`: Compact, memory-optimized node definition using `__slots__`.
2. `has_cycle` & `detect_cycle_entry`: Floyd's Tortoise and Hare algorithm.
3. `reverse_linked_list`: In-place three-pointer iterative reversal (O(1) auxiliary space).
4. `is_palindrome_linked_list`: O(N) time & O(1) space palindrome check with structure restoration.
5. `reorder_list`: Fold list (L0 -> Ln -> L1 -> Ln-1 ...) via middle-split, reverse, and zig-zag merge.
6. `remove_nth_from_end`: One-pass lead-follower deletion using a sentinel dummy head.
7. Memory and performance benchmark: `__slots__` vs standard `__dict__` overhead and O(1) vs O(N) head deletion.
"""

from __future__ import annotations

import sys
import time
from typing import Any, List, Optional, Tuple


# ============================================================================
# 1. COMPACT NODE DEFINITION USING __SLOTS__
# ============================================================================

class ListNode:
    """
    Singly linked list node optimized for high-density allocation.
    
    WHY __SLOTS__:
    By default, Python classes allocate a dynamic dictionary (__dict__) for every instance,
    adding ~104+ bytes of overhead per node. Declaring __slots__ eliminates __dict__ and
    __weakref__, dropping memory per node from ~152 bytes to ~48 bytes (a ~68% reduction).
    """
    __slots__ = ("val", "next")

    def __init__(self, val: int, next: Optional[ListNode] = None) -> None:
        self.val = val
        self.next = next

    def __repr__(self) -> str:
        return f"ListNode({self.val})"


# Utility helpers for testing and conversion
def build_linked_list(values: List[int]) -> Optional[ListNode]:
    """Converts a Python list of integers into a singly linked list."""
    if not values:
        return None
    head = ListNode(values[0])
    curr = head
    for v in values[1:]:
        curr.next = ListNode(v)
        curr = curr.next
    return head


def to_list(head: Optional[ListNode]) -> List[int]:
    """Serializes a singly linked list back into a Python list."""
    result: List[int] = []
    curr = head
    while curr:
        result.append(curr.val)
        curr = curr.next
    return result


# ============================================================================
# 2. FLOYD'S TORTOISE & HARE (CYCLE DETECTION & CYCLE ENTRY)
# ============================================================================

def has_cycle(head: Optional[ListNode]) -> bool:
    """
    Determines if a linked list contains a cycle using Floyd's algorithm.
    
    WHY FAST & SLOW POINTERS:
    Using a hash set of visited node IDs requires O(N) auxiliary space.
    Moving slow by 1 step and fast by 2 steps guarantees collision within the cycle
    in O(N) time while using strictly O(1) auxiliary space.
    """
    slow = fast = head
    while fast and fast.next:
        slow = slow.next          # 1 step
        fast = fast.next.next     # 2 steps
        if slow is fast:
            return True
    return False


def detect_cycle_entry(head: Optional[ListNode]) -> Optional[ListNode]:
    """
    Finds the exact starting node where the cycle begins.
    
    MATHEMATICAL DERIVATION:
    Let L = distance from head to cycle start.
    Let C = cycle length.
    Let k = distance from cycle start to meeting point.
    Slow travels: L + k.
    Fast travels: L + nC + k.
    Because Fast is 2x faster: 2(L + k) = L + nC + k  =>  L = nC - k = (n-1)C + (C - k).
    Therefore, resetting one pointer to head and advancing both by 1 step causes
    them to collide exactly at the cycle entry after L steps!
    """
    slow = fast = head
    has_cycle_detected = False

    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow is fast:
            has_cycle_detected = True
            break

    if not has_cycle_detected:
        return None

    # Phase 2: Reset slow to head, advance both by 1 step simultaneously
    slow = head
    while slow is not fast:
        slow = slow.next
        fast = fast.next

    return slow  # Exact collision point is the cycle entrance


# ============================================================================
# 3. IN-PLACE THREE-POINTER REVERSAL
# ============================================================================

def reverse_linked_list(head: Optional[ListNode]) -> Optional[ListNode]:
    """
    Reverses a singly linked list in-place using three pointers.
    
    WHY ITERATIVE OVER RECURSIVE:
    Recursive reversal consumes O(N) stack frames. For lists with N > 1,000,
    recursion crashes with RecursionError (Python default recursion limit is 1,000).
    Iterative pointer redirection guarantees strict O(1) memory and O(N) time.
    """
    prev: Optional[ListNode] = None
    curr: Optional[ListNode] = head

    while curr:
        next_node = curr.next  # Cache next node before breaking reference
        curr.next = prev       # Reverse arrow
        prev = curr            # Advance prev
        curr = next_node       # Advance curr

    return prev


# ============================================================================
# 4. PALINDROME VERIFICATION (O(1) AUXILIARY MEMORY + RESTORATION)
# ============================================================================

def is_palindrome_linked_list(head: Optional[ListNode]) -> bool:
    """
    Checks if a linked list is a palindrome in O(N) time and O(1) auxiliary space.
    Crucially restores the list structure before returning.
    
    ALGORITHM:
    1. Find middle node using Fast & Slow pointers.
    2. Reverse the second half in-place.
    3. Compare node values from front and back.
    4. Reverse the second half again to restore original list integrity.
    """
    if not head or not head.next:
        return True

    # 1. Find end of first half
    slow = fast = head
    while fast.next and fast.next.next:
        slow = slow.next
        fast = fast.next.next

    # 2. Reverse second half
    second_half_start = reverse_linked_list(slow.next)

    # 3. Compare two halves
    p1 = head
    p2 = second_half_start
    is_valid = True
    while is_valid and p2:
        if p1.val != p2.val:
            is_valid = False
        p1 = p1.next
        p2 = p2.next

    # 4. Restore original list structure (Engineering hygiene)
    slow.next = reverse_linked_list(second_half_start)

    return is_valid


# ============================================================================
# 5. REORDER / FOLD LIST PATTERN (L0 -> Ln -> L1 -> Ln-1 ...)
# ============================================================================

def reorder_list(head: Optional[ListNode]) -> None:
    """
    Reorders linked list in-place: L0 -> Ln -> L1 -> Ln-1 -> L2 -> Ln-2 ...
    
    WHY DECOMPOSITION MATTERS:
    Complex pointer problems become simple when decomposed into 3 primitives:
    Step 1: Find middle and sever halves (slow.next = None).
    Step 2: Reverse second half.
    Step 3: Interleave/merge two lists in zig-zag fashion.
    """
    if not head or not head.next or not head.next.next:
        return

    # Step 1: Find middle and sever
    slow = fast = head
    while fast.next and fast.next.next:
        slow = slow.next
        fast = fast.next.next

    second_half = slow.next
    slow.next = None  # Sever connection to prevent cycles during merge!

    # Step 2: Reverse second half
    second_half = reverse_linked_list(second_half)

    # Step 3: Interleave
    first = head
    second = second_half
    while second:
        tmp1, tmp2 = first.next, second.next
        first.next = second
        second.next = tmp1
        first, second = tmp1, tmp2


# ============================================================================
# 6. REMOVE N-TH NODE FROM END (ONE-PASS TWO POINTERS + SENTINEL)
# ============================================================================

def remove_nth_from_end(head: Optional[ListNode], n: int) -> Optional[ListNode]:
    """
    Deletes the n-th node from the end of the list in a single pass.
    
    WHY SENTINEL DUMMY NODE:
    If n equals the list length, the node to delete is the head itself.
    A dummy sentinel node positioned before head allows uniform deletion
    without special-case branching (`dummy.next = dummy.next.next`).
    """
    dummy = ListNode(0, head)
    leader = follower = dummy

    # Advance leader n + 1 steps ahead
    for _ in range(n + 1):
        if leader is None:
            return head  # n exceeds length
        leader = leader.next

    # Advance both simultaneously until leader hits the end
    while leader:
        leader = leader.next
        follower = follower.next

    # follower is now positioned immediately before the target node
    if follower.next:
        follower.next = follower.next.next

    return dummy.next


# ============================================================================
# 7. MERGE TWO SORTED LISTS
# ============================================================================

def merge_two_sorted_lists(l1: Optional[ListNode], l2: Optional[ListNode]) -> Optional[ListNode]:
    """Merges two sorted linked lists into one sorted list in O(N + M) time and O(1) space."""
    dummy = ListNode(0)
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


# ============================================================================
# 8. VERIFICATION SUITE & COMPREHENSIVE BENCHMARKS
# ============================================================================

class UnoptimizedNode:
    """Standard node carrying default __dict__ overhead for memory benchmarking."""
    def __init__(self, val: int, next: Any = None) -> None:
        self.val = val
        self.next = next


def run_tests() -> None:
    """Validates edge cases and standard fixtures across all algorithms."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR LINKED LIST ALGORITHMS")
    print("======================================================================")

    # 1. Reverse Linked List
    ll1 = build_linked_list([1, 2, 3, 4, 5])
    rev = reverse_linked_list(ll1)
    assert to_list(rev) == [5, 4, 3, 2, 1], "Failed reverse list"
    assert reverse_linked_list(None) is None
    print("[PASS] reverse_linked_list in-place verified.")

    # 2. Cycle Detection & Cycle Entry
    nodes = [ListNode(i) for i in range(5)]
    for i in range(4):
        nodes[i].next = nodes[i + 1]
    # No cycle yet
    assert not has_cycle(nodes[0]), "False positive in cycle check"

    # Create cycle: Node 4 points back to Node 2 (Cycle: 2 -> 3 -> 4 -> 2)
    nodes[4].next = nodes[2]
    assert has_cycle(nodes[0]), "Failed to detect cycle"
    cycle_start = detect_cycle_entry(nodes[0])
    assert cycle_start is nodes[2], f"Expected Node 2, got {cycle_start}"
    print("[PASS] Floyd's cycle detection and entry derivation verified.")

    # 3. Palindrome Check
    p1 = build_linked_list([1, 2, 2, 1])
    assert is_palindrome_linked_list(p1), "Failed even palindrome"
    assert to_list(p1) == [1, 2, 2, 1], "Structure was corrupted after check!"

    p2 = build_linked_list([1, 2, 3, 2, 1])
    assert is_palindrome_linked_list(p2), "Failed odd palindrome"
    assert to_list(p2) == [1, 2, 3, 2, 1], "Structure was corrupted after check!"

    p3 = build_linked_list([1, 2, 3, 4])
    assert not is_palindrome_linked_list(p3), "False positive for non-palindrome"
    print("[PASS] is_palindrome_linked_list with structural restoration verified.")

    # 4. Reorder List
    r1 = build_linked_list([1, 2, 3, 4, 5])
    reorder_list(r1)
    assert to_list(r1) == [1, 5, 2, 4, 3], f"Failed reorder list: {to_list(r1)}"

    r2 = build_linked_list([1, 2, 3, 4])
    reorder_list(r2)
    assert to_list(r2) == [1, 4, 2, 3], f"Failed reorder even list: {to_list(r2)}"
    print("[PASS] reorder_list fold transformation verified.")

    # 5. Remove N-th From End
    rem1 = build_linked_list([1, 2, 3, 4, 5])
    rem1 = remove_nth_from_end(rem1, 2)
    assert to_list(rem1) == [1, 2, 3, 5], f"Failed removing 2nd from end: {to_list(rem1)}"

    rem2 = build_linked_list([1, 2])
    rem2 = remove_nth_from_end(rem2, 2)  # Delete head
    assert to_list(rem2) == [2], f"Failed deleting head: {to_list(rem2)}"
    print("[PASS] remove_nth_from_end one-pass deletion verified.")

    # 6. Merge Two Sorted
    m1 = build_linked_list([1, 2, 4])
    m2 = build_linked_list([1, 3, 4])
    merged = merge_two_sorted_lists(m1, m2)
    assert to_list(merged) == [1, 1, 2, 3, 4, 4]
    print("[PASS] merge_two_sorted_lists verified.")

    print("All unit tests passed successfully!\n")


def run_benchmarks() -> None:
    """Benchmarks CPython memory mechanics (__slots__) and head mutation speed."""
    print("======================================================================")
    print("BENCHMARK 1: CPYTHON MEMORY OVERHEAD (__SLOTS__ VS __DICT__)")
    print("======================================================================")

    n_nodes = 100_000
    
    # Measure memory size of individual node instance
    unopt_sample = UnoptimizedNode(42)
    opt_sample = ListNode(42)

    unopt_size = sys.getsizeof(unopt_sample) + sys.getsizeof(unopt_sample.__dict__)
    opt_size = sys.getsizeof(opt_sample)

    print(f"Single Node Instance Size:")
    print(f"  Unoptimized Node (__dict__): {unopt_size} bytes")
    print(f"  Optimized Node (__slots__):   {opt_size} bytes")
    print(f"  Memory Saved per Node:       {unopt_size - opt_size} bytes ({(1 - opt_size / unopt_size) * 100:.1f}% reduction)")

    print(f"\nProjected RAM footprint for {n_nodes:,} nodes:")
    print(f"  Unoptimized: ~{(unopt_size * n_nodes) / (1024 * 1024):.2f} MB")
    print(f"  Optimized:   ~{(opt_size * n_nodes) / (1024 * 1024):.2f} MB")

    print("\n======================================================================")
    print("BENCHMARK 2: HEAD DELETION: LIST.POP(0) O(N) VS LINKED LIST HEAD=HEAD.NEXT O(1)")
    print("======================================================================")

    data_size = 50_000
    py_list = list(range(data_size))
    ll_head = build_linked_list(list(range(data_size)))

    # Deleting 5,000 items from front of Python list (O(N) shifts)
    start_list = time.perf_counter()
    for _ in range(5_000):
        py_list.pop(0)
    elapsed_list = time.perf_counter() - start_list

    # Deleting 5,000 items from front of Linked List (O(1) pointer advance)
    start_ll = time.perf_counter()
    curr_ll = ll_head
    for _ in range(5_000):
        if curr_ll:
            curr_ll = curr_ll.next
    elapsed_ll = time.perf_counter() - start_ll

    speedup = elapsed_list / elapsed_ll if elapsed_ll > 0 else float("inf")

    print(f"Python list.pop(0) [5,000 deletions]:       {elapsed_list * 1000:8.2f} ms")
    print(f"Linked List head = head.next [5,000 pops]:   {elapsed_ll * 1000:8.4f} ms")
    print(f"Speedup Factor with Linked Structure:        {speedup:8.1f}x faster!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmarks()
