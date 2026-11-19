# Day 71: Enterprise Practical Exercises: Apache Kafka

This workbook contains **3 enterprise-grade Kafka engineering exercises**. Each exercise presents a production scenario, precise architectural requirements, and a complete, tested reference implementation.

---

## Exercise 1: Resilient Manual Offset Committer with Dead-Letter Topic (DLT) Dispatch

### Scenario
A payments consumer processes financial webhooks. If an unrecoverable payload error (e.g., malformed customer schema) occurs, the consumer must not crash or loop infinitely. Instead, it must publish the poison pill to a **Dead-Letter Topic (DLT)**, commit the failed offset forward, and continue processing without stalling the pipeline.

### Requirements
1. Implement a `PaymentConsumerLoop` that pulls records from assigned partitions.
2. If business logic throws a `PermanentValidationError`, immediately route the record to the `payments.DLT` topic with failure diagnostic headers (`x-failure-reason`, `x-failed-at-offset`, `x-timestamp`).
3. Commit the offset of the failed message so the partition can progress.
4. If a transient error occurs, retry processing up to `MAX_RETRIES = 2` with backoff before falling back to DLT.

### Solution

```python
"""
Exercise 1 Solution: Resilient Manual Offset Committer with Dead-Letter Topic
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class PermanentValidationError(Exception):
    """Payload is structurally invalid or unparseable."""
    pass


class TransientSystemError(Exception):
    """Downstream database or gateway timeout."""
    pass


@dataclass
class KafkaMessage:
    topic: str
    partition: int
    offset: int
    key: Optional[str]
    value: Dict[str, Any]
    headers: Dict[str, Any] = field(default_factory=dict)


class ResilientDLTConsumer:
    def __init__(self, dlt_topic: str = "payments.DLT", max_retries: int = 2):
        self.dlt_topic = dlt_topic
        self.max_retries = max_retries
        self.committed_offsets: Dict[int, int] = {}
        self.dlt_records: List[KafkaMessage] = []
        self.successful_records: List[KafkaMessage] = []

    def simulate_business_processing(self, msg: KafkaMessage):
        payload = msg.value
        if payload.get("corrupted_schema"):
            raise PermanentValidationError("Missing mandatory field: 'merchant_id'")
        if payload.get("transient_timeout") and msg.headers.get("retry_count", 0) < 1:
            raise TransientSystemError("Payment gateway 504 Gateway Timeout")
        return True

    def process_and_commit(self, records: List[KafkaMessage]) -> None:
        for msg in records:
            p_id = msg.partition
            success = False
            attempts = 0

            while not success and attempts <= self.max_retries:
                try:
                    self.simulate_business_processing(msg)
                    self.successful_records.append(msg)
                    success = True
                    print(f"[SUCCESS] Processed message {msg.topic}-{msg.partition} at offset {msg.offset}")

                except PermanentValidationError as pve:
                    print(f"[DLT DISPATCH] Permanent validation failure on offset {msg.offset}: {pve}")
                    self._dispatch_to_dlt(msg, str(pve), "PERMANENT")
                    success = True  # Mark handled to advance offset

                except TransientSystemError as tse:
                    attempts += 1
                    msg.headers["retry_count"] = attempts
                    if attempts <= self.max_retries:
                        print(f"[RETRY] Transient failure on offset {msg.offset}: {tse}. Retry attempt #{attempts}...")
                        time.sleep(0.005)
                    else:
                        print(f"[DLT DISPATCH] Retries exhausted for offset {msg.offset}. Sending to DLT.")
                        self._dispatch_to_dlt(msg, str(tse), "RETRIES_EXHAUSTED")
                        success = True

            # Advance and commit offset (monotonic offset + 1)
            self.committed_offsets[p_id] = msg.offset + 1

    def _dispatch_to_dlt(self, msg: KafkaMessage, error_msg: str, error_type: str) -> None:
        dlt_msg = KafkaMessage(
            topic=self.dlt_topic,
            partition=0,
            offset=len(self.dlt_records),
            key=msg.key,
            value=msg.value,
            headers={
                **msg.headers,
                "x-failure-reason": error_msg,
                "x-failure-type": error_type,
                "x-origin-topic": msg.topic,
                "x-origin-partition": msg.partition,
                "x-origin-offset": msg.offset,
                "x-timestamp": time.time()
            }
        )
        self.dlt_records.append(dlt_msg)


# --- Verification Test ---
if __name__ == "__main__":
    consumer = ResilientDLTConsumer()

    # Batch of 4 messages
    batch = [
        KafkaMessage("payments", 0, 0, "tx_1", {"amount": 100}),                                # Success
        KafkaMessage("payments", 0, 1, "tx_2", {"amount": 200, "corrupted_schema": True}),      # Permanent Fail -> DLT
        KafkaMessage("payments", 0, 2, "tx_3", {"amount": 300, "transient_timeout": True}),     # Transient -> Succeeds on retry
        KafkaMessage("payments", 0, 3, "tx_4", {"amount": 400})                                 # Success
    ]

    consumer.process_and_commit(batch)

    # Assertions
    assert len(consumer.successful_records) == 3, f"Expected 3 successful, got {len(consumer.successful_records)}"
    assert len(consumer.dlt_records) == 1, f"Expected 1 DLT message, got {len(consumer.dlt_records)}"
    assert consumer.dlt_records[0].headers["x-origin-offset"] == 1
    assert "Missing mandatory field" in consumer.dlt_records[0].headers["x-failure-reason"]
    # Offset must have progressed to 4
    assert consumer.committed_offsets[0] == 4, f"Expected committed offset 4, got {consumer.committed_offsets[0]}"

    print("\nExercise 1: Resilient DLT Dispatch & Offset Progress verified successfully!")
```

---

## Exercise 2: Cooperative Sticky Partition Rebalancer Simulator

### Scenario
In a consumer group processing high-throughput event logs, node additions or rollouts must not freeze partition processing across all consumers. You must implement a **Cooperative Sticky Partition Assignor** (Kafka KIP-429) that leaves existing healthy partitions untouched, only migrating the minimum required partition slice.

### Requirements
1. Implement a `CooperativeStickyAssignor` algorithm:
   - Calculate target capacity per consumer ($N_{\text{partitions}} // N_{\text{consumers}}$).
   - If a partition is already assigned to a live consumer and that consumer is within capacity, preserve the assignment.
   - Only revoke partitions from consumers exceeding target capacity.
   - Assign newly unassigned partitions to new or under-capacity consumers in a second phase.
2. Verify that scaling from 2 consumers to 3 consumers on 6 partitions causes **only 2 partition transfers** rather than all 6.

### Solution

```python
"""
Exercise 2 Solution: Cooperative Sticky Partition Rebalancer (KIP-429)
"""
from __future__ import annotations
from typing import Dict, List, Set, Tuple


class CooperativeStickyAssignor:
    """
    Implements Kafka KIP-429 Cooperative Sticky Assignor logic:
    - Retains stable partition assignments.
    - Revokes only migrating partitions.
    """
    @classmethod
    def rebalance(
        cls,
        total_partitions: List[int],
        active_consumers: List[str],
        current_assignments: Dict[str, List[int]]
    ) -> Tuple[Dict[str, List[int]], List[int]]:
        """
        Returns (new_assignments, revoked_partitions).
        """
        consumers = sorted(active_consumers)
        if not consumers:
            return {}, list(total_partitions)

        num_parts = len(total_partitions)
        num_cons = len(consumers)
        base_target = num_parts // num_cons
        remainder = num_parts % num_cons

        # Max allowed partitions per consumer
        max_allowed = {c: base_target + (1 if i < remainder else 0) for i, c in enumerate(consumers)}

        new_assignments: Dict[str, List[int]] = {c: [] for c in consumers}
        revoked: List[int] = []
        unassigned: List[int] = []

        # Phase 1: Preserve existing valid assignments
        active_set = set(consumers)
        for c, parts in current_assignments.items():
            if c in active_set:
                limit = max_allowed[c]
                for p in parts:
                    if len(new_assignments[c]) < limit:
                        new_assignments[c].append(p)
                    else:
                        revoked.append(p)
                        unassigned.append(p)
            else:
                # Consumer departed
                unassigned.extend(parts)

        # Identify newly discovered partitions
        assigned_so_far = {p for plist in new_assignments.values() for p in plist}
        for p in total_partitions:
            if p not in assigned_so_far and p not in unassigned:
                unassigned.append(p)

        # Phase 2: Distribute unassigned partitions to consumers below capacity
        for p in sorted(unassigned):
            recipient = min(consumers, key=lambda c: len(new_assignments[c]))
            new_assignments[recipient].append(p)

        return new_assignments, revoked


# --- Verification Test ---
if __name__ == "__main__":
    partitions = [0, 1, 2, 3, 4, 5]  # 6 partitions

    # Step 1: Initial state with 2 consumers
    # C1 has [0, 1, 2], C2 has [3, 4, 5]
    initial_assignments = {
        "C1": [0, 1, 2],
        "C2": [3, 4, 5]
    }

    # Step 2: Scale up to 3 consumers (C1, C2, C3)
    # Each consumer should get exactly 2 partitions (6 // 3 = 2)
    new_assignments, revoked = CooperativeStickyAssignor.rebalance(
        total_partitions=partitions,
        active_consumers=["C1", "C2", "C3"],
        current_assignments=initial_assignments
    )

    print("Initial Assignments:")
    for c, p in initial_assignments.items():
        print(f"  {c} -> {p}")

    print("\nCooperative Sticky Rebalance (C3 joined):")
    for c, p in sorted(new_assignments.items()):
        print(f"  {c} -> {p}")

    print(f"\nRevoked Partitions for Transfer: {revoked}")

    # Assertions
    # 1. Total revoked must be exactly 2 partitions (one from C1 and one from C2 to give to C3)
    assert len(revoked) == 2, f"Expected exactly 2 revocations, got {len(revoked)}: {revoked}"
    # 2. Every consumer has exactly 2 partitions
    assert all(len(p) == 2 for p in new_assignments.values())
    # 3. All 6 partitions are covered uniquely
    all_assigned = [p for plist in new_assignments.values() for p in plist]
    assert sorted(all_assigned) == partitions

    print("\nExercise 2: Cooperative Sticky Rebalance verified successfully!")
```

---

## Exercise 3: Kafka Log Compactor Engine with Tombstones

### Scenario
An account balance service uses a Kafka topic as an event-sourced database changelog. As millions of balance updates occur, disk usage must not grow unboundedly. You must implement a **Log Compaction Cleaner** that scans closed log segments, deduplicates keys, preserves the latest state, and handles deletion tombstones (`value=None`) respecting retention delays.

### Requirements
1. Implement a `LogCompactor` that takes an array of historical records across closed segments.
2. Retain only the record with the highest offset for each distinct key.
3. If a record has `value=None` (a tombstone), retain it only if `(current_time - record.timestamp) < tombstone_retention_sec`.
4. Output a clean, compacted record array verifying that obsolete historical state is deleted while active state and recent tombstones are preserved.

### Solution

```python
"""
Exercise 3 Solution: Kafka Log Compaction Engine
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class LogRecord:
    offset: int
    key: str
    value: Optional[Any]
    timestamp: float


class KafkaLogCompactor:
    def __init__(self, tombstone_retention_sec: float = 86400.0):
        self.tombstone_retention_sec = tombstone_retention_sec

    def compact(self, raw_records: List[LogRecord], current_time: Optional[float] = None) -> List[LogRecord]:
        now = current_time if current_time is not None else time.time()

        # Step 1: Scan records forward; latest offset wins
        latest_by_key: Dict[str, LogRecord] = {}
        for rec in raw_records:
            latest_by_key[rec.key] = rec

        # Step 2: Filter out expired tombstones
        compacted: List[LogRecord] = []
        for rec in sorted(latest_by_key.values(), key=lambda r: r.offset):
            if rec.value is None:
                # Check tombstone expiration
                age = now - rec.timestamp
                if age < self.tombstone_retention_sec:
                    compacted.append(rec)  # Keep recent tombstone for downstream consumers
                else:
                    # Expired tombstone: physically delete
                    continue
            else:
                compacted.append(rec)

        return compacted


# --- Verification Test ---
if __name__ == "__main__":
    compactor = KafkaLogCompactor(tombstone_retention_sec=60.0)  # 60s tombstone retention
    ref_time = 1000.0

    raw_log = [
        LogRecord(offset=0, key="acc_1", value={"bal": 100}, timestamp=ref_time - 120),
        LogRecord(offset=1, key="acc_2", value={"bal": 250}, timestamp=ref_time - 110),
        LogRecord(offset=2, key="acc_1", value={"bal": 150}, timestamp=ref_time - 90),   # Overwrites offset 0
        LogRecord(offset=3, key="acc_3", value={"bal": 500}, timestamp=ref_time - 80),
        LogRecord(offset=4, key="acc_3", value=None,         timestamp=ref_time - 70),   # Expired Tombstone (>60s old)
        LogRecord(offset=5, key="acc_4", value={"bal": 900}, timestamp=ref_time - 30),
        LogRecord(offset=6, key="acc_4", value=None,         timestamp=ref_time - 10),   # Recent Tombstone (<60s old)
        LogRecord(offset=7, key="acc_2", value={"bal": 300}, timestamp=ref_time - 5),    # Overwrites offset 1
    ]

    result = compactor.compact(raw_log, current_time=ref_time)

    print("Compacted Log Records:")
    for r in result:
        print(f"  Offset {r.offset}: Key={r.key}, Value={r.value}")

    # Assertions
    # Expected survivors:
    # - acc_1: offset 2 (bal: 150)
    # - acc_2: offset 7 (bal: 300)
    # - acc_3: PURGED (tombstone offset 4 is 70s old > 60s retention)
    # - acc_4: offset 6 (tombstone preserved because age 10s < 60s)
    surviving_keys = [r.key for r in result]
    assert surviving_keys == ["acc_1", "acc_4", "acc_2"]
    assert len(result) == 3
    assert result[0].offset == 2 and result[0].value == {"bal": 150}
    assert result[1].offset == 6 and result[1].value is None
    assert result[2].offset == 7 and result[2].value == {"bal": 300}

    print("\nExercise 3: Kafka Log Compaction and Tombstone Purging verified successfully!")
```
