"""
Day 03: Data Structures Internals (Lists, Tuples, Sets & Dictionaries)
File: example_01.py - Deep Dive into List Over-Allocation & Hash Table Lookups

COMMENT PHILOSOPHY:
Comments explain *WHY* memory allocation and complexity patterns exist inside CPython,
not simply *WHAT* the code is executing.
"""

import sys
import time
from collections import deque
from typing import List, Set


def inspect_list_overallocation(target_count: int = 30) -> None:
    """
    Demonstrates CPython's list over-allocation growth curve.
    
    WHY THIS MATTERS:
    CPython lists do NOT resize by exactly 1 slot on every .append().
    If they did, each append would require a malloc() syscall and a full memory copy,
    making list building an O(N^2) operation.
    Instead, CPython grows capacity in geometric increments, guaranteeing
    amortized O(1) append time complexity.
    """
    print("=" * 65)
    print("1. LIST CAPACITY & OVER-ALLOCATION INSPECTION")
    print("=" * 65)

    elements: List[int] = []
    previous_byte_size = sys.getsizeof(elements)

    print(f"Empty list base overhead: {previous_byte_size} bytes (PyListObject struct)")

    for i in range(1, target_count + 1):
        elements.append(i)
        current_byte_size = sys.getsizeof(elements)

        # WHY: A change in getsizeof() indicates that the internal pointer array
        # ran out of allocated slots and reallocated a larger memory block.
        if current_byte_size != previous_byte_size:
            # On 64-bit systems, each pointer occupies 8 bytes
            capacity = (current_byte_size - 56) // 8  # 56 bytes is PyListObject header
            print(
                f"Appended: {len(elements):<2} items | "
                f"Memory: {current_byte_size} bytes | "
                f"Allocated Capacity: ~{capacity} slots"
            )
            previous_byte_size = current_byte_size


def benchmark_list_vs_set_lookup(dataset_size: int = 100_000) -> None:
    """
    Benchmarks O(N) linear search in lists vs O(1) hash lookup in sets.
    
    WHY THIS MATTERS:
    In backend authentication middleware, checking if a user token is revoked
    using a list will degrade database/API latency linearly as user count grows.
    Sets use open-addressing hash tables, maintaining sub-microsecond lookups
    regardless of whether the set contains 100 or 10,000,000 elements.
    """
    print("\n" + "=" * 65)
    print(f"2. LOOKUP BENCHMARK: List O(N) vs Set O(1) ({dataset_size:,} items)")
    print("=" * 65)

    raw_data = list(range(dataset_size))
    data_list: List[int] = raw_data
    data_set: Set[int] = set(raw_data)

    target_item = dataset_size - 1  # Worst-case item at the very end

    # Benchmark List Lookup
    start_list = time.perf_counter()
    _ = target_item in data_list
    list_duration = (time.perf_counter() - start_list) * 1000

    # Benchmark Set Lookup
    start_set = time.perf_counter()
    _ = target_item in data_set
    set_duration = (time.perf_counter() - start_set) * 1000

    print(f"List Lookup O(N) Duration : {list_duration:.4f} ms")
    print(f"Set Lookup O(1) Duration  : {set_duration:.6f} ms")
    if set_duration > 0:
        print(f"Set Speedup Multiplier    : ~{list_duration / set_duration:,.0f}x faster!")


def benchmark_fifo_queue_pop(operations: int = 50_000) -> None:
    """
    Benchmarks list.pop(0) vs collections.deque.popleft().
    
    WHY THIS MATTERS:
    'list.pop(0)' is an O(N) operation. When element 0 is removed from a dynamic array,
    all remaining (N-1) pointers must be shifted 8 bytes to the left in contiguous memory.
    
    'collections.deque' is implemented as a doubly-linked list of 64-element block arrays.
    Popping from either extreme requires only updating boundary pointers: O(1) time complexity.
    """
    print("\n" + "=" * 65)
    print(f"3. QUEUE BENCHMARK: list.pop(0) vs deque.popleft() ({operations:,} pops)")
    print("=" * 65)

    test_list = list(range(operations))
    test_deque = deque(range(operations))

    # Benchmark list.pop(0)
    start_list = time.perf_counter()
    while test_list:
        test_list.pop(0)
    list_time = time.perf_counter() - start_list

    # Benchmark deque.popleft()
    start_deque = time.perf_counter()
    while test_deque:
        test_deque.popleft()
    deque_time = time.perf_counter() - start_deque

    print(f"'list.pop(0)' O(N) Total Time       : {list_time:.4f} seconds")
    print(f"'deque.popleft()' O(1) Total Time   : {deque_time:.6f} seconds")
    print(f"Deque Speedup Multiplier            : ~{list_time / deque_time:,.0f}x faster!")


def demonstrate_hashability_contract() -> None:
    """
    Demonstrates why mutable objects cannot be dictionary keys.
    """
    print("\n" + "=" * 65)
    print("4. THE HASHABILITY CONTRACT (Why mutable objects cannot be keys)")
    print("=" * 65)

    # Immutable tuples can be hashed if all contained elements are also hashable
    valid_key = ("192.168.1.1", 8080)
    print(f"Tuple key {valid_key} hash: {hash(valid_key)} (Valid dict key)")

    try:
        # A list is mutable; its contents can change, which would corrupt hash table invariants
        invalid_key = ["192.168.1.1", 8080]
        _ = {invalid_key: "active_connection"}
    except TypeError as err:
        print(f"Attempting to use list as dict key -> Caught expected error: {err}")


if __name__ == "__main__":
    inspect_list_overallocation()
    benchmark_list_vs_set_lookup()
    benchmark_fifo_queue_pop()
    demonstrate_hashability_contract()
