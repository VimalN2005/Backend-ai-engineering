# Day 33: Django Models, Constraints & Indexing Exercises

Three hands-on engineering challenges testing your mastery of declarative model metaclasses, composite B-Tree index optimization (Leftmost Prefix Rule), and database-level constraint validation.

---

## Exercise 1: Declarative Abstract Base Model Metaclass & Inheritance Engine

### Problem Statement:
Enterprise Django architectures rely on reusable abstract base models to standardize primary keys, audit timestamps, and soft deletion across dozens of domain tables.

Build a pure-Python declarative model inheritance engine that:
1. Implements `ModelBase` metaclass supporting `abstract = True` in `class Meta`.
2. Provides three reusable abstract base models:
   - `UUIDModel`: Generates a UUIDv4 primary key (`id`).
   - `TimeStampedModel`: Provides `created_at` (auto set on creation) and `updated_at` (auto updated on save).
   - `SoftDeleteModel`: Provides `is_deleted`, `deleted_at`, and a `soft_delete()` method.
3. Allows concrete models (e.g. `Product(UUIDModel, TimeStampedModel, SoftDeleteModel)`) to inherit all parent fields in their `_meta.fields` registry without duplicating declarations.

### Constraints:
- Pure Python standard library.
- Abstract models must not be instantiable directly.
- The `soft_delete()` method must record the UTC timestamp and set `is_deleted = True`.

### Complete Solution:

```python
"""
Exercise 1: Declarative Abstract Base Model Metaclass & Inheritance Engine
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Type


class ModelOptions:
    """Stores metadata for a model class, mirroring Django's Model._meta."""
    def __init__(self, model_cls: Type[Any], meta_cls: Optional[Type[Any]]):
        self.model_cls = model_cls
        self.fields: Dict[str, Any] = {}
        self.abstract: bool = getattr(meta_cls, "abstract", False)
        self.db_table: str = getattr(meta_cls, "db_table", model_cls.__name__.lower())


class ModelBase(type):
    """Metaclass that extracts fields, handles inheritance, and configures _meta."""
    def __new__(mcs, name: str, bases: Tuple[Type[Any], ...], attrs: Dict[str, Any]) -> Any:
        meta_cls = attrs.pop("Meta", None)
        
        # Extract fields declared directly on this class
        declared_fields = {}
        for key, val in list(attrs.items()):
            if isinstance(val, str) and val.startswith("FIELD:"):
                declared_fields[key] = val
                del attrs[key]

        # Inherit fields from base classes
        inherited_fields = {}
        for base in bases:
            if hasattr(base, "_meta") and base._meta is not None:
                inherited_fields.update(base._meta.fields)

        cls = super().__new__(mcs, name, bases, attrs)
        cls._meta = ModelOptions(cls, meta_cls)
        cls._meta.fields = {**inherited_fields, **declared_fields}
        return cls


class Model(metaclass=ModelBase):
    class Meta:
        abstract = True

    def __init__(self, **kwargs: Any):
        if self._meta.abstract:
            raise TypeError(f"Cannot instantiate abstract model '{self.__class__.__name__}'.")
        
        # Assign attributes
        for field_name in self._meta.fields:
            setattr(self, field_name, kwargs.get(field_name, None))


# Abstract Base Models
class UUIDModel(Model):
    id = "FIELD:UUIDField"

    class Meta:
        abstract = True

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if self.id is None:
            self.id = uuid.uuid4()


class TimeStampedModel(Model):
    created_at = "FIELD:DateTimeField"
    updated_at = "FIELD:DateTimeField"

    class Meta:
        abstract = True

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        now = datetime.now(timezone.utc)
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now

    def save(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


class SoftDeleteModel(Model):
    is_deleted = "FIELD:BooleanField"
    deleted_at = "FIELD:DateTimeField"

    class Meta:
        abstract = True

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if self.is_deleted is None:
            self.is_deleted = False

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)


# Concrete Enterprise Model
class EnterpriseProduct(UUIDModel, TimeStampedModel, SoftDeleteModel):
    name = "FIELD:CharField"
    sku = "FIELD:CharField"

    class Meta:
        db_table = "catalog_products"


def test_model_inheritance() -> None:
    print("[*] Testing Exercise 1: Model Metaclass & Inheritance Engine...")

    # 1. Abstract Models Cannot Be Instantiated
    try:
        UUIDModel()
        assert False, "Should have prevented instantiation of abstract model"
    except TypeError:
        pass
    print("  [+] Abstract model instantiation blocked.")

    # 2. Inherited Fields Registry Verification
    expected_fields = {"id", "created_at", "updated_at", "is_deleted", "deleted_at", "name", "sku"}
    assert set(EnterpriseProduct._meta.fields.keys()) == expected_fields
    assert EnterpriseProduct._meta.db_table == "catalog_products"
    print("  [+] Multi-tier field inheritance verified.")

    # 3. Instance Lifecycle & Soft Deletion
    product = EnterpriseProduct(name="Enterprise Server", sku="SRV-1001")
    assert isinstance(product.id, uuid.UUID)
    assert product.is_deleted is False
    assert product.created_at is not None

    # Trigger soft delete
    product.soft_delete()
    assert product.is_deleted is True
    assert product.deleted_at is not None
    print("  [+] Model lifecycle and soft delete verified.")


if __name__ == "__main__":
    test_model_inheritance()
```

---

## Exercise 2: Leftmost Prefix Rule Query Optimizer & Index Matcher

### Problem Statement:
When executing queries in a relational database like PostgreSQL, the query optimizer evaluates available composite B-Tree indexes against the query's WHERE clauses.

Implement a query optimizer function `select_best_index(available_indexes, query_filters)` that:
1. Accepts a list of composite indexes: each having a `name` and an ordered tuple of `fields` (e.g. `("tenant_id", "status", "created_at")`).
2. Evaluates incoming query filter keys against the **Leftmost Prefix Rule**:
   - The leading column (first field) MUST be present in `query_filters`.
   - Matching prefix length is determined by counting consecutive columns starting from index 0.
3. Selects the index with the longest matching prefix.
4. If no index satisfies the leftmost column requirement, returns `None` (signaling a Full Table Scan).

### Complete Solution:

```python
"""
Exercise 2: Leftmost Prefix Rule Query Optimizer & Index Matcher
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class IndexDefinition:
    name: str
    fields: Tuple[str, ...]


@dataclass(frozen=True)
class IndexMatchResult:
    index: Optional[IndexDefinition]
    matched_columns: Tuple[str, ...]
    is_full_table_scan: bool


def select_best_index(
    available_indexes: List[IndexDefinition],
    query_filters: Dict[str, Any],
) -> IndexMatchResult:
    """
    Selects the optimal B-Tree index using the Leftmost Prefix Rule.
    Returns the IndexDefinition with the deepest consecutive prefix match.
    """
    filter_keys: Set[str] = set(query_filters.keys())
    best_index: Optional[IndexDefinition] = None
    best_matched_cols: Tuple[str, ...] = ()
    max_prefix_depth = 0

    for idx in available_indexes:
        # Check if the leading column is present
        if not idx.fields or idx.fields[0] not in filter_keys:
            continue  # Leftmost column missing: cannot accelerate this query

        # Count consecutive matching prefix columns
        matched_prefix = []
        for col in idx.fields:
            if col in filter_keys:
                matched_prefix.append(col)
            else:
                break  # Prefix broken! Remaining columns cannot be used for B-Tree lookup

        prefix_depth = len(matched_prefix)
        if prefix_depth > max_prefix_depth:
            max_prefix_depth = prefix_depth
            best_index = idx
            best_matched_cols = tuple(matched_prefix)

    if best_index is None:
        return IndexMatchResult(
            index=None,
            matched_columns=(),
            is_full_table_scan=True,
        )

    return IndexMatchResult(
        index=best_index,
        matched_columns=best_matched_cols,
        is_full_table_scan=False,
    )


def test_index_matcher() -> None:
    print("[*] Testing Exercise 2: Leftmost Prefix Query Optimizer...")

    indexes = [
        IndexDefinition(name="idx_tenant_status_date", fields=("tenant_id", "status", "created_at")),
        IndexDefinition(name="idx_user_email", fields=("email",)),
        IndexDefinition(name="idx_status_priority", fields=("status", "priority")),
    ]

    # Case 1: Query matching full 3-column prefix -> Deepest match
    q1 = {"tenant_id": 5, "status": "ACTIVE", "created_at": "2026-01-01"}
    res1 = select_best_index(indexes, q1)
    assert res1.is_full_table_scan is False
    assert res1.index.name == "idx_tenant_status_date"
    assert res1.matched_columns == ("tenant_id", "status", "created_at")

    # Case 2: Query matching prefix with a gap (tenant_id + created_at, status missing)
    # The B-Tree can ONLY use tenant_id because the prefix breaks at status!
    q2 = {"tenant_id": 5, "created_at": "2026-01-01"}
    res2 = select_best_index(indexes, q2)
    assert res2.is_full_table_scan is False
    assert res2.index.name == "idx_tenant_status_date"
    assert res2.matched_columns == ("tenant_id",)  # Only 1 column used

    # Case 3: Query missing leading column of idx_tenant_status_date, but matching idx_status_priority
    q3 = {"status": "ACTIVE", "priority": 1}
    res3 = select_best_index(indexes, q3)
    assert res3.is_full_table_scan is False
    assert res3.index.name == "idx_status_priority"
    assert res3.matched_columns == ("status", "priority")

    # Case 4: Query with no matching leading columns -> Full Table Scan
    q4 = {"amount": 500, "description": "Payment"}
    res4 = select_best_index(indexes, q4)
    assert res4.is_full_table_scan is True
    assert res4.index is None

    print("  [+] Leftmost prefix optimization and prefix-gap detection verified.")


if __name__ == "__main__":
    test_index_matcher()
```

---

## Exercise 3: Declarative Partial Unique Constraint & Check Constraint Validator

### Problem Statement:
Design an in-memory transactional database schema validator that executes:
1. `PartialUniqueConstraint`: Enforces uniqueness across a tuple of columns, but only for records satisfying an arbitrary filter condition (e.g. `is_active=True`).
2. `CheckConstraint`: Enforces domain invariant formulas (e.g. `discount_price <= original_price`).
3. An `insert()` and `update()` pipeline that validates incoming operations against existing records, raising `IntegrityError` if any constraint fails.

### Complete Solution:

```python
"""
Exercise 3: Declarative Partial Unique Constraint & Check Constraint Validator
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


class IntegrityError(Exception):
    pass


@dataclass
class PartialUniqueConstraint:
    name: str
    fields: Tuple[str, ...]
    condition: Callable[[Dict[str, Any]], bool]

    def validate(self, existing_rows: List[Dict[str, Any]], new_row: Dict[str, Any]) -> None:
        if not self.condition(new_row):
            return  # Exempt from uniqueness

        new_key = tuple(new_row.get(f) for f in self.fields)
        if None in new_key:
            return  # SQL NULLs are distinct

        for row in existing_rows:
            if row.get("id") == new_row.get("id"):
                continue
            if not self.condition(row):
                continue
            
            existing_key = tuple(row.get(f) for f in self.fields)
            if new_key == existing_key:
                raise IntegrityError(
                    f"UNIQUE constraint violation '{self.name}': "
                    f"Duplicate entry for {self.fields} with values {new_key}."
                )


@dataclass
class CheckConstraint:
    name: str
    predicate: Callable[[Dict[str, Any]], bool]
    error_message: str

    def validate(self, new_row: Dict[str, Any]) -> None:
        if not self.predicate(new_row):
            raise IntegrityError(
                f"CHECK constraint violation '{self.name}': {self.error_message}"
            )


class SchemaTable:
    def __init__(
        self,
        name: str,
        unique_constraints: List[PartialUniqueConstraint],
        check_constraints: List[CheckConstraint],
    ):
        self.name = name
        self.unique_constraints = unique_constraints
        self.check_constraints = check_constraints
        self.rows: Dict[int, Dict[str, Any]] = {}

    def insert(self, row: Dict[str, Any]) -> Dict[str, Any]:
        existing = list(self.rows.values())
        
        # 1. Run CHECK constraints
        for check in self.check_constraints:
            check.validate(row)

        # 2. Run UNIQUE constraints
        for uq in self.unique_constraints:
            uq.validate(existing, row)

        self.rows[row["id"]] = row
        return row


def test_constraints() -> None:
    print("[*] Testing Exercise 3: Partial Unique & Check Constraints...")

    table = SchemaTable(
        name="store_discounts",
        unique_constraints=[
            # Only ACTIVE coupon codes for a given store must be unique!
            PartialUniqueConstraint(
                name="uq_active_store_coupon",
                fields=("store_id", "code"),
                condition=lambda r: r.get("is_active", True) is True,
            )
        ],
        check_constraints=[
            # Discount price cannot exceed original retail price
            CheckConstraint(
                name="check_discount_le_original",
                predicate=lambda r: r.get("discount_price", 0) <= r.get("original_price", 0),
                error_message="Discount price must be less than or equal to original price.",
            )
        ],
    )

    # 1. Valid record insertion
    c1 = {"id": 1, "store_id": 10, "code": "SUMMER20", "original_price": 100, "discount_price": 80, "is_active": True}
    table.insert(c1)
    print("  [+] Valid record inserted.")

    # 2. CHECK constraint violation (discount > original)
    bad_price = {"id": 2, "store_id": 10, "code": "WINTER50", "original_price": 50, "discount_price": 70, "is_active": True}
    try:
        table.insert(bad_price)
        assert False, "Should have failed CHECK constraint"
    except IntegrityError as exc:
        assert "check_discount_le_original" in str(exc)
    print("  [+] CHECK constraint successfully blocked invalid price.")

    # 3. Duplicate active coupon in same store -> IntegrityError
    dup_active = {"id": 3, "store_id": 10, "code": "SUMMER20", "original_price": 100, "discount_price": 80, "is_active": True}
    try:
        table.insert(dup_active)
        assert False, "Should have failed UNIQUE constraint for active coupon"
    except IntegrityError as exc:
        assert "uq_active_store_coupon" in str(exc)
    print("  [+] Partial UNIQUE constraint blocked duplicate active coupon.")

    # 4. Same coupon code but is_active=False -> Allowed!
    inactive_coupon = {"id": 4, "store_id": 10, "code": "SUMMER20", "original_price": 100, "discount_price": 80, "is_active": False}
    table.insert(inactive_coupon)
    print("  [+] Partial UNIQUE constraint allowed inactive duplicate.")


if __name__ == "__main__":
    test_constraints()
```
