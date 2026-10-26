# Day 36: Common Architectural Mistakes & Antipatterns in Django Migrations

Database migrations operate directly against persistent production state. A code bug can be hotfixed in minutes, but a flawed database migration can lock multi-million row tables, corrupt relational graphs, break zero-downtime rolling deployments, and take hours of high-risk downtime to repair.

Here are the **7 most dangerous migration antipatterns**, why they cause production outages, and how senior engineers remediate them.

---

### Mistake 1: Adding a `NOT NULL` Column with a Default on Massive Tables

#### The Flawed Approach:
Adding a non-nullable column with a default directly to a 20-million row table:

```python
# migrations/0005_add_status.py - TABLE LOCK DISASTER
migrations.AddField(
    model_name="transaction",
    name="status",
    field=models.CharField(max_length=20, default="PENDING"),  # NOT NULL by default!
)
```

#### Why It Fails in Production:
In PostgreSQL versions prior to 11 (and on tables with volatile default expressions or complex constraints):
1. Adding a `NOT NULL` column with a default forces PostgreSQL to rewrite the **entire physical table on disk** to populate the column on every existing row.
2. The migration acquires an **`AccessExclusiveLock`** on the table.
3. While this lock is held (which can take 5 to 30 minutes on a 50 GB table), **all incoming HTTP requests trying to read or write to the table are queued up**.
4. The database connection pool is instantly exhausted, Gunicorn workers hang waiting for DB sockets, and the entire platform crashes with HTTP 504 Gateway Timeouts.

#### The Senior Engineer Remediation:
Use the **Expand and Contract** multi-step pattern:
1. Add the column as `null=True` (instantaneous metadata change, zero table rewrite).
2. Backfill existing records in the background using chunked batches.
3. Add a PostgreSQL `CHECK (status IS NOT NULL) NOT VALID` constraint (instant lock).
4. Run `VALIDATE CONSTRAINT` (scans table concurrently without blocking writes).
5. Flip `null=False` in Django models.

---

### Mistake 2: Importing Models Directly in `RunPython` Data Migrations

#### The Flawed Approach:
```python
# migrations/0008_backfill_slugs.py - FATAL IMPORT ERROR
from apps.products.models import Product  # ❌ DIRECT IMPORT!

def populate_slugs(apps, schema_editor):
    for p in Product.objects.all():
        p.slug = slugify(p.name)
        p.save()
```

#### Why It Fails in Production:
`from apps.products.models import Product` imports the **current Python code state** of the model as it exists today.
Fast forward 6 months:
- Another engineer adds a new mandatory column `sku_number` to `Product` in migration `0015`.
- A new developer joins the team or a fresh CI test database is spun up from scratch.
- Django runs migrations sequentially: `0001` -> `0002` -> ... -> `0008`.
- When migration `0008` runs, it imports `Product` from Python code. The Python model expects `sku_number` to exist, but in the database at step `0008`, `sku_number` **has not been created yet**!
- The migration crashes with `django.db.utils.ProgrammingError: column "sku_number" does not exist`.

#### The Senior Engineer Remediation:
Always use **historical models** via `apps.get_model()`:

```python
def populate_slugs(apps, schema_editor):
    # ✅ Retrieves the frozen model state as it existed at this EXACT migration:
    Product = apps.get_model("products", "Product")
    for p in Product.objects.iterator(chunk_size=2000):
        p.slug = slugify(p.name)
        p.save(update_fields=["slug"])
```

---

### Mistake 3: Creating Indexes Without `AddIndexConcurrently`

#### The Flawed Approach:
Adding an index to a high-traffic production table via standard `models.Index`:

```python
# migrations/0010_add_created_idx.py - WRITE LOCKOUT
migrations.AddIndex(
    model_name="order",
    index=models.Index(fields=["created_at"], name="idx_order_created"),
)
```

#### Why It Fails in Production:
Standard `CREATE INDEX` acquires a **`ShareLock`** on the target table.
- While reads are permitted, **all write operations (`INSERT`, `UPDATE`, `DELETE`) are blocked** until the entire index is built on disk.
- If building the index takes 10 minutes on a 100-million row table, customer checkout queries and background payment webhooks fail or time out for 10 continuous minutes!

#### The Senior Engineer Remediation:
Use PostgreSQL's `AddIndexConcurrently` with `atomic = False`:

```python
from django.contrib.postgres.operations import AddIndexConcurrently

class Migration(migrations.Migration):
    # CRITICAL: Concurrent index creation cannot run inside a transaction block!
    atomic = False

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(fields=["created_at"], name="idx_order_created"),
        )
    ]
```

---

### Mistake 4: Omitting `reverse_code` in `RunPython` (Irreversible Migrations)

#### The Flawed Approach:
```python
# migrations/0012_encrypt_tokens.py - ONE-WAY TICKET
migrations.RunPython(encrypt_tokens)  # No reverse_code supplied!
```

#### Why It Fails in Production:
In modern CI/CD deployment pipelines, if a canary rollout or smoke test fails post-deployment, the automated release engine triggers a rollback:
`python manage.py migrate app_name 0011`
Because `0012` has no `reverse_code`, Django immediately raises:
`django.db.migrations.exceptions.IrreversibleError: 0012_encrypt_tokens is irreversible`
The automated rollback crashes, leaving the system in a broken, half-deployed state requiring high-stress manual DBA intervention during an active incident.

#### The Senior Engineer Remediation:
Always provide a reverse callback (or `migrations.RunPython.noop` if the reverse operation is intentionally a no-op):

```python
def reverse_encrypt(apps, schema_editor):
    # Reverse logic or safe rollback handling
    pass

migrations.RunPython(encrypt_tokens, reverse_code=reverse_encrypt)
# Or if irreversible by business logic:
migrations.RunPython(encrypt_tokens, reverse_code=migrations.RunPython.noop)
```

---

### Mistake 5: Renaming Columns or Models Directly in Rolling Deployments

#### The Flawed Approach:
Renaming a column using `RenameField`:

```python
migrations.RenameField(
    model_name="user",
    old_name="phone",
    new_name="mobile_number",
)
```

#### Why It Fails in Production:
In Kubernetes or AWS ECS, container updates follow a **rolling deployment**:
1. Migration runs at time $T_0$: PostgreSQL column `phone` is renamed to `mobile_number`.
2. Old container pods (v1) are still active handling live traffic for the next 2 minutes.
3. A user visits the website; the request hits Pod v1.
4. Pod v1 executes: `SELECT phone FROM users;`
5. PostgreSQL returns: `column "phone" does not exist` -> **HTTP 500 Server Error** for thousands of users until all v1 pods are terminated!

#### The Senior Engineer Remediation:
Follow the **3-Deploy Zero-Downtime Renaming Protocol**:
- **Deploy 1**: Add `mobile_number` as nullable. Deploy code with dual-writing to both `phone` and `mobile_number`.
- **Deploy 2**: Backfill historical rows. Switch application reads to `mobile_number`.
- **Deploy 3**: Remove `phone` from code. Run final migration to drop the obsolete `phone` column.

---

### Mistake 6: Unchunked Data Backfills Inside Migration Transactions

#### The Flawed Approach:
Running an unchunked mass update inside a migration:

```python
def backfill_all_orders(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    # ❌ Updating 5 million rows in a single SQL statement!
    Order.objects.filter(is_active=True).update(tier="STANDARD")
```

#### Why It Fails in Production:
1. Updating 5 million rows in one statement acquires exclusive row locks on 5 million rows simultaneously.
2. In PostgreSQL, this causes massive **Write-Ahead Log (WAL) bloat** and replication lag across read-replicas.
3. Any active user attempting to update an order that is locked by the migration will hang until the migration completes.

#### The Senior Engineer Remediation:
Chunk updates into small batches of 1,000–5,000 rows, committing between batches or offloading to Celery:

```python
def backfill_in_chunks(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    batch_size = 2000
    last_id = 0

    while True:
        # Fetch chunk of IDs
        chunk_ids = list(
            Order.objects.filter(id__gt=last_id, is_active=True)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )
        if not chunk_ids:
            break

        Order.objects.filter(id__in=chunk_ids).update(tier="STANDARD")
        last_id = chunk_ids[-1]
```

---

### Mistake 7: Using `--fake` to "Fix" Failed Production Migrations

#### The Flawed Approach:
A migration fails in production due to a constraint violation. An engineer runs:
`python manage.py migrate app_name 0005 --fake`
to make the error "go away".

#### Why It Fails in Production:
`--fake` inserts a record into `django_migrations` pretending the migration was executed, **without applying any of the underlying schema changes to the database**.
- Django's migration graph now believes column `status` exists in PostgreSQL, but physical table `orders` does NOT have column `status`.
- The very next deployment runs migration `0006_modify_status`. Django compiles SQL modifying `status`, and PostgreSQL crashes with `column "status" does not exist`.
- Now the database schema, migration files, and `django_migrations` table are completely desynchronized!

#### The Senior Engineer Remediation:
Never use `--fake` unless you are adopting an existing legacy table whose schema is **100% verified to match** the migration AST.
If a migration fails:
1. Fix the underlying data constraint or schema conflict.
2. Roll back cleanly using standard tools or revert the pull request.

---

### Summary Checklist: Migration Antipattern Detection Matrix

| # | Antipattern | Primary Consequence | Senior Remediation |
|---|-------------|---------------------|--------------------|
| 1 | `NOT NULL` with default | Table lock & 504 timeouts | Expand-and-Contract (Add nullable -> Backfill -> Validate) |
| 2 | Direct model import in `RunPython` | Future migration crashes | Always use `apps.get_model('app', 'Model')` |
| 3 | Standard `AddIndex` on large table | Write lockouts on live traffic | Use `AddIndexConcurrently` with `atomic = False` |
| 4 | Missing `reverse_code` | Rollback crashes (`IrreversibleError`) | Always supply reversible callbacks |
| 5 | Instant column rename | 500 errors on v1 pods during deploy | 3-Deploy Expand & Contract (Dual Write) |
| 6 | Unchunked mass update | WAL bloat & multi-table row locks | Chunked updates in batches of 2,000 |
| 7 | Indiscriminate `--fake` | Schema desynchronization & deadlocks | Fix root cause; verify DB schema alignment |
