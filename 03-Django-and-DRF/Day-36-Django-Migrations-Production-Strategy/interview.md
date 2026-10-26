# Day 36: Top 5 Senior Backend Interview Questions — Django Database Migrations & Production Strategy

---

### Question 1: How does the Expand and Contract pattern achieve zero-downtime database migrations in Kubernetes or rolling deployment environments? Detail how you would rename a column or add a `NOT NULL` constraint without 500 errors.

#### Architectural Answer:
In high-scale cloud platforms, deployments follow a **rolling update** strategy: new container pods (v2) are deployed gradually while old pods (v1) remain active to serve live customer traffic.

#### The Fundamental Problem:
If you execute a single migration that immediately alters or drops a column (e.g. `ALTER TABLE users RENAME COLUMN phone TO mobile;`), running v1 pods will execute queries containing `SELECT phone FROM users;` and immediately fail with **HTTP 500 errors** because PostgreSQL no longer has a column named `phone`.

#### The Expand-and-Contract Architecture:

```
+─────────────────────────────────────────────────────────────────────────────+
|                     EXPAND AND CONTRACT RENAME LIFECYCLE                    |
+─────────────────────────────────────────────────────────────────────────────+
| PHASE 1: EXPAND (Additive Migration & Dual Write)                           |
|   - Add column `mobile` as NULLABLE.                                        |
|   - Deploy Code v1.1: Reads from `phone`, writes to BOTH `phone` & `mobile`. |
|   - Both v1.0 and v1.1 pods can run side-by-side with zero errors.          |
|                                                                             |
| PHASE 2: BACKFILL & SWITCH READS                                            |
|   - Run background task to copy historical data from `phone` to `mobile`.   |
|   - Deploy Code v1.2: Reads from `mobile`, writes to BOTH `phone` & `mobile`.|
|                                                                             |
| PHASE 3: CONTRACT (Subtractive Migration)                                   |
|   - Deploy Code v2.0: Reads and writes ONLY to `mobile`.                    |
|   - Once all v1 pods are terminated, drop the `phone` column in PostgreSQL. |
+─────────────────────────────────────────────────────────────────────────────+
```

#### Adding a `NOT NULL` Column Safely:
Adding a column with `NOT NULL DEFAULT 'x'` on a 50-million row table in PostgreSQL holds an `AccessExclusiveLock`, causing full table rewrites and outages.
The zero-downtime protocol:
1. Add the column as `NULLABLE`.
2. Backfill existing records in chunks of 5,000 using background workers.
3. Add a PostgreSQL constraint: `ALTER TABLE ... ADD CONSTRAINT ... CHECK (col IS NOT NULL) NOT VALID;` (Instantaneous lock!).
4. Run `VALIDATE CONSTRAINT` (Scans table concurrently without blocking writes).
5. Flip `null=False` in Django models.

---

### Question 2: Why must engineers strictly avoid importing models directly (`from myapp.models import MyModel`) inside a `RunPython` data migration? How does `apps.get_model()` protect against migration desynchronization?

#### Architectural Answer:

#### The Failure Mechanism:
Directly importing a model:
```python
from apps.billing.models import Invoice  # ❌ DANGEROUS!
```
imports the **live, current Python definition** of the model as it exists in the codebase today.

#### Why this breaks historical replay:
Assume migration `0005` uses direct import to backfill invoice totals.
Six months later, an engineer adds a new field `tax_identifier` to `Invoice` in migration `0022`.
Now, when a new staging environment, fresh developer laptop, or CI pipeline runs migrations from scratch (`0001` to `0030`):
1. Migration `0005` executes.
2. It imports `Invoice` from Python code.
3. The Python model class expects `tax_identifier` to exist in the database.
4. But the physical database is only at step `0005`—`tax_identifier` won't be created until step `0022`!
5. Django crashes with `django.db.utils.ProgrammingError: column "tax_identifier" does not exist`.

#### The `apps.get_model()` Solution:
```python
Invoice = apps.get_model("billing", "Invoice")
```
`apps.get_model()` inspects the **migration state history** and returns a dynamically constructed **historical model class** containing *only* the fields and relationships that physically existed at migration `0005`. It is completely decoupled from future codebase changes, ensuring migrations can be replayed from scratch indefinitely.

---

### Question 3: How does `CREATE INDEX CONCURRENTLY` in PostgreSQL prevent write lockouts, and why does Django require `atomic = False` when using `AddIndexConcurrently`?

#### Architectural Answer:

#### Standard Indexing vs Concurrent Indexing:
- **Standard `CREATE INDEX`**: Acquires a `ShareLock` on the target table. It permits `SELECT` queries, but **strictly blocks all write operations (`INSERT`, `UPDATE`, `DELETE`)**. On a table with 50 million rows, building an index can take 15 minutes, halting all transactional writes.
- **`CREATE INDEX CONCURRENTLY`**: Builds the index across multiple table scans without acquiring a write lock:
  1. Phase 1: Registers the index in PostgreSQL catalogs.
  2. Phase 2: Performs a table scan to build the index while live writes continue uninterrupted.
  3. Phase 3: Performs a second scan to index any rows that were inserted or modified during Phase 2.

#### Why `atomic = False` is Mandatory:
In PostgreSQL, transactions guarantee atomicity—if a statement fails, all changes within the transaction block (`BEGIN ... COMMIT`) are rolled back.
However, `CREATE INDEX CONCURRENTLY` **cannot execute inside an active transaction block** because:
1. It must commit its initial catalog changes immediately so concurrent write transactions can see the pending index and update it.
2. If it were inside a transaction, it would hold locks that interfere with concurrent transactions.

Attempting to run `CREATE INDEX CONCURRENTLY` inside an atomic migration raises:
`psycopg2.errors.ActiveSqlTransaction: CREATE INDEX CONCURRENTLY cannot run inside a transaction block`
Setting `atomic = False` tells Django not to wrap the migration operations in `BEGIN ... COMMIT`.

---

### Question 4: What happens when an `ALTER TABLE` statement requests an `AccessExclusiveLock` behind a long-running `SELECT` query in PostgreSQL? How do you configure `lock_timeout` to protect live production traffic?

#### Architectural Answer:

#### The Lock Queue Deadlock Avalanche:
In PostgreSQL, locks are queued in strict First-In, First-Out (FIFO) order:
1. A slow reporting query $Q_1$ is running on the `orders` table (takes 30 seconds). It holds an `AccessShareLock`.
2. A deployment migration runs an `ALTER TABLE orders ADD COLUMN ...` statement ($M$). It requires an **`AccessExclusiveLock`**.
3. Because $Q_1$ holds the table, $M$ waits in the lock queue.
4. **The Disaster**: Every subsequent fast web request ($Q_2, Q_3, \dots, Q_N$) arriving from users requesting `SELECT * FROM orders` **queues up BEHIND migration $M$** in the lock queue!
5. Within 5 seconds, hundreds of user requests are stuck waiting behind the migration lock request. Gunicorn worker threads are exhausted, database connection pools max out, and the entire production API becomes completely unresponsive.

```
[Running Query Q1] ──> [Migration ALTER TABLE (Waiting)] ──> [User Query Q2 (Blocked!)]
(AccessShareLock)      (Needs AccessExclusiveLock)           (Blocked behind migration!)
```

#### The `lock_timeout` Defense:
Senior engineers always inject lock timeouts before executing DDL migrations:
```sql
SET lock_timeout = '2s';
ALTER TABLE orders ADD COLUMN status_code VARCHAR(20);
```
If the migration cannot acquire the table lock within 2 seconds (because of an active query), PostgreSQL **aborts the migration immediately with an error**.
The active web traffic is completely unaffected, no queue pileup occurs, and the deployment runner can retry the migration during a quieter traffic window.

---

### Question 5: If a production migration fails midway through execution, how do you recover? Differentiate between PostgreSQL's transactional DDL and MySQL's non-transactional DDL, and explain why `--fake` can be dangerous.

#### Architectural Answer:

#### PostgreSQL vs MySQL DDL Atomicity:
- **PostgreSQL (Transactional DDL)**: PostgreSQL wraps DDL statements (`CREATE TABLE`, `ALTER TABLE`) inside standard ACID transactions. If a migration with 5 operations fails on operation #4, PostgreSQL automatically rolls back operations #1, #2, and #3. The database schema and `django_migrations` table remain completely clean and uncorrupted.
- **MySQL / Oracle (Non-Transactional DDL)**: DDL statements trigger an **implicit commit**. If a migration fails on operation #4, operations #1, #2, and #3 remain permanently applied to the MySQL table, but Django records the migration as unapplied. The database is now in a broken, semi-migrated state that cannot be cleanly re-run or rolled back without manual DBA surgery.

#### The Danger of `--fake`:
Running `python manage.py migrate app_name 0005 --fake` writes a record into `django_migrations` telling Django that migration `0005` has been applied, **without executing any of the actual SQL schema changes**.

#### Why this causes production catastrophes:
1. If an engineer uses `--fake` to bypass an error on `AddField("users", "tax_id")`, the physical table `users` never receives the `tax_id` column.
2. The code deployed to production assumes `tax_id` exists and issues queries selecting it, crashing with `ProgrammingError: column "tax_id" does not exist`.
3. Subsequent migrations in future sprints depending on `tax_id` will fail because the physical schema and the Django migration graph are completely out of sync.

#### Safe Recovery Protocol:
1. Inspect the exact failure reason using `python manage.py showmigrations` and PostgreSQL logs.
2. If using PostgreSQL, resolve the data conflict and re-run `migrate`.
3. If using MySQL, manually inspect the database schema, undo the partial DDL operations by hand via raw SQL, and re-run.
4. Only use `--fake` when retrofitting existing legacy tables whose physical structure has been independently verified to match the migration state.
