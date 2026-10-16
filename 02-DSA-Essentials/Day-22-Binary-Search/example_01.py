"""
Day 22: Example 01 - Core Binary Search Algorithms, Variants & Performance Benchmarks

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-throughput storage engines (e.g., Apache Kafka index files, RocksDB SSTable block
indexes, B-Trees in PostgreSQL), finding a record across millions of sequential entries
cannot rely on linear scans (O(N)). For N = 10,000,000 records, linear search takes up to
10,000,000 comparisons, while Binary Search resolves the key in at most 24 comparisons (O(log N)).

Furthermore, in AI batch scheduling and Kubernetes fleet autoscaling, binary search is used
on monotonic capacity functions ("Binary Search on Answer") to calculate the minimum compute
footprint or rate required to satisfy strict SLA latency deadlines.

This module implements:
1. `binary_search_exact`: Classic O(log N) lookup using the closed-interval [left, right] invariant.
2. `search_range`: Locates first and last occurrence of target using lower/upper boundary bisects.
3. `search_rotated_array`: O(log N) search on rotated arrays by identifying the strictly sorted half.
4. `find_min_rotated_array`: O(log N) inflection point / minimum value detection.
5. `min_batch_processing_rate`: Monotonic optimization boundary ("Binary Search on Answer").
6. High-volume comparative benchmark: O(N) linear search vs O(log N) binary search on 10,000,000 items.
"""

from __future__ import annotations

import bisect
import time
from typing import Callable, List, Optional, Tuple


# ============================================================================
# 1. EXACT BINARY SEARCH (CLOSED INTERVAL [left, right])
# ============================================================================

def binary_search_exact(nums: List[int], target: int) -> int:
    """
    Finds the exact index of target in sorted array nums, or returns -1 if absent.
    
    WHY CLOSED INTERVAL [left, right]:
    Both left and right are valid candidate indices. The loop condition 'while left <= right'
    guarantees that when the loop exits (left = right + 1), the search space has been
    exhaustively evaluated without missing any element.
    
    Time Complexity:  O(log N)
    Space Complexity: O(1)
    """
    left = 0
    right = len(nums) - 1

    while left <= right:
        # Safe midpoint to prevent 32-bit integer overflow in C-based runtimes
        mid = left + (right - left) // 2

        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1   # Target lies strictly in the right half
        else:
            right = mid - 1  # Target lies strictly in the left half

    return -1


# ============================================================================
# 2. SEARCH RANGE (FIRST & LAST OCCURRENCE BOUNDARIES)
# ============================================================================

def find_bisect_left(nums: List[int], target: int) -> int:
    """Finds the first index where nums[index] >= target (Lower Bound)."""
    left, right = 0, len(nums)
    while left < right:
        mid = left + (right - left) // 2
        if nums[mid] >= target:
            right = mid
        else:
            left = mid + 1
    return left


def find_bisect_right(nums: List[int], target: int) -> int:
    """Finds the first index where nums[index] > target (Upper Bound)."""
    left, right = 0, len(nums)
    while left < right:
        mid = left + (right - left) // 2
        if nums[mid] > target:
            right = mid
        else:
            left = mid + 1
    return left


def search_range(nums: List[int], target: int) -> List[int]:
    """
    Returns the [start, end] indices of target in a sorted array containing duplicates.
    Returns [-1, -1] if target is not found.
    
    Time Complexity:  O(log N)
    Space Complexity: O(1)
    """
    start = find_bisect_left(nums, target)
    
    # Check if target exists in array
    if start >= len(nums) or nums[start] != target:
        return [-1, -1]

    # Upper bound gives first index > target; subtract 1 to get last occurrence
    end = find_bisect_right(nums, target) - 1
    return [start, end]


# ============================================================================
# 3. SEARCH IN ROTATED SORTED ARRAY (INVARIANT OF THE SORTED HALF)
# ============================================================================

def search_rotated_array(nums: List[int], target: int) -> int:
    """
    Searches for target in a rotated sorted array of unique integers.
    
    THE CRITICAL INVARIANT:
    Splitting a rotated sorted array at ANY midpoint divides it into two halves:
    at least one half is ALWAYS strictly sorted in ascending order!
    By testing if the target falls within the sorted half's boundaries, we can
    safely discard half the search space in O(1) time.
    
    Time Complexity:  O(log N)
    Space Complexity: O(1)
    """
    left = 0
    right = len(nums) - 1

    while left <= right:
        mid = left + (right - left) // 2

        if nums[mid] == target:
            return mid

        # Check if the LEFT half [left .. mid] is sorted
        if nums[left] <= nums[mid]:
            # Target lies within the sorted left half
            if nums[left] <= target < nums[mid]:
                right = mid - 1
            else:
                left = mid + 1
        # Otherwise, the RIGHT half [mid .. right] MUST be sorted
        else:
            # Target lies within the sorted right half
            if nums[mid] < target <= nums[right]:
                left = mid + 1
            else:
                right = mid - 1

    return -1


# ============================================================================
# 4. FIND MINIMUM IN ROTATED SORTED ARRAY (INFLECTION POINT)
# ============================================================================

def find_min_rotated_array(nums: List[int]) -> int:
    """
    Finds the minimum element (rotation inflection point) in a rotated sorted array.
    
    WHY COMPARE AGAINST nums[right]:
    In a rotated sorted array, all elements after the pivot are smaller than elements
    before the pivot. Comparing nums[mid] with nums[right]:
    - If nums[mid] > nums[right], the pivot MUST be strictly to the right of mid.
    - If nums[mid] <= nums[right], mid could be the minimum, or the pivot is to its left.
    
    Time Complexity:  O(log N)
    Space Complexity: O(1)
    """
    left = 0
    right = len(nums) - 1

    while left < right:
        mid = left + (right - left) // 2

        if nums[mid] > nums[right]:
            # Minimum is strictly to the right
            left = mid + 1
        else:
            # Minimum is at mid or to the left
            right = mid

    return nums[left]


# ============================================================================
# 5. MONOTONIC OPTIMIZATION BOUNDARY (BINARY SEARCH ON ANSWER)
# ============================================================================

def min_batch_processing_rate(workloads: List[int], max_hours: int) -> int:
    """
    Finds the minimum integer processing rate (tasks/hour) required to finish
    all task workloads within max_hours. (Koko Eating Bananas Pattern).
    
    MONOTONIC PREDICATE THEOREM:
    As processing rate R increases, total processing time T(R) monotonically decreases.
    Predicate can_finish(R) is monotonic: [False, False, ..., True, True].
    Binary search identifies the exact transition boundary in O(N log(max_workload)).
    
    Time Complexity:  O(N log M) where N = len(workloads), M = max(workloads)
    Space Complexity: O(1)
    """
    def can_finish(rate: int) -> bool:
        # Compute ceiling division (workload / rate) without floating point imprecision:
        # ceil(a / b) == (a + b - 1) // b
        total_hours = sum((w + rate - 1) // rate for w in workloads)
        return total_hours <= max_hours

    # Lower bound: at least 1 task/hour. Upper bound: max single workload in 1 hour.
    left = 1
    right = max(workloads) if workloads else 1

    while left < right:
        mid = left + (right - left) // 2
        if can_finish(mid):
            right = mid      # Rate is sufficient, try lower rate
        else:
            left = mid + 1   # Rate is insufficient, must increase speed

    return left


# ============================================================================
# 6. VERIFICATION SUITE & COMPREHENSIVE BENCHMARKS
# ============================================================================

def run_tests() -> None:
    """Validates edge cases and standard fixtures across all algorithms."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR BINARY SEARCH ALGORITHMS")
    print("======================================================================")

    # 1. Exact Binary Search
    sorted_arr = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]
    assert binary_search_exact(sorted_arr, 23) == 5
    assert binary_search_exact(sorted_arr, 2) == 0
    assert binary_search_exact(sorted_arr, 91) == 9
    assert binary_search_exact(sorted_arr, 50) == -1
    print("[PASS] binary_search_exact verified.")

    # 2. Search Range
    dup_arr = [5, 7, 7, 8, 8, 8, 10]
    assert search_range(dup_arr, 8) == [3, 5]
    assert search_range(dup_arr, 7) == [1, 2]
    assert search_range(dup_arr, 6) == [-1, -1]
    assert search_range(dup_arr, 5) == [0, 0]
    assert search_range(dup_arr, 10) == [6, 6]
    print("[PASS] search_range boundary bisects verified.")

    # 3. Rotated Array Search
    rot1 = [4, 5, 6, 7, 0, 1, 2]
    assert search_rotated_array(rot1, 0) == 4
    assert search_rotated_array(rot1, 3) == -1
    assert search_rotated_array(rot1, 4) == 0
    assert search_rotated_array(rot1, 2) == 6

    rot2 = [1]
    assert search_rotated_array(rot2, 0) == -1
    assert search_rotated_array(rot2, 1) == 0
    print("[PASS] search_rotated_array sorted-half invariants verified.")

    # 4. Find Minimum in Rotated Array
    assert find_min_rotated_array([3, 4, 5, 1, 2]) == 1
    assert find_min_rotated_array([4, 5, 6, 7, 0, 1, 2]) == 0
    assert find_min_rotated_array([11, 13, 15, 17]) == 11
    assert find_min_rotated_array([2, 1]) == 1
    print("[PASS] find_min_rotated_array inflection point verified.")

    # 5. Monotonic Optimization Boundary (Koko Bananas)
    assert min_batch_processing_rate([3, 6, 7, 11], 8) == 4
    assert min_batch_processing_rate([30, 11, 23, 4, 20], 5) == 30
    assert min_batch_processing_rate([30, 11, 23, 4, 20], 6) == 23
    print("[PASS] min_batch_processing_rate optimization boundary verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Benchmarks O(N) linear search vs O(log N) binary search over 10,000,000 items."""
    print("======================================================================")
    print("HIGH-VOLUME BENCHMARK: O(N) LINEAR SEARCH VS O(LOG N) BINARY SEARCH")
    print("======================================================================")

    data_size = 10_000_000
    print(f"Dataset Size: {data_size:,} sorted integers in contiguous memory.")
    
    # Target positioned near the very end to evaluate worst-case linear traversal
    target = data_size - 42

    # Linear Search Benchmark (O(N))
    start_linear = time.perf_counter()
    linear_idx = -1
    # Standard manual linear scan
    for idx in range(data_size):
        if idx == target:
            linear_idx = idx
            break
    elapsed_linear = time.perf_counter() - start_linear

    # Binary Search Benchmark (O(log N))
    start_binary = time.perf_counter()
    # Search simulated space [0 .. data_size - 1]
    left, right = 0, data_size - 1
    binary_idx = -1
    ops = 0
    while left <= right:
        ops += 1
        mid = left + (right - left) // 2
        if mid == target:
            binary_idx = mid
            break
        elif mid < target:
            left = mid + 1
        else:
            right = mid - 1
    elapsed_binary = time.perf_counter() - start_binary

    speedup = elapsed_linear / elapsed_binary if elapsed_binary > 0 else float("inf")

    print(f"Linear Search (O(N) - {target:,} comparisons):   {elapsed_linear * 1000:8.2f} ms")
    print(f"Binary Search (O(log N) - {ops} comparisons):         {elapsed_binary * 1000:8.4f} ms")
    print(f"Speedup Factor with Binary Search:                 {speedup:8.1f}x faster!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
