"""
Day 20: Example 02 - Production Architecture: Real-Time Telemetry Watchdog & Cluster Burst Optimizer

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In enterprise infrastructure, two critical problems demand sub-millisecond linear processing:
1. Real-Time Telemetry SLA Watchdog:
   Distributed API gateways (Kong, Envoy, Nginx) emit continuous streams of latency metrics.
   Site Reliability Engineers (SREs) and AI inference clusters need to detect *latency degradation
   frontiers*—determining how many requests or milliseconds elapse before a response time exceeds
   a threshold or spikes higher than the current baseline. A Monotonic Decreasing Stack processes
   streaming metrics in amortized O(1) time per event, immediately firing alerting hooks upon
   resolution without quadratic retrospective scans.

2. Ingress Cluster Burst Capacity Optimizer:
   Kubernetes worker node pools and distributed GPU clusters report available compute headroom
   (e.g., contiguous CPU cores or GPU VRAM) across sequential worker nodes or time slots.
   Scheduling distributed AI training jobs (like PyTorch Distributed Data Parallel) requires
   finding the maximum contiguous block of compute with guaranteed minimum sustained capacity.
   This is mathematically identical to the Largest Rectangle in Histogram: by treating worker node
   capacities as histogram bars, an increasing monotonic stack identifies the optimal job sizing
   in strict O(N) linear time.

This module implements:
1. `TelemetryMetric`: High-precision telemetry event model.
2. `LatencySpikeWatchdog`: Streaming real-time SLA breach and spike detector with window retention.
3. `IngressBurstCapacityOptimizer`: Cluster compute allocation planner using monotonic histogram optimization.
4. Comprehensive end-to-end simulation and throughput verification suite.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# ============================================================================
# 1. TELEMETRY EVENT DATA MODELS
# ============================================================================

@dataclass(frozen=True)
class TelemetryMetric:
    """Immutable high-precision telemetry event emitted by API gateways or AI models."""
    request_id: str
    timestamp_ms: float
    endpoint: str
    latency_ms: float
    status_code: int = 200


@dataclass
class LatencyResolution:
    """Represents a resolved latency spike boundary for a past request."""
    request_id: str
    baseline_latency_ms: float
    spiked_by_request_id: str
    spiked_latency_ms: float
    requests_until_spike: int
    elapsed_time_ms: float


# ============================================================================
# 2. REAL-TIME LATENCY SPIKE WATCHDOG (STREAMING MONOTONIC DECREASING STACK)
# ============================================================================

class LatencySpikeWatchdog:
    """
    Streaming telemetry analyzer that uses a Monotonic Decreasing Stack to detect
    latency degradation frontiers in real-time.
    
    WHY MONOTONIC STACK IN STREAMING:
    As new metrics arrive, previous requests waiting for a higher latency spike are
    resolved in amortized O(1) time. The stack retains only pending requests in
    descending latency order. When an extreme latency spike arrives, it immediately
    unwinds and resolves all lower-latency requests in a single cascade.
    
    To prevent unbounded memory growth in 24/7 production streams, this class enforces
    a sliding window retention horizon that evicts stale unresolved requests.
    """

    def __init__(
        self,
        retention_window_ms: float = 60_000.0,
        alert_callback: Optional[Callable[[LatencyResolution], None]] = None,
    ) -> None:
        self.retention_window_ms = retention_window_ms
        self.alert_callback = alert_callback
        
        # Monotonic decreasing stack storing (metric, stream_sequence_index)
        self._stack: List[Tuple[TelemetryMetric, int]] = []
        self._sequence_counter: int = 0
        self._resolved_count: int = 0
        self._total_ingested: int = 0

    def ingest_metric(self, metric: TelemetryMetric) -> List[LatencyResolution]:
        """
        Ingests a single incoming telemetry event in amortized O(1) time.
        Resolves any pending lower-latency metrics whose 'next greater spike' is this metric.
        
        Returns:
            List of LatencyResolutions resolved by this incoming metric.
        """
        self._total_ingested += 1
        current_seq = self._sequence_counter
        self._sequence_counter += 1
        
        resolutions: List[LatencyResolution] = []

        # Monotonic Decreasing Invariant:
        # If incoming latency exceeds the latency at stack top, incoming metric is its NGE.
        while self._stack and self._stack[-1][0].latency_ms < metric.latency_ms:
            past_metric, past_seq = self._stack.pop()
            
            resolution = LatencyResolution(
                request_id=past_metric.request_id,
                baseline_latency_ms=past_metric.latency_ms,
                spiked_by_request_id=metric.request_id,
                spiked_latency_ms=metric.latency_ms,
                requests_until_spike=current_seq - past_seq,
                elapsed_time_ms=metric.timestamp_ms - past_metric.timestamp_ms,
            )
            resolutions.append(resolution)
            self._resolved_count += 1

            if self.alert_callback:
                self.alert_callback(resolution)

        # Append current metric to stack to await future spikes
        self._stack.append((metric, current_seq))

        # Periodic retention cleanup (prevent memory leak from ancient unresolved spikes)
        if self._sequence_counter % 1000 == 0:
            self._prune_stale_metrics(metric.timestamp_ms)

        return resolutions

    def _prune_stale_metrics(self, current_timestamp_ms: float) -> None:
        """Removes metrics that have exceeded the retention horizon without being spiked."""
        cutoff_time = current_timestamp_ms - self.retention_window_ms
        # Filter stack in-place while maintaining monotonic ordering
        self._stack = [item for item in self._stack if item[0].timestamp_ms >= cutoff_time]

    @property
    def pending_count(self) -> int:
        """Returns number of unresolved metrics currently in the monotonic stack."""
        return len(self._stack)

    @property
    def total_ingested(self) -> int:
        return self._total_ingested

    @property
    def total_resolved(self) -> int:
        return self._resolved_count


# ============================================================================
# 3. CLUSTER BURST CAPACITY OPTIMIZER (MONOTONIC INCREASING STACK)
# ============================================================================

@dataclass(frozen=True)
class BurstPlacement:
    """Optimal contiguous cluster allocation window."""
    start_node_index: int
    end_node_index: int
    num_nodes: int
    guaranteed_headroom: float
    total_compute_volume: float


class IngressBurstCapacityOptimizer:
    """
    Evaluates cluster node pools or sequential GPU allocation windows to determine
    the maximum contiguous burst capacity for distributed batch workloads.
    
    WHY MONOTONIC HISTOGRAM PATTERN:
    In distributed ML training (e.g., DeepSpeed or Megatron-LM), workers must be allocated
    contiguously or symmetrically across high-bandwidth network fabrics. The effective
    capacity of a contiguous partition of nodes is limited by the BOTTLENECK node
    (the one with the lowest headroom):
        Volume = min(Headroom) * Number_of_Contiguous_Nodes
    
    Evaluating all pairs of nodes takes O(N^2). By treating node headrooms as histogram bars,
    a Monotonic Increasing Stack calculates the global maximum volume in guaranteed O(N) time!
    """

    @staticmethod
    def find_maximum_burst_allocation(node_headrooms: List[float]) -> BurstPlacement:
        """
        Finds the contiguous node partition that maximizes guaranteed compute volume.
        
        Args:
            node_headrooms: List of available compute units (e.g., available CPU cores / GPU VRAM).
            
        Returns:
            BurstPlacement detailing the best start index, end index, and total compute volume.
        """
        if not node_headrooms:
            return BurstPlacement(0, 0, 0, 0.0, 0.0)

        # Add 0.0 sentinel to guarantee all remaining bars are cleanly flushed
        extended = list(node_headrooms) + [0.0]
        stack: List[int] = []  # Stores indices of nodes with monotonic increasing headroom
        
        best_placement = BurstPlacement(0, 0, 0, 0.0, 0.0)

        for current_idx, current_headroom in enumerate(extended):
            # When current node has less headroom than stack top, the stack top node
            # cannot extend any further to the right. Resolve its maximum contiguous span!
            while stack and extended[stack[-1]] > current_headroom:
                popped_idx = stack.pop()
                bottleneck_headroom = extended[popped_idx]

                # Left boundary calculation:
                # If stack is empty, popped_idx was smaller than all preceding nodes -> spans from 0
                # If stack is non-empty, left boundary is stack[-1] + 1
                left_idx = stack[-1] + 1 if stack else 0
                right_idx = current_idx - 1
                span_width = right_idx - left_idx + 1

                volume = bottleneck_headroom * span_width

                if volume > best_placement.total_compute_volume:
                    best_placement = BurstPlacement(
                        start_node_index=left_idx,
                        end_node_index=right_idx,
                        num_nodes=span_width,
                        guaranteed_headroom=bottleneck_headroom,
                        total_compute_volume=volume,
                    )

            stack.append(current_idx)

        return best_placement


# ============================================================================
# 4. VERIFICATION SUITE & PRODUCTION SIMULATION
# ============================================================================

def run_production_simulation() -> None:
    """Simulates real-time telemetry stream ingestion and cluster burst optimization."""
    print("======================================================================")
    print("SIMULATING PRODUCTION REAL-TIME TELEMETRY WATCHDOG")
    print("======================================================================")

    alerts_triggered: List[LatencyResolution] = []

    def on_sla_breach(res: LatencyResolution) -> None:
        # Trigger alert if latency jumped by more than 100ms
        if res.spiked_latency_ms - res.baseline_latency_ms >= 100.0:
            alerts_triggered.append(res)

    watchdog = LatencySpikeWatchdog(
        retention_window_ms=30_000.0,
        alert_callback=on_sla_breach,
    )

    # 1. Feed a realistic synthetic stream of 50,000 metrics
    stream_size = 50_000
    random.seed(42)
    base_time = time.time() * 1000.0

    print(f"Streaming {stream_size:,} real-time API telemetry events through Watchdog...")
    start_time = time.perf_counter()

    for i in range(stream_size):
        # Normal latency ~20-50ms, with occasional spike up to 500ms
        is_spike = (i % 250 == 0)
        latency = random.uniform(200.0, 500.0) if is_spike else random.uniform(15.0, 60.0)
        
        metric = TelemetryMetric(
            request_id=f"req_{i:06d}",
            timestamp_ms=base_time + (i * 10.0),  # 10ms intervals
            endpoint="/api/v1/inference/predict",
            latency_ms=latency,
        )
        watchdog.ingest_metric(metric)

    elapsed = time.perf_counter() - start_time
    throughput = stream_size / elapsed if elapsed > 0 else 0.0

    print(f"\n[OK] Ingestion Completed in:      {elapsed * 1000:8.2f} ms")
    print(f"[OK] Stream Processing Rate:      {throughput:10,.0f} metrics/sec")
    print(f"[OK] Total Metrics Ingested:      {watchdog.total_ingested:,}")
    print(f"[OK] Total Latency Spikes Found:  {watchdog.total_resolved:,}")
    print(f"[OK] Severe Alerts Dispatched:    {len(alerts_triggered):,}")
    print(f"[OK] Pending Unresolved on Stack: {watchdog.pending_count}")

    # Inspect the first 3 severe alerts
    print("\nSample Severe Latency Spikes Detected in Real Time:")
    for alert in alerts_triggered[:3]:
        print(f"  -> Baseline [{alert.request_id}] {alert.baseline_latency_ms:.1f}ms spiked by "
              f"[{alert.spiked_by_request_id}] to {alert.spiked_latency_ms:.1f}ms "
              f"after {alert.requests_until_spike} requests ({alert.elapsed_time_ms:.1f}ms)")

    print("\n======================================================================")
    print("SIMULATING INGRESS CLUSTER BURST CAPACITY OPTIMIZER")
    print("======================================================================")

    # Simulate 16 Kubernetes worker nodes with available GPU memory (GB)
    # [Node 0: 40GB, Node 1: 32GB, Node 2: 80GB, Node 3: 80GB, Node 4: 80GB, Node 5: 16GB, ...]
    cluster_nodes = [40.0, 32.0, 80.0, 80.0, 80.0, 16.0, 24.0, 64.0, 64.0, 64.0, 64.0, 12.0]
    print(f"Node Pool Headrooms (GB): {cluster_nodes}")

    optimizer = IngressBurstCapacityOptimizer()
    optimal = optimizer.find_maximum_burst_allocation(cluster_nodes)

    print(f"\nOptimal Distributed Placement Identified:")
    print(f"  Contiguous Nodes:    Node {optimal.start_node_index} to Node {optimal.end_node_index} ({optimal.num_nodes} nodes)")
    print(f"  Guaranteed Headroom: {optimal.guaranteed_headroom:.1f} GB per node")
    print(f"  Total Compute Block: {optimal.total_compute_volume:.1f} GB contiguous capacity")

    # Verify correctness:
    # Nodes 7, 8, 9, 10 each have 64.0 GB -> 4 * 64.0 = 256.0 GB (vs nodes 2,3,4: 3 * 80 = 240 GB)
    assert optimal.start_node_index == 7 and optimal.end_node_index == 10
    assert optimal.total_compute_volume == 256.0
    print("[PASS] Cluster Burst Allocation mathematically verified!")
    print("======================================================================")


if __name__ == "__main__":
    run_production_simulation()
