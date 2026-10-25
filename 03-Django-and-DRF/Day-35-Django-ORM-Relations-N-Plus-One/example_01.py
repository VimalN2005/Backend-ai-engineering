r"""
Day 35: Example 01 - Relational Descriptors, On-Delete Policies & N+1 Problem
=============================================================================

This module implements a pure-Python, zero-dependency architectural simulation of
Django's relational model descriptors (`ForeignKey`, `OneToOneField`, reverse managers),
on-delete integrity policies (`CASCADE`, `PROTECT`, `SET_NULL`), and an automated
demonstration of the N+1 Query Problem with SQL execution telemetry.

WHY THIS ARCHITECTURE MATTERS:
1. Descriptor-Driven Traversal: In Django, accessing `order.customer` doesn't just read an
   attribute—it triggers a Python descriptor (`ForeignKeyDeferredAttribute`) that checks
   an in-memory cache and, if absent, executes a synchronous SQL query over the network.
2. The N+1 Catastrophe: Accessing unoptimized foreign keys in a loop of $N$ items results in
   $1 + N$ queries. Over a cloud database connection with 15ms latency, 100 iterations
   take 1.5+ seconds just in network roundtrips.
3. ACID Relational Integrity (on_delete): In enterprise financial systems, cascading deletions
   can silently destroy transaction history. Enforcing `PROTECT` at the relational layer
   prevents catastrophic data loss when parent accounts or organizations are deleted.
"""

from __future__ import annotations

import enum
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple


class ProtectedError(Exception):
    """Raised when an object cannot be deleted because it is referenced by PROTECT relations."""
    pass


# -----------------------------------------------------------------------------
# Database Mock & Query Telemetry Logger
# -----------------------------------------------------------------------------

class DatabaseDriver:
    """Simulates a database engine tracking executed SQL statements."""
    def __init__(self):
        self.queries: List[str] = []

    def execute(self, sql: str) -> None:
        self.queries.append(sql)

    def clear(self) -> None:
        self.queries.clear()

    @property
    def query_count(self) -> int:
        return len(self.queries)


# Global DB driver singleton
DB = DatabaseDriver()


# -----------------------------------------------------------------------------
# On-Delete Deletion Policies
# -----------------------------------------------------------------------------

class OnDelete(enum.Enum):
    CASCADE = "CASCADE"
    PROTECT = "PROTECT"
    SET_NULL = "SET_NULL"
    DO_NOTHING = "DO_NOTHING"


# -----------------------------------------------------------------------------
# Simulated Model & Relational Descriptors
# -----------------------------------------------------------------------------

class RelationalModel:
    """Base model supporting primary keys and relation management."""
    def __init__(self, **kwargs: Any):
        self._state_cache: Dict[str, Any] = {}
        for k, v in kwargs.items():
            setattr(self, k, v)


class ForeignKeyDescriptor:
    """
    Simulates Django's forward ForeignKey descriptor.
    Fetches the related model instance on attribute access if not already cached.
    """
    def __init__(self, target_cls: type, db_column: str, on_delete: OnDelete, cache_attr: str):
        self.target_cls = target_cls
        self.db_column = db_column
        self.on_delete = on_delete
        self.cache_attr = cache_attr

    def __get__(self, instance: Optional[Any], owner: type) -> Any:
        if instance is None:
            return self

        # 1. Check in-memory prefetch/select_related cache
        if self.cache_attr in instance._state_cache:
            return instance._state_cache[self.cache_attr]

        foreign_id = getattr(instance, self.db_column, None)
        if foreign_id is None:
            return None

        # 2. TRIGGER ON-DEMAND QUERY (The source of the N+1 problem!)
        DB.execute(f"SELECT * FROM {self.target_cls.__name__.lower()}s WHERE id = {foreign_id};")
        
        # Look up record in global table registry
        target_instance = TABLE_REGISTRY[self.target_cls].get(foreign_id)
        instance._state_cache[self.cache_attr] = target_instance
        return target_instance

    def __set__(self, instance: Any, value: Any) -> None:
        if value is None:
            setattr(instance, self.db_column, None)
            instance._state_cache.pop(self.cache_attr, None)
        else:
            setattr(instance, self.db_column, value.id)
            instance._state_cache[self.cache_attr] = value


class ReverseManyToOneDescriptor:
    """
    Simulates Django's reverse foreign key manager (e.g. customer.orders.all()).
    """
    def __init__(self, child_cls: type, foreign_key_col: str, cache_attr: str):
        self.child_cls = child_cls
        self.foreign_key_col = foreign_key_col
        self.cache_attr = cache_attr

    def __get__(self, instance: Optional[Any], owner: type) -> Any:
        if instance is None:
            return self

        # Check prefetch cache first
        if self.cache_attr in instance._state_cache:
            return instance._state_cache[self.cache_attr]

        # Trigger un-prefetched reverse query
        DB.execute(f"SELECT * FROM {self.child_cls.__name__.lower()}s WHERE {self.foreign_key_col} = {instance.id};")
        records = [
            obj for obj in TABLE_REGISTRY[self.child_cls].values()
            if getattr(obj, self.foreign_key_col) == instance.id
        ]
        return records


# -----------------------------------------------------------------------------
# Domain Models Schema
# -----------------------------------------------------------------------------

class Customer(RelationalModel):
    id: int
    name: str
    email: str

    # Reverse relationship: customer.orders
    orders = ReverseManyToOneDescriptor(
        child_cls=lambda: Order,  # Forward reference resolved below
        foreign_key_col="customer_id",
        cache_attr="_prefetched_orders",
    )


class Order(RelationalModel):
    id: int
    customer_id: Optional[int]
    total_amount: float
    status: str

    # Forward relationship: order.customer
    customer = ForeignKeyDescriptor(
        target_cls=Customer,
        db_column="customer_id",
        on_delete=OnDelete.PROTECT,
        cache_attr="_cached_customer",
    )


# Resolve reverse manager child class
Customer.orders.child_cls = Order

# In-memory table storage simulating database physical tables
TABLE_REGISTRY: Dict[type, Dict[int, Any]] = {
    Customer: {},
    Order: {},
}


# -----------------------------------------------------------------------------
# Relational Deletion Engine
# -----------------------------------------------------------------------------

def delete_customer(customer: Customer) -> None:
    """
    Simulates Django's relational deletion collector.
    Enforces OnDelete rules (PROTECT, CASCADE, SET_NULL) before executing deletion.
    """
    DB.execute(f"SELECT * FROM orders WHERE customer_id = {customer.id} FOR UPDATE;")
    related_orders = [
        o for o in TABLE_REGISTRY[Order].values() if o.customer_id == customer.id
    ]

    # Evaluate on_delete policy
    if Order.customer.on_delete == OnDelete.PROTECT and related_orders:
        raise ProtectedError(
            f"Cannot delete Customer '{customer.name}' (id={customer.id}): "
            f"Referenced by {len(related_orders)} active order(s) under OnDelete.PROTECT."
        )
    elif Order.customer.on_delete == OnDelete.CASCADE:
        for o in related_orders:
            del TABLE_REGISTRY[Order][o.id]
            DB.execute(f"DELETE FROM orders WHERE id = {o.id};")
    elif Order.customer.on_delete == OnDelete.SET_NULL:
        for o in related_orders:
            o.customer_id = None
            DB.execute(f"UPDATE orders SET customer_id = NULL WHERE id = {o.id};")

    del TABLE_REGISTRY[Customer][customer.id]
    DB.execute(f"DELETE FROM customers WHERE id = {customer.id};")


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 35 Relational Mapping & N+1 Verification Suite...")
    DB.clear()
    TABLE_REGISTRY[Customer].clear()
    TABLE_REGISTRY[Order].clear()

    # 1. Seed Database with Customers and Orders
    c1 = Customer(id=1, name="Acme Corp", email="billing@acme.corp")
    c2 = Customer(id=2, name="Global Tech", email="admin@global.tech")
    TABLE_REGISTRY[Customer][c1.id] = c1
    TABLE_REGISTRY[Customer][c2.id] = c2

    orders_list = []
    for i in range(1, 101):
        # 100 orders split between 2 customers
        cust = c1 if i % 2 == 0 else c2
        o = Order(id=i, customer_id=cust.id, total_amount=float(i * 10), status="PAID")
        TABLE_REGISTRY[Order][o.id] = o
        orders_list.append(o)

    print("  [+] Seeded 2 customers and 100 orders.")

    # 2. DEMONSTRATE THE N+1 QUERY CATASTROPHE
    DB.clear()
    # Query 1: Fetch 100 orders
    DB.execute("SELECT * FROM orders LIMIT 100;")

    # Now iterate through orders and access un-prefetched customer
    customer_emails = []
    for order in orders_list:
        customer_emails.append(order.customer.email)

    # In an un-prefetched loop, each order.customer accesses the descriptor and issues a query!
    # Because there are 2 customers, after each is queried once, it was cached on that specific
    # instance, but because each order instance is separate, it issued queries across instances!
    print(f"  [!] N+1 Demonstration: Executed {DB.query_count} queries for 100 orders.")
    assert DB.query_count > 10, "Expected multiple queries due to N+1 access!"

    # 3. Test OnDelete.PROTECT Integrity
    try:
        delete_customer(c1)
        assert False, "Should have raised ProtectedError due to OnDelete.PROTECT"
    except ProtectedError as exc:
        assert "OnDelete.PROTECT" in str(exc)
    print("  [+] OnDelete.PROTECT successfully blocked deletion of referenced customer.")

    # 4. Test Reverse Manager Lookup
    DB.clear()
    acme_orders = c1.orders
    assert len(acme_orders) == 50
    assert DB.query_count == 1
    print("  [+] Reverse Many-to-One lookup verified.")


def benchmark_n_plus_one_simulation(num_orders: int = 1_000) -> None:
    """Benchmark the network latency impact of N+1 queries vs optimized in-memory access."""
    simulated_network_latency_ms = 1.0  # 1ms per DB roundtrip

    n_plus_one_queries = 1 + num_orders
    n_plus_one_time_sec = (n_plus_one_queries * simulated_network_latency_ms) / 1000

    optimized_queries = 1  # With select_related
    optimized_time_sec = (optimized_queries * simulated_network_latency_ms) / 1000

    speedup = n_plus_one_time_sec / optimized_time_sec

    print("\n" + "=" * 65)
    print("DAY 35: N+1 QUERY LATENCY IMPACT BENCHMARK")
    print("=" * 65)
    print(f"Dataset Size:              {num_orders:,} related child rows")
    print(f"Assumed Network Latency:   {simulated_network_latency_ms:.1f} ms per database roundtrip")
    print(f"N+1 Queries Executed:      {n_plus_one_queries:,} queries -> {n_plus_one_time_sec:.2f} seconds")
    print(f"Optimized (select_related):{optimized_queries:,} query   -> {optimized_time_sec:.4f} seconds")
    print(f"Latency Reduction:         {speedup:,.0f}x FASTER network turnaround")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_n_plus_one_simulation()
