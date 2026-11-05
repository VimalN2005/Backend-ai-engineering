# Day 50: Asynchronous SQLAlchemy & Relationships Practical Exercises

Mastering SQLAlchemy 2.0 async operations requires deep hands-on fluency with eager loading, dynamic filtering, and clean architectural encapsulation. Complete the following three production-grade engineering exercises. Verified reference solutions follow each prompt.

---

## Exercise 1: Multi-Level E-Commerce Aggregator with Nested `selectinload`

### Architectural Scenario
You are designing an asynchronous analytics endpoint for an enterprise commerce platform. The system needs to retrieve customer orders including all nested line items, the underlying product details for each line item, and customer information.

### Requirements
1. Design the declarative models:
   - `Customer` (1:N with `Order`)
   - `Order` (M:1 with `Customer`, 1:N with `OrderItem`)
   - `Product` (1:N with `OrderItem`)
   - `OrderItem` (M:1 with `Order`, M:1 with `Product`)
2. Configure all models using SQLAlchemy 2.0 `Mapped` and `mapped_column` annotations.
3. Enforce `lazy="raise_on_sql"` across all relationships.
4. Construct an asynchronous query function `get_customer_order_summary(session, customer_id)` that:
   - Fetches the customer by ID.
   - Eager-loads all associated `orders`, each order's `items`, and each item's associated `product`.
   - Uses `selectinload` to achieve $O(1)$ database round trips.
   - Computes total order value and item counts in memory without triggering additional database queries.

### Verified Reference Solution

```python
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import Integer, String, Float, ForeignKey, DateTime, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

class Base(DeclarativeBase):
    pass

class Customer(Base):
    __tablename__ = "ex1_customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    orders: Mapped[List["Order"]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )

class Order(Base):
    __tablename__ = "ex1_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("ex1_customers.id", ondelete="CASCADE"), nullable=False)
    order_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    customer: Mapped["Customer"] = relationship(back_populates="orders", lazy="raise_on_sql")
    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )

class Product(Base):
    __tablename__ = "ex1_products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)

    order_items: Mapped[List["OrderItem"]] = relationship(back_populates="product", lazy="raise_on_sql")

class OrderItem(Base):
    __tablename__ = "ex1_order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("ex1_orders.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("ex1_products.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items", lazy="raise_on_sql")
    product: Mapped["Product"] = relationship(back_populates="order_items", lazy="raise_on_sql")


async def get_customer_order_summary(session: AsyncSession, customer_id: int) -> dict:
    """
    Eagerly loads Customer -> Orders -> OrderItems -> Product via nested selectinload.
    Executes in exactly 4 queries regardless of the number of orders or items.
    """
    stmt = (
        select(Customer)
        .options(
            selectinload(Customer.orders)
            .selectinload(Order.items)
            .selectinload(OrderItem.product)
        )
        .where(Customer.id == customer_id)
    )

    result = await session.execute(stmt)
    customer = result.scalar_one_or_none()
    if not customer:
        raise ValueError(f"Customer {customer_id} not found")

    orders_summary = []
    total_lifetime_spend = 0.0

    for order in customer.orders:
        order_total = sum(item.quantity * item.product.unit_price for item in order.items)
        total_lifetime_spend += order_total
        orders_summary.append({
            "order_code": order.order_code,
            "item_count": sum(item.quantity for item in order.items),
            "order_total": round(order_total, 2),
        })

    return {
        "customer_id": customer.id,
        "name": customer.name,
        "lifetime_spend": round(total_lifetime_spend, 2),
        "orders": orders_summary,
    }
```

---

## Exercise 2: Safe `contains_eager` with Dynamic Filtering & Pagination

### Architectural Scenario
In an enterprise project management tool, users can list `Departments` and view only the active `Projects` that match a minimum budget threshold. If `joinedload` were used with a `.where()` clause, it would generate duplicate joins or ignore the filter. If pagination (`limit`/`offset`) were applied to a joined query, it would slice joined rows instead of departments.

### Requirements
1. Implement a function `get_departments_with_filtered_projects(session, min_budget, limit, offset)`:
   - Filters projects having `budget >= min_budget`.
   - Uses `contains_eager` to attach the filtered projects to each department in memory.
   - Accurately respects `limit` and `offset` on the `Department` entity level using a subquery or two-phase selection.

### Verified Reference Solution

```python
from sqlalchemy import select, func, and_
from sqlalchemy.orm import contains_eager

async def get_departments_with_filtered_projects(
    session: AsyncSession, min_budget: int, limit: int = 10, offset: int = 0
) -> List[Department]:
    """
    Two-phase query:
    Phase 1: Subquery fetches paginated Department IDs that have qualifying projects.
    Phase 2: Performs an INNER JOIN on only those IDs and populates using contains_eager.
    """
    # 1. Subquery to retrieve paginated Department IDs
    id_subquery = (
        select(Department.id)
        .join(Department.projects)
        .where(Project.budget >= min_budget)
        .group_by(Department.id)
        .order_by(Department.id)
        .limit(limit)
        .offset(offset)
        .scalar_subquery()
    )

    # 2. Main query joining only matching departments and projects
    stmt = (
        select(Department)
        .join(Department.projects)
        .where(
            and_(
                Department.id.in_(id_subquery),
                Project.budget >= min_budget,
            )
        )
        .options(contains_eager(Department.projects))
        .order_by(Department.id)
    )

    result = await session.execute(stmt)
    return list(result.unique().scalars().all())
```

---

## Exercise 3: Async Generic Repository & Unit of Work (UoW) Pattern

### Architectural Scenario
To decouple domain logic from database access and prevent leaky session abstractions in FastAPI controllers, design a production-grade **Async Unit of Work (UoW)** pattern supporting nested transaction savepoints.

### Requirements
1. Define an abstract or generic `AsyncRepository` interface for CRUD operations.
2. Define an `AsyncUnitOfWork` context manager that:
   - Manages the lifecycle of an `AsyncSession`.
   - Exposes transactional boundaries (`async with uow:`).
   - Automatically commits on successful exit.
   - Automatically rolls back on uncaught exceptions.
   - Exposes a `savepoint()` context manager for nested `begin_nested()` calls.

### Verified Reference Solution

```python
from typing import TypeVar, Generic, Type, Optional, List
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

T = TypeVar("T", bound=Base)

class AsyncRepository(Generic[T]):
    """Generic CRUD repository operating against an injected AsyncSession."""
    def __init__(self, model_class: Type[T], session: AsyncSession):
        self.model_class = model_class
        self.session = session

    async def get_by_id(self, entity_id: int) -> Optional[T]:
        return await self.session.get(self.model_class, entity_id)

    async def list_all(self) -> List[T]:
        stmt = select(self.model_class)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def add(self, entity: T) -> None:
        self.session.add(entity)

    async def delete(self, entity: T) -> None:
        await self.session.delete(entity)


class AsyncUnitOfWork:
    """
    Manages transaction lifecycle and aggregates repositories.
    Guarantees atomic commit or rollback across multi-repository operations.
    """
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self.session: Optional[AsyncSession] = None

    async def __aenter__(self):
        self.session = self._session_factory()
        # Repositories are instantiated with the active session
        self.customers = AsyncRepository(Customer, self.session)
        self.orders = AsyncRepository(Order, self.session)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                await self.rollback()
            else:
                await self.commit()
        finally:
            if self.session:
                await self.session.close()

    async def commit(self) -> None:
        if self.session:
            await self.session.commit()

    async def rollback(self) -> None:
        if self.session:
            await self.session.rollback()

    @asynccontextmanager
    async def savepoint(self):
        """Creates a nested transaction (SAVEPOINT)."""
        if not self.session:
            raise RuntimeError("Unit of Work has not been started.")
        async with self.session.begin_nested() as svpt:
            yield svpt
```

---
*Completing these exercises ensures production-ready architectural mastery of asynchronous database workflows.*
