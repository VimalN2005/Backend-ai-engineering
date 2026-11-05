# Day 50: Critical Async SQLAlchemy & Relationship Antipatterns

This document explores the top 7 architectural mistakes engineers make when working with **SQLAlchemy 2.0 AsyncSession**, declarative relationships, and eager loading strategies. Each antipattern includes its underlying failure mechanism, bad code vs remediated code, and production rules.

---

## 1. Relying on Default Lazy Loading in Async Code (`lazy="select"`)

### Failure Mechanism
In synchronous SQLAlchemy, relationships default to `lazy="select"`. Accessing `user.orders` triggers an on-demand SQL query via Python's descriptor protocol (`__getattr__`). 

In an async application, property access (`instance.relation`) is synchronous and cannot be awaited. When SQLAlchemy detects an unloaded relationship attribute being accessed outside an active greenlet bridge (such as inside a Pydantic serializer or Jinja template), it crashes with:
```text
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.
```

### Bad Practice
```python
# Model definition with default lazy loading
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    orders: Mapped[List["Order"]] = relationship(back_populates="user") # Default: lazy="select"

# Query execution
stmt = select(User).where(User.id == 10)
user = (await session.execute(stmt)).scalar_one()

# CRASH: Accessing user.orders synchronously triggers MissingGreenlet!
for order in user.orders:
    print(order.order_number)
```

### Remediated Practice
```python
# 1. Enforce explicit loading on the model using lazy="raise" or "raise_on_sql"
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    orders: Mapped[List["Order"]] = relationship(
        back_populates="user",
        lazy="raise_on_sql"  # Explicitly disallow implicit lazy queries
    )

# 2. Explicitly eager-load the relationship at the query call-site
stmt = (
    select(User)
    .options(selectinload(User.orders))
    .where(User.id == 10)
)
user = (await session.execute(stmt)).scalar_one()

# Safe: user.orders was pre-hydrated in memory
for order in user.orders:
    print(order.order_number)
```

---

## 2. Leaving `expire_on_commit=True` in `async_sessionmaker`

### Failure Mechanism
By default, SQLAlchemy's session factory has `expire_on_commit=True`. When `await session.commit()` succeeds, the session marks all persistent instances in its identity map as "expired". The next time any attribute (even a primitive scalar like `user.email`) is read, SQLAlchemy attempts to reload it from the database. In async mode, reading `user.email` cannot be awaited, immediately throwing `MissingGreenlet`!

### Bad Practice
```python
# Factory misconfiguration
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=True,  # BUG: Causes instances to expire after commit!
)

async with async_session_factory() as session:
    user = User(email="staff@enterprise.io", full_name="Staff Engineer")
    session.add(user)
    await session.commit()

# CRASH: Reading user.email outside the transaction triggers an async reload synchronously!
print(user.email)
```

### Remediated Practice
```python
# Always set expire_on_commit=False for async session factories
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Retains loaded instance state post-commit
    autoflush=False,
)

async with async_session_factory() as session:
    user = User(email="staff@enterprise.io", full_name="Staff Engineer")
    session.add(user)
    await session.commit()

# Safe: All attributes remain hydrated in memory
print(user.email)  # "staff@enterprise.io"
```

---

## 3. Using `joinedload` on Multiple One-to-Many Collections (Cartesian Product Blowup)

### Failure Mechanism
Using `joinedload` on two or more One-to-Many collections in a single query instructs SQLAlchemy to perform multiple SQL `LEFT OUTER JOIN`s. Relational database engines calculate the Cartesian product of these joins. If a `Customer` has 20 `Orders` and 20 `SupportTickets`, the query returns $20 \times 20 = 400$ rows for a single customer!

If you fetch 100 customers, the database returns 40,000 rows. The vast majority of the payload is duplicate parent data repeated hundreds of times.

### Bad Practice
```python
# Catastrophic Cartesian explosion: 100 customers * 20 orders * 20 tickets = 40,000 rows
stmt = (
    select(Customer)
    .options(
        joinedload(Customer.orders),
        joinedload(Customer.support_tickets),
    )
)
result = await session.execute(stmt)
customers = result.unique().scalars().all()
```

### Remediated Practice
```python
# Use selectinload for collections: Exactly 3 lightweight queries emitted!
# Query 1: SELECT * FROM customers; (100 rows)
# Query 2: SELECT * FROM orders WHERE customer_id IN (...); (2,000 rows)
# Query 3: SELECT * FROM support_tickets WHERE customer_id IN (...); (2,000 rows)
# Total rows transferred: 4,100 rows instead of 40,000!
stmt = (
    select(Customer)
    .options(
        selectinload(Customer.orders),
        selectinload(Customer.support_tickets),
    )
)
result = await session.execute(stmt)
customers = result.scalars().all()
```

---

## 4. Filtering on a Joined Table Using `joinedload` Instead of `contains_eager`

### Failure Mechanism
Developers frequently attempt to filter parent records based on child attributes by writing `.where(...)` combined with `joinedload()`. 

However, `joinedload()` is designed strictly for **loader strategies**, not for modifying query logic. SQLAlchemy generates an anonymous alias for the `joinedload` `LEFT OUTER JOIN`. Your `.where()` clause targets the base table, forcing SQLAlchemy to either add a redundant second join or ignore the eager load instructions.

### Bad Practice
```python
# BUG: Adds a redundant second LEFT JOIN and may produce unexpected SQL!
stmt = (
    select(Department)
    .join(Department.projects)
    .where(Project.budget > 100000)
    .options(joinedload(Department.projects)) # Redundant duplicate join!
)
result = await session.execute(stmt)
```

### Remediated Practice
```python
from sqlalchemy.orm import contains_eager

# Use contains_eager to tell SQLAlchemy that the .join() already loaded the columns!
stmt = (
    select(Department)
    .join(Department.projects)
    .where(Project.budget > 100000)
    .options(contains_eager(Department.projects)) # Hydrates relation directly from the join
)
result = await session.execute(stmt)
departments = result.unique().scalars().all()
```

---

## 5. Passing Detached ORM Instances to Background Tasks Without Eager Loading

### Failure Mechanism
In FastAPI and async services, developers often pass an ORM instance to a background task (e.g., Celery or FastAPI `BackgroundTasks`). Once the HTTP request finishes, the `AsyncSession` context manager closes the connection and detaches the instance. When the background task accesses an unloaded relationship or expired scalar attribute, SQLAlchemy raises:
```text
sqlalchemy.orm.exc.DetachedInstanceError: Parent instance <User at 0x...> is not bound to a Session
```

### Bad Practice
```python
@app.post("/users/{user_id}/send-summary")
async def send_summary(user_id: int, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_db)):
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    # BUG: Passing detached ORM model to background task
    background_tasks.add_task(async_generate_report, user)
    return {"status": "enqueued"}

async def async_generate_report(user: User):
    # Session is closed! Accessing user.orders raises DetachedInstanceError!
    for order in user.orders:
        send_email(user.email, order)
```

### Remediated Practice
```python
@app.post("/users/{user_id}/send-summary")
async def send_summary(user_id: int, background_tasks: BackgroundTasks):
    # Pass primitive IDs or serializable Pydantic DTOs, NOT raw ORM instances!
    background_tasks.add_task(async_generate_report, user_id)
    return {"status": "enqueued"}

async def async_generate_report(user_id: int):
    # Background task opens its own independent session
    async with async_session_factory() as session:
        stmt = (
            select(User)
            .options(selectinload(User.orders))
            .where(User.id == user_id)
        )
        user = (await session.execute(stmt)).scalar_one()
        for order in user.orders:
            send_email(user.email, order)
```

---

## 6. Forgetting `cascade="all, delete-orphan"` on Parent-Child Collections

### Failure Mechanism
When removing a child entity from a parent's collection (`parent.items.remove(item)`), or when deleting a parent entity without configuring orphan removal, SQLAlchemy merely sets the child's foreign key column to `NULL`. If the foreign key column is defined as `nullable=False`, the database raises an `IntegrityError: NOT NULL constraint failed`.

Even if the foreign key is nullable, disconnected child records remain indefinitely in the database as orphaned "zombie" records.

### Bad Practice
```python
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Lacks cascade="all, delete-orphan"
    items: Mapped[List["OrderItem"]] = relationship(back_populates="order")

# Later in service layer:
order.items.remove(item_to_remove)
await session.commit()
# DB FAILS with: IntegrityError: foreign key order_id cannot be null!
```

### Remediated Practice
```python
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan", # Deletes child from DB when removed from list
        passive_deletes=True,         # Leverages DB ON DELETE CASCADE for bulk speed
        lazy="raise_on_sql",
    )

class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    order: Mapped["Order"] = relationship(back_populates="items")
```

---

## 7. Calling `session.commit()` Inside Tight Loops

### Failure Mechanism
Calling `await session.commit()` inside a loop (e.g., during CSV imports or message batch processing) forces an immediate round-trip commit on every record. Each commit:
1. Forces an `fsync()` on the database transaction log.
2. Acquires and releases global locks.
3. If an error occurs on record 500 out of 1000, the database is left in a dirty half-imported state that cannot be cleanly rolled back.

### Bad Practice
```python
# Catastrophic: 1,000 network round trips and 1,000 fsync log writes!
for row in incoming_csv_rows:
    emp = Employee(email=row["email"], full_name=row["name"], organization_id=org_id)
    session.add(emp)
    await session.commit()  # Horrendously slow, violates atomicity!
```

### Remediated Practice
```python
# Batch operations into atomic chunked transactions
BATCH_SIZE = 500

async with session.begin():
    for index, row in enumerate(incoming_csv_rows, start=1):
        emp = Employee(email=row["email"], full_name=row["name"], organization_id=org_id)
        session.add(emp)
        
        # Flush periodically to keep memory bounded without finalizing commit
        if index % BATCH_SIZE == 0:
            await session.flush()

# Transaction commits ONCE atomically at the end of the begin() context!
```

---
*Follow these rules strictly to keep async SQLAlchemy architectures resilient, fast, and bug-free.*
