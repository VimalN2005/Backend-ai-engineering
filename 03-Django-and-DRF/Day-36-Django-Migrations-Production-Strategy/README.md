# Day 36: Safe Database Migrations, Reversible Migrations & Production Strategy

Database migrations are the bridge between evolving application code and persistent, stateful data. In local development, running `python manage.py makemigrations` and `python manage.py migrate` is instantaneous and forgiving. In enterprise production environments with terabyte-scale databases, hundreds of millions of rows, and continuous traffic, **a naive migration can lock tables, block writes, exhaust connection pools, and take down entire platforms**.

Understanding how Django compiles migrations under the hood, how the **Migration Dependency Graph (DAG)** is linearized, how to execute the **Expand and Contract pattern**, and how to safely run **concurrent indexes and zero-downtime column operations** is a mandatory competency for Senior Backend and Platform Engineers.

---

## 1. Django Migration Internals & Architectural Foundations

A migration in Django is a Python script containing declarative schema operations. It does not store raw SQL; instead, it stores an engine-agnostic **Abstract Syntax Tree (AST)** of operations that Django's `SchemaEditor` compiles into the target database dialect (PostgreSQL, MySQL, SQLite) at execution time.

```
+-----------------------------------------------------------------------------------+
|                        DJANGO MIGRATION COMPILATION PIPELINE                      |
+-----------------------------------------------------------------------------------+
| 1. makemigrations:                                                                |
|    Model Definitions in Python (Current State)                                    |
|              VS                                                                   |
|    History of all existing migrations (Recorded State)                            |
|              │                                                                    |
|              ▼ (MigrationAutodetector compares states)                            |
|    Generates migration file: 0002_add_status_column.py                            |
|                                                                                   |
| 2. migrate:                                                                       |
|    Checks django_migrations table in PostgreSQL                                  |
|              │                                                                    |
|              ▼ (Builds Directed Acyclic Graph - DAG)                              |
|    Linearizes unapplied migrations in topological order                           |
|              │                                                                    |
|              ▼ (SchemaEditor compiles operations)                                 |
|    Emits SQL: ALTER TABLE orders ADD COLUMN status VARCHAR(20)...;                |
|    Inserts row into django_migrations: ('orders', '0002_add_status_column')       |
+-----------------------------------------------------------------------------------+
```

### The `django_migrations` Physical Table:
Django maintains an internal tracking table in the database:
```sql
CREATE TABLE django_migrations (
    id SERIAL PRIMARY KEY,
    app VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    applied TIMESTAMPTZ NOT NULL
);
```
When `migrate` runs, Django inspects this table to identify which migrations in your codebase have not yet been executed.

---

## 2. The Migration Dependency Graph (DAG)

Django structures migrations across all installed applications into a **Directed Acyclic Graph (DAG)**:
- Each migration node declares its `dependencies = [('app_name', '0001_initial')]`.
- A migration cannot run until all its parent nodes have been executed.
- Django uses **Topological Sorting** to resolve the linear execution order across independent domain apps.

```
               [auth.0001_initial]
                        │
                        ▼
               [users.0001_initial] ◄──────┐
                        │                  │ (Foreign Key Dependency)
                        ▼                  │
               [orders.0001_initial] ──────┘
                        │
                        ▼
               [orders.0002_add_status]
```

### Circular Dependency Deadlocks:
If App A has a ForeignKey pointing to App B, and App B has a ForeignKey pointing to App A in their initial migrations, Django will fail with a circular dependency error.
**Resolution**: Split the model creation from the foreign key constraint into two separate migrations, or use `swappable_dependency`.

---

## 3. Core Migration CLI Tooling

Senior engineers never run `migrate` blindly in production without inspecting the underlying DDL.

```bash
# 1. Preview the exact SQL statements Django will execute for a migration
python manage.py sqlmigrate orders 0002_add_status

# 2. View the application status of all migrations across the system
python manage.py showmigrations

# 3. Detect unapplied model changes without creating migration files (CI/CD check)
python manage.py makemigrations --check --dry-run

# 4. Compact 50+ historical migrations into a clean baseline
python manage.py squashmigrations orders 0001 0050
```

> [!TIP]
> **CI/CD Pre-Commit Gate**: Always run `python manage.py makemigrations --check --dry-run` in your GitHub Actions pipeline. If a developer modified a `models.py` file but forgot to commit the generated migration file, the build immediately fails, preventing production drift!

---

## 4. Zero-Downtime Migrations: The Expand and Contract Pattern

In modern high-availability environments, applications run across multiple container replicas (Kubernetes pods, ECS tasks). When deploying an update, old containers and new containers **run side-by-side during rolling deployments**.

If a migration immediately alters or deletes a column, old containers still in service will crash with 500 errors!

```
+-----------------------------------------------------------------------------------+
|                        EXPAND AND CONTRACT DEPLOYMENT PATTERN                     |
+-----------------------------------------------------------------------------------+
| PHASE 1: EXPAND (Additive Migration)                                              |
|   - Add new column as NULLABLE in database.                                       |
|   - Both Old Code (v1) and New Code (v2) can run simultaneously.                   |
|                                                                                   |
| PHASE 2: DUAL WRITE & BACKFILL                                                    |
|   - Deploy code that writes to BOTH old and new columns.                          |
|   - Run background data backfill script to populate existing rows.                |
|                                                                                   |
| PHASE 3: CONTRACT (Subtractive Migration)                                         |
|   - Deploy code that reads and writes ONLY from the new column.                   |
|   - Run final migration to drop the old column and add NOT NULL constraints.      |
+-----------------------------------------------------------------------------------+
```

---

## 5. Adding `NOT NULL` Columns Safely on Massive Tables

### The Dangerous Naive Approach:
```python
# MIGRATION THAT CRASHES PRODUCTION:
migrations.AddField(
    model_name="order",
    name="status",
    field=models.CharField(max_length=20, default="PENDING"), # NOT NULL by default!
)
```

#### Why it fails on PostgreSQL:
In PostgreSQL versions prior to 11, adding a `NOT NULL` column with a default value requires rewriting the entire table on disk while holding an **`AccessExclusiveLock`**. On a 50-million-row table, this lock persists for minutes, queueing all incoming HTTP requests and bringing down the website.

### The 4-Step Zero-Downtime Strategy:

#### Step 1: Add the column as `NULLABLE` (Zero table rewrite):
```python
migrations.AddField(
    model_name="order",
    name="status",
    field=models.CharField(max_length=20, null=True, blank=True),
)
```

#### Step 2: Backfill data in small chunks (via Celery or RunPython):
Update existing rows in batches of 5,000 using `iterator()` or raw SQL to avoid long-lived row locks.

#### Step 3: Add Database `CHECK` Constraint (NOT VALID):
```sql
ALTER TABLE orders ADD CONSTRAINT order_status_not_null CHECK (status IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT order_status_not_null;
```
`NOT VALID` acquires an instantaneous lock, and `VALIDATE` scans the table without blocking concurrent writes!

#### Step 4: Flip `null=False` in Django:
Once all rows are guaranteed non-null, set `null=False` in Django models.

---

## 6. Safe Column Renaming

Renaming a column via `RenameField` breaks rolling deployments:
1. Migration runs: `ALTER TABLE users RENAME COLUMN phone TO mobile;`
2. Old containers (v1) still running execute: `SELECT phone FROM users;` -> **CRASH (500 Error: column "phone" does not exist)**.

### The Zero-Downtime Renaming Protocol:
1. **Expand**: Add `mobile` column as nullable.
2. **Dual-Write**: Update Django model to write to both `phone` and `mobile` on `.save()`.
3. **Backfill**: Background script copies all data from `phone` to `mobile`.
4. **Switch Reads**: Update code to read from `mobile`.
5. **Contract**: Remove `phone` from code and drop `phone` column in database.

---

## 7. Data Migrations: `RunPython` and Historical Models

When a schema change requires converting or calculating data, Django provides `migrations.RunPython`.

```python
from django.db import migrations

def backfill_uuid(apps, schema_editor):
    # CRITICAL: Always retrieve models via apps.get_model()!
    # NEVER import models directly (e.g. from apps.users.models import User)!
    User = apps.get_model("users", "User")
    import uuid

    for user in User.objects.filter(uuid_token__isnull=True).iterator(chunk_size=2000):
        user.uuid_token = uuid.uuid4()
        user.save(update_fields=["uuid_token"])

def reverse_backfill(apps, schema_editor):
    pass  # Logic to reverse the migration if rolled back

class Migration(migrations.Migration):
    dependencies = [("users", "0001_initial")]
    operations = [
        migrations.RunPython(backfill_uuid, reverse_code=reverse_backfill),
    ]
```

### The "Historical Model" Rule:
> **NEVER import models directly from your application code inside a migration!**
> `from myapp.models import User` imports the **current** state of the model. If another engineer adds a new field or method 6 months later, running old migrations from scratch will crash because the imported model expects columns that do not exist yet in the database!
> **Always use `apps.get_model('app_name', 'ModelName')`**. This returns the frozen historical state of the model as it existed at that exact migration step.

---

## 8. Reversible Migrations & `elidable=True`

Every migration should be reversible so deployment pipelines can execute rollbacks automatically on failure.

### 1. The `reverse_code` Requirement:
```python
migrations.RunPython(forwards_func, reverse_code=backwards_func)
```
If `reverse_code` is omitted, running `python manage.py migrate app 0001` to roll back will raise `IrreversibleError`.

### 2. The `elidable=True` Attribute:
```python
migrations.RunPython(backfill_func, reverse_code=migrations.RunPython.noop, elidable=True)
```
When squashing migrations via `squashmigrations`, operations marked with `elidable=True` are **completely removed from the squashed migration**. This is ideal for one-off data backfills that are irrelevant when setting up a fresh database from scratch!

---

## 9. Zero-Downtime Indexing with `AddIndexConcurrently`

Creating an index on a table with 10 million rows via standard `models.Index` issues:
```sql
CREATE INDEX idx_orders_customer ON orders (customer_id);
```
In PostgreSQL, this holds an **`AccessExclusiveLock`** (or `ShareLock`), blocking all write queries (`INSERT`, `UPDATE`, `DELETE`) until the index finishes building!

### The Concurrent Index Solution:
PostgreSQL supports `CREATE INDEX CONCURRENTLY`, which builds the index in the background without blocking concurrent reads or writes:

```python
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models

class Migration(migrations.Migration):
    # CRITICAL: Concurrent index creation CANNOT run inside an atomic transaction block!
    atomic = False

    dependencies = [("orders", "0001_initial")]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(fields=["customer", "created_at"], name="idx_order_cust_date"),
        )
    ]
```

---

## 10. Transactional Migrations: `atomic = True` vs `atomic = False`

- **PostgreSQL**: Fully supports **Transactional DDL**. Schema operations (`CREATE TABLE`, `ALTER TABLE`, `ADD COLUMN`) run inside a database transaction (`atomic = True`). If an error occurs halfway through, PostgreSQL automatically rolls back the entire migration, leaving the schema in a clean state.
- **MySQL / Oracle**: DDL statements cause an **implicit commit**. If a migration fails halfway through, the database is left in a corrupted, semi-migrated state requiring manual DBA intervention.
- **When to use `atomic = False` in PostgreSQL**:
  - `CREATE INDEX CONCURRENTLY`
  - `DROP INDEX CONCURRENTLY`
  - Modifying `ENUM` types

---

## 11. Lock Timeouts & DDL Pre-Flight Guardrails

When Django executes an `ALTER TABLE` statement, PostgreSQL requests an exclusive lock. If a long-running read query is currently active on that table, the migration waits in line.
Worse, **all subsequent queries queue up behind the migration lock request**, causing the entire application to hang!

### Senior Production Guardrail:
Configure strict DDL lock timeouts in migration operations or database settings:
```sql
-- Abort migration immediately if lock cannot be acquired within 2 seconds
SET lock_timeout = '2s';

-- Abort migration if statement takes more than 10 seconds to execute
SET statement_timeout = '10s';
```

If the lock cannot be obtained within 2 seconds, the migration fails gracefully, leaving the production application completely unaffected.

---

## 12. Multi-Stage Deployment CI/CD Workflow

Enterprise Kubernetes / ECS deployments decouple migration execution from pod rollouts:

```
+─────────────────────────────────────────────────────────────────────────────+
|                     ENTERPRISE MIGRATION CI/CD PIPELINE                     |
+─────────────────────────────────────────────────────────────────────────────+
| 1. CI Build Stage:                                                          |
|    -> Run python manage.py makemigrations --check --dry-run                 |
|    -> Run test suite against clean migrated database                        |
|                                                                             |
| 2. Pre-Deployment Stage (Job / InitContainer):                              |
|    -> Run backward-compatible schema migrations (Expand phase)              |
|    -> Wait for completion before booting application containers             |
|                                                                             |
| 3. Canary / Rolling Deployment Stage:                                       |
|    -> Deploy New Code (v2) alongside Old Code (v1)                          |
|    -> Verify error rates and health metrics                                 |
|                                                                             |
| 4. Post-Deployment Stage (Off-Peak Cron / Background):                       |
|    -> Run data backfills in small batches                                   |
|    -> Run contract migrations (dropping obsolete columns)                   |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 13. Fake Migrations & Disaster Recovery (`--fake`)

### When to use `--fake`:
`python manage.py migrate app_name 0005 --fake`
- Updates the `django_migrations` table to mark migration `0005` as applied **without executing any underlying SQL DDL**.
- **Use Case**: Adopting an existing legacy database table into Django, or recovering from a migration that was manually applied by a DBA.

### When to use `--fake-initial`:
- If tables already exist in the database, `--fake-initial` checks if all tables created by `0001_initial` exist, and if so, fakes the initial migration.

> [!CAUTION]
> Never use `--fake` to bypass a failed migration without understanding why it failed! Doing so creates a divergence between Django's migration graph and physical database tables, leading to catastrophic errors in future deployments.

---

## 14. 7 Golden Invariant Rules for Production Migrations

1. **Never add a `NOT NULL` column with a default on massive tables**: Use the 4-step Expand-and-Contract protocol.
2. **Never import models directly in `RunPython`**: Always use `apps.get_model('app', 'Model')`.
3. **Always supply `reverse_code` in `RunPython`**: Ensure every migration is fully reversible.
4. **Always create indexes concurrently**: Use `AddIndexConcurrently` with `atomic = False` on high-traffic tables.
5. **Always set `lock_timeout` before DDL statements**: Prevent migration queue pileups from taking down active traffic.
6. **Mark one-off data backfills as `elidable=True`**: Keep squashed baselines lean and clean.
7. **Always enforce `makemigrations --check` in CI/CD**: Prevent uncommitted migrations from causing production drift.

---

## 15. Zero-Downtime Migration Strategy Decision Matrix

| Operation | Direct SQL Approach | Risk | Zero-Downtime Enterprise Pattern |
|:----------|:--------------------|:-----|:---------------------------------|
| **Add Nullable Column** | `ADD COLUMN col VARCHAR` | Minimal | Safe to execute directly in pre-deploy |
| **Add NOT NULL Column** | `ADD COLUMN col VARCHAR NOT NULL` | Table lock & 500s | Add nullable -> Backfill -> Add Check Constraint |
| **Drop Column** | `DROP COLUMN col` | Crashes old code (v1) | Remove from code -> Deploy -> Drop column later |
| **Rename Column** | `RENAME COLUMN col TO new_col` | Crashes old code (v1) | Add new -> Dual write -> Backfill -> Read new -> Drop old |
| **Create Index** | `CREATE INDEX ...` | Table write lock | `AddIndexConcurrently` with `atomic = False` |
| **Data Backfill** | `UPDATE table SET ...` | Unbounded row locks | Chunked batches of 2,000 rows via `iterator()` |
| **Alter Enum** | `ALTER TYPE ...` | Lock table | Add new value via `atomic = False` |
