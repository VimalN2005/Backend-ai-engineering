"""
Day 74: Production Circuit Breaker & Bulkhead Fault Isolation Simulator
========================================================================
Architecture & Principles Demonstrated:
1. Advanced Circuit Breaker Engine:
   - Sliding Window failure rate calculation (circular buffer).
   - Three-state lifecycle: CLOSED -> OPEN -> HALF-OPEN.
   - Failure rate threshold percentage with automatic trip.
   - Half-Open trial probe verification (requires N consecutive successes to close).
   - Graceful fallback execution when OPEN.
2. Semaphore Bulkhead Pattern:
   - Limits concurrent in-flight calls to downstream dependencies.
   - Rejects overflow requests immediately with BulkheadFullException.
"""

from __future__ import annotations
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# =====================================================================
# 1. Sliding Window & Circuit Breaker Engine
# =====================================================================

class CircuitBreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircularSlidingWindow:
    """Fixed-capacity rolling circular buffer of boolean outcomes."""
    def __init__(self, size: int = 10):
        self.size = size
        self.buffer: deque[bool] = deque(maxlen=size)

    def record(self, success: bool) -> None:
        self.buffer.append(success)

    @property
    def total_recorded(self) -> int:
        return len(self.buffer)

    @property
    def failure_rate(self) -> float:
        if not self.buffer:
            return 0.0
        failures = sum(1 for outcome in self.buffer if not outcome)
        return (failures / len(self.buffer)) * 100.0


class CircuitBreakerOpenException(Exception):
    """Raised when call is rejected because breaker is OPEN."""
    pass


class CircuitBreaker:
    """
    Production-grade Circuit Breaker with sliding window failure rate metrics,
    configurable sleep windows, and half-open trial probes.
    """
    def __init__(
        self,
        name: str,
        failure_rate_threshold: float = 50.0,
        sliding_window_size: int = 10,
        sleep_window_sec: float = 0.5,
        half_open_probe_trials: int = 3,
        fallback_handler: Optional[Callable[..., Any]] = None
    ):
        self.name = name
        self.failure_rate_threshold = failure_rate_threshold
        self.sliding_window = CircularSlidingWindow(size=sliding_window_size)
        self.sleep_window_sec = sleep_window_sec
        self.half_open_probe_trials = half_open_probe_trials
        self.fallback_handler = fallback_handler

        self.state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self.last_state_change: float = time.time()
        self.consecutive_probe_successes: int = 0
        self.lock = threading.Lock()

    def execute(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        with self.lock:
            now = time.time()

            # Check OPEN -> HALF-OPEN transition
            if self.state == CircuitBreakerState.OPEN:
                if now - self.last_state_change >= self.sleep_window_sec:
                    self._transition_to(CircuitBreakerState.HALF_OPEN)
                    self.consecutive_probe_successes = 0
                else:
                    if self.fallback_handler:
                        return self.fallback_handler(*args, **kwargs)
                    raise CircuitBreakerOpenException(f"Circuit Breaker '{self.name}' is OPEN")

        # Execute downstream call outside lock to prevent blocking
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            if self.fallback_handler:
                return self.fallback_handler(*args, **kwargs)
            raise exc

    def _on_success(self) -> None:
        with self.lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.consecutive_probe_successes += 1
                if self.consecutive_probe_successes >= self.half_open_probe_trials:
                    self._transition_to(CircuitBreakerState.CLOSED)
                    self.sliding_window.buffer.clear()
            elif self.state == CircuitBreakerState.CLOSED:
                self.sliding_window.record(success=True)

    def _on_failure(self) -> None:
        with self.lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                # Any failure in HALF_OPEN immediately re-trips to OPEN
                self._transition_to(CircuitBreakerState.OPEN)
            elif self.state == CircuitBreakerState.CLOSED:
                self.sliding_window.record(success=False)
                # Check threshold once buffer is populated
                if self.sliding_window.total_recorded >= 5:
                    if self.sliding_window.failure_rate >= self.failure_rate_threshold:
                        self._transition_to(CircuitBreakerState.OPEN)

    def _transition_to(self, new_state: CircuitBreakerState) -> None:
        self.state = new_state
        self.last_state_change = time.time()


# =====================================================================
# 2. Bulkhead Pattern (Concurrency Limiter)
# =====================================================================

class BulkheadFullException(Exception):
    """Raised when dependency concurrency limit is reached."""
    pass


class SemaphoreBulkhead:
    """
    Limits concurrent in-flight requests to a dependency without thread allocation overhead.
    """
    def __init__(self, name: str, max_concurrent: int = 3):
        self.name = name
        self.max_concurrent = max_concurrent
        self._current_inflight = 0
        self._lock = threading.Lock()

    def execute(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        with self._lock:
            if self._current_inflight >= self.max_concurrent:
                raise BulkheadFullException(
                    f"Bulkhead '{self.name}' is FULL: {self._current_inflight}/{self.max_concurrent} concurrent calls active"
                )
            self._current_inflight += 1

        try:
            return func(*args, **kwargs)
        finally:
            with self._lock:
                self._current_inflight -= 1


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_circuit_breaker_lifecycle():
    print("\n--- 1. CIRCUIT BREAKER SLIDING WINDOW & LIFECYCLE DEMO ---")
    
    # Mock fallback returning cached response
    def fallback_orders(*args, **kwargs):
        return {"orders": ["cached_order_1"], "source": "STALE_CACHE_FALLBACK"}

    cb = CircuitBreaker(
        name="OrdersService",
        failure_rate_threshold=50.0,
        sliding_window_size=10,
        sleep_window_sec=0.2,
        half_open_probe_trials=2,
        fallback_handler=fallback_orders
    )

    should_fail = False

    def downstream_call():
        if should_fail:
            raise ConnectionError("Downstream DB timed out")
        return {"orders": ["live_order_101"], "source": "LIVE_DATABASE"}

    # 1. State CLOSED: 4 successes
    for _ in range(4):
        resp = cb.execute(downstream_call)
        assert resp["source"] == "LIVE_DATABASE"
    assert cb.state == CircuitBreakerState.CLOSED
    print(f"Initial state: {cb.state} (Failure rate: {cb.sliding_window.failure_rate:.1f}%)")

    # 2. Downstream service degrades -> inject failures
    should_fail = True
    for _ in range(5):
        resp = cb.execute(downstream_call)
        assert resp["source"] == "STALE_CACHE_FALLBACK"

    # Circuit Breaker must have tripped to OPEN
    print(f"State after failures: {cb.state} (Failure rate: {cb.sliding_window.failure_rate:.1f}%)")
    assert cb.state == CircuitBreakerState.OPEN

    # 3. In OPEN state: fast-fails immediately without calling downstream
    start_t = time.time()
    fast_fail_resp = cb.execute(downstream_call)
    elapsed = time.time() - start_t
    assert fast_fail_resp["source"] == "STALE_CACHE_FALLBACK"
    assert elapsed < 0.01  # Immediate return
    print(f"Fast-fail executed in {elapsed*1000:.2f}ms: returned {fast_fail_resp['source']}")

    # 4. Sleep window expires (0.2s) -> transition to HALF-OPEN on next request
    time.sleep(0.25)
    should_fail = False  # Downstream recovered!

    probe_1 = cb.execute(downstream_call)
    assert cb.state == CircuitBreakerState.HALF_OPEN
    assert probe_1["source"] == "LIVE_DATABASE"
    print(f"Trial probe 1 in {cb.state}: succeeded.")

    probe_2 = cb.execute(downstream_call)
    assert probe_2["source"] == "LIVE_DATABASE"
    print(f"Trial probe 2 in {cb.state}: succeeded.")

    # 2 consecutive probe successes must reset breaker back to CLOSED
    assert cb.state == CircuitBreakerState.CLOSED
    print(f"Circuit Breaker successfully recovered back to {cb.state}!")


def demo_bulkhead_fault_isolation():
    print("\n--- 2. BULKHEAD CONCURRENCY ISOLATION DEMO ---")
    bulkhead = SemaphoreBulkhead(name="PaymentGatewayPool", max_concurrent=2)

    active_tasks = 0
    barrier = threading.Barrier(2)

    def slow_payment_call(task_id: int):
        nonlocal active_tasks
        def work():
            nonlocal active_tasks
            active_tasks += 1
            barrier.wait()  # Hold both workers in-flight
            time.sleep(0.05)
            active_tasks -= 1
            return f"task_{task_id}_done"
        return bulkhead.execute(work)

    # Launch 2 background threads saturating the bulkhead (max_concurrent = 2)
    t1 = threading.Thread(target=slow_payment_call, args=(1,))
    t2 = threading.Thread(target=slow_payment_call, args=(2,))
    t1.start()
    t2.start()

    time.sleep(0.01)  # Ensure threads entered bulkhead

    # 3rd request arrives while bulkhead is full -> must be rejected immediately!
    print("Dispatching 3rd request against saturated Bulkhead (capacity=2)...")
    rejected = False
    try:
        bulkhead.execute(lambda: "overflow_call")
    except BulkheadFullException as bfe:
        rejected = True
        print(f"Bulkhead successfully rejected overflow: {bfe}")

    assert rejected is True, "Bulkhead must reject overflow requests"

    t1.join()
    t2.join()

    # Once threads complete, new requests are accepted
    resp_after = bulkhead.execute(lambda: "healthy_call")
    assert resp_after == "healthy_call"
    print("Bulkhead capacity freed; subsequent calls processed normally.")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING CIRCUIT BREAKER & BULKHEAD RESILIENCE SIMULATOR")
    print("=" * 70)
    demo_circuit_breaker_lifecycle()
    demo_bulkhead_fault_isolation()
    print("\n" + "=" * 70)
    print("ALL CIRCUIT BREAKER & BULKHEAD TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
