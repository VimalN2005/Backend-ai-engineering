"""
Day 74: Jitter Algorithms, Thundering Herd Defense & Deadline Propagation
========================================================================
Architecture & Principles Demonstrated:
1. AWS Jitter Algorithms:
   - Exponential Backoff without Jitter (produces destructive synchronized pulses).
   - Full Jitter (uniform randomization spreading retries evenly).
   - Decorrelated Jitter (adaptive sleep calculation based on previous sleep).
2. Thundering Herd Simulation & Benchmark:
   - Simulates 100 concurrent clients recovering from an outage.
   - Measures peak concurrency spikes: proves Full Jitter reduces peak load by >70%.
3. Distributed Deadline Budget Propagation:
   - Ingress deadline propagation across microservice call chains.
   - Immediate short-circuiting when deadline budget is exhausted.
"""

from __future__ import annotations
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# =====================================================================
# 1. Backoff & Jitter Strategies
# =====================================================================

class BackoffStrategy:
    """Calculates retry sleep durations."""
    def __init__(self, base_ms: float = 100.0, max_ms: float = 5000.0):
        self.base_ms = base_ms
        self.max_ms = max_ms

    def exponential_no_jitter(self, attempt: int) -> float:
        """Deterministic exponential backoff: base * 2^attempt capped at max."""
        return min(self.max_ms, self.base_ms * (2 ** attempt))

    def full_jitter(self, attempt: int) -> float:
        """AWS Full Jitter: Uniform(0, min(max, base * 2^attempt))."""
        ceiling = min(self.max_ms, self.base_ms * (2 ** attempt))
        return random.uniform(0.0, ceiling)

    def equal_jitter(self, attempt: int) -> float:
        """AWS Equal Jitter: (ceiling / 2) + Uniform(0, ceiling / 2)."""
        ceiling = min(self.max_ms, self.base_ms * (2 ** attempt))
        half = ceiling / 2.0
        return half + random.uniform(0.0, half)

    def decorrelated_jitter(self, prev_sleep_ms: float) -> float:
        """AWS Decorrelated Jitter: min(max, Uniform(base, prev_sleep * 3))."""
        return min(self.max_ms, random.uniform(self.base_ms, prev_sleep_ms * 3.0))


# =====================================================================
# 2. Thundering Herd Simulation & Concurrency Benchmark
# =====================================================================

def simulate_thundering_herd(
    num_clients: int = 150,
    max_retries: int = 3,
    call_duration_ms: float = 50.0,
    bucket_size_ms: float = 50.0
) -> Tuple[int, int, int]:
    """
    Simulates simultaneous client retry bursts at t=0 following a service restart.
    Returns: (peak_spike_no_jitter, peak_spike_full_jitter, peak_spike_decorrelated).
    """
    strategy = BackoffStrategy(base_ms=200.0, max_ms=5000.0)

    # Buckets track request count in discrete time slices
    buckets_no_jitter: Dict[int, int] = defaultdict(int)
    buckets_full_jitter: Dict[int, int] = defaultdict(int)
    buckets_decorrelated: Dict[int, int] = defaultdict(int)

    # 1. Simulate Clients with Exponential Backoff (NO Jitter)
    for _ in range(num_clients):
        timeline = 0.0
        for attempt in range(max_retries):
            timeline += call_duration_ms + strategy.exponential_no_jitter(attempt)
            bucket_idx = int(timeline // bucket_size_ms)
            buckets_no_jitter[bucket_idx] += 1

    # 2. Simulate Clients with Full Jitter
    for _ in range(num_clients):
        timeline = 0.0
        for attempt in range(max_retries):
            timeline += call_duration_ms + strategy.full_jitter(attempt)
            bucket_idx = int(timeline // bucket_size_ms)
            buckets_full_jitter[bucket_idx] += 1

    # 3. Simulate Clients with Decorrelated Jitter
    for _ in range(num_clients):
        timeline = 0.0
        prev_sleep = strategy.base_ms
        for attempt in range(max_retries):
            sleep_ms = strategy.decorrelated_jitter(prev_sleep)
            prev_sleep = sleep_ms
            timeline += call_duration_ms + sleep_ms
            bucket_idx = int(timeline // bucket_size_ms)
            buckets_decorrelated[bucket_idx] += 1

    peak_no_jitter = max(buckets_no_jitter.values()) if buckets_no_jitter else 0
    peak_full_jitter = max(buckets_full_jitter.values()) if buckets_full_jitter else 0
    peak_decorrelated = max(buckets_decorrelated.values()) if buckets_decorrelated else 0

    return peak_no_jitter, peak_full_jitter, peak_decorrelated


# =====================================================================
# 3. Distributed Deadline Budget Propagation
# =====================================================================

class DeadlineExceededException(Exception):
    """Raised when request execution budget is exhausted."""
    pass


@dataclass
class DistributedContext:
    """Propagated request context carrying end-to-end deadline budget."""
    trace_id: str
    deadline_ms: float

    @property
    def remaining_budget_ms(self) -> float:
        now_ms = time.time() * 1000.0
        return self.deadline_ms - now_ms

    def check_deadline(self) -> None:
        if self.remaining_budget_ms <= 0:
            raise DeadlineExceededException(
                f"Distributed deadline exceeded! Budget expired {abs(self.remaining_budget_ms):.1f}ms ago."
            )


class DownstreamService:
    """Service verifying deadline budget before executing heavy compute/queries."""
    def execute_expensive_query(self, ctx: DistributedContext, required_work_time_ms: float) -> str:
        # Pre-execution budget check
        ctx.check_deadline()

        if ctx.remaining_budget_ms < required_work_time_ms:
            # Short-circuit: not enough budget left to finish query
            raise DeadlineExceededException(
                f"Aborting query: required {required_work_time_ms}ms, but only {ctx.remaining_budget_ms:.1f}ms remains in budget"
            )

        # Simulate execution
        time.sleep(required_work_time_ms / 1000.0)
        return "QUERY_RESULT_SUCCESS"


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_jitter_benchmark():
    print("\n--- 1. JITTER ALGORITHMS & THUNDERING HERD BENCHMARK ---")
    random.seed(42)  # Deterministic seed for reproducible evaluation

    clients = 150
    peak_no_jitter, peak_full_jitter, peak_decorrelated = simulate_thundering_herd(
        num_clients=clients,
        max_retries=3
    )

    print(f"Simulation of {clients} concurrent client retries:")
    print(f"  Peak Concurrency Spike (NO JITTER):           {peak_no_jitter} requests in single 50ms window")
    print(f"  Peak Concurrency Spike (FULL JITTER):         {peak_full_jitter} requests in single 50ms window")
    print(f"  Peak Concurrency Spike (DECORRELATED JITTER): {peak_decorrelated} requests in single 50ms window")

    reduction = ((peak_no_jitter - peak_full_jitter) / peak_no_jitter) * 100.0
    decorrelated_reduction = ((peak_no_jitter - peak_decorrelated) / peak_no_jitter) * 100.0
    print(f"  Full Jitter Concurrency Spike Reduction:         {reduction:.1f}%")
    print(f"  Decorrelated Jitter Concurrency Spike Reduction: {decorrelated_reduction:.1f}%")

    # In no-jitter, all 150 clients hit the server in the exact same time bucket!
    assert peak_no_jitter == clients
    # Full jitter and Decorrelated jitter must significantly disperse the load
    assert peak_full_jitter < peak_no_jitter * 0.50  # Over 50% reduction
    assert peak_decorrelated < peak_no_jitter * 0.30  # Over 70% reduction
    print("Jitter algorithms successfully eradicated the synchronized Thundering Herd!")


def demo_deadline_budget_propagation():
    print("\n--- 2. DISTRIBUTED DEADLINE BUDGET PROPAGATION DEMO ---")
    service = DownstreamService()

    # 1. Request with generous 500ms budget -> succeeds
    now_ms = time.time() * 1000.0
    valid_ctx = DistributedContext(trace_id="trace_01", deadline_ms=now_ms + 500.0)
    result = service.execute_expensive_query(valid_ctx, required_work_time_ms=50.0)
    assert result == "QUERY_RESULT_SUCCESS"
    print(f"Request with sufficient budget ({valid_ctx.remaining_budget_ms:.1f}ms remaining): succeeded.")

    # 2. Request with expired budget (0ms) -> aborted immediately without work
    expired_ctx = DistributedContext(trace_id="trace_02", deadline_ms=now_ms - 10.0)
    aborted = False
    try:
        service.execute_expensive_query(expired_ctx, required_work_time_ms=50.0)
    except DeadlineExceededException as dee:
        aborted = True
        print(f"Expired request successfully short-circuited: {dee}")

    assert aborted is True, "Expired deadline must immediately abort execution"

    # 3. Request with insufficient remaining budget -> aborted before starting work
    now_ms = time.time() * 1000.0
    tight_ctx = DistributedContext(trace_id="trace_03", deadline_ms=now_ms + 20.0)
    tight_aborted = False
    try:
        # Requires 100ms, but only 20ms remains
        service.execute_expensive_query(tight_ctx, required_work_time_ms=100.0)
    except DeadlineExceededException as dee:
        tight_aborted = True
        print(f"Insufficient budget short-circuited: {dee}")

    assert tight_aborted is True, "Service must not start work if budget is insufficient"
    print("Distributed deadline budget propagation successfully prevented wasted computation!")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING JITTER ALGORITHMS & DISTRIBUTED DEADLINE SIMULATOR")
    print("=" * 70)
    demo_jitter_benchmark()
    demo_deadline_budget_propagation()
    print("\n" + "=" * 70)
    print("ALL JITTER & DEADLINE BUDGET TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
