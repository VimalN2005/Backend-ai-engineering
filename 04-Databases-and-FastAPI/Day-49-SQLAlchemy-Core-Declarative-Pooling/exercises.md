# SQLAlchemy 2.0 & Engine Pooling Practical Exercises

This document provides three senior-level database engineering challenges designed to reinforce mastery of SQLAlchemy 2.0 Core, connection pool event hooks, and dynamic query compilation. Complete reference solutions are included.

---

## Challenge 1: Connection Pool Leak & Saturation Monitor

### Problem Specification
Connection leaks occur when application code checks out a connection from `QueuePool` and fails to check it back in (e.g. due to unhandled exceptions or missing context managers). Over time, this exhausts `pool_size + max_overflow`, locking up the entire backend.

Build an event-driven monitoring class `ConnectionPoolGuard` that:
1. Listens to SQLAlchemy engine `checkout` and `checkin` events.
2. Tracks the active checked-out connection count, peak checkout concurrency, and connection hold duration.
3. Detects if any connection is held longer than `max_allowed_hold_sec` (e.g. 2.0 seconds), logging a warning with the offending thread ID and checkout timestamp.
4. Provides a `get_pool_health()` report returning current active, peak, and leak warnings.

### Reference Solution & Verification Test

```python
import threading
import time
from typing import Dict, Any, List
from sqlalchemy import create_engine, select, event, Engine
from sqlalchemy.pool import QueuePool

class ConnectionPoolGuard:
    def __init__(self, engine: Engine, max_allowed_hold_sec: float = 2.0):
        self.engine = engine
        self.max_allowed_hold_sec = max_allowed_hold_sec
        self.active_checkouts = 0
        self.peak_checkouts = 0
        self.leak_alerts: List[str] = []
        self._lock = threading.Lock()
        self._attach_listeners()

    def _attach_listeners(self) -> None:
        @event.listens_for(self.engine, "checkout")
        def on_checkout(dbapi_conn, conn_record, conn_proxy):
            with self._lock:
                self.active_checkouts += 1
                if self.active_checkouts > self.peak_checkouts:
                    self.peak_checkouts = self.active_checkouts
                conn_record.info["checkout_start"] = time.perf_counter()
                conn_record.info["thread_id"] = threading.get_ident()

        @event.listens_for(self.engine, "checkin")
        def on_checkin(dbapi_conn, conn_record):
            with self._lock:
                self.active_checkouts -= 1
                start = conn_record.info.pop("checkout_start", None)
                if start:
                    duration = time.perf_counter() - start
                    if duration > self.max_allowed_hold_sec:
                        self.leak_alerts.append(
                            f"Connection held for {duration:.2f}s by Thread {conn_record.info.get('thread_id')}"
                        )

    def get_pool_health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "active_checkouts": self.active_checkouts,
                "peak_checkouts": self.peak_checkouts,
                "leak_alerts_count": len(self.leak_alerts),
                "alerts": list(self.leak_alerts)
            }

# Verification Test
if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", poolclass=QueuePool, pool_size=5)
    guard = ConnectionPoolGuard(engine, max_allowed_hold_sec=0.1)

    # 1. Normal fast query
    with engine.connect() as conn:
        conn.execute(select(1))

    # 2. Simulated slow/leaky hold
    with engine.connect() as conn:
        conn.execute(select(1))
        time.sleep(0.15)  # Triggers hold warning!

    health = guard.get_pool_health()
    print("Pool Health Report:", health)
    assert health["active_checkouts"] == 0
    assert health["peak_checkouts"] == 1
    assert health["leak_alerts_count"] == 1
    print("Challenge 1 Verification Passed!")
```

---

## Challenge 2: Dynamic Multi-Tenant Query Filter (Core Expression AST Compiler)

### Problem Specification
In enterprise multi-tenant SaaS applications, every database query must strictly isolate data by `tenant_id`. Relying on developers to remember `.where(table.c.tenant_id == current_tenant)` on every query is prone to human error and cross-tenant data leaks.

Write a query compiler utility `enforce_tenant_isolation(stmt: Select, tenant_id: str, tenant_column_name: str = "tenant_id") -> Select` that:
1. Inspects the `froms` and table entities participating in the `Select` statement.
2. Identifies all tables that contain a column named `tenant_column_name`.
3. Appends an `AND` filter `table.c.tenant_id == tenant_id` for every participating tenant table.
4. Returns the modified type-safe `Select` statement without mutating the original query object.

### Reference Solution & Verification Test

```python
from sqlalchemy import MetaData, Table, Column, Integer, String, select, Select

def enforce_tenant_isolation(stmt: Select, tenant_id: str, tenant_column_name: str = "tenant_id") -> Select:
    """
    Dynamically injects tenant_id predicates into all participating tables in a Select AST.
    """
    modified_stmt = stmt
    # Inspect tables in the FROM clause
    for from_item in stmt.get_final_froms():
        if hasattr(from_item, "c") and tenant_column_name in from_item.c:
            col = from_item.c[tenant_column_name]
            modified_stmt = modified_stmt.where(col == tenant_id)
            
    return modified_stmt

# Verification Test
if __name__ == "__main__":
    metadata = MetaData()
    orders = Table(
        "orders", metadata,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", String(36)),
        Column("amount", Integer)
    )

    base_query = select(orders.c.id, orders.c.amount).where(orders.c.amount > 100)
    secure_query = enforce_tenant_isolation(base_query, tenant_id="acme_corp")

    compiled_sql = str(secure_query.compile(compile_kwargs={"literal_binds": True}))
    print("Compiled Secure SQL:\n", compiled_sql)
    
    assert "orders.tenant_id = 'acme_corp'" in compiled_sql
    assert "orders.amount > 100" in compiled_sql
    print("Challenge 2 Verification Passed!")
```

---

## Challenge 3: High-Performance Bulk Ingester with Automatic Chunking & Retries

### Problem Specification
Databases enforce strict parameter limits per query (SQLite allows up to 32,767 parameters; PostgreSQL allows 65,535). Attempting to bulk-insert 100,000 records with 5 columns in a single `conn.execute()` statement exceeds the parameter limit and raises `OperationalError: too many SQL variables`.

Implement a robust utility `bulk_insert_chunked(engine: Engine, table: Table, records: List[Dict[str, Any]], max_params_per_chunk: int = 30_000, max_retries: int = 3) -> int` that:
1. Calculates the optimal batch size based on the number of columns per record.
2. Slices records into safe chunks where `chunk_size * num_columns <= max_params_per_chunk`.
3. Executes each chunk inside an explicit transaction (`engine.begin()`).
4. Retries failed chunks up to `max_retries` with exponential backoff upon transient operational errors.
5. Returns the total number of successfully inserted rows.

### Reference Solution & Verification Test

```python
import math
import random
import time
from typing import List, Dict, Any
from sqlalchemy import MetaData, Table, Column, Integer, String, Engine, create_engine, insert, select, func

def bulk_insert_chunked(
    engine: Engine,
    table: Table,
    records: List[Dict[str, Any]],
    max_params_per_chunk: int = 30_000,
    max_retries: int = 3
) -> int:
    if not records:
        return 0

    num_cols = len(records[0].keys())
    # Calculate chunk size guaranteeing parameter count stays below limit
    chunk_size = max(1, max_params_per_chunk // num_cols)
    total_inserted = 0

    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]
        for attempt in range(max_retries):
            try:
                with engine.begin() as conn:
                    result = conn.execute(insert(table), chunk)
                    total_inserted += (result.rowcount or len(chunk))
                break  # Chunk succeeded!
            except Exception as ex:
                if attempt == max_retries - 1:
                    raise
                sleep_sec = random.uniform(0.01, 0.05 * (2 ** attempt))
                time.sleep(sleep_sec)

    return total_inserted

# Verification Test
if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:")
    meta = MetaData()
    test_table = Table(
        "bulk_items", meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(50)),
        Column("category", String(50)),
        Column("sku", String(50))
    )
    meta.create_all(engine)

    # 10,000 items x 3 parameters = 30,000 parameters!
    dataset = [{"name": f"item_{i}", "category": "electronics", "sku": f"SKU_{i}"} for i in range(10_000)]
    
    # Run with small chunk limit to force multiple chunks
    inserted = bulk_insert_chunked(engine, test_table, dataset, max_params_per_chunk=3000)
    print(f"Successfully chunked and inserted {inserted} records.")
    
    with engine.connect() as conn:
        count = conn.execute(select(func.count(test_table.c.id))).scalar_one()
    assert count == 10_000
    print("Challenge 3 Verification Passed!")
```
