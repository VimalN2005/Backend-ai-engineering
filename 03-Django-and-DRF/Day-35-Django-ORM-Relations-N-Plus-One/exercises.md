# Day 35: ORM Relations & N+1 Optimization Exercises

Three hands-on engineering challenges testing your mastery of SQL `JOIN` simulation (`select_related`), bulk two-query hash stitching (`prefetch_related` with `to_attr`), and automated runtime N+1 query regression detection.

---

## Exercise 1: In-Memory `select_related` SQL Join Compiler & Hydrator

### Problem Statement:
When `select_related("customer")` is invoked, Django compiles an SQL `INNER JOIN` (or `LEFT OUTER JOIN`), queries both tables simultaneously, and "hydrates" both parent and child model instances from a single result row.

Implement a pure-Python relational hydrator `join_and_hydrate(orders, customers, foreign_key_col, target_attr)` that:
1. Emulates an SQL `INNER JOIN` in $\mathcal{O}(N)$ time using an in-memory dictionary index on the target primary key.
2. Instantiates `Order` objects and sets `order.customer = customer_instance`.
3. Ensures that accessing `order.customer` requires zero subsequent database queries.

### Constraints:
- Pure Python standard library.
- $\mathcal{O}(N + M)$ time complexity (no nested loops).
- Correctly handle missing foreign keys (omit row if inner join).

### Complete Solution:

```python
"""
Exercise 1: In-Memory select_related SQL Join Compiler & Hydrator
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Customer:
    id: int
    name: str
    email: str


@dataclass
class Order:
    id: int
    customer_id: int
    total_amount: float
    customer: Optional[Customer] = None


def join_and_hydrate(
    raw_orders: List[Dict[str, Any]],
    raw_customers: List[Dict[str, Any]],
) -> List[Order]:
    """
    Simulates Django's select_related SQL INNER JOIN and model hydration.
    Complexity: O(len(raw_customers) + len(raw_orders)).
    """
    # 1. Build hash table for target model (O(M))
    customer_index: Dict[int, Customer] = {
        c["id"]: Customer(**c) for c in raw_customers
    }

    # 2. Iterate orders and hydrate customer in a single pass (O(N))
    hydrated_orders: List[Order] = []
    for o_data in raw_orders:
        c_id = o_data.get("customer_id")
        # INNER JOIN: Only include if matching customer exists
        if c_id in customer_index:
            customer_inst = customer_index[c_id]
            order_inst = Order(
                id=o_data["id"],
                customer_id=c_id,
                total_amount=o_data["total_amount"],
                customer=customer_inst,
            )
            hydrated_orders.append(order_inst)

    return hydrated_orders


def test_select_related_hydrator() -> None:
    print("[*] Testing Exercise 1: select_related Join Hydrator...")

    raw_customers = [
        {"id": 101, "name": "Alice Corp", "email": "alice@corp.com"},
        {"id": 102, "name": "Bob LLC", "email": "bob@llc.com"},
    ]
    raw_orders = [
        {"id": 1, "customer_id": 101, "total_amount": 150.0},
        {"id": 2, "customer_id": 102, "total_amount": 75.0},
        {"id": 3, "customer_id": 101, "total_amount": 320.0},
        {"id": 4, "customer_id": 999, "total_amount": 50.0},  # Non-existent customer
    ]

    orders = join_and_hydrate(raw_orders, raw_customers)

    # Verify length (ID 4 excluded due to INNER JOIN)
    assert len(orders) == 3

    # Verify deep attribute access with 0 extra queries
    o1 = orders[0]
    assert o1.customer is not None
    assert o1.customer.name == "Alice Corp"

    o3 = orders[2]
    assert o3.customer is not None
    assert o3.customer.name == "Alice Corp"
    # Same customer instance is shared in memory (identity check)
    assert o1.customer is o3.customer

    print("  [+] select_related O(N+M) join and instance hydration verified.")


if __name__ == "__main__":
    test_select_related_hydrator()
```

---

## Exercise 2: `prefetch_related` Bulk Two-Query Hash Join Engine with `to_attr`

### Problem Statement:
Multi-valued relations (e.g. `Order` to `OrderItem`) cannot use single-query SQL joins without Cartesian row multiplication. Django solves this by fetching children in a second bulk query and stitching them in Python.

Build `prefetch_and_stitch(parents, child_fetcher, foreign_key_attr, to_attr, filter_fn)` that:
1. Collects all parent primary keys: `parent_ids = [p.id for p in parents]`.
2. Invokes `child_fetcher(parent_ids)` to retrieve children in bulk (simulating `WHERE parent_id IN (...)`).
3. Applies `filter_fn(child)` if provided.
4. Groups matching children by `foreign_key_attr` using an in-memory dictionary.
5. Attaches the list of matching children to each parent instance under attribute `to_attr`.

### Complete Solution:

```python
"""
Exercise 2: prefetch_related Bulk Two-Query Hash Join Engine with to_attr
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class OrderItem:
    id: int
    order_id: int
    sku: str
    price: float
    is_refunded: bool = False


@dataclass
class Order:
    id: int
    total_amount: float
    active_items: List[OrderItem] = field(default_factory=list)


def prefetch_and_stitch(
    parents: List[Order],
    child_fetcher: Callable[[List[int]], List[OrderItem]],
    foreign_key_attr: str,
    to_attr: str,
    filter_fn: Optional[Callable[[OrderItem], bool]] = None,
) -> None:
    """
    Simulates Django's prefetch_related with Prefetch(..., to_attr=...).
    """
    if not parents:
        return

    # Step 1: Collect parent IDs
    parent_ids = [p.id for p in parents]

    # Step 2: Fetch related children in bulk (Simulates Query 2: SELECT ... WHERE order_id IN (...))
    raw_children = child_fetcher(parent_ids)

    # Step 3: Hash group by foreign key attribute in O(K)
    children_by_parent: Dict[int, List[OrderItem]] = {}
    for child in raw_children:
        if filter_fn is not None and not filter_fn(child):
            continue  # Exclude child based on custom filter
        
        fk_val = getattr(child, foreign_key_attr)
        children_by_parent.setdefault(fk_val, []).append(child)

    # Step 4: Stitch into parents in O(N)
    for parent in parents:
        matched_items = children_by_parent.get(parent.id, [])
        setattr(parent, to_attr, matched_items)


def test_prefetch_stitcher() -> None:
    print("[*] Testing Exercise 2: prefetch_related Stitcher...")

    orders = [
        Order(id=1, total_amount=100.0),
        Order(id=2, total_amount=200.0),
        Order(id=3, total_amount=50.0),  # Has no items
    ]

    all_items_db = [
        OrderItem(id=1, order_id=1, sku="A1", price=40.0, is_refunded=False),
        OrderItem(id=2, order_id=1, sku="A2", price=60.0, is_refunded=True),   # Refunded!
        OrderItem(id=3, order_id=2, sku="B1", price=200.0, is_refunded=False),
    ]

    query_count = 0

    def mock_child_fetcher(order_ids: List[int]) -> List[OrderItem]:
        nonlocal query_count
        query_count += 1
        return [item for item in all_items_db if item.order_id in order_ids]

    # Execute prefetch filtering out refunded items:
    prefetch_and_stitch(
        parents=orders,
        child_fetcher=mock_child_fetcher,
        foreign_key_attr="order_id",
        to_attr="active_items",
        filter_fn=lambda item: not item.is_refunded,
    )

    # Exactly 1 bulk query was executed for all child items
    assert query_count == 1

    # Order 1: has 2 items, but only 1 is active (non-refunded)
    assert len(orders[0].active_items) == 1
    assert orders[0].active_items[0].sku == "A1"

    # Order 2: has 1 active item
    assert len(orders[1].active_items) == 1
    assert orders[1].active_items[0].sku == "B1"

    # Order 3: has 0 items
    assert len(orders[2].active_items) == 0

    print("  [+] prefetch_related bulk query stitching with to_attr verified.")


if __name__ == "__main__":
    test_prefetch_stitcher()
```

---

## Exercise 3: Automated N+1 Query Regression Auditor Context Manager

### Problem Statement:
In continuous integration pipelines, catching N+1 query bugs before code reaches production is critical.

Implement `NPlusOneAuditor`, a context manager that:
1. Intercepts executed SQL statements and normalizes them (e.g. replaces literal IDs `id = 1` with `id = ?`).
2. Detects if the **same normalized SQL pattern is executed more than once** within the context block.
3. If duplicate query patterns are detected, raises `NPlusOneDetectedError` with a complete diagnostic report listing the offending SQL template and execution count.

### Complete Solution:

```python
"""
Exercise 3: Automated N+1 Query Regression Auditor Context Manager
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


class NPlusOneDetectedError(AssertionError):
    pass


class MockDatabase:
    def __init__(self):
        self.executed_queries: List[str] = []

    def query(self, sql: str) -> None:
        self.executed_queries.append(sql)

    def clear(self) -> None:
        self.executed_queries.clear()


DB = MockDatabase()


class NPlusOneAuditor:
    """
    Context manager that detects repeated query patterns indicating an N+1 bug.
    """
    def __init__(self, db: MockDatabase = DB):
        self.db = db
        self.start_index = 0

    def __enter__(self) -> NPlusOneAuditor:
        self.start_index = len(self.db.executed_queries)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            return

        new_queries = self.db.executed_queries[self.start_index:]
        pattern_counts: Dict[str, int] = {}

        for raw_sql in new_queries:
            # Normalize SQL: replace numbers with '?'
            normalized = re.sub(r"\b\d+\b", "?", raw_sql.strip())
            pattern_counts[normalized] = pattern_counts.get(normalized, 0) + 1

        # Check for repeated query patterns
        violations = {pat: cnt for pat, cnt in pattern_counts.items() if cnt > 1}
        if violations:
            error_msg = "N+1 Query Detected! Repeated SQL Patterns:\n"
            for pat, cnt in violations.items():
                error_msg += f"  - Executed {cnt} times: '{pat}'\n"
            error_msg += "Remediation: Apply select_related() or prefetch_related() to fetch in bulk."
            raise NPlusOneDetectedError(error_msg)


def test_n_plus_one_auditor() -> None:
    print("[*] Testing Exercise 3: N+1 Regression Auditor...")
    DB.clear()

    # Case 1: Optimized queries (no repeated query templates) -> PASSES
    with NPlusOneAuditor(DB):
        DB.query("SELECT * FROM orders WHERE status = 'PAID';")
        DB.query("SELECT * FROM customers WHERE id IN (?, ?, ?);")
    print("  [+] Optimized queries passed auditor.")

    # Case 2: N+1 loop bug -> RAISES NPlusOneDetectedError
    try:
        with NPlusOneAuditor(DB):
            DB.query("SELECT * FROM orders LIMIT 100;")
            # Simulated un-prefetched N+1 loop:
            DB.query("SELECT * FROM customers WHERE id = 1;")
            DB.query("SELECT * FROM customers WHERE id = 2;")
            DB.query("SELECT * FROM customers WHERE id = 3;")
        assert False, "Should have detected N+1 query pattern!"
    except NPlusOneDetectedError as exc:
        assert "Executed 3 times: 'SELECT * FROM customers WHERE id = ?;'" in str(exc)
        assert "Remediation" in str(exc)
    print("  [+] NPlusOneAuditor successfully trapped N+1 query loop.")


if __name__ == "__main__":
    test_n_plus_one_auditor()
```
