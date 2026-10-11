"""
Day 16: Example 01 - Core Two Pointers Algorithms & In-Place Array Mechanics

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-throughput microservices, manipulating sorted logs, search indices, and
in-memory cache entries requires algorithms that minimize both time complexity
and memory allocations. Naive quadratic searches (O(N^2)) degrade system throughput,
while excessive list copying triggers frequent garbage collection (GC) pauses.

The Two Pointers technique provides optimal O(N) time complexity with O(1) auxiliary
space by exploiting sorted invariants and monotonic properties across contiguous memory.

This module implements:
1. Two Sum on Sorted Array (Opposite-direction boundary pointers).
2. In-Place Duplicate Removal & Array Compaction (Fast/Slow runner pointers).
3. Container With Most Water (Greedy inward boundary shrinkage).
4. 3Sum Triplet Identification (Anchor pointer + dual-pointer sweep with duplicate pruning).
5. Comprehensive unit tests and algorithmic performance benchmarks.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple


# ============================================================================
# 1. TWO SUM ON SORTED ARRAY (OPPOSITE DIRECTION)
# ============================================================================

def two_sum_sorted(numbers: List[int], target: int) -> Optional[Tuple[int, int]]:
    """
    Finds 0-indexed indices of two numbers in a sorted array that sum to target.
    
    WHY TWO POINTERS:
    Because 'numbers' is monotonically increasing, moving 'left' inward strictly
    increases the sum, while moving 'right' inward strictly decreases the sum.
    This eliminates O(N^2) nested loops down to a single O(N) pass with O(1) space,
    avoiding the O(N) hash table memory allocation overhead.
    
    Time Complexity:  O(N) - At most N pointer advancements.
    Space Complexity: O(1) - Zero additional memory allocated.
    """
    left = 0
    right = len(numbers) - 1

    while left < right:
        current_sum = numbers[left] + numbers[right]
        if current_sum == target:
            return left, right
        elif current_sum < target:
            # Current sum is too small; incrementing left is the only way to increase it
            left += 1
        else:
            # Current sum is too large; decrementing right is the only way to decrease it
            right -= 1

    return None


# ============================================================================
# 2. IN-PLACE ARRAY COMPACTION (FAST & SLOW RUNNER POINTERS)
# ============================================================================

def remove_duplicates_inplace(nums: List[int]) -> int:
    """
    Deduplicates a sorted array in-place, returning the count of unique elements.
    
    WHY FAST & SLOW RUNNERS:
    In-memory datasets (e.g. database query results, sorted event logs) should be
    compacted without allocating new lists (`list(set(nums))`), which causes
    unnecessary heap allocations and GC pressure.
    The slow pointer acts as the write head; the fast pointer acts as the read head.
    
    Time Complexity:  O(N) - Fast pointer scans the list once.
    Space Complexity: O(1) - Mutates input buffer directly in-place.
    """
    if not nums:
        return 0

    write_idx = 0
    for read_idx in range(1, len(nums)):
        # When a new distinct value is encountered by the read head
        if nums[read_idx] != nums[write_idx]:
            write_idx += 1
            nums[write_idx] = nums[read_idx]

    # Length of unique elements is write_idx + 1
    return write_idx + 1


# ============================================================================
# 3. CONTAINER WITH MOST WATER (GREEDY INWARD SHRINKAGE)
# ============================================================================

def container_with_most_water(heights: List[int]) -> int:
    """
    Calculates the maximum area formed between vertical lines and the x-axis.
    
    WHY GREEDY TWO POINTERS:
    Area = (right - left) * min(heights[left], heights[right]).
    If we advance the pointer with the taller line, the width decreases while the
    limiting height cannot increase (it is already bound by the shorter line).
    Therefore, the ONLY way to discover a potentially larger area is to discard
    the shorter line and move its pointer inward.
    
    Time Complexity:  O(N) - Inward shrinkage until pointers meet.
    Space Complexity: O(1) - Constant auxiliary registers.
    """
    left = 0
    right = len(heights) - 1
    max_area = 0

    while left < right:
        width = right - left
        h_left = heights[left]
        h_right = heights[right]

        current_height = h_left if h_left < h_right else h_right
        area = width * current_height

        if area > max_area:
            max_area = area

        # Always advance the pointer pointing to the shorter vertical bar
        if h_left < h_right:
            left += 1
        else:
            right -= 1

    return max_area


# ============================================================================
# 4. 3SUM PROBLEM (SORTING + DUAL POINTER SWEEP + PRUNING)
# ============================================================================

def three_sum(nums: List[int]) -> List[List[int]]:
    """
    Finds all unique triplets [nums[i], nums[j], nums[k]] that sum to 0.
    
    WHY SORTING + TWO POINTERS OVER HASH SET:
    Sorting the array upfront takes O(N log N). Once sorted, we fix an anchor element
    nums[i] and use two pointers (left, right) to find matching pairs in O(N).
    More importantly, sorting enables effortless O(1) duplicate skipping by checking
    adjacent values, preventing duplicate triplets without maintaining an expensive
    hash set of tuples.
    
    Time Complexity:  O(N^2) - O(N log N) sort + N passes of O(N) two-pointer search.
    Space Complexity: O(1) auxiliary space (excluding storage for the output list).
    """
    nums.sort()
    n = len(nums)
    triplets: List[List[int]] = []

    for i in range(n - 2):
        # Optimization 1: Since array is sorted, if anchor > 0, sum can never be 0
        if nums[i] > 0:
            break

        # Optimization 2: Skip identical anchor elements to prevent duplicate triplets
        if i > 0 and nums[i] == nums[i - 1]:
            continue

        target = -nums[i]
        left = i + 1
        right = n - 1

        while left < right:
            current_sum = nums[left] + nums[right]

            if current_sum == target:
                triplets.append([nums[i], nums[left], nums[right]])

                # Skip consecutive duplicate values at left pointer
                while left < right and nums[left] == nums[left + 1]:
                    left += 1
                # Skip consecutive duplicate values at right pointer
                while left < right and nums[right] == nums[right - 1]:
                    right -= 1

                left += 1
                right -= 1
            elif current_sum < target:
                left += 1
            else:
                right -= 1

    return triplets


# ============================================================================
# 5. DEMONSTRATION & VERIFICATION SUITE
# ============================================================================

def run_two_pointers_demo() -> None:
    print("=" * 75)
    print("DAY 16: TWO POINTERS ALGORITHMIC PATTERNS & IN-PLACE MECHANICS")
    print("=" * 75)

    # 1. Test Two Sum on Sorted Array
    print("\n[PART 1] Testing Two Sum on Sorted Array:")
    test_cases_two_sum = [
        ([2, 7, 11, 15], 9, (0, 1)),
        ([1, 2, 3, 4, 6], 10, (3, 4)),
        ([-5, -2, 0, 3, 8], 1, (1, 3)),
        ([1, 3, 5, 7], 100, None),
    ]

    for nums, target, expected in test_cases_two_sum:
        result = two_sum_sorted(nums, target)
        status = "PASSED" if result == expected else "FAILED"
        print(f"  - Array: {nums} | Target: {target:<3} -> Result: {result} [{status}]")
        assert result == expected

    # 2. Test In-Place Duplicate Removal
    print("\n[PART 2] Testing In-Place Deduplication & Compaction:")
    nums_to_compact = [0, 0, 1, 1, 1, 2, 2, 3, 3, 4]
    original_copy = list(nums_to_compact)
    unique_count = remove_duplicates_inplace(nums_to_compact)
    compacted_slice = nums_to_compact[:unique_count]

    print(f"  - Original Array : {original_copy}")
    print(f"  - Unique Count   : {unique_count}")
    print(f"  - Compacted Data : {compacted_slice}")
    assert unique_count == 5
    assert compacted_slice == [0, 1, 2, 3, 4]
    print("  - Status: IN-PLACE COMPACTION VERIFIED [PASSED]")

    # 3. Test Container With Most Water
    print("\n[PART 3] Testing Container With Most Water (Greedy Inward Shrinkage):")
    heights_samples = [
        ([1, 8, 6, 2, 5, 4, 8, 3, 7], 49),
        ([1, 1], 1),
        ([4, 3, 2, 1, 4], 16),
        ([1, 2, 1], 2),
    ]

    for heights, expected_area in heights_samples:
        calculated_area = container_with_most_water(heights)
        status = "PASSED" if calculated_area == expected_area else "FAILED"
        print(f"  - Heights: {heights} -> Max Area: {calculated_area} (Expected: {expected_area}) [{status}]")
        assert calculated_area == expected_area

    # 4. Test 3Sum Triplet Search
    print("\n[PART 4] Testing 3Sum with Duplicate Skipping:")
    nums_3sum = [-1, 0, 1, 2, -1, -4]
    result_triplets = three_sum(nums_3sum)
    expected_triplets = [[-1, -1, 2], [-1, 0, 1]]

    print(f"  - Input Array : {nums_3sum}")
    print(f"  - Triplets    : {result_triplets}")
    assert result_triplets == expected_triplets
    print("  - Status: 3SUM TRIPLETS AND DUPLICATE SKIPPING VERIFIED [PASSED]")

    # 5. Benchmark: Two Pointers vs Naive O(N^2) Nested Loop
    print("\n[PART 5] Performance Benchmark: Two Pointers vs Naive O(N^2) Search:")
    benchmark_size = 5000
    sorted_dataset = list(range(1, benchmark_size * 2, 2)) # [1, 3, 5, ..., 9999]
    search_target = sorted_dataset[-2] + sorted_dataset[-1]

    # Benchmark Naive O(N^2)
    start_time = time.perf_counter()
    naive_found = None
    for i in range(len(sorted_dataset)):
        for j in range(i + 1, len(sorted_dataset)):
            if sorted_dataset[i] + sorted_dataset[j] == search_target:
                naive_found = (i, j)
                break
        if naive_found:
            break
    naive_duration = (time.perf_counter() - start_time) * 1000.0

    # Benchmark Two Pointers O(N)
    start_time = time.perf_counter()
    tp_found = two_sum_sorted(sorted_dataset, search_target)
    tp_duration = (time.perf_counter() - start_time) * 1000.0

    print(f"  Dataset size: {benchmark_size} elements")
    print(f"  - Naive O(N^2)      Duration : {naive_duration:.2f} ms")
    print(f"  - Two Pointers O(N) Duration : {tp_duration:.4f} ms")
    speedup = naive_duration / (tp_duration if tp_duration > 0 else 0.001)
    print(f"  - Performance Speedup: ~{speedup:.1f}x faster with O(1) auxiliary memory")

    print("\n" + "=" * 75)
    print("ALL CORE TWO POINTERS ALGORITHMS PASSED AND VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_two_pointers_demo()
