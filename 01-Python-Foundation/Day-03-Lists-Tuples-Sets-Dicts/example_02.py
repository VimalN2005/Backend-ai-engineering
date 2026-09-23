"""
Day 03: Data Structures Internals (Lists, Tuples, Sets & Dictionaries)
File: example_02.py - Production Snapshot Reconciliation Engine (CDC / Sync)

WHY THIS PATTERN IS CRITICAL:
Backend microservices and data pipelines frequently sync records between external APIs
(e.g., Stripe, Shopify, Salesforce) and an internal database.
A naive nested-loop implementation comparing N records against M existing records runs
in O(N * M) time. For 50,000 records, this requires 2.5 billion iterations (~15 minutes of CPU freeze).

By leveraging Python sets and dictionary hash maps, this engine computes all created,
updated, deleted, and unchanged records in strictly linear O(N + M) time (< 0.1 seconds).
"""

import time
import hashlib
from typing import Dict, Set, List, NamedTuple


class Record(NamedTuple):
    """
    WHY NamedTuple:
    NamedTuples have the exact same memory footprint as regular tuples (zero per-instance
    dictionary overhead), but provide readable attribute access and guaranteed immutability.
    """
    id: int
    data_hash: str
    payload: dict


class SyncReport(NamedTuple):
    created: List[int]
    updated: List[int]
    deleted: List[int]
    unchanged: List[int]
    duration_ms: float


class SnapshotReconciliationEngine:
    """
    Calculates differential state transitions between two system snapshots.
    """

    @staticmethod
    def compute_hash(payload: dict) -> str:
        """Computes a deterministic hash of dictionary contents for O(1) equality check."""
        serialized = str(sorted(payload.items())).encode("utf-8")
        return hashlib.md5(serialized).hexdigest()

    @classmethod
    def reconcile(
        cls,
        existing_snapshot: Dict[int, Record],
        incoming_snapshot: Dict[int, Record]
    ) -> SyncReport:
        """
        Reconciles two snapshots using set theoretical differences in O(N + M) time.
        """
        start = time.perf_counter()

        # WHY: Extracting key sets gives us O(1) set operations
        existing_ids: Set[int] = set(existing_snapshot.keys())
        incoming_ids: Set[int] = set(incoming_snapshot.keys())

        # Set difference: IDs in incoming but NOT in existing -> CREATED
        created_ids = list(incoming_ids - existing_ids)

        # Set difference: IDs in existing but NOT in incoming -> DELETED
        deleted_ids = list(existing_ids - incoming_ids)

        # Set intersection: IDs present in BOTH snapshots -> candidates for UPDATE
        common_ids = existing_ids & incoming_ids

        updated_ids: List[int] = []
        unchanged_ids: List[int] = []

        # WHY: Compare pre-calculated MD5 content hashes rather than deep-inspecting nested dicts
        for rec_id in common_ids:
            if existing_snapshot[rec_id].data_hash != incoming_snapshot[rec_id].data_hash:
                updated_ids.append(rec_id)
            else:
                unchanged_ids.append(rec_id)

        duration = (time.perf_counter() - start) * 1000

        return SyncReport(
            created=created_ids,
            updated=updated_ids,
            deleted=deleted_ids,
            unchanged=unchanged_ids,
            duration_ms=duration
        )


def run_production_simulation() -> None:
    print("=" * 65)
    print("  PRODUCTION DATABASE SNAPSHOT RECONCILIATION BENCHMARK")
    print("=" * 65)

    dataset_size = 50_000
    print(f"Generating state snapshots with {dataset_size:,} records each...")

    # Build initial database snapshot
    existing_db: Dict[int, Record] = {}
    for i in range(dataset_size):
        payload = {"email": f"user_{i}@enterprise.com", "tier": "free"}
        existing_db[i] = Record(id=i, data_hash=SnapshotReconciliationEngine.compute_hash(payload), payload=payload)

    # Build incoming API snapshot with deliberate mutations:
    # - IDs 0 to 4,999: Mutated tier from 'free' to 'pro' (UPDATED)
    # - IDs 5,000 to 44,999: Unchanged (UNCHANGED)
    # - IDs 45,000 to 49,999: Dropped from incoming (DELETED)
    # - IDs 50,000 to 54,999: Newly introduced records (CREATED)
    incoming_api: Dict[int, Record] = {}

    for i in range(dataset_size + 5_000):
        if 45_000 <= i < 50_000:
            continue  # Simulate deleted records
        
        # Mutate first 5,000 records
        tier = "pro" if i < 5_000 else "free"
        payload = {"email": f"user_{i}@enterprise.com", "tier": tier}
        incoming_api[i] = Record(id=i, data_hash=SnapshotReconciliationEngine.compute_hash(payload), payload=payload)

    print("Executing set-theoretic reconciliation engine...")
    report = SnapshotReconciliationEngine.reconcile(existing_db, incoming_api)

    print("\n--- [RECONCILIATION SUMMARY REPORT] ---")
    print(f"Total Created Records : {len(report.created):,}")
    print(f"Total Updated Records : {len(report.updated):,}")
    print(f"Total Deleted Records : {len(report.deleted):,}")
    print(f"Total Unchanged Records: {len(report.unchanged):,}")
    print(f"Execution Latency     : {report.duration_ms:.2f} ms")
    print("=" * 65)


if __name__ == "__main__":
    run_production_simulation()
