"""
Day 50: Eager Loading Strategy Benchmark & Diagnostic Harness
=============================================================

Production Performance Comparison:
1. The N+1 Query Disaster (Simulated sequential loading)
2. joinedload (LEFT OUTER JOIN - single round-trip with Cartesian product)
3. selectinload (Batch SELECT IN - O(1) queries without duplicate row fan-out)

Includes an automated SQL statement counter listening to low-level engine events
to record exact round-trips to the database driver.
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    event,
    func,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    joinedload,
    mapped_column,
    relationship,
    selectinload,
)

# ---------------------------------------------------------------------------
# 1. Models & Schemas
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), default="STANDARD")

    # 1:N Relationship to Order
    orders: Mapped[List["Order"]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    customer: Mapped["Customer"] = relationship(
        back_populates="orders",
        lazy="raise_on_sql",
    )

    # 1:N Relationship to OrderItem
    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)

    order: Mapped["Order"] = relationship(
        back_populates="items",
        lazy="raise_on_sql",
    )


# ---------------------------------------------------------------------------
# 2. SQL Execution Counter Event Listener
# ---------------------------------------------------------------------------

class SQLQueryCounter:
    """
    Hooks into SQLAlchemy's Core engine events to count exact SQL statements emitted.
    WHY:
    Benchmarking ORMs without tracking emitted statements leads to false conclusions.
    This listener accurately counts every cursor execution across threads.
    """
    def __init__(self, engine: AsyncEngine):
        self.count = 0
        self.statements: List[str] = []
        # Listen to the underlying sync engine's before_cursor_execute event
        event.listen(engine.sync_engine, "before_cursor_execute", self._before_execute)

    def _before_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.statements.append(statement)

    def reset(self) -> None:
        self.count = 0
        self.statements.clear()


# ---------------------------------------------------------------------------
# 3. Benchmark Fixture & Seeding
# ---------------------------------------------------------------------------

DB_FILE = "day50_benchmark_demo.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"

NUM_CUSTOMERS = 80
ORDERS_PER_CUSTOMER = 4
ITEMS_PER_ORDER = 3
TOTAL_ORDERS = NUM_CUSTOMERS * ORDERS_PER_CUSTOMER
TOTAL_ITEMS = TOTAL_ORDERS * ITEMS_PER_ORDER


async def setup_benchmark_environment(engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Creates tables and seeds benchmark dataset.
    80 Customers x 4 Orders x 3 Items = 320 Orders, 960 Items.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    print(f"[SETUP] Seeding dataset: {NUM_CUSTOMERS} customers, {TOTAL_ORDERS} orders, {TOTAL_ITEMS} order items...")
    seed_start = time.perf_counter()

    async with session_factory() as session:
        async with session.begin():
            customers = []
            for c_idx in range(1, NUM_CUSTOMERS + 1):
                cust = Customer(
                    name=f"Enterprise Client #{c_idx:03d}",
                    tier="VIP" if c_idx % 5 == 0 else "STANDARD",
                )
                session.add(cust)
                # Build hierarchy directly in memory before flush
                for o_idx in range(1, ORDERS_PER_CUSTOMER + 1):
                    order = Order(
                        order_number=f"ORD-C{c_idx:03d}-O{o_idx:02d}",
                        status="DELIVERED",
                        customer=cust,
                    )
                    session.add(order)
                    for i_idx in range(1, ITEMS_PER_ORDER + 1):
                        item = OrderItem(
                            sku=f"SKU-{c_idx}-{o_idx}-{i_idx}",
                            quantity=i_idx * 2,
                            price=19.99 * i_idx,
                            order=order,
                        )
                        session.add(item)

    seed_duration = (time.perf_counter() - seed_start) * 1000
    print(f"[SETUP] Seed completed in {seed_duration:.2f} ms.")


# ---------------------------------------------------------------------------
# 4. Loading Strategies Benchmarks
# ---------------------------------------------------------------------------

async def benchmark_n_plus_one(
    session_factory: async_sessionmaker[AsyncSession], counter: SQLQueryCounter
) -> Dict[str, Any]:
    """
    Simulates the classical N+1 query antipattern.
    In async mode with lazy='raise', doing this sequentially requires manual queries.
    We query all Customers (1 query), then for each customer query their orders (N queries),
    and for each order query its items (N*M queries).
    Total queries = 1 + NUM_CUSTOMERS + TOTAL_ORDERS = 1 + 80 + 320 = 401 queries!
    """
    counter.reset()
    start_time = time.perf_counter()

    item_count = 0
    order_count = 0

    async with session_factory() as session:
        # Query 1: Fetch all customers
        cust_stmt = select(Customer)
        customers = (await session.execute(cust_stmt)).scalars().all()

        for cust in customers:
            # Query N: Fetch orders for customer
            order_stmt = select(Order).where(Order.customer_id == cust.id)
            orders = (await session.execute(order_stmt)).scalars().all()
            order_count += len(orders)

            for order in orders:
                # Query N * M: Fetch items for order
                item_stmt = select(OrderItem).where(OrderItem.order_id == order.id)
                items = (await session.execute(item_stmt)).scalars().all()
                item_count += len(items)

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    return {
        "strategy": "N+1 Query Storm (Sequential)",
        "queries": counter.count,
        "elapsed_ms": elapsed_ms,
        "items_hydrated": item_count,
        "orders_hydrated": order_count,
        "round_trip_type": "O(1 + N + N*M) round trips",
        "cartesian_factor": "1.0 (No join fan-out, but catastrophic latency)",
    }


async def benchmark_joinedload(
    session_factory: async_sessionmaker[AsyncSession], counter: SQLQueryCounter
) -> Dict[str, Any]:
    """
    Executes eager loading using joinedload for both levels:
    select(Customer).options(joinedload(Customer.orders).joinedload(Order.items))
    Emits 1 single SQL query with 2 LEFT OUTER JOINs.
    Returns 960 joined rows, resulting in Cartesian product redundancy!
    """
    counter.reset()
    start_time = time.perf_counter()

    async with session_factory() as session:
        stmt = (
            select(Customer)
            .options(
                joinedload(Customer.orders).joinedload(Order.items)
            )
        )
        result = await session.execute(stmt)
        # unique() is required when joinedload is used on collections
        customers = result.unique().scalars().all()

        item_count = sum(len(order.items) for cust in customers for order in cust.orders)
        order_count = sum(len(cust.orders) for cust in customers)

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    return {
        "strategy": "joinedload (LEFT OUTER JOIN)",
        "queries": counter.count,
        "elapsed_ms": elapsed_ms,
        "items_hydrated": item_count,
        "orders_hydrated": order_count,
        "round_trip_type": "O(1) single round trip",
        "cartesian_factor": f"{TOTAL_ITEMS / NUM_CUSTOMERS:.1f}x redundant parent columns",
    }


async def benchmark_selectinload(
    session_factory: async_sessionmaker[AsyncSession], counter: SQLQueryCounter
) -> Dict[str, Any]:
    """
    Executes eager loading using selectinload for both levels:
    select(Customer).options(selectinload(Customer.orders).selectinload(Order.items))
    Emits exactly 3 queries:
      1. SELECT * FROM customers
      2. SELECT * FROM orders WHERE customer_id IN (...)
      3. SELECT * FROM order_items WHERE order_id IN (...)
    Zero duplicate rows transferred, O(1) query count regardless of row volume.
    """
    counter.reset()
    start_time = time.perf_counter()

    async with session_factory() as session:
        stmt = (
            select(Customer)
            .options(
                selectinload(Customer.orders).selectinload(Order.items)
            )
        )
        result = await session.execute(stmt)
        customers = result.scalars().all()

        item_count = sum(len(order.items) for cust in customers for order in cust.orders)
        order_count = sum(len(cust.orders) for cust in customers)

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    return {
        "strategy": "selectinload (Batch SELECT IN)",
        "queries": counter.count,
        "elapsed_ms": elapsed_ms,
        "items_hydrated": item_count,
        "orders_hydrated": order_count,
        "round_trip_type": "O(Depth) = exactly 3 queries",
        "cartesian_factor": "1.0 (Zero duplicate row transfer)",
    }


# ---------------------------------------------------------------------------
# 5. Presentation & Analysis Report
# ---------------------------------------------------------------------------

def display_benchmark_results(results: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 90)
    print("SQLAlchemy 2.0 Eager Loading Architectural Benchmark Report")
    print(f"Dataset Size: {NUM_CUSTOMERS} Customers | {TOTAL_ORDERS} Orders | {TOTAL_ITEMS} OrderItems")
    print("=" * 90)

    header = f"{'Strategy':<32} | {'Queries':<9} | {'Latency (ms)':<14} | {'Speedup':<9} | {'Cartesian Multiplier'}"
    print(header)
    print("-" * 90)

    baseline_latency = results[0]["elapsed_ms"]

    for res in results:
        speedup = f"{baseline_latency / res['elapsed_ms']:.2f}x"
        row = (
            f"{res['strategy']:<32} | "
            f"{res['queries']:<9} | "
            f"{res['elapsed_ms']:<14.2f} | "
            f"{speedup:<9} | "
            f"{res['cartesian_factor']}"
        )
        print(row)

    print("=" * 90)
    print("\nARCHITECTURAL TAKEAWAYS:")
    print("1. The N+1 Disaster:")
    print(f"   Emitted {results[0]['queries']} database queries. In production environments with a 2ms network hop,")
    print(f"   this strategy adds {results[0]['queries'] * 2}ms purely in socket latency, saturating the connection pool.")
    print("2. joinedload Trade-Off:")
    print("   Achieves single-query execution, but causes Cartesian row explosion on multi-level collections.")
    print("   Every Customer and Order column is redundantly repeated for every OrderItem in the joined rowset.")
    print("3. selectinload Winner for Collections:")
    print(f"   Emitted exactly {results[2]['queries']} queries (1 per relationship depth).")
    print("   Transfers exactly 1 row per record with zero Cartesian bloat and minimal memory overhead.")
    print("   Recommendation: Use selectinload for 1:N and M:N collections; reserve joinedload for M:1 and 1:1.")


# ---------------------------------------------------------------------------
# 6. Main Orchestrator
# ---------------------------------------------------------------------------

async def main() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    counter = SQLQueryCounter(engine)

    try:
        await setup_benchmark_environment(engine, session_factory)

        print("\n[RUNNING BENCHMARKS]")
        print("1. Benchmarking N+1 Query Storm...")
        res_n_plus_one = await benchmark_n_plus_one(session_factory, counter)

        print("2. Benchmarking joinedload (Cartesian Join)...")
        res_joinedload = await benchmark_joinedload(session_factory, counter)

        print("3. Benchmarking selectinload (Batch SELECT IN)...")
        res_selectinload = await benchmark_selectinload(session_factory, counter)

        display_benchmark_results([res_n_plus_one, res_joinedload, res_selectinload])

    finally:
        await engine.dispose()
        if os.path.exists(DB_FILE):
            try:
                os.remove(DB_FILE)
                print(f"\n[CLEANUP] Benchmark database file '{DB_FILE}' removed.")
            except OSError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
