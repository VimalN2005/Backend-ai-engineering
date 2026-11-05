# Day 51: Rapid Revision & Cheat Sheet - Alembic Database Migrations

A high-density 5-minute reference sheet for **Alembic**, database schema migrations, automated drift detection, and zero-downtime schema evolution.

---

## 1. Essential Alembic CLI Commands

| Command | Description | Production Example |
| :--- | :--- | :--- |
| `alembic init <dir>` | Scaffolds a new Alembic project directory | `alembic init -t async migrations` |
| `alembic revision -m "msg"` | Creates an empty migration script | `alembic revision -m "add_indexes"` |
| `alembic revision --autogenerate -m "msg"` | Auto-detects differences between models and DB | `alembic revision --autogenerate -m "v2_schema"` |
| `alembic upgrade head` | Migrates database forward to latest revision | `alembic upgrade head` |
| `alembic upgrade +1` | Migrates database forward by exactly 1 step | `alembic upgrade +1` |
| `alembic downgrade -1` | Rolls back the last applied migration | `alembic downgrade -1` |
| `alembic downgrade base` | Rolls back ALL applied migrations to initial state | `alembic downgrade base` |
| `alembic current` | Displays the current active revision in the DB | `alembic current --verbose` |
| `alembic history` | Lists chronological history of all migration revisions | `alembic history --indicate-current` |
| `alembic heads` | Shows all unmerged leaf revisions | `alembic heads` |
| `alembic merge heads -m "msg"` | Merges divergent branch heads into a single head | `alembic merge heads -m "merge_branches"` |
| `alembic check` | Fails with exit code 1 if model/DB drift is detected | `alembic check` (Used in CI/CD pipelines) |
| `alembic upgrade head --sql` | Emits raw SQL DDL without connecting to DB | `alembic upgrade head --sql > release.sql` |

---

## 2. Async `env.py` Configuration Snippet

```python
# alembic/env.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context
from myapp.models import Base

target_metadata = Base.metadata

def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,              # CRITICAL: Detect column type modifications
        compare_server_default=True,    # CRITICAL: Detect server default modifications
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    url = context.config.get_main_option("sqlalchemy.url")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()

def run_migrations_online():
    asyncio.run(run_async_migrations())
```

---

## 3. Safe PostgreSQL Concurrent Index Creation

```python
def upgrade() -> None:
    # autocommit_block() exits Alembic's transaction context
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_accounts_email",
            "accounts",
            ["email"],
            unique=True,
            postgresql_concurrently=True, # Zero-lock concurrent index build
        )

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_accounts_email",
            table_name="accounts",
            postgresql_concurrently=True,
        )
```

---

## 4. Zero-Downtime Expand/Contract Checklist

```
Step 1: EXPAND
  [ ] Add new column as NULLABLE (or with server_default).
  [ ] Deploy application writing to BOTH old and new columns.

Step 2: BACKFILL
  [ ] Run batched background update: `UPDATE tbl SET new_col = old_col WHERE new_col IS NULL`.
  [ ] Verify 100% of rows have populated new column.

Step 3: CONTRACT
  [ ] Deploy application reading and writing ONLY to new column.
  [ ] Drain and terminate old application pods.
  [ ] Run migration setting NOT NULL or dropping deprecated old column.
```

---

## 5. In-Migration Data Transformation Template

```python
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column

def upgrade() -> None:
    # 1. Define isolated Core table (NEVER import application models!)
    users = table(
        "users",
        column("id", sa.Integer),
        column("tier", sa.String),
    )

    # 2. Execute SQL update directly via active connection
    bind = op.get_bind()
    bind.execute(
        users.update()
        .where(users.c.tier.is_(None))
        .values(tier="STANDARD")
    )
```

---

## 6. One-Minute Flashcard Review

1. **What is `alembic_version`?** A database table storing the active migration revision hash (`version_num`), preventing re-execution of previously applied migrations.
2. **Why does autogenerate fail on column renames?** It cannot distinguish between renaming a column vs dropping it and creating a new one, generating a destructive `drop_column` + `add_column`.
3. **Why use `alembic merge`?** To reconcile divergent Git branches that branched from the same ancestor, restoring a single `head` revision.
4. **Why is `autocommit_block()` needed for concurrent indexes in PostgreSQL?** PostgreSQL prohibits `CREATE INDEX CONCURRENTLY` inside an explicit transaction block (`BEGIN ... COMMIT`).
5. **What does `alembic check` do?** Checks if the current database schema matches SQLAlchemy declarative models, exiting with code 1 if unmigrated changes exist (ideal for CI/CD gates).
