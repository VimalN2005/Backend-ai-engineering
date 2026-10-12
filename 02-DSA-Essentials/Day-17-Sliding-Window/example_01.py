"""
Day 17: Example 01 - Core Sliding Window Algorithms & Mechanics

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-throughput microservices, streaming ingestion pipelines, and time-series
monitoring engines, recalculating metrics over continuous sequences from scratch
results in catastrophic O(N * K) or O(N^2) CPU degradation.

The Sliding Window pattern leverages incremental state updates:
    State[t] = State[t-1] + Incoming - Outgoing
This guarantees strictly linear O(N) time complexity and O(1) or O(K) space complexity,
enabling backend services to evaluate metrics over millions of data points per second.

This module implements:
1. Fixed-Size Sliding Window: Maximum sum of contiguous subarray of length K.
2. Dynamic-Size Sliding Window: Longest substring without repeating characters (with O(1) index leaps).
3. Minimum Size Subarray Sum: Minimal contiguous length satisfying sum >= target.
4. Monotonic Deque Sliding Window Maximum: Peak value per window of size K in O(N) time.
5. Algorithmic verification tests and benchmarks against naive recalculation.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Dict, List, Optional


# ============================================================================
# 1. FIXED-SIZE SLIDING WINDOW
# ============================================================================

def max_sum_subarray_fixed(nums: List[int], k: int) -> int:
    """
    Finds the maximum sum of any contiguous subarray of fixed size k.
    
    WHY INCREMENTAL COMPUTATION:
    Instead of slicing and computing sum(nums[i:i+k]) on every step (which costs O(k)
    per step, totaling O(N * k)), we compute the initial window once, then subtract
    the leaving element and add the entering element in O(1) constant time.
    
    Time Complexity:  O(N) - Single pass through the array.
    Space Complexity: O(1) - Two scalar integer registers.
    """
    n = len(nums)
    if n < k or k <= 0:
        return 0

    # Initialize first window sum
    window_sum = sum(nums[:k])
    max_sum = window_sum

    # Slide the window from index k to n - 1
    for right in range(k, n):
        # State transition: add incoming element, subtract outgoing element
        window_sum += nums[right] - nums[right - k]
        if window_sum > max_sum:
            max_sum = window_sum

    return max_sum


# ============================================================================
# 2. DYNAMIC SLIDING WINDOW: LONGEST SUBSTRING WITHOUT REPEATING CHARACTERS
# ============================================================================

def longest_substring_without_repeating(s: str) -> int:
    """
    Calculates the length of the longest contiguous substring without duplicate characters.
    
    WHY HASH MAP INDEX LEAPING:
    When a duplicate character is encountered at index 'right', standard two-pointer
    implementations increment 'left' one step at a time (O(2N)). By storing the most
    recent 0-based index of each character in a hash map, 'left' can jump directly
    past the previous occurrence:
        left = max(left, last_seen_index + 1)
    This guarantees an optimal single-pass traversal.
    
    Time Complexity:  O(N) - Each character is visited once by 'right'.
    Space Complexity: O(min(N, Sigma)) - Hash map size bounded by alphabet size Sigma.
    """
    char_index_map: Dict[str, int] = {}
    left = 0
    max_len = 0

    for right, char in enumerate(s):
        # If character was seen and its last index is inside the current window
        if char in char_index_map and char_index_map[char] >= left:
            # Leap left pointer directly past the previous occurrence
            left = char_index_map[char] + 1

        char_index_map[char] = right
        current_len = right - left + 1
        if current_len > max_len:
            max_len = current_len

    return max_len


# ============================================================================
# 3. DYNAMIC SLIDING WINDOW: MINIMUM SIZE SUBARRAY SUM
# ============================================================================

def min_subarray_len_target(target: int, nums: List[int]) -> int:
    """
    Finds the minimal length of a contiguous subarray of positive integers
    whose sum is greater than or equal to target.
    
    WHY EXPAND-THEN-GREEDILY-SHRINK:
    1. Expand 'right' to accumulate sum until condition (current_sum >= target) is met.
    2. Once met, greedily contract 'left' while the condition remains satisfied to
       find the local minimum length.
    Both pointers move strictly forward from 0 to N.
    
    Time Complexity:  O(N) - Each element enters the window once and leaves at most once.
    Space Complexity: O(1) - Constant auxiliary space.
    """
    left = 0
    current_sum = 0
    min_length = float("inf")

    for right in range(len(nums)):
        current_sum += nums[right]

        # While condition is satisfied, greedily shrink window from the left
        while current_sum >= target:
            window_size = right - left + 1
            if window_size < min_length:
                min_length = window_size

            current_sum -= nums[left]
            left += 1

    return 0 if min_length == float("inf") else int(min_length)


# ============================================================================
# 4. MONOTONIC DEQUE SLIDING WINDOW MAXIMUM
# ============================================================================

def max_sliding_window(nums: List[int], k: int) -> List[int]:
    """
    Returns the maximum element in each sliding window of size k moving from left to right.
    
    WHY MONOTONIC DEQUE OVER HEAP:
    A Max-Heap (heapq) requires O(N log K) time because stale elements leaving the window
    cannot be removed in O(1).
    A Monotonic Decreasing Deque stores indices of elements in strictly non-increasing order:
    1. Before inserting index 'i', pop all indices from the back whose values <= nums[i]
       (they can NEVER be the maximum in any future window containing nums[i]).
    2. Pop the front index if it has slid out of the window (front <= i - k).
    3. The front of the deque ALWAYS holds the maximum value for the current window!
    
    Time Complexity:  O(N) - Each index is pushed once and popped at most once.
    Space Complexity: O(K) - Deque holds at most K indices.
    """
    if not nums or k <= 0:
        return []

    # Stores indices in monotonically decreasing order of their corresponding values
    mono_deque: deque[int] = deque()
    result: List[int] = []

    for i, val in enumerate(nums):
        # 1. Evict elements that fell outside the left boundary of the window
        while mono_deque and mono_deque[0] <= i - k:
            mono_deque.popleft()

        # 2. Maintain monotonic decreasing property by popping smaller elements from back
        while mono_deque and nums[mono_deque[-1]] <= val:
            mono_deque.pop()

        mono_deque.append(i)

        # 3. Append the maximum value (front of deque) once first window is formed
        if i >= k - 1:
            result.append(nums[mono_deque[0]])

    return result


# ============================================================================
# 5. DEMONSTRATION & VERIFICATION SUITE
# ============================================================================

def run_sliding_window_demo() -> None:
    print("=" * 75)
    print("DAY 17: CORE SLIDING WINDOW ALGORITHMS & BENCHMARK SUITE")
    print("=" * 75)

    # 1. Test Fixed-Size Sliding Window
    print("\n[PART 1] Testing Fixed-Size Sliding Window (Max Sum Subarray):")
    test_cases_fixed = [
        ([2, 1, 5, 1, 3, 2], 3, 9),      # Subarray [5, 1, 3] = 9
        ([2, 3, 4, 1, 5], 2, 7),         # Subarray [3, 4] = 7
        ([1, 8, 30, -5, 20, 7], 3, 45),  # Subarray [30, -5, 20] = 45
        ([5], 1, 5),
    ]

    for arr, k, expected in test_cases_fixed:
        calculated = max_sum_subarray_fixed(arr, k)
        status = "PASSED" if calculated == expected else "FAILED"
        print(f"  - Array: {arr} | K: {k} -> Max Sum: {calculated} (Expected: {expected}) [{status}]")
        assert calculated == expected

    # 2. Test Longest Substring Without Repeating Characters
    print("\n[PART 2] Testing Longest Substring Without Repeating Characters:")
    test_cases_substr = [
        ("abcabcbb", 3),  # "abc" = 3
        ("bbbbb", 1),     # "b" = 1
        ("pwwkew", 3),    # "wke" = 3
        ("", 0),
        ("au", 2),
        ("abba", 2),     # Tests leaping logic past duplicate 'b'
    ]

    for s, expected_len in test_cases_substr:
        calculated_len = longest_substring_without_repeating(s)
        status = "PASSED" if calculated_len == expected_len else "FAILED"
        print(f"  - String: '{s:<8}' -> Longest Length: {calculated_len} (Expected: {expected_len}) [{status}]")
        assert calculated_len == expected_len

    # 3. Test Minimum Size Subarray Sum
    print("\n[PART 3] Testing Minimum Size Subarray Sum (Target Bound):")
    test_cases_min_sub = [
        (7, [2, 3, 1, 2, 4, 3], 2),       # [4, 3] = 7, len = 2
        (4, [1, 4, 4], 1),                # [4] = 4, len = 1
        (11, [1, 1, 1, 1, 1, 1, 1, 1], 0), # No subarray reaches 11
        (15, [1, 2, 3, 4, 5], 5),         # All elements needed
    ]

    for target, nums, expected_min in test_cases_min_sub:
        calculated_min = min_subarray_len_target(target, nums)
        status = "PASSED" if calculated_min == expected_min else "FAILED"
        print(f"  - Target: {target:<2} | Array: {nums} -> Min Len: {calculated_min} (Expected: {expected_min}) [{status}]")
        assert calculated_min == expected_min

    # 4. Test Monotonic Deque Sliding Window Maximum
    print("\n[PART 4] Testing Sliding Window Maximum (Monotonic Deque):")
    test_cases_max = [
        ([1, 3, -1, -3, 5, 3, 6, 7], 3, [3, 3, 5, 5, 6, 7]),
        ([1], 1, [1]),
        ([9, 11], 2, [11]),
        ([4, -2], 2, [4]),
        ([7, 2, 4], 2, [7, 4]),
    ]

    for nums, k, expected_list in test_cases_max:
        calculated_list = max_sliding_window(nums, k)
        status = "PASSED" if calculated_list == expected_list else "FAILED"
        print(f"  - Array: {nums} | K: {k}")
        print(f"    Window Maxima: {calculated_list} (Expected: {expected_list}) [{status}]")
        assert calculated_list == expected_list

    # 5. Performance Benchmark: Incremental Sliding Window vs Naive Slicing
    print("\n[PART 5] Performance Benchmark: Sliding Window O(N) vs Naive Recalculation O(N*K):")
    dataset_size = 50000
    window_k = 500
    benchmark_data = list(range(1, dataset_size + 1))

    # Benchmark Naive O(N * K) re-summing
    start_time = time.perf_counter()
    naive_max = 0
    for i in range(dataset_size - window_k + 1):
        current_s = sum(benchmark_data[i:i + window_k])
        if current_s > naive_max:
            naive_max = current_s
    naive_ms = (time.perf_counter() - start_time) * 1000.0

    # Benchmark Sliding Window O(N) incremental
    start_time = time.perf_counter()
    sw_max = max_sum_subarray_fixed(benchmark_data, window_k)
    sw_ms = (time.perf_counter() - start_time) * 1000.0

    print(f"  Dataset: {dataset_size} elements | Window K = {window_k}")
    print(f"  - Naive O(N * K) Re-Summing : {naive_ms:.2f} ms")
    print(f"  - Sliding Window O(N)       : {sw_ms:.2f} ms")
    speedup = naive_ms / (sw_ms if sw_ms > 0 else 0.001)
    print(f"  - Execution Speedup: ~{speedup:.1f}x faster with O(1) auxiliary memory")

    assert naive_max == sw_max

    print("\n" + "=" * 75)
    print("ALL CORE SLIDING WINDOW ALGORITHMS PASSED AND VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_sliding_window_demo()
