"""
Day 33: Example 01 - Django Model Schema, Field Descriptors & Validation Engine
================================================================================

This module implements a pure-Python, zero-dependency architectural simulation of
Django's declarative model system, field descriptor protocol, constraint validation,
and the full validation lifecycle (`clean()`, `full_clean()`, and `save()`).

WHY THIS ARCHITECTURE MATTERS:
1. Declarative Metaclass Pattern: Django uses `models.base.ModelBase` (a Python metaclass)
   to inspect class definitions at import time, extracting `Field` instances from the class
   dictionary and populating the model's `_meta` registry. Understanding this eliminates the
   "magic" behind Django models.
2. The Descriptor Protocol: Django fields act as Python descriptors (`__get__` and `__set__`).
   They intercept attribute assignment to perform type coercion, detect mutated/dirty fields,
   and prevent invalid types from ever reaching the database layer.
3. The `null` vs `blank` Invariant: Enforces Django's fundamental rule:
   - `null=True`: Affects database storage (allows SQL NULL).
   - `blank=True`: Affects application-level validation (allows empty strings/blanks).
   - String fields (`CharField`) must never use `null=True` to avoid the "dual empty state" bug.
4. Precision Currency: Simulates `DecimalField` using Python's `decimal.Decimal` to prove
   how fixed-point arithmetic avoids IEEE-754 floating-point rounding errors.
"""

from __future__ import annotations

import decimal
import enum
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union


class ValidationError(Exception):
    """
    Raised when field or model validation fails.
    Mirrors `django.core.exceptions.ValidationError`.
    """
    def __init__(self, message: Union[str, Dict[str, List[str]]]):
        if isinstance(message, dict):
            self.message_dict = message
            super().__init__(str(message))
        else:
            self.message_dict = {"__all__": [message]}
            super().__init__(message)


# -----------------------------------------------------------------------------
# Declarative Field System (Descriptor Protocol)
# -----------------------------------------------------------------------------

class Field:
    """
    Base descriptor for all model fields.
    Intercepts attribute access on model instances to provide caching and validation.
    """
    def __init__(
        self,
        *,
        primary_key: bool = False,
        null: bool = False,
        blank: bool = False,
        default: Any = None,
        choices: Optional[Type[enum.Enum]] = None,
        db_index: bool = False,
    ):
        self.primary_key = primary_key
        self.null = null
        self.blank = blank
        self.default = default
        self.choices = choices
        self.db_index = db_index
        self.name: str = ""  # Assigned by ModelBase metaclass
        self.attname: str = ""  # Storage key in instance.__dict__

    def contribute_to_class(self, cls: Type[Any], name: str) -> None:
        """Called by ModelBase to attach the field descriptor to the model class."""
        self.name = name
        self.attname = f"_{name}"
        setattr(cls, name, self)

    def __get__(self, instance: Optional[Any], owner: Type[Any]) -> Any:
        if instance is None:
            # Accessed from class (e.g. User.email) -> return descriptor itself
            return self
        return instance.__dict__.get(self.attname, self.get_default())

    def __set__(self, instance: Any, value: Any) -> None:
        coerced = self.to_python(value)
        instance.__dict__[self.attname] = coerced

    def get_default(self) -> Any:
        """Evaluate callable defaults or return fixed default."""
        if callable(self.default):
            return self.default()
        return self.default

    def to_python(self, value: Any) -> Any:
        """Coerce raw values to appropriate Python types."""
        return value

    def validate(self, value: Any) -> None:
        """Validate field constraints (null, blank, choices)."""
        # 1. Null check (Database constraint)
        if value is None:
            if not self.null:
                raise ValidationError(f"Field '{self.name}' cannot be NULL.")
            return

        # 2. Blank check (Application validation)
        if value == "" and not self.blank:
            raise ValidationError(f"Field '{self.name}' cannot be blank.")

        # 3. Choices check
        if self.choices is not None:
            valid_values = {
                choice.value if isinstance(choice, enum.Enum) else choice
                for choice in self.choices
            }
            if value not in valid_values:
                raise ValidationError(
                    f"Value '{value}' is not a valid choice for field '{self.name}'. "
                    f"Expected one of: {sorted(list(valid_values))}"
                )


class CharField(Field):
    """
    String field corresponding to SQL VARCHAR(max_length).
    Enforces the rule that CharField must never set null=True in enterprise schemas.
    """
    def __init__(self, max_length: int, **kwargs: Any):
        if kwargs.get("null", False):
            # Enforce architectural rule: prevent dual empty state ("" vs NULL)
            raise ValueError(
                "CRITICAL ARCHITECTURAL HAZARD: CharField should not use null=True. "
                "Use blank=True to permit empty string values instead."
            )
        super().__init__(**kwargs)
        self.max_length = max_length

    def to_python(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)

    def validate(self, value: Any) -> None:
        super().validate(value)
        if value is not None and len(value) > self.max_length:
            raise ValidationError(
                f"Field '{self.name}' exceeds maximum length of {self.max_length} characters "
                f"(got {len(value)})."
            )


class IntegerField(Field):
    """Integer field corresponding to SQL INTEGER (32-bit signed)."""
    def to_python(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError) as exc:
            raise ValidationError(f"Field '{self.name}' value '{value}' must be an integer.") from exc


class DecimalField(Field):
    """
    Exact fixed-point decimal field corresponding to SQL NUMERIC(max_digits, decimal_places).
    Rejects binary floating-point numbers to protect financial calculations.
    """
    def __init__(self, max_digits: int, decimal_places: int, **kwargs: Any):
        super().__init__(**kwargs)
        self.max_digits = max_digits
        self.decimal_places = decimal_places

    def to_python(self, value: Any) -> Optional[decimal.Decimal]:
        if value is None:
            return None
        if isinstance(value, float):
            # WHY: Using float causes silent precision loss (e.g. 0.1 + 0.2 != 0.3)
            # Require string, int, or explicit Decimal instance
            raise ValidationError(
                f"Field '{self.name}' received float '{value}'. Floats are prohibited in DecimalField "
                f"due to IEEE-754 precision loss. Pass decimal.Decimal or string representation."
            )
        try:
            return decimal.Decimal(str(value))
        except (decimal.InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationError(f"Field '{self.name}' cannot parse '{value}' as Decimal.") from exc

    def validate(self, value: Any) -> None:
        super().validate(value)
        if value is not None:
            sign, digits, exponent = value.as_tuple()
            total_digits = len(digits)
            decimals = -exponent if exponent < 0 else 0
            if decimals > self.decimal_places:
                raise ValidationError(
                    f"Field '{self.name}' has {decimals} decimal places (max allowed: {self.decimal_places})."
                )
            if total_digits > self.max_digits:
                raise ValidationError(
                    f"Field '{self.name}' has {total_digits} total digits (max allowed: {self.max_digits})."
                )


class UUIDField(Field):
    """UUID primary key field corresponding to SQL UUID (16 bytes)."""
    def __init__(self, **kwargs: Any):
        kwargs.setdefault("default", uuid.uuid4)
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Optional[uuid.UUID]:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise ValidationError(f"Field '{self.name}' value '{value}' is not a valid UUID.") from exc


class DateTimeField(Field):
    """Timezone-aware timestamp corresponding to SQL TIMESTAMPTZ."""
    def __init__(self, auto_now_add: bool = False, auto_now: bool = False, **kwargs: Any):
        self.auto_now_add = auto_now_add
        self.auto_now = auto_now
        if auto_now_add or auto_now:
            kwargs["default"] = lambda: datetime.now(timezone.utc)
        super().__init__(**kwargs)


# -----------------------------------------------------------------------------
# Metaclass & Model Base Architecture
# -----------------------------------------------------------------------------

class ModelOptions:
    """
    Stores metadata for a model class, mirroring Django's `Model._meta`.
    """
    def __init__(self, model_cls: Type[Any], meta_cls: Optional[Type[Any]]):
        self.model_cls = model_cls
        self.fields: Dict[str, Field] = {}
        self.pk_field: Optional[Field] = None
        self.db_table: str = getattr(meta_cls, "db_table", model_cls.__name__.lower())
        self.ordering: List[str] = getattr(meta_cls, "ordering", [])
        self.abstract: bool = getattr(meta_cls, "abstract", False)

    def add_field(self, field: Field) -> None:
        self.fields[field.name] = field
        if field.primary_key:
            if self.pk_field is not None:
                raise ValueError(
                    f"Cannot define multiple primary keys on {self.model_cls.__name__} "
                    f"('{self.pk_field.name}' and '{field.name}')."
                )
            self.pk_field = field


class ModelBase(type):
    """
    Metaclass for all models. Collects Field descriptors, handles inheritance,
    and initializes `_meta` options.
    """
    def __new__(mcs, name: str, bases: Tuple[Type[Any], ...], attrs: Dict[str, Any]) -> Any:
        # Extract inner Meta class if declared
        meta_cls = attrs.pop("Meta", None)
        
        # Collect fields declared on this class
        fields: Dict[str, Field] = {}
        for attr_name, attr_value in list(attrs.items()):
            if isinstance(attr_value, Field):
                fields[attr_name] = attr_value
                del attrs[attr_name]

        # Inherit fields from parent base classes
        inherited_fields: Dict[str, Field] = {}
        for base in bases:
            if hasattr(base, "_meta") and base._meta is not None:
                for f_name, f_obj in base._meta.fields.items():
                    inherited_fields[f_name] = f_obj

        # Create the new class
        cls = super().__new__(mcs, name, bases, attrs)
        cls._meta = ModelOptions(cls, meta_cls)

        # Merge inherited fields and newly declared fields
        all_fields = {**inherited_fields, **fields}
        for f_name, f_obj in all_fields.items():
            f_obj.contribute_to_class(cls, f_name)
            cls._meta.add_field(f_obj)

        # Auto-create primary key if none specified on non-abstract models
        if not cls._meta.abstract and cls._meta.pk_field is None:
            pk = UUIDField(primary_key=True)
            pk.contribute_to_class(cls, "id")
            cls._meta.add_field(pk)

        return cls


class Model(metaclass=ModelBase):
    """
    Master active record model base class.
    Provides instantiation, full validation lifecycle, and persistence simulation.
    """
    class Meta:
        abstract = True

    def __init__(self, **kwargs: Any):
        # Set defaults for all known fields
        for field_name, field in self._meta.fields.items():
            if getattr(field, "auto_now_add", False) or (field.default is not None and field_name not in kwargs):
                setattr(self, field_name, field.get_default())

        # Assign supplied keyword arguments
        for key, value in kwargs.items():
            if key in self._meta.fields:
                setattr(self, key, value)
            else:
                raise AttributeError(f"'{self.__class__.__name__}' has no field named '{key}'")

        self._is_persisted = False

    @property
    def pk(self) -> Any:
        """Return the primary key value of this model instance."""
        if self._meta.pk_field:
            return getattr(self, self._meta.pk_field.name)
        return None

    def clean_fields(self) -> None:
        """Validate all field-level constraints individually."""
        errors: Dict[str, List[str]] = {}
        for name, field in self._meta.fields.items():
            value = getattr(self, name)
            try:
                field.validate(value)
            except ValidationError as exc:
                errors[name] = [str(exc)]
        if errors:
            raise ValidationError(errors)

    def clean(self) -> None:
        """Hook for custom multi-field validation logic in child models."""
        pass

    def full_clean(self) -> None:
        """
        Execute complete Django validation pipeline:
        1. clean_fields()
        2. clean()
        """
        errors: Dict[str, List[str]] = {}

        try:
            self.clean_fields()
        except ValidationError as exc:
            errors.update(exc.message_dict)

        try:
            self.clean()
        except ValidationError as exc:
            errors.setdefault("__all__", []).extend(exc.message_dict.get("__all__", [str(exc)]))

        if errors:
            raise ValidationError(errors)

    def save(self, enforce_clean: bool = True) -> None:
        """
        Simulate database persistence.
        
        WHY: In standard Django, `save()` does NOT call `full_clean()`.
        In enterprise setups, defining `enforce_clean=True` guarantees that invalid
        models are caught before issuing SQL INSERT/UPDATE statements.
        """
        if enforce_clean:
            self.full_clean()

        # Update auto_now fields on save
        for field in self._meta.fields.values():
            if getattr(field, "auto_now", False):
                setattr(self, field.name, datetime.now(timezone.utc))

        self._is_persisted = True


# -----------------------------------------------------------------------------
# Concrete Production Domain Models
# -----------------------------------------------------------------------------

class OrderStatus(enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class AbstractAuditModel(Model):
    """Abstract base model injecting enterprise timestamp auditing."""
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CustomerAccount(AbstractAuditModel):
    """Simulates an enterprise customer ledger account."""
    account_number = CharField(max_length=20, db_index=True)
    balance = DecimalField(max_digits=14, decimal_places=4, default=decimal.Decimal("0.0000"))
    credit_limit = DecimalField(max_digits=14, decimal_places=4, default=decimal.Decimal("1000.0000"))
    status = CharField(
        max_length=20,
        choices=OrderStatus,
        default=OrderStatus.PENDING.value,
    )

    class Meta:
        db_table = "billing_accounts"

    def clean(self) -> None:
        """Business Invariant: Balance cannot fall below negative credit limit."""
        super().clean()
        if self.balance is not None and self.credit_limit is not None:
            max_overdraft = -self.credit_limit
            if self.balance < max_overdraft:
                raise ValidationError(
                    f"Balance ({self.balance}) exceeds allowed credit limit ({self.credit_limit})."
                )


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 33 Model Schema & Descriptor Verification Suite...")

    # 1. Test CharField null=True Prohibition
    try:
        CharField(max_length=50, null=True)
        assert False, "Should have prohibited null=True on CharField"
    except ValueError:
        pass
    print("  [+] Prohibiting null=True on CharField verified.")

    # 2. Test Float Prohibition on DecimalField
    dec_field = DecimalField(max_digits=10, decimal_places=2)
    try:
        dec_field.to_python(45.99)
        assert False, "Should have prohibited float input on DecimalField"
    except ValidationError:
        pass
    assert dec_field.to_python("45.99") == decimal.Decimal("45.99")
    assert dec_field.to_python(100) == decimal.Decimal("100")
    print("  [+] Precision currency float rejection verified.")

    # 3. Test Metaclass & Metadata Inheritance
    assert CustomerAccount._meta.db_table == "billing_accounts"
    assert "created_at" in CustomerAccount._meta.fields
    assert "updated_at" in CustomerAccount._meta.fields
    assert "account_number" in CustomerAccount._meta.fields
    assert CustomerAccount._meta.pk_field is not None
    assert CustomerAccount._meta.pk_field.name == "id"
    print("  [+] Metaclass schema compilation and field inheritance verified.")

    # 4. Test Valid Instance Creation & Persistence
    account = CustomerAccount(
        account_number="ACCT-987654",
        balance=decimal.Decimal("250.5000"),
        credit_limit=decimal.Decimal("500.0000"),
        status="PENDING",
    )
    account.save()
    assert account._is_persisted is True
    assert isinstance(account.pk, uuid.UUID)
    assert account.created_at is not None
    print("  [+] Model instantiation, auto-UUID primary key, and save verified.")

    # 5. Test Max Length Validation
    long_account = CustomerAccount(account_number="A" * 25)
    try:
        long_account.full_clean()
        assert False, "Should have failed max_length validation"
    except ValidationError as exc:
        assert "account_number" in exc.message_dict
    print("  [+] Max length constraint validation verified.")

    # 6. Test Invalid Enum Choice
    bad_choice_account = CustomerAccount(
        account_number="ACCT-001",
        status="NON_EXISTENT_STATUS",
    )
    try:
        bad_choice_account.full_clean()
        assert False, "Should have failed enum choice validation"
    except ValidationError as exc:
        assert "status" in exc.message_dict
    print("  [+] Enum choice constraint validation verified.")

    # 7. Test Multi-Field clean() Business Invariant
    overdraft_account = CustomerAccount(
        account_number="ACCT-002",
        balance=decimal.Decimal("-1500.0000"),  # Below -1000 credit limit
        credit_limit=decimal.Decimal("1000.0000"),
        status="PENDING",
    )
    try:
        overdraft_account.save(enforce_clean=True)
        assert False, "Should have failed overdraft validation in clean()"
    except ValidationError:
        pass
    print("  [+] Multi-field business invariant validation in clean() verified.")


def benchmark_model_pipeline(iterations: int = 50_000) -> None:
    """Benchmark model instantiation, descriptor access, and validation throughput."""
    start_time = time.perf_counter()
    for i in range(iterations):
        acct = CustomerAccount(
            account_number=f"ACCT-{i}",
            balance=decimal.Decimal("100.0000"),
            credit_limit=decimal.Decimal("500.0000"),
            status="PENDING",
        )
        acct.full_clean()
    elapsed = time.perf_counter() - start_time

    latency_us = (elapsed / iterations) * 1_000_000
    ops_per_sec = iterations / elapsed

    print("\n" + "=" * 65)
    print("DAY 33: MODEL DESCRIPTOR & VALIDATION ENGINE BENCHMARK")
    print("=" * 65)
    print(f"Iterations:        {iterations:,}")
    print(f"Total Time:        {elapsed:.4f} seconds")
    print(f"Average Latency:   {latency_us:.2f} microseconds per instance")
    print(f"Throughput:        {ops_per_sec:,.0f} instances/sec")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_model_pipeline()
