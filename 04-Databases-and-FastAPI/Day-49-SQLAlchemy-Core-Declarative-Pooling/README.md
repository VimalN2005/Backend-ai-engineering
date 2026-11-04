# Day 49: SQLAlchemy 2.0 Core, Declarative Models & Engine Pooling

## Executive Overview & Architectural Scope

In modern Python backend engineering, SQLAlchemy is the industry standard for database persistence, query compilation, and relational schema modeling. However, the release of **SQLAlchemy 2.0** marked a fundamental paradigm shift away from legacy 1.x idioms (implicit autocommit, string-based queries, implicit join heuristics, and the legacy `Query` object) toward an **explicit, type-safe, compiled expression architecture**.

For senior backend and AI systems engineers, treating SQLAlchemy as a black-box ORM leads to severe production vulnerabilities: connection pool starvation, thread starvation under high concurrency, socket corruption across multiprocessing process forks, multi-gigabyte memory leaks from uncollected ORM identity maps, and catastrophic latency degradation on bulk analytical pipelines.

This architectural guide covers:
1. The unified SQLAlchemy 2.0 architecture and the execution boundary between **Core** and the **ORM**.
2. Connection pool topology (`QueuePool`, `NullPool`, `StaticPool`), pre-ping heartbeats, and mathematical pool capacity sizing.
3. High-throughput programmatic DML compilation via the Core Expression Language.
4. Modern 2.0 Declarative Models using PEP 484 static typing (`Mapped[]` and `mapped_column()`).
5. Bulk execution physics (`insertmanyvalues` batching vs ORM hydration overhead) and connection pool observability hooks.

```
+---------------------------------------------------------------------------------------------------+
|                                 SQLALCHEMY 2.0 SYSTEM ARCHITECTURE                                |
|                                                                                                   |
|  +---------------------------------------------------------------------------------------------+  |
|  |                            OBJECT-RELATIONAL MAPPING (ORM) LAYER                            |  |
|  |                                                                                             |  |
|  |   class Base(DeclarativeBase): ...             Session / AsyncSession                       |  |
|  |   id: Mapped[int] = mapped_column(...)         Unit of Work, Identity Map, State Tracker    |  |
|  |   email: Mapped[str] = mapped_column(...)      select(User).where(User.id == 42)            |  |
|  +----------------------------------------------+----------------------------------------------+  |
|                                                 | (Compiles into Core Expression Tree)            |
|                                                 v                                                 |
|  +---------------------------------------------------------------------------------------------+  |
|  |                             SQL EXPRESSION LANGUAGE (CORE) LAYER                            |  |
|  |                                                                                             |  |
|  |   MetaData & Table Definitions                 ClauseElements & DML Statements              |  |
|  |   users_table = Table('users', metadata, ...)  select(users_table).where(...)               |  |
|  |   Compiled SQL Cache (ClauseVisitor)           insert(users_table).values(...)              |  |
|  +----------------------------------------------+----------------------------------------------+  |
|                                                 | (Executes via Engine & Connection)              |
|                                                 v                                                 |
|  +---------------------------------------------------------------------------------------------+  |
|  |                              ENGINE & CONNECTION POOLING LAYER                              |  |
|  |                                                                                             |  |
|  |   Engine (create_engine)                       Connection Pool (QueuePool / NullPool)       |  |
|  |   +------------------------------------+       +------------------------------------+       |  |
|  |   | Dialect (PostgreSQL, SQLite, MySQL)|       | Checked-Out Conns: [Conn 1, Conn 2]|       |  |
|  |   | ExecutionContext / Parameter Binder|       | Idle Pool (pool_size): [Conn 3, ..]|       |  |
|  |   | Event Subsystem (checkout/checkin) |       | Overflow Pool (max_overflow): [...]|       |  |
|  |   +------------------------------------+       +-----------------+------------------+       |  |
|  +--------------------------------------------------------------|------------------------------+  |
|                                                                 | (Physical DBAPI Connection)     |
|                                                                 v                                 |
|  +---------------------------------------------------------------------------------------------+  |
|  |                       DRIVER (DBAPI) & PHYSICAL DATABASE BOUNDARY                           |  |
|  |   DBAPI Drivers: psycopg, asyncpg, sqlite3    Network Sockets: TCP 5432, Unix Domain Sockets |  |
|  +---------------------------------------------------------------------------------------------+  |
+---------------------------------------------------------------------------------------------------+
```

---

## 1. The SQLAlchemy 2.0 Architectural Paradigm Shift

The transition from SQLAlchemy 1.x to 2.0 is not merely an API cosmetic update; it is an overhaul of how SQL statements are structured, compiled, and executed.

### 1. Unified Execution Syntax Across Core and ORM
In SQLAlchemy 1.x, two completely disjoint querying interfaces existed:
- Core used `conn.execute(users_table.select().where(...))`
- ORM used `session.query(User).filter(...).all()`

In SQLAlchemy 2.0, **`session.query()` is legacy and deprecated**. Both Core and ORM utilize the exact same top-level `select()`, `insert()`, `update()`, and `delete()` construct:
```python
# Modern 2.0 Core:
with engine.connect() as conn:
    result = conn.execute(select(users_table).where(users_table.c.id == 42))

# Modern 2.0 ORM:
with Session(engine) as session:
    result = session.execute(select(User).where(User.id == 42))
```

### 2. Removal of Implicit Autocommit
In legacy 1.x, issuing an `UPDATE` or `INSERT` on a connection implicitly issued a commit behind the scenes under certain heuristics. In SQLAlchemy 2.0, **implicit autocommit is completely eradicated**. All transactional operations must be explicitly committed:
```python
# Explicit commit on connection checkout:
with engine.connect() as conn:
    conn.execute(update(users_table).where(users_table.c.id == 1).values(active=True))
    conn.commit()  # REQUIRED! Otherwise changes roll back on context exit.

# Or commit-as-you-go block:
with engine.begin() as conn:  # Automatically issues COMMIT on clean exit, ROLLBACK on error
    conn.execute(update(users_table).where(users_table.c.id == 1).values(active=True))
```

### 3. Native Static Typing Integration (PEP 484 / PEP 681)
SQLAlchemy 2.0 introduces first-class Mypy/Pyright type inference through `Mapped[]` and `mapped_column()`, eliminating the dynamic attribute lookup antipatterns that plagued legacy models.

---

## 2. Layered Architecture: Core vs ORM

SQLAlchemy is architected in two clean horizontal strata:

```
+---------------------------------------------------------------------------------------------+
|                                    SQLAlchemy ORM                                           |
|   - Declarative Models (Base, Mapped)                                                       |
|   - Identity Map (Caches loaded instances by Primary Key; deduplicates in-memory objects)   |
|   - Unit of Work (Accumulates mutations, topologically sorts foreign key DAGs, flushes)     |
|   - Session (Manages transactional lifecycle and ORM instance state transitions)            |
+---------------------------------------------------------------------------------------------+
                                              |
                                              v (Translates ORM operations to Core AST)
+---------------------------------------------------------------------------------------------+
|                                   SQLAlchemy Core                                           |
|   - Schema Metadata (MetaData, Table, Column, Index, Constraints)                           |
|   - SQL Expression Language (select, insert, update, delete, CTEs, Window Functions)        |
|   - Clause Compilation Engine (Translates Abstract Syntax Trees to Dialect-specific SQL)    |
|   - Engine & Connection (Executes SQL strings, binds parameters, unwraps CursorResults)     |
|   - Connection Pooling (Manages re-use of physical database socket connections)             |
+---------------------------------------------------------------------------------------------+
```

### When to Use Core vs ORM
- **Use SQLAlchemy Core when**:
  1. Executing high-throughput bulk ingestion (e.g. inserting 100,000 telemetry events per second).
  2. Running analytical ETL queries, complex aggregations, window functions, and recursive CTEs where hydrating ORM objects incurs prohibitive CPU and memory overhead ($10\times$ slower).
  3. Reading data strictly for read-only serialization (e.g. streaming JSON out of a FastAPI endpoint).
- **Use SQLAlchemy ORM when**:
  1. Operating on rich domain entities with intricate business logic and multi-table validation.
  2. Modifying parent-child object graphs where the Unit of Work automates topological dependency resolution (`parent.children.append(child)`).
  3. Handling transactional web application workflows requiring identity deduplication.

---

## 3. Engine Creation, Configuration & Flags

The `Engine` is the central operational hub of any SQLAlchemy application. It maintains a **Dialect** (which knows how to communicate with PostgreSQL, SQLite, MySQL, or Oracle) and a **Connection Pool**.

```python
from sqlalchemy import create_engine

engine = create_engine(
    "postgresql+psycopg://app_user:secret_pass@db-primary.internal:5432/finance_db",
    # Connection Pool Topology
    pool_size=10,             # Persistent active pool capacity
    max_overflow=20,          # Temporary burst capacity during traffic spikes
    pool_timeout=30,          # Seconds to wait for an available connection before raising TimeoutError
    pool_recycle=1800,        # Maximum lifetime of a connection in seconds (30 mins)
    pool_pre_ping=True,       # Issue lightweight heartbeat check before checkout
    # Diagnostic & Logging Options
    echo=False,               # Set True only in local debugging; logs all raw SQL
    hide_parameters=False,    # Mask parameter values in logs (essential for PII compliance)
    isolation_level="READ COMMITTED", # Default transaction isolation
)
```

### Key Execution Options
Engines and connections support execution options for fine-grained runtime tuning:
- `stream_results=True`: Enables server-side cursors in PostgreSQL, allowing an application to iterate across a 50,000,000-row table in streaming chunks of 10,000 without loading the entire result set into Python RAM.
- `schema_translate_map`: Dynamically remaps database schema names at runtime (critical for multi-tenant SaaS architectures sharing a single database instance).

---

## 4. Connection Pool Topology: `QueuePool` vs `NullPool` vs `StaticPool`

A connection pool maintains a cache of open database socket connections, amortizing the massive CPU and network latency overhead of establishing physical TCP handshakes, TLS negotiations, and authentication handshakes on every request.

```
+--------------------+-----------------------------+------------------------------------+-------------------------------------+
| Pool Class         | Underlying Behavior         | Ideal Production Workloads         | Dangerous / Antipattern Workloads   |
+--------------------+-----------------------------+------------------------------------+-------------------------------------+
| QueuePool          | Thread-safe FIFO queue with | High-concurrency web services      | Multiprocessing process forks       |
|                    | fixed size + burst overflow | (FastAPI, Gunicorn, Celery solo)   | (Celery fork pools, multiprocessing)|
+--------------------+-----------------------------+------------------------------------+-------------------------------------+
| NullPool           | Disables pooling entirely;  | AWS Lambda, Serverless functions,  | High-throughput transactional APIs  |
|                    | opens & closes on demand    | Celery worker tasks with fork pool | (TCP handshake latency overhead)    |
+--------------------+-----------------------------+------------------------------------+-------------------------------------+
| StaticPool         | Holds exactly ONE single    | In-memory SQLite (`:memory:`)      | Concurrent multi-threaded apps      |
|                    | connection indefinitely     | Unit test suites                   | requiring isolated transactions     |
+--------------------+-----------------------------+------------------------------------+-------------------------------------+
| AsyncAdaptedQueue  | Asyncio-compatible variant  | Asynchronous web backends          | Synchronous blocking threads        |
| Pool               | of QueuePool                | (FastAPI with asyncpg / psycopg3)  |                                     |
+--------------------+-----------------------------+------------------------------------+-------------------------------------+
```

---

## 5. Pool Parameters, Pre-Ping Heartbeats & Exhaustion Physics

### The Connection Lifecycle
1. **Checkout**: A worker thread invokes `engine.connect()` or `Session()`. The pool checks if an idle connection exists in its FIFO queue.
2. **Pre-Ping (`pool_pre_ping=True`)**:
   - Cloud firewalls, AWS NAT Gateways, and stateful load balancers frequently drop idle TCP connections after 5–15 minutes of inactivity without sending a TCP `RST` or `FIN` packet.
   - When a thread checks out a "dead" socket, executing a query raises `OperationalError: SSL connection has been closed unexpectedly`.
   - With `pool_pre_ping=True`, the pool executes an immediate, lightweight `SELECT 1` ping. If the connection fails, it is silently purged from the pool, and a fresh socket is negotiated transparently!
3. **Usage**: The thread executes queries within an explicit transaction.
4. **Checkin & Reset-on-Return**:
   - When the connection context exits, the connection is returned to the pool.
   - **`reset_on_return = 'rollback'` (Default)**: SQLAlchemy immediately executes an internal `ROLLBACK` on the connection before placing it back in the pool! This cleanses any uncommitted row locks, temporary tables, or modified session variables (`SET search_path`), preventing cross-request data leaks.

### Pool Sizing Formula for Web Backends
If a cluster runs 4 Kubernetes pods, each running Gunicorn with 4 Uvicorn worker processes:
$$\text{Max Checkouts Per Pod} = \text{Workers} \times (\text{pool\_size} + \text{max\_overflow}) = 4 \times (5 + 10) = 60\text{ connections}$$
$$\text{Total Cluster Connections} = 4\text{ pods} \times 60 = 240\text{ connections}$$
If PostgreSQL’s `max_connections` is configured to 200, an unexpected traffic burst will cause PostgreSQL to reject connections with:
```
FATAL: remaining connection slots are reserved for non-replication superuser connections
```
**Rule of Thumb**: Always size `pool_size` such that total peak cluster checkouts remain well below PostgreSQL's `max_connections` (leaving at least 20% headroom for background workers, migrations, and DBA administration).

---

## 6. SQLAlchemy Core: Schema Metadata & Programmatic DDL

In SQLAlchemy Core, database schemas are defined programmatically using `MetaData` and `Table` objects:

```python
from sqlalchemy import MetaData, Table, Column, Integer, String, Boolean, DateTime, ForeignKey, Index, CheckConstraint, func

metadata = MetaData()

users_table = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False, index=True),
    Column("email", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    # Table-level Constraints & Multi-Column Composite Indexes
    Index("ix_users_tenant_email", "tenant_id", "email", unique=True),
    CheckConstraint("length(email) >= 5", name="ck_users_email_min_length"),
)
```

### In-Memory DDL Generation & Reflection
- **DDL Compilation**: `metadata.create_all(engine)` inspects dialect specifications and emits exact dialect-compliant SQL (`CREATE TABLE ...`).
- **Database Reflection**: An application can inspect and map existing production database tables dynamically at runtime without manually writing schema definitions:
  ```python
  orders_table = Table("orders", metadata, autoload_with=engine)
  ```

---

## 7. SQLAlchemy Core Expression Language: Type-Safe DML Compilation

The Core Expression Language allows building programmatic, dialect-agnostic SQL queries using Python syntax.

```python
from sqlalchemy import select, insert, update, delete, and_, or_, desc, func

# 1. Programmatic SELECT with Joins and Aggregations
stmt = (
    select(
        users_table.c.tenant_id,
        func.count(users_table.c.id).label("total_users"),
        func.max(users_table.c.created_at).label("latest_signup")
    )
    .where(
        and_(
            users_table.c.is_active == True,
            users_table.c.tenant_id.in_(["acme", "globex"])
        )
    )
    .group_by(users_table.c.tenant_id)
    .having(func.count(users_table.c.id) > 5)
    .order_by(desc("total_users"))
    .limit(10)
)

# 2. Window Functions in Core Expression Language
running_count = func.row_number().over(
    partition_by=users_table.c.tenant_id,
    order_by=users_table.c.created_at.asc()
).label("tenant_user_seq")

window_stmt = select(users_table.c.id, users_table.c.email, running_count)
```

---

## 8. Modern 2.0 Declarative Models (`Mapped` & `mapped_column`)

In SQLAlchemy 2.0, declarative models inherit from `DeclarativeBase` and define columns using Python type annotations:

```python
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    # Primary Key
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    
    # Required String Column (nullable=False automatically inferred from 'str'!)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    
    # Unique Column
    email: Mapped[str] = mapped_column(String(255))
    
    # Optional / Nullable Column (nullable=True automatically inferred from 'Optional[str]'!)
    phone_number: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    
    # Default Booleans
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    
    # Server Defaults with Timezones
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Multi-Column Table Arguments
    __table_args__ = (
        Index("ix_users_tenant_email", "tenant_id", "email", unique=True),
    )
```

### The Nullability Inference Rule
In 2.0, SQLAlchemy inspects Python typing:
- `Mapped[str]`: Automatically sets `nullable=False`.
- `Mapped[Optional[str]]` or `Mapped[str | None]`: Automatically sets `nullable=True`.
This completely eliminates discrepancies between runtime database column constraints and Python static type checkers!

---

## 9. Result Processing & Hydration Semantics

Executing a statement returns a `Result` object (or `CursorResult`). SQLAlchemy 2.0 provides precise methods for extracting data:

```python
with engine.connect() as conn:
    result = conn.execute(select(users_table))

    # 1. Row Tuple Access (Positional or Attribute)
    for row in result:
        print(row.id, row.email)  # Named attribute access
        print(row[0], row[1])     # Positional tuple access

    # 2. Dictionary / Mapping Access (RowMapping)
    # Essential for fast serialization to JSON dictionaries!
    dict_results = conn.execute(select(users_table)).mappings().all()
    # Returns: [{'id': 1, 'email': '...'}, {'id': 2, ...}]

    # 3. Scalar Extraction (Single Column Queries)
    total_count = conn.execute(select(func.count(users_table.c.id))).scalar_one()

    # 4. Safe Single-Row Lookups
    user_row = conn.execute(
        select(users_table).where(users_table.c.id == 42)
    ).one_or_none()  # Returns None if not found; raises if >1 row returned!
```

---

## 10. Bulk Execution Physics: `insertmanyvalues` vs ORM Hydration

One of the most catastrophic mistakes in Python database development is executing bulk inserts via individual ORM instance loops:

```python
# DISASTER (50x Slowdown):
for record in records_list:
    user = User(email=record["email"], tenant_id=record["tenant_id"])
    session.add(user)
session.commit()
```
### Why Individual ORM Inserts Collapse Performance:
1. **Identity Map Overhead**: The ORM allocates state trackers, attribute history trackers, and weak references for every single object in memory.
2. **Network Round-Trip Multiplication**: In legacy drivers, each `INSERT` executes as a separate network round-trip packet across the TCP socket!
3. **Flushing & Dependency Sorting**: Prior to committing, the Unit of Work topologically sorts all instances.

### The SQLAlchemy 2.0 Solution: Core `insertmanyvalues`
SQLAlchemy 2.0 Core automatically optimizes multi-dictionary parameter lists passed to `execute()`:
```python
# ENTERPRISE PATTERN: High-Throughput Core Bulk Insert
with engine.begin() as conn:
    conn.execute(
        insert(users_table),
        [
            {"tenant_id": "acme", "email": f"user_{i}@acme.com"}
            for i in range(50_000)
        ]
    )
```
- The 2.0 engine intercepts the multi-parameter dictionary list and compiles a single batched multi-row SQL statement:
  ```sql
  INSERT INTO users (tenant_id, email) VALUES 
  ('acme', 'user_0@acme.com'),
  ('acme', 'user_1@acme.com'),
  ...
  ('acme', 'user_999@acme.com');
  ```
- Automatically chunks large payloads into dialect-specific parameter limits ($32,767$ in SQLite, $65,535$ in PostgreSQL), reducing wall-clock execution time by **over 95%**!

---

## 11. Parameter Binding, Compilation & SQL Injection Immunity

Under no circumstances should user input be concatenated directly into SQL strings using Python f-strings:
```python
# EXTREME VULNERABILITY (SQL Injection):
conn.execute(text(f"SELECT * FROM users WHERE email = '{user_input}'"))
```
SQLAlchemy compiles queries into parameterized abstract syntax trees (`ClauseElements`):
```python
# SECURE (Type-Safe Parameter Binding):
stmt = select(users_table).where(users_table.c.email == user_input)
conn.execute(stmt)
```
- The database driver receives the SQL string with parameter placeholders (`:email` or `%s` or `?`) alongside a separate parameter array tuple.
- The SQL text and data payload travel across the wire in isolated protocol buffers. User input can **never** alter the parse tree or execution AST of the query!

---

## 12. Connection Reset & Transaction Cleansing (`reset_on_return`)

When a checked-out connection is released back into the pool:
- What happens if the previous request opened a transaction, acquired locks, or modified session configuration (`SET TIME ZONE 'UTC'`) and crashed before committing?
- If the connection returned to the pool without cleanup, the next completely unrelated HTTP request would inherit the previous user's uncommitted transaction and open locks!

### The Engine Reset Policy
SQLAlchemy enforces `reset_on_return = 'rollback'`:
- Upon checkin, the connection pool invokes `rollback()` on the underlying physical DBAPI connection before returning it to the pool queue.
- This guarantees that every request checking out a pooled connection receives a completely pristine, neutral database session.

---

## 13. Connection Pool Event Subsystem (`sqlalchemy.event`)

SQLAlchemy provides an event hook architecture allowing production systems to intercept every connection and query event for metrics collection, logging, and dynamic security validation:

```python
from sqlalchemy import event

# 1. Connection Lifecycle Tracking
@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_connection, connection_record, connection_proxy):
    connection_record.info["checkout_time"] = time.perf_counter()

@event.listens_for(engine, "checkin")
def receive_checkin(dbapi_connection, connection_record):
    checkout_time = connection_record.info.pop("checkout_time", None)
    if checkout_time:
        duration_ms = (time.perf_counter() - checkout_time) * 1000.0
        # Export duration_ms to Prometheus / Datadog metrics!

# 2. Slow Query Detection Interceptor
@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_time", []).append(time.perf_counter())

@event.listens_for(engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total_time_ms = (time.perf_counter() - conn.info["query_start_time"].pop()) * 1000.0
    if total_time_ms > 200.0:  # Query exceeded 200ms threshold
        logger.warning(f"SLOW QUERY ({total_time_ms:.2f}ms): {statement} | Params: {parameters}")
```

---

## 14. Multiprocessing Fork Traps in Celery & Gunicorn

One of the most elusive production bugs occurs when mixing SQLAlchemy with POSIX process forking (`os.fork()`), standard in Gunicorn prefork workers and Celery:

### The File Descriptor Duplication Disaster
1. The parent master process creates an `engine = create_engine(...)` at module import time.
2. The engine opens an initial database connection in its pool (acquiring an OS socket file descriptor, e.g. `FD 4`).
3. Gunicorn or Celery forks 4 child worker processes.
4. **The Trap**: `fork()` duplicates the parent process's memory space and **file descriptor table**. All 4 child worker processes now hold file descriptor `FD 4` pointing to the **exact same physical TCP socket**!
5. When Worker 1 and Worker 2 issue database queries concurrently, both write raw bytes simultaneously into the same TCP pipe. PostgreSQL receives interleaved, corrupted protocol packets and abruptly closes the socket:
   ```
   OperationalError: SSL error: decryption failed or bad record mac
   ```

### The Architectural Remediation
1. **Dispose the Engine Immediately Post-Fork**:
   ```python
   # Gunicorn post_fork hook:
   def post_fork(server, worker):
       engine.dispose(close=False)
   ```
   `close=False` instructs SQLAlchemy to drop its internal connection references in the child process without attempting to send a `CLOSE` packet across the socket (which would kill the parent's connection).
2. **Use `NullPool` in Worker Tasks**:
   For Celery workers or AWS Lambda functions where tasks execute in independent processes, configure `poolclass=NullPool`.

---

## 15. Production Capacity Planning Runbook

```
                         [Production Web Request]
                                    |
                    +---------------+---------------+
                    |                               |
             [Read Analytics]               [OLTP Transaction]
                    |                               |
           (Core Expression AST)            (Declarative ORM)
                    |                               |
           conn.execute(select())           session.execute(select())
                    |                               |
                    +---------------+---------------+
                                    |
                            [QueuePool Checkout]
                                    |
                    +---------------+---------------+
                    |                               |
          [Socket Available]              [Pool Saturated]
                    |                               |
             pool_pre_ping?                         |
              /          \                          v
           (OK)        (Dead)             Wait up to 'pool_timeout'
            |            |                          |
            v            v                  +-------+-------+
       Execute SQL   Reconnect              |               |
            |            |             [Slot Freed]    [Timeout Error]
            v            v                  |               |
       Reset-on-Return (Rollback)      Execute SQL   (HTTP 503 Raised)
```
