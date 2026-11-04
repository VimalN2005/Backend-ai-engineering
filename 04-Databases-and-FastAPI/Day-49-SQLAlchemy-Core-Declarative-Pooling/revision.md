# Day 49 Rapid Revision: SQLAlchemy 2.0 Core & Engine Pooling

A 5-minute executive cheat sheet for senior backend engineers.

---

## 1. Engine & QueuePool Production Blueprint

```python
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

engine = create_engine(
    "postgresql+psycopg://user:pass@db-host:5432/production_db",
    poolclass=QueuePool,
    pool_size=10,        # Baseline open connections
    max_overflow=20,     # Max temporary burst connections (Total Capacity = 30)
    pool_timeout=30.0,   # Seconds to wait before raising TimeoutError
    pool_recycle=1800,   # Recycle sockets every 30m to avoid firewall drops
    pool_pre_ping=True,  # Lightweight SELECT 1 heartbeat before checkout
    echo=False,          # Disable SQL stdout logging in production
)
```

---

## 2. Explicit Transaction Boundaries (No Implicit Autocommit!)

```python
# Pattern 1: Automatic Commit/Rollback Context (Preferred)
with engine.begin() as conn:
    conn.execute(update(users_table).where(users_table.c.id == 42).values(is_active=True))
# Automatically issues COMMIT on clean exit, ROLLBACK on exception!

# Pattern 2: Explicit Commit
with engine.connect() as conn:
    conn.execute(update(users_table).where(users_table.c.id == 42).values(is_active=True))
    conn.commit()  # MANDATORY in 2.0! Otherwise rolls back on exit!
```

---

## 3. Modern 2.0 Declarative Models (`Mapped` & `mapped_column`)

```python
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)         # nullable=False inferred
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(32), default=None) # nullable=True inferred
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_users_tenant_email", "tenant_id", "email", unique=True),
    )
```

---

## 4. Result Unwrapping Quick Reference

```python
# 1. ORM Instances:
users = session.execute(select(User)).scalars().all()

# 2. Dictionary Mappings (Fast serialization for JSON APIs):
dict_rows = conn.execute(select(users_table)).mappings().all()

# 3. Single Scalar Value (Aggregations):
count = conn.execute(select(func.count(users_table.c.id))).scalar_one()

# 4. Safe Single Row (Returns None if not found, raises if multiple):
user = conn.execute(select(users_table).where(users_table.c.id == 42)).one_or_none()
```

---

## 5. High-Throughput Bulk Operations (`insertmanyvalues`)

```python
# 10x-50x FASTER than ORM loops (Bypasses Identity Map & Batches Multi-Row SQL):
records = [{"tenant_id": "acme", "email": f"user_{i}@acme.com"} for i in range(50_000)]

with engine.begin() as conn:
    conn.execute(insert(users_table), records)
```

---

## 6. Critical Fork & Celery Checklist

1. **Gunicorn / Multiprocessing Fork Trap**: If an engine is created pre-fork, child processes share the same physical socket FD!
   - Fix: `engine.dispose(close=False)` in Gunicorn's `post_fork` hook.
2. **Celery Worker Tasks**:
   - Use `poolclass=NullPool` on worker engines to prevent socket sharing across worker forks.
3. **AWS NAT / Cloud Firewalls**:
   - Always set `pool_pre_ping=True` and `pool_recycle=1800` to prevent sudden connection drop errors.
