"""
Day 71: Apache Kafka Storage Engine & Producer Durability Simulator
===================================================================
Architecture & Principles Demonstrated:
1. Segment-based Commit Log & Sparse Indexing:
   - Partitions split into Segment files (.log, .index, .timeindex).
   - Rolling active segment upon reaching byte-size thresholds.
   - Sparse offset indexing with O(log S) binary search + sequential scan.
2. Log Retention & Compaction:
   - Time-based segment deletion (`cleanup.policy=delete`).
   - Log compaction (`cleanup.policy=compact`) with tombstone removal.
3. Producer Durability & In-Sync Replicas (ISR):
   - `acks=0`, `acks=1`, and `acks=all` with `min.insync.replicas`.
4. Idempotent Producer Deduplication:
   - Producer ID (PID) + Monotonic Sequence Number deduplication barrier.
"""

from __future__ import annotations
import bisect
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class KafkaRecord:
    offset: int
    key: Optional[str]
    value: Any
    timestamp: float
    producer_id: Optional[str] = None
    sequence_number: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def approximate_bytes(self) -> int:
        """Approximates raw on-disk serialized record size."""
        k_len = len(self.key.encode("utf-8")) if self.key else 0
        v_len = len(str(self.value).encode("utf-8")) if self.value is not None else 0
        return k_len + v_len + 32  # 32 bytes metadata overhead (offset, timestamp, CRC)


@dataclass
class IndexEntry:
    relative_offset: int
    physical_position: int


class LogSegment:
    """
    Simulates a Kafka disk segment comprising:
    - .log: sequential serialized records.
    - .index: sparse memory-mapped index mapping relative offset -> byte position.
    - .timeindex: maps timestamp -> offset.
    """
    def __init__(self, base_offset: int, max_segment_bytes: int = 1024, index_interval_bytes: int = 128):
        self.base_offset = base_offset
        self.max_segment_bytes = max_segment_bytes
        self.index_interval_bytes = index_interval_bytes

        self.records: List[KafkaRecord] = []
        self.sparse_index: List[IndexEntry] = []
        self.time_index: List[Tuple[float, int]] = []  # (timestamp, offset)

        self.bytes_written: int = 0
        self.bytes_since_last_index_entry: int = 0
        self.is_closed: bool = False

    def append(self, record: KafkaRecord) -> bool:
        """Appends record to segment and maintains sparse index."""
        if self.is_closed:
            raise RuntimeError(f"Cannot append to closed segment {self.base_offset}")

        rec_bytes = record.approximate_bytes
        physical_pos = self.bytes_written

        # Sparse indexing: index only when accumulated bytes exceed threshold
        if not self.sparse_index or self.bytes_since_last_index_entry >= self.index_interval_bytes:
            rel_offset = record.offset - self.base_offset
            self.sparse_index.append(IndexEntry(rel_offset, physical_pos))
            self.time_index.append((record.timestamp, record.offset))
            self.bytes_since_last_index_entry = 0

        self.records.append(record)
        self.bytes_written += rec_bytes
        self.bytes_since_last_index_entry += rec_bytes

        if self.bytes_written >= self.max_segment_bytes:
            self.is_closed = True

        return self.is_closed

    def find_record_by_offset(self, target_offset: int) -> Optional[KafkaRecord]:
        """
        Sparse Index Lookup Algorithm:
        1. Binary search the sparse index to find the highest indexed offset <= target.
        2. Scan sequentially from that position to the target offset.
        """
        if not self.records or target_offset < self.base_offset:
            return None

        rel_target = target_offset - self.base_offset
        # Binary search for rightmost entry where relative_offset <= rel_target
        rel_offsets = [entry.relative_offset for entry in self.sparse_index]
        idx = bisect.bisect_right(rel_offsets, rel_target) - 1

        if idx < 0:
            start_scan_idx = 0
        else:
            # Physical position points into record array
            # In our simulation, map physical entry back to records index
            indexed_rel = self.sparse_index[idx].relative_offset
            start_scan_idx = indexed_rel

        # Sequential scan from the sparse anchor
        for rec in self.records[start_scan_idx:]:
            if rec.offset == target_offset:
                return rec
            if rec.offset > target_offset:
                break
        return None

    @property
    def max_timestamp(self) -> float:
        return self.records[-1].timestamp if self.records else 0.0


class CleanupPolicy(str, Enum):
    DELETE = "delete"
    COMPACT = "compact"


class PartitionStorageEngine:
    """
    Manages all segments in a single partition.
    Enforces segment rolling, sparse index lookups, time-based deletion, and compaction.
    """
    def __init__(
        self,
        partition_id: int,
        max_segment_bytes: int = 512,
        index_interval_bytes: int = 64,
        cleanup_policy: CleanupPolicy = CleanupPolicy.DELETE,
        retention_ms: float = 60_000.0
    ):
        self.partition_id = partition_id
        self.max_segment_bytes = max_segment_bytes
        self.index_interval_bytes = index_interval_bytes
        self.cleanup_policy = cleanup_policy
        self.retention_ms = retention_ms

        self.segments: List[LogSegment] = [
            LogSegment(base_offset=0, max_segment_bytes=max_segment_bytes, index_interval_bytes=index_interval_bytes)
        ]
        self.next_offset: int = 0

    @property
    def active_segment(self) -> LogSegment:
        return self.segments[-1]

    def append(self, key: Optional[str], value: Any, timestamp: Optional[float] = None, producer_id: Optional[str] = None, seq_no: Optional[int] = None) -> int:
        offset = self.next_offset
        ts = timestamp if timestamp is not None else time.time()
        record = KafkaRecord(
            offset=offset,
            key=key,
            value=value,
            timestamp=ts,
            producer_id=producer_id,
            sequence_number=seq_no
        )

        should_roll = self.active_segment.append(record)
        self.next_offset += 1

        if should_roll:
            # Create new active segment with base offset = next offset
            new_seg = LogSegment(
                base_offset=self.next_offset,
                max_segment_bytes=self.max_segment_bytes,
                index_interval_bytes=self.index_interval_bytes
            )
            self.segments.append(new_seg)

        return offset

    def read_offset(self, target_offset: int) -> Optional[KafkaRecord]:
        """Locates the segment containing target_offset and searches using sparse index."""
        if target_offset >= self.next_offset:
            return None

        # Binary search segments by base_offset
        base_offsets = [seg.base_offset for seg in self.segments]
        seg_idx = bisect.bisect_right(base_offsets, target_offset) - 1
        if seg_idx < 0:
            return None

        target_seg = self.segments[seg_idx]
        return target_seg.find_record_by_offset(target_offset)

    def enforce_retention(self, current_time: Optional[float] = None) -> int:
        """
        Executes cleanup policy:
        - DELETE: drops closed segments older than retention threshold.
        - COMPACT: collapses duplicate keys across closed segments.
        """
        now = current_time if current_time is not None else time.time()
        deleted_count = 0

        if self.cleanup_policy == CleanupPolicy.DELETE:
            # Active segment is NEVER deleted regardless of age
            retained_segments = []
            for seg in self.segments[:-1]:
                if (now - seg.max_timestamp) * 1000.0 > self.retention_ms:
                    deleted_count += 1
                else:
                    retained_segments.append(seg)
            retained_segments.append(self.active_segment)
            self.segments = retained_segments

        elif self.cleanup_policy == CleanupPolicy.COMPACT:
            # Compact all closed segments
            if len(self.segments) > 1:
                latest_by_key: Dict[str, KafkaRecord] = {}
                # Traverse closed segments
                for seg in self.segments[:-1]:
                    for rec in seg.records:
                        if rec.key is not None:
                            latest_by_key[rec.key] = rec

                # Reconstruct compacted segment
                compacted_seg = LogSegment(
                    base_offset=self.segments[0].base_offset,
                    max_segment_bytes=self.max_segment_bytes * 10,
                    index_interval_bytes=self.index_interval_bytes
                )
                for rec in sorted(latest_by_key.values(), key=lambda r: r.offset):
                    # Filter out tombstones (value is None)
                    if rec.value is not None:
                        compacted_seg.records.append(rec)
                        compacted_seg.bytes_written += rec.approximate_bytes

                compacted_seg.is_closed = True
                self.segments = [compacted_seg, self.active_segment]

        return deleted_count


class ProducerAcks(int, Enum):
    NONE = 0      # acks=0 (Fire-and-forget)
    LEADER = 1    # acks=1 (Leader local write)
    ALL = -1      # acks=all / -1 (Leader + min.insync.replicas)


class KafkaBrokerPartition:
    """
    High-availability Partition replica manager.
    Models Leader, Followers (ISR), and Min.InSync.Replicas enforcement.
    """
    def __init__(self, partition_id: int, total_replicas: int = 3, min_insync_replicas: int = 2):
        self.partition_id = partition_id
        self.total_replicas = total_replicas
        self.min_insync_replicas = min_insync_replicas
        self.storage = PartitionStorageEngine(partition_id=partition_id)
        # Set of replica node IDs currently in sync with the leader
        self.in_sync_replicas: Set[int] = set(range(total_replicas))
        # Idempotence barrier: producer_id -> last_sequence_number
        self.producer_state: Dict[str, int] = {}

    def simulate_follower_crash(self, replica_id: int):
        self.in_sync_replicas.discard(replica_id)

    def simulate_follower_recovery(self, replica_id: int):
        self.in_sync_replicas.add(replica_id)

    def write_record(
        self,
        key: Optional[str],
        value: Any,
        acks: ProducerAcks,
        producer_id: Optional[str] = None,
        sequence_number: Optional[int] = None
    ) -> Tuple[int, str]:
        """
        Handles writes with durability and idempotency checks.
        Returns (offset, status_message).
        """
        # 1. Idempotency Check
        if producer_id is not None and sequence_number is not None:
            last_seq = self.producer_state.get(producer_id)
            if last_seq is not None:
                if sequence_number == last_seq:
                    # Duplicate detected! Return existing offset idempotently
                    return self.storage.next_offset - 1, "DUPLICATE_IGNORED_IDEMPOTENT_ACK"
                elif sequence_number != last_seq + 1:
                    raise ValueError(f"Out-of-order sequence: expected {last_seq + 1}, got {sequence_number}")
            self.producer_state[producer_id] = sequence_number

        # 2. Durability / ISR Check
        if acks == ProducerAcks.ALL:
            if len(self.in_sync_replicas) < self.min_insync_replicas:
                raise RuntimeError(
                    f"NotEnoughReplicasException: in-sync replicas ({len(self.in_sync_replicas)}) < "
                    f"min.insync.replicas ({self.min_insync_replicas})"
                )

        # 3. Append to Leader Storage Engine
        offset = self.storage.append(
            key=key,
            value=value,
            producer_id=producer_id,
            seq_no=sequence_number
        )

        status = f"ACKED_{acks.name}"
        return offset, status


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_segment_rolling_and_sparse_indexing():
    print("\n--- 1. SEGMENT ROLLING & SPARSE INDEX BINARY SEARCH DEMO ---")
    # Low threshold of 300 bytes to force segment rolling
    engine = PartitionStorageEngine(partition_id=0, max_segment_bytes=300, index_interval_bytes=64)

    # Append 15 records to roll across multiple segments
    for i in range(15):
        engine.append(key=f"user_{i}", value={"event": "login", "idx": i, "data": "x" * 20})

    print(f"Total Records Appended: {engine.next_offset}")
    print(f"Total Segments Created: {len(engine.segments)} (including 1 active segment)")
    assert len(engine.segments) > 1, "Segments must roll after exceeding max_segment_bytes"

    # Verify base offsets of segments
    for idx, seg in enumerate(engine.segments):
        print(f"  Segment {idx}: Base Offset = {seg.base_offset}, Records = {len(seg.records)}, Bytes = {seg.bytes_written}")

    # Lookup offset using sparse binary search
    target_offset = 7
    found_rec = engine.read_offset(target_offset)
    assert found_rec is not None
    assert found_rec.offset == target_offset
    assert found_rec.key == f"user_{target_offset}"
    print(f"Sparse index binary search retrieved Record at Offset {target_offset}: Key = {found_rec.key}")


def demo_log_compaction():
    print("\n--- 2. LOG COMPACTION (KEY DEDUPLICATION & TOMBSTONES) DEMO ---")
    engine = PartitionStorageEngine(
        partition_id=0,
        max_segment_bytes=150,
        cleanup_policy=CleanupPolicy.COMPACT
    )

    # Produce state updates on same keys
    updates = [
        ("user_101", "name=Alice"),
        ("user_102", "name=Bob"),
        ("user_101", "name=Alice_Updated"),  # Overwrites user_101
        ("user_103", "name=Charlie"),
        ("user_102", "name=Bob_Final"),     # Overwrites user_102
        ("user_103", None),                  # Tombstone: delete user_103
        ("user_104", "name=Diana"),          # In active segment
    ]

    for k, v in updates:
        engine.append(key=k, value=v)

    print(f"Pre-compaction total segments: {len(engine.segments)}")
    engine.enforce_retention()
    print(f"Post-compaction total segments: {len(engine.segments)}")

    # Closed compacted segment must retain latest Alice and Bob, and omit Charlie (tombstone)
    compacted_records = {rec.key: rec.value for rec in engine.segments[0].records}
    print(f"Compacted State Map: {compacted_records}")
    assert compacted_records.get("user_101") == "name=Alice_Updated"
    assert compacted_records.get("user_102") == "name=Bob_Final"
    assert "user_103" not in compacted_records, "Tombstones must be purged during compaction"
    print("Log compaction verified: obsolete versions and tombstones cleanly pruned!")


def demo_durability_and_idempotence():
    print("\n--- 3. PRODUCER DURABILITY (ISR) & IDEMPOTENCE DEMO ---")
    partition = KafkaBrokerPartition(partition_id=0, total_replicas=3, min_insync_replicas=2)

    # 1. Normal write with acks=all
    offset, status = partition.write_record(
        key="tx_1",
        value={"amount": 500},
        acks=ProducerAcks.ALL,
        producer_id="pid_client_9",
        sequence_number=0
    )
    assert status == "ACKED_ALL"
    print(f"Write 1 succeeded at offset {offset} with status {status}")

    # 2. Duplicate retry with same sequence number (Simulating network retry)
    dup_offset, dup_status = partition.write_record(
        key="tx_1",
        value={"amount": 500},
        acks=ProducerAcks.ALL,
        producer_id="pid_client_9",
        sequence_number=0
    )
    assert dup_status == "DUPLICATE_IGNORED_IDEMPOTENT_ACK"
    assert dup_offset == offset
    print(f"Duplicate sequence number 0 intercepted cleanly: {dup_status}")

    # 3. Next sequential write succeeds
    offset_2, _ = partition.write_record(
        key="tx_2",
        value={"amount": 1000},
        acks=ProducerAcks.ALL,
        producer_id="pid_client_9",
        sequence_number=1
    )
    assert offset_2 == 1

    # 4. Out-of-order write triggers exception
    try:
        partition.write_record(
            key="tx_3",
            value={"amount": 1500},
            acks=ProducerAcks.ALL,
            producer_id="pid_client_9",
            sequence_number=5  # Gap: expected 2
        )
        assert False, "Should have raised Out-of-order sequence ValueError"
    except ValueError as ve:
        print(f"Out-of-order write intercepted: {ve}")

    # 5. Simulate broker failures causing ISR to drop below min.insync.replicas
    print("\nSimulating 2 follower node crashes (ISR drops to 1)...")
    partition.simulate_follower_crash(1)
    partition.simulate_follower_crash(2)
    assert len(partition.in_sync_replicas) == 1

    try:
        partition.write_record(
            key="tx_4",
            value={"amount": 2000},
            acks=ProducerAcks.ALL,
            producer_id="pid_client_9",
            sequence_number=2
        )
        assert False, "Should have raised NotEnoughReplicasException"
    except RuntimeError as re:
        print(f"Durability barrier upheld: {re}")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING APACHE KAFKA STORAGE ENGINE & PRODUCER SIMULATOR")
    print("=" * 70)
    demo_segment_rolling_and_sparse_indexing()
    demo_log_compaction()
    demo_durability_and_idempotence()
    print("\n" + "=" * 70)
    print("ALL KAFKA STORAGE ENGINE & DURABILITY TESTS PASSED CLEANLY!")
    print("=" * 70)
