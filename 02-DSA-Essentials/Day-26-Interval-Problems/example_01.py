"""
Day 26: Example 01 - Core Interval Algorithms, Sweep-Line Mechanics & Benchmarks

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-scale infrastructure (e.g. Prometheus metrics compaction, AWS Security Group CIDR
merging, Kubernetes worker node reservation windows, and Google Calendar booking engines),
ranges of time and IP addresses must be evaluated for overlaps, gaps, and concurrent capacities.

A naive approach comparing every interval against every other interval requires O(N^2) time,
freezing backend microservices on moderate workloads. By applying Chronological Sorting Invariants
and the Sweep-Line Algorithm, we reduce computational complexity to O(N log N) or O(N),
enabling real-time stream processing of millions of intervals per second.

This module implements:
1. `merge_intervals`: O(N log N) overlapping range merger.
2. `insert_interval`: O(N) three-phase linear insertion and merge.
3. `interval_intersection`: O(N + M) two-pointer overlap intersection between disjoint lists.
4. `min_meeting_rooms_heap` & `min_meeting_rooms_sweepline`: Peak concurrent capacity sizing.
5. `erase_overlap_intervals`: Greedy Interval Scheduling Maximization (ISMP) sorting by end time.
6. High-volume comparative benchmark: O(N^2) naive scan vs O(N log N) chronological sort on 20,000 intervals.
"""

from __future__ import annotations

import heapq
import random
import time
from typing import List, Tuple


# ============================================================================
# 1. MERGE OVERLAPPING INTERVALS (CHRONOLOGICAL SORTING)
# ============================================================================

def merge_intervals(intervals: List[List[int]]) -> List[List[int]]:
    """
    Merges all overlapping intervals into a minimal non-overlapping set.
    
    WHY SORT BY START TIME:
    Sorting ensures that for any interval i, all intervals that could possibly overlap
    with it have start times <= intervals[i][0]. We only need to check and extend
    the last merged interval in O(1) time.
    
    Time Complexity:  O(N log N) due to initial sort.
    Space Complexity: O(N) to store the merged result list.
    """
    if not intervals:
        return []

    # Sort strictly by start time
    intervals.sort(key=lambda x: x[0])
    merged: List[List[int]] = [intervals[0]]

    for curr_start, curr_end in intervals[1:]:
        prev_start, prev_end = merged[-1]

        # Overlap detected: current starts before or exactly when previous ends
        if curr_start <= prev_end:
            # Crucial: Use max() to handle complete containment where curr_end < prev_end!
            merged[-1][1] = max(prev_end, curr_end)
        else:
            # Disjoint interval: start a new interval segment
            merged.append([curr_start, curr_end])

    return merged


# ============================================================================
# 2. INSERT INTERVAL (THREE-PHASE LINEAR SWEEP)
# ============================================================================

def insert_interval(intervals: List[List[int]], new_interval: List[int]) -> List[List[int]]:
    """
    Inserts a new interval into a sorted non-overlapping list and merges if necessary.
    
    WHY THREE-PHASE LINEAR SWEEP:
    Instead of inserting and sorting in O(N log N), we take advantage of the pre-sorted
    order to perform a single linear O(N) pass across three distinct phases.
    
    Time Complexity:  O(N) single pass.
    Space Complexity: O(N) result list.
    """
    result: List[List[int]] = []
    i = 0
    n = len(intervals)
    new_start, new_end = new_interval

    # Phase 1: Add all intervals that end strictly before new_interval starts
    while i < n and intervals[i][1] < new_start:
        result.append(intervals[i])
        i += 1

    # Phase 2: Merge all overlapping intervals with new_interval
    while i < n and intervals[i][0] <= new_end:
        new_start = min(new_start, intervals[i][0])
        new_end = max(new_end, intervals[i][1])
        i += 1

    result.append([new_start, new_end])

    # Phase 3: Add all remaining intervals that start strictly after new_interval ends
    while i < n:
        result.append(intervals[i])
        i += 1

    return result


# ============================================================================
# 3. INTERVAL LIST INTERSECTIONS (TWO POINTERS)
# ============================================================================

def interval_intersection(first_list: List[List[int]], second_list: List[List[int]]) -> List[List[int]]:
    """
    Finds the intersection of two sorted lists of disjoint intervals in O(N + M) time.
    
    POINTER ADVANCEMENT INVARIANT:
    Always advance the pointer whose interval FINISHES EARLIER.
    The interval that finishes later could potentially intersect with future intervals
    in the other list!
    """
    i = 0
    j = 0
    result: List[List[int]] = []

    while i < len(first_list) and j < len(second_list):
        a_start, a_end = first_list[i]
        b_start, b_end = second_list[j]

        # Calculate overlap window
        start = max(a_start, b_start)
        end = min(a_end, b_end)

        if start <= end:
            result.append([start, end])

        # Advance the pointer with earlier end time
        if a_end < b_end:
            i += 1
        else:
            j += 1

    return result


# ============================================================================
# 4. MEETING ROOMS II: MINIMUM CONCURRENT CAPACITY (MIN-HEAP & SWEEP-LINE)
# ============================================================================

def min_meeting_rooms_heap(intervals: List[List[int]]) -> int:
    """
    Calculates minimum rooms required using a Min-Heap of end times.
    
    Time Complexity:  O(N log N)
    Space Complexity: O(N)
    """
    if not intervals:
        return 0

    # Sort meetings by start time
    intervals.sort(key=lambda x: x[0])

    # Min-Heap stores the end times of active rooms
    room_heap: List[int] = []
    heapq.heappush(room_heap, intervals[0][1])

    for start, end in intervals[1:]:
        # If the earliest ending meeting finished before or when current starts, reuse room
        if room_heap[0] <= start:
            heapq.heappop(room_heap)

        # Allocate room
        heapq.heappush(room_heap, end)

    return len(room_heap)


def min_meeting_rooms_sweepline(intervals: List[List[int]]) -> int:
    """
    Calculates minimum rooms required using the Sweep-Line Algorithm.
    
    TIE-BREAKING INVARIANT:
    If meeting A ends at time T and meeting B starts at time T, the END event (-1)
    must be processed before the START event (+1) for half-open intervals.
    Using (-1) for end and (+1) for start handles this naturally in tuple sorting.
    """
    events: List[Tuple[int, int]] = []
    for start, end in intervals:
        events.append((start, 1))   # Start: +1 room
        events.append((end, -1))    # End:   -1 room

    # Sort by timestamp. If timestamps tie, -1 sorts before +1!
    events.sort(key=lambda x: (x[0], x[1]))

    max_rooms = 0
    current_rooms = 0

    for _, delta in events:
        current_rooms += delta
        max_rooms = max(max_rooms, current_rooms)

    return max_rooms


# ============================================================================
# 5. ERASE OVERLAP INTERVALS (GREEDY SCHEDULING MAXIMIZATION)
# ============================================================================

def erase_overlap_intervals(intervals: List[List[int]]) -> int:
    """
    Finds the minimum number of intervals to remove to make the rest non-overlapping.
    
    WHY SORT BY END TIME:
    Greedy choice property: To leave the maximum possible room for remaining intervals,
    we must always pick the interval that FINISHES EARLIEST.
    
    Time Complexity:  O(N log N)
    Space Complexity: O(1) auxiliary.
    """
    if not intervals:
        return 0

    # Sort strictly by END time!
    intervals.sort(key=lambda x: x[1])

    retained_count = 1
    last_end = intervals[0][1]

    for start, end in intervals[1:]:
        if start >= last_end:
            # Compatible interval: retain it
            retained_count += 1
            last_end = end

    return len(intervals) - retained_count


# ============================================================================
# 6. VERIFICATION SUITE & COMPREHENSIVE BENCHMARK
# ============================================================================

def run_tests() -> None:
    """Validates edge cases and standard fixtures across all algorithms."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR INTERVAL ALGORITHMS")
    print("======================================================================")

    # 1. Merge Intervals
    assert merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]
    assert merge_intervals([[1, 4], [4, 5]]) == [[1, 5]]
    assert merge_intervals([[1, 4], [2, 3]]) == [[1, 4]]  # Complete containment
    assert merge_intervals([]) == []
    print("[PASS] merge_intervals verified.")

    # 2. Insert Interval
    assert insert_interval([[1, 3], [6, 9]], [2, 5]) == [[1, 5], [6, 9]]
    assert insert_interval([[1, 2], [3, 5], [6, 7], [8, 10], [12, 16]], [4, 8]) == [[1, 2], [3, 10], [12, 16]]
    assert insert_interval([], [5, 7]) == [[5, 7]]
    assert insert_interval([[1, 5]], [6, 8]) == [[1, 5], [6, 8]]
    print("[PASS] insert_interval verified.")

    # 3. Interval Intersection
    l1 = [[0, 2], [5, 10], [13, 23], [24, 25]]
    l2 = [[1, 5], [8, 12], [15, 24], [25, 26]]
    expected_inter = [[1, 2], [5, 5], [8, 10], [15, 23], [24, 24], [25, 25]]
    assert interval_intersection(l1, l2) == expected_inter
    print("[PASS] interval_intersection verified.")

    # 4. Meeting Rooms II
    meetings1 = [[0, 30], [5, 10], [15, 20]]
    assert min_meeting_rooms_heap(meetings1) == 2
    assert min_meeting_rooms_sweepline(meetings1) == 2

    meetings2 = [[7, 10], [2, 4]]
    assert min_meeting_rooms_heap(meetings2) == 1
    assert min_meeting_rooms_sweepline(meetings2) == 1

    # Adjacent meetings (end == start): should reuse room
    meetings3 = [[1, 5], [5, 10], [10, 15]]
    assert min_meeting_rooms_heap(meetings3) == 1
    assert min_meeting_rooms_sweepline(meetings3) == 1
    print("[PASS] min_meeting_rooms (Heap & Sweep-Line) verified.")

    # 5. Erase Overlap Intervals
    assert erase_overlap_intervals([[1, 2], [2, 3], [3, 4], [1, 3]]) == 1
    assert erase_overlap_intervals([[1, 2], [1, 2], [1, 2]]) == 2
    assert erase_overlap_intervals([[1, 2], [2, 3]]) == 0
    print("[PASS] erase_overlap_intervals greedy scheduling verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Benchmarks O(N log N) chronological merge sort vs O(N^2) pairwise comparisons."""
    print("======================================================================")
    print("HIGH-VOLUME BENCHMARK: O(N LOG N) MERGE SORT VS O(N^2) PAIRWISE SCAN")
    print("======================================================================")

    data_size = 20_000
    print(f"Dataset Size: {data_size:,} random time-series intervals.")

    random.seed(42)
    test_intervals = []
    for _ in range(data_size):
        start = random.randint(1, 1_000_000)
        duration = random.randint(10, 500)
        test_intervals.append([start, start + duration])

    # Benchmark O(N log N) Merge Sort
    start_fast = time.perf_counter()
    merged_res = merge_intervals(list(test_intervals))
    elapsed_fast = time.perf_counter() - start_fast
    print(f"Chronological Merge (O(N log N)):     {elapsed_fast * 1000:8.2f} ms")

    # Benchmark Simulated O(N^2) Pairwise Overlap Matrix (limit to first 3,000 for safety)
    sample_size = 3_000
    sample_subset = test_intervals[:sample_size]
    start_slow = time.perf_counter()
    pairwise_overlaps = 0
    for i in range(sample_size):
        for j in range(i + 1, sample_size):
            if not (sample_subset[i][1] < sample_subset[j][0] or sample_subset[j][1] < sample_subset[i][0]):
                pairwise_overlaps += 1
    elapsed_slow = time.perf_counter() - start_slow

    # Extrapolate quadratic time for full 20,000 items
    projected_quadratic_time = elapsed_slow * ((data_size / sample_size) ** 2)
    speedup = projected_quadratic_time / elapsed_fast if elapsed_fast > 0 else float("inf")

    print(f"Pairwise Scan (Projected O(N^2)):    {projected_quadratic_time * 1000:8.2f} ms")
    print(f"Speedup Factor with Chronological Sort: {speedup:8.1f}x faster!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
