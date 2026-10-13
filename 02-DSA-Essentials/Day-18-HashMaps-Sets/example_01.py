"""
Day 18: Example 01 - Core HashMap & Set Algorithms & Performance Benchmarks

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
HashMaps and Sets provide the empirical foundation of high-speed data retrieval in
backend platforms. From primary key caches to token normalization in AI tokenizers,
understanding how to construct optimal hash keys and leverage prefix sums separates
fragile O(N^2) algorithms from scalable O(N) enterprise services.

Furthermore, naive operations like `item in my_list` introduce hidden O(N) linear
scans that degrade database throughput. Replacing list lookups with Hash Sets yields
dramatic O(1) speedups exceeding 1,000x on production data volumes.

This module implements:
1. Group Anagrams: Comparing character sorting key O(K log K) vs frequency tuple key O(K).
2. Subarray Sum Equals K: Prefix sum + hash map frequency lookup across positive/negative values.
3. Longest Consecutive Sequence: Set boundary expansion in strictly linear O(N) time.
4. Two Sum (Unsorted) with Hash Map complement index lookup in O(N) time.
5. High-volume performance benchmark: Hash Set O(1) membership vs List O(N) scan.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


# ============================================================================
# 1. GROUP ANAGRAMS (FREQUENCY TUPLE VS SORTED KEY)
# ============================================================================

def group_anagrams_sorted(strs: List[str]) -> List[List[str]]:
    """
    Groups anagrams using sorted character strings as dictionary keys.
    
    Time Complexity:  O(N * K log K) where N is string count, K is max string length.
    Space Complexity: O(N * K) to store the grouped output.
    """
    groups: Dict[str, List[str]] = defaultdict(list)
    for s in strs:
        # Canonical key: sort characters alphabetically
        key = "".join(sorted(s))
        groups[key].append(s)
    return list(groups.values())


def group_anagrams_frequency(strs: List[str]) -> List[List[str]]:
    """
    Groups anagrams using a 26-element character frequency tuple as dictionary key.
    
    WHY FREQUENCY TUPLES OVER SORTING:
    Sorting strings takes O(K log K). For large strings (e.g. document paragraphs,
    token sequences), sorting is computationally expensive.
    Using an immutable 26-integer tuple avoids sorting entirely, running in strictly
    linear O(K) time per string.
    
    Time Complexity:  O(N * K) - Optimal linear time relative to total character count.
    Space Complexity: O(N * K) - Groups storage.
    """
    groups: Dict[Tuple[int, ...], List[str]] = defaultdict(list)
    for s in strs:
        # 26 buckets for lowercase English letters
        count = [0] * 26
        for char in s:
            count[ord(char) - ord("a")] += 1
        # Convert mutable list to immutable hashable tuple
        groups[tuple(count)].append(s)
    return list(groups.values())


# ============================================================================
# 2. SUBARRAY SUM EQUALS K (PREFIX SUM + HASH MAP)
# ============================================================================

def subarray_sum_equals_k(nums: List[int], k: int) -> int:
    """
    Finds the total number of continuous subarrays whose sum equals k.
    
    WHY PREFIX SUMS + HASH MAP OVER SLIDING WINDOW:
    Sliding Window requires that expanding the window monotonically increases the sum.
    If the array contains negative numbers (e.g. [-1, 2, -1]), that invariant breaks!
    By maintaining a running prefix sum P[i]:
        Sum(i, j) = P[j] - P[i-1] = k  ==>  P[i-1] = P[j] - k
    We record historical prefix sum counts in a hash map. At each step, checking
    if (running_sum - k) exists in the map provides the exact match count in O(1) time.
    
    Time Complexity:  O(N) - Single pass through nums.
    Space Complexity: O(N) - Hash map storing up to N unique prefix sums.
    """
    # Base case: A prefix sum of 0 has occurred once before the array begins (empty prefix)
    prefix_counts: Dict[int, int] = {0: 1}
    running_sum = 0
    total_matches = 0

    for x in nums:
        running_sum += x
        complement = running_sum - k

        # If (running_sum - k) was observed, all those prefix boundaries form valid subarrays
        if complement in prefix_counts:
            total_matches += prefix_counts[complement]

        # Record occurrence of current prefix sum
        prefix_counts[running_sum] = prefix_counts.get(running_sum, 0) + 1

    return total_matches


# ============================================================================
# 3. LONGEST CONSECUTIVE SEQUENCE (HASH SET BOUNDARY EXPANSION)
# ============================================================================

def longest_consecutive_sequence(nums: List[int]) -> int:
    """
    Finds the length of the longest consecutive elements sequence in O(N) time.
    
    WHY THE (num - 1) NOT IN SET INVARIANT:
    Sorting takes O(N log N). To achieve O(N), we insert all numbers into a Hash Set.
    Crucial optimization: We ONLY begin counting consecutive numbers if 'num' is the
    START of a sequence (i.e. num - 1 is NOT in the set).
    If (num - 1) is present, 'num' is an interior element and we skip it in O(1) time.
    This guarantees each number is visited at most twice across the entire execution.
    
    Time Complexity:  O(N) - Linear set construction and linear element traversals.
    Space Complexity: O(N) - Hash Set storage.
    """
    if not nums:
        return 0

    num_set = set(nums)
    max_streak = 0

    for num in num_set:
        # Check if num is the start of a sequence
        if (num - 1) not in num_set:
            current_num = num
            current_streak = 1

            # Expand sequence forward in O(1) set lookups
            while (current_num + 1) in num_set:
                current_num += 1
                current_streak += 1

            if current_streak > max_streak:
                max_streak = current_streak

    return max_streak


# ============================================================================
# 4. TWO SUM (UNSORTED) WITH HASH MAP COMPLEMENT LOOKUP
# ============================================================================

def two_sum_hashmap(nums: List[int], target: int) -> Optional[Tuple[int, int]]:
    """
    Finds the indices of two numbers that add up to target in an unsorted list.
    
    Time Complexity:  O(N) - Single pass with O(1) average dictionary lookups.
    Space Complexity: O(N) - Storage for value -> index map.
    """
    seen: Dict[int, int] = {}  # Maps value -> index
    for idx, val in enumerate(nums):
        complement = target - val
        if complement in seen:
            return seen[complement], idx
        seen[val] = idx
    return None


# ============================================================================
# 5. DEMONSTRATION & VERIFICATION SUITE
# ============================================================================

def run_hashmap_algorithms_demo() -> None:
    print("=" * 75)
    print("DAY 18: CORE HASHMAP & SET ALGORITHMIC PATTERNS & BENCHMARKS")
    print("=" * 75)

    # 1. Test Group Anagrams
    print("\n[PART 1] Testing Group Anagrams (Sorted Key vs Frequency Tuple):")
    words_input = ["eat", "tea", "tan", "ate", "nat", "bat"]

    groups_sorted = group_anagrams_sorted(words_input)
    groups_freq = group_anagrams_frequency(words_input)

    # Sort inner and outer lists to compare equivalence
    normalized_sorted = sorted([sorted(g) for g in groups_sorted])
    normalized_freq = sorted([sorted(g) for g in groups_freq])

    print(f"  - Input Words        : {words_input}")
    print(f"  - Groups (Frequency) : {groups_freq}")
    assert normalized_sorted == normalized_freq
    assert len(groups_freq) == 3
    print("  - Status: ANAGRAM GROUPING EQUIVALENCE VERIFIED [PASSED]")

    # 2. Test Subarray Sum Equals K (With Positive, Negative, and Zero Values)
    print("\n[PART 2] Testing Subarray Sum Equals K (Prefix Sums + Hash Map):")
    test_cases_sub = [
        ([1, 1, 1], 2, 2),                # [1, 1] at (0..1) and (1..2)
        ([1, 2, 3], 3, 2),                # [1, 2] and [3]
        ([1, -1, 0], 0, 3),               # [1, -1], [0], and [1, -1, 0]
        ([3, 4, 7, -2, 2, 1, 4], 7, 4),   # Multiple positive/negative cancellations
        ([1], 0, 0),
    ]

    for nums, k, expected_count in test_cases_sub:
        count = subarray_sum_equals_k(nums, k)
        status = "PASSED" if count == expected_count else "FAILED"
        print(f"  - Array: {nums} | K = {k:<2} -> Matches: {count} (Expected: {expected_count}) [{status}]")
        assert count == expected_count

    # 3. Test Longest Consecutive Sequence
    print("\n[PART 3] Testing Longest Consecutive Sequence in O(N) Time:")
    test_cases_seq = [
        ([100, 4, 200, 1, 3, 2], 4),      # [1, 2, 3, 4] -> length 4
        ([0, 3, 7, 2, 5, 8, 4, 6, 0, 1], 9), # [0, 1, 2, 3, 4, 5, 6, 7, 8] -> length 9
        ([], 0),
        ([5], 1),
        ([10, 20, 30], 1),
    ]

    for nums, expected_streak in test_cases_seq:
        streak = longest_consecutive_sequence(nums)
        status = "PASSED" if streak == expected_streak else "FAILED"
        print(f"  - Array: {nums} -> Longest Streak: {streak} (Expected: {expected_streak}) [{status}]")
        assert streak == expected_streak

    # 4. Test Two Sum Unsorted
    print("\n[PART 4] Testing Two Sum Unsorted via Hash Map:")
    nums_two_sum = [3, 2, 4]
    target_two_sum = 6
    pair = two_sum_hashmap(nums_two_sum, target_two_sum)
    print(f"  - Array: {nums_two_sum} | Target: {target_two_sum} -> Indices: {pair} (Values: {nums_two_sum[pair[0]]}, {nums_two_sum[pair[1]]})")
    assert pair == (1, 2)
    print("  - Status: TWO SUM INDICES VERIFIED [PASSED]")

    # 5. Performance Benchmark: Hash Set O(1) Lookup vs List O(N) Scan
    print("\n[PART 5] Performance Benchmark: Hash Set O(1) vs List O(N) Membership Check:")
    benchmark_size = 20000
    dataset_list = list(range(benchmark_size))
    dataset_set = set(dataset_list)
    query_target = benchmark_size - 1  # Worst-case item at the end of the list

    # Benchmark List Membership: O(N)
    start_time = time.perf_counter()
    for _ in range(500):
        _ = query_target in dataset_list
    list_ms = (time.perf_counter() - start_time) * 1000.0

    # Benchmark Set Membership: O(1)
    start_time = time.perf_counter()
    for _ in range(500):
        _ = query_target in dataset_set
    set_ms = (time.perf_counter() - start_time) * 1000.0

    print(f"  Dataset Size: {benchmark_size:,} elements | 500 Membership Queries:")
    print(f"  - List Search (O(N) linear scan) : {list_ms:.2f} ms")
    print(f"  - Set Search (O(1) hash lookup)  : {set_ms:.4f} ms")
    speedup = list_ms / (set_ms if set_ms > 0 else 0.0001)
    print(f"  - Hash Table Speedup Advantage   : ~{speedup:,.1f}x faster execution!")

    print("\n" + "=" * 75)
    print("ALL CORE HASHMAP & SET ALGORITHMS PASSED AND VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_hashmap_algorithms_demo()
