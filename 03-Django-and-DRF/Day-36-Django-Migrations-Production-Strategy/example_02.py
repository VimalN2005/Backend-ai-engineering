r"""
Day 36: Example 02 - Production Zero-Downtime Migration Engine
==============================================================

This module implements an enterprise-grade architectural simulation of zero-downtime
database migrations, including the Expand-and-Contract lifecycle, safe addition of
NOT NULL columns via chunked backfills and NOT VALID constraints, concurrent index
creation with non-atomic transactions, and DDL lock-timeout guardrails.

WHY THIS ARCHITECTURE MATTERS:
1. Zero-Downtime Expand & Contract: In Kubernetes rolling deployments, old code (v1) and
   new code (v2) run concurrently. Destructive or renaming schema changes instantly break
   v1 pods. Expand-and-Contract decouples additive schema changes from removals.
2. Avoiding Long-Lived Table Locks: Adding a `NOT NULL DEFAULT 'x'` column on massive tables
   holds an `AccessExclusiveLock`. The safe pattern: Add nullable column -> Chunked backfill
   -> Add `CHECK (col IS NOT NULL) NOT VALID` -> Validate asynchronously.
3. Concurrent Indexing (atomic = False): Standard `CREATE INDEX` blocks all writes on the
   table. `CREATE INDEX CONCURRENTLY` builds the index in the background without locking
   writes, but requires running outside of an active database transaction block.
4. DDL Lock Timeout Guardrails: Running DDL without `SET lock_timeout = '2s'` causes the
   migration to wait behind active read queries, causing all subsequent application queries
   to pile up behind the lock request and crashing production traffic.
"""

from __future__ import annotations

import decimal
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple


class MigrationLockTimeoutError(Exception):
    """Raised when an ALTER TABLE lock cannot be acquired within the pre-configured lock_timeout."""
    pass


class NonAtomicMigrationError(Exception):
    """Raised when an operation requiring non-atomic execution is attempted inside a transaction."""
    pass


# -----------------------------------------------------------------------------
# Simulated Production Database Engine
# -----------------------------------------------------------------------------

class ProductionDatabaseEngine:
    """
    Simulates a PostgreSQL database engine tracking locks, tables, transactions,
    and schema operations.
    """
    def __init__(self):
        # Maps table_name -> list of row dicts
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        # Maps table_name -> schema dict: {column_name: {"type": str, "nullable": bool}}
        self.schemas: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # Active locks held by simulated application transactions
        self.active_table_locks: Set[str] = set()
        # Execution audit log
        self.query_log: List[str] = []

    def execute(self, sql: str, lock_timeout_sec: float = 2.0) -> None:
        """Executes a DDL or DML statement respecting lock_timeout guardrails."""
        # Check if table being modified is currently locked by a long-running query
        for table_name in self.tables:
            if f"ALTER TABLE {table_name}" in sql or f"DROP TABLE {table_name}" in sql:
                if table_name in self.active_table_locks:
                    # Simulate lock timeout guardrail aborting the migration
                    raise MigrationLockTimeoutError(
                        f"Lock timeout ({lock_timeout_sec}s) exceeded while attempting to acquire "
                        f"AccessExclusiveLock on table '{table_name}'. Migration aborted safely."
                    )
        self.query_log.append(sql)


# Global DB engine singleton
PROD_DB = ProductionDatabaseEngine()


# -----------------------------------------------------------------------------
# Zero-Downtime Expand and Contract Migration Pipeline
# -----------------------------------------------------------------------------

class ZeroDowntimeMigrationEngine:
    """
    Orchestrates the 3-phase Expand-and-Contract migration protocol:
    1. Phase 1 (Expand): Add nullable column.
    2. Phase 2 (Dual Write & Chunked Backfill): Background chunked update.
    3. Phase 3 (Contract): Add check constraint, validate, and drop old column.
    """
    def __init__(self, db: ProductionDatabaseEngine = PROD_DB):
        self.db = db

    def phase_1_expand(self, table_name: str, new_column_name: str, column_type: str) -> None:
        """
        Step 1 (Pre-Deploy): Add new column as NULLABLE.
        Zero table rewrite, instantaneous lock. Old containers (v1) unaffected.
        """
        sql = f"ALTER TABLE {table_name} ADD COLUMN {new_column_name} {column_type} NULL;"
        self.db.execute(sql)
        self.db.schemas[table_name][new_column_name] = {"type": column_type, "nullable": True}

    def phase_2_chunked_backfill(
        self,
        table_name: str,
        source_col: str,
        target_col: str,
        transform_fn: Callable[[Any], Any],
        batch_size: int = 500,
    ) -> int:
        """
        Step 2 (Post-Deploy Background Task): Backfill existing rows in small batches.
        Prevents unbounded row-level write locks.
        """
        rows = self.db.tables.get(table_name, [])
        total_backfilled = 0

        # Stream rows in chunks
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            for row in chunk:
                if row.get(target_col) is None:
                    row[target_col] = transform_fn(row.get(source_col))
                    total_backfilled += 1
            
            # Log chunked update execution
            self.db.execute(
                f"UPDATE {table_name} SET {target_col} = <calculated> "
                f"WHERE id BETWEEN {i+1} AND {i+len(chunk)};"
            )

        return total_backfilled

    def phase_3_contract_and_enforce_not_null(
        self,
        table_name: str,
        target_col: str,
        old_col_to_drop: Optional[str] = None,
    ) -> None:
        """
        Step 3 (Post-Deployment Contract):
        - Add CHECK (target_col IS NOT NULL) NOT VALID (instant lock).
        - VALIDATE CONSTRAINT (scans table without blocking concurrent writes).
        - Drop obsolete column once v1 code is decommissioned.
        """
        constraint_name = f"chk_{table_name}_{target_col}_not_null"

        # 1. Add Constraint NOT VALID (Fast, doesn't lock writes!)
        self.db.execute(
            f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
            f"CHECK ({target_col} IS NOT NULL) NOT VALID;"
        )

        # 2. Validate Constraint (Scans existing data concurrently)
        self.db.execute(f"ALTER TABLE {table_name} VALIDATE CONSTRAINT {constraint_name};")
        self.db.schemas[table_name][target_col]["nullable"] = False

        # 3. Drop obsolete column if specified
        if old_col_to_drop:
            self.db.execute(f"ALTER TABLE {table_name} DROP COLUMN {old_col_to_drop};")
            self.db.schemas[table_name].pop(old_col_to_drop, None)
            for row in self.db.tables.get(table_name, []):
                row.pop(old_col_to_drop, None)


# -----------------------------------------------------------------------------
# Concurrent Index Builder (CREATE INDEX CONCURRENTLY)
# -----------------------------------------------------------------------------

class ConcurrentIndexBuilder:
    """
    Executes PostgreSQL CREATE INDEX CONCURRENTLY.
    Enforces the rule that concurrent index creation must run with atomic = False.
    """
    @staticmethod
    def create_index_concurrently(
        db: ProductionDatabaseEngine,
        table_name: str,
        index_name: str,
        columns: List[str],
        is_in_transaction: bool = False,
    ) -> None:
        if is_in_transaction:
            raise NonAtomicMigrationError(
                "CRITICAL POSTGRESQL CONSTRAINT: 'CREATE INDEX CONCURRENTLY' cannot run "
                "inside a transaction block! You must set 'atomic = False' on this migration class."
            )

        cols_str = ", ".join(columns)
        sql = f"CREATE INDEX CONCURRENTLY {index_name} ON {table_name} ({cols_str});"
        db.execute(sql)


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 36 Production Zero-Downtime Migration Suite...")
    PROD_DB.tables.clear()
    PROD_DB.schemas.clear()
    PROD_DB.query_log.clear()
    PROD_DB.active_table_locks.clear()

    # Seed orders table with 2,500 rows
    table_name = "orders"
    PROD_DB.schemas[table_name] = {
        "id": {"type": "BIGINT", "nullable": False},
        "order_number": {"type": "VARCHAR(50)", "nullable": False},
        "customer_id": {"type": "BIGINT", "nullable": False},
    }
    PROD_DB.tables[table_name] = [
        {"id": i, "order_number": f"ORD-{i:05d}", "customer_id": (i % 50) + 1}
        for i in range(1, 2501)
    ]

    engine = ZeroDowntimeMigrationEngine(PROD_DB)

    # 1. Test Phase 1: Expand (Add nullable column)
    engine.phase_1_expand(table_name, "status_code", "VARCHAR(20)")
    assert "status_code" in PROD_DB.schemas[table_name]
    assert PROD_DB.schemas[table_name]["status_code"]["nullable"] is True
    print("  [+] Phase 1 (Expand): Added nullable column with zero table lock.")

    # 2. Test Phase 2: Dual Write & Chunked Backfill
    backfilled_count = engine.phase_2_chunked_backfill(
        table_name=table_name,
        source_col="order_number",
        target_col="status_code",
        transform_fn=lambda val: "COMPLETED",
        batch_size=500,
    )
    assert backfilled_count == 2500
    assert all(r["status_code"] == "COMPLETED" for r in PROD_DB.tables[table_name])
    print(f"  [+] Phase 2 (Backfill): Chunked backfill updated {backfilled_count:,} rows in batches of 500.")

    # 3. Test Phase 3: Contract & Enforce NOT NULL via NOT VALID constraint
    engine.phase_3_contract_and_enforce_not_null(
        table_name=table_name,
        target_col="status_code",
        old_col_to_drop=None,
    )
    assert PROD_DB.schemas[table_name]["status_code"]["nullable"] is False
    print("  [+] Phase 3 (Contract): Added and validated NOT NULL constraint safely.")

    # 4. Test Concurrent Index Creation (atomic = False requirement)
    try:
        # Fails if attempted inside transaction
        ConcurrentIndexBuilder.create_index_concurrently(
            PROD_DB, table_name, "idx_orders_status_cust", ["status_code", "customer_id"],
            is_in_transaction=True,
        )
        assert False, "Should have rejected concurrent index inside transaction!"
    except NonAtomicMigrationError as exc:
        assert "atomic = False" in str(exc)
    print("  [+] ConcurrentIndexBuilder correctly enforced 'atomic = False'.")

    # Succeeded outside transaction
    ConcurrentIndexBuilder.create_index_concurrently(
        PROD_DB, table_name, "idx_orders_status_cust", ["status_code", "customer_id"],
        is_in_transaction=False,
    )
    assert any("CREATE INDEX CONCURRENTLY" in s for s in PROD_DB.query_log)
    print("  [+] Concurrent index creation executed without locking table.")

    # 5. Test DDL Lock Timeout Pre-Flight Guardrail
    PROD_DB.active_table_locks.add("orders")
    try:
        # Attempting DDL while another query holds lock -> Aborts immediately!
        engine.phase_1_expand("orders", "risk_score", "INTEGER")
        assert False, "Should have raised MigrationLockTimeoutError!"
    except MigrationLockTimeoutError as exc:
        assert "Lock timeout" in str(exc)
    PROD_DB.active_table_locks.remove("orders")
    print("  [+] Lock timeout guardrail successfully aborted blocked migration to protect traffic.")


def benchmark_chunked_backfill(num_rows: int = 10_000, batch_size: int = 1_000) -> None:
    """Benchmark chunked backfill execution latency and throughput."""
    db = ProductionDatabaseEngine()
    db.schemas["bench_table"] = {"id": {"type": "BIGINT", "nullable": False}}
    db.tables["bench_table"] = [{"id": i, "legacy_val": i} for i in range(num_rows)]

    engine = ZeroDowntimeMigrationEngine(db)
    engine.phase_1_expand("bench_table", "new_val", "BIGINT")

    start = time.perf_counter()
    total_updated = engine.phase_2_chunked_backfill(
        table_name="bench_table",
        source_col="legacy_val",
        target_col="new_val",
        transform_fn=lambda v: v * 2,
        batch_size=batch_size,
    )
    elapsed = time.perf_counter() - start

    rows_per_sec = total_updated / elapsed

    print("\n" + "=" * 65)
    print("DAY 36: ZERO-DOWNTIME CHUNKED BACKFILL BENCHMARK")
    print("=" * 65)
    print(f"Total Rows Backfilled:     {total_updated:,} rows")
    print(f"Batch Chunk Size:          {batch_size:,} rows/batch")
    print(f"Total Execution Time:      {elapsed:.4f} seconds")
    print(f"Backfill Throughput:       {rows_per_sec:,.0f} rows/second")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_chunked_backfill()
