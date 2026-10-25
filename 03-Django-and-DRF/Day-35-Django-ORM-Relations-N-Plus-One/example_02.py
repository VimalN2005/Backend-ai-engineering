r"""
Day 35: Example 02 - Production select_related, prefetch_related & Prefetch Engine
==================================================================================

This module implements an enterprise-grade architectural simulation of Django's
relational optimization engine: `select_related` (SQL JOINs), `prefetch_related`
(bulk two-query hash stitching), `Prefetch` objects with `to_attr`, and an automated
query budget auditor (`assert_num_queries`).

WHY THIS ARCHITECTURE MATTERS:
1. Two-Tier Relational Optimization:
   - `select_related`: Pushes single-valued foreign key traversal into the database engine
     via SQL `INNER JOIN` / `LEFT OUTER JOIN`, returning all columns in 1 single query.
   - `prefetch_related`: Solves multi-valued relations (1-to-N, M-to-M) without Cartesian
     product explosions by executing 2 compact queries and joining them in Python via O(1) hash maps.
2. The Prefetch Invalidation Trap: Chaining `.filter()` on a prefetched relation inside a loop
   bypasses the in-memory cache and triggers N+1 queries. Using `Prefetch(..., to_attr=...)`
   pre-filters child collections into dedicated Python list attributes, guaranteeing zero SQL queries.
3. Strict Query Budgeting: Enforcing `assert_num_queries` ensures an endpoint processing 1,000
   orders never scales its query count with record volume ($\mathcal{O}(1)$ query complexity).
"""

from __future__ import annotations

import decimal
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple


# -----------------------------------------------------------------------------
# Database Engine Mock & Query Telemetry
# -----------------------------------------------------------------------------

class SQLTelemetryDriver:
    """Tracks and logs all SQL statements executed during a request."""
    def __init__(self):
        self.log: List[Dict[str, Any]] = []

    def record(self, sql: str, rows_returned: int, duration_ms: float) -> None:
        self.log.append({
            "sql": sql,
            "rows_returned": rows_returned,
            "duration_ms": duration_ms,
        })

    def clear(self) -> None:
        self.log.clear()

    @property
    def query_count(self) -> int:
        return len(self.log)


DB = SQLTelemetryDriver()


class QueryBudgetExceeded(AssertionError):
    pass


@contextmanager
def assert_num_queries(expected: int) -> Iterator[SQLTelemetryDriver]:
    """Context manager asserting exact SQL query budget."""
    start_count = DB.query_count
    try:
        yield DB
    finally:
        executed = DB.query_count - start_count
        if executed != expected:
            executed_sqls = [q["sql"] for q in DB.log[start_count:]]
            raise QueryBudgetExceeded(
                f"Query budget failed! Expected: {expected}, Executed: {executed}.\n"
                f"Executed SQL Statements:\n" + "\n".join(f"  [{i+1}] {s}" for i, s in enumerate(executed_sqls))
            )


# -----------------------------------------------------------------------------
# Model Declarations & Mock Relational Storage
# -----------------------------------------------------------------------------

@dataclass
class Organization:
    id: int
    name: str


@dataclass
class Customer:
    id: int
    name: str
    organization_id: int
    organization: Optional[Organization] = None


@dataclass
class OrderItem:
    id: int
    order_id: int
    product_name: str
    price: decimal.Decimal
    is_active: bool = True


@dataclass
class Order:
    id: int
    customer_id: int
    total_amount: decimal.Decimal
    status: str
    customer: Optional[Customer] = None
    items: List[OrderItem] = field(default_factory=list)
    # Target attribute for custom Prefetch(to_attr=...)
    active_items: List[OrderItem] = field(default_factory=list)


# Physical tables in simulated PostgreSQL
TABLES = {
    "organizations": {
        1: Organization(id=1, name="Enterprise SaaS Corp"),
        2: Organization(id=2, name="FinTech Global"),
    },
    "customers": {
        101: Customer(id=101, name="Alice Smith", organization_id=1),
        102: Customer(id=102, name="Bob Jones", organization_id=2),
    },
    "orders": {},      # Seeded dynamically
    "order_items": {}, # Seeded dynamically
}


# -----------------------------------------------------------------------------
# Prefetch Specification Object
# -----------------------------------------------------------------------------

class Prefetch:
    """
    Specifies advanced prefetch configuration:
    - Custom filtering query
    - Custom target attribute (to_attr)
    Mirrors `django.db.models.Prefetch`.
    """
    def __init__(
        self,
        lookup: str,
        filter_fn: Optional[Callable[[OrderItem], bool]] = None,
        to_attr: Optional[str] = None,
    ):
        self.lookup = lookup
        self.filter_fn = filter_fn
        self.to_attr = to_attr


# -----------------------------------------------------------------------------
# Optimized Relational QuerySet Engine
# -----------------------------------------------------------------------------

class OptimizedQuerySet:
    """
    Simulates Django's relational query compilation engine:
    Supports select_related (SQL JOINs), prefetch_related, and Prefetch(to_attr).
    """
    def __init__(self, model_cls: type, table_name: str):
        self.model_cls = model_cls
        self.table_name = table_name
        self._select_related_fields: Set[str] = set()
        self._prefetch_related_lookups: List[Union[str, Prefetch]] = []

    def select_related(self, *fields: str) -> OptimizedQuerySet:
        """Add single-valued relations to be fetched via SQL JOIN."""
        self._select_related_fields.update(fields)
        return self

    def prefetch_related(self, *lookups: Union[str, Prefetch]) -> OptimizedQuerySet:
        """Add multi-valued relations to be fetched via bulk secondary queries."""
        self._prefetch_related_lookups.extend(lookups)
        return self

    def execute(self) -> List[Order]:
        """
        Execute compilation and data retrieval pipeline:
        1. Compile primary query with SQL JOINs if select_related specified (1 Query).
        2. Execute prefetch queries for related collections (1 Query per lookup).
        3. Stitch related collections in Python memory using hash tables.
        """
        start = time.perf_counter()

        # Step 1: Execute primary query (with select_related joins if requested)
        has_joins = len(self._select_related_fields) > 0
        sql = f"SELECT * FROM {self.table_name}"
        if "customer" in self._select_related_fields:
            sql += " INNER JOIN customers ON (orders.customer_id = customers.id)"
        if "customer__organization" in self._select_related_fields:
            sql += " INNER JOIN organizations ON (customers.organization_id = organizations.id)"
        sql += ";"

        # Retrieve orders from mock storage
        raw_orders: List[Order] = list(TABLES[self.table_name].values())
        
        # Populate joined models on instances directly if select_related was used
        orders = []
        for o in raw_orders:
            order_copy = Order(
                id=o.id,
                customer_id=o.customer_id,
                total_amount=o.total_amount,
                status=o.status,
            )
            if "customer" in self._select_related_fields:
                cust = TABLES["customers"].get(o.customer_id)
                if cust and "customer__organization" in self._select_related_fields:
                    cust_copy = Customer(
                        id=cust.id,
                        name=cust.name,
                        organization_id=cust.organization_id,
                        organization=TABLES["organizations"].get(cust.organization_id),
                    )
                    order_copy.customer = cust_copy
                else:
                    order_copy.customer = cust
            orders.append(order_copy)

        duration = (time.perf_counter() - start) * 1000
        DB.record(sql, len(orders), duration)

        # Step 2: Execute prefetch queries for multi-valued relations
        if not orders:
            return orders

        order_ids = [o.id for o in orders]

        for lookup in self._prefetch_related_lookups:
            prefetch_obj = lookup if isinstance(lookup, Prefetch) else Prefetch(lookup)
            self._execute_prefetch(orders, order_ids, prefetch_obj)

        return orders

    def _execute_prefetch(self, orders: List[Order], order_ids: List[int], prefetch: Prefetch) -> None:
        """
        Executes a bulk child query using SQL 'IN' and stitches results into parent instances.
        """
        start = time.perf_counter()

        # 1. Bulk SQL Query for children: SELECT * FROM order_items WHERE order_id IN (...)
        id_list_str = ", ".join(str(i) for i in order_ids)
        sql = f"SELECT * FROM order_items WHERE order_id IN ({id_list_str});"

        all_items: List[OrderItem] = list(TABLES["order_items"].values())
        matching_items = [item for item in all_items if item.order_id in order_ids]

        duration = (time.perf_counter() - start) * 1000
        DB.record(sql, len(matching_items), duration)

        # 2. Python-level hash join: group items by parent order_id
        items_by_order_id: Dict[int, List[OrderItem]] = {}
        for item in matching_items:
            # Apply optional custom prefetch filter
            if prefetch.filter_fn is not None and not prefetch.filter_fn(item):
                continue
            items_by_order_id.setdefault(item.order_id, []).append(item)

        # 3. Stitch into order instances
        target_attr = prefetch.to_attr if prefetch.to_attr else prefetch.lookup
        for order in orders:
            assigned_items = items_by_order_id.get(order.id, [])
            setattr(order, target_attr, assigned_items)


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 35 Relational Optimization & Prefetch Suite...")
    DB.clear()
    TABLES["orders"].clear()
    TABLES["order_items"].clear()

    # Seed 50 orders with 4 items each (200 items total)
    item_id_counter = 1
    for o_id in range(1, 51):
        cust_id = 101 if o_id % 2 == 0 else 102
        TABLES["orders"][o_id] = Order(
            id=o_id,
            customer_id=cust_id,
            total_amount=decimal.Decimal(str(o_id * 15)),
            status="PAID",
        )
        for sub_id in range(4):
            TABLES["order_items"][item_id_counter] = OrderItem(
                id=item_id_counter,
                order_id=o_id,
                product_name=f"Product-{sub_id}",
                price=decimal.Decimal("25.00"),
                is_active=(sub_id % 2 == 0),  # 2 active, 2 inactive
            )
            item_id_counter += 1

    print("  [+] Seeded 50 orders with 200 order items.")

    # 1. Test select_related (Deep join: order -> customer -> organization)
    # MUST EXECUTE IN EXACTLY 1 QUERY!
    with assert_num_queries(1):
        qs = OptimizedQuerySet(Order, "orders").select_related("customer", "customer__organization")
        orders = qs.execute()

    assert len(orders) == 50
    first_order = orders[0]
    assert first_order.customer is not None
    assert first_order.customer.organization is not None
    assert first_order.customer.organization.name in ("Enterprise SaaS Corp", "FinTech Global")
    print("  [+] select_related deep 2-level join verified (1 query).")

    # 2. Test prefetch_related with Prefetch(to_attr=...)
    # Query 1: Orders + joined customers via select_related
    # Query 2: Active order items via bulk IN query
    # TOTAL QUERIES: EXACTLY 2!
    with assert_num_queries(2):
        qs = (
            OptimizedQuerySet(Order, "orders")
            .select_related("customer")
            .prefetch_related(
                Prefetch(
                    lookup="items",
                    filter_fn=lambda item: item.is_active is True,
                    to_attr="active_items",
                )
            )
        )
        orders = qs.execute()

        # Iterate and verify in-memory zero-query access
        total_active_items = 0
        for o in orders:
            assert o.customer is not None  # 0 SQL queries
            assert len(o.active_items) == 2  # Only active items prefetched! (0 SQL queries)
            total_active_items += len(o.active_items)

    assert total_active_items == 100  # 50 orders * 2 active items
    print("  [+] prefetch_related with Prefetch(to_attr) verified (exactly 2 queries).")

    # 3. Test assert_num_queries Trapping N+1 Regressions
    try:
        with assert_num_queries(1):
            # Issues 2 queries when 1 was budgeted
            _ = (
                OptimizedQuerySet(Order, "orders")
                .select_related("customer")
                .prefetch_related("items")
                .execute()
            )
        assert False, "Should have trapped budget violation"
    except QueryBudgetExceeded:
        pass
    print("  [+] assert_num_queries successfully trapped query budget violation.")


def benchmark_optimization_speedup(num_orders: int = 1_000) -> None:
    """Benchmark in-memory hash join throughput in prefetch_related."""
    TABLES["orders"].clear()
    TABLES["order_items"].clear()

    # Seed 1,000 orders with 5 items each (5,000 items)
    item_counter = 1
    for i in range(num_orders):
        TABLES["orders"][i] = Order(
            id=i,
            customer_id=101,
            total_amount=decimal.Decimal("100.00"),
            status="PAID",
        )
        for k in range(5):
            TABLES["order_items"][item_counter] = OrderItem(
                id=item_counter,
                order_id=i,
                product_name=f"SKU-{k}",
                price=decimal.Decimal("20.00"),
                is_active=True,
            )
            item_counter += 1

    start_time = time.perf_counter()
    iterations = 100
    for _ in range(iterations):
        _ = (
            OptimizedQuerySet(Order, "orders")
            .select_related("customer")
            .prefetch_related(
                Prefetch("items", filter_fn=lambda it: it.is_active, to_attr="active_items")
            )
            .execute()
        )
    elapsed = time.perf_counter() - start_time

    latency_ms = (elapsed / iterations) * 1000
    ops_per_sec = iterations / elapsed

    print("\n" + "=" * 65)
    print("DAY 35: SELECT_RELATED + PREFETCH_RELATED BENCHMARK")
    print("=" * 65)
    print(f"Parent Orders:             {num_orders:,} rows")
    print(f"Prefetched Children:       5,000 line items")
    print(f"Total Iterations:          {iterations:,}")
    print(f"Total Time:                {elapsed:.4f} seconds")
    print(f"Average Execution Latency: {latency_ms:.2f} ms per 2-query graph load")
    print(f"Throughput:                {ops_per_sec:,.0f} relational graph loads/sec")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_optimization_speedup()
