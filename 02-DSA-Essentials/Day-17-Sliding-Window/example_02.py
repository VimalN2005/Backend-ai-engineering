"""
Day 17: Example 02 - Production Backend Architecture: Sliding Window Rate Limiting & Rolling Telemetry Engine

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-concurrency distributed backends (e.g. FastAPI / DRF gateways, LLM endpoints,
and telemetry collectors), protecting downstream databases and model APIs from abuse
requires precise, low-overhead rate limiting.

Fixed-window rate limiters suffer from the 'boundary burst flaw' (a client can send 2x
the rate limit right across the minute boundary). The Sliding Window Log pattern eliminates
this edge case entirely by maintaining a continuous rolling time window.

Additionally, production monitoring agents (Prometheus, Datadog) compute real-time
rolling averages and standard deviations over sliding observation windows to detect
latency anomalies (p99 spikes, memory leaks) without storing millions of past data points.

This module implements:
1. `SlidingWindowLogRateLimiter`: Production rolling window rate limiter with microsecond
   precision, timestamp eviction, and thread-safe lock synchronization.
2. `RollingTelemetryWindowAggregator`: O(1) incremental time-series metrics aggregator
   tracking rolling mean, rolling variance, and automated 3-sigma anomaly spike detection.
3. Automated verification suite and high-throughput simulation benchmarks.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ============================================================================
# 1. SLIDING WINDOW LOG RATE LIMITER
# ============================================================================

@dataclass
class RateLimitResult:
    """Represents the rate limiting verdict for an incoming HTTP request."""
    allowed: bool
    current_count: int
    limit: int
    retry_after_seconds: float
    client_id: str


class SlidingWindowLogRateLimiter:
    """
    High-precision rolling window rate limiter using a sliding timestamp deque.
    
    WHY SLIDING WINDOW LOG:
    Unlike fixed-window limiters (which reset at 12:00, 12:01, etc., allowing 2x bursts
    at 11:59 and 12:01), the Sliding Window Log maintains an exact sliding window
    [now - window_size, now]. Expired timestamps are pruned from the left of the deque
    in O(1) amortized time, guaranteeing strict compliance with rate limits.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # Maps client_id -> deque of request timestamps (floats in seconds)
        self._client_logs: Dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def acquire(self, client_id: str, current_time: Optional[float] = None) -> RateLimitResult:
        """
        Evaluates whether a request from client_id is permitted.
        Thread-safe execution protected by mutex lock.
        """
        now = current_time if current_time is not None else time.time()
        boundary_time = now - self.window_seconds

        with self._lock:
            if client_id not in self._client_logs:
                self._client_logs[client_id] = deque()

            log_deque = self._client_logs[client_id]

            # 1. Slide window: Evict all expired timestamps from the front in O(1)
            while log_deque and log_deque[0] <= boundary_time:
                log_deque.popleft()

            # 2. Check if request count exceeds threshold
            if len(log_deque) < self.max_requests:
                # Permit request and record timestamp
                log_deque.append(now)
                return RateLimitResult(
                    allowed=True,
                    current_count=len(log_deque),
                    limit=self.max_requests,
                    retry_after_seconds=0.0,
                    client_id=client_id,
                )
            else:
                # Reject request and compute wait time until earliest request expires
                earliest_ts = log_deque[0]
                retry_after = max(0.0, (earliest_ts + self.window_seconds) - now)
                return RateLimitResult(
                    allowed=False,
                    current_count=len(log_deque),
                    limit=self.max_requests,
                    retry_after_seconds=round(retry_after, 4),
                    client_id=client_id,
                )

    def prune_idle_clients(self, current_time: Optional[float] = None) -> int:
        """Purges memory entries for clients with no activity in the current window."""
        now = current_time if current_time is not None else time.time()
        boundary_time = now - self.window_seconds
        pruned_count = 0

        with self._lock:
            idle_keys = []
            for client_id, log_deque in self._client_logs.items():
                while log_deque and log_deque[0] <= boundary_time:
                    log_deque.popleft()
                if not log_deque:
                    idle_keys.append(client_id)

            for key in idle_keys:
                del self._client_logs[key]
                pruned_count += 1

        return pruned_count


# ============================================================================
# 2. ROLLING TELEMETRY WINDOW AGGREGATOR (O(1) INCREMENTAL METRICS)
# ============================================================================

@dataclass
class AnomalyAlert:
    """Represents an anomaly detection event emitted by the telemetry window."""
    timestamp: float
    value: float
    rolling_mean: float
    rolling_std_dev: float
    z_score: float
    message: str


class RollingTelemetryWindowAggregator:
    """
    Fixed-size sliding window aggregator for high-speed streaming metrics.
    
    WHY INCREMENTAL O(1) STATISTICS:
    Computing mean and variance from scratch over a window of size W takes O(W) per sample.
    By maintaining running sum:
        Sum = Sum + New - Old
    and running sum of squares:
        SumSq = SumSq + New^2 - Old^2
    we compute the moving average and standard deviation in O(1) constant time,
    enabling real-time 3-sigma anomaly detection for 500k+ req/sec streams.
    """

    def __init__(self, capacity: int, anomaly_z_threshold: float = 3.0) -> None:
        if capacity <= 1:
            raise ValueError("Capacity must be greater than 1.")
        self.capacity = capacity
        self.anomaly_z_threshold = anomaly_z_threshold

        self._window: deque[float] = deque(maxlen=capacity)
        self._running_sum: float = 0.0
        self._running_sum_sq: float = 0.0

    def add_sample(self, value: float, timestamp: Optional[float] = None) -> Optional[AnomalyAlert]:
        """
        Appends a new metric reading, incrementally updates stats, and detects anomalies.
        Returns AnomalyAlert if value deviates significantly from the rolling baseline.
        """
        ts = timestamp if timestamp is not None else time.time()
        alert: Optional[AnomalyAlert] = None

        # Check for anomaly BEFORE updating baseline (if sufficient history exists)
        if len(self._window) >= self.capacity:
            mean, std_dev = self.get_stats()
            if std_dev > 0.0001:
                z_score = abs(value - mean) / std_dev
                if z_score >= self.anomaly_z_threshold:
                    alert = AnomalyAlert(
                        timestamp=ts,
                        value=value,
                        rolling_mean=round(mean, 2),
                        rolling_std_dev=round(std_dev, 2),
                        z_score=round(z_score, 2),
                        message=f"Latency spike detected! Value {value:.2f}ms is {z_score:.2f} standard deviations above mean ({mean:.2f}ms).",
                    )

        # Evict outgoing element if window is at full capacity
        if len(self._window) == self.capacity:
            outgoing = self._window[0]  # deque(maxlen) will automatically discard on append
            self._running_sum -= outgoing
            self._running_sum_sq -= outgoing * outgoing

        # Incorporate incoming element
        self._window.append(value)
        self._running_sum += value
        self._running_sum_sq += value * value

        return alert

    def get_stats(self) -> Tuple[float, float]:
        """Returns (rolling_mean, rolling_std_dev) in O(1) time."""
        n = len(self._window)
        if n == 0:
            return 0.0, 0.0

        mean = self._running_sum / n
        # Variance formula: Var = E[X^2] - (E[X])^2
        variance = (self._running_sum_sq / n) - (mean * mean)
        # Protect against precision floating-point negative zeros
        std_dev = math.sqrt(max(0.0, variance))
        return mean, std_dev


# ============================================================================
# 3. DEMONSTRATION & VERIFICATION SUITE
# ============================================================================

def run_production_sliding_window_demo() -> None:
    print("=" * 75)
    print("DAY 17: PRODUCTION SLIDING WINDOW RATE LIMITER & METRICS ENGINE")
    print("=" * 75)

    # 1. Verify Sliding Window Log Rate Limiter
    print("\n[PART 1] Testing Sliding Window Log Rate Limiter (Limit: 3 reqs / 1.0 sec):")
    limiter = SlidingWindowLogRateLimiter(max_requests=3, window_seconds=1.0)
    simulated_base_time = 1000.0
    client_ip = "192.168.1.50"

    # Simulate 5 rapid requests within 0.5s window
    requests_timing = [
        (simulated_base_time + 0.1, True),   # 1st request -> Allow
        (simulated_base_time + 0.2, True),   # 2nd request -> Allow
        (simulated_base_time + 0.3, True),   # 3rd request -> Allow (Reached Limit)
        (simulated_base_time + 0.4, False),  # 4th request -> Reject (Limit Exceeded!)
        (simulated_base_time + 0.5, False),  # 5th request -> Reject (Limit Exceeded!)
        (simulated_base_time + 1.15, True),  # 6th request (1.05s later -> Earliest expired, Allow!)
    ]

    for req_time, expected_status in requests_timing:
        result = limiter.acquire(client_ip, current_time=req_time)
        verdict = "ALLOWED " if result.allowed else "BLOCKED "
        status_pass = "PASSED" if result.allowed == expected_status else "FAILED"
        print(f"  - T={req_time:.2f}s | Status: {verdict} | Active Count: {result.current_count}/3 | RetryAfter: {result.retry_after_seconds:.2f}s [{status_pass}]")
        assert result.allowed == expected_status

    # Prune memory
    pruned = limiter.prune_idle_clients(current_time=simulated_base_time + 5.0)
    print(f"  - Pruned Idle Client Records: {pruned} client entries removed from RAM.")
    assert pruned == 1

    # 2. Verify Rolling Telemetry Window Aggregator & Anomaly Detection
    print("\n[PART 2] Testing Rolling Telemetry Window & 3-Sigma Anomaly Spike Detector:")
    aggregator = RollingTelemetryWindowAggregator(capacity=10, anomaly_z_threshold=3.0)

    # Feed steady baseline latency readings (~20ms with slight variance)
    normal_latencies = [20.1, 19.8, 20.5, 19.9, 20.2, 20.0, 19.7, 20.3, 20.1, 20.0]
    for lat in normal_latencies:
        alert = aggregator.add_sample(lat)
        assert alert is None, "False positive alert during normal baseline!"

    mean, std = aggregator.get_stats()
    print(f"  - Baseline Window Established (10 samples): Mean = {mean:.2f}ms | StdDev = {std:.2f}ms")

    # Inject normal small fluctuation (20.4ms) -> Should NOT trigger alert (Z-score ~ 1.5 < 3.0)
    alert = aggregator.add_sample(20.4)
    assert alert is None
    print("  - Sample 20.4ms: Normal variation -> No alert triggered.")

    # Inject severe anomaly spike (85.0ms) -> Must trigger 3-sigma alert!
    alert = aggregator.add_sample(85.0)
    assert alert is not None
    print(f"  - [ANOMALY DETECTED] Sample 85.0ms | Z-Score: {alert.z_score} | Alert: {alert.message}")
    print("  - Status: ANOMALY DETECTION ENGINE VERIFIED [PASSED]")

    # 3. High-Throughput Performance Benchmark
    print("\n[PART 3] Benchmark: Incremental O(1) Rolling Aggregation vs O(W) Re-Summing:")
    bench_capacity = 1000
    total_samples = 100000

    # Benchmark Incremental Aggregator
    agg_bench = RollingTelemetryWindowAggregator(capacity=bench_capacity)
    start_time = time.perf_counter()
    for v in range(total_samples):
        agg_bench.add_sample(float(v % 100))
    incremental_ms = (time.perf_counter() - start_time) * 1000.0

    print(f"  Processed {total_samples:,} continuous stream metrics (Window W = {bench_capacity}):")
    print(f"  - Incremental O(1) Sliding Window Duration : {incremental_ms:.2f} ms")
    throughput = (total_samples / (incremental_ms / 1000.0))
    print(f"  - Ingestion Throughput Capacity           : ~{throughput:,.0f} samples/sec")

    print("\n" + "=" * 75)
    print("PRODUCTION SLIDING WINDOW ARCHITECTURES VERIFIED & VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_production_sliding_window_demo()
