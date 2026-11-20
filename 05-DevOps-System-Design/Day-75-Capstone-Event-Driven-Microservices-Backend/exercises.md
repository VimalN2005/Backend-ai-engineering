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
