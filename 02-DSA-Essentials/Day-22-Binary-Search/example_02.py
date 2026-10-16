"""
Day 22: Example 02 - Production Architecture: SSTable Sparse Index & AI Batch Scheduler

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In enterprise infrastructure, two foundational systems rely heavily on binary search:

1. LSM-Tree Storage Engine Sparse Indexing (Apache Kafka, RocksDB, Cassandra):
   High-throughput database engines persist write streams into immutable Sorted String Tables
   (SSTables) partitioned into 4KB data blocks on disk. Loading gigabytes of data to find a key
   causes severe disk I/O bottlenecks.
   Instead, an in-memory Sparse Index stores the first key of each block along with its physical
   byte offset. An incoming key lookup performs Binary Search over the sparse index in O(log(Blocks))
   time, pinpointing the exact 4KB block on disk and avoiding 99.9% of I/O reads.

2. AI Inference Dynamic Batch Capacity Planner (vLLM, Triton, TensorRT-LLM):
   LLM inference servers dynamically batch incoming prompt tokens. Increasing batch size B
   improves GPU compute efficiency, but increases memory bandwidth contention and token generation
   latency. Given an empirical non-linear latency function and a strict P99 SLA deadline,
   the scheduler uses Binary Search over the monotonic capacity space to determine the maximum
   safe batch size in sub-microsecond time.

This module implements:
1. `SSTableSparseIndex`: Production LSM-Tree block index search using lower-bound binary search.
2. `AIBatchInferenceScheduler`: Dynamic GPU batch size optimizer using binary search on answer.
3. End-to-end simulation, correctness verification, and throughput benchmarks.
"""

from __future__ import annotations

import bisect
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# 1. SSTABLE SPARSE INDEX SEARCH (LSM-TREE STORAGE ENGINE)
# ============================================================================

@dataclass(frozen=True)
class IndexEntry:
    """Represents a sparse index entry pointing to a physical data block."""
    first_key: str
    byte_offset: int
    block_size_bytes: int


@dataclass
class StorageBlock:
    """Simulates a 4KB disk data block containing sorted key-value records."""
    byte_offset: int
    records: Dict[str, str] = field(default_factory=dict)


class SSTableIndexSearchEngine:
    """
    High-performance sparse index search engine modeled after RocksDB / Kafka log segments.
    
    WHY SPARSE BINARY SEARCH:
    Storing an index for EVERY key in memory exhausts RAM. Storing an index for only the FIRST
    key of each 4KB disk block reduces index memory by 99% while Binary Search identifies
    the correct disk block in O(log(Number_of_Blocks)) time.
    """

    def __init__(self) -> None:
        self._sparse_index: List[IndexEntry] = []
        self._disk_blocks: Dict[int, StorageBlock] = {}

    def add_block(self, block: StorageBlock) -> None:
        """Appends a new sorted block and updates the sparse index."""
        if not block.records:
            return
        
        first_key = min(block.records.keys())
        entry = IndexEntry(
            first_key=first_key,
            byte_offset=block.byte_offset,
            block_size_bytes=4096,
        )
        self._sparse_index.append(entry)
        self._disk_blocks[block.byte_offset] = block

    def get(self, target_key: str) -> Optional[str]:
        """
        Locates target_key using Binary Search over the sparse index.
        
        ALGORITHM:
        1. Perform upper-bound binary search to find the greatest index entry where
           entry.first_key <= target_key.
        2. Seek to that block's byte_offset.
        3. Scan or binary-search within the localized 4KB block.
        """
        if not self._sparse_index:
            return None

        # Binary search for the block that could contain target_key
        # We need the last block whose first_key <= target_key
        left = 0
        right = len(self._sparse_index) - 1
        candidate_block_idx = -1

        while left <= right:
            mid = left + (right - left) // 2
            if self._sparse_index[mid].first_key <= target_key:
                candidate_block_idx = mid  # mid is a valid candidate; search right for tighter bound
                left = mid + 1
            else:
                right = mid - 1

        if candidate_block_idx == -1:
            # target_key is smaller than the very first key in the entire SSTable
            return None

        # Fetch simulated 4KB disk block
        target_entry = self._sparse_index[candidate_block_idx]
        block = self._disk_blocks.get(target_entry.byte_offset)
        if not block:
            return None

        # Local lookup within the 4KB block
        return block.records.get(target_key)

    @property
    def total_blocks(self) -> int:
        return len(self._sparse_index)


# ============================================================================
# 2. AI BATCH INFERENCE CAPACITY PLANNER (DYNAMIC GPU BATCH SCHEDULING)
# ============================================================================

class AIBatchInferenceScheduler:
    """
    Calculates the maximum safe concurrent batch size for GPU AI inference models
    (e.g., Llama-3-70B, Mistral, embedding models) given strict SLA latency targets.
    
    LATENCY COST MODEL:
    On modern GPUs (NVIDIA H100 / A100), execution time scales non-linearly with batch size:
        T(B) = base_latency + (compute_slope * B) + (memory_contention_factor * B^2)
    Because T(B) is strictly monotonic increasing with batch size B, we can binary search
    the batch space [1 .. max_batch_limit] in O(log B_max) time to find the maximum B
    where T(B) <= sla_deadline_ms.
    """

    def __init__(
        self,
        base_latency_ms: float = 8.0,
        compute_slope: float = 0.45,
        memory_contention_factor: float = 0.002,
        max_batch_limit: int = 512,
    ) -> None:
        self.base_latency = base_latency_ms
        self.compute_slope = compute_slope
        self.memory_contention = memory_contention_factor
        self.max_batch_limit = max_batch_limit

    def predict_latency_ms(self, batch_size: int) -> float:
        """Evaluates non-linear GPU inference latency for batch size B."""
        if batch_size <= 0:
            return 0.0
        return self.base_latency + (self.compute_slope * batch_size) + (self.memory_contention * (batch_size ** 2))

    def find_max_batch_size_for_sla(self, max_sla_ms: float) -> int:
        """
        Uses Binary Search on Answer to find the largest batch size that finishes <= max_sla_ms.
        
        Returns:
            Integer batch size B in range [0 .. max_batch_limit].
            Returns 0 if even a batch of 1 violates the SLA.
        """
        if max_sla_ms < self.predict_latency_ms(1):
            return 0  # Even batch of 1 breaches SLA!

        left = 1
        right = self.max_batch_limit
        optimal_batch = 0

        while left <= right:
            mid = left + (right - left) // 2
            predicted_time = self.predict_latency_ms(mid)

            if predicted_time <= max_sla_ms:
                optimal_batch = mid  # mid is valid; try larger batch size to maximize throughput
                left = mid + 1
            else:
                right = mid - 1     # SLA breached, must reduce batch size

        return optimal_batch


# ============================================================================
# 3. VERIFICATION SUITE & PRODUCTION SIMULATION
# ============================================================================

def run_tests() -> None:
    """Validates correctness across sparse index lookups and GPU batch sizing."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR PRODUCTION BINARY SEARCH ARCHITECTURES")
    print("======================================================================")

    # 1. SSTable Sparse Index Lookup
    sstable = SSTableIndexSearchEngine()

    # Block 0: Keys ["user:001", "user:002", "user:003"]
    b0 = StorageBlock(byte_offset=0, records={"user:001": "Alice", "user:002": "Bob", "user:003": "Charlie"})
    # Block 1: Keys ["user:010", "user:011", "user:012"]
    b1 = StorageBlock(byte_offset=4096, records={"user:010": "David", "user:011": "Emma", "user:012": "Frank"})
    # Block 2: Keys ["user:020", "user:025", "user:030"]
    b2 = StorageBlock(byte_offset=8192, records={"user:020": "Grace", "user:025": "Hannah", "user:030": "Ivan"})

    sstable.add_block(b0)
    sstable.add_block(b1)
    sstable.add_block(b2)

    assert sstable.get("user:002") == "Bob"
    assert sstable.get("user:011") == "Emma"
    assert sstable.get("user:030") == "Ivan"
    assert sstable.get("user:005") is None  # Not present in b0
    assert sstable.get("user:000") is None  # Smaller than first key
    print("[PASS] SSTable sparse index binary search verified.")

    # 2. AI Batch Inference Scheduler
    scheduler = AIBatchInferenceScheduler(base_latency_ms=10.0, compute_slope=0.5, memory_contention_factor=0.001)

    # SLA = 25ms:
    # B = 20 -> 10 + 10 + 0.4 = 20.4ms (OK)
    # B = 26 -> 10 + 13 + 0.676 = 23.676ms (OK)
    # B = 28 -> 10 + 14 + 0.784 = 24.784ms (OK)
    # B = 29 -> 10 + 14.5 + 0.841 = 25.341ms (BREACH)
    optimal_b = scheduler.find_max_batch_size_for_sla(max_sla_ms=25.0)
    assert optimal_b == 28, f"Expected batch size 28, got {optimal_b}"
    assert scheduler.predict_latency_ms(optimal_b) <= 25.0
    assert scheduler.predict_latency_ms(optimal_b + 1) > 25.0

    # Unrealistic SLA = 5.0ms (base is 10.0ms) -> Returns 0
    assert scheduler.find_max_batch_size_for_sla(max_sla_ms=5.0) == 0
    print("[PASS] AI batch capacity optimization boundary verified.")

    print("All unit tests passed successfully!\n")


def run_production_simulation() -> None:
    """Simulates high-volume database index lookups and dynamic GPU scheduling."""
    print("======================================================================")
    print("SIMULATING HIGH-SCALE SSTABLE SPARSE INDEX LOOKUPS")
    print("======================================================================")

    # Build SSTable with 10,000 blocks (representing 40MB on disk)
    sstable = SSTableIndexSearchEngine()
    total_blocks = 10_000

    for i in range(total_blocks):
        block_offset = i * 4096
        # Each block holds 5 keys
        records = {f"k_{i:06d}_{j:02d}": f"payload_{i}_{j}" for j in range(5)}
        block = StorageBlock(byte_offset=block_offset, records=records)
        sstable.add_block(block)

    print(f"Constructed SSTable with {sstable.total_blocks:,} indexed 4KB disk blocks.")

    # Query 50,000 random keys via sparse binary search
    query_count = 50_000
    start_time = time.perf_counter()

    for i in range(query_count):
        target_block = (i * 7) % total_blocks
        query_key = f"k_{target_block:06d}_02"
        val = sstable.get(query_key)
        assert val is not None

    elapsed = time.perf_counter() - start_time
    throughput = query_count / elapsed if elapsed > 0 else 0.0

    print(f"Executed {query_count:,} Sparse Binary Search queries in {elapsed * 1000:8.2f} ms")
    print(f"Sparse Index Throughput:  {throughput:10,.0f} queries/sec")
    print(f"Average Lookup Latency:   {(elapsed / query_count) * 1_000_000:8.4f} microseconds/query")

    print("\n======================================================================")
    print("SIMULATING AI DYNAMIC GPU BATCH SCHEDULING UNDER VARIABLE SLAs")
    print("======================================================================")

    scheduler = AIBatchInferenceScheduler(base_latency_ms=12.0, compute_slope=0.35, memory_contention_factor=0.0015)
    sla_targets = [20.0, 35.0, 50.0, 75.0, 100.0, 150.0]

    for sla in sla_targets:
        b_size = scheduler.find_max_batch_size_for_sla(sla)
        actual_lat = scheduler.predict_latency_ms(b_size)
        tokens_per_sec = (b_size * 1000.0) / actual_lat if actual_lat > 0 else 0.0
        print(f"  SLA Target: {sla:5.1f} ms  ->  Max Safe Batch: {b_size:3d}  (Lat: {actual_lat:5.2f} ms)  ->  Throughput: {tokens_per_sec:7.1f} req/sec")

    print("\n[PASS] Production simulation completed successfully!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_production_simulation()
