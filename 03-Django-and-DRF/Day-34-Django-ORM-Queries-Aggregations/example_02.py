r"""
Day 34: Example 02 - Production Aggregations, Annotations & Query Count Auditor
================================================================================

This module implements an enterprise-grade simulation of Django's aggregation engine,
row annotations (`annotate`), conditional expressions (`Case`, `When`), correlated
subqueries (`Subquery`, `OuterRef`), and a query count auditor (`assert_num_queries`).

WHY THIS ARCHITECTURE MATTERS:
1. Database-Level Compute: Performing aggregations (sums, averages, counts) in Python
   by iterating over thousands of rows wastes network bandwidth and memory. Django's
   `aggregate()` and `annotate()` compile calculations into SQL `GROUP BY` and aggregate
   functions, executing them directly inside PostgreSQL's high-speed C engine.
2. Single-Pass Conditional Aggregation: Using `Sum('amount', filter=Q(status='PAID'))`
   allows computing multiple metrics across different subsets of data in a single SQL
   query, replacing 5 separate roundtrips with 1.
3. Case/When Column Projections: Dynamically categorizing rows (e.g. VIP tiers, risk scores)
   via SQL `CASE WHEN` prevents costly post-processing in Python serializers.
4. Continuous Query Count Auditing: The number one cause of production latency spikes
   is hidden N+1 queries. Wrapping critical API flows in a query count auditor
   (`assert_num_queries`) guarantees that queries remain constant regardless of data volume.
"""

from __future__ import annotations

import decimal
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union


# -----------------------------------------------------------------------------
# Query Count Auditing Architecture
# -----------------------------------------------------------------------------

class QueryLogger:
    """Tracks and logs all SQL queries executed against simulated database tables."""
    def __init__(self):
        self.queries: List[Dict[str, Any]] = []

    def record(self, sql: str, duration_ms: float) -> None:
        self.queries.append({"sql": sql, "duration_ms": duration_ms})

    def clear(self) -> None:
        self.queries.clear()

    @property
    def count(self) -> int:
        return len(self.queries)


# Global query logger singleton
GLOBAL_QUERY_LOGGER = QueryLogger()


class QueryCountAssertionError(AssertionError):
    """Raised when executed queries do not match expected query budget."""
    pass


@contextmanager
def assert_num_queries(expected_count: int) -> Iterator[QueryLogger]:
    """
    Context manager that asserts the exact number of database queries executed.
    Mirrors `django.test.TestCase.assertNumQueries`.
    
    WHY: Catches N+1 query regressions in CI/CD pipelines before code is deployed.
    """
    start_count = GLOBAL_QUERY_LOGGER.count
    try:
        yield GLOBAL_QUERY_LOGGER
    finally:
        total_executed = GLOBAL_QUERY_LOGGER.count - start_count
        if total_executed != expected_count:
            executed_sqls = [
                q["sql"] for q in GLOBAL_QUERY_LOGGER.queries[start_count:]
            ]
            raise QueryCountAssertionError(
                f"Expected {expected_count} queries, but executed {total_executed} queries.\n"
                f"Executed SQL statements:\n" + "\n".join(f"  [{i+1}] {s}" for i, s in enumerate(executed_sqls))
            )


# -----------------------------------------------------------------------------
# Aggregation & Annotation Expressions (Sum, Avg, Count, Case, When)
# -----------------------------------------------------------------------------

class AggregateExpression:
    """Base class for SQL aggregate functions."""
    def __init__(self, field_name: str, filter_condition: Optional[Callable[[Dict[str, Any]], bool]] = None):
        self.field_name = field_name
        self.filter_condition = filter_condition

    def compute(self, rows: List[Dict[str, Any]]) -> Any:
        raise NotImplementedError


class Sum(AggregateExpression):
    """Computes SQL SUM(field) over matching rows."""
    def compute(self, rows: List[Dict[str, Any]]) -> decimal.Decimal:
        total = decimal.Decimal("0.00")
        for r in rows:
            if self.filter_condition is None or self.filter_condition(r):
                val = r.get(self.field_name)
                if val is not None:
                    total += decimal.Decimal(str(val))
        return total


class Count(AggregateExpression):
    """Computes SQL COUNT(field) with distinct support."""
    def __init__(
        self,
        field_name: str,
        distinct: bool = False,
        filter_condition: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ):
        super().__init__(field_name, filter_condition)
        self.distinct = distinct

    def compute(self, rows: List[Dict[str, Any]]) -> int:
        filtered_rows = [
            r for r in rows
            if self.filter_condition is None or self.filter_condition(r)
        ]
        values = [r.get(self.field_name) for r in filtered_rows if r.get(self.field_name) is not None]
        if self.distinct:
            return len(set(values))
        return len(values)


class Avg(AggregateExpression):
    """Computes SQL AVG(field)."""
    def compute(self, rows: List[Dict[str, Any]]) -> Optional[decimal.Decimal]:
        filtered_rows = [
            r for r in rows
            if self.filter_condition is None or self.filter_condition(r)
        ]
        values = [decimal.Decimal(str(r.get(self.field_name))) for r in filtered_rows if r.get(self.field_name) is not None]
        if not values:
            return None
        return sum(values) / len(values)


class When:
    """Represents a WHEN condition THEN value clause in SQL CASE expressions."""
    def __init__(self, condition: Callable[[Dict[str, Any]], bool], then: Any):
        self.condition = condition
        self.then = then


class Case:
    """
    Evaluates SQL CASE WHEN ... THEN ... ELSE ... END conditional expressions.
    Mirrors `django.db.models.Case`.
    """
    def __init__(self, *when_clauses: When, default: Any = None):
        self.when_clauses = list(when_clauses)
        self.default = default

    def resolve(self, record: Dict[str, Any]) -> Any:
        for when in self.when_clauses:
            if when.condition(record):
                return when.then
        return self.default


# -----------------------------------------------------------------------------
# Enterprise Table & Reporting Engine
# -----------------------------------------------------------------------------

class EnterpriseReportingTable:
    """
    Simulates a high-throughput reporting table supporting single-pass
    aggregations, annotations, and correlated subqueries with SQL telemetry.
    """
    def __init__(self, table_name: str, records: List[Dict[str, Any]]):
        self.table_name = table_name
        self.records = records

    def aggregate(self, **aggregates: AggregateExpression) -> Dict[str, Any]:
        """
        Executes SQL table aggregation in a single query.
        Returns a scalar dictionary summarizing the dataset.
        """
        start = time.perf_counter()
        results: Dict[str, Any] = {}

        for alias, agg in aggregates.items():
            results[alias] = agg.compute(self.records)

        duration = (time.perf_counter() - start) * 1000
        GLOBAL_QUERY_LOGGER.record(
            f"SELECT {', '.join(f'{type(a).__name__}({a.field_name}) AS {k}' for k, a in aggregates.items())} FROM {self.table_name};",
            duration,
        )
        return results

    def annotate(self, **annotations: Union[Case, Callable[[Dict[str, Any]], Any]]) -> List[Dict[str, Any]]:
        """
        Enriches each row with computed annotation columns (SQL CASE or Subquery).
        """
        start = time.perf_counter()
        annotated_records: List[Dict[str, Any]] = []

        for row in self.records:
            row_copy = row.copy()
            for alias, expr in annotations.items():
                if isinstance(expr, Case):
                    row_copy[alias] = expr.resolve(row)
                elif callable(expr):
                    row_copy[alias] = expr(row)
                else:
                    row_copy[alias] = expr
            annotated_records.append(row_copy)

        duration = (time.perf_counter() - start) * 1000
        GLOBAL_QUERY_LOGGER.record(
            f"SELECT *, <{len(annotations)} ANNOTATED COLUMNS> FROM {self.table_name};",
            duration,
        )
        return annotated_records


# -----------------------------------------------------------------------------
# Correlated Subquery Simulation (Subquery / OuterRef)
# -----------------------------------------------------------------------------

class SubqueryEngine:
    """
    Simulates Django's `Subquery(Child.objects.filter(parent_id=OuterRef('pk'))[:1])`.
    Pulls a scalar value from a child table without generating expensive Cartesian joins.
    """
    @staticmethod
    def get_latest_scalar(
        child_records: List[Dict[str, Any]],
        foreign_key_col: str,
        outer_id_val: Any,
        target_col: str,
        order_by_col: str,
    ) -> Any:
        matched = [r for r in child_records if r.get(foreign_key_col) == outer_id_val]
        if not matched:
            return None
        # Sort descending by order_by_col
        matched.sort(key=lambda r: r.get(order_by_col), reverse=True)
        return matched[0].get(target_col)


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 34 Aggregations & Query Count Auditor Verification Suite...")
    GLOBAL_QUERY_LOGGER.clear()

    # Seed data: Orders
    orders_data = [
        {"id": 1, "customer_id": 101, "amount": decimal.Decimal("250.00"), "status": "PAID"},
        {"id": 2, "customer_id": 102, "amount": decimal.Decimal("50.00"), "status": "PAID"},
        {"id": 3, "customer_id": 101, "amount": decimal.Decimal("100.00"), "status": "REFUNDED"},
        {"id": 4, "customer_id": 103, "amount": decimal.Decimal("450.00"), "status": "PAID"},
        {"id": 5, "customer_id": 102, "amount": decimal.Decimal("75.00"), "status": "PENDING"},
        {"id": 6, "customer_id": 104, "amount": decimal.Decimal("1200.00"), "status": "PAID"},
    ]
    orders_table = EnterpriseReportingTable("orders", orders_data)

    # 1. Test Single-Pass Conditional Aggregations
    # Compute paid sum, refund sum, and unique customer count in EXACTLY 1 QUERY!
    with assert_num_queries(1):
        metrics = orders_table.aggregate(
            total_paid=Sum("amount", filter_condition=lambda r: r["status"] == "PAID"),
            total_refunded=Sum("amount", filter_condition=lambda r: r["status"] == "REFUNDED"),
            unique_customers=Count("customer_id", distinct=True),
            avg_order=Avg("amount"),
        )

    assert metrics["total_paid"] == decimal.Decimal("1950.00")
    assert metrics["total_refunded"] == decimal.Decimal("100.00")
    assert metrics["unique_customers"] == 4
    assert metrics["avg_order"] == decimal.Decimal("354.1666666666666666666666667")
    print("  [+] Single-pass multi-metric conditional aggregation verified.")

    # 2. Test Case / When Row Annotations (VIP Customer Categorization)
    customers_data = [
        {"id": 101, "name": "Alice", "lifetime_spend": decimal.Decimal("350.00")},
        {"id": 102, "name": "Bob", "lifetime_spend": decimal.Decimal("125.00")},
        {"id": 103, "name": "Charlie", "lifetime_spend": decimal.Decimal("450.00")},
        {"id": 104, "name": "Diana", "lifetime_spend": decimal.Decimal("5200.00")},
    ]
    customers_table = EnterpriseReportingTable("customers", customers_data)

    tier_case = Case(
        When(condition=lambda r: r["lifetime_spend"] >= decimal.Decimal("5000.00"), then="PLATINUM"),
        When(condition=lambda r: r["lifetime_spend"] >= decimal.Decimal("300.00"), then="GOLD"),
        default="STANDARD",
    )

    with assert_num_queries(1):
        annotated_customers = customers_table.annotate(vip_tier=tier_case)

    tiers_by_name = {c["name"]: c["vip_tier"] for c in annotated_customers}
    assert tiers_by_name["Diana"] == "PLATINUM"
    assert tiers_by_name["Alice"] == "GOLD"
    assert tiers_by_name["Charlie"] == "GOLD"
    assert tiers_by_name["Bob"] == "STANDARD"
    print("  [+] Case/When conditional row annotations verified.")

    # 3. Test Correlated Subquery Annotation
    # Annotate each customer with their latest order amount without cartesian joins
    notes = [
        {"id": 1, "customer_id": 101, "created_at": 10, "note": "First order"},
        {"id": 2, "customer_id": 101, "created_at": 20, "note": "Latest order for Alice"},
        {"id": 3, "customer_id": 102, "created_at": 15, "note": "Only order for Bob"},
    ]

    with assert_num_queries(1):
        customers_with_notes = customers_table.annotate(
            latest_note=lambda c: SubqueryEngine.get_latest_scalar(
                child_records=notes,
                foreign_key_col="customer_id",
                outer_id_val=c["id"],
                target_col="note",
                order_by_col="created_at",
            )
        )

    alice_rec = next(c for c in customers_with_notes if c["name"] == "Alice")
    assert alice_rec["latest_note"] == "Latest order for Alice"
    diana_rec = next(c for c in customers_with_notes if c["name"] == "Diana")
    assert diana_rec["latest_note"] is None
    print("  [+] Correlated Subquery simulation verified.")

    # 4. Test assert_num_queries Regression Detection
    try:
        with assert_num_queries(1):
            # Simulate an N+1 query loop: executing 3 queries when 1 was expected
            orders_table.aggregate(total=Sum("amount"))
            orders_table.aggregate(total=Sum("amount"))
            orders_table.aggregate(total=Sum("amount"))
        assert False, "Should have caught N+1 query regression"
    except QueryCountAssertionError:
        pass
    print("  [+] assert_num_queries successfully trapped N+1 query regression.")


def benchmark_aggregation_engine(num_records: int = 10_000) -> None:
    """Benchmark single-pass aggregation execution throughput."""
    mock_records = [
        {
            "id": i,
            "amount": decimal.Decimal(str((i % 500) + 10.50)),
            "status": "PAID" if i % 2 == 0 else "REFUNDED",
            "customer_id": (i % 200) + 1,
        }
        for i in range(num_records)
    ]
    table = EnterpriseReportingTable("bench_orders", mock_records)

    start_time = time.perf_counter()
    iterations = 200
    for _ in range(iterations):
        _ = table.aggregate(
            paid_sum=Sum("amount", filter_condition=lambda r: r["status"] == "PAID"),
            refund_sum=Sum("amount", filter_condition=lambda r: r["status"] == "REFUNDED"),
            customer_count=Count("customer_id", distinct=True),
        )
    elapsed = time.perf_counter() - start_time

    latency_ms = (elapsed / iterations) * 1000
    ops_per_sec = iterations / elapsed

    print("\n" + "=" * 65)
    print("DAY 34: SINGLE-PASS AGGREGATION BENCHMARK")
    print("=" * 65)
    print(f"Dataset Size:          {num_records:,} rows")
    print(f"Iterations:            {iterations:,}")
    print(f"Total Time:            {elapsed:.4f} seconds")
    print(f"Average Latency:       {latency_ms:.2f} ms per multi-metric aggregate")
    print(f"Throughput:            {ops_per_sec:,.0f} aggregate computations/sec")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_aggregation_engine()
