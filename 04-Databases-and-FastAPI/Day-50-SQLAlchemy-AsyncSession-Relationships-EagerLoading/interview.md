# Day 50: Senior & Staff Technical Interview Mastery

This guide presents the top 5 advanced architectural interview questions focusing on **SQLAlchemy 2.0 AsyncSession, Greenlet Concurrency, Relationships, and Eager Loading Strategies**.

---

### Question 1: How does SQLAlchemy 2.0 execute async operations using `greenlet` under the hood, and why is `MissingGreenlet` raised on lazy attribute access?

#### Architectural Answer
SQLAlchemy 2.0 was designed to support async DBAPI drivers (`asyncpg`, `aiosqlite`) while preserving the entire existing codebase for Core statement compilation, dialects, type mapping, and the Unit of Work identity map.

Rather than creating a separate asynchronous compilation and ORM pipeline, SQLAlchemy utilizes **`greenlet`** as a cooperative context-switching bridge:
1. When an asynchronous entry point (such as `await session.execute(stmt)`) is called, `AsyncSession` uses `greenlet.spawn()` to initiate a greenlet coroutine.
2. Inside this greenlet, synchronous SQLAlchemy Core runs unchanged.
3. When the synchronous Core compiler is ready to talk to the database driver, it calls `await_only(driver_coroutine)`.
4. `await_only()` pauses the greenlet and switches context back to the primary `asyncio` event loop thread, handing the coroutine to the event loop.
5. Once the physical network packet returns from PostgreSQL/SQLite, the event loop resumes the greenlet with the result.

#### Why `MissingGreenlet` Occurs
In Python, attribute access (`user.orders`) uses the synchronous `__getattr__` or descriptor `__get__` protocol. Synchronous property access cannot be prepended with `await`.

When a model has a relationship configured with default lazy loading (`lazy="select"`), accessing `user.orders` prompts SQLAlchemy to emit a database query on the spot. If this property access occurs in ordinary Python code (such as inside a Pydantic serializer or API route handler) outside an active `greenlet.spawn()` call, SQLAlchemy calls `await_only()`. Because there is no active greenlet to switch away from, it immediately aborts with:
```text
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.
```

#### Staff-Level Recommendation
In production async applications:
1. Configure `lazy="raise"` or `lazy="raise_on_sql"` on all declarative relationships.
2. Require all queries to explicitly define eager-loading loader strategies (`selectinload`, `joinedload`, `contains_eager`).

---

### Question 2: Compare and contrast `joinedload`, `selectinload`, `subqueryload`, and `contains_eager`. When does each strategy excel or degrade?

#### Architectural Comparison Table

| Strategy | SQL Emission Pattern | Number of Queries | Best Used For | Primary Pitfall |
| :--- | :--- | :--- | :--- | :--- |
| **`joinedload`** | `LEFT OUTER JOIN` in main query | Exactly 1 | Many-to-One (`M:1`), One-to-One (`1:1`) | Cartesian product explosion on 1:N collections; breaks `LIMIT`/`OFFSET` |
| **`selectinload`** | `SELECT ... WHERE fk IN (...)` | Exactly 2 ($1 + \text{depth}$) | One-to-Many (`1:N`), Many-to-Many (`M:N`) | Large `IN` lists in poorly tuned databases (mitigated by batch chunking) |
| **`subqueryload`** | `SELECT ... WHERE fk IN (SELECT id ...)` | Exactly 2 | Legacy 1.4 codebases | Re-executes the entire parent `WHERE`/`GROUP BY` clause; superseded by `selectinload` |
| **`contains_eager`** | No extra SQL; populates from explicit `.join()` | 0 additional | Filtered / Sorted joins (`.where(Child.status == 'A')`) | Requires developer to manually formulate the `.join()` accurately |

#### Staff-Level Trade-Off Analysis
- **`joinedload`**: While it achieves a single database round-trip, using it on collections creates redundant data transfer. For example, joining a Customer with 10 Orders where each Order has 10 Items produces 100 rows, each redundantly transmitting Customer and Order column strings. Furthermore, applying `LIMIT 10` on a joined query limits the joined rows rather than the parent entities, resulting in truncated relationships.
- **`selectinload`**: Resolves the pagination issue completely because `LIMIT 10` applies cleanly to the parent query. The second query fetches children only for those 10 parents using an `IN` clause.
- **`contains_eager`**: Essential when business logic requires filtering parent rows based on child criteria while hydrating the child collection simultaneously. Using `joinedload` in this situation either introduces a duplicate redundant join or ignores the filter.

---

### Question 3: Why is `expire_on_commit=False` standard in async web frameworks, and what concurrency implications does it have on stale data?

#### Architectural Answer
In synchronous SQLAlchemy, when a transaction commits (`session.commit()`), all objects in the identity map are automatically expired. The attributes remain accessible because synchronous attribute reads silently re-query the database.

In asynchronous applications:
1. Committing with `expire_on_commit=True` marks all attributes as expired.
2. When the web framework serializes the returned model (e.g., FastAPI serializing an ORM instance to a Pydantic response schema), it reads `user.id` or `user.email`.
3. Reading an expired attribute requires a database trip. Because the session transaction is already closed, this triggers an asynchronous refresh inside a synchronous serialization block, raising `MissingGreenlet` or `DetachedInstanceError`.
4. Setting `expire_on_commit=False` retains all currently loaded scalar and relationship attributes in memory after the commit, allowing safe serialization without database round-trips.

#### Concurrency & Stale Data Implications
- **Stale Reads within the Same Session**: If another concurrent process updates the row in the database between your commit and subsequent reads in the same session, the local instance retains its in-memory snapshot.
- **Mitigation in Web Architecture**: In typical request-response lifecycles (e.g., FastAPI), an `AsyncSession` lives for the duration of a single HTTP request (10–50ms). Retaining committed state in memory for the final serialization step presents negligible risk of stale data while preventing catastrophic async crashes.

---

### Question 4: Explain the difference between ORM-level cascades (`cascade="all, delete-orphan"`) and DB-level cascades (`ondelete="CASCADE"`). How do they behave during bulk deletions?

#### Architectural Answer

```
ORM-Level Cascade (cascade="all, delete-orphan")
Parent Instance ---> ORM loads children into memory ---> Emits individual DELETEs
Cost: High memory, N queries, fires ORM hooks.

Database-Level Cascade (ForeignKey(..., ondelete="CASCADE"))
Parent Row ---> PostgreSQL engine cascades deletion on disk
Cost: O(1) query, minimal memory, bypasses ORM lifecycle hooks.
```

1. **Database-Level (`ForeignKey("parent.id", ondelete="CASCADE")`)**:
   - Handled directly by the database engine (PostgreSQL/SQLite).
   - When a parent row is deleted (`DELETE FROM parents WHERE id = 1`), the database engine's storage manager automatically deletes all referencing child rows.
   - Extremely fast and operates in a single database statement.
   - **Trade-off**: The ORM does not know that child objects in its identity map have been deleted unless `passive_deletes=True` is configured.

2. **ORM-Level (`cascade="all, delete-orphan"`)**:
   - Handled by SQLAlchemy in Python memory.
   - When a parent is marked for deletion or a child is removed from the collection (`parent.children.remove(child)`), SQLAlchemy explicitly issues `DELETE` statements for each child row.
   - Triggers Python-level ORM events (`before_delete`, `after_delete`).
   - Supports **orphan removal** (deleting children detached from the parent collection), which raw relational databases cannot do natively.

#### Production Best Practice for High Scale
Combine both:
```python
# Model Definition
class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    children: Mapped[List["Child"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,  # Informs ORM that DB engine handles physical ON DELETE CASCADE
    )

class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("parents.id", ondelete="CASCADE"), nullable=False
    )
```
Setting `passive_deletes=True` prevents SQLAlchemy from pre-fetching all child rows into application RAM before issuing the parent delete, letting PostgreSQL handle the deletion at disk speed while preserving ORM orphan removal in memory!

---

### Question 5: How do you design an asynchronous Repository and Unit of Work (UoW) pattern with SQLAlchemy 2.0 to prevent session leakage and decouple database access from business logic?

#### Architectural Answer
In enterprise backends, directly importing and querying `AsyncSession` inside FastAPI routes or domain services leads to:
- High coupling between HTTP routing and database schema definitions.
- Leaked sessions when exceptions occur mid-transaction.
- Difficulties in mocking database interactions during unit testing.

The solution is an **Async Unit of Work (UoW)** coordinating **Async Repositories**:

```
+-------------------------------------------------------------+
| FastAPI Route Handler / Use Case Layer                      |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
| AsyncUnitOfWork (Context Manager)                           |
| - __aenter__: Injects AsyncSession into Repositories        |
| - __aexit__: Atomic commit on success, rollback on error   |
| - savepoint(): Supports begin_nested() for sub-transactions |
+------------------------------+------------------------------+
                               |
            +------------------+------------------+
            v                                     v
+-----------------------+             +-----------------------+
| CustomerRepository    |             | OrderRepository       |
| (Async CRUD queries)  |             | (Async CRUD queries)  |
+-----------------------+             +-----------------------+
```

#### Core Implementation Principles
1. **Per-Request Scoping**: The Unit of Work is injected via FastAPI's `Depends()`. The session is instantiated on route entry and unconditionally closed on route exit.
2. **Repository Decoupling**: Repositories only receive the active `AsyncSession` from the UoW. They do not own or commit transactions; only the UoW issues `commit()` or `rollback()`.
3. **Atomic Boundaries**: Business operations spanning multiple repositories (e.g., deducting inventory and creating an order) succeed or fail as a single atomic unit.
4. **Nested Transactions (Savepoints)**: The UoW exposes `uow.savepoint()` wrapping `session.begin_nested()`, allowing partial rollbacks without invalidating the parent transaction.

---
*Senior interviewers look for candidates who understand the concurrency mechanics beneath the ORM abstraction.*
