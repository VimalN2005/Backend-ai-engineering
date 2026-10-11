# Day 16: Practical Hands-On DSA Challenges

Master the Two Pointers technique and in-place array algorithms with these 3 production-level challenges.

---

## Challenge 1: Merge Sorted Array In-Place (Backwards Three-Pointer Pattern)

### Objective
In high-performance database storage engines (such as SQLite B-Tree page rebalancing), two sorted data chunks must frequently be merged into a pre-allocated contiguous memory buffer without allocating a secondary array.

Implement `merge_sorted_inplace(nums1: list[int], m: int, nums2: list[int], n: int) -> None` that merges `nums2` into `nums1` as one sorted array in-place.

### Requirements
1. **Inputs**:
   - `nums1`: Integer array of length $m + n$. The first $m$ elements represent valid sorted data; the remaining $n$ elements are set to `0` and act as spare buffer space.
   - `m`: Count of initialized elements in `nums1`.
   - `nums2`: Integer array of length $n$ containing sorted data.
   - `n`: Count of initialized elements in `nums2`.
2. **Algorithm Constraint**:
   - Must achieve **$\mathcal{O}(m + n)$ time complexity** and **$\mathcal{O}(1)$ auxiliary space**.
   - **WHY BACKWARDS POINTERS**: Moving from left to right would overwrite unprocessed elements in `nums1`. You must start three pointers from the **end** of the arrays:
     - `p1 = m - 1` (last active element in `nums1`)
     - `p2 = n - 1` (last element in `nums2`)
     - `p = m + n - 1` (write pointer at the very end of `nums1`'s buffer)
3. **Edge Cases**:
   - `m = 0` (nums1 is completely empty buffer).
   - `n = 0` (nums2 has no elements to merge).
   - All elements in `nums2` are strictly smaller than elements in `nums1`.

---

## Challenge 2: 4Sum Engine with Mathematical Branch-and-Bound Pruning

### Objective
In fraud detection and multi-party cryptographic verification, backend engines must identify combinations of 4 transactions that net out to a specific financial balance `target`.

Implement `four_sum(nums: list[int], target: int) -> list[list[int]]` that finds all unique quadruplets `[nums[a], nums[b], nums[c], nums[d]]` where $a < b < c < d$ and their sum equals `target`.

### Requirements
1. **Complexity**:
   - Time Complexity: $\mathcal{O}(N^3)$ (Two nested anchor loops + Two Pointers sweep).
   - Space Complexity: $\mathcal{O}(1)$ auxiliary space (excluding the output list).
2. **Mathematical Pruning (Branch-and-Bound)**:
   - To pass enterprise performance benchmarks, implement aggressive pruning:
     - If `nums[i] + nums[i+1] + nums[i+2] + nums[i+3] > target`: Break immediately (the smallest possible quadruplet exceeds the target).
     - If `nums[i] + nums[n-3] + nums[n-2] + nums[n-1] < target`: Continue to next iteration (the largest possible quadruplet cannot reach the target).
     - Apply symmetric pruning to the secondary anchor loop `j`.
3. **Zero Hash Set Deduplication**:
   - You must eliminate duplicate quadruplets strictly via pointer skips, never by inserting tuples into a `set()`.

---

## Challenge 3: Real-Time Traffic Ingress Trapping & In-Place Compaction Pipeline

### Objective
Build a production-grade time-series telemetry compaction and reservoir analyzer for an API Gateway.

### Requirements
1. Implement a class `TelemetryReservoirPipeline`:
   - Accepts a list of integer traffic throughput samples `raw_traffic: list[int]`.
2. **Stage 1 - In-Place Zero & Outlier Compaction**:
   - Compact the list in-place using fast/slow runners: remove consecutive duplicate baseline values (e.g. consecutive baseline readings of `0` or `1` that indicate idle server polling).
3. **Stage 2 - Dynamic Boundary Reservoir Calculation**:
   - Run the two-pointer Trapping Rain Water algorithm across the compacted traffic profile to calculate the total trapped burst buffer units.
4. **Stage 3 - Telemetry Report**:
   - Return a structured dataclass `PipelineReport`:
     ```python
     @dataclass
     class PipelineReport:
         original_length: int
         compacted_length: int
         trapped_buffer_units: int
         processing_time_ms: float
     ```
5. **Validation**:
   - Verify on a test dataset of 50,000 traffic samples, confirming $\mathcal{O}(N)$ linear time and zero memory leaks.

---

## Solution Guidelines & Architecture Hints
- For Challenge 1, when `p2 >= 0` and `p1 < 0`, remember to flush remaining elements from `nums2` into `nums1`.
- For Challenge 2, sorting the array upfront takes $\mathcal{O}(N \log N)$ and unlocks both mathematical pruning and the two-pointer inner convergence.
- For Challenge 3, combine the fast/slow pointer technique from `example_01.py` with the reservoir analyzer from `example_02.py`.
