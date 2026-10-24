# Day 33: Django Models, Field Constraints, Meta Options & Indexing

The data model is the single most critical structural foundation of any backend system. In Django, the **Object-Relational Mapping (ORM)** layer serves as the single source of truth: defining database table schemas, column types, relationship integrity, foreign key cascades, data validation rules, and indexing strategies directly in Python.

A poorly designed model schema leads to database lockups, data corruption, migration deadlocks, and catastrophic query performance at scale. Conversely, an enterprise-grade model schema leverages database-native constraints, composite indexes, predictable primary keys, and strict field options to ensure ACID data integrity and sub-millisecond query performance.

---

## 1. The Django ORM & Active Record Architecture

Django models follow the **Active Record design pattern** (Martin Fowler): each model class represents a database table, an instance of the class represents a single row, and class methods provide data access, query building, and domain logic.

```
+-----------------------------------------------------------------------------------+
|                        DJANGO ACTIVE RECORD ARCHITECTURE                          |
+-----------------------------------------------------------------------------------+
| Python Layer       | Django ORM Translation          | PostgreSQL / MySQL Engine  |
+--------------------+---------------------------------+----------------------------+
| class User(Model): | ModelBase metaclass             | CREATE TABLE users_user (  |
|   id = UUIDField() | -> Field definitions            |   id UUID PRIMARY KEY,     |
|   email = Email()  | -> Constraints & Meta options   |   email VARCHAR(255) UNIQUE|
|   balance = Dec()  | -> SQL Compiler & Query Builder |   balance NUMERIC(12, 2)   |
|                    |                                 | );                         |
| user.save()        | Inspects state -> Generates SQL | INSERT INTO / UPDATE ...   |
| user.delete()      | Handles cascades -> Generates   | DELETE FROM ...            |
+--------------------+---------------------------------+----------------------------+
```

### Active Record vs Data Mapper:
- **Active Record (Django, Rails)**: Model instances combine database persistence logic with domain logic. Highly productive, concise, and deeply integrated with forms, admin, and serializers.
- **Data Mapper (SQLAlchemy, Hibernate)**: Completely separates domain objects from database persistence engines. Better for highly complex enterprise domain-driven design (DDD), but requires more boilerplate.

---

## 2. Core Field Types & PostgreSQL Engine Alignment

Choosing the correct field type impacts storage efficiency, query indexing speed, and cross-database portability.

| Django Field Type | PostgreSQL Engine Type | Bytes / Storage | Primary Use Case & Considerations |
|:------------------|:-----------------------|:----------------|:-----------------------------------|
| `CharField(max_length=N)` | `VARCHAR(N)` | 1–4 bytes + data | Short fixed or bounded strings (names, codes, slugs). `max_length` enforced at DB and form level. |
| `TextField` | `TEXT` | Variable (TOAST) | Unbounded text (articles, JSON logs, markdown). Handled efficiently in Postgres via TOAST. |
| `IntegerField` | `INTEGER` | 4 bytes | 32-bit signed integer ($-2,147,483,648$ to $+2,147,483,647$). |
| `BigIntegerField` | `BIGINT` | 8 bytes | 64-bit integer (high-cardinality IDs, counters, timestamps). |
| `DecimalField(max_digits, decimal_places)` | `NUMERIC(M, D)` | Variable (exact) | **Currency & financial balances**. Never use `FloatField` for money! |
| `DateTimeField(auto_now_add, auto_now)` | `TIMESTAMPTZ` | 8 bytes | Timestamps with timezone awareness (`USE_TZ=True`). |
| `BooleanField` | `BOOLEAN` | 1 byte | True / False flags. (Django 3.1+ deprecated `NullBooleanField`; use `BooleanField(null=True)`). |
| `UUIDField` | `UUID` | 16 bytes | Cryptographically random or time-ordered GUIDs. |
| `JSONField` | `JSONB` (Postgres) | Binary JSON | Semi-structured data, feature flags, configuration payloads. Indexed via GIN. |

---

## 3. The `null=True` vs `blank=True` Law

One of the most frequent junior antipatterns in Django is misunderstanding the separation of concerns between `null` and `blank`.

```
                  +----------------------------------------------+
                  |            DJANGO FIELD VALIDATION           |
                  +----------------------------------------------+
                                         |
                   +---------------------+---------------------+
                   |                                           |
                   v                                           v
       +-----------------------+                   +-----------------------+
       |       null=True       |                   |      blank=True       |
       |  (DATABASE CONSTRAINT)|                   | (VALIDATION / UI RULE)|
       +-----------------------+                   +-----------------------+
       | Determines whether the|                   | Determines whether form|
       | column allows SQL NULL|                   | inputs, DRF serializers|
       | values in PostgreSQL. |                   | or full_clean() allow |
       | Checked by database.  |                   | empty string / blank. |
       +-----------------------+                   +-----------------------+
```

### The String Field Law:
> **Never set `null=True` on string-based fields (`CharField`, `TextField`, `SlugField`, `EmailField`)!**

#### Why? The "Dual Empty State" Disaster:
If you set `null=True, blank=True` on a `CharField`:
1. Row A stores `""` (empty string).
2. Row B stores `NULL`.

Both signify "no data", but in SQL:
- `WHERE bio IS NULL` will NOT return Row A.
- `WHERE bio = ''` will NOT return Row B.
- `COUNT(bio)` counts empty strings, but ignores `NULL`.
- Unique constraints fail: In SQL standards, two rows with `NULL` do not violate uniqueness, but two empty strings `""` do!

**The Golden Standard**:
- **String fields**: `blank=True` (stores `""` for empty, never `NULL`).
- **Non-string fields (Integer, DateTime, ForeignKey, Boolean)**: `null=True, blank=True` (stores `NULL` when empty).

---

## 4. Primary Key Architectures: AutoField vs UUIDv4 vs UUIDv7

```
+-----------------------------------------------------------------------------------------+
|                                PRIMARY KEY COMPARISON                                   |
+-----------------------------------------------------------------------------------------+
| Feature               | BigAutoField (64-bit Int) | UUIDv4 (Random) | UUIDv7 (Time-Ordered)   |
+-----------------------+---------------------------+-----------------+-------------------------+
| Storage Size          | 8 bytes                   | 16 bytes        | 16 bytes                |
| Sequential Locality   | Perfect (Monotonic)       | None (Random)   | High (Unix millisecond) |
| B-Tree Index Bloat    | Minimal                   | Severe (~30-50%)| Minimal                 |
| Enumeration Attack    | Vulnerable (/api/user/1)  | Immune          | Immune                  |
| Offline Generation    | Impossible (Needs DB Seq) | Instant         | Instant                 |
+-----------------------+---------------------------+-----------------+-------------------------+
```

### 1. BigAutoField (Default)
- **Pros**: Fastest indexing, compact 8-byte storage, cache friendly.
- **Cons**: Exposes business volume via IDOR attacks (e.g. competitor registers today, sees `id=1050`, registers tomorrow, sees `id=1200`, calculates 150 daily signups).

### 2. UUIDv4 (`uuid.uuid4`)
- **Pros**: 128 bits of cryptographically unguessable randomness. Can be generated offline by clients before sending to DB.
- **Cons**: Completely non-sequential. Inserting into a PostgreSQL B-Tree index causes massive **page splits**, cache misses, and index fragmentation as rows are written to random leaf pages.

### 3. UUIDv7 (Modern Enterprise Standard - RFC 9562)
- Combines a 48-bit Unix millisecond timestamp with 74 bits of pseudo-random data.
- Monotonically increasing: Maintains B-Tree index sequential locality while retaining the security and offline generation benefits of UUIDs.

---

## 5. Modern Model Constraints (`models.Constraint`)

Prior to Django 2.2, developers relied on `unique_together` inside `class Meta`. Django has deprecated `unique_together` in favor of declarative, database-native `UniqueConstraint` and `CheckConstraint`.

### 1. `UniqueConstraint` with Conditions (Partial Indexes)
Allows enforcing uniqueness only on active records, solving the classic soft-delete conflict:

```python
from django.db import models
from django.db.models import Q

class TeamMember(models.Model):
    team = models.ForeignKey("Team", on_delete=models.CASCADE)
    email = models.EmailField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            # User can be deleted (is_active=False) and re-invited with the same email,
            # but cannot have two active memberships in the same team!
            models.UniqueConstraint(
                fields=["team", "email"],
                condition=Q(is_active=True),
                name="unique_active_team_member_email",
            )
        ]
```

### 2. `CheckConstraint` (Database-Enforced Invariants)
Pushes business-critical validation into the database engine itself, guaranteeing data integrity even if queries bypass Django via raw SQL or external ETL scripts.

```python
class Account(models.Model):
    balance = models.DecimalField(max_digits=12, decimal_places=2)
    minimum_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    class Meta:
        constraints = [
            # Database engine rejects any UPDATE or INSERT that drives balance below minimum_limit
            models.CheckConstraint(
                check=Q(balance__gte=models.F("minimum_limit")),
                name="check_balance_above_minimum_limit",
            )
        ]
```

---

## 6. Database Indexing Strategies & The Leftmost Prefix Rule

An index is an auxiliary data structure (typically a balanced B-Tree) that allows the database engine to find specific rows in $\mathcal{O}(\log N)$ time instead of scanning every table page ($\mathcal{O}(N)$ sequential table scan).

```
                      +-----------------------------+
                      |     B-Tree Root Node        |
                      +--------------+--------------+
                                     |
                     +---------------+---------------+
                     |                               |
                     v                               v
         +-----------------------+       +-----------------------+
         | Intermediate Page     |       | Intermediate Page     |
         +-----------+-----------+       +-----------+-----------+
                     |                               |
         +-----------+-----------+       +-----------+-----------+
         |                       |       |                       |
         v                       v       v                       v
    +---------+             +---------+ +---------+             +---------+
    | Leaf [A]|<----------->| Leaf [B]| | Leaf [C]|<----------->| Leaf [D]|
    +---------+             +---------+ +---------+             +---------+
```

### Composite Indexes & The Leftmost Prefix Rule
When defining a composite index across multiple columns:
```python
models.Index(fields=["tenant_id", "status", "created_at"], name="idx_tenant_status_created")
```

The database index can only be leveraged if query filters use the **leftmost columns in order**:
- ✅ `WHERE tenant_id = 5` (Uses index)
- ✅ `WHERE tenant_id = 5 AND status = 'ACTIVE'` (Uses index)
- ✅ `WHERE tenant_id = 5 AND status = 'ACTIVE' AND created_at >= '2026-01-01'` (Uses index)
- ❌ `WHERE status = 'ACTIVE'` (Cannot use index; causes full table scan!)
- ❌ `WHERE created_at >= '2026-01-01'` (Cannot use index)

**Rule**: Place high-cardinality equality columns first (`tenant_id`), low-cardinality filter columns second (`status`), and range/ordering columns last (`created_at`).

---

## 7. Specialized Index Types

Django provides native access to advanced PostgreSQL indexing algorithms:

```python
from django.contrib.postgres.indexes import GinIndex, BTreeIndex, HashIndex, BrinIndex
from django.db.models.functions import Lower

class Article(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    tags = models.JSONField(default=list)
    published_at = models.DateTimeField()

    class Meta:
        indexes = [
            # 1. GIN (Generalized Inverted Index): Supercharged JSONB and array containment queries (?&, @>)
            GinIndex(fields=["tags"], name="article_tags_gin_idx"),

            # 2. Functional Index: Indexes expression output, accelerating case-insensitive searches
            models.Index(Lower("title"), name="article_lower_title_idx"),

            # 3. BRIN (Block Range Index): Extremely small index for multi-million row append-only timeseries
            BrinIndex(fields=["published_at"], name="article_published_brin_idx"),
        ]
```

### When to Use Which Index:
- **B-Tree (Default)**: Equality (`=`), range comparisons (`<`, `<=`, `>`, `>=`), and `ORDER BY`.
- **GIN**: Full-text search and `JSONField` lookups.
- **Hash**: Exact equality lookups only (`=`); smaller than B-tree for large strings, but cannot do range scans or sorting.
- **BRIN**: Huge tables (millions of rows) physically ordered by date or monotonic ID. Consumes $<1\%$ of the RAM of a B-tree index.

---

## 8. Model `Meta` Class Mastery

The inner `class Meta` configures table-level attributes and behaviors:

```python
class Order(models.Model):
    class Meta:
        # 1. Physical database table name (keeps schema clean and prevents django app prefixing)
        db_table = "orders"

        # 2. Default QuerySet ordering (CAUTION: Can destroy performance if applied carelessly)
        ordering = ["-created_at"]

        # 3. Plural human-readable names for Django Admin
        verbose_name = "Customer Order"
        verbose_name_plural = "Customer Orders"

        # 4. Declarative composite indexes
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

        # 5. Declarative table constraints
        constraints = [
            models.CheckConstraint(check=models.Q(total_amount__gte=0), name="positive_order_total")
        ]
```

> [!WARNING]
> **The `ordering` Antipattern**:
> Defining `ordering = ['-created_at']` in `Meta` forces Django to append `ORDER BY created_at DESC` to **EVERY** query executed against this model—including subqueries, `count()` aggregations, and chunked iterations! If `created_at` is unindexed, aggregations will trigger full-table sorts. Prefer explicit `.order_by()` in queries or ensure the ordering field is indexed.

---

## 9. Enumerations: `models.TextChoices` & `models.IntegerChoices`

Django 3.0+ introduced declarative, type-safe choices classes:

```python
from django.db import models

class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Payment"
        PROCESSING = "PROCESSING", "Payment Processing"
        COMPLETED = "COMPLETED", "Payment Completed"
        FAILED = "FAILED", "Payment Failed"
        REFUNDED = "REFUNDED", "Payment Refunded"

    class Priority(models.IntegerChoices):
        LOW = 1, "Low Priority"
        MEDIUM = 2, "Medium Priority"
        HIGH = 3, "High Priority"

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    priority = models.PositiveSmallIntegerField(
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
```

### TextChoices vs IntegerChoices Tradeoff:
- **`TextChoices` (Recommended for readable domain code)**: Stores human-readable strings (`"COMPLETED"`). When debugging directly in PostgreSQL via `psql` or Metabase, values are instantly understandable without joining lookup tables.
- **`IntegerChoices` (Recommended for hyper-scale tables)**: Stores a 2-byte integer (`1`, `2`). Consumes slightly less index memory on 100M+ row tables.

---

## 10. Model Lifecycle & The Validation Pipeline

A critical architectural pitfall in Django: **`Model.save()` DOES NOT call `Model.full_clean()`!**

```
+-----------------------------------------------------------------------------+
|                          DJANGO VALIDATION LIFECYCLE                        |
+-----------------------------------------------------------------------------+
| 1. Form / DRF Serializer Layer                                              |
|    -> Calls model_instance.full_clean()                                     |
|                                                                             |
| 2. Inside full_clean():                                                     |
|    a. clean_fields()        -> Type conversion & field validators           |
|    b. clean()               -> Custom multi-field model validation logic    |
|    c. validate_unique()     -> Python-level uniqueness checks               |
|    d. validate_constraints()-> Evaluates model Meta constraints             |
|                                                                             |
| 3. Direct Model Instantiation:                                              |
|    Order.objects.create(total=-100) -> BYPASSES full_clean() completely!    |
|    -> Triggers Database-Level Constraints (CheckConstraint / NOT NULL)      |
+-----------------------------------------------------------------------------+
```

### Implementing `clean()` Defensively:
To enforce business invariants when models are processed by Django Admin, Forms, or DRF:

```python
from django.core.exceptions import ValidationError

class Subscription(models.Model):
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    def clean(self):
        super().clean()
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                "end_date": "End date cannot be prior to start date."
            })

    def save(self, *args, **kwargs):
        # Explicitly enforce full_clean() on direct programmatic saves
        self.full_clean()
        super().save(*args, **kwargs)
```

---

## 11. Abstract Base Models Architecture

In enterprise codebases, models share auditing, identification, and lifecycle patterns. Abstract base models (`abstract = True`) define columns and methods that child tables inherit without creating a physical parent table in the database.

```python
import uuid
from django.db import models

class TimeStampedModel(models.Model):
    """Universal auditing timestamps."""
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Cryptographic UUID primary key."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Soft deletion lifecycle."""
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def soft_delete(self):
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])


class EnterpriseBaseModel(UUIDModel, TimeStampedModel, SoftDeleteModel):
    """Master production base model."""
    class Meta:
        abstract = True
```

---

## 12. Database Defaults vs Python Defaults

Django supports two distinct default paradigms:

```python
from django.db import models
from django.db.models.functions import Now

class AuditEvent(models.Model):
    # 1. Python-Level Default (Evaluated inside Python process before SQL generation)
    token = models.CharField(max_length=64, default=generate_token)

    # 2. Database-Level Default (Evaluated directly by PostgreSQL engine: DEFAULT NOW())
    # Available in Django 4.2+ / 5.0+ via db_default
    db_created_at = models.DateTimeField(db_default=Now())
```

### The Mutable Default Bug:
```python
# CRITICAL BUG: All model instances share the exact same list in memory!
tags = models.JSONField(default=[])

# CORRECT: Pass callable to produce a fresh list for every new instance
tags = models.JSONField(default=list)
```

---

## 13. Currency & Financial Math Modeling

Computers represent floating-point numbers (`float`) in base-2 binary IEEE-754 format. Certain decimal fractions like $0.1$ or $0.2$ cannot be represented precisely in binary:

$$\text{In Python: } 0.1 + 0.2 = 0.30000000000000004$$

If you use `models.FloatField()` for currency, rounding errors compound across millions of ledger transactions, causing balance discrepancies.

### The Financial Invariant:
Always use `DecimalField`:
```python
# Exact fixed-point numeric calculation backed by NUMERIC(12, 4) in PostgreSQL
amount = models.DecimalField(max_digits=12, decimal_places=4)
```

---

## 14. Database-Level vs Application-Level Validation

| Feature | Application-Level Validation (`clean()`, DRF) | Database-Level Constraints (`CheckConstraint`, `UniqueConstraint`) |
|:--------|:----------------------------------------------|:-------------------------------------------------------------------|
| **Execution Point** | Python worker memory before sending query | PostgreSQL / MySQL engine during write |
| **Race Condition Immunity** | ❌ Vulnerable (concurrent requests can pass check) | ✅ Completely ACID atomic & immune |
| **Bypassable?** | ⚠️ Yes (via `bulk_create()`, `update()`, raw SQL) | 🛡️ Impossible to bypass |
| **User Experience** | Rich error messages returned to frontend | Returns database `IntegrityError` if unhandled |

**Production Strategy (Defense in Depth)**: Use both. Validate user inputs in serializers/clean methods to provide human-friendly errors, and back them with database constraints to guarantee physical schema consistency.

---

## 15. 7 Golden Invariant Rules & Production Schema Reference Matrix

### 7 Golden Invariant Rules:
1. **Never use `null=True` on string fields**: Use `blank=True` and store `""` as empty.
2. **Never use `FloatField` for currency**: Use `DecimalField(max_digits=14, decimal_places=4)`.
3. **Never pass mutable defaults**: Use callables (`default=list`, `default=dict`, `default=uuid.uuid4`).
4. **Always declare `related_name` explicitly on foreign keys**: Avoid automatic `_set` pollution.
5. **Enforce business invariants in SQL `CheckConstraint`**: Never rely solely on Python validation.
6. **Order composite index columns by selectivity**: High-cardinality equality fields first, ranges last.
7. **Never define unindexed `ordering` in model `Meta`**: Prevents accidental full-table sort penalties on aggregations.

### Production Schema Field Configuration Matrix:

| Data Type | Field Definition | Nullable? | Index Type |
|:----------|:-----------------|:----------|:-----------|
| **Identifier** | `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)` | `No` | B-Tree (PK) |
| **Email** | `EmailField(unique=True, max_length=255)` | `No` | B-Tree (Unique) |
| **Short Text** | `CharField(max_length=120, blank=True)` | `No` | Optional |
| **Long Text** | `TextField(blank=True)` | `No` | Optional (GIN for FTS) |
| **Foreign Key**| `ForeignKey(Target, on_delete=PROTECT, related_name="...")` | Per domain | B-Tree (Default) |
| **Currency** | `DecimalField(max_digits=12, decimal_places=2)` | `No` | Optional |
| **JSON/Config**| `JSONField(default=dict, blank=True)` | `No` | GIN Index |
| **Timestamp** | `DateTimeField(auto_now_add=True, db_index=True)` | `No` | B-Tree or BRIN |
