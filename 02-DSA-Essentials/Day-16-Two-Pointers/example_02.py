"""
Day 16: Example 02 - Production Backend Architecture: Two-Pointer Stream Merger & Traffic Reservoir Analyzer

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In large-scale distributed architectures (e.g., Kafka consumers, Prometheus time-series engines,
and log aggregation collectors like FluentBit/Datadog), backend systems must continuously merge
and compact chronologically ordered event streams arriving from multiple independent microservices.

Allocating intermediate data structures or performing full sorts O((N+M) log(N+M)) across
millions of events causes severe heap bloat and CPU spikes. By applying the Two Pointers
technique, stream ingestion executes in strictly linear O(N + M) time and O(1) auxiliary space.

Furthermore, analyzing ingress traffic spikes to size token-bucket rate limiters and burst
buffers is a direct real-world application of the Trapping Rain Water algorithm: calculating
the trapped capacity between server throughput peaks to dynamically size ingress buffer reservoirs.

This module implements:
1. `TelemetryLogEntry` domain model for distributed audit event streams.
2. `TwoPointerLogStreamMerger`: High-throughput dual-pointer stream merge & deduplicator.
3. `TrafficReservoirAnalyzer`: Two-pointer O(N) Trapping Rain Water engine to compute
   trapped burst volume across server traffic profiles.
4. Comprehensive verification suite and performance benchmarks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generator, List, Optional, Tuple


# ============================================================================
# 1. DOMAIN MODELS
# ============================================================================

@dataclass(frozen=True, slots=True)
class TelemetryLogEntry:
    """
    Represents an immutable time-series audit event from a backend microservice.
    WHY SLOTS=TRUE: Eliminates __dict__ overhead across millions of telemetry events,
    saving up to 60% RAM in streaming backend ingestion pipelines.
    """
    timestamp_ms: int
    service_name: str
    event_id: str
    payload: str

    def __lt__(self, other: TelemetryLogEntry) -> bool:
        return self.timestamp_ms < other.timestamp_ms


# ============================================================================
# 2. TWO-POINTER DISTRIBUTED LOG STREAM MERGER & DEDUPLICATOR
# ============================================================================

class TwoPointerLogStreamMerger:
    """
    Merges two sorted telemetry streams in O(N + M) time and O(1) auxiliary memory.
    
    WHY TWO POINTERS OVER SORTING:
    Concatenating two sorted streams of sizes N and M and calling .sort() takes
    O((N + M) log(N + M)) and allocates an entire combined list in memory.
    Two Pointers processes streams sequentially as a generator, achieving O(1) space
    and O(N + M) time, while performing in-line deduplication of duplicate event IDs.
    """

    @classmethod
    def merge_streams(
        cls,
        stream_a: List[TelemetryLogEntry],
        stream_b: List[TelemetryLogEntry],
    ) -> Generator[TelemetryLogEntry, None, None]:
        """
        Merges two chronologically sorted log streams using dual pointer traversal.
        Yields merged logs in non-decreasing timestamp order, discarding duplicate event IDs.
        """
        ptr_a = 0
        ptr_b = 0
        len_a = len(stream_a)
        len_b = len(stream_b)
        last_emitted_id: Optional[str] = None

        while ptr_a < len_a and ptr_b < len_b:
            entry_a = stream_a[ptr_a]
            entry_b = stream_b[ptr_b]

            # Choose earlier timestamp
            if entry_a.timestamp_ms <= entry_b.timestamp_ms:
                candidate = entry_a
                ptr_a += 1
                # If identical event ID exists in stream B at same time, advance B pointer too
                if entry_b.event_id == candidate.event_id:
                    ptr_b += 1
            else:
                candidate = entry_b
                ptr_b += 1
                # If identical event ID exists in stream A at same time, advance A pointer too
                if entry_a.event_id == candidate.event_id:
                    ptr_a += 1

            # In-line deduplication: skip if identical event was already emitted
            if candidate.event_id != last_emitted_id:
                last_emitted_id = candidate.event_id
                yield candidate

        # Drain remaining elements from stream A
        while ptr_a < len_a:
            candidate = stream_a[ptr_a]
            ptr_a += 1
            if candidate.event_id != last_emitted_id:
                last_emitted_id = candidate.event_id
                yield candidate

        # Drain remaining elements from stream B
        while ptr_b < len_b:
            candidate = stream_b[ptr_b]
            ptr_b += 1
            if candidate.event_id != last_emitted_id:
                last_emitted_id = candidate.event_id
                yield candidate


# ============================================================================
# 3. TRAFFIC RESERVOIR ANALYZER (TRAPPING RAIN WATER FOR BURST BUFFERS)
# ============================================================================

class TrafficReservoirAnalyzer:
    """
    Calculates trapped traffic capacity between ingress traffic spikes using Two Pointers.
    
    REAL-WORLD BACKEND APPLICATION:
    In API Gateways (Envoy/Kong), ingress traffic fluctuates with sharp load peaks.
    The 'valleys' between peaks represent available reserve buffer capacity that can
    absorb delayed asynchronous task execution without exceeding peak cluster limits.
    
    The Trapping Rain Water algorithm computes this exact trapped volume in O(N) time
    and O(1) space, avoiding O(N) prefix/suffix memory allocation arrays.
    """

    @classmethod
    def compute_trapped_buffer_capacity(cls, traffic_spikes: List[int]) -> int:
        """
        Computes total trapped buffer units across traffic elevation points.
        
        Algorithm: Dynamic 2-Pointer Boundary Invariant
        - Maintain left and right pointers at array extremes.
        - Maintain running left_max and right_max boundaries.
        - Advance the pointer with the smaller boundary: because the opposite side
          is guaranteed to have a boundary >= current side, water trapped at the
          current point depends strictly on the current side's max!
        """
        if not traffic_spikes or len(traffic_spikes) < 3:
            return 0

        left = 0
        right = len(traffic_spikes) - 1
        left_max = 0
        right_max = 0
        total_trapped_units = 0

        while left < right:
            if traffic_spikes[left] < traffic_spikes[right]:
                # Left side is the bottleneck
                if traffic_spikes[left] >= left_max:
                    left_max = traffic_spikes[left]
                else:
                    total_trapped_units += left_max - traffic_spikes[left]
                left += 1
            else:
                # Right side is the bottleneck
                if traffic_spikes[right] >= right_max:
                    right_max = traffic_spikes[right]
                else:
                    total_trapped_units += right_max - traffic_spikes[right]
                right -= 1

        return total_trapped_units


# ============================================================================
# 4. DEMONSTRATION & VERIFICATION SUITE
# ============================================================================

def run_production_two_pointers_demo() -> None:
    print("=" * 75)
    print("DAY 16: PRODUCTION TWO-POINTER STREAM MERGER & RESERVOIR ANALYZER")
    print("=" * 75)

    # 1. Test Two-Pointer Log Stream Merger
    print("\n[PART 1] Merging Distributed Microservice Telemetry Streams:")
    stream_orders = [
        TelemetryLogEntry(1000, "auth-svc", "evt-001", "User Login Success"),
        TelemetryLogEntry(1005, "order-svc", "evt-002", "Order Created"),
        TelemetryLogEntry(1015, "order-svc", "evt-004", "Inventory Reserved"),
        TelemetryLogEntry(1030, "auth-svc", "evt-006", "Token Refreshed"),
    ]

    stream_payments = [
        TelemetryLogEntry(1002, "pay-svc", "evt-003", "Stripe Charge Authorized"),
        TelemetryLogEntry(1015, "pay-svc", "evt-004", "Inventory Reserved (Duplicate Event)"),
        TelemetryLogEntry(1020, "pay-svc", "evt-005", "Webhook Dispatched"),
        TelemetryLogEntry(1040, "pay-svc", "evt-007", "Payment Settled"),
    ]

    merged_entries = list(TwoPointerLogStreamMerger.merge_streams(stream_orders, stream_payments))

    print(f"  Stream Orders Count   : {len(stream_orders)}")
    print(f"  Stream Payments Count : {len(stream_payments)}")
    print(f"  Merged Output Count   : {len(merged_entries)} (Deduplicated)\n")

    for entry in merged_entries:
        print(f"  - [{entry.timestamp_ms} ms] [{entry.service_name:<9}] ID: {entry.event_id} | {entry.payload}")

    # Validate non-decreasing order and deduplication (8 total entries - 1 duplicate evt-004 = 7)
    assert len(merged_entries) == 7, f"Expected 7 deduplicated entries, got {len(merged_entries)}"
    for idx in range(len(merged_entries) - 1):
        assert merged_entries[idx].timestamp_ms <= merged_entries[idx + 1].timestamp_ms, "Ordering violation"
    print("  - Status: STREAM MERGER ORDERING & DEDUPLICATION VERIFIED [PASSED]")

    # 2. Test Traffic Reservoir Analyzer (Trapping Rain Water)
    print("\n[PART 2] Computing Ingress Traffic Reservoir Capacity (Trapping Water):")
    traffic_profiles: List[Tuple[List[int], int, str]] = [
        ([0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1], 6, "Standard Fluctuation Pattern"),
        ([4, 2, 0, 3, 2, 5], 9, "Deep Traffic Chasm"),
        ([3, 0, 2, 0, 4], 7, "Double Saddle Topology"),
        ([1, 2, 3, 4, 5], 0, "Monotonically Increasing (Zero Trapped)"),
        ([5, 4, 3, 2, 1], 0, "Monotonically Decreasing (Zero Trapped)"),
    ]

    for profile, expected_units, label in traffic_profiles:
        calculated_units = TrafficReservoirAnalyzer.compute_trapped_buffer_capacity(profile)
        status = "PASSED" if calculated_units == expected_units else "FAILED"
        print(f"  - Profile: {profile}")
        print(f"    Trapped Capacity: {calculated_units} units (Expected: {expected_units}) [{label}] -> [{status}]")
        assert calculated_units == expected_units

    # 3. Benchmark: Dual-Pointer Merge vs List Concatenate + Sort
    print("\n[PART 3] Performance Benchmark: Two-Pointer Merge vs Concatenate+Sort:")
    sample_size = 20000
    large_stream_a = [
        TelemetryLogEntry(t * 2, "svc-a", f"a-{t}", "payload")
        for t in range(sample_size)
    ]
    large_stream_b = [
        TelemetryLogEntry(t * 2 + 1, "svc-b", f"b-{t}", "payload")
        for t in range(sample_size)
    ]

    # Benchmark Concatenate + Sort: O((N+M) log(N+M))
    start_time = time.perf_counter()
    naive_combined = list(large_stream_a) + list(large_stream_b)
    naive_combined.sort(key=lambda x: x.timestamp_ms)
    naive_duration = (time.perf_counter() - start_time) * 1000.0

    # Benchmark Two Pointers Merge: O(N + M)
    start_time = time.perf_counter()
    tp_merged = list(TwoPointerLogStreamMerger.merge_streams(large_stream_a, large_stream_b))
    tp_duration = (time.perf_counter() - start_time) * 1000.0

    print(f"  Merged {sample_size * 2} records across two independent streams:")
    print(f"  - Concatenate + Sort : {naive_duration:.2f} ms | Requires allocating O(N + M) array in RAM")
    print(f"  - Two-Pointer Merge  : {tp_duration:.2f} ms | Pure lazy streaming with O(1) auxiliary RAM")
    print("  - Production Takeaway: Two-Pointer streaming avoids heap spikes on multi-GB event streams")

    assert len(tp_merged) == sample_size * 2

    print("\n" + "=" * 75)
    print("PRODUCTION TWO-POINTER ARCHITECTURE VERIFIED & VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_production_two_pointers_demo()
