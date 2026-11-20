"""
Day 75: Capstone Resilience, Chaos Engineering & Stress Benchmark
=================================================================
Architecture & Principles Demonstrated:
1. Chaos Engineering Harness:
   - Random transient service brownouts (simulated downstream 503s).
   - Network latency jitter injection.
2. Production Defense Integration:
   - Circuit Breaker fast-fail.
   - Semaphore Bulkhead concurrency cap.
   - Full Jitter retry backoff.
"""

from __future__ import annotations
import random
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


class ChaosFailureException(Exception):
    pass


class ChaosInjectionProxy:
    """Injects artificial latency and random transient failures to test resilience."""
    def __init__(self, failure_rate_pct: float = 15.0, min_latency_ms: float = 5.0, max_latency_ms: float = 30.0):
        self.failure_rate_pct = failure_rate_pct
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max_latency_ms

    def invoke(self, func: Callable[..., Any], req_id: int = 0) -> Any:
        # Simulate network jitter
        jitter_ms = random.uniform(self.min_latency_ms, self.max_latency_ms)
        time.sleep(jitter_ms / 1000.0)

        # Inject concentrated brownout burst between requests 15 and 20
        if 15 <= req_id <= 20:
            raise ChaosFailureException(f"Downstream service brownout active during request {req_id}")

        # Inject background ambient transient failures (10%)
        if random.uniform(0.0, 100.0) < self.failure_rate_pct:
            raise ChaosFailureException("Chaos monkey injected transient 503 Service Unavailable")

        return func()


# =====================================================================
# 2. Resilient Client with Circuit Breaker & Retry
# =====================================================================

class ProductionResilienceEngine:
    """Wraps calls with a Circuit Breaker and Full Jitter retries."""
    def __init__(self, failure_threshold_pct: float = 40.0, sleep_window_sec: float = 0.2):
        self.failure_threshold_pct = failure_threshold_pct
        self.sleep_window_sec = sleep_window_sec
        self.is_open = False
        self.last_failure_time = 0.0
        self.window = deque(maxlen=10)

    def execute_with_resilience(self, target: Callable[[], Any], max_retries: int = 2) -> Tuple[bool, Any, str]:
        now = time.time()
        # Fast-fail if circuit is OPEN
        if self.is_open:
            if now - self.last_failure_time < self.sleep_window_sec:
                return False, None, "FAST_FAIL_CIRCUIT_OPEN"
            else:
                self.is_open = False  # Probe reset

        for attempt in range(max_retries + 1):
            try:
                res = target()
                self.window.append(True)
                return True, res, "SUCCESS"
            except ChaosFailureException:
                if attempt == max_retries:
                    self.window.append(False)
                    if len(self.window) >= 5:
                        fail_rate = (sum(1 for p in self.window if not p) / len(self.window)) * 100.0
                        if fail_rate >= self.failure_threshold_pct:
                            self.is_open = True
                            self.last_failure_time = time.time()
                    return False, None, "RETRIES_EXHAUSTED"
                
                # Full Jitter backoff (between 5ms and 20ms)
                time.sleep(random.uniform(0.005, 0.020))

        return False, None, "RETRIES_EXHAUSTED"


# =====================================================================
# 3. Chaos Stress Benchmark Suite
# =====================================================================

def run_chaos_benchmark(num_requests: int = 60) -> Dict[str, Any]:
    print(f"\n--- RUNNING CAPSTONE CHAOS STRESS BENCHMARK ({num_requests} TRANSACTIONS) ---")
    random.seed(42)

    chaos_proxy = ChaosInjectionProxy(failure_rate_pct=10.0, min_latency_ms=1.0, max_latency_ms=5.0)
    resilience = ProductionResilienceEngine(failure_threshold_pct=50.0, sleep_window_sec=0.04)

    latencies_ms: List[float] = []
    success_count = 0
    fast_fail_count = 0
    retry_exhausted_count = 0
    dlq_quarantine: List[int] = []

    for req_id in range(num_requests):
        time.sleep(0.004)  # Natural arrival cadence
        start_t = time.perf_counter()
        
        # Simulating payment call through chaos proxy with request tracking
        target_call = lambda: chaos_proxy.invoke(lambda: f"order_{req_id}_processed", req_id=req_id)
        success, res, status = resilience.execute_with_resilience(target_call, max_retries=2)
        
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        latencies_ms.append(elapsed_ms)

        if success:
            success_count += 1
        elif status == "FAST_FAIL_CIRCUIT_OPEN":
            fast_fail_count += 1
            dlq_quarantine.append(req_id)
        else:
            retry_exhausted_count += 1
            dlq_quarantine.append(req_id)

    # Compute percentiles
    latencies_sorted = sorted(latencies_ms)
    p50 = statistics.median(latencies_sorted)
    p90 = latencies_sorted[int(len(latencies_sorted) * 0.90)]
    p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]

    print(f"Total Transactions:        {num_requests}")
    print(f"Successful Transactions:   {success_count} ({success_count/num_requests*100:.1f}%)")
    print(f"Fast-Failed (Circuit OPEN):{fast_fail_count}")
    print(f"Retries Exhausted:         {retry_exhausted_count}")
    print(f"DLQ Quarantine Size:       {len(dlq_quarantine)}")
    print(f"Latency P50:               {p50:.2f} ms")
    print(f"Latency P90:               {p90:.2f} ms")
    print(f"Latency P99:               {p99:.2f} ms")

    return {
        "success_count": success_count,
        "fast_fail_count": fast_fail_count,
        "dlq_size": len(dlq_quarantine),
        "p50": p50,
        "p99": p99
    }


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING CAPSTONE 3: CHAOS ENGINEERING & RESILIENCE BENCHMARK")
    print("=" * 70)
    metrics = run_chaos_benchmark(num_requests=60)
    assert metrics["success_count"] > 35, "Majority of calls must succeed despite 20% chaos injection"
    assert metrics["fast_fail_count"] > 0, "Circuit Breaker must have fast-failed during chaos bursts"
    assert metrics["dlq_size"] > 0, "Failed requests must be safely quarantined to DLQ"
    print("\n" + "=" * 70)
    print("CAPSTONE 3 CHAOS BENCHMARK VERIFIED SUCCESSFULLY!")
    print("=" * 70)
