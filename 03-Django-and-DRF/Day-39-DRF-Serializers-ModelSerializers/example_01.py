"""
Day 39: Django REST Framework (DRF): Serializers & ModelSerializers
Example 01: DRF Serialization & Validation Engine from First Principles

Architecture Overview:
----------------------
This module implements a pure Python, zero-external-dependency architectural model of the
Django REST Framework (DRF) serializer and validation engine. It models:
  1. Declarative Field Hierarchy: `CharField`, `IntegerField`, `FloatField`, `EmailField`,
     `ChoiceField`, `SerializerMethodField`.
  2. The 4-Stage Validation Lifecycle:
     `to_internal_value()` -> Field Validators -> `validate_<field>()` -> `validate(attrs)`.
  3. `Serializer` Invariants:
     Strict access control on `initial_data`, `validated_data`, and `data`.
  4. Outgoing Representation Engine (`to_representation`):
     Handling `read_only=True`, `write_only=True`, and dynamic computed fields.
  5. The Persistence Router (`save()`):
     Routing to `create()` or `update()` with request-scoped keyword argument injection.

WHY THIS ARCHITECTURE?
----------------------
In enterprise backend engineering, understanding how serializers validate input data and
route calls allows developers to write clean, reusable validation boundaries that protect
the service and database layers from malformed data and security vulnerabilities.
"""

from __future__ import annotations

import copy
import datetime
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


# ============================================================================
# 0. Sentinel Values & Exception Classes
# ============================================================================

class _EmptySentinel:
    """Sentinel representing an unset default or unprovided value."""
    def __repr__(self) -> str:
        return "<empty>"

empty = _EmptySentinel()


class ValidationError(Exception):
    """
    Simulates rest_framework.exceptions.ValidationError.
    Maintains a normalized dictionary of field errors or list of non-field errors.
    """
    def __init__(self, detail: Union[str, List[str], Dict[str, Any]]) -> None:
        if isinstance(detail, str):
            self.detail: Union[List[str], Dict[str, Any]] = [detail]
        elif isinstance(detail, (list, dict)):
            self.detail = detail
        else:
            self.detail = [str(detail)]
        super().__init__(str(self.detail))

    def __repr__(self) -> str:
        return f"ValidationError({self.detail})"


# ============================================================================
# 1. Declarative Field Hierarchy
# ============================================================================

class Field:
    """Base class for all serializer fields."""

    def __init__(
        self,
        read_only: bool = False,
        write_only: bool = False,
        required: bool = True,
        default: Any = empty,
        validators: Optional[List[Callable[[Any], None]]] = None,
        source: Optional[str] = None,
    ) -> None:
        if read_only and write_only:
            raise ValueError("Field cannot be both read_only and write_only.")

        self.read_only = read_only
        self.write_only = write_only
        self.required = required and not read_only and default is empty
        self.default = default
        self.validators = validators or []
        self.source = source
        self.field_name: Optional[str] = None
        self.parent: Optional["Serializer"] = None

    def bind(self, field_name: str, parent: "Serializer") -> None:
        """Binds the field to its containing serializer instance."""
        self.field_name = field_name
        self.parent = parent

    def to_internal_value(self, data: Any) -> Any:
        """Converts raw primitive input data into internal Python representation."""
        return data

    def to_representation(self, value: Any) -> Any:
        """Converts internal Python object attribute into primitive JSON-ready value."""
        return value

    def run_validators(self, value: Any) -> None:
        """Runs explicit field-level callable validators."""
        errors: List[str] = []
        for validator in self.validators:
            try:
                validator(value)
            except ValidationError as exc:
                if isinstance(exc.detail, list):
                    errors.extend(exc.detail)
                else:
                    errors.append(str(exc.detail))
        if errors:
            raise ValidationError(errors)


class CharField(Field):
    def __init__(
        self,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.min_length = min_length
        self.max_length = max_length

    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, (str, int, float)):
            raise ValidationError("Expected a string or valid character sequence.")
        value = str(data)

        if self.min_length is not None and len(value) < self.min_length:
            raise ValidationError(f"Ensure this field has at least {self.min_length} characters.")
        if self.max_length is not None and len(value) > self.max_length:
            raise ValidationError(f"Ensure this field has no more than {self.max_length} characters.")

        return value


class IntegerField(Field):
    def __init__(
        self,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.min_value = min_value
        self.max_value = max_value

    def to_internal_value(self, data: Any) -> int:
        try:
            val = int(data)
        except (ValueError, TypeError):
            raise ValidationError("A valid integer is required.")

        if self.min_value is not None and val < self.min_value:
            raise ValidationError(f"Ensure this value is greater than or equal to {self.min_value}.")
        if self.max_value is not None and val > self.max_value:
            raise ValidationError(f"Ensure this value is less than or equal to {self.max_value}.")
        return val


class FloatField(Field):
    def __init__(self, min_value: Optional[float] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.min_value = min_value

    def to_internal_value(self, data: Any) -> float:
        try:
            val = float(data)
        except (ValueError, TypeError):
            raise ValidationError("A valid number is required.")
        if self.min_value is not None and val < self.min_value:
            raise ValidationError(f"Ensure this value is greater than or equal to {self.min_value}.")
        return round(val, 2)


class EmailField(CharField):
    EMAIL_REGEX = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

    def to_internal_value(self, data: Any) -> str:
        value = super().to_internal_value(data).strip()
        if not self.EMAIL_REGEX.match(value):
            raise ValidationError("Enter a valid email address.")
        return value.lower()


class ChoiceField(Field):
    def __init__(self, choices: List[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.choices = set(choices)

    def to_internal_value(self, data: Any) -> str:
        val = str(data)
        if val not in self.choices:
            raise ValidationError(f"'{val}' is not a valid choice. Allowed: {sorted(self.choices)}")
        return val


class SerializerMethodField(Field):
    """
    Read-only dynamic computed field.
    Calls `get_<field_name>(self, obj)` on the parent serializer.
    """
    def __init__(self, method_name: Optional[str] = None, **kwargs: Any) -> None:
        kwargs["read_only"] = True
        super().__init__(**kwargs)
        self.method_name = method_name

    def to_representation(self, value: Any) -> Any:
        assert self.parent is not None and self.field_name is not None
        method_name = self.method_name or f"get_{self.field_name}"
        method = getattr(self.parent, method_name, None)
        if method is None or not callable(method):
            raise AttributeError(f"SerializerMethodField '{self.field_name}' missing method '{method_name}' on serializer.")
        return method(value)


# ============================================================================
# 2. Metaclass for Declarative Field Registration
# ============================================================================

class SerializerMetaclass(type):
    """
    Discovers Field declarations on the class and populates `_declared_fields`.
    Enables declarative syntax mirroring DRF serializers.
    """
    def __new__(mcs, name: str, bases: Tuple[type, ...], attrs: Dict[str, Any]) -> Any:
        fields: Dict[str, Field] = {}

        # Inherit fields from parent base classes
        for base in reversed(bases):
            if hasattr(base, "_declared_fields"):
                fields.update(copy.deepcopy(base._declared_fields))  # type: ignore

        # Discover fields declared on this class
        for key, value in list(attrs.items()):
            if isinstance(value, Field):
                fields[key] = value

        attrs["_declared_fields"] = fields
        return super().__new__(mcs, name, bases, attrs)


# ============================================================================
# 3. Base Serializer Class
# ============================================================================

class Serializer(metaclass=SerializerMetaclass):
    """
    Base serializer implementing DRF's 4-stage validation and bidirectional transform engine.
    """
    _declared_fields: Dict[str, Field] = {}

    def __init__(
        self,
        instance: Optional[Any] = None,
        data: Any = empty,
        context: Optional[Dict[str, Any]] = None,
        partial: bool = False,
    ) -> None:
        self.instance = instance
        self.initial_data = data
        self.context = context or {}
        self.partial = partial

        # Initialize field copies for this instance
        self.fields: Dict[str, Field] = copy.deepcopy(self._declared_fields)
        for name, field in self.fields.items():
            field.bind(field_name=name, parent=self)

        self._validated_data: Optional[Dict[str, Any]] = None
        self._errors: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------------
    # Data Access Properties & Invariants
    # ------------------------------------------------------------------------

    @property
    def validated_data(self) -> Dict[str, Any]:
        """
        WHY THIS ASSERTION?
        Accessing validated_data before calling is_valid() violates the serialization contract
        and could expose unvalidated, uncoerced client inputs to service layers.
        """
        if self._validated_data is None:
            raise AssertionError(
                "You must call .is_valid() before accessing .validated_data."
            )
        return self._validated_data

    @property
    def errors(self) -> Dict[str, Any]:
        if self._errors is None:
            raise AssertionError("You must call .is_valid() before accessing .errors.")
        return self._errors

    @property
    def data(self) -> Dict[str, Any]:
        """
        Returns the outbound serialized representation.
        If instance is provided, serializes instance.
        If write serializer has validated_data, serializes validated_data.
        """
        if self.instance is not None:
            return self.to_representation(self.instance)
        elif self._validated_data is not None:
            return self.to_representation(self._validated_data)
        raise AssertionError("Cannot access .data before calling .is_valid() or supplying an instance.")

    # ------------------------------------------------------------------------
    # The 4-Stage Validation Engine
    # ------------------------------------------------------------------------

    def is_valid(self, raise_exception: bool = False) -> bool:
        if self.initial_data is empty:
            raise AssertionError("Cannot call .is_valid() as no 'data=' keyword argument was passed.")

        if self._validated_data is not None:
            return not bool(self._errors)

        errors: Dict[str, Any] = {}
        validated_data: Dict[str, Any] = {}

        # STAGE 1: to_internal_value (Type coercion & Field existence)
        for field_name, field in self.fields.items():
            if field.read_only:
                continue

            if field_name not in self.initial_data:
                # Handle missing field: on partial updates, omitted fields are not populated with defaults
                if not self.partial and field.default is not empty:
                    validated_data[field_name] = field.default
                elif field.required and not self.partial:
                    errors[field_name] = ["This field is required."]
                continue

            raw_val = self.initial_data[field_name]

            try:
                # Stage 1: Coerce primitive to internal value
                internal_val = field.to_internal_value(raw_val)

                # STAGE 2: Explicit field validators
                field.run_validators(internal_val)

                # STAGE 3: Custom validate_<field_name> method on serializer
                validator_method = getattr(self, f"validate_{field_name}", None)
                if validator_method and callable(validator_method):
                    internal_val = validator_method(internal_val)

                validated_data[field_name] = internal_val

            except ValidationError as exc:
                errors[field_name] = exc.detail if isinstance(exc.detail, list) else [str(exc.detail)]

        # STAGE 4: Object-Level Cross-Field Validation
        if not errors:
            try:
                validated_data = self.validate(validated_data)
            except ValidationError as exc:
                if isinstance(exc.detail, dict):
                    errors.update(exc.detail)
                else:
                    errors["non_field_errors"] = exc.detail if isinstance(exc.detail, list) else [str(exc.detail)]

        if errors:
            self._errors = errors
            self._validated_data = {}
            if raise_exception:
                raise ValidationError(errors)
            return False

        self._errors = {}
        self._validated_data = validated_data
        return True

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Subclasses override this for object-level, cross-field validation."""
        return attrs

    # ------------------------------------------------------------------------
    # Representation & Persistence
    # ------------------------------------------------------------------------

    def to_representation(self, instance: Any) -> Dict[str, Any]:
        """Serializes an object or dictionary into native primitives."""
        ret: Dict[str, Any] = {}

        for field_name, field in self.fields.items():
            if field.write_only:
                # Never expose write-only fields in output representation!
                continue

            if isinstance(field, SerializerMethodField):
                ret[field_name] = field.to_representation(instance)
                continue

            source_key = field.source or field_name
            if isinstance(instance, dict):
                val = instance.get(source_key, empty)
            else:
                val = getattr(instance, source_key, empty)

            if val is not empty:
                ret[field_name] = field.to_representation(val)
            else:
                ret[field_name] = None

        return ret

    def save(self, **kwargs: Any) -> Any:
        """
        Persists validated data. Routes to create() or update() depending on
        whether an existing instance was provided.
        """
        assert self._validated_data is not None, "You must call .is_valid() before .save()."
        assert not self._errors, "Cannot call .save() with invalid data."

        # Merge injected kwargs into validated data
        combined_data = {**self._validated_data, **kwargs}

        if self.instance is not None:
            self.instance = self.update(self.instance, combined_data)
        else:
            self.instance = self.create(combined_data)

        return self.instance

    def create(self, validated_data: Dict[str, Any]) -> Any:
        raise NotImplementedError("`create()` must be implemented by concrete subclasses.")

    def update(self, instance: Any, validated_data: Dict[str, Any]) -> Any:
        raise NotImplementedError("`update()` must be implemented by concrete subclasses.")


# ============================================================================
# 4. Concrete Domain Serializer Implementation
# ============================================================================

class OrderEntity:
    """Simulates a domain model instance."""
    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", 101)
        self.customer_email = kwargs.get("customer_email", "")
        self.sku = kwargs.get("sku", "")
        self.quantity = kwargs.get("quantity", 1)
        self.unit_price = kwargs.get("unit_price", 0.0)
        self.discount_amount = kwargs.get("discount_amount", 0.0)
        self.status = kwargs.get("status", "PENDING")
        self.tenant_id = kwargs.get("tenant_id", "default")
        self.created_at = kwargs.get("created_at", datetime.datetime.now(datetime.timezone.utc))


class OrderSerializer(Serializer):
    """
    Concrete Serializer demonstrating:
      - Field declarations with constraints
      - Read-only and write-only fields
      - Dynamic SerializerMethodField
      - Field-level validation (validate_sku)
      - Object-level validation (validate)
    """
    id = IntegerField(read_only=True)
    customer_email = EmailField()
    sku = CharField(max_length=32)
    quantity = IntegerField(min_value=1, max_value=1000)
    unit_price = FloatField(min_value=0.01)
    discount_amount = FloatField(default=0.0, min_value=0.0)
    status = ChoiceField(choices=["PENDING", "CONFIRMED", "SHIPPED", "CANCELLED"], default="PENDING")
    
    # Write-only security attribute (e.g., promotional authorization code)
    promo_auth_token = CharField(write_only=True, required=False)

    # Computed read-only attribute
    net_total = SerializerMethodField()

    def get_net_total(self, obj: Any) -> float:
        """Computes subtotal minus discount."""
        if isinstance(obj, dict):
            qty = obj.get("quantity", 0)
            price = obj.get("unit_price", 0.0)
            discount = obj.get("discount_amount", 0.0)
        else:
            qty = obj.quantity
            price = obj.unit_price
            discount = obj.discount_amount
        return round(max(0.0, (qty * price) - discount), 2)

    def validate_sku(self, value: str) -> str:
        """
        Field-level validation:
        Enforces SKU format: SKU-<CAT>-<NUM>, e.g. SKU-TECH-1092
        """
        pattern = r"^SKU-[A-Z]{3,6}-[0-9]{3,6}$"
        if not re.match(pattern, value):
            raise ValidationError("Invalid SKU format. Must match 'SKU-<CATEGORY>-<NUMBER>' (e.g. SKU-TECH-1001).")
        return value

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Object-level validation:
        Enforces that discount cannot exceed the gross subtotal!
        In partial updates, falls back to instance attributes for cross-field checks.
        """
        qty = attrs.get("quantity")
        price = attrs.get("unit_price")
        discount = attrs.get("discount_amount")

        # Fall back to existing instance values during partial updates
        if self.instance is not None:
            if qty is None:
                qty = self.instance.quantity
            if price is None:
                price = self.instance.unit_price
            if discount is None:
                discount = self.instance.discount_amount

        qty = qty if qty is not None else 0
        price = price if price is not None else 0.0
        discount = discount if discount is not None else 0.0

        subtotal = qty * price
        if subtotal > 0 and discount >= subtotal:
            raise ValidationError({
                "discount_amount": f"Discount (${discount:.2f}) cannot equal or exceed subtotal (${subtotal:.2f})."
            })

        return attrs

    def create(self, validated_data: Dict[str, Any]) -> OrderEntity:
        return OrderEntity(**validated_data)

    def update(self, instance: OrderEntity, validated_data: Dict[str, Any]) -> OrderEntity:
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        return instance


# ============================================================================
# 5. Comprehensive Verification Suite
# ============================================================================

def run_tests() -> None:
    print("======================================================================")
    print(" Day 39: DRF Serialization & Validation Engine Architectural Tests")
    print("======================================================================\n")

    # ------------------------------------------------------------------------
    # Test 1: Successful Deserialization & Invariant Checks
    # ------------------------------------------------------------------------
    print("[TEST 1] Successful Deserialization & Accessor Invariants...")
    valid_payload = {
        "customer_email": "ALICE.DEV@AcmeCorp.com",
        "sku": "SKU-PRO-8899",
        "quantity": 3,
        "unit_price": 50.0,
        "discount_amount": 10.0,
        "promo_auth_token": "secret_promo_token_99",
    }

    serializer = OrderSerializer(data=valid_payload)

    # Invariant: validated_data cannot be accessed before is_valid()
    try:
        _ = serializer.validated_data
        assert False, "Should raise AssertionError before is_valid()"
    except AssertionError as e:
        print("  -> Caught expected pre-validation assertion error:", e)

    # Run validation
    is_valid = serializer.is_valid(raise_exception=True)
    assert is_valid is True
    assert serializer.validated_data["customer_email"] == "alice.dev@acmecorp.com"  # Normalized!
    assert serializer.validated_data["status"] == "PENDING"  # Default injected!
    print("  -> Validation passed. Clean validated data:", serializer.validated_data)

    # Persist via save() with extra context injection (tenant_id)
    order_instance = serializer.save(tenant_id="tenant_charlie")
    assert isinstance(order_instance, OrderEntity)
    assert order_instance.tenant_id == "tenant_charlie"
    print("  -> Entity persisted via save() with injected tenant_id.")

    # Check serialized outbound representation
    outbound_data = serializer.data
    assert "promo_auth_token" not in outbound_data, "Write-only field must NOT appear in output!"
    assert outbound_data["net_total"] == 140.0  # (3 * 50) - 10 = 140.0
    print("  -> Outbound serialization verified (write-only masked, computed field generated):", outbound_data)
    print("  -> Test 1 PASSED.\n")

    # ------------------------------------------------------------------------
    # Test 2: Field-Level Validation Failure (Bad SKU)
    # ------------------------------------------------------------------------
    print("[TEST 2] Field-Level Validation Rejection (Invalid SKU)...")
    bad_sku_payload = {
        "customer_email": "bob@example.com",
        "sku": "INVALID_SKU_FORMAT",
        "quantity": 2,
        "unit_price": 20.0,
    }
    s2 = OrderSerializer(data=bad_sku_payload)
    assert s2.is_valid() is False
    assert "sku" in s2.errors
    print("  -> Caught expected SKU validation error:", s2.errors["sku"])
    print("  -> Test 2 PASSED.\n")

    # ------------------------------------------------------------------------
    # Test 3: Object-Level Cross-Field Validation Failure (Excessive Discount)
    # ------------------------------------------------------------------------
    print("[TEST 3] Object-Level Validation Rejection (Discount Exceeds Subtotal)...")
    bad_discount_payload = {
        "customer_email": "bob@example.com",
        "sku": "SKU-GADGET-102",
        "quantity": 1,
        "unit_price": 25.0,
        "discount_amount": 50.0,  # FLAW: $50 discount on $25 order!
    }
    s3 = OrderSerializer(data=bad_discount_payload)
    assert s3.is_valid() is False
    assert "discount_amount" in s3.errors
    print("  -> Caught expected cross-field validation error:", s3.errors["discount_amount"])
    print("  -> Test 3 PASSED.\n")

    # ------------------------------------------------------------------------
    # Test 4: Serialization of Existing Domain Model Instance
    # ------------------------------------------------------------------------
    print("[TEST 4] Read Serialization of Existing Model Instance...")
    existing_order = OrderEntity(
        id=777,
        customer_email="diana@cloud.io",
        sku="SKU-SERV-900",
        quantity=5,
        unit_price=100.0,
        discount_amount=25.0,
        status="CONFIRMED",
    )
    s4 = OrderSerializer(instance=existing_order)
    read_data = s4.data
    assert read_data["id"] == 777
    assert read_data["customer_email"] == "diana@cloud.io"
    assert read_data["net_total"] == 475.0  # (5 * 100) - 25 = 475.0
    print("  -> Model instance serialized cleanly:", read_data)
    print("  -> Test 4 PASSED.\n")

    # ------------------------------------------------------------------------
    # Test 5: Partial Update (PATCH)
    # ------------------------------------------------------------------------
    print("[TEST 5] Partial Update (PATCH) on Existing Instance...")
    patch_payload = {"status": "SHIPPED"}
    s5 = OrderSerializer(instance=existing_order, data=patch_payload, partial=True)
    assert s5.is_valid() is True
    updated_order = s5.save()
    assert updated_order.status == "SHIPPED"
    assert updated_order.sku == "SKU-SERV-900"  # Unchanged
    print("  -> Partial update succeeded. Updated status:", updated_order.status)
    print("  -> Test 5 PASSED.\n")

    print("All Day 39 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
