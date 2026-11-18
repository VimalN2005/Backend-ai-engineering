"""
Day 70: Distributed Commit-Log & Event-Driven Architecture Simulator (Kafka Model)
==================================================================================
Architecture & Principles Demonstrated:
1. Distributed Partitioned Commit-Log Broker:
   - Topics sharded into N immutable append-only partitions.
   - Deterministic Key-based Partitioning (`murmur3_hash(key) % num_partitions`)
     guaranteeing per-key strict chronological ordering.
   - Zero-message-deletion on read (retention-based log persistence).
2. Consumer Group Coordination & Offset Management:
   - Consumer Groups with automatic partition assignment (Round-Robin & Range).
   - Offset committed tracking (`__consumer_offsets`).
   - Time-Travel Replay: seeking offsets backwards to reprocess historical events.
3. Head-to-Head Benchmark:
   - RabbitMQ individual Ack-tracking vs Kafka append-only commit-log batching.
"""

from __future__ import annotations
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class Record:
    """Immutable record stored in the append-only commit log."""
    offset: int
    key: Optional[str]
    value: Any
    timestamp: float
    headers: Dict[str, str] = field(default_factory=dict)


class Partition:
    """
    Append-only sequential disk log abstraction.
    - Offsets are monotonically increasing 64-bit integers.
    - Reads are non-destructive (multiple consumer groups read same physical offsets).
    """
    def __init__(self, partition_id: int):
        self.partition_id = partition_id
        self._log: List[Record] = []
        self._next_offset: int = 0

    def append(self, key: Optional[str], value: Any, headers: Optional[Dict[str, str]] = None) -> int:
        offset = self._next_offset
        record = Record(
            offset=offset,
            key=key,
            value=value,
            timestamp=time.time(),
            headers=headers or {}
        )
        self._log.append(record)
        self._next_offset += 1
        return offset

    def read_from(self, start_offset: int, max_records: int = 100) -> List[Record]:
        """Fetch records beginning at start_offset (non-destructive)."""
        if start_offset >= len(self._log):
            return []
        end_offset = min(start_offset + max_records, len(self._log))
        return self._log[start_offset:end_offset]

    @property
    def high_watermark(self) -> int:
        """Next offset to be written (latest available offset is high_watermark - 1)."""
        return self._next_offset

    def __len__(self) -> int:
        return len(self._log)


class KafkaTopic:
    """Topic comprising multiple partitioned commit logs."""
    def __init__(self, name: str, num_partitions: int = 3):
        self.name = name
        self.num_partitions = num_partitions
        self.partitions: List[Partition] = [Partition(i) for i in range(num_partitions)]

    def select_partition(self, key: Optional[str], round_robin_counter: int = 0) -> int:
        """
        Deterministic partitioning:
        - If key is provided: murmur3/md5 hash modulus num_partitions.
        - If key is None: round-robin across partitions.
        """
        if key is not None:
            # Consistent hashing on key string
            hash_val = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
            return hash_val % self.num_partitions
        return round_robin_counter % self.num_partitions

    def produce(self, key: Optional[str], value: Any, headers: Optional[Dict[str, str]] = None, rr_counter: int = 0) -> Tuple[int, int]:
        """Produces record to assigned partition. Returns (partition_id, offset)."""
        p_id = self.select_partition(key, rr_counter)
        offset = self.partitions[p_id].append(key, value, headers)
        return p_id, offset


class ConsumerGroupCoordinator:
    """
    Manages partition assignment across consumers in a group and persists offsets.
    Implements Round-Robin partition rebalance.
    """
    def __init__(self, group_id: str, topic: KafkaTopic):
        self.group_id = group_id
        self.topic = topic
        self.consumers: List[str] = []
        self.assignments: Dict[str, List[int]] = {}  # consumer_id -> list of partition_ids
        # Committed offsets: partition_id -> next offset to consume
        self.committed_offsets: Dict[int, int] = {p.partition_id: 0 for p in topic.partitions}

    def register_consumer(self, consumer_id: str) -> None:
        if consumer_id not in self.consumers:
            self.consumers.append(consumer_id)
            self._rebalance()

    def unregister_consumer(self, consumer_id: str) -> None:
        if consumer_id in self.consumers:
            self.consumers.remove(consumer_id)
            self._rebalance()

    def _rebalance(self) -> None:
        """
        Round-robin partition distribution:
        Partitions [0, 1, 2] among Consumers [C1, C2] -> C1: [0, 2], C2: [1]
        If num_consumers > num_partitions, idle consumers get empty assignments.
        """
        self.assignments = {c: [] for c in self.consumers}
        if not self.consumers:
            return

        for p_id in range(self.topic.num_partitions):
            assigned_consumer = self.consumers[p_id % len(self.consumers)]
            self.assignments[assigned_consumer].append(p_id)

    def commit_offset(self, partition_id: int, offset: int) -> None:
        """Commit consumer offset (offset + 1 is the next record)."""
        self.committed_offsets[partition_id] = offset + 1

    def get_offset(self, partition_id: int) -> int:
        return self.committed_offsets.get(partition_id, 0)

    def seek(self, partition_id: int, target_offset: int) -> None:
        """Time-travel rewind or fast-forward."""
        max_valid = self.topic.partitions[partition_id].high_watermark
        target_offset = max(0, min(target_offset, max_valid))
        self.committed_offsets[partition_id] = target_offset


class GroupConsumer:
    """
    Kafka consumer client adhering to pull-based batch polling.
    """
    def __init__(self, consumer_id: str, coordinator: ConsumerGroupCoordinator):
        self.consumer_id = consumer_id
        self.coordinator = coordinator
        self.coordinator.register_consumer(consumer_id)
        self.records_processed: List[Record] = []

    @property
    def assigned_partitions(self) -> List[int]:
        return self.coordinator.assignments.get(self.consumer_id, [])

    def poll(self, max_records_per_partition: int = 50) -> List[Record]:
        """Pulls records from all assigned partitions starting at committed offsets."""
        batch: List[Record] = []
        for p_id in self.assigned_partitions:
            part = self.coordinator.topic.partitions[p_id]
            curr_offset = self.coordinator.get_offset(p_id)
            records = part.read_from(curr_offset, max_records_per_partition)
            if records:
                batch.extend(records)
                # Auto-commit offset to latest in batch
                last_offset = records[-1].offset
                self.coordinator.commit_offset(p_id, last_offset)
                self.records_processed.extend(records)
        return batch


# =====================================================================
# Head-to-Head Performance Benchmark: RabbitMQ vs. Kafka Simulation
# =====================================================================

def benchmark_rabbitmq_vs_kafka(num_messages: int = 20_000):
    """
    Comparative throughput and behavior simulation:
    - RabbitMQ Model: Queue insert, per-message unack tracking, per-message dequeue/ack deletion.
    - Kafka Model: Partition append-only array push, zero-deletion poll, offset pointer increment.
    """
    print(f"\n--- RUNNING HEAD-TO-HEAD BENCHMARK ({num_messages:,} MESSAGES) ---")

    # 1. RabbitMQ Simulation (Smart Broker)
    # Fast queue with dictionary of inflight messages and per-message deletion
    start_time = time.perf_counter()
    rmq_queue: List[Dict[str, Any]] = []
    rmq_unacked: Dict[int, Dict[str, Any]] = {}

    # Produce
    for i in range(num_messages):
        rmq_queue.append({"id": i, "payload": "sample_data", "tag": i})
    rmq_produce_time = time.perf_counter() - start_time

    # Consume with Individual Acknowledgments
    start_time = time.perf_counter()
    consumed_count = 0
    while rmq_queue:
        # Dequeue to in-flight
        item = rmq_queue.pop(0)
        tag = item["tag"]
        rmq_unacked[tag] = item
        # Consumer acknowledges
        del rmq_unacked[tag]
        consumed_count += 1
    rmq_consume_time = time.perf_counter() - start_time
    rmq_total_time = rmq_produce_time + rmq_consume_time
    rmq_throughput = num_messages / rmq_total_time

    # 2. Kafka Simulation (Commit Log)
    start_time = time.perf_counter()
    kafka_topic = KafkaTopic("benchmark.events", num_partitions=4)

    # Produce (append to partitions via key hash)
    for i in range(num_messages):
        kafka_topic.produce(key=f"user_{i % 100}", value={"id": i, "payload": "sample_data"})
    kafka_produce_time = time.perf_counter() - start_time

    # Consume (batch read + offset commit, NO DELETIONS)
    start_time = time.perf_counter()
    coord = ConsumerGroupCoordinator("bench-group", kafka_topic)
    c1 = GroupConsumer("bench-c1", coord)
    c2 = GroupConsumer("bench-c2", coord)

    kafka_consumed = 0
    while True:
        b1 = c1.poll(max_records_per_partition=500)
        b2 = c2.poll(max_records_per_partition=500)
        kafka_consumed += len(b1) + len(b2)
        if not b1 and not b2:
            break
    kafka_consume_time = time.perf_counter() - start_time
    kafka_total_time = kafka_produce_time + kafka_consume_time
    kafka_throughput = num_messages / kafka_total_time

    print(f"RabbitMQ Simulation (Smart Broker):")
    print(f"  Produce Time: {rmq_produce_time:.4f}s | Consume + Individual Ack Time: {rmq_consume_time:.4f}s")
    print(f"  Total: {rmq_total_time:.4f}s | Throughput: {rmq_throughput:,.0f} msg/sec")
    print(f"  Remaining Queue Size: {len(rmq_queue)} (messages purged upon ACK)")

    print(f"\nKafka Simulation (Commit Log):")
    print(f"  Produce Time: {kafka_produce_time:.4f}s | Batch Poll + Offset Commit Time: {kafka_consume_time:.4f}s")
    print(f"  Total: {kafka_total_time:.4f}s | Throughput: {kafka_throughput:,.0f} msg/sec")
    total_stored = sum(len(p) for p in kafka_topic.partitions)
    print(f"  Total Retained on Log: {total_stored:,} (non-destructive, enables time-travel replay)")

    return rmq_throughput, kafka_throughput


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_partitioning_and_ordering():
    print("\n--- 1. DETERMINISTIC PARTITIONING & KEY-ORDERING DEMO ---")
    topic = KafkaTopic("orders.v1", num_partitions=3)

    # All events for customer_42 must land in the EXACT SAME partition
    customer_id = "customer_42"
    offsets_customer = []
    partitions_customer = []

    for event_type in ["order_created", "payment_processed", "invoice_generated", "order_shipped"]:
        p_id, offset = topic.produce(key=customer_id, value={"type": event_type, "customer": customer_id})
        partitions_customer.append(p_id)
        offsets_customer.append(offset)

    # Assert that all 4 events landed in the same partition
    assert len(set(partitions_customer)) == 1, "Per-key events must land in the same partition!"
    assigned_p = partitions_customer[0]
    print(f"Key '{customer_id}' consistently routed to Partition {assigned_p} at offsets {offsets_customer}")

    # Verify chronological ordering inside partition
    p_log = topic.partitions[assigned_p].read_from(0, 10)
    event_sequence = [rec.value["type"] for rec in p_log if rec.key == customer_id]
    assert event_sequence == ["order_created", "payment_processed", "invoice_generated", "order_shipped"]
    print(f"Verified strict sequential order: {' -> '.join(event_sequence)}")


def demo_consumer_group_rebalancing():
    print("\n--- 2. CONSUMER GROUP DYNAMIC REBALANCING DEMO ---")
    topic = KafkaTopic("telemetry", num_partitions=4)
    coord = ConsumerGroupCoordinator("telemetry_group", topic)

    # 1. Start with 1 Consumer: should own all 4 partitions [0, 1, 2, 3]
    c1 = GroupConsumer("consumer-alpha", coord)
    assert sorted(c1.assigned_partitions) == [0, 1, 2, 3]
    print(f"Single consumer: {c1.consumer_id} owns partitions {c1.assigned_partitions}")

    # 2. Add second Consumer: partitions split 2 and 2
    c2 = GroupConsumer("consumer-beta", coord)
    assert len(c1.assigned_partitions) == 2
    assert len(c2.assigned_partitions) == 2
    assert set(c1.assigned_partitions).union(set(c2.assigned_partitions)) == {0, 1, 2, 3}
    print(f"After scaling to 2 consumers: {c1.consumer_id} -> {c1.assigned_partitions}, {c2.consumer_id} -> {c2.assigned_partitions}")

    # 3. Simulate failure: unregister c1 -> c2 takes over all 4 partitions
    coord.unregister_consumer("consumer-alpha")
    assert sorted(c2.assigned_partitions) == [0, 1, 2, 3]
    print(f"After consumer-alpha failure: {c2.consumer_id} rebalanced to own all partitions {c2.assigned_partitions}")


def demo_time_travel_replay():
    print("\n--- 3. TIME-TRAVEL OFFSET REPLAY DEMO ---")
    topic = KafkaTopic("financial.ledger", num_partitions=1)
    coord = ConsumerGroupCoordinator("audit_team", topic)
    auditor = GroupConsumer("auditor-1", coord)

    # Produce 5 financial transactions
    for amount in [100, 250, 50, 400, 150]:
        topic.produce(key="TX", value={"amount": amount})

    # First consumption run
    batch1 = auditor.poll(max_records_per_partition=10)
    assert len(batch1) == 5
    total_ledger = sum(rec.value["amount"] for rec in batch1)
    assert total_ledger == 950
    print(f"Initial run processed {len(batch1)} records. Total: ${total_ledger}")

    # Poll again: returns empty because consumer is at high watermark
    batch2 = auditor.poll()
    assert len(batch2) == 0
    print("Subsequent poll returned 0 records (consumer at end of log).")

    # Time-travel: seek offset back to 0 (replay from beginning)
    print("Performing Time-Travel Replay: seeking partition 0 back to offset 0...")
    coord.seek(partition_id=0, target_offset=0)
    assert coord.get_offset(0) == 0

    # Re-read historical log
    replay_batch = auditor.poll(max_records_per_partition=10)
    assert len(replay_batch) == 5
    replay_total = sum(rec.value["amount"] for rec in replay_batch)
    assert replay_total == 950
    print(f"Replay successfully processed {len(replay_batch)} records. Re-computed total: ${replay_total}")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING DISTRIBUTED COMMIT-LOG SIMULATOR (KAFKA ARCHITECTURE)")
    print("=" * 70)
    demo_partitioning_and_ordering()
    demo_consumer_group_rebalancing()
    demo_time_travel_replay()
    benchmark_rabbitmq_vs_kafka(num_messages=15_000)
    print("\n" + "=" * 70)
    print("ALL KAFKA DISTRIBUTED COMMIT-LOG TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
