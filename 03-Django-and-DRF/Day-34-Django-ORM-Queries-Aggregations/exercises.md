# Day 34: Django ORM Queries, Filtering & Aggregation Exercises

Three hands-on engineering challenges testing your mastery of dynamic `Q` object composition, single-pass conditional aggregations, and automated query count regression auditing.

---

## Exercise 1: Dynamic Multi-Term Search Filter Engine with `Q` Objects

### Problem Statement:
In enterprise search systems, users enter multi-word queries, optional status filters, and price ranges. Hardcoding `.filter(title__icontains=...)` breaks when search terms vary in number.

Build a `DynamicFilterBuilder` that:
1. Takes a list of search keywords (e.g. `["laptop", "pro"]`) and combines them with SQL `OR` (`|`) across multiple fields (`title` and `description`).
2. Takes an optional list of allowed statuses (e.g. `["IN_STOCK", "BACKORDER"]`) and filters with SQL `IN`.
3. Excludes items where `is_archived = True` using SQL `NOT` (`~Q`).
4. Evaluates the composed `Q` tree against in-memory records and returns matching items.

### Constraints:
- Pure Python standard library (`functools.reduce`, `operator.or_`).
- Maintain immutability and handle empty term lists gracefully.

### Complete Solution:

```python
"""
Exercise 1: Dynamic Multi-Term Search Filter Engine with Q Objects
"""

from __future__ import annotations

import functools
import operator
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


@dataclass
class SearchCriteria:
    keywords: List[str]
    allowed_statuses: Optional[List[str]] = None
    exclude_archived: bool = True
    min_price: Optional[float] = None
    max_price: Optional[float] = None


class Predicate:
    """Represents a boolean evaluation node."""
    def __init__(self, fn: Callable[[Dict[str, Any]], bool]):
        self.fn = fn

    def __call__(self, record: Dict[str, Any]) -> bool:
        return self.fn(record)

    def __and__(self, other: Predicate) -> Predicate:
        return Predicate(lambda r: self(r) and other(r))

    def __or__(self, other: Predicate) -> Predicate:
        return Predicate(lambda r: self(r) or other(r))

    def __invert__(self) -> Predicate:
        return Predicate(lambda r: not self(r))


class DynamicFilterBuilder:
    """Compiles complex search criteria into an executable predicate tree."""
    
    @classmethod
    def build_predicate(cls, criteria: SearchCriteria) -> Predicate:
        predicates: List[Predicate] = []

        # 1. Keywords: (title CONTAINS k OR desc CONTAINS k) for each keyword
        if criteria.keywords:
            keyword_predicates = []
            for term in criteria.keywords:
                term_lower = term.lower()
                p = Predicate(
                    lambda r, t=term_lower: t in r.get("title", "").lower() or t in r.get("description", "").lower()
                )
                keyword_predicates.append(p)
            
            # Combine all keyword predicates with OR
            combined_keywords = functools.reduce(operator.or_, keyword_predicates)
            predicates.append(combined_keywords)

        # 2. Status IN
        if criteria.allowed_statuses is not None:
            status_set = set(criteria.allowed_statuses)
            predicates.append(Predicate(lambda r: r.get("status") in status_set))

        # 3. Exclude Archived (~Q(is_archived=True))
        if criteria.exclude_archived:
            predicates.append(~Predicate(lambda r: bool(r.get("is_archived", False))))

        # 4. Price Ranges
        if criteria.min_price is not None:
            predicates.append(Predicate(lambda r: r.get("price", 0) >= criteria.min_price))
        if criteria.max_price is not None:
            predicates.append(Predicate(lambda r: r.get("price", 0) <= criteria.max_price))

        if not predicates:
            return Predicate(lambda r: True)

        # Combine all conditions with AND
        return functools.reduce(operator.and_, predicates)

    @classmethod
    def filter_records(cls, records: List[Dict[str, Any]], criteria: SearchCriteria) -> List[Dict[str, Any]]:
        predicate = cls.build_predicate(criteria)
        return [r for r in records if predicate(r)]


def test_dynamic_filter() -> None:
    print("[*] Testing Exercise 1: Dynamic Q Filter Builder...")

    catalog = [
        {"id": 1, "title": "Apple MacBook Pro 16", "description": "High performance laptop", "status": "IN_STOCK", "price": 2499.00, "is_archived": False},
        {"id": 2, "title": "Dell XPS 15 Laptop", "description": "InfinityEdge screen", "status": "IN_STOCK", "price": 1899.00, "is_archived": False},
        {"id": 3, "title": "Logitech MX Master 3", "description": "Ergonomic wireless mouse", "status": "IN_STOCK", "price": 99.00, "is_archived": False},
        {"id": 4, "title": "Old Laptop Prototype", "description": "Decommissioned pro unit", "status": "DISCONTINUED", "price": 500.00, "is_archived": True},
        {"id": 5, "title": "Lenovo ThinkPad Pro", "description": "Business laptop", "status": "BACKORDER", "price": 1400.00, "is_archived": False},
    ]

    # Search for ("laptop" OR "pro") in IN_STOCK or BACKORDER, not archived, price between 1000 and 2000
    criteria = SearchCriteria(
        keywords=["laptop", "pro"],
        allowed_statuses=["IN_STOCK", "BACKORDER"],
        exclude_archived=True,
        min_price=1000.00,
        max_price=2000.00,
    )

    results = DynamicFilterBuilder.filter_records(catalog, criteria)
    matched_ids = {r["id"] for r in results}

    # ID 2 (Dell XPS 15: 1899, IN_STOCK, matches 'laptop')
    # ID 5 (Lenovo ThinkPad: 1400, BACKORDER, matches 'laptop' and 'pro')
    # ID 1 is excluded because price > 2000
    # ID 4 is excluded because is_archived = True
    assert matched_ids == {2, 5}
    print("  [+] Dynamic multi-term Q-filter tree passed all assertions.")


if __name__ == "__main__":
    test_dynamic_filter()
```

---

## Exercise 2: Single-Pass Multi-Category Financial Metrics Aggregator

### Problem Statement:
Executive dashboards require key performance indicators (KPIs) calculated across different slices of transactions (e.g. gross revenue from paid orders, refund volume, distinct customer count, net revenue). Running separate queries for each metric exhausts database connection limits.

Build a financial aggregation engine `FinancialAggregator.compute_dashboard_metrics(records)` that computes in a **single pass**:
1. `gross_revenue`: Sum of `amount` where `status == 'PAID'`.
2. `refund_volume`: Sum of `amount` where `status == 'REFUNDED'`.
3. `net_revenue`: `gross_revenue - refund_volume`.
4. `paid_order_count`: Count of orders with `status == 'PAID'`.
5. `unique_paying_customers`: Distinct count of `customer_id` among `PAID` orders.
6. `average_order_value`: `gross_revenue / paid_order_count` (or 0 if no paid orders).

### Complete Solution:

```python
"""
Exercise 2: Single-Pass Multi-Category Financial Metrics Aggregator
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import Any, Dict, List, Set


@dataclass(frozen=True)
class DashboardKPIs:
    gross_revenue: decimal.Decimal
    refund_volume: decimal.Decimal
    net_revenue: decimal.Decimal
    paid_order_count: int
    unique_paying_customers: int
    average_order_value: decimal.Decimal


class FinancialAggregator:
    """Computes executive financial KPIs in a single traversal."""
    
    @classmethod
    def compute_dashboard_metrics(cls, records: List[Dict[str, Any]]) -> DashboardKPIs:
        gross_revenue = decimal.Decimal("0.00")
        refund_volume = decimal.Decimal("0.00")
        paid_count = 0
        paying_customers: Set[Any] = set()

        for r in records:
            status = r.get("status")
            amount = decimal.Decimal(str(r.get("amount", "0.00")))
            customer = r.get("customer_id")

            if status == "PAID":
                gross_revenue += amount
                paid_count += 1
                if customer is not None:
                    paying_customers.add(customer)
            elif status == "REFUNDED":
                refund_volume += amount

        net_revenue = gross_revenue - refund_volume
        aov = (gross_revenue / paid_count) if paid_count > 0 else decimal.Decimal("0.00")

        return DashboardKPIs(
            gross_revenue=gross_revenue,
            refund_volume=refund_volume,
            net_revenue=net_revenue,
            paid_order_count=paid_count,
            unique_paying_customers=len(paying_customers),
            average_order_value=aov.quantize(decimal.Decimal("0.01")),
        )


def test_financial_aggregator() -> None:
    print("[*] Testing Exercise 2: Financial Aggregator...")

    transactions = [
        {"id": 1, "customer_id": "CUST-1", "amount": "100.00", "status": "PAID"},
        {"id": 2, "customer_id": "CUST-2", "amount": "50.00", "status": "PAID"},
        {"id": 3, "customer_id": "CUST-1", "amount": "150.00", "status": "PAID"},  # Repeat customer
        {"id": 4, "customer_id": "CUST-3", "amount": "40.00", "status": "REFUNDED"},
        {"id": 5, "customer_id": "CUST-4", "amount": "20.00", "status": "PENDING"},
        {"id": 6, "customer_id": "CUST-5", "amount": "10.00", "status": "FAILED"},
    ]

    kpis = FinancialAggregator.compute_dashboard_metrics(transactions)

    assert kpis.gross_revenue == decimal.Decimal("300.00")
    assert kpis.refund_volume == decimal.Decimal("40.00")
    assert kpis.net_revenue == decimal.Decimal("260.00")
    assert kpis.paid_order_count == 3
    assert kpis.unique_paying_customers == 2  # CUST-1 and CUST-2
    assert kpis.average_order_value == decimal.Decimal("100.00")  # 300 / 3

    print("  [+] Single-pass multi-category financial metrics verified.")


if __name__ == "__main__":
    test_financial_aggregator()
```

---

## Exercise 3: Automated Query Count Regression Detector & Assertor

### Problem Statement:
Continuous Integration (CI) test suites must protect against N+1 query regressions.
Implement `QueryBudgetManager`, a context manager that:
1. Accepts a `budget: int` representing maximum allowed database queries.
2. Intercepts and counts database queries executed within its block.
3. If executed queries exceed the budget, raises `QueryBudgetExceededError` detailing the budget, actual queries, and SQL log.
4. If executed queries are $\le$ budget, succeeds and records execution time.

### Complete Solution:

```python
"""
Exercise 3: Automated Query Count Regression Detector & Assertor
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class QueryBudgetExceededError(AssertionError):
    pass


class MockDatabaseConnection:
    """Simulates a database driver tracking query executions."""
    def __init__(self):
        self.queries: List[Dict[str, Any]] = []

    def execute(self, sql: str) -> None:
        self.queries.append({"sql": sql, "timestamp": time.time()})

    def clear(self) -> None:
        self.queries.clear()


DB = MockDatabaseConnection()


class QueryBudgetManager:
    """Context manager asserting database query budgets."""
    def __init__(self, budget: int, db: MockDatabaseConnection = DB):
        self.budget = budget
        self.db = db
        self.start_index: int = 0
        self.executed_count: int = 0

    def __enter__(self) -> QueryBudgetManager:
        self.start_index = len(self.db.queries)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            return  # Allow inner exception to propagate

        self.executed_count = len(self.db.queries) - self.start_index
        if self.executed_count > self.budget:
            executed_sqls = [q["sql"] for q in self.db.queries[self.start_index:]]
            raise QueryBudgetExceededError(
                f"Query budget exceeded! Allowed: {self.budget}, Executed: {self.executed_count}.\n"
                f"Executed SQL statements:\n" + "\n".join(f"  [{i+1}] {s}" for i, s in enumerate(executed_sqls))
            )


def test_query_budget_manager() -> None:
    print("[*] Testing Exercise 3: Query Budget Manager...")
    DB.clear()

    # Case 1: Within budget -> Passes cleanly
    with QueryBudgetManager(budget=2) as mgr:
        DB.execute("SELECT * FROM users WHERE id = 1;")
        DB.execute("SELECT * FROM profiles WHERE user_id = 1;")
    assert mgr.executed_count == 2
    print("  [+] Within budget execution passed.")

    # Case 2: Exceeding budget (simulated N+1 loop) -> Raises QueryBudgetExceededError
    try:
        with QueryBudgetManager(budget=2):
            DB.execute("SELECT * FROM orders;")
            # Simulated N+1 query loop
            DB.execute("SELECT * FROM items WHERE order_id = 1;")
            DB.execute("SELECT * FROM items WHERE order_id = 2;")
            DB.execute("SELECT * FROM items WHERE order_id = 3;")
        assert False, "Should have raised QueryBudgetExceededError"
    except QueryBudgetExceededError as exc:
        assert "Allowed: 2, Executed: 4" in str(exc)
    print("  [+] QueryBudgetExceededError caught N+1 query regression.")


if __name__ == "__main__":
    test_query_budget_manager()
```
