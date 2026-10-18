"""
Day 25: Example 01 - Core Heap Algorithms, Top-K Filtering & Stream Median Finder

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-throughput services (e.g. tracking Top-100 slowest API endpoints across 1,000,000
requests, or sampling Top-K token probabilities in LLM generation), sorting the entire
dataset takes O(N log N) time and gigabytes of memory.

Maintaining a bounded Min-Heap of size K reduces the complexity to strictly O(N log K) time
and O(K) space. When K << N (e.g. K = 50, N = 1,000,000), a bounded heap completes in
milliseconds with virtually zero memory overhead.

Furthermore, real-time telemetry pipelines often require dynamic median tracking (P50 latency).
The Dual-Heap pattern maintains two balanced heaps, answering median queries in strict O(1) time.

This module implements:
1. `find_k_largest_elements`: Bounded Min-Heap of size K using `heappushpop()`.
2. `top_k_frequent_words`: Frequency ranking with deterministic tie-breaking mechanics.
3. `MedianFinder`: Balanced Dual-Heap architecture (Max-Heap + Min-Heap) for O(1) median retrieval.
4. `merge_k_sorted_arrays`: K-way multi-stream merger in O(N log K) time.
5. High-volume benchmark: Full sorting O(N log N) vs Bounded Min-Heap O(N log K) on 1,000,000 items.
"""

from __future__ import annotations

import heapq
import random
import time
from collections import Counter
from typing import Any, List, Optional, Tuple


# ============================================================================
# 1. TOP-K LARGEST ELEMENTS (BOUNDED MIN-HEAP PATTERN)
# ============================================================================

def find_k_largest_elements(nums: List[int], k: int) -> List[int]:
    """
    Finds the K largest elements in nums in O(N log K) time and O(K) space.
    
    WHY MIN-HEAP FOR LARGEST:
    The root of a Min-Heap (heap[0]) is always the SMALLEST of the currently retained items.
    If an incoming element is larger than heap[0], it belongs in the top-K; `heappushpop`
    evicts the old threshold and inserts the new candidate in a single O(log K) pass.
    """
    if k <= 0 or not nums:
        return []
    if k >= len(nums):
        return sorted(nums, reverse=True)

    # Initialize heap with the first K elements
    min_heap = nums[:k]
    heapq.heapify(min_heap)  # O(K) linear heapify

    # Stream through remaining elements
    for num in nums[k:]:
        if num > min_heap[0]:
            heapq.heappushpop(min_heap, num)  # Single sift-down pass

    # Return sorted descending
    return sorted(min_heap, reverse=True)


# ============================================================================
# 2. TOP-K FREQUENT WORDS WITH TIE-BREAKING
# ============================================================================

class WordFreqItem:
    """
    Wrapper class to enforce custom comparison for heap storage:
    Higher frequency is prioritized; if frequencies tie, lexicographically smaller word wins.
    """
    __slots__ = ("word", "freq")

    def __init__(self, word: str, freq: int) -> None:
        self.word = word
        self.freq = freq

    def __lt__(self, other: WordFreqItem) -> bool:
        # Min-Heap order: We want to evict LOWEST frequency first.
        # If frequencies match, we want to evict LEXICOGRAPHICALLY LARGER word first.
        if self.freq != other.freq:
            return self.freq < other.freq
        return self.word > other.word


def top_k_frequent_words(words: List[str], k: int) -> List[str]:
    """
    Returns the K most frequent words sorted by frequency (descending) and word (ascending).
    
    Time Complexity:  O(N + M log K) where N = total words, M = unique words.
    Space Complexity: O(M) for counter + O(K) for heap.
    """
    counts = Counter(words)
    min_heap: List[WordFreqItem] = []

    for word, freq in counts.items():
        item = WordFreqItem(word, freq)
        if len(min_heap) < k:
            heapq.heappush(min_heap, item)
        elif item > min_heap[0]:  # Notice > comparison based on __lt__ logic
            heapq.heappushpop(min_heap, item)

    # Reconstruct result sorted from highest to lowest
    result = []
    while min_heap:
        result.append(heapq.heappop(min_heap).word)

    return result[::-1]


# ============================================================================
# 3. DUAL-HEAP CONTINUOUS MEDIAN FINDER (STREAMING P50 METRIC)
# ============================================================================

class MedianFinder:
    """
    Maintains a continuous running median over an unbounded stream of numbers.
    
    DUAL-HEAP INVARIANTS:
    1. `max_heap` stores the smaller half of numbers (inverted keys: -val).
    2. `min_heap` stores the larger half of numbers (normal keys: val).
    3. Balance invariant: len(max_heap) == len(min_heap) or len(max_heap) == len(min_heap) + 1.
    4. Ordering invariant: Every item in max_heap <= Every item in min_heap.
    
    Time Complexity:
        add_num:     O(log N)
        find_median: O(1)
    Space Complexity: O(N)
    """

    def __init__(self) -> None:
        self._max_heap: List[float] = []  # Stores smaller half (negated)
        self._min_heap: List[float] = []  # Stores larger half (normal)

    def add_num(self, num: float) -> None:
        """Adds a number into the data structure in O(log N) time."""
        # Step 1: Push to max_heap (smaller half)
        heapq.heappush(self._max_heap, -num)

        # Step 2: Ensure all elements in max_heap <= all elements in min_heap
        if self._min_heap and (-self._max_heap[0] > self._min_heap[0]):
            val_to_move = -heapq.heappop(self._max_heap)
            heapq.heappush(self._min_heap, val_to_move)

        # Step 3: Enforce size balance: len(max_heap) >= len(min_heap)
        if len(self._max_heap) < len(self._min_heap):
            val_to_move = heapq.heappop(self._min_heap)
            heapq.heappush(self._max_heap, -val_to_move)
        elif len(self._max_heap) > len(self._min_heap) + 1:
            val_to_move = -heapq.heappop(self._max_heap)
            heapq.heappush(self._min_heap, val_to_move)

    def find_median(self) -> float:
        """Returns the current median in O(1) time."""
        if not self._max_heap and not self._min_heap:
            raise ValueError("MedianFinder is empty.")

        if len(self._max_heap) > len(self._min_heap):
            return -self._max_heap[0]
        else:
            return (-self._max_heap[0] + self._min_heap[0]) / 2.0


# ============================================================================
# 4. K-WAY SORTED STREAM MERGE
# ============================================================================

def merge_k_sorted_arrays(arrays: List[List[int]]) -> List[int]:
    """
    Merges K sorted arrays into a single globally sorted array in O(N log K) time.
    """
    min_heap: List[Tuple[int, int, int]] = []

    # Initialize heap with (val, array_index, element_index)
    for arr_idx, arr in enumerate(arrays):
        if arr:
            min_heap.append((arr[0], arr_idx, 0))

    heapq.heapify(min_heap)
    result: List[int] = []

    while min_heap:
        val, arr_idx, elem_idx = heapq.heappop(min_heap)
        result.append(val)

        next_idx = elem_idx + 1
        if next_idx < len(arrays[arr_idx]):
            next_val = arrays[arr_idx][next_idx]
            heapq.heappush(min_heap, (next_val, arr_idx, next_idx))

    return result


# ============================================================================
# 5. VERIFICATION SUITE & COMPREHENSIVE BENCHMARK
# ============================================================================

def run_tests() -> None:
    """Validates Top-K, MedianFinder, and K-Way merge."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR HEAP & PRIORITY QUEUE ALGORITHMS")
    print("======================================================================")

    # 1. Top-K Largest Elements
    nums = [3, 2, 1, 5, 6, 4]
    assert find_k_largest_elements(nums, 2) == [6, 5]
    assert find_k_largest_elements(nums, 3) == [6, 5, 4]
    print("[PASS] find_k_largest_elements verified.")

    # 2. Top-K Frequent Words
    words = ["i", "love", "leetcode", "i", "love", "coding"]
    # "i": 2, "love": 2, "coding": 1, "leetcode": 1
    # For k = 2: "i" and "love" tie at 2; "i" < "love" lexicographically
    assert top_k_frequent_words(words, 2) == ["i", "love"]

    words2 = ["the", "day", "is", "sunny", "the", "the", "the", "sunny", "is", "is"]
    # "the": 4, "is": 3, "sunny": 2, "day": 1
    assert top_k_frequent_words(words2, 4) == ["the", "is", "sunny", "day"]
    print("[PASS] top_k_frequent_words with lexicographical ties verified.")

    # 3. MedianFinder
    mf = MedianFinder()
    mf.add_num(1.0)
    mf.add_num(2.0)
    assert mf.find_median() == 1.5
    mf.add_num(3.0)
    assert mf.find_median() == 2.0
    mf.add_num(0.0)
    assert mf.find_median() == 1.5
    print("[PASS] MedianFinder O(1) continuous median verified.")

    # 4. K-Way Merge
    arrays = [
        [1, 4, 7],
        [2, 5, 8],
        [3, 6, 9],
    ]
    merged = merge_k_sorted_arrays(arrays)
    assert merged == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    print("[PASS] merge_k_sorted_arrays O(N log K) verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Benchmarks full sorting O(N log N) vs bounded Min-Heap O(N log K) on 1,000,000 items."""
    print("======================================================================")
    print("HIGH-VOLUME BENCHMARK: FULL SORT O(N LOG N) VS MIN-HEAP O(N LOG K)")
    print("======================================================================")

    data_size = 1_000_000
    k = 50
    print(f"Dataset Size: {data_size:,} random latency telemetry metrics.")
    print(f"Target:       Top-{k} largest outliers.")

    random.seed(42)
    telemetry_data = [random.randint(1, 100_000_000) for _ in range(data_size)]

    # Benchmark Full Sorting O(N log N)
    start_sort = time.perf_counter()
    sorted_top_k = sorted(telemetry_data, reverse=True)[:k]
    elapsed_sort = time.perf_counter() - start_sort
    print(f"Full Array Sort (O(N log N)):     {elapsed_sort * 1000:8.2f} ms")

    # Benchmark Bounded Min-Heap O(N log K)
    start_heap = time.perf_counter()
    heap_top_k = find_k_largest_elements(telemetry_data, k)
    elapsed_heap = time.perf_counter() - start_heap
    print(f"Bounded Min-Heap (O(N log K)):    {elapsed_heap * 1000:8.2f} ms")

    speedup = elapsed_sort / elapsed_heap if elapsed_heap > 0 else float("inf")
    print(f"Speedup Factor with Bounded Heap: {speedup:8.1f}x faster!")

    assert sorted_top_k == heap_top_k, "Heap and sort outputs differed!"
    print("[PASS] Output datasets match identically!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
