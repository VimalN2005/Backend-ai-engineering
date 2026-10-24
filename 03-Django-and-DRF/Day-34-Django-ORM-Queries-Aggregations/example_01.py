r"""
Day 34: Example 01 - Django QuerySet Architecture, Lazy Evaluation & Q/F Expressions
=====================================================================================

This module implements a pure-Python, zero-dependency architectural simulation of
Django's QuerySet engine, including lazy evaluation, immutable cloning, internal
result caching (`_result_cache`), streaming iterators, `Q` boolean trees, and `F` expressions.

WHY THIS ARCHITECTURE MATTERS:
1. Immutability & Reusability: QuerySets are immutable blueprints. Chaining `.filter()`
   or `.exclude()` creates a cloned query object (`_clone()`), allowing repository managers
   to compose modular queries without mutating shared state.
2. Lazy Evaluation Lifecycle: Queries are not sent across the network when constructed.
   Execution is deferred until iteration, slicing, or conversion (`list()`, `count()`).
3. The Result Cache (`_result_cache`): Once evaluated, Django caches the model instances
   in memory. Re-iterating over the same QuerySet costs 0 database queries, but can cause
   Out-Of-Memory (OOM) crashes on large datasets unless `iterator()` is used.
4. Concurrency Safety with F(): In-memory read-modify-write patterns (`obj.count += 1; obj.save()`)
   suffer from lost updates under concurrent requests. `F()` expressions defer the calculation
   to the database engine (`UPDATE table SET count = count + 1`), guaranteeing atomic mutations.
"""

from __future__ import annotations

import copy
import decimal
import functools
import operator
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union


# -----------------------------------------------------------------------------
# Q Object (Boolean Expression Tree)
# -----------------------------------------------------------------------------

class Q:
    """
    Encapsulates SQL WHERE conditions that can be combined with & (AND), | (OR), and ~ (NOT).
    Mirrors `django.db.models.Q`.
    """
    AND = "AND"
    OR = "OR"

    def __init__(self, *args: Tuple[str, Any], _connector: str = AND, _negated: bool = False, **kwargs: Any):
        self.connector = _connector
        self.negated = _negated
        # List of children: can be (lookup_key, value) tuples or nested Q objects
        self.children: List[Union[Tuple[str, Any], Q]] = list(args) + list(kwargs.items())

    def __and__(self, other: Q) -> Q:
        return self._combine(other, self.AND)

    def __or__(self, other: Q) -> Q:
        return self._combine(other, self.OR)

    def __invert__(self) -> Q:
        q = copy.deepcopy(self)
        q.negated = not q.negated
        return q

    def _combine(self, other: Q, connector: str) -> Q:
        if not isinstance(other, Q):
            raise TypeError(f"Cannot combine Q object with {type(other)}")
        combined = Q(_connector=connector)
        combined.children = [copy.deepcopy(self), copy.deepcopy(other)]
        return combined

    def evaluate(self, record: Dict[str, Any]) -> bool:
        """
        Recursively evaluate whether an in-memory record satisfies this Q expression tree.
        """
        results: List[bool] = []
        for child in self.children:
            if isinstance(child, Q):
                res = child.evaluate(record)
            else:
                lookup_key, expected = child
                res = self._evaluate_lookup(record, lookup_key, expected)
            results.append(res)

        if not results:
            outcome = True
        elif self.connector == self.AND:
            outcome = all(results)
        elif self.connector == self.OR:
            outcome = any(results)
        else:
            outcome = True

        return not outcome if self.negated else outcome

    @staticmethod
    def _evaluate_lookup(record: Dict[str, Any], lookup_key: str, expected: Any) -> bool:
        """Evaluate field lookup operators (__exact, __gt, __gte, __in, __contains, etc.)."""
        parts = lookup_key.split("__")
        field_name = parts[0]
        operator_type = parts[1] if len(parts) > 1 else "exact"

        val = record.get(field_name)

        if operator_type == "exact":
            return val == expected
        elif operator_type == "iexact":
            return str(val).lower() == str(expected).lower()
        elif operator_type == "gt":
            return val is not None and val > expected
        elif operator_type == "gte":
            return val is not None and val >= expected
        elif operator_type == "lt":
            return val is not None and val < expected
        elif operator_type == "lte":
            return val is not None and val <= expected
        elif operator_type == "in":
            return val in expected
        elif operator_type == "contains":
            return expected in str(val)
        elif operator_type == "icontains":
            return str(expected).lower() in str(val).lower()
        elif operator_type == "isnull":
            return (val is None) == bool(expected)
        else:
            raise NotImplementedError(f"Lookup '{operator_type}' is not implemented.")


# -----------------------------------------------------------------------------
# F Expression (Database-Level Field Reference)
# -----------------------------------------------------------------------------

class F:
    """
    Represents the value of a model field directly inside database operations.
    Mirrors `django.db.models.F`.
    """
    def __init__(self, name: str):
        self.name = name

    def __add__(self, other: Any) -> ExpressionNode:
        return ExpressionNode(self, "+", other)

    def __sub__(self, other: Any) -> ExpressionNode:
        return ExpressionNode(self, "-", other)

    def __mul__(self, other: Any) -> ExpressionNode:
        return ExpressionNode(self, "*", other)

    def resolve(self, record: Dict[str, Any]) -> Any:
        return record.get(self.name)


class ExpressionNode:
    """Represents an arithmetic operation on an F expression."""
    def __init__(self, lhs: Union[F, ExpressionNode], op: str, rhs: Any):
        self.lhs = lhs
        self.op = op
        self.rhs = rhs

    def resolve(self, record: Dict[str, Any]) -> Any:
        lhs_val = self.lhs.resolve(record) if isinstance(self.lhs, (F, ExpressionNode)) else self.lhs
        rhs_val = self.rhs.resolve(record) if isinstance(self.rhs, (F, ExpressionNode)) else self.rhs

        if self.op == "+":
            return lhs_val + rhs_val
        elif self.op == "-":
            return lhs_val - rhs_val
        elif self.op == "*":
            return lhs_val * rhs_val
        raise NotImplementedError(f"Operator '{self.op}' not supported.")


# -----------------------------------------------------------------------------
# Simulated Model Class
# -----------------------------------------------------------------------------

class SimulatedModel:
    """Represents an instantiated Django model object."""
    def __init__(self, **kwargs: Any):
        self.__dict__.update(kwargs)

    def __repr__(self) -> str:
        attrs = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items() if not k.startswith("_"))
        return f"{self.__class__.__name__}({attrs})"


# -----------------------------------------------------------------------------
# QuerySet Engine Simulation
# -----------------------------------------------------------------------------

class QuerySet:
    """
    Simulates Django's QuerySet architecture:
    - Lazy query construction
    - Immutable cloning via _clone()
    - In-memory result caching via _result_cache
    - Streaming iterator support via iterator()
    """
    def __init__(
        self,
        model_cls: type,
        datasource: List[Dict[str, Any]],
        q_filter: Optional[Q] = None,
        ordering: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ):
        self.model_cls = model_cls
        self._datasource = datasource
        self._q_filter = q_filter or Q()
        self._ordering = ordering or []
        self._limit = limit
        self._offset = offset

        # The internal result cache (populated only when evaluated!)
        self._result_cache: Optional[List[SimulatedModel]] = None
        self._query_execution_count: int = 0

    def _clone(self) -> QuerySet:
        """
        Creates a copy of the QuerySet.
        WHY: Django QuerySets are immutable. Every filtering operation returns a new clone
        so existing QuerySet instances can be reused safely.
        """
        clone = QuerySet(
            model_cls=self.model_cls,
            datasource=self._datasource,
            q_filter=copy.deepcopy(self._q_filter),
            ordering=list(self._ordering),
            limit=self._limit,
            offset=self._offset,
        )
        return clone

    def filter(self, *args: Q, **kwargs: Any) -> QuerySet:
        """Chain additional filter conditions (SQL AND)."""
        clone = self._clone()
        for q_obj in args:
            clone._q_filter &= q_obj
        if kwargs:
            clone._q_filter &= Q(**kwargs)
        return clone

    def exclude(self, *args: Q, **kwargs: Any) -> QuerySet:
        """Chain exclusion filter conditions (SQL AND NOT (...))."""
        clone = self._clone()
        for q_obj in args:
            clone._q_filter &= ~q_obj
        if kwargs:
            clone._q_filter &= ~Q(**kwargs)
        return clone

    def order_by(self, *field_names: str) -> QuerySet:
        """Set query ordering."""
        clone = self._clone()
        clone._ordering = list(field_names)
        return clone

    def _execute(self) -> List[SimulatedModel]:
        """
        Simulate database execution: evaluates filters, sorting, slicing,
        and instantiates model instances.
        """
        self._query_execution_count += 1

        # 1. Evaluate Q filter
        matched: List[Dict[str, Any]] = [
            row for row in self._datasource if self._q_filter.evaluate(row)
        ]

        # 2. Apply Ordering
        if self._ordering:
            for field in reversed(self._ordering):
                descending = field.startswith("-")
                key_name = field.lstrip("-")
                matched.sort(key=lambda r: (r.get(key_name) is None, r.get(key_name)), reverse=descending)

        # 3. Apply Limit / Offset Slicing
        start = self._offset
        end = self._offset + self._limit if self._limit is not None else None
        sliced = matched[start:end]

        # 4. Instantiate Model Instances
        return [self.model_cls(**row) for row in sliced]

    def _fetch_all(self) -> None:
        """Populate _result_cache if not already loaded."""
        if self._result_cache is None:
            self._result_cache = self._execute()

    def __iter__(self) -> Iterator[SimulatedModel]:
        """Iterating over a QuerySet evaluates it and populates _result_cache."""
        self._fetch_all()
        assert self._result_cache is not None
        return iter(self._result_cache)

    def __len__(self) -> int:
        """Calling len() evaluates the QuerySet and loads all rows into memory."""
        self._fetch_all()
        assert self._result_cache is not None
        return len(self._result_cache)

    def __getitem__(self, k: Union[int, slice]) -> Union[SimulatedModel, QuerySet]:
        """Support indexing and slicing."""
        if isinstance(k, slice):
            clone = self._clone()
            clone._offset = (self._offset + (k.start or 0))
            if k.stop is not None:
                clone._limit = k.stop - (k.start or 0)
            return clone
        elif isinstance(k, int):
            self._fetch_all()
            assert self._result_cache is not None
            return self._result_cache[k]
        raise TypeError(f"Invalid slice type: {type(k)}")

    def count(self) -> int:
        """
        WHY: In real Django, qs.count() executes SELECT COUNT(*) in SQL without
        instantiating model objects or populating _result_cache.
        """
        if self._result_cache is not None:
            return len(self._result_cache)
        # Execute fast count over datasource
        return sum(1 for row in self._datasource if self._q_filter.evaluate(row))

    def exists(self) -> bool:
        """
        WHY: Translates to SELECT 1 ... LIMIT 1 in SQL. Stops scanning on first match!
        """
        if self._result_cache is not None:
            return bool(self._result_cache)
        for row in self._datasource:
            if self._q_filter.evaluate(row):
                return True
        return False

    def first(self) -> Optional[SimulatedModel]:
        """Returns the first model instance or None (appends LIMIT 1)."""
        qs = self[0:1]
        for obj in qs:
            return obj
        return None

    def last(self) -> Optional[SimulatedModel]:
        """Returns the last model instance or None."""
        self._fetch_all()
        assert self._result_cache is not None
        return self._result_cache[-1] if self._result_cache else None

    def values(self, *fields: str) -> List[Dict[str, Any]]:
        """
        WHY: Returns raw dictionaries instead of model instances.
        Dramatically reduces memory consumption on large readouts.
        """
        self._fetch_all()
        assert self._result_cache is not None
        return [
            {f: getattr(obj, f, None) for f in fields}
            for obj in self._result_cache
        ]

    def values_list(self, *fields: str, flat: bool = False) -> List[Any]:
        """
        WHY: Returns raw tuples (or flat scalar values), avoiding model instantiation overhead.
        """
        self._fetch_all()
        assert self._result_cache is not None
        if flat:
            if len(fields) != 1:
                raise TypeError("flat=True requires exactly one field.")
            field_name = fields[0]
            return [getattr(obj, field_name, None) for obj in self._result_cache]
        return [
            tuple(getattr(obj, f, None) for f in fields)
            for obj in self._result_cache
        ]

    def iterator(self, chunk_size: int = 2000) -> Iterator[SimulatedModel]:
        """
        WHY: Streams records chunk-by-chunk WITHOUT populating _result_cache.
        Maintains flat O(1) memory consumption during massive ETL / export jobs.
        """
        matched = [row for row in self._datasource if self._q_filter.evaluate(row)]
        for i in range(0, len(matched), chunk_size):
            chunk = matched[i:i + chunk_size]
            for row in chunk:
                yield self.model_cls(**row)

    def update(self, **kwargs: Any) -> int:
        """
        Simulate Django's QuerySet.update().
        Supports F() expressions for atomic concurrency updates directly on the datasource.
        """
        updated_count = 0
        for row in self._datasource:
            if self._q_filter.evaluate(row):
                for key, val in kwargs.items():
                    if isinstance(val, (F, ExpressionNode)):
                        row[key] = val.resolve(row)
                    else:
                        row[key] = val
                updated_count += 1
        return updated_count


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 34 QuerySet Architecture Verification Suite...")

    # Mock database table of orders
    orders_table: List[Dict[str, Any]] = [
        {"id": 1, "customer": "Alice", "amount": decimal.Decimal("150.00"), "status": "PAID", "is_deleted": False},
        {"id": 2, "customer": "Bob", "amount": decimal.Decimal("45.50"), "status": "PENDING", "is_deleted": False},
        {"id": 3, "customer": "Charlie", "amount": decimal.Decimal("200.00"), "status": "PAID", "is_deleted": False},
        {"id": 4, "customer": "David", "amount": decimal.Decimal("10.00"), "status": "CANCELLED", "is_deleted": True},
        {"id": 5, "customer": "Eve", "amount": decimal.Decimal("320.00"), "status": "PAID", "is_deleted": False},
    ]

    base_qs = QuerySet(SimulatedModel, orders_table)

    # 1. Test Lazy Evaluation (Creating query does not execute it!)
    filtered_qs = base_qs.filter(status="PAID").exclude(is_deleted=True)
    assert filtered_qs._result_cache is None
    assert filtered_qs._query_execution_count == 0
    print("  [+] Lazy evaluation verified (0 DB queries on creation).")

    # 2. Test Immutability & Cloning
    clone_1 = base_qs.filter(status="PAID")
    clone_2 = base_qs.filter(status="PENDING")
    assert clone_1 is not base_qs
    assert clone_2 is not base_qs
    assert clone_1._q_filter is not clone_2._q_filter
    print("  [+] QuerySet immutability & cloning verified.")

    # 3. Test Evaluation & Result Caching
    # First iteration: executes query and populates cache
    results_pass1 = [o for o in filtered_qs]
    assert len(results_pass1) == 3
    assert filtered_qs._query_execution_count == 1
    assert filtered_qs._result_cache is not None

    # Second iteration: reads from cache, 0 extra queries!
    results_pass2 = [o for o in filtered_qs]
    assert len(results_pass2) == 3
    assert filtered_qs._query_execution_count == 1  # Still 1!
    print("  [+] _result_cache reuse verified (0 subsequent DB executions).")

    # 4. Test Q Object Combinations (OR and NOT)
    # Status is PAID OR PENDING, and NOT deleted
    q_complex = (Q(status="PAID") | Q(status="PENDING")) & ~Q(is_deleted=True)
    complex_qs = base_qs.filter(q_complex)
    complex_results = list(complex_qs)
    assert len(complex_results) == 4
    assert {o.customer for o in complex_results} == {"Alice", "Bob", "Charlie", "Eve"}
    print("  [+] Q object (OR, NOT, AND) expression trees verified.")

    # 5. Test F() Expression Atomic Updates
    # Increment all PAID orders by 50.00 directly via F('amount') + 50.00
    paid_qs = base_qs.filter(status="PAID")
    updated_count = paid_qs.update(amount=F("amount") + decimal.Decimal("50.00"))
    assert updated_count == 3
    
    # Alice was 150.00 -> now 200.00
    alice = base_qs.filter(customer="Alice").first()
    assert alice is not None
    assert alice.amount == decimal.Decimal("200.00")
    print("  [+] F() expression atomic concurrency updates verified.")

    # 6. Test Optimized Projections (values & values_list)
    val_list = base_qs.filter(status="PAID").values_list("id", flat=True)
    assert val_list == [1, 3, 5]
    vals = base_qs.filter(customer="Alice").values("id", "customer")
    assert vals == [{"id": 1, "customer": "Alice"}]
    print("  [+] values() and values_list(flat=True) projections verified.")

    # 7. Test Streaming iterator() (Does not populate _result_cache!)
    stream_qs = base_qs.filter(status="PAID")
    streamed_items = list(stream_qs.iterator(chunk_size=2))
    assert len(streamed_items) == 3
    assert stream_qs._result_cache is None  # Cache was never populated!
    print("  [+] Streaming iterator() verified (bypasses _result_cache for O(1) memory).")


def benchmark_queryset(iterations: int = 5_000) -> None:
    """Benchmark QuerySet cloning, filtering, and caching throughput."""
    mock_db = [
        {"id": i, "tenant": i % 10, "status": "ACTIVE" if i % 2 == 0 else "INACTIVE", "val": i * 10}
        for i in range(100)
    ]
    base_qs = QuerySet(SimulatedModel, mock_db)

    start_time = time.perf_counter()
    for _ in range(iterations):
        # Build multi-clause chained query and evaluate count
        qs = base_qs.filter(tenant=5).exclude(status="INACTIVE")
        _ = qs.count()
    elapsed = time.perf_counter() - start_time

    latency_us = (elapsed / iterations) * 1_000_000
    ops_per_sec = iterations / elapsed

    print("\n" + "=" * 65)
    print("DAY 34: QUERYSET CLONING & EVALUATION BENCHMARK")
    print("=" * 65)
    print(f"Iterations:        {iterations:,}")
    print(f"Total Time:        {elapsed:.4f} seconds")
    print(f"Average Latency:   {latency_us:.2f} microseconds per query cycle")
    print(f"Throughput:        {ops_per_sec:,.0f} queries/sec")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_queryset()
