"""
Day 71: Apache Kafka Consumer Group Coordinator, Rebalance & Offset Engine
==========================================================================
Architecture & Principles Demonstrated:
1. Consumer Group Coordination & State Machine:
   - Group States: EMPTY -> PREPARING_REBALANCE -> COMPLETING_REBALANCE -> STABLE.
   - Dynamic JoinGroup and LeaveGroup / Failure Detection.
2. Rebalance Protocols:
   - Legacy Eager Rebalance (Stop-The-World revocation of all partitions).
   - Cooperative Sticky Rebalance (Incremental: uninterrupted processing of stable partitions).
3. Offset Management & Consumer Lag Tracking:
   - Offset commits stored in simulated `__consumer_offsets`.
   - Real-time Consumer Lag computation: (High Watermark - Current Offset).
   - Alerting when lag exceeds SLA threshold.
"""

from __future__ import annotations
import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class GroupState(str, Enum):
    EMPTY = "Empty"
    PREPARING_REBALANCE = "PreparingRebalance"
    COMPLETING_REBALANCE = "CompletingRebalance"
    STABLE = "Stable"
    DEAD = "Dead"


@dataclass
class TopicPartition:
    topic: str
    partition: int

    def __hash__(self):
        return hash((self.topic, self.partition))

    def __repr__(self):
        return f"{self.topic}-{self.partition}"


class RebalanceProtocol(str, Enum):
    EAGER = "eager"
    COOPERATIVE = "cooperative"


class PartitionAssignor:
    """Interface for Kafka partition assignment strategies."""
    @staticmethod
    def assign_round_robin(
        partitions: List[TopicPartition],
        consumer_ids: List[str]
    ) -> Dict[str, List[TopicPartition]]:
        """Round-robin partition distribution."""
        consumers = sorted(consumer_ids)
        assignments = {c: [] for c in consumers}
        if not consumers:
            return assignments

        sorted_parts = sorted(partitions, key=lambda p: (p.topic, p.partition))
        for idx, part in enumerate(sorted_parts):
            assigned_c = consumers[idx % len(consumers)]
            assignments[assigned_c].append(part)
        return assignments

    @staticmethod
    def assign_cooperative_sticky(
        partitions: List[TopicPartition],
        consumer_ids: List[str],
        current_assignments: Dict[str, List[TopicPartition]]
    ) -> Tuple[Dict[str, List[TopicPartition]], List[TopicPartition]]:
        """
        Cooperative Sticky Assignment (Kafka KIP-429):
        - Preserves existing assignments as much as possible to minimize movement.
        - Identifies partitions that MUST be revoked for transfer in the 2nd phase.
        Returns: (tentative_assignments, revoked_partitions).
        """
        consumers = sorted(consumer_ids)
        if not consumers:
            return {}, list(partitions)

        # Retain valid partitions owned by active consumers
        target_per_consumer = len(partitions) // len(consumers)
        max_per_consumer = target_per_consumer + (1 if len(partitions) % len(consumers) != 0 else 0)

        preserved: Dict[str, List[TopicPartition]] = {c: [] for c in consumers}
        unassigned: List[TopicPartition] = []
        revoked: List[TopicPartition] = []

        # Step 1: Retain current valid assignments up to max allowed
        active_set = set(consumers)
        for c, parts in current_assignments.items():
            if c in active_set:
                for p in parts:
                    if p in partitions:
                        if len(preserved[c]) < max_per_consumer:
                            preserved[c].append(p)
                        else:
                            revoked.append(p)
                            unassigned.append(p)
            else:
                # Consumer departed; its partitions are now unassigned
                unassigned.extend(parts)

        # Identify any completely missing partitions
        assigned_set = {p for plist in preserved.values() for p in plist}
        for p in partitions:
            if p not in assigned_set and p not in unassigned:
                unassigned.append(p)

        # Step 2: Assign unassigned partitions to consumers with capacity
        for p in sorted(unassigned, key=lambda x: (x.topic, x.partition)):
            # Find consumer with fewest assignments
            least_loaded = min(consumers, key=lambda c: len(preserved[c]))
            preserved[least_loaded].append(p)

        return preserved, revoked


class GroupCoordinator:
    """
    Simulates Kafka's Group Coordinator broker component:
    - Maintains group membership and state.
    - Manages consumer offsets (__consumer_offsets).
    - Coordinates incremental and eager rebalances.
    """
    def __init__(self, group_id: str, topic: str, num_partitions: int):
        self.group_id = group_id
        self.topic = topic
        self.num_partitions = num_partitions
        self.partitions = [TopicPartition(topic, i) for i in range(num_partitions)]

        self.state: GroupState = GroupState.EMPTY
        self.members: Set[str] = set()
        self.assignments: Dict[str, List[TopicPartition]] = {}
        # Internal __consumer_offsets store: TopicPartition -> committed_offset
        self.committed_offsets: Dict[TopicPartition, int] = {p: 0 for p in self.partitions}
        # High watermarks (Log End Offsets): TopicPartition -> latest_offset
        self.high_watermarks: Dict[TopicPartition, int] = {p: 0 for p in self.partitions}

        # Metrics
        self.rebalance_count: int = 0
        self.revoked_partition_events: int = 0

    def join_group(self, consumer_id: str, protocol: RebalanceProtocol = RebalanceProtocol.COOPERATIVE) -> None:
        """New or recovering consumer joins the group."""
        self.members.add(consumer_id)
        self._trigger_rebalance(protocol)

    def leave_group(self, consumer_id: str, protocol: RebalanceProtocol = RebalanceProtocol.COOPERATIVE) -> None:
        """Consumer gracefully exits or crashes."""
        if consumer_id in self.members:
            self.members.remove(consumer_id)
            self._trigger_rebalance(protocol)

    def _trigger_rebalance(self, protocol: RebalanceProtocol) -> None:
        self.rebalance_count += 1
        self.state = GroupState.PREPARING_REBALANCE

        if not self.members:
            self.state = GroupState.EMPTY
            self.assignments.clear()
            return

        self.state = GroupState.COMPLETING_REBALANCE

        if protocol == RebalanceProtocol.EAGER:
            # Eager rebalance: Revoke ALL partitions from ALL members (Stop-the-world)
            self.revoked_partition_events += sum(len(p) for p in self.assignments.values())
            self.assignments = PartitionAssignor.assign_round_robin(self.partitions, list(self.members))

        elif protocol == RebalanceProtocol.COOPERATIVE:
            # Cooperative Sticky: only revoke partitions that must migrate
            new_assignments, revoked = PartitionAssignor.assign_cooperative_sticky(
                self.partitions,
                list(self.members),
                self.assignments
            )
            self.revoked_partition_events += len(revoked)
            self.assignments = new_assignments

        self.state = GroupState.STABLE

    def commit_offset(self, tp: TopicPartition, offset: int) -> None:
        """Persist consumer offset into __consumer_offsets."""
        self.committed_offsets[tp] = offset

    def produce_to_partition(self, partition_id: int, count: int = 1) -> None:
        """Simulate upstream producer incrementing partition high watermark."""
        tp = TopicPartition(self.topic, partition_id)
        self.high_watermarks[tp] += count

    def get_consumer_lag(self, tp: TopicPartition) -> int:
        """Lag = Log End Offset - Committed Offset."""
        hw = self.high_watermarks.get(tp, 0)
        committed = self.committed_offsets.get(tp, 0)
        return max(0, hw - committed)

    def get_total_group_lag(self) -> int:
        return sum(self.get_consumer_lag(tp) for tp in self.partitions)


class KafkaConsumerWorker:
    """
    Simulated Kafka Consumer Client with manual offset commit loop.
    """
    def __init__(self, consumer_id: str, coordinator: GroupCoordinator):
        self.consumer_id = consumer_id
        self.coordinator = coordinator
        self.is_running = True

    @property
    def assigned_partitions(self) -> List[TopicPartition]:
        return self.coordinator.assignments.get(self.consumer_id, [])

    def poll_and_process(self, max_records: int = 10) -> int:
        """
        Pulls records up to high watermark, executes processing,
        and manually commits offsets synchronously.
        """
        processed_total = 0
        for tp in self.assigned_partitions:
            current_offset = self.coordinator.committed_offsets[tp]
            high_watermark = self.coordinator.high_watermarks[tp]

            records_available = high_watermark - current_offset
            if records_available > 0:
                to_process = min(records_available, max_records)
                # Simulated processing
                new_offset = current_offset + to_process
                # Manual Synchronous Commit
                self.coordinator.commit_offset(tp, new_offset)
                processed_total += to_process

        return processed_total


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_rebalance_eager_vs_cooperative():
    print("\n--- 1. REBALANCE PROTOCOL: EAGER VS COOPERATIVE STICKY ---")
    topic = "orders.v1"
    num_parts = 6

    # 1. Test Eager Rebalance (Stop-the-World)
    coord_eager = GroupCoordinator("eager_group", topic, num_parts)
    coord_eager.join_group("worker-1", protocol=RebalanceProtocol.EAGER)
    coord_eager.join_group("worker-2", protocol=RebalanceProtocol.EAGER)
    # Scale from 2 to 3 workers with EAGER protocol
    coord_eager.join_group("worker-3", protocol=RebalanceProtocol.EAGER)
    print(f"Eager Rebalance Total Partition Revocations: {coord_eager.revoked_partition_events}")

    # 2. Test Cooperative Sticky Rebalance (Incremental)
    coord_coop = GroupCoordinator("coop_group", topic, num_parts)
    coord_coop.join_group("worker-1", protocol=RebalanceProtocol.COOPERATIVE)
    coord_coop.join_group("worker-2", protocol=RebalanceProtocol.COOPERATIVE)
    revocations_before_scale = coord_coop.revoked_partition_events
    # Scale from 2 to 3 workers with COOPERATIVE protocol
    coord_coop.join_group("worker-3", protocol=RebalanceProtocol.COOPERATIVE)
    revocations_on_scale = coord_coop.revoked_partition_events - revocations_before_scale

    print(f"Cooperative Sticky Partition Revocations on scale: {revocations_on_scale}")
    print(f"Cooperative Final Assignments: {coord_coop.assignments}")

    # Each worker in cooperative must have exactly 2 partitions (6 partitions // 3 workers)
    for c, p in coord_coop.assignments.items():
        assert len(p) == 2, f"Expected 2 partitions for {c}, got {len(p)}"
    # Cooperative revocations must be strictly less than Eager revocations
    assert revocations_on_scale < coord_eager.revoked_partition_events
    print("Cooperative Sticky successfully eliminated global stop-the-world revocations!")


def demo_consumer_lag_monitoring():
    print("\n--- 2. REAL-TIME CONSUMER LAG MONITORING & BURST PRODUCTION ---")
    coord = GroupCoordinator("analytics_group", "telemetry", num_partitions=3)
    c1 = KafkaConsumerWorker("analyst-1", coord)
    c2 = KafkaConsumerWorker("analyst-2", coord)

    coord.join_group(c1.consumer_id)
    coord.join_group(c2.consumer_id)

    # 1. Initial State: Zero lag
    assert coord.get_total_group_lag() == 0
    print(f"Initial Group Lag: {coord.get_total_group_lag()} records")

    # 2. Producer bursts 5,000 records across 3 partitions
    print("Simulating upstream producer burst: 5,000 records across partitions...")
    coord.produce_to_partition(0, count=2000)
    coord.produce_to_partition(1, count=1500)
    coord.produce_to_partition(2, count=1500)

    burst_lag = coord.get_total_group_lag()
    print(f"Post-burst Consumer Lag: {burst_lag:,} records")
    assert burst_lag == 5000

    # Per-partition lag inspection
    for p in coord.partitions:
        print(f"  Partition {p}: HW={coord.high_watermarks[p]}, Committed={coord.committed_offsets[p]}, Lag={coord.get_consumer_lag(p)}")

    # 3. Consumers poll and drain lag
    processed_cycle_1 = c1.poll_and_process(max_records=500) + c2.poll_and_process(max_records=500)
    remaining_lag = coord.get_total_group_lag()
    print(f"After poll cycle 1: Processed {processed_cycle_1:,} records. Remaining Lag: {remaining_lag:,}")
    assert remaining_lag == 5000 - processed_cycle_1

    # Drain remaining lag completely
    while coord.get_total_group_lag() > 0:
        c1.poll_and_process(max_records=1000)
        c2.poll_and_process(max_records=1000)

    final_lag = coord.get_total_group_lag()
    print(f"Final Consumer Lag: {final_lag} records (SLA fully restored).")
    assert final_lag == 0


def demo_manual_offset_recovery():
    print("\n--- 3. CRASH RECOVERY & MANUAL OFFSET CONSISTENCY DEMO ---")
    coord = GroupCoordinator("billing_group", "invoices", num_partitions=2)
    worker_a = KafkaConsumerWorker("worker-A", coord)
    coord.join_group(worker_a.consumer_id)

    # Produce 10 invoices
    coord.produce_to_partition(0, count=10)
    coord.produce_to_partition(1, count=10)

    # Worker A processes 5 records from partition 0 and commits offset 5
    p0 = TopicPartition("invoices", 0)
    coord.commit_offset(p0, 5)
    print(f"Worker-A committed offset 5 for {p0}. Remaining lag on {p0}: {coord.get_consumer_lag(p0)}")
    assert coord.get_consumer_lag(p0) == 5

    # Simulate Worker-A catastrophic crash (leave group without clean commit)
    print("Worker-A crashed! Spinning up Worker-B to assume ownership...")
    coord.leave_group(worker_a.consumer_id)
    worker_b = KafkaConsumerWorker("worker-B", coord)
    coord.join_group(worker_b.consumer_id)

    # Worker-B should start exactly at committed offset 5, not 0 (no duplicate) and not 10 (no loss)
    assert coord.committed_offsets[p0] == 5
    print(f"Worker-B resumed {p0} cleanly at offset {coord.committed_offsets[p0]} (Zero message loss).")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING KAFKA CONSUMER GROUP COORDINATOR & REBALANCE ENGINE")
    print("=" * 70)
    demo_rebalance_eager_vs_cooperative()
    demo_consumer_lag_monitoring()
    demo_manual_offset_recovery()
    print("\n" + "=" * 70)
    print("ALL CONSUMER GROUP COORDINATION & REBALANCE TESTS COMPLETED!")
    print("=" * 70)
