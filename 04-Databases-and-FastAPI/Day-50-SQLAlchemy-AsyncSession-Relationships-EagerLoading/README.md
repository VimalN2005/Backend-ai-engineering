# Day 50: SQLAlchemy 2.0 AsyncSession, Relationships & Eager Loading

Welcome to Day 50 of the Enterprise Backend & AI Engineering curriculum. Today, we step into the core architecture of asynchronous Object-Relational Mapping (ORM) using **SQLAlchemy 2.0**. Modern asynchronous backend frameworks such as FastAPI, BlackSheep, and Litestar rely heavily on non-blocking database communication to achieve massive concurrency. However, transitioning an ORM from synchronous blocking execution to an asynchronous event loop introduces unique architectural challenges—most notably around relationship resolution, deferred loading, and session identity maps.

This guide provides a comprehensive 15-point architectural breakdown of SQLAlchemy 2.0's async subsystem, relationship mechanics, and eager loading optimization patterns.

---

## 1. The Async Paradigm in SQLAlchemy 2.0: The Greenlet Concurrency Bridge

Traditional Python database drivers (DBAPI 2.0, PEP 249) operate synchronously: executing a query blocks the operating system thread until the database server returns the results over the socket. In contrast, modern async drivers (`asyncpg` for PostgreSQL, `aiosqlite` for SQLite) yield execution back to the Python `asyncio` event loop while waiting for database socket I/O.

SQLAlchemy Core was historically designed around a strictly synchronous execution flow: compiling AST statements, cursor management, and type conversions happen linearly. Re-architecting SQLAlchemy from the ground up for native `async/await` would have required duplicating the entire compiler, dialect, and expression pipeline.

Instead, SQLAlchemy 2.0 solves this via a sophisticated concurrency bridge powered by **`greenlet`**:

```
+---------------------------------------------------------------------------------+
|                                asyncio Event Loop                               |
|                                                                                 |
|  +--------------------+        +---------------------+       +---------------+  |
|  | Coroutine Task A   |        | AsyncSession        |       | Coroutine     |  |
|  | (FastAPI Endpoint) | -----> | (execute/commit)    |       | Task B        |  |
|  +--------------------+        +----------+----------+       +---------------+  |
|                                           |                                     |
|                                 greenlet.spawn()                                |
|                                           v                                     |
|                                +---------------------+                          |
|                                | Synchronous Core    |                          |
|                                | Compiler & Mapper   |                          |
|                                +----------+----------+                          |
|                                           |                                     |
|                                    await_only()                                 |
|                                (switches to loop)                               |
|                                           v                                     |
|                                +---------------------+                          |
|                                | Async DBAPI Driver  |                          |
|                                | (asyncpg/aiosqlite) |                          |
|                                +----------+----------+                          |
|                                           |                                     |
+-------------------------------------------|-------------------------------------+
                                            v (Non-blocking Socket I/O)
                                 [ PostgreSQL / SQLite ]
```

### How the Bridge Works
1. An async method such as `await session.execute(stmt)` or `await session.commit()` is invoked.
2. The `AsyncSession` uses `greenlet.spawn()` to initiate a lightweight coroutine-like greenlet context that runs the synchronous SQLAlchemy Core engine and compiler.
3. When the synchronous Core engine needs to interact with the database (e.g., executing a prepared statement or fetching rows), it invokes `await_only()`.
4. `await_only()` switches the execution context back from the greenlet to the primary `asyncio` event loop, delegating the physical non-blocking socket read/write to the underlying async DBAPI driver (`asyncpg` or `aiosqlite`).
5. Once the DBAPI driver completes the network operation, execution context resumes inside the greenlet to finish row hydrations and state transitions.

This design enables 100% code reuse of SQLAlchemy's battle-tested statement compilation and type mapping while providing true non-blocking asynchronous execution.

---

## 2. `AsyncEngine` Architecture & Lifecycle

The `AsyncEngine` proxies an underlying synchronous `Engine` wrapped in an async adapter.

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

engine: AsyncEngine = create_async_engine(
    "postgresql+asyncpg://app_user:app_pass@localhost:5432/enterprise_db",
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)
```

### Key Parameters for High-Concurrency Production
- **`pool_size`**: The baseline number of persistent database connections maintained in the pool (`AsyncAdaptedQueuePool`).
- **`max_overflow`**: The maximum number of auxiliary connections permitted above `pool_size` during unexpected traffic spikes.
- **`pool_timeout`**: The number of seconds a task will wait for an available connection from the pool before raising `TimeoutError`.
- **`pool_recycle`**: Re-establishes connections that have been open for longer than this duration (in seconds). Prevents stale connection drops caused by stateful firewalls or database timeouts.
- **`pool_pre_ping=True`**: Issues a lightweight `SELECT 1` heartbeat check before returning a connection to the caller. This eliminates "server closed the connection unexpectedly" crashes after database restarts or failovers.

### Engine Teardown
In long-running services (FastAPI/worker daemons), always cleanly close the pool during application shutdown:
```python
await engine.dispose()
```
Failing to call `dispose()` leaves connection sockets open, eventually exhausting the target database server's connection limits (`max_connections`).

---

## 3. `AsyncSession` & `async_sessionmaker`

The `AsyncSession` implements the **Unit of Work** and **Identity Map** patterns in asynchronous mode.

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
```

### Why `expire_on_commit=False` is Mandatory in Async Python
In synchronous SQLAlchemy, `session.commit()` by default expires all attributes on loaded model instances. The next time you access an attribute (e.g., `user.username` or `order.items`), synchronous SQLAlchemy transparently emits a fresh `SELECT` query to reload the state from the database.

In an **asynchronous** context, accessing a Python attribute (`instance.field`) is a synchronous syntax operation (`__getattr__`). Python attributes cannot be `await`ed! If `expire_on_commit=True`, accessing any attribute on an instance after committing raises:

```
sqlalchemy.exc.MissingGreenlet: await_only() can only be called from within a greenlet
```

Setting `expire_on_commit=False` preserves all loaded scalar and relationship attributes in memory after the commit, allowing safe serialization and passing to response schemas.

---

## 4. Declarative Relationships in SQLAlchemy 2.0

SQLAlchemy 2.0 introduces fully type-safe declarative models leveraging PEP 484 and PEP 593 through `Mapped[...]` and `mapped_column()`.

```python
from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy import String, Integer, ForeignKey, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc)
    )

    # One-to-Many Relationship
    employees: Mapped[List["Employee"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        lazy="raise",  # Strictly prohibit silent lazy loads
    )

class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)

    # Many-to-One Relationship
    company: Mapped["Company"] = relationship(
        back_populates="employees",
        lazy="raise",
    )
```

### Relationship Types
- **One-to-Many (`1:N`)**: Declared using `Mapped[List["Child"]]` on the parent, mapped with `relationship(back_populates="...")`.
- **Many-to-One (`M:1`)**: Declared using `Mapped["Parent"]` on the child, paired with a `ForeignKey` column.
- **One-to-One (`1:1`)**: Declared identically to Many-to-One or One-to-Many but typed as `Mapped[Optional["Profile"]]` with a unique constraint on the foreign key column.
- **Many-to-Many (`M:N`)**: Requires a secondary association table:
  ```python
  from sqlalchemy import Table, Column

  project_employee_assoc = Table(
      "project_employee_association",
      Base.metadata,
      Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
      Column("employee_id", ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
  )
  ```

---

## 5. Foreign Keys, Referential Integrity & `back_populates`

Understanding the division of labor between the database engine and the ORM is critical for production reliability:

| Responsibility | Database Level (`ForeignKey(..., ondelete="...")`) | ORM Level (`relationship(..., cascade="...")`) |
| :--- | :--- | :--- |
| **Enforcement** | Engine level (Foreign Key constraint in PostgreSQL/SQLite) | Python level in memory before emitting SQL |
| **Cascade Deletes** | `ondelete="CASCADE"` triggers automatic row deletion inside PostgreSQL when parent row is deleted. Fast, single DB operation. | `cascade="all, delete-orphan"` loads children into memory and issues individual `DELETE` statements or manages association links. |
| **Orphan Removal** | Not supported by raw relational foreign keys. | Supported via `delete-orphan`. Deletes a child when it is removed from the parent's collection (`parent.children.remove(child)`). |

### `back_populates` vs `backref`
Always use `back_populates` in SQLAlchemy 2.0. `backref` dynamically mutates the target class at runtime, breaking static type checkers (`mypy`, `pyright`). `back_populates` requires explicit declarations on both sides of the relationship, ensuring complete static analysis and autocompletion.

---

## 6. The Lazy Loading Dilemma in Async Contexts: The `MissingGreenlet` Anatomy

In traditional synchronous ORM development, developers often wrote:

```python
# Synchronous Code (Antipattern in High Scale, Disastrous in Async)
users = session.query(User).all()
for user in users:
    print(user.profile.bio)  # Synchronous implicit query triggered on access!
```

### Why Lazy Loading Fails in Async
When an unloaded relationship attribute is accessed on an instance:
1. Python calls `__get__` on the descriptor.
2. The ORM discovers the collection/entity is not in memory.
3. The ORM attempts to emit an immediate `SELECT` statement synchronously to hydrate the attribute.
4. In an `AsyncSession`, emitting a query requires an event loop context. Because property access (`user.profile`) is synchronous and cannot be `await`ed, SQLAlchemy checks if it is inside an active greenlet bridge.
5. If accessed outside an active `await` call (such as during template rendering, JSON serialization, or FastAPI Pydantic schema validation), it immediately crashes with:

```text
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.
This is likely due to lazy loading occurring inside an async context where the relationship
has not been eagerly loaded.
```

### The Solution: `lazy="raise"`
To prevent silent runtime crashes, configure all relationships with `lazy="raise"` or `lazy="raise_on_sql"`:
```python
profile: Mapped["Profile"] = relationship(back_populates="user", lazy="raise")
```
This forces developers to deliberately specify eager loading strategies in their queries. Any attempt to access an un-eagerly-loaded relationship raises an explicit error immediately at development time.

---

## 7. The $N+1$ Query Problem Anatomy

The $N+1$ query problem occurs when an application executes 1 query to fetch $N$ parent records, followed by $N$ subsequent individual queries to fetch related child records for each parent.

```
Query 1: SELECT id, name FROM companies;  --> Returns 50 rows
Query 2: SELECT * FROM employees WHERE company_id = 1;
Query 3: SELECT * FROM employees WHERE company_id = 2;
...
Query 51: SELECT * FROM employees WHERE company_id = 50;
```

### Mathematical & Network Cost
If network latency between the application server and the database server is $L = 2\text{ ms}$:
- Total Round Trip Latency = $(1 + N) \times L$.
- For $N = 500$ entities: Total latency $\approx (1 + 500) \times 2\text{ ms} = 1002\text{ ms}$ (Over 1 second spent purely waiting for network hops!).
- Connection pool saturation: 501 separate database queries lock pooled connections, preventing other concurrent web requests from progressing.

Eager loading strategies eliminate this bottleneck by batching or joining queries into $O(1)$ operations.

---

## 8. Eager Loading Strategy 1: `joinedload`

`joinedload` instructs SQLAlchemy to load the related entity within the **same query** using an SQL `LEFT OUTER JOIN`.

```python
from sqlalchemy import select
from sqlalchemy.orm import joinedload

stmt = (
    select(Employee)
    .options(joinedload(Employee.company))
    .where(Employee.id == 42)
)
result = await session.execute(stmt)
employee = result.unique().scalar_one()
```

### Generated SQL
```sql
SELECT 
    employees.id, employees.company_id, employees.email, employees.full_name,
    companies_1.id AS id_1, companies_1.name AS name_1, companies_1.created_at AS created_at_1
FROM employees 
LEFT OUTER JOIN companies AS companies_1 ON companies_1.id = employees.company_id 
WHERE employees.id = 42;
```

### When to Use `joinedload`
- **Many-to-One (`M:1`)** and **One-to-One (`1:1`)** relationships.
- Fetching a single entity where loading its direct parent or profile in a single round-trip is optimal.

### Warning: Cartesian Product Explosion on One-to-Many Collections
If a `Company` has 100 `Employees`, and each `Employee` has 10 `Projects`, using `joinedload(Company.employees).joinedload(Employee.projects)` generates a Cartesian product:
$$\text{Rows Returned} = 1 \times 100 \times 10 = 1000\text{ rows}$$
The company data and employee data are redundantly repeated across all 1,000 rows, wasting CPU time in deserialization and flooding network bandwidth.

---

## 9. Eager Loading Strategy 2: `selectinload`

`selectinload` is the **gold standard** for loading collections (One-to-Many and Many-to-Many) in SQLAlchemy 2.0.

Instead of performing a massive join, `selectinload` emits **two separate queries**:
1. The primary query fetches the parent records.
2. The second query fetches all matching child records using an SQL `IN (...)` clause referencing the primary keys of the parents retrieved in step 1.

```python
from sqlalchemy import select
from sqlalchemy.orm import selectinload

stmt = (
    select(Company)
    .options(selectinload(Company.employees))
    .where(Company.name.like("Acme%"))
)
result = await session.execute(stmt)
companies = result.scalars().all()
```

### Generated SQL
```sql
-- Query 1: Fetch parents
SELECT companies.id, companies.name, companies.created_at 
FROM companies 
WHERE companies.name LIKE 'Acme%';

-- Query 2: Fetch all children in one single batch
SELECT employees.id, employees.company_id, employees.email, employees.full_name 
FROM employees 
WHERE employees.company_id IN (1, 2, 5, 8, 12);
```

### Advantages of `selectinload`
- **Zero Cartesian Product**: Each child row is transferred exactly once.
- **$O(1)$ Queries**: Exactly 2 queries are emitted regardless of whether there are 10 parents or 10,000 parents.
- **Memory Efficient**: Avoids massive redundant data transfer over wire protocols.
- **Ideal for Pagination**: Using `LIMIT` and `OFFSET` on the primary query works properly without corrupting row counts (unlike `joinedload`, where a `LIMIT 10` would limit joined rows rather than parent entities).

---

## 10. Eager Loading Strategy 3: `subqueryload` & `contains_eager`

### `subqueryload` (Legacy / Specialized)
`subqueryload` emits a second query that re-executes the original query as an embedded subquery:
```sql
SELECT employees.id, employees.company_id, employees.email 
FROM employees 
WHERE employees.company_id IN (
    SELECT companies.id FROM companies WHERE companies.name LIKE 'Acme%'
);
```
*Note*: In modern SQLAlchemy 2.0, `selectinload` has largely superseded `subqueryload` because `selectinload` does not require re-executing complex `WHERE` or `GROUP BY` logic on the child query.

### `contains_eager` (Filtering on Related Entities)
A common beginner mistake is using `joinedload` with a `.where()` filter on the joined table:
```python
# INCORRECT: joinedload ignores where clause or duplicates joins!
stmt = (
    select(Company)
    .join(Company.employees)
    .where(Employee.email.endswith("@google.com"))
    .options(joinedload(Company.employees)) # BUG: Adds a second redundant LEFT JOIN!
)
```
When you manually `.join()` a relationship to filter or sort by child columns, use **`contains_eager`**. This informs SQLAlchemy that the joined columns already exist in the SQL projection and instructs it to populate the relationship collection directly:

```python
from sqlalchemy.orm import contains_eager

stmt = (
    select(Company)
    .join(Company.employees)
    .where(Employee.email.endswith("@google.com"))
    .options(contains_eager(Company.employees)) # Reuses the existing INNER JOIN
)
result = await session.execute(stmt)
companies = result.unique().scalars().all()
```

---

## 11. Async Unit of Work (UoW) & Flushing Mechanics

The Unit of Work pattern maintains a list of objects affected by a business transaction and coordinates the writing of changes:

```
[ New Entities ]      --> session.add(entity)    --> Marked as Pending
[ Modified Entities ] --> entity.field = "value" --> Marked as Dirty
[ Removed Entities ]  --> session.delete(entity) --> Marked as Deleted
                                  |
                                  v
                         await session.flush()
   * Computes dependency graph (Topological Sort)
   * Inserts parents first to acquire Primary Keys
   * Inserts foreign keys into child rows
   * Emits INSERT / UPDATE / DELETE statements
   * Flushes to DB transaction buffer (Does NOT commit!)
                                  |
                                  v
                        await session.commit()
   * Finalizes database transaction on the server
```

### `session.flush()` vs `session.commit()`
- `await session.flush()`: Emits SQL statements to the database transaction, populating autoincremented primary keys and checking DB-level constraints without closing or finalizing the transaction.
- `await session.commit()`: Flushes any remaining pending changes, commits the database transaction (`COMMIT`), releases locks, and returns the connection to the connection pool.

---

## 12. Context Managers & Safe Transaction Boundaries

Always manage async session transactions using structured context managers.

### The Recommended Idiom: `async with session.begin():`
```python
async with async_session_factory() as session:
    async with session.begin():
        user = User(email="staff@enterprise.io", full_name="Staff Engineer")
        session.add(user)
        # Automatic commit on exit of begin() block
        # Automatic rollback on unhandled exception
```

### Savepoints with `begin_nested()`
When executing risky sub-operations that should not abort the entire parent transaction, use database savepoints (`SAVEPOINT`):

```python
async with session.begin():
    # Parent transaction starts
    session.add(audit_log)
    
    try:
        async with session.begin_nested(): # SAVEPOINT svpt_1
            charge_credit_card(user)
    except PaymentFailedError:
        # Rolls back to svpt_1; audit_log is preserved!
        user.status = "payment_failed"
    
    # Outer transaction commits cleanly
```

---

## 13. Dynamic / Stream Loading for Massive Datasets

When querying hundreds of thousands of records, loading everything into memory via `.scalars().all()` causes memory spikes and triggers Garbage Collection pauses.

SQLAlchemy 2.0 provides **async streaming cursors**:

```python
stmt = select(LogRecord).order_by(LogRecord.timestamp.desc())

# stream_scalars() uses server-side cursors to fetch rows in batches
async with session.begin():
    async for record in await session.stream_scalars(stmt):
        process_log_record(record)
```
This guarantees an $O(1)$ constant application memory footprint regardless of dataset size.

---

## 14. Async Cascades and Orphan Removal

Cascading rules define what happens to child entities when a parent entity is modified or removed:

```python
class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    
    children: Mapped[List["Child"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan", # ORM lifecycle cascade
        passive_deletes=True,         # Delegates deletion to DB ondelete="CASCADE"
    )
```

### Common Cascade Directives
- **`save-update`**: Automatically adds referenced objects to the session when the parent is added (`session.add(parent)` also adds `parent.children`).
- **`merge`**: Merges relationship state when `session.merge()` is called.
- **`delete`**: Emits delete operations for all children when the parent is deleted.
- **`delete-orphan`**: Emits a delete operation for a child whenever it is disconnected from its parent (`parent.children.remove(child)`).
- **`all, delete-orphan`**: The standard shorthand combination for parent-child aggregate roots.
- **`passive_deletes=True`**: Prevents SQLAlchemy from loading child objects into memory prior to emitting a parent delete, relying instead on the database's foreign key `ON DELETE CASCADE`.

---

## 15. Production Best Practices & FastAPI Integration Checklist

### FastAPI Dependency Injection Architecture
In production FastAPI applications, provide sessions per-request with guaranteed cleanup:

```python
from typing import AsyncGenerator
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

### Production Checklist
1. **Never use `lazy="select"`**: Configure `lazy="raise"` on all model relationships to catch un-eagerly-loaded queries immediately during CI/testing.
2. **Always set `expire_on_commit=False`**: Prevents `MissingGreenlet` errors when serializing response models after transactions commit.
3. **Use `selectinload` for Collections**: Choose `selectinload` for 1:N and M:N relationships to avoid Cartesian blowups.
4. **Use `joinedload` for Single Entities**: Choose `joinedload` for M:1 and 1:1 foreign keys.
5. **Use `contains_eager` for Filtered Joins**: Never combine `.where()` on joined tables with `joinedload`; use `contains_eager` instead.
6. **Pool Health Verification**: Enable `pool_pre_ping=True` and configure realistic `pool_recycle` timeouts (e.g., 1800s).
7. **Clean Engine Shutdown**: Hook `engine.dispose()` into the application's lifespan shutdown handler.

---
*Next Step: Explore `example_01.py` for a fully functional enterprise async pipeline, and `example_02.py` for eager loading performance benchmarks.*
