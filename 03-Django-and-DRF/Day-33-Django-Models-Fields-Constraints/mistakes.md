# Day 33: Common Architectural Mistakes & Antipatterns in Django Models

Database schema mistakes are the most expensive class of errors in backend engineering. Unlike application code bugs that can be patched with a single commit, flawed database schemas lead to corrupt records, migration deadlocks, degraded indexing, and painful zero-downtime data migrations.

Here are the **7 most prevalent model and indexing antipatterns**, why they fail in production, and how senior engineers remediate them.

---

### Mistake 1: Setting `null=True` on `CharField` or `TextField`

#### The Flawed Approach:
```python
# models.py - DANGEROUS
class UserProfile(models.Model):
    bio = models.CharField(max_length=255, null=True, blank=True)
    middle_name = models.CharField(max_length=50, null=True, blank=True)
```

#### Why It Fails in Production:
This introduces the dreaded **"Dual Empty State"** bug:
- A user who never enters a middle name may have `middle_name = NULL` in PostgreSQL.
- A user who clears their middle name in an HTML form or DRF serializer sends `""` (empty string), resulting in `middle_name = ""` in PostgreSQL.

Now your application has two distinct representations for "no data":
1. `WHERE middle_name IS NULL` does not return users with `""`.
2. `WHERE middle_name = ''` does not return users with `NULL`.
3. `COUNT(middle_name)` in SQL counts empty strings, but excludes `NULL`.
4. If you later add a `unique=True` constraint, SQL standard allows multiple `NULL` values, but rejects multiple empty strings `""`, causing sudden `IntegrityError` crashes in production.

#### The Senior Engineer Remediation:
Never set `null=True` on string fields. Use `blank=True` and let Django store empty strings `""` uniformly:

```python
# models.py - PRODUCTION GRADE
class UserProfile(models.Model):
    # Empty state is consistently stored as "" (never NULL)
    bio = models.CharField(max_length=255, blank=True, default="")
    middle_name = models.CharField(max_length=50, blank=True, default="")
```
*(Exception: When a string field has `unique=True` and is optional, `null=True` is occasionally required in legacy databases, though modern Django solves this via partial `UniqueConstraint(condition=Q(...))`)*.

---

### Mistake 2: Using `FloatField` for Currency or Ledger Balances

#### The Flawed Approach:
```python
# models.py - CRITICAL FINANCIAL RISK
class Wallet(models.Model):
    balance = models.FloatField(default=0.0)
```

#### Why It Fails in Production:
CPUs represent `float` using binary IEEE-754 floating-point arithmetic. Common base-10 decimal fractions cannot be represented precisely in binary:
```python
>>> 0.1 + 0.2
0.30000000000000004
```
In an e-commerce or fintech platform processing 100,000 ledger transactions daily, compounding rounding errors cause balances to diverge from actual bank settlements, creating accounting discrepancies and audit failures.

#### The Senior Engineer Remediation:
Always use `DecimalField` with explicit `max_digits` and `decimal_places`. PostgreSQL backs this with the exact `NUMERIC` or `DECIMAL` fixed-point engine:

```python
# models.py - EXACT FINANCIAL PRECISION
class Wallet(models.Model):
    # Backed by NUMERIC(14, 4) in PostgreSQL - zero rounding loss
    balance = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=decimal.Decimal("0.0000"),
    )
```

---

### Mistake 3: Passing Mutable Defaults (`default=[]` or `default={}`)

#### The Flawed Approach:
```python
# models.py - PYTHON MEMORY HAZARD
class TenantSetting(models.Model):
    feature_flags = models.JSONField(default={})
    notification_tags = models.JSONField(default=[])
```

#### Why It Fails in Production:
In Python, default arguments are evaluated **once** when the module is imported. The exact same dictionary or list instance in memory is assigned as the default for every newly instantiated model:
```python
tenant_a = TenantSetting()
tenant_a.feature_flags["dark_mode"] = True

tenant_b = TenantSetting()
print(tenant_b.feature_flags["dark_mode"])  # True! Accidental cross-tenant leakage in memory!
```

#### The Senior Engineer Remediation:
Always pass callables (`default=dict` or `default=list`):

```python
# models.py - SAFE CALLABLE DEFAULTS
class TenantSetting(models.Model):
    # A fresh dict/list is generated for each new instance
    feature_flags = models.JSONField(default=dict, blank=True)
    notification_tags = models.JSONField(default=list, blank=True)
```

---

### Mistake 4: Adding Unindexed `ordering` in Model `class Meta`

#### The Flawed Approach:
```python
# models.py - SILENT PERFORMANCE KILLER
class AuditLog(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    message = models.TextField()

    class Meta:
        ordering = ["-created_at"]  # Without a database index!
```

#### Why It Fails in Production:
When `ordering` is specified in `class Meta`, Django automatically appends `ORDER BY created_at DESC` to **EVERY** query executed against this table, including:
- `AuditLog.objects.count()` (in some legacy queries or subqueries)
- `AuditLog.objects.filter(user_id=123)`
- Chunked iteration via `iterator()`

If `AuditLog` has 5 million rows and `created_at` lacks an index, PostgreSQL must perform an expensive, disk-spilling **external merge sort** on every single query, driving database CPU to 100%.

#### The Senior Engineer Remediation:
Either index the ordering column, or remove `ordering` from `Meta` and specify `.order_by()` explicitly in the views/managers that require it:

```python
# models.py - OPTIMIZED
class AuditLog(models.Model):
    # Add db_index=True if default ordering is mandatory
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    message = models.TextField()

    class Meta:
        ordering = ["-created_at"]
```

---

### Mistake 5: Assuming `Model.save()` Executes Validation (`clean()`)

#### The Flawed Approach:
```python
class Event(models.Model):
    start_date = models.DateField()
    end_date = models.DateField()

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError("End date cannot precede start date.")

# In some API endpoint or background task:
event = Event.objects.create(start_date="2026-05-10", end_date="2026-05-01")  # SAVES SUCCESSFULLY!
```

#### Why It Fails in Production:
In Django, **`Model.save()` DOES NOT call `clean()` or `full_clean()`!**
- `full_clean()` is only automatically invoked by Django HTML Forms and ModelForms.
- Direct model creation (`Event.objects.create()`, `event.save()`, DRF `ModelSerializer` without explicit overrides, and bulk operations) completely bypasses Python-level `clean()` methods!
- Invalid data enters your database, causing downstream crashes.

#### The Senior Engineer Remediation:
1. Back application validations with database-level `CheckConstraint` (impossible to bypass).
2. Override `save()` to explicitly call `full_clean()` if business-critical:

```python
# models.py - DEFENSE IN DEPTH
class Event(models.Model):
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        constraints = [
            # Enforced by PostgreSQL engine even on raw SQL or bulk_create()
            models.CheckConstraint(
                check=models.Q(end_date__gte=models.F("start_date")),
                name="check_event_dates_chronological",
            )
        ]

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot precede start date."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
```

---

### Mistake 6: Violating the Leftmost Prefix Rule on Composite Indexes

#### The Flawed Approach:
Creating a multi-column composite index:
```python
class CustomerOrder(models.Model):
    tenant = models.ForeignKey("Tenant", on_delete=models.CASCADE)
    status = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"], name="idx_tenant_stat_date")
        ]
```
And running queries that filter only by `status` or `created_at`:
```python
# Query:
CustomerOrder.objects.filter(status="SHIPPED")
```

#### Why It Fails in Production:
A composite B-Tree index on `(A, B, C)` is physically sorted first by `A`, then by `B`, then by `C`.
If a query does not filter on leading column `A`, the database engine cannot perform a binary search tree lookup and is forced to fall back to a **Full Table Scan (Seq Scan)** across millions of rows.

#### The Senior Engineer Remediation:
Design composite indexes based on query access patterns following the **Equality-Sort-Range (ESR)** rule:
1. Place equality fields with high cardinality first (`tenant`).
2. If queries frequently search by `status` independently across all tenants, create a dedicated index on `status`:
```python
class Meta:
    indexes = [
        models.Index(fields=["tenant", "status", "created_at"]),
        models.Index(fields=["status"]),  # Dedicated index for cross-tenant status queries
    ]
```

---

### Mistake 7: Using Legacy `unique_together` on Soft-Delete Models

#### The Flawed Approach:
```python
class OrganizationMember(models.Model):
    organization = models.ForeignKey("Organization", on_delete=models.CASCADE)
    email = models.EmailField()
    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [["organization", "email"]]
```

#### Why It Fails in Production:
When a member leaves the organization, they are soft-deleted: `member.is_deleted = True`.
Six months later, they rejoin or are re-invited with the same email. Attempting to create the new membership crashes with an `IntegrityError` because `unique_together` unconditionally enforces uniqueness across **all rows**, including soft-deleted ones.

#### The Senior Engineer Remediation:
Replace `unique_together` with modern `models.UniqueConstraint` and a partial index condition:

```python
class OrganizationMember(models.Model):
    organization = models.ForeignKey("Organization", on_delete=models.CASCADE)
    email = models.EmailField()
    is_deleted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # PostgreSQL Partial Index: Uniqueness is ONLY enforced for active members!
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(is_deleted=False),
                name="unique_active_org_member",
            )
        ]
```

---

### Summary Checklist: Antipattern Detection Matrix

| # | Antipattern | Primary Failure Mode | Detection Tool / Strategy |
|---|-------------|----------------------|---------------------------|
| 1 | `null=True` on `CharField` | Dual empty state (`NULL` vs `""`) | Linter (`flake8-django` / `ruff`) |
| 2 | `FloatField` for Money | IEEE-754 currency rounding errors | Code review / schema audit |
| 3 | Mutable `default=[]` | Cross-instance memory mutation | Code review |
| 4 | Unindexed `Meta.ordering` | Full table sorts on all queries | `EXPLAIN ANALYZE` / APM alerts |
| 5 | Relying solely on `clean()` | Bypassed by `bulk_create` / `save` | Add `models.CheckConstraint` |
| 6 | Broken Leftmost Prefix | Full table scan despite composite index | `pg_stat_statements` / `EXPLAIN` |
| 7 | `unique_together` + Soft Delete | Re-creation conflicts on deleted rows | Migrate to `UniqueConstraint(condition=...)` |
