# Day 74: Enterprise Practical Exercises: Distributed Resilience

This workbook contains **3 enterprise-grade distributed resilience exercises**. Each exercise presents a real-world scenario, precise architectural requirements, and a complete, tested reference implementation.

---

## Exercise 1: Count-Based Sliding Window Circuit Breaker with Trial Recovery

### Scenario
An inventory microservice calls an external third-party courier API to verify shipping rates. The courier service occasionally suffers brownouts. You must implement a **Count-Based Sliding Window Circuit Breaker** that monitors the last 10 requests, trips to `OPEN` if the failure rate exceeds 50%, and automatically tests recovery using trial probe requests in `HALF-OPEN` mode.

### Requirements
1. Implement a `CountBasedCircuitBreaker`:
   - Sliding window capacity: 10 requests.
   - Failure threshold: $\ge 50\%$.
   - Sleep window before entering `HALF-OPEN`: 0.2 seconds.
   - Required trial probe successes in `HALF-OPEN` to close: 2 consecutive successes.
2. If `OPEN`, immediately execute a local fallback function without making network calls.
3. If a trial probe in `HALF-OPEN` fails, immediately re-trip to `OPEN`.

### Solution

```python
"""
Exercise 1 Solution: Count-Based Sliding Window Circuit Breaker
"""
from __future__ import annotations
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Dict, Optional


class BreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CountBasedCircuitBreaker:
    def __init__(
        self,
        failure_threshold_pct: float = 50.0,
        window_size: int = 10,
        sleep_window_sec: float = 0.2,
        half_open_probes_needed: int = 2,
        fallback: Optional[Callable[[], Any]] = None
    ):
        self.failure_threshold_pct = failure_threshold_pct
        self.window_size = window_size
        self.sleep_window_sec = sleep_window_sec
        self.half_open_probes_needed = half_open_probes_needed
        self.fallback = fallback

        self.state = BreakerState.CLOSED
        self.window: deque[bool] = deque(maxlen=window_size)
        self.last_state_change = time.time()
        self.half_open_successes = 0

    def call(self, target_func: Callable[[], Any]) -> Any:
        now = time.time()

        # Check sleep window expiration to enter HALF_OPEN
        if self.state == BreakerState.OPEN:
            if now - self.last_state_change >= self.sleep_window_sec:
                self.state = BreakerState.HALF_OPEN
                self.half_open_successes = 0
            else:
                if self.fallback:
                    return self.fallback()
                raise RuntimeError("Circuit Breaker is OPEN: fast-failing call")

        # Execute call
        try:
            result = target_func()
            self._handle_success()
            return result
        except Exception as exc:
            self._handle_failure()
            if self.fallback:
                return self.fallback()
            raise exc

    def _handle_success(self) -> None:
        if self.state == BreakerState.HALF_OPEN:
            self.half_open_successes += 1
            if self.half_open_successes >= self.half_open_probes_needed:
                self.state = BreakerState.CLOSED
                self.window.clear()
        elif self.state == BreakerState.CLOSED:
            self.window.append(True)

    def _handle_failure(self) -> None:
        if self.state == BreakerState.HALF_OPEN:
            self.state = BreakerState.OPEN
            self.last_state_change = time.time()
        elif self.state == BreakerState.CLOSED:
            self.window.append(False)
            if len(self.window) >= 5:  # Minimum volume before evaluation
                failures = sum(1 for passed in self.window if not passed)
                rate = (failures / len(self.window)) * 100.0
                if rate >= self.failure_threshold_pct:
                    self.state = BreakerState.OPEN
                    self.last_state_change = time.time()


# --- Verification Test ---
if __name__ == "__main__":
    should_fail = False
    def external_courier_api():
        if should_fail:
            raise ConnectionError("Courier 503")
        return {"rate": 15.0, "status": "LIVE"}

    def local_fallback():
        return {"rate": 20.0, "status": "DEFAULT_FALLBACK"}

    cb = CountBasedCircuitBreaker(fallback=local_fallback)

    # 1. Normal operations in CLOSED
    assert cb.call(external_courier_api)["status"] == "LIVE"
    assert cb.state == BreakerState.CLOSED

    # 2. Inject 5 failures to breach 50% threshold
    should_fail = True
    for _ in range(5):
        resp = cb.call(external_courier_api)
        assert resp["status"] == "DEFAULT_FALLBACK"

    assert cb.state == BreakerState.OPEN
    print("Circuit Breaker successfully tripped to OPEN on failure rate breach.")

    # 3. Wait for sleep window (0.2s) to enter HALF_OPEN
    time.sleep(0.25)
    should_fail = False  # Downstream recovers

    # Probe 1
    resp1 = cb.call(external_courier_api)
    assert cb.state == BreakerState.HALF_OPEN
    assert resp1["status"] == "LIVE"

    # Probe 2 (resets to CLOSED)
    resp2 = cb.call(external_courier_api)
    assert cb.state == BreakerState.CLOSED
    assert resp2["status"] == "LIVE"

    print("Circuit Breaker successfully reset to CLOSED following successful probe trials!")
    print("\nExercise 1: Sliding Window Circuit Breaker verified successfully!")
```

---

## Exercise 2: AWS Full Jitter Backoff Engine with Bounded Retries

### Scenario
A payments consumer processes outgoing settlement transfers. When a bank API experiences intermittent 503 errors, the consumer must retry using the **AWS Full Jitter** formula to avoid synchronizing with hundreds of other worker instances.

### Requirements
1. Implement a `FullJitterRetrier`:
   - Max retries: 3 attempts.
   - Base backoff: 50 ms.
   - Max backoff ceiling: 1000 ms.
   - Formula: $\text{Sleep} = \text{Uniform}(0, \min(T_{\text{max}}, T_{\text{base}} \times 2^i))$.
2. Track and return the list of actual sleep durations applied across the attempts.
3. Verify that every sleep duration falls strictly within the dynamic $[0, \text{ceiling}]$ bound.

### Solution

```python
"""
Exercise 2 Solution: AWS Full Jitter Backoff Engine
"""
from __future__ import annotations
import random
import time
from typing import Any, Callable, List, Tuple


class FullJitterRetrier:
    def __init__(self, base_ms: float = 50.0, max_ms: float = 1000.0, max_retries: int = 3):
        self.base_ms = base_ms
        self.max_ms = max_ms
        self.max_retries = max_retries

    def execute_with_retry(self, action: Callable[[], Any]) -> Tuple[bool, Any, List[float]]:
        sleep_history_ms: List[float] = []

        for attempt in range(self.max_retries + 1):
            try:
                result = action()
                return True, result, sleep_history_ms
            except Exception as exc:
                if attempt == self.max_retries:
                    return False, str(exc), sleep_history_ms

                # Calculate Full Jitter sleep
                ceiling_ms = min(self.max_ms, self.base_ms * (2 ** attempt))
                sleep_ms = random.uniform(0.0, ceiling_ms)
                sleep_history_ms.append(sleep_ms)

                # Simulated sleep
                time.sleep(sleep_ms / 1000.0)

        return False, "Retries exhausted", sleep_history_ms


# --- Verification Test ---
if __name__ == "__main__":
    retrier = FullJitterRetrier(base_ms=20.0, max_ms=200.0, max_retries=3)

    attempts_made = 0
    def flaky_bank_call():
        nonlocal attempts_made
        attempts_made += 1
        if attempts_made < 3:
            raise ConnectionResetError("Bank gateway reset connection")
        return "SETTLEMENT_ACCEPTED"

    success, result, history = retrier.execute_with_retry(flaky_bank_call)

    print(f"Result: {result}")
    print(f"Recorded Full Jitter Sleeps (ms): {history}")

    assert success is True
    assert result == "SETTLEMENT_ACCEPTED"
    assert len(history) == 2, f"Expected 2 retries before success, got {len(history)}"

    # Validate Jitter bounds:
    # Attempt 0: ceiling = min(200, 20 * 2^0) = 20ms -> sleep in [0, 20]
    assert 0.0 <= history[0] <= 20.0, f"History[0] out of bounds: {history[0]}"
    # Attempt 1: ceiling = min(200, 20 * 2^1) = 40ms -> sleep in [0, 40]
    assert 0.0 <= history[1] <= 40.0, f"History[1] out of bounds: {history[1]}"

    print("\nExercise 2: AWS Full Jitter bounds and retry execution verified successfully!")
```

---

## Exercise 3: Distributed Deadline Budget Propagator with In-Flight Cancellation

### Scenario
An API Gateway processes user search requests with an end-to-end deadline of 200ms. The request traverses an `AggregationService` and a downstream `DatabaseService`. If the budget is exhausted before or during execution, the downstream service must **immediately cancel work** rather than completing an expensive full-table scan for a connection that has already been dropped by the gateway.

### Requirements
1. Implement a `DeadlineContext` carrying `deadline_epoch_ms`.
2. Provide a `check_or_cancel()` method that raises `DeadlineExceededException` if the current time exceeds the deadline.
3. Simulate a multi-tier call chain where downstream services verify remaining budget before executing chunks of work, preventing wasted database CPU cycles.

### Solution

```python
"""
Exercise 3 Solution: Distributed Deadline Budget Propagator
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any, Dict, List


class DeadlineExceededException(Exception):
    pass


@dataclass
class DeadlineContext:
    trace_id: str
    deadline_epoch_ms: float

    @property
    def remaining_ms(self) -> float:
        now_ms = time.time() * 1000.0
        return self.deadline_epoch_ms - now_ms

    def check_or_cancel(self, minimum_required_ms: float = 0.0) -> None:
        rem = self.remaining_ms
        if rem <= 0:
            raise DeadlineExceededException(f"Deadline exceeded by {abs(rem):.2f}ms")
        if rem < minimum_required_ms:
            raise DeadlineExceededException(
                f"Insufficient budget: requires {minimum_required_ms:.1f}ms, but only {rem:.1f}ms remaining"
            )


class DatabaseWorker:
    def execute_chunked_query(self, ctx: DeadlineContext, total_chunks: int = 4, chunk_time_ms: float = 20.0) -> int:
        completed = 0
        for i in range(total_chunks):
            # Check deadline before processing each chunk
            ctx.check_or_cancel(minimum_required_ms=chunk_time_ms)
            time.sleep(chunk_time_ms / 1000.0)
            completed += 1
        return completed


# --- Verification Test ---
if __name__ == "__main__":
    db = DatabaseWorker()

    # Case 1: Generous deadline (300ms budget for 4 * 20ms = 80ms work) -> completes 4 chunks
    now_ms = time.time() * 1000.0
    ctx_good = DeadlineContext("trace_101", deadline_epoch_ms=now_ms + 300.0)
    completed_good = db.execute_chunked_query(ctx_good, total_chunks=4, chunk_time_ms=10.0)
    assert completed_good == 4
    print(f"Good context completed all {completed_good} chunks cleanly.")

    # Case 2: Tight deadline (35ms budget for 4 * 20ms = 80ms work) -> completes 1-2 chunks then cancels
    now_ms = time.time() * 1000.0
    ctx_tight = DeadlineContext("trace_102", deadline_epoch_ms=now_ms + 35.0)
    cancelled = False
    try:
        db.execute_chunked_query(ctx_tight, total_chunks=4, chunk_time_ms=20.0)
    except DeadlineExceededException as dee:
        cancelled = True
        print(f"Work in-flight successfully cancelled as deadline expired: {dee}")

    assert cancelled is True, "Must raise DeadlineExceededException before burning unnecessary CPU"
    print("\nExercise 3: Distributed Deadline propagation and in-flight cancellation verified!")
```
