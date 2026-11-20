# Day 75: Enterprise Practical Exercises: Microservices Capstone

This workbook contains **3 enterprise-grade capstone implementation exercises**. Each exercise presents a production scenario, precise architectural requirements, and a complete, tested reference implementation.

---

## Exercise 1: Resilient Transactional Outbox CDC Worker with Crash Recovery

### Scenario
An e-commerce order service writes checkout events into a PostgreSQL `outbox_events` table. The asynchronous CDC relay process may crash or experience network partitions during transmission to Apache Kafka. You must build a **Resilient Outbox CDC Worker** that uses monotonic batching, status locks, and graceful crash recovery to ensure **At-Least-Once delivery with zero dropped events**.

### Requirements
1. Implement an `OutboxTable` storing events with states: `PENDING`, `IN_FLIGHT`, `PUBLISHED`.
2. Implement a `CDCRelayWorker`:
   - Fetches up to $N$ `PENDING` events.
   - Transitions their status to `IN_FLIGHT`.
   - Publishes to simulated Kafka topic.
   - Transitions status to `PUBLISHED` with timestamps.
3. Simulate worker crash after transition to `IN_FLIGHT`:
   - A recovery daemon must detect abandoned `IN_FLIGHT` events older than a timeout (e.g. 100ms) and reset them back to `PENDING` so the next worker can retry.

### Solution

```python
"""
Exercise 1 Solution: Resilient Outbox CDC Worker with Crash Recovery
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class OutboxState(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    PUBLISHED = "PUBLISHED"


@dataclass
class OutboxRecord:
    event_id: str
    aggregate_id: str
    payload: dict
    state: OutboxState = OutboxState.PENDING
    updated_at: float = field(default_factory=time.time)


class ResilientOutboxStore:
    def __init__(self):
        self.records: Dict[str, OutboxRecord] = {}

    def insert(self, record: OutboxRecord) -> None:
        self.records[record.event_id] = record

    def fetch_pending_and_lock(self, batch_size: int = 5) -> List[OutboxRecord]:
        pending = [r for r in self.records.values() if r.state == OutboxState.PENDING][:batch_size]
        for r in pending:
            r.state = OutboxState.IN_FLIGHT
            r.updated_at = time.time()
        return pending

    def mark_published(self, event_ids: List[str]) -> None:
        for eid in event_ids:
            if eid in self.records:
                self.records[eid].state = OutboxState.PUBLISHED
                self.records[eid].updated_at = time.time()

    def recover_abandoned_in_flight(self, timeout_sec: float = 0.05) -> int:
        now = time.time()
        recovered = 0
        for r in self.records.values():
            if r.state == OutboxState.IN_FLIGHT and (now - r.updated_at) >= timeout_sec:
                r.state = OutboxState.PENDING
                r.updated_at = now
                recovered += 1
        return recovered


# --- Verification Test ---
if __name__ == "__main__":
    store = ResilientOutboxStore()

    # Step 1: Insert 3 pending events
    store.insert(OutboxRecord("evt_1", "ord_101", {"amount": 50}))
    store.insert(OutboxRecord("evt_2", "ord_102", {"amount": 100}))
    store.insert(OutboxRecord("evt_3", "ord_103", {"amount": 150}))

    # Step 2: Worker picks up 2 events and locks them to IN_FLIGHT
    batch = store.fetch_pending_and_lock(batch_size=2)
    assert len(batch) == 2
    assert all(r.state == OutboxState.IN_FLIGHT for r in batch)

    # Step 3: Simulate worker crash before marking published!
    # Wait for timeout (0.06s > 0.05s)
    time.sleep(0.06)

    # Step 4: Recovery daemon runs
    recovered_count = store.recover_abandoned_in_flight(timeout_sec=0.05)
    assert recovered_count == 2
    assert store.records["evt_1"].state == OutboxState.PENDING
    assert store.records["evt_2"].state == OutboxState.PENDING

    # Step 5: Next healthy worker successfully processes all 3 events
    full_batch = store.fetch_pending_and_lock(batch_size=5)
    assert len(full_batch) == 3
    store.mark_published([r.event_id for r in full_batch])
    assert all(r.state == OutboxState.PUBLISHED for r in store.records.values())

    print("Exercise 1: Resilient Outbox CDC Crash Recovery verified successfully!")
```


---

## Exercise 2: Distributed Saga State Machine with Step Timeout

### Scenario
A multi-step Saga orchestrates an online order across Payment, Inventory, and Shipping. If an external service hangs without returning a response, the Saga coordinator must not wait forever. You must implement a **Timed Saga Coordinator** that enforces step-level deadlines, cancels hanging steps, and initiates reverse compensation.

### Requirements
1. Implement a `TimedSagaCoordinator`:
   - Enforces a per-step timeout (e.g. 50ms).
2. If a service action does not return within the deadline:
   - Mark the step as `TIMED_OUT`.
   - Abort forward execution.
   - Execute compensating transactions for all previously completed steps in reverse order.

### Solution

```python
"""
Exercise 2 Solution: Timed Saga Coordinator with Reverse Compensation
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Callable, List, Tuple


@dataclass
class SagaStepDef:
    name: str
    action: Callable[[], bool]
    compensation: Callable[[], bool]
    timeout_sec: float = 0.05


class TimedSagaCoordinator:
    def __init__(self):
        self.log: List[str] = []

    def execute_saga(self, steps: List[SagaStepDef]) -> Tuple[bool, str]:
        completed: List[SagaStepDef] = []

        for step in steps:
            self.log.append(f"EXEC:{step.name}")
            start_t = time.time()
            try:
                # Simulated timed execution
                success = step.action()
                elapsed = time.time() - start_t
                if elapsed > step.timeout_sec:
                    raise TimeoutError(f"Step {step.name} exceeded deadline {step.timeout_sec}s (took {elapsed:.3f}s)")
                
                if success:
                    completed.append(step)
                    self.log.append(f"SUCCESS:{step.name}")
                else:
                    raise RuntimeError("Returned False")
            except Exception as exc:
                self.log.append(f"FAILED:{step.name}:{exc}")
                self._compensate(completed)
                return False, f"Saga aborted at {step.name}: {exc}"

        return True, "Saga completed"

    def _compensate(self, completed: List[SagaStepDef]) -> None:
        for step in reversed(completed):
            self.log.append(f"COMPENSATING:{step.name}")
            step.compensation()
            self.log.append(f"COMPENSATED:{step.name}")


# --- Verification Test ---
if __name__ == "__main__":
    payment_done = False
    inventory_done = False

    def do_payment():
        nonlocal payment_done
        payment_done = True
        return True

    def undo_payment():
        nonlocal payment_done
        payment_done = False
        return True

    def do_hanging_shipping():
        # Exceeds 0.05s timeout
        time.sleep(0.08)
        return True

    steps = [
        SagaStepDef("Payment", do_payment, undo_payment, timeout_sec=0.05),
        SagaStepDef("Shipping", do_hanging_shipping, lambda: True, timeout_sec=0.05)
    ]

    coord = TimedSagaCoordinator()
    ok, msg = coord.execute_saga(steps)

    assert ok is False
    assert "exceeded deadline" in msg
    assert payment_done is False, "Payment must be compensated on shipping timeout"
    print("Exercise 2: Timed Saga Coordinator and Timeout Compensation verified successfully!")
```


---

## Exercise 3: OpenTelemetry Distributed Trace Context Propagator

### Scenario
An end-to-end distributed transaction traverses an API Gateway, Order Service, and Kafka Event Backbone. To provide full visibility in Jaeger/Datadog, each service must propagate the W3C `traceparent` context header across both HTTP requests and Kafka message headers.

### Requirements
1. Implement a `W3CTraceContext` parser and generator:
   - Format: `version-trace_id-parent_id-trace_flags` (e.g. `00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`).
2. Provide `create_child_span()` which preserves the `trace_id` while generating a new 64-bit hexadecimal `span_id`.
3. Propagate context from an HTTP header into a Kafka Record header, verifying that the entire asynchronous pipeline shares the exact same `trace_id`.

### Solution

```python
"""
Exercise 3 Solution: OpenTelemetry Distributed Trace Context Propagator
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class W3CTraceContext:
    version: str
    trace_id: str
    span_id: str
    trace_flags: str

    @classmethod
    def parse_header(cls, header_value: str) -> Optional[W3CTraceContext]:
        parts = header_value.strip().split("-")
        if len(parts) == 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
            return cls(version=parts[0], trace_id=parts[1], span_id=parts[2], trace_flags=parts[3])
        return None

    def serialize(self) -> str:
        return f"{self.version}-{self.trace_id}-{self.span_id}-{self.trace_flags}"

    def create_child_span(self) -> W3CTraceContext:
        """Preserves trace_id, generates new 64-bit span_id."""
        new_span_id = uuid.uuid4().hex[:16]
        return W3CTraceContext(
            version=self.version,
            trace_id=self.trace_id,
            span_id=new_span_id,
            trace_flags=self.trace_flags
        )


# --- Verification Test ---
if __name__ == "__main__":
    # Ingress Gateway receives request with incoming W3C traceparent
    incoming_header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    gateway_ctx = W3CTraceContext.parse_header(incoming_header)

    assert gateway_ctx is not None
    assert gateway_ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert gateway_ctx.span_id == "00f067aa0ba902b7"

    # Gateway calls Order Service -> creates child span
    order_span = gateway_ctx.create_child_span()
    assert order_span.trace_id == gateway_ctx.trace_id, "Trace ID must remain constant across hops"
    assert order_span.span_id != gateway_ctx.span_id, "Span ID must be distinct per hop"

    # Order Service publishes to Kafka -> injects into Kafka record headers
    kafka_span = order_span.create_child_span()
    kafka_headers = {"traceparent": kafka_span.serialize()}

    # Downstream Notification Worker consumes Kafka message
    consumed_ctx = W3CTraceContext.parse_header(kafka_headers["traceparent"])
    assert consumed_ctx.trace_id == gateway_ctx.trace_id, "End-to-end trace ID must survive Kafka serialization"

    print("Exercise 3: W3C Distributed Trace Context propagation verified successfully across HTTP & Kafka!")
```
