"""
Day 20: Example 01 - Core Monotonic Stack Algorithms & Performance Benchmarks

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
High-throughput telemetry ingestion pipelines (e.g. Prometheus, Datadog, OpenTelemetry)
frequently process millions of sequential metrics per second—such as request latencies,
GPU cluster temperatures, and server pool memory usages.

A frequent requirement is finding the next immediate metric spike, computing the time span
until a SLA threshold is exceeded, or finding the maximum continuous compute burst capacity.
A naive nested loop scanning rightward requires O(N^2) time, freezing real-time ingestion
services. The Monotonic Stack pattern prunes the search space by maintaining strict sorting
order in a LIFO stack, delivering guaranteed O(N) linear time and sub-millisecond execution.

This module implements:
1. `next_greater_element`: O(N) NGE lookup using a monotonic decreasing stack + hash map.
2. `daily_temperatures`: Span distance calculation (time-to-next-warmer-day) storing indices.
3. `next_greater_elements_circular`: Circular array NGE lookup via virtual 2N unrolling.
4. `largest_rectangle_in_histogram`: O(N) max contiguous area using monotonic increasing stack & sentinel flush.
5. High-volume comparative benchmark: O(N^2) naive scan vs O(N) monotonic stack.
"""

from __future__ import annotations

import random
import time
from typing import Dict, List, Optional, Tuple


# ============================================================================
# 1. NAIVE BASELINE (QUADRATIC O(N^2) REFERENCE)
# ============================================================================

def next_greater_element_naive(nums: List[int]) -> List[int]:
    """
    Finds the next greater element for each item using naive nested loops.
    
    WHY BENCHMARK THIS:
    Provides the ground-truth baseline to verify algorithmic correctness and
    demonstrate the catastrophic degradation of O(N^2) scans as N grows.
    
    Time Complexity:  O(N^2) - Scans up to N items for every element.
    Space Complexity: O(1) auxiliary (excluding the result array).
    """
    n = len(nums)
    result = [-1] * n
    for i in range(n):
        for j in range(i + 1, n):
            if nums[j] > nums[i]:
                result[i] = nums[j]
                break
    return result


# ============================================================================
# 2. NEXT GREATER ELEMENT I (MONOTONIC DECREASING STACK + HASH MAP)
# ============================================================================

def next_greater_element(nums1: List[int], nums2: List[int]) -> List[int]:
    """
    Finds the next greater element for every item in `nums1` based on its position in `nums2`.
    
    WHY MONOTONIC DECREASING:
    As we iterate through `nums2`, we maintain unresolved elements in decreasing order.
    When a larger element `num` arrives, it resolves ALL pending smaller items on the stack
    simultaneously in amortized O(1) time.
    
    Time Complexity:  O(N + M) where N = len(nums2) and M = len(nums1).
    Space Complexity: O(N) to store the mapping and stack.
    """
    nge_map: Dict[int, int] = {}
    stack: List[int] = []  # Stores values from nums2 in strictly decreasing order

    for num in nums2:
        # Invariant: Incoming 'num' is greater than stack[-1].
        # Therefore, 'num' is the immediate Next Greater Element for stack[-1].
        while stack and stack[-1] < num:
            smaller_val = stack.pop()
            nge_map[smaller_val] = num
        
        # Current element is now smaller than remaining stack elements (or stack is empty)
        stack.append(num)

    # Any elements remaining on stack have no greater element to their right
    while stack:
        nge_map[stack.pop()] = -1

    # Map queries from nums1 in O(1) each
    return [nge_map.get(x, -1) for x in nums1]


# ============================================================================
# 3. DAILY TEMPERATURES / LATENCY SPIKE SPAN (INDEX-BASED STACK)
# ============================================================================

def daily_temperatures(temperatures: List[int]) -> List[int]:
    """
    Computes the number of intervals until the next strictly warmer temperature.
    If no warmer temperature occurs in the future, the answer is 0.
    
    WHY STORE INDICES INSTEAD OF VALUES:
    Storing array indices `i` on the stack provides a dual capability:
    1. Access the value via `temperatures[i]` for monotonicity comparisons.
    2. Compute the exact distance (span) in O(1) via `current_idx - prev_idx`.
    
    Time Complexity:  O(N) - Every index is pushed once and popped at most once.
    Space Complexity: O(N) - Worst-case strictly decreasing input holds N indices.
    """
    n = len(temperatures)
    result = [0] * n
    stack: List[int] = []  # Stores indices in monotonic decreasing temperature order

    for current_idx, current_temp in enumerate(temperatures):
        # Resolve all previous days whose temperature is lower than today's
        while stack and temperatures[stack[-1]] < current_temp:
            prev_idx = stack.pop()
            result[prev_idx] = current_idx - prev_idx  # Distance in intervals
        
        stack.append(current_idx)

    # Indices left in stack have no warmer day in the future; result already initialized to 0
    return result


# ============================================================================
# 4. NEXT GREATER ELEMENT II: CIRCULAR BUFFER (VIRTUAL 2N UNROLLING)
# ============================================================================

def next_greater_elements_circular(nums: List[int]) -> List[int]:
    """
    Finds the next greater element in a circular array (the next element of the last
    element is the first element).
    
    WHY VIRTUAL 2N UNROLLING:
    Duplicating the physical array (`nums + nums`) allocates O(N) extra heap memory.
    Instead, we iterate over virtual indices `0 .. 2N - 1` and map to real indices
    via modulo arithmetic `i % N`.
    
    Time Complexity:  O(N) - Loops 2N times; at most 2N pushes and 2N pops.
    Space Complexity: O(N) - Stack holds at most N unresolved indices.
    """
    n = len(nums)
    result = [-1] * n
    stack: List[int] = []  # Stores real indices (0 <= idx < n)

    # Iterate through two full cycles to simulate circular wrapping
    for i in range(2 * n):
        real_idx = i % n
        current_val = nums[real_idx]

        # Monotonic decreasing stack: Resolve pending smaller values
        while stack and nums[stack[-1]] < current_val:
            prev_idx = stack.pop()
            result[prev_idx] = current_val

        # Only push indices during the first cycle (0 .. n-1).
        # In the second cycle (n .. 2n-1), we only resolve remaining pending indices.
        if i < n:
            stack.append(real_idx)

    return result


# ============================================================================
# 5. LARGEST RECTANGLE IN HISTOGRAM (SENTINEL FLUSH & BOUNDARY RESOLUTION)
# ============================================================================

def largest_rectangle_in_histogram(heights: List[int]) -> int:
    """
    Finds the area of the largest rectangle in a histogram.
    
    WHY MONOTONIC INCREASING STACK:
    A bar of height H can extend horizontally to the left until encountering a bar
    shorter than H, and to the right until encountering a bar shorter than H.
    An increasing stack allows us to find both the Left Smaller Element (the bar
    immediately beneath in the stack) and the Right Smaller Element (the current incoming bar).
    
    WHY THE SENTINEL VALUE [0]:
    Appending a dummy height of 0 at the end guarantees that EVERY remaining bar on the
    stack will be strictly taller than 0, forcing a complete flush without needing
    duplicate post-loop cleanup code.
    
    Width Formula Derivation:
    - Right boundary = current index `i` (exclusive).
    - Left boundary  = `stack[-1]` (exclusive) if stack non-empty, else -1.
    - Width = `i - stack[-1] - 1` (or `i` if stack is empty).
    
    Time Complexity:  O(N) - Single pass over heights + sentinel.
    Space Complexity: O(N) - Stack stores indices.
    """
    # Append 0 sentinel to flush all pending bars at the end of traversal
    extended_heights = heights + [0]
    stack: List[int] = []  # Stores indices in monotonic increasing height order
    max_area = 0

    for i, current_h in enumerate(extended_heights):
        # When current bar is shorter than the bar at stack top, the bar at stack top
        # cannot extend any further to the right. We must pop and calculate its area!
        while stack and extended_heights[stack[-1]] > current_h:
            popped_idx = stack.pop()
            h = extended_heights[popped_idx]
            
            # If stack is empty, popped bar was the smallest seen so far; it spans from 0 to i-1.
            # Otherwise, it spans between stack[-1] + 1 and i - 1.
            w = i if not stack else i - stack[-1] - 1
            area = h * w
            if area > max_area:
                max_area = area

        stack.append(i)

    return max_area


# ============================================================================
# 6. VERIFICATION SUITE & COMPREHENSIVE BENCHMARKS
# ============================================================================

def run_tests() -> None:
    """Validates edge cases and standard test fixtures across all implementations."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR MONOTONIC STACK ALGORITHMS")
    print("======================================================================")

    # 1. Next Greater Element I
    n1 = [4, 1, 2]
    n2 = [1, 3, 4, 2]
    res_nge = next_greater_element(n1, n2)
    assert res_nge == [-1, 3, -1], f"Expected [-1, 3, -1], got {res_nge}"
    print("[PASS] next_greater_element standard queries verified.")

    # 2. Daily Temperatures
    temps = [73, 74, 75, 71, 69, 72, 76, 73]
    expected_temps = [1, 1, 4, 2, 1, 1, 0, 0]
    res_temps = daily_temperatures(temps)
    assert res_temps == expected_temps, f"Expected {expected_temps}, got {res_temps}"
    print("[PASS] daily_temperatures latency/weather spans verified.")

    # 3. Circular Next Greater Element II
    circ = [1, 2, 1]
    expected_circ = [2, -1, 2]
    res_circ = next_greater_elements_circular(circ)
    assert res_circ == expected_circ, f"Expected {expected_circ}, got {res_circ}"

    circ2 = [1, 2, 3, 4, 3]
    expected_circ2 = [2, 3, 4, -1, 4]
    res_circ2 = next_greater_elements_circular(circ2)
    assert res_circ2 == expected_circ2, f"Expected {expected_circ2}, got {res_circ2}"
    print("[PASS] next_greater_elements_circular modulo wrapping verified.")

    # 4. Largest Rectangle in Histogram
    hist1 = [2, 1, 5, 6, 2, 3]
    assert largest_rectangle_in_histogram(hist1) == 10, "Failed for standard histogram"

    hist2 = [2, 4]
    assert largest_rectangle_in_histogram(hist2) == 4, "Failed for [2, 4]"

    hist_flat = [5, 5, 5, 5]
    assert largest_rectangle_in_histogram(hist_flat) == 20, "Failed for flat histogram"

    hist_descending = [6, 5, 4, 3, 2, 1]
    assert largest_rectangle_in_histogram(hist_descending) == 12, "Failed for descending histogram"
    print("[PASS] largest_rectangle_in_histogram sentinel flush verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Demonstrates O(N^2) quadratic vs O(N) monotonic stack execution time."""
    print("======================================================================")
    print("HIGH-VOLUME PERFORMANCE BENCHMARK: O(N^2) NAIVE VS O(N) MONOTONIC")
    print("======================================================================")

    data_size = 10_000
    random.seed(42)
    test_data = [random.randint(1, 1_000_000) for _ in range(data_size)]

    print(f"Dataset Size: {data_size:,} random telemetry latency samples.")

    # Benchmark Naive O(N^2)
    start_naive = time.perf_counter()
    naive_result = next_greater_element_naive(test_data)
    elapsed_naive = time.perf_counter() - start_naive
    print(f"Naive Nested Loop (O(N^2)):     {elapsed_naive * 1000:8.2f} ms")

    # Benchmark Monotonic Stack O(N)
    start_mono = time.perf_counter()
    mono_result = daily_temperatures(test_data)
    elapsed_mono = time.perf_counter() - start_mono
    print(f"Monotonic Stack (O(N)):          {elapsed_mono * 1000:8.2f} ms")

    speedup = elapsed_naive / elapsed_mono if elapsed_mono > 0 else float("inf")
    print(f"\nResulting Speedup Factor:        {speedup:8.1f}x faster with Monotonic Stack!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
