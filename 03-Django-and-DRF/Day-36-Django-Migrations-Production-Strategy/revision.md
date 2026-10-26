# Day 36 Rapid Revision: Safe Database Migrations & Production Strategy

A high-yield, 5-minute reference guide covering the migration DAG, Expand-and-Contract zero-downtime workflows, concurrent indexing, and production guardrails.

---

## 1. Quick Decision Matrix: Safe Migration Protocols

| Schema Change Operation | Danger Level | Naive Antipattern | Zero-Downtime Enterprise Protocol |
|:------------------------|:-------------|:------------------|:----------------------------------|
| **Add Nullable Column** | Low | None | Safe to add directly in pre-deployment. |
| **Add NOT NULL Column** | **CRITICAL** | `ADD COLUMN col NOT NULL DEFAULT 'x'` | Add as nullable -> Chunked backfill -> `CHECK NOT VALID` -> Validate. |
| **Rename Column** | **CRITICAL** | `migrations.RenameField` | Add new -> Dual write -> Backfill -> Read new -> Drop old. |
| **Drop Column** | **HIGH** | `migrations.RemoveField` directly | Remove from code -> Deploy -> Drop in database later. |
| **Create Index** | **HIGH** | Standard `models.Index` | `AddIndexConcurrently` with `atomic = False`. |
| **Data Migration** | Medium | Direct import `from myapp.models` | Always use historical `apps.get_model('app', 'Model')`. |
| **Data Backfill** | **HIGH** | Single mass `UPDATE` query | Chunked updates in batches of 2,000 using `iterator()`. |

---

## 2. The 5 Non-Negotiable Migration Laws

1. **The Historical Model Law**: Never import models directly from Python files inside a `RunPython` migration. Always use `apps.get_model('app_name', 'ModelName')` to access frozen historical state.
2. **The Reversibility Law**: Never omit `reverse_code` in `RunPython`. Every data migration must support automated rollbacks in CI/CD pipelines.
3. **The Non-Atomic Indexing Law**: Always use `AddIndexConcurrently` with `atomic = False` when adding indexes to high-traffic production tables to prevent write lockouts.
4. **The Lock Timeout Law**: Always configure `lock_timeout = '2s'` before running DDL migrations in production to prevent lock queue avalanches from taking down live traffic.
5. **The CI/CD Validation Law**: Always enforce `python manage.py makemigrations --check --dry-run` in GitHub Actions to block pull requests with uncommitted migration files.

---

## 3. Essential Migration CLI Commands

```bash
# 1. Preview generated raw SQL statements without modifying database
python manage.py sqlmigrate orders 0002_add_status

# 2. View applied status of all migrations across apps
python manage.py showmigrations

# 3. Detect unapplied schema changes in CI/CD without writing files
python manage.py makemigrations --check --dry-run

# 4. Squash historical migrations into a clean baseline
python manage.py squashmigrations orders 0001 0045

# 5. Rollback an application to a specific previous migration
python manage.py migrate orders 0003
```

---

## 4. Production Code Blueprints

### 1. Concurrent Index Creation:
```python
# migrations/0005_order_cust_idx.py
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models

class Migration(migrations.Migration):
    # CRITICAL: Non-atomic execution required for concurrent indexing
    atomic = False

    dependencies = [("orders", "0004_previous")]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(fields=["customer", "created_at"], name="idx_orders_cust_created"),
        )
    ]
```

### 2. Safe Reversible Data Migration (`elidable=True`):
```python
def backfill_tiers(apps, schema_editor):
    User = apps.get_model("users", "User")
    for user in User.objects.filter(tier__isnull=True).iterator(chunk_size=2000):
        user.tier = "STANDARD"
        user.save(update_fields=["tier"])

def reverse_tiers(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(tier="STANDARD").update(tier=None)

class Migration(migrations.Migration):
    dependencies = [("users", "0002_add_tier_nullable")]
    operations = [
        migrations.RunPython(backfill_tiers, reverse_code=reverse_tiers, elidable=True)
    ]
```
