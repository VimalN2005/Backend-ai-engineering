r"""
Day 33: Example 02 - Production Database Constraints & Indexing Engine
======================================================================

This module implements an enterprise-grade simulation of database-native constraints
(`UniqueConstraint` with partial conditions, `CheckConstraint`), composite B-Tree indexes
(enforcing the Leftmost Prefix Rule), and GIN inverted indexes for JSON/array fields.

WHY THIS ARCHITECTURE MATTERS:
1. Defense-in-Depth Constraints: Application-level validations (`clean()`, DRF serializers)
   are susceptible to race conditions under concurrent writes. Database-level constraints
   (`UNIQUE`, `CHECK`) are enforced inside PostgreSQL transactions, guaranteeing ACID consistency.
2. Soft-Delete Unique Conflict Resolution: With traditional `unique=True`, once a user is
   soft-deleted (`is_deleted=True`), another user cannot register with that same email!
   Django's `UniqueConstraint(fields=['email'], condition=Q(is_deleted=False))` solves this
   via PostgreSQL partial indexes (indexes with a WHERE clause).
3. The Leftmost Prefix Rule: Composite indexes `(tenant_id, status, created_at)` cannot accelerate
   queries filtering only by `status` or `created_at`. Understanding this prevents engineers from
   bloating databases with useless indexes.
4. GIN Index Speedup: Inverted indexes map individual tags or JSON keys to row IDs, turning
   $\mathcal{O}(N)$ sequential scans on semi-structured JSONB into $\mathcal{O}(1)$ dictionary lookups.
"""

from __future__ import annotations

import bisect
import decimal
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class IntegrityError(Exception):
    """Raised when a database-level constraint (UNIQUE, CHECK, NOT NULL) is violated."""
    pass


# -----------------------------------------------------------------------------
# Declarative Constraint Architecture
# -----------------------------------------------------------------------------

class Constraint:
    """Base class for database-level constraints."""
    def __init__(self, name: str):
        self.name = name

    def validate(self, records: List[Dict[str, Any]], new_record: Dict[str, Any]) -> None:
        raise NotImplementedError


class UniqueConstraint(Constraint):
    """
    Simulates PostgreSQL UNIQUE constraint with partial index condition.
    
    Example:
      UniqueConstraint(
          fields=["tenant_id", "email"],
          condition=lambda r: not r.get("is_deleted", False),
          name="unique_active_tenant_email"
      )
    """
    def __init__(
        self,
        fields: List[str],
        name: str,
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ):
        super().__init__(name)
        self.fields = fields
        self.condition = condition

    def validate(self, records: List[Dict[str, Any]], new_record: Dict[str, Any]) -> None:
        # If record does not satisfy the partial condition, it is exempt from the unique index
        if self.condition and not self.condition(new_record):
            return

        new_key = tuple(new_record.get(f) for f in self.fields)
        
        # In SQL, NULL values never violate uniqueness: (NULL == NULL is false)
        if None in new_key:
            return

        for record in records:
            # Skip records that don't match the condition or are identical by primary key (updates)
            if self.condition and not self.condition(record):
                continue
            if record.get("id") == new_record.get("id"):
                continue

            existing_key = tuple(record.get(f) for f in self.fields)
            if new_key == existing_key:
                field_str = ", ".join(f"{f}='{new_record.get(f)}'" for f in self.fields)
                raise IntegrityError(
                    f"UNIQUE constraint failed: '{self.name}'. "
                    f"Duplicate entry found for ({field_str})."
                )


class CheckConstraint(Constraint):
    """
    Simulates PostgreSQL CHECK constraint.
    Enforces algebraic and domain invariants directly at the database engine level.
    """
    def __init__(self, check: Callable[[Dict[str, Any]], bool], name: str, violation_message: str):
        super().__init__(name)
        self.check = check
        self.violation_message = violation_message

    def validate(self, records: List[Dict[str, Any]], new_record: Dict[str, Any]) -> None:
        if not self.check(new_record):
            raise IntegrityError(
                f"CHECK constraint failed: '{self.name}'. Reason: {self.violation_message}"
            )


# -----------------------------------------------------------------------------
# Indexing Architecture (B-Tree Composite & GIN Inverted Indexes)
# -----------------------------------------------------------------------------

class CompositeBTreeIndex:
    """
    Simulates a PostgreSQL multi-column B-Tree index.
    
    Demonstrates the Leftmost Prefix Rule:
    An index on (A, B, C) can satisfy:
    - (A)
    - (A, B)
    - (A, B, C)
    It CANNOT satisfy:
    - (B)
    - (C)
    - (B, C)
    """
    def __init__(self, name: str, fields: List[str]):
        self.name = name
        self.fields = fields  # Ordered list of column names
        # In-memory sorted array representing B-tree leaf nodes: list of (tuple_of_keys, row_id)
        self._keys: List[Tuple[Any, ...]] = []
        self._row_ids: List[Any] = []

    def insert(self, record: Dict[str, Any]) -> None:
        key = tuple(record.get(f) for f in self.fields)
        row_id = record["id"]
        # Maintain sorted order via binary search insertion (bisect)
        idx = bisect.bisect_left(self._keys, key)
        self._keys.insert(idx, key)
        self._row_ids.insert(idx, row_id)

    def can_accelerate(self, filter_fields: Set[str]) -> bool:
        """
        Evaluate if query filters satisfy the Leftmost Prefix Rule.
        The leading column (fields[0]) MUST be present in the query filter!
        """
        if not self.fields or self.fields[0] not in filter_fields:
            return False
        return True

    def lookup(self, filter_kwargs: Dict[str, Any]) -> Set[Any]:
        """
        Execute prefix range lookup over the B-Tree index in O(log N) time.
        """
        # Build prefix key from filter
        prefix = []
        for field_name in self.fields:
            if field_name in filter_kwargs:
                prefix.append(filter_kwargs[field_name])
            else:
                break
        
        prefix_tuple = tuple(prefix)
        matched_ids = set()

        # Binary search for the first occurrence of prefix
        idx = bisect.bisect_left(self._keys, prefix_tuple)
        while idx < len(self._keys):
            entry_key = self._keys[idx]
            # Check if entry matches the prefix
            if entry_key[:len(prefix_tuple)] == prefix_tuple:
                matched_ids.add(self._row_ids[idx])
                idx += 1
            else:
                break

        return matched_ids


class GINInvertedIndex:
    """
    Simulates a PostgreSQL GIN (Generalized Inverted Index) for array and JSON tags.
    Maps individual elements to sets of row IDs for O(1) containment lookups.
    """
    def __init__(self, name: str, field_name: str):
        self.name = name
        self.field_name = field_name
        self._inverted_table: Dict[str, Set[Any]] = {}

    def insert(self, record: Dict[str, Any]) -> None:
        row_id = record["id"]
        tags = record.get(self.field_name, [])
        if isinstance(tags, (list, set, tuple)):
            for tag in tags:
                tag_str = str(tag).lower()
                self._inverted_table.setdefault(tag_str, set()).add(row_id)

    def lookup_contains(self, required_tags: List[str]) -> Set[Any]:
        """Lookup rows that contain ALL required tags (PostgreSQL array containment @>)."""
        if not required_tags:
            return set()
        
        result_sets = []
        for tag in required_tags:
            tag_str = str(tag).lower()
            result_sets.append(self._inverted_table.get(tag_str, set()))

        # Set intersection for AND semantics
        return set.intersection(*result_sets) if result_sets else set()


# -----------------------------------------------------------------------------
# Enterprise Table Engine (Schema, Constraints & Execution Planner)
# -----------------------------------------------------------------------------

@dataclass
class QueryExecutionPlan:
    method: str  # "INDEX_SCAN" or "FULL_TABLE_SCAN"
    index_name: Optional[str]
    rows_examined: int
    rows_matched: int
    duration_ms: float
    matched_records: List[Dict[str, Any]]


class EnterpriseDatabaseTable:
    """
    Simulates an enterprise PostgreSQL table equipped with constraints,
    composite B-tree indexes, GIN indexes, and an execution query planner.
    """
    def __init__(
        self,
        table_name: str,
        constraints: List[Constraint],
        indexes: List[CompositeBTreeIndex],
        gin_indexes: Optional[List[GINInvertedIndex]] = None,
    ):
        self.table_name = table_name
        self.constraints = constraints
        self.indexes = indexes
        self.gin_indexes = gin_indexes or []
        
        # Raw storage: dictionary mapping ID -> Record
        self._storage: Dict[Any, Dict[str, Any]] = {}

    def insert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a record within an ACID transaction:
        1. Validate all database constraints (UNIQUE, CHECK).
        2. Persist to storage.
        3. Update all B-Tree and GIN indexes.
        """
        record = record.copy()
        if "id" not in record:
            record["id"] = str(uuid.uuid4())

        # 1. Execute constraint validations
        existing_records = list(self._storage.values())
        for constraint in self.constraints:
            constraint.validate(existing_records, record)

        # 2. Persist
        row_id = record["id"]
        self._storage[row_id] = record

        # 3. Update Indexes
        for idx in self.indexes:
            idx.insert(record)
        for gin_idx in self.gin_indexes:
            gin_idx.insert(record)

        return record

    def execute_query(
        self,
        filters: Dict[str, Any],
        contains_tags: Optional[List[str]] = None,
    ) -> QueryExecutionPlan:
        """
        Simulate database query optimizer:
        Inspects available indexes, chooses between Index Scan and Full Table Scan,
        and returns results alongside execution telemetry.
        """
        start_time = time.perf_counter()
        filter_fields = set(filters.keys())
        
        # 1. Check GIN index eligibility
        if contains_tags and self.gin_indexes:
            gin = self.gin_indexes[0]
            matched_ids = gin.lookup_contains(contains_tags)
            
            # Filter remaining conditions
            final_records = []
            for row_id in matched_ids:
                rec = self._storage[row_id]
                if all(rec.get(k) == v for k, v in filters.items()):
                    final_records.append(rec)

            duration_ms = (time.perf_counter() - start_time) * 1000
            return QueryExecutionPlan(
                method="INDEX_SCAN (GIN)",
                index_name=gin.name,
                rows_examined=len(matched_ids),
                rows_matched=len(final_records),
                duration_ms=duration_ms,
                matched_records=final_records,
            )

        # 2. Check B-Tree Composite index eligibility
        selected_index = None
        for idx in self.indexes:
            if idx.can_accelerate(filter_fields):
                selected_index = idx
                break

        if selected_index is not None:
            # INDEX SCAN: O(log N) lookup
            candidate_ids = selected_index.lookup(filters)
            final_records = []
            for row_id in candidate_ids:
                rec = self._storage[row_id]
                if all(rec.get(k) == v for k, v in filters.items()):
                    final_records.append(rec)
            
            duration_ms = (time.perf_counter() - start_time) * 1000
            return QueryExecutionPlan(
                method="INDEX_SCAN (B-TREE)",
                index_name=selected_index.name,
                rows_examined=len(candidate_ids),
                rows_matched=len(final_records),
                duration_ms=duration_ms,
                matched_records=final_records,
            )

        # 3. Fallback: FULL TABLE SCAN (Sequential Scan O(N))
        final_records = []
        for rec in self._storage.values():
            if all(rec.get(k) == v for k, v in filters.items()):
                if contains_tags:
                    rec_tags = rec.get("tags", [])
                    if not all(t in rec_tags for t in contains_tags):
                        continue
                final_records.append(rec)

        duration_ms = (time.perf_counter() - start_time) * 1000
        return QueryExecutionPlan(
            method="FULL_TABLE_SCAN",
            index_name=None,
            rows_examined=len(self._storage),
            rows_matched=len(final_records),
            duration_ms=duration_ms,
            matched_records=final_records,
        )


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 33 Constraints & Indexing Verification Suite...")

    # Set up Table with Constraints and Indexes
    table = EnterpriseDatabaseTable(
        table_name="tenant_orders",
        constraints=[
            # 1. Partial Unique Constraint: Only active orders must have unique idempotency keys
            UniqueConstraint(
                fields=["tenant_id", "idempotency_key"],
                condition=lambda r: not r.get("is_cancelled", False),
                name="unique_active_tenant_idempotency",
            ),
            # 2. Check Constraint: Total amount must be non-negative
            CheckConstraint(
                check=lambda r: r.get("total_amount", 0) >= decimal.Decimal("0.00"),
                name="positive_total_amount",
                violation_message="Order total_amount cannot be negative.",
            ),
        ],
        indexes=[
            # Composite index on (tenant_id, status, created_at)
            CompositeBTreeIndex(
                name="idx_tenant_status_created",
                fields=["tenant_id", "status", "created_at"],
            )
        ],
        gin_indexes=[
            GINInvertedIndex(name="idx_order_tags_gin", field_name="tags")
        ],
    )

    # 1. Test Valid Insertion
    o1 = table.insert({
        "tenant_id": 101,
        "idempotency_key": "IDEM-ABC-001",
        "status": "PAID",
        "total_amount": decimal.Decimal("150.00"),
        "created_at": "2026-01-01",
        "is_cancelled": False,
        "tags": ["express", "priority"],
    })
    assert o1["id"] in table._storage
    print("  [+] Valid record insertion and index population verified.")

    # 2. Test Partial Unique Constraint Enforcement
    try:
        table.insert({
            "tenant_id": 101,
            "idempotency_key": "IDEM-ABC-001",  # Duplicate active key for same tenant!
            "status": "PENDING",
            "total_amount": decimal.Decimal("20.00"),
            "created_at": "2026-01-02",
            "is_cancelled": False,
        })
        assert False, "Should have rejected duplicate active idempotency_key"
    except IntegrityError:
        pass
    print("  [+] UniqueConstraint rejected duplicate active record.")

    # 3. Test Partial Unique Exemption (Cancelled / Soft-Deleted Record)
    # Inserting the same key but with is_cancelled=True MUST SUCCEED because of the partial condition!
    cancelled_order = table.insert({
        "tenant_id": 101,
        "idempotency_key": "IDEM-ABC-001",
        "status": "CANCELLED",
        "total_amount": decimal.Decimal("150.00"),
        "created_at": "2026-01-03",
        "is_cancelled": True,
    })
    assert cancelled_order["id"] in table._storage
    print("  [+] Partial unique constraint successfully exempted cancelled record.")

    # 4. Test Check Constraint Enforcement
    try:
        table.insert({
            "tenant_id": 101,
            "idempotency_key": "IDEM-XYZ-999",
            "status": "PENDING",
            "total_amount": decimal.Decimal("-50.00"),  # Negative amount violates CHECK
            "is_cancelled": False,
        })
        assert False, "Should have rejected negative total_amount"
    except IntegrityError:
        pass
    print("  [+] CheckConstraint rejected negative total_amount.")

    # 5. Test Leftmost Prefix Rule on Composite Index
    # Query matching leading prefix: tenant_id + status -> INDEX SCAN
    plan1 = table.execute_query({"tenant_id": 101, "status": "PAID"})
    assert plan1.method == "INDEX_SCAN (B-TREE)"
    assert plan1.index_name == "idx_tenant_status_created"
    assert len(plan1.matched_records) == 1
    print("  [+] Leftmost prefix query correctly leveraged B-Tree Index Scan.")

    # Query skipping leading column: status alone -> FULL TABLE SCAN
    plan2 = table.execute_query({"status": "PAID"})
    assert plan2.method == "FULL_TABLE_SCAN"
    assert plan2.index_name is None
    print("  [+] Non-prefix query correctly fell back to Full Table Scan.")

    # 6. Test GIN Inverted Index Lookups
    plan3 = table.execute_query({}, contains_tags=["express"])
    assert plan3.method == "INDEX_SCAN (GIN)"
    assert plan3.index_name == "idx_order_tags_gin"
    assert len(plan3.matched_records) == 1
    print("  [+] GIN Inverted Index tag containment query verified.")


def benchmark_indexing_speedup(num_records: int = 10_000) -> None:
    """Benchmark performance difference between Full Table Scan and B-Tree Index Scan."""
    table = EnterpriseDatabaseTable(
        table_name="bench_orders",
        constraints=[],
        indexes=[
            CompositeBTreeIndex(
                name="idx_tenant_status_created",
                fields=["tenant_id", "status", "created_at"],
            )
        ],
    )

    print(f"\n[*] Generating {num_records:,} database records for index benchmark...")
    for i in range(num_records):
        table.insert({
            "tenant_id": (i % 50) + 1,  # 50 distinct tenants
            "status": "PAID" if i % 2 == 0 else "PENDING",
            "created_at": f"2026-01-{(i % 28) + 1:02d}",
            "amount": i,
        })

    # Benchmark Full Table Scan (filtering without leading column)
    scan_start = time.perf_counter()
    for _ in range(500):
        _ = table.execute_query({"status": "PAID"})
    scan_time = time.perf_counter() - scan_start

    # Benchmark Index Scan (filtering with leading column)
    index_start = time.perf_counter()
    for _ in range(500):
        _ = table.execute_query({"tenant_id": 25, "status": "PAID"})
    index_time = time.perf_counter() - index_start

    speedup = scan_time / index_time

    print("\n" + "=" * 65)
    print("DAY 33: INDEX SCAN VS FULL TABLE SCAN BENCHMARK")
    print("=" * 65)
    print(f"Dataset Size:          {num_records:,} rows")
    print(f"500 Full Table Scans:  {scan_time:.4f} seconds ({(scan_time/500)*1000:.2f} ms/query)")
    print(f"500 B-Tree Index Scans:{index_time:.4f} seconds ({(index_time/500)*1000:.2f} ms/query)")
    print(f"Index Acceleration:    {speedup:.1f}x FASTER than sequential scan")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_indexing_speedup()
