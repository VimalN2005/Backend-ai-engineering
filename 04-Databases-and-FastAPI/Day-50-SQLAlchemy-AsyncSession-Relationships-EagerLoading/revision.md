# Day 50: Rapid Revision & Cheat Sheet - Async SQLAlchemy & Relationships

A high-density 5-minute reference sheet for **SQLAlchemy 2.0 AsyncSession**, declarative relationship mappings, and eager loading strategies.

---

## 1. Engine & Session Boilerplate

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# 1. Engine Initialization
engine = create_async_engine(
    "postgresql+asyncpg://user:pass@localhost:5432/dbname",
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
    echo=False,
)

# 2. Session Factory - ALWAYS set expire_on_commit=False in async!
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # CRITICAL: Prevents MissingGreenlet post-commit
    autoflush=False,
)

# 3. Idiomatic Transaction Execution
async with async_session_factory() as session:
    async with session.begin():
        session.add(entity)
        # Automatic commit on exit; automatic rollback on exception
```

---

## 2. Relationship Definition Cheatsheet

```python
from typing import List, Optional
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[int] = mapped_column(primary_key=True)

    # 1:N Collection
    children: Mapped[List["Child"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan", # ORM orphan removal
        passive_deletes=True,         # Leverages DB ON DELETE CASCADE
        lazy="raise_on_sql",          # Enforces explicit eager loading
    )

class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"))

    # M:1 Scalar
    parent: Mapped["Parent"] = relationship(
        back_populates="children",
        lazy="raise_on_sql",
    )
```

---

## 3. Eager Loading Decision Matrix

```
                      Relationship Type
                             |
         +-------------------+-------------------+
         |                                       |
    Scalar (M:1 or 1:1)                  Collection (1:N or M:N)
         |                                       |
   Use joinedload()                        Is there a WHERE
         |                                  on child table?
   (1 SQL query with                             |
    LEFT OUTER JOIN)                   +---------+---------+
                                       |                   |
                                      YES                  NO
                                       |                   |
                             Use contains_eager()    Use selectinload()
                             (hydrates from .join()) (2 queries via WHERE IN)
```

### Quick Code Reference
```python
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload, contains_eager

# 1. Collection (1:N, M:N) -> selectinload
stmt = select(Parent).options(selectinload(Parent.children))

# 2. Scalar (M:1, 1:1) -> joinedload
stmt = select(Child).options(joinedload(Child.parent))

# 3. Filtered Join -> contains_eager
stmt = (
    select(Parent)
    .join(Parent.children)
    .where(Child.status == "ACTIVE")
    .options(contains_eager(Parent.children))
)

# 4. Nested Loading -> Chained loaders
stmt = select(Company).options(
    selectinload(Company.departments).selectinload(Department.projects)
)
```

---

## 4. The `MissingGreenlet` Troubleshooting Checklist

| Symptom | Root Cause | Instant Fix |
| :--- | :--- | :--- |
| `MissingGreenlet: await_only() can only be called from within a greenlet` on property read | Accessing an unloaded relationship synchronously (`user.orders`). | Add `selectinload(User.orders)` or `joinedload(User.orders)` to the query. |
| `MissingGreenlet` after `await session.commit()` on scalar attribute read | `expire_on_commit=True` expired all loaded fields on commit. | Configure `expire_on_commit=False` in `async_sessionmaker`. |
| `InvalidRequestError: 'User.orders' is not available due to lazy='raise'` | `lazy="raise"` proactively blocked an implicit synchronous lazy query. | Add eager loading loader option to the executing query. |
| `DetachedInstanceError` in background task | Model passed to another thread or coroutine after session was closed. | Pass entity IDs (integers) to background tasks; create a fresh session inside the task. |

---

## 5. Streaming Large Datasets (`stream_scalars`)

```python
# Fetches rows via server-side cursors in constant O(1) memory
async with async_session_factory() as session:
    stmt = select(Transaction).order_by(Transaction.timestamp)
    stream = await session.stream_scalars(stmt)
    
    async for tx in stream:
        process_transaction(tx)
```

---

## 6. One-Minute Flashcard Review

1. **What is greenlet in SQLAlchemy 2.0?** A lightweight coroutine context switcher that lets synchronous Core compilers run on top of asynchronous DBAPI drivers (`asyncpg`).
2. **Why does lazy loading fail in async?** Python property access (`obj.attr`) cannot be awaited. Emitting a query requires non-blocking socket I/O, which cannot be initiated from synchronous property reads without an active greenlet.
3. **When should you avoid `joinedload`?** On One-to-Many and Many-to-Many collections, because it generates a Cartesian product of duplicate parent rows and corrupts `LIMIT`/`OFFSET` queries.
4. **Why is `selectinload` preferred for collections?** It issues exactly 2 queries ($O(1)$) using an `IN (...)` clause, avoiding Cartesian fan-out and respecting pagination.
5. **What does `contains_eager` do?** Tells SQLAlchemy that an existing manual `.join()` has already loaded related columns into the result set, hydrating the relationship without a duplicate join.
