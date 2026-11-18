# Day 70: Enterprise Practical Exercises: Message Queues & Event-Driven Architecture

This workbook contains **3 enterprise-grade distributed systems exercises**. Each exercise presents a real-world scenario, precise architectural requirements, and a complete, production-tested reference solution.

---

## Exercise 1: Dead-Letter Queue (DLQ) with Exponential Backoff Retry Worker

### Scenario
In a payments processing microservice, occasional transient network glitches (e.g., downstream bank HTTP 503) cause transaction processing to fail. However, malformed JSON or unrecoverable customer validation errors must **not** be retried indefinitely.

### Requirements
1. Build a resilient message consumer that intercepts failures.
2. If failure is transient (e.g., `TransientNetworkError`), retry up to `MAX_RETRIES = 3` with simulated exponential backoff (`delay = base * (2 ** retry_count)`).
3. If retries exceed 3, or if the failure is a poison-pill (`PoisonPillError`), immediately route the message to the **Dead-Letter Queue (DLQ)** with diagnostic metadata (`x-death-reason`, `x-attempts`, `timestamp`).
4. Prevent queue starvation or head-of-line blocking.

### Solution

```python
"""
Exercise 1 Solution: DLQ with Exponential Backoff Retry Worker
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class TransientNetworkError(Exception):
    """Simulates a temporary upstream timeout or 503."""
    pass


class PoisonPillError(Exception):
    """Simulates invalid payload or unrecoverable schema mismatch."""
    pass


@dataclass
class DeliveryMessage:
    msg_id: str
    body: Dict[str, Any]
    headers: Dict[str, Any] = field(default_factory=dict)
    delivery_tag: int = 1


class ResilientDLQWorker:
    def __init__(self, max_retries: int = 3, base_backoff_ms: float = 10.0):
        self.max_retries = max_retries
        self.base_backoff_ms = base_backoff_ms
        self.primary_queue: List[DeliveryMessage] = []
        self.retry_queue: List[DeliveryMessage] = []
        self.dlq: List[DeliveryMessage] = []
        self.processed_success: List[DeliveryMessage] = []

    def simulate_downstream_service(self, msg: DeliveryMessage):
        """Simulates external API processing with failure injection."""
        payload = msg.body
        if payload.get("corrupted"):
            raise PoisonPillError("Schema invalid: missing mandatory field 'account_id'")
        if payload.get("simulate_network_fail") and msg.headers.get("x-retries", 0) < 2:
            raise TransientNetworkError("Bank gateway 503 Service Unavailable")
        # Success
        return True

    def process_message(self, msg: DeliveryMessage) -> None:
        retries = msg.headers.get("x-retries", 0)
        try:
            self.simulate_downstream_service(msg)
            # Acknowledge success
            self.processed_success.append(msg)
            print(f"[SUCCESS] Message {msg.msg_id} processed cleanly on attempt {retries + 1}.")

        except PoisonPillError as ppe:
            print(f"[POISON PILL] Message {msg.msg_id} rejected immediately: {ppe}")
            self._route_to_dlq(msg, reason=str(ppe), unrecoverable=True)

        except TransientNetworkError as tne:
            if retries < self.max_retries:
                retries += 1
                msg.headers["x-retries"] = retries
                backoff_delay = (self.base_backoff_ms * (2 ** (retries - 1))) / 1000.0
                print(f"[TRANSIENT ERROR] Message {msg.msg_id} failed: {tne}. Scheduling retry #{retries} in {backoff_delay:.3f}s...")
                time.sleep(backoff_delay)
                self.retry_queue.append(msg)
            else:
                print(f"[MAX RETRIES EXCEEDED] Message {msg.msg_id} failed after {retries} retries.")
                self._route_to_dlq(msg, reason=f"Max retries ({self.max_retries}) exceeded: {tne}", unrecoverable=False)

    def _route_to_dlq(self, msg: DeliveryMessage, reason: str, unrecoverable: bool) -> None:
        msg.headers["x-death-reason"] = reason
        msg.headers["x-death-timestamp"] = time.time()
        msg.headers["x-unrecoverable"] = unrecoverable
        self.dlq.append(msg)


# --- Verification Test ---
if __name__ == "__main__":
    worker = ResilientDLQWorker(max_retries=3, base_backoff_ms=5.0)

    # 1. Normal message
    m1 = DeliveryMessage("msg-101", {"account_id": "acc_1", "amount": 100})
    # 2. Transient failure message (fails twice, succeeds on 3rd attempt)
    m2 = DeliveryMessage("msg-102", {"account_id": "acc_2", "amount": 250, "simulate_network_fail": True})
    # 3. Permanent Poison-Pill message
    m3 = DeliveryMessage("msg-103", {"corrupted": True})

    for m in [m1, m2, m3]:
        worker.process_message(m)

    # Drain retry queue
    while worker.retry_queue:
        next_msg = worker.retry_queue.pop(0)
        worker.process_message(next_msg)

    assert len(worker.processed_success) == 2, f"Expected 2 successful messages, got {len(worker.processed_success)}"
    assert len(worker.dlq) == 1, f"Expected 1 dead-lettered message, got {len(worker.dlq)}"
    assert worker.dlq[0].msg_id == "msg-103"
    assert "Schema invalid" in worker.dlq[0].headers["x-death-reason"]
    print("\nExercise 1: All assertions verified successfully!")
```

---

## Exercise 2: Transactional Outbox Pattern with CDC Poller & Idempotent Consumer

### Scenario
An e-commerce order service must charge customer credits and publish an `OrderPlaced` event. Dual-writes directly to SQL and Kafka lead to split-brain scenarios during network cuts.

### Requirements
1. Implement a local ACID transaction simulator that saves an `Order` and an `OutboxEvent` within the exact same database atomic block.
2. Build an asynchronous CDC Poller / Publisher daemon that:
   - Polls unpublished outbox events (`status = 'PENDING'`).
   - Relays them to the message broker.
   - Marks the outbox records as `'PUBLISHED'` with an updated timestamp.
3. Build an **Idempotent Consumer** that tracks processed message IDs using an in-memory or Redis-style set, guaranteeing exactly-once side-effects even when the CDC poller retries delivery.

### Solution

```python
"""
Exercise 2 Solution: Transactional Outbox Pattern & Idempotent Consumer
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set


@dataclass
class Order:
    order_id: str
    customer_id: str
    amount: float
    status: str = "CONFIRMED"


@dataclass
class OutboxRecord:
    event_id: str
    aggregate_type: str
    aggregate_id: str
    payload: Dict[str, Any]
    status: str = "PENDING"
    created_at: float = field(default_factory=time.time)
    published_at: float = 0.0


class MockDatabase:
    """Simulates an ACID-compliant Relational Database."""
    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.outbox_events: Dict[str, OutboxRecord] = {}

    def transactional_order_placement(self, order: Order, payload: Dict[str, Any]) -> OutboxRecord:
        """Atomic Unit-of-Work: inserts order and outbox record together."""
        # Step 1: Save business entity
        self.orders[order.order_id] = order
        # Step 2: Save outbox event in same transaction
        event = OutboxRecord(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            aggregate_type="Order",
            aggregate_id=order.order_id,
            payload=payload,
            status="PENDING"
        )
        self.outbox_events[event.event_id] = event
        return event


class SimulatedBroker:
    def __init__(self):
        self.topics: Dict[str, List[Dict[str, Any]]] = {}

    def publish(self, topic: str, message: Dict[str, Any]) -> None:
        if topic not in self.topics:
            self.topics[topic] = []
        self.topics[topic].append(message)


class CDCPollerRelay:
    """Change Data Capture (CDC) worker relaying outbox events to the broker."""
    def __init__(self, db: MockDatabase, broker: SimulatedBroker):
        self.db = db
        self.broker = broker

    def poll_and_publish(self, batch_size: int = 10) -> int:
        pending = [
            evt for evt in self.db.outbox_events.values()
            if evt.status == "PENDING"
        ][:batch_size]

        count = 0
        for evt in pending:
            # Publish to message broker
            self.broker.publish("orders.events", {
                "event_id": evt.event_id,
                "type": evt.aggregate_type,
                "id": evt.aggregate_id,
                "payload": evt.payload
            })
            # Mark outbox record as PUBLISHED
            evt.status = "PUBLISHED"
            evt.published_at = time.time()
            count += 1
        return count


class IdempotentConsumer:
    """Consumer enforcing exactly-once execution semantics via deduplication cache."""
    def __init__(self):
        self.processed_event_ids: Set[str] = set()
        self.executed_side_effects: List[str] = []

    def consume(self, message: Dict[str, Any]) -> bool:
        evt_id = message["event_id"]
        # Idempotency barrier
        if evt_id in self.processed_event_ids:
            print(f"[IDEMPOTENCY DUPLICATE] Event {evt_id} already executed. Ignoring.")
            return False

        # Execute business side effect (e.g., send confirmation email)
        order_id = message["id"]
        self.executed_side_effects.append(order_id)
        self.processed_event_ids.add(evt_id)
        print(f"[CONSUMED] Processed order confirmation for {order_id} (Event: {evt_id}).")
        return True


# --- Verification Test ---
if __name__ == "__main__":
    db = MockDatabase()
    broker = SimulatedBroker()
    cdc = CDCPollerRelay(db, broker)
    consumer = IdempotentConsumer()

    # 1. Place 3 orders in Database (atomic with outbox)
    for i in range(1, 4):
        ord_obj = Order(order_id=f"ord_{i}", customer_id=f"cust_{i*10}", amount=100.0 * i)
        db.transactional_order_placement(ord_obj, {"order_id": ord_obj.order_id, "amount": ord_obj.amount})

    assert len(db.orders) == 3
    assert len(db.outbox_events) == 3
    assert all(e.status == "PENDING" for e in db.outbox_events.values())

    # 2. CDC poller runs and relays to broker
    published_count = cdc.poll_and_publish()
    assert published_count == 3
    assert all(e.status == "PUBLISHED" for e in db.outbox_events.values())
    assert len(broker.topics["orders.events"]) == 3

    # 3. Consumer processes messages from broker
    for msg in broker.topics["orders.events"]:
        consumer.consume(msg)
    assert len(consumer.executed_side_effects) == 3

    # 4. Simulate Redelivery (Duplicate delivery from network retry)
    duplicate_msg = broker.topics["orders.events"][0]
    result = consumer.consume(duplicate_msg)
    assert result is False, "Duplicate message must be rejected by idempotency check"
    assert len(consumer.executed_side_effects) == 3, "Side effects must remain exactly 3"

    print("\nExercise 2: Transactional Outbox and Idempotency verified successfully!")
```

---

## Exercise 3: Apache Kafka Partition Rebalancer and Consumer Group Coordinator

### Scenario
A high-throughput event processing cluster needs to distribute a 6-partition topic across a dynamic pool of consumers. When workers crash or scale horizontally, partitions must be redistributed fairly without dropping records or scrambling committed offsets.

### Requirements
1. Implement a `TopicPartitionCoordinator` that manages 6 partitions and tracks consumer group memberships.
2. Implement dynamic partition reassignment using the **Range** strategy:
   - Calculate `partitions_per_consumer = total_partitions // num_consumers`.
   - Distribute remainder evenly across the first few consumers.
3. Simulate node addition (scale up) and node crash (scale down), verifying that:
   - Every partition is assigned to exactly **one** consumer.
   - Zero partitions are left unassigned.
   - Consumers preserve their committed offsets across rebalances.

### Solution

```python
"""
Exercise 3 Solution: Kafka Partition Rebalancer (Range Assignor)
"""
from __future__ import annotations
from typing import Dict, List, Set


class KafkaRangeAssignor:
    """
    Kafka Range Assignor:
    Divides topic partitions into contiguous numeric ranges per consumer.
    """
    @staticmethod
    def assign(num_partitions: int, consumer_ids: List[str]) -> Dict[str, List[int]]:
        consumers = sorted(consumer_ids)
        if not consumers:
            return {}

        assignments: Dict[str, List[int]] = {c: [] for c in consumers}
        n_c = len(consumers)
        base = num_partitions // n_c
        rem = num_partitions % n_c

        start = 0
        for i, c in enumerate(consumers):
            # First 'rem' consumers receive (base + 1) partitions
            length = base + (1 if i < rem else 0)
            assignments[c] = list(range(start, start + length))
            start += length

        return assignments


class PartitionClusterCoordinator:
    def __init__(self, topic_name: str, num_partitions: int = 6):
        self.topic_name = topic_name
        self.num_partitions = num_partitions
        self.consumers: Set[str] = set()
        self.assignments: Dict[str, List[int]] = {}
        self.committed_offsets: Dict[int, int] = {p: 0 for p in range(num_partitions)}

    def join_group(self, consumer_id: str) -> None:
        self.consumers.add(consumer_id)
        self.rebalance()

    def leave_group(self, consumer_id: str) -> None:
        self.consumers.discard(consumer_id)
        self.rebalance()

    def rebalance(self) -> None:
        self.assignments = KafkaRangeAssignor.assign(self.num_partitions, list(self.consumers))

    def commit(self, partition_id: int, offset: int) -> None:
        self.committed_offsets[partition_id] = offset + 1

    def verify_integrity(self) -> bool:
        """Validates that all partitions are assigned to exactly one consumer."""
        assigned_partitions: List[int] = []
        for p_list in self.assignments.values():
            assigned_partitions.extend(p_list)
        
        all_unique = len(assigned_partitions) == len(set(assigned_partitions))
        covers_all = set(assigned_partitions) == set(range(self.num_partitions))
        return all_unique and covers_all


# --- Verification Test ---
if __name__ == "__main__":
    cluster = PartitionClusterCoordinator("metrics.stream", num_partitions=6)

    # Stage 1: Single Consumer -> receives all 6 partitions [0, 1, 2, 3, 4, 5]
    cluster.join_group("node-A")
    assert cluster.verify_integrity()
    assert cluster.assignments["node-A"] == [0, 1, 2, 3, 4, 5]
    print(f"1 Consumer: node-A has {cluster.assignments['node-A']}")

    # Commit some offsets
    cluster.commit(partition_id=0, offset=45)
    cluster.commit(partition_id=5, offset=120)

    # Stage 2: Scale up to 2 Consumers -> 3 partitions each
    cluster.join_group("node-B")
    assert cluster.verify_integrity()
    assert cluster.assignments["node-A"] == [0, 1, 2]
    assert cluster.assignments["node-B"] == [3, 4, 5]
    print(f"2 Consumers: node-A -> {cluster.assignments['node-A']}, node-B -> {cluster.assignments['node-B']}")

    # Stage 3: Scale up to 4 Consumers (6 partitions // 4 consumers = 1 rem 2 -> [2, 2, 1, 1])
    cluster.join_group("node-C")
    cluster.join_group("node-D")
    assert cluster.verify_integrity()
    print("4 Consumers Assignment:")
    for c, p in sorted(cluster.assignments.items()):
        print(f"  {c} -> {p}")
    assert cluster.assignments["node-A"] == [0, 1]
    assert cluster.assignments["node-B"] == [2, 3]
    assert cluster.assignments["node-C"] == [4]
    assert cluster.assignments["node-D"] == [5]

    # Stage 4: Node failure (node-A crashes)
    print("Simulating node-A crash...")
    cluster.leave_group("node-A")
    assert cluster.verify_integrity()
    assert "node-A" not in cluster.assignments
    # 6 partitions among 3 consumers (B, C, D) -> 2 partitions each
    assert all(len(p) == 2 for p in cluster.assignments.values())
    print("Post-crash 3 Consumers Assignment:")
    for c, p in sorted(cluster.assignments.items()):
        print(f"  {c} -> {p}")

    # Ensure committed offsets survived rebalance intact
    assert cluster.committed_offsets[0] == 46
    assert cluster.committed_offsets[5] == 121
    print("\nExercise 3: Kafka Range Rebalancer and Offset Integrity verified successfully!")
```
