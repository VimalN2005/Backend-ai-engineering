# Day 33 Rapid Revision: Django Models, Constraints & Indexing

A high-yield, 5-minute reference guide covering enterprise Django model design, database constraints, indexing strategies, and validation lifecycles.

---

## 1. Field Configuration & Nullability Decision Matrix

| Data Type | Field Definition | Nullable? | Notes / Database Type |
|:----------|:-----------------|:----------|:----------------------|
| **Primary Key** | `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)` | `No` | 16-byte UUID; or `BigAutoField` |
| **Short Text** | `CharField(max_length=120, blank=True, default="")` | **`Never null=True`** | Stores `""` when empty (prevents dual state) |
| **Long Text** | `TextField(blank=True, default="")` | **`Never null=True`** | Handled via PostgreSQL TOAST storage |
| **Foreign Key** | `ForeignKey(Target, on_delete=PROTECT, related_name="...")` | Per domain | Always set explicit `related_name` |
| **Currency** | `DecimalField(max_digits=14, decimal_places=4)` | `No` | Exact NUMERIC; never `FloatField` |
| **JSON/Config** | `JSONField(default=dict, blank=True)` | `No` | Always callable default (`default=dict`) |
| **Flag / Status** | `CharField(max_length=20, choices=Status.choices, db_index=True)` | `No` | Use `models.TextChoices` |
| **Timestamp** | `DateTimeField(auto_now_add=True, db_index=True)` | `No` | UTC TIMESTAMPTZ |

---

## 2. The 5 Non-Negotiable Model Architectural Laws

1. **The String Field Law**: Never set `null=True` on `CharField` or `TextField`. Use `blank=True` with `default=""`.
2. **The Currency Law**: Never use `FloatField` for financial transactions or balances. Always use `DecimalField(max_digits=14, decimal_places=4)`.
3. **The Callable Default Law**: Never pass mutable collections as defaults (`default=[]` or `default={}`). Always pass callables (`default=list`, `default=dict`).
4. **The Database Constraint Law**: Never rely exclusively on Python `clean()` methods for data integrity. Back all invariants with database-level `CheckConstraint` and `UniqueConstraint`.
5. **The Leftmost Prefix Law**: In composite indexes `(A, B, C)`, queries must filter on column `A` to use the index. Order columns by **ESR**: Equality first, Sort second, Range last.

---

## 3. Modern Constraints Reference

```python
from django.db import models
from django.db.models import Q, F

class Meta:
    constraints = [
        # 1. Partial Unique Constraint (Solves soft-delete conflict)
        models.UniqueConstraint(
            fields=["organization", "email"],
            condition=Q(is_deleted=False),
            name="unique_active_org_member",
        ),

        # 2. Check Constraint (Database-enforced domain invariant)
        models.CheckConstraint(
            check=Q(discount_price__lte=F("original_price")),
            name="check_discount_not_greater_than_original",
        ),

        # 3. Positive Balance Check
        models.CheckConstraint(
            check=Q(balance__gte=0),
            name="check_non_negative_balance",
        ),
    ]
```

---

## 4. Index Selection Cheat Sheet

| Index Algorithm | Primary Query Patterns | Ideal Use Case | Trade-Off |
|:----------------|:-----------------------|:---------------|:----------|
| **B-Tree** (Default) | `=`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `ORDER BY` | General columns, IDs, dates, statuses | High write overhead on random UUIDv4 |
| **GIN** | `@>` (contains), `?&`, Full-Text Search | `JSONField`, array tags, search vectors | Heavy write amplification |
| **BRIN** | Range scans on naturally ordered tables | Multi-million row append-only logs | Ineffective for random unordered data |
| **Functional Index**| `Lower('email')` | Case-insensitive unique searches | Recomputed on every write |

---

## 5. Model Validation Pipeline Cheat Sheet

```text
model_instance.full_clean()
│
├── 1. clean_fields()        -> Type conversion & individual field validators
├── 2. clean()               -> Custom multi-field business logic
├── 3. validate_unique()     -> Database check for unique=True & unique_together
└── 4. validate_constraints()-> Evaluates model Meta CheckConstraints & UniqueConstraints
```

> [!NOTE]
> `Model.save()` **does not call** `full_clean()`. If business validation must be enforced on direct programmatic saves, override `save()` to invoke `self.full_clean()` explicitly.
