# Day 51: Senior & Staff Technical Interview Mastery

This guide presents the top 5 advanced architectural interview questions focusing on **Database Schema Migrations, Alembic Internals, Zero-Downtime Evolution, and DDL Concurrency Control**.

---

### Question 1: How does Alembic's `--autogenerate` work internally, and what are its fundamental limitations?

#### Architectural Answer
Alembic's autogeneration engine is a **catalog comparison engine**. When `alembic revision --autogenerate` is executed:
1. **Reflection Phase**: Alembic uses SQLAlchemy's `Inspector` to query system catalogs (`information_schema`, `pg_catalog` in PostgreSQL, or `sqlite_master` in SQLite) reflecting current tables, columns, indexes, foreign keys, and constraints.
2. **Metadata Inspection**: Alembic walks the Python AST and declarations in `target_metadata` (`Base.metadata`), collecting intended tables, types, and constraints.
3. **Set-Difference ($\Delta$) Computation**: Alembic computes the symmetric difference between reflected objects and target metadata objects:
   $$\Delta_{\text{create}} = \text{Metadata} \setminus \text{Catalog}$$
   $$\Delta_{\text{drop}} = \text{Catalog} \setminus \text{Metadata}$$
   $$\Delta_{\text{modify}} = \{(c_1, c_2) \in \text{Metadata} \cap \text{Catalog} \mid c_1 \neq c_2\}$$
4. **Code Generation**: The calculated diff operations are mapped into Mako template directives (`op.create_table`, `op.add_column`, `op.alter_column`) in `script.py.mako`.

#### Fundamental Limitations
- **No Intention History (Renames)**: Relational catalogs store current states, not mutation intent. Renaming `name` to `full_name` is indistinguishable from deleting `name` and adding `full_name`. Autogenerate will generate a destructive `drop_column` + `add_column` sequence unless manually corrected.
- **Type Alterations Disabled by Default**: By default, `compare_type=False`. Changing `String(50)` to `String(255)` or `Integer` to `BigInteger` is silently ignored unless explicitly configured in `env.py`.
- **Database-Specific Features**: Views, stored procedures, triggers, row-level security (RLS) policies, and custom database extensions (e.g., PostGIS geometry types or Citext) are completely invisible to reflection.

#### Staff-Level Recommendation
Treat `--autogenerate` strictly as a **scaffolding draft tool**, never as an autonomous deployment engine. Every generated migration file must undergo human code review and peer audit before merging into `main`.

---

### Question 2: Describe the Expand/Contract (Parallel Run) migration pattern for achieving zero downtime during breaking schema changes.

#### Architectural Answer
In modern distributed deployments (Kubernetes, AWS ECS), deployments occur progressively via rolling updates. During deployment, pods running the legacy application version ($v_1$) and new pods running the updated version ($v_2$) operate concurrently against the same shared database cluster.

If a migration immediately drops or renames a column, legacy $v_1$ pods will immediately fail with `UndefinedColumn` errors, causing a 500 error spike.

The **Expand/Contract Pattern** breaks breaking schema modifications into three asynchronous release phases:

```
+-------------------------------------------------------------------------------+
| PHASE 1: EXPAND                                                               |
| DDL Migration  : Add new column `full_name` as NULLABLE.                       |
| App Deploy v1.1: Reads from `name`. Writes to both `name` AND `full_name`.     |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
| PHASE 2: BACKFILL (Data Migration)                                            |
| Background Job : Batched script updates historic records:                     |
|                  `UPDATE users SET full_name = name WHERE full_name IS NULL;` |
| Verification   : Verify 100% of existing rows have populated `full_name`.     |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
| PHASE 3: CONTRACT                                                             |
| App Deploy v1.2: Reads and writes ONLY to `full_name`. No references to `name`.|
| Verification   : Terminate and drain all legacy v1.1 pods.                    |
| DDL Migration  : Drop deprecated `name` column: `op.drop_column('users', 'name')`
+-------------------------------------------------------------------------------+
```

#### Why Server Defaults and Nullability Matter
During Phase 1, `full_name` must be either `nullable=True` or have a database-level `server_default`. If made `nullable=False` without a default, any `INSERT` issued by legacy $v_1$ pods (which do not supply `full_name`) will fail database constraints.

---

### Question 3: How do PostgreSQL DDL locks affect high-concurrency production databases during migrations, and how do you mitigate lock contention?

#### Architectural Answer
In PostgreSQL, nearly all DDL operations acquire an **`ACCESS EXCLUSIVE`** lock on the target table. An `ACCESS EXCLUSIVE` lock blocks **every other transaction**, including read-only `SELECT` statements!

#### The Lock Queue Disaster Scenario
```
1. Long-Running Query : SELECT * FROM orders ... (Running for 30 seconds)
2. Migration Script    : ALTER TABLE orders ADD COLUMN ... (Requests ACCESS EXCLUSIVE)
                         -> Queued waiting for Query 1 to finish!
3. Incoming Queries    : Hundreds of incoming SELECTs for API traffic arrive.
                         -> Queued BEHIND the Migration Script lock request!
4. System Failure      : Connection pool is exhausted within seconds. The entire API halts.
```

Even if the `ALTER TABLE` operation takes only 2 milliseconds to apply, waiting for existing queries to finish causes catastrophic request pile-up.

#### Staff-Level Mitigation Rules
1. **Set Strict Lock Timeouts**:
   Always set a tight `lock_timeout` in migration transactions:
   ```sql
   SET lock_timeout = '2s';
   ALTER TABLE orders ADD COLUMN status VARCHAR(20);
   ```
   If the lock cannot be acquired within 2 seconds, the migration fails immediately, releasing the lock queue and preserving production API availability.
2. **Concurrent Index Builds**:
   Never run standard `CREATE INDEX` on live tables. Use `CREATE INDEX CONCURRENTLY` wrapped in `autocommit_block()`.
3. **Avoid Full Table Rewrites**:
   In PostgreSQL 11+, adding a column with a `server_default` is an $O(1)$ metadata update. In older versions or when modifying types without binary compatibility, PostgreSQL rewrites the entire physical table on disk, holding an exclusive lock for the entire duration.

---

### Question 4: What is the purpose of `alembic merge`, and how do you resolve divergent revision heads in a multi-developer Git repository?

#### Architectural Answer
In enterprise environments with hundreds of developers shipping code daily across multiple Git feature branches, two developers often create migrations branching off the same common ancestor:
- Developer A generates revision `rev_a` with `down_revision = 'rev_base'`.
- Developer B generates revision `rev_b` with `down_revision = 'rev_base'`.

When both branches merge into `main`, Git merges the code cleanly because each migration is a distinct Python file in `alembic/versions/`. However, Alembic's DAG now has two simultaneous "heads" (`rev_a` and `rev_b`). Running `alembic upgrade head` aborts with `Multiple head revisions are present`.

#### The Resolution Protocol
```bash
# 1. Identify active heads in the script directory
alembic heads

# 2. Reconcile both branches into a unified merge revision
alembic merge -m "merge rev_a and rev_b branches" rev_a rev_b
```

#### Anatomical Breakdown of a Merge Revision
The generated merge file contains a tuple in its `down_revision` field:
```python
# Revision ID: rev_merge
# Revises: ('rev_a', 'rev_b')
revision = 'rev_merge'
down_revision = ('rev_a', 'rev_b')

def upgrade():
    pass # No DDL operations needed; purely a DAG reconciliation point!

def downgrade():
    pass
```
This tells Alembic's topological sorter that `rev_merge` depends on both branches having completed. Running `alembic upgrade head` will now execute both branches followed by `rev_merge`, successfully restoring a linear state graph.

---

### Question 5: Why is importing application ORM models inside Alembic revision scripts an antipattern, and how should historical data migrations be implemented instead?

#### Architectural Answer
Application ORM models are living, mutable software contracts that evolve with every commit. Alembic migration scripts, however, are immutable historical records that represent schema states at specific points in time.

#### The Failure Mechanism
Consider a migration written in 2024:
```python
# DANGEROUS: Importing the current User model
from myapp.models import User

def upgrade():
    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)
    for user in session.query(User).all():
        user.is_verified = True
    session.commit()
```
In 2025, an engineer refactors the application, renaming `User.username` to `User.handle` and deleting `User.legacy_flag`.

When a new test environment or staging cluster attempts to build the database from scratch (`alembic upgrade head`), the 2024 migration executes. Python imports the 2025 `User` model, which queries the database expecting `handle`. But the database at that historical step in the migration graph only has `username`! The migration crashes with `ProgrammingError: column "handle" does not exist`.

#### Staff-Level Production Pattern
Data migrations must be completely self-contained. Always use SQLAlchemy Core `table()` and `column()` declarations defined locally within the migration function:

```python
from sqlalchemy.sql import table, column
import sqlalchemy as sa

def upgrade():
    # Define an immutable, local snapshot of only the columns needed
    historical_users = table(
        'users',
        column('id', sa.Integer),
        column('is_verified', sa.Boolean),
    )

    bind = op.get_bind()
    # Execute batch set operation without instantiating application models
    bind.execute(
        historical_users.update()
        .where(historical_users.c.is_verified == False)
        .values(is_verified=True)
    )
```
This decouples the migration script from future application codebase modifications forever.

---
*Senior interviewers look for candidates who understand schema evolution as an operational distributed system rather than simple local tool syntax.*
