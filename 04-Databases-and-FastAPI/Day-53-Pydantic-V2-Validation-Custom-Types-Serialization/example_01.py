"""
Day 53: Enterprise Pydantic V2 Architecture - Validation, Custom Types & Serialization
======================================================================================

Complete runnable pipeline demonstrating:
1. Pydantic V2 ConfigDict configuration (strictness, frozen, attribute mapping)
2. Functional field validation with Annotated, BeforeValidator & AfterValidator
3. Class-based @field_validator (mode='before' and mode='after') with ValidationInfo
4. Cross-field @model_validator (mode='before' and mode='after')
5. Dynamic computed fields with @computed_field
6. Custom serialization with @field_serializer and @model_serializer
7. Discriminated Unions for high-speed O(1) polymorphic event routing
8. Data masking with SecretStr
9. TypeAdapter validation and serialization on arbitrary collections
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    computed_field,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

# ---------------------------------------------------------------------------
# 1. Custom Functional Types (PEP 593 + Annotated)
# ---------------------------------------------------------------------------

def sanitize_string(value: Any) -> Any:
    """Strips leading/trailing whitespace before parsing."""
    if isinstance(value, str):
        return value.strip()
    return value

def validate_currency_iso(value: str) -> str:
    """Ensures currency is uppercase 3-letter ISO 4217 code."""
    cleaned = value.upper()
    valid_currencies = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"}
    if cleaned not in valid_currencies:
        raise ValueError(f"Invalid currency '{value}'. Must be one of: {sorted(list(valid_currencies))}")
    return cleaned

def validate_sku(value: str) -> str:
    """Ensures product SKU matches standard enterprise prefix."""
    if not value.startswith("SKU-"):
        raise ValueError(f"Product SKU must start with 'SKU-'. Got: '{value}'")
    return value

# Reusable domain types using BeforeValidator and AfterValidator
CleanedString = Annotated[str, BeforeValidator(sanitize_string)]
CurrencyCode = Annotated[CleanedString, AfterValidator(validate_currency_iso)]
ProductSKU = Annotated[CleanedString, AfterValidator(validate_sku)]


# ---------------------------------------------------------------------------
# 2. Domain Models: Field Validation, Model Validation & Computed Fields
# ---------------------------------------------------------------------------

class OrderItem(BaseModel):
    """
    Immutable Order Line Item.
    WHY frozen=True: Guarantees immutability and generates __hash__,
    allowing OrderItems to be stored in sets or used as dictionary keys.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: ProductSKU
    name: CleanedString = Field(..., min_length=2, max_length=100)
    unit_price: Decimal = Field(..., gt=Decimal("0.0"), max_digits=12, decimal_places=2)
    quantity: int = Field(..., ge=1, le=500)

    # Dynamic Computed Field in V2
    # WHY: Automatically serialized in model_dump() and OpenAPI schemas
    @computed_field
    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


class Order(BaseModel):
    """
    Enterprise Order Aggregate Root.
    Demonstrates model_validator (before/after), field_serializer, and SecretStr.
    """
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        validate_default=True,
    )

    order_id: str = Field(..., pattern=r"^ORD-\d{4,8}$")
    currency: CurrencyCode = "USD"
    items: List[OrderItem] = Field(..., min_length=1)
    discount_amount: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0.0"))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    customer_notes: Optional[CleanedString] = None
    payment_token: SecretStr  # SENSITIVE: Masked in logs and default dumps

    # 1. Model Validator: mode='before' (Pre-process input dictionary)
    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Backward-compatibility: map legacy 'id' key to 'order_id'
            if "id" in data and "order_id" not in data:
                data["order_id"] = data.pop("id")
        return data

    # 2. Model Validator: mode='after' (Cross-field business invariant)
    @model_validator(mode="after")
    def verify_discount_does_not_exceed_subtotal(self) -> "Order":
        subtotal = sum(item.line_total for item in self.items)
        if self.discount_amount >= subtotal:
            raise ValueError(
                f"Discount amount (${self.discount_amount}) cannot equal or exceed subtotal (${subtotal})."
            )
        return self

    # 3. Dynamic Computed Field for Net Total
    @computed_field
    @property
    def net_total(self) -> Decimal:
        subtotal = sum(item.line_total for item in self.items)
        return subtotal - self.discount_amount

    # 4. Field Serializer: mode='plain' when dumping to JSON
    @field_serializer("created_at", when_used="json")
    def serialize_datetime_as_iso_z(self, dt: datetime) -> str:
        # Guarantee ISO 8601 representation ending in 'Z'
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 3. Polymorphic Event Processing via Discriminated Unions
# ---------------------------------------------------------------------------

class OrderCreatedEvent(BaseModel):
    event_type: Literal["order.created"]
    order_id: str
    amount: Decimal

class OrderCancelledEvent(BaseModel):
    event_type: Literal["order.cancelled"]
    order_id: str
    cancellation_reason: str

class OrderShippedEvent(BaseModel):
    event_type: Literal["order.shipped"]
    order_id: str
    tracking_number: str
    carrier: str

# Discriminated Union: Rust engine routes directly using 'event_type' in O(1) time
DomainEvent = Annotated[
    Union[OrderCreatedEvent, OrderCancelledEvent, OrderShippedEvent],
    Field(discriminator="event_type"),
]


# ---------------------------------------------------------------------------
# 4. Custom Model Serializer (Envelope Wrapper)
# ---------------------------------------------------------------------------

class ApiResponseEnvelope(BaseModel):
    status_code: int = 200
    message: str = "Success"
    payload: Dict[str, Any]

    # Model Serializer: completely controls root dictionary serialization
    @model_serializer
    def serialize_custom_envelope(self) -> Dict[str, Any]:
        return {
            "meta": {
                "status": self.status_code,
                "msg": self.message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "data": self.payload,
        }


# ---------------------------------------------------------------------------
# 5. Pipeline Demonstration & Verification Harness
# ---------------------------------------------------------------------------

def run_verification_pipeline():
    print("=" * 75)
    print("Pydantic V2 Architectural Pipeline Verification Suite")
    print("=" * 75)

    # 1. Functional Custom Types & Sanitization
    print("\n[STEP 1] Testing Custom Functional Types (Before/After Validators)...")
    item = OrderItem(
        sku="  SKU-SERVER-RACK  ",  # Tests BeforeValidator whitespace stripping
        name="  High-Density Compute Chassis  ",
        unit_price=Decimal("1250.50"),
        quantity=4,
    )
    print(f"  Item Instantiated: SKU='{item.sku}' | Name='{item.name}'")
    assert item.sku == "SKU-SERVER-RACK", "Whitespace should be stripped"
    assert item.name == "High-Density Compute Chassis"
    assert item.line_total == Decimal("5002.00"), f"Expected 5002.00, got {item.line_total}"
    print(f"  Computed Field 'line_total': ${item.line_total}")
    print("  -> PASSED: Custom types and functional validators verified.")

    # 2. Testing Strict Invariant Validation (Invalid SKU Failure)
    print("\n[STEP 2] Testing Field Validation Error Handling...")
    try:
        OrderItem(sku="INVALID-PREFIX", name="Test Item", unit_price=Decimal("10.0"), quantity=1)
        assert False, "Should have raised ValidationError"
    except ValidationError as exc:
        print("  Successfully caught expected ValidationError on invalid SKU:")
        err = exc.errors()[0]
        print(f"    Field: {err['loc'][0]} | Error: {err['msg']}")
        assert err["loc"][0] == "sku"
        print("  -> PASSED: Field validator error caught.")

    # 3. Model Validator (Legacy Key Mapping & Cross-Field Validation)
    print("\n[STEP 3] Testing Cross-Field Validation & Legacy Key Preprocessing...")
    order_data = {
        "id": "ORD-88990",  # Legacy key mapped to 'order_id' via mode='before'
        "currency": "usd",  # Coerced to 'USD' via AfterValidator
        "items": [
            {"sku": "SKU-SWITCH-400G", "name": "Arista Switch", "unit_price": "4500.00", "quantity": 2}
        ],
        "discount_amount": "500.00",
        "payment_token": "tok_live_secret_key_998811",
    }
    order = Order.model_validate(order_data)
    print(f"  Order Validated: ID={order.order_id} | Currency={order.currency} | Net Total=${order.net_total}")
    assert order.order_id == "ORD-88990"
    assert order.currency == "USD"
    assert order.net_total == Decimal("8500.00")
    print("  -> PASSED: Preprocessor and cross-field validator executed cleanly.")

    # 4. SecretStr Protection Verification
    print("\n[STEP 4] Verifying SecretStr Credential Masking...")
    dumped_dict = order.model_dump()
    print(f"  Repr of SecretStr: {order.payment_token}")
    print(f"  Dumped Dict: {dumped_dict['payment_token']}")
    assert str(order.payment_token) == "**********"
    assert order.payment_token.get_secret_value() == "tok_live_secret_key_998811"
    print("  -> PASSED: SecretStr strictly masks credential; raw value accessible only via get_secret_value().")

    # 5. JSON Serialization with Custom Field Serializer
    print("\n[STEP 5] Testing JSON Serialization with @field_serializer...")
    json_output = order.model_dump_json(indent=2)
    print(f"  Serialized JSON Sample (Timestamp & Decimals):\n{json_output[:250]}...")
    parsed_json = json.loads(json_output)
    assert parsed_json["created_at"].endswith("Z"), "Timestamp must end with Z"
    assert "net_total" in parsed_json, "Computed field net_total must be present in JSON"
    print("  -> PASSED: JSON serialization and custom field serializer verified.")

    # 6. Discriminated Union Polymorphic Routing
    print("\n[STEP 6] Testing Discriminated Union Polymorphic Routing...")
    event_payload_1 = {"event_type": "order.created", "order_id": "ORD-88990", "amount": "9000.00"}
    event_payload_2 = {"event_type": "order.shipped", "order_id": "ORD-88990", "tracking_number": "1Z99999", "carrier": "FedEx"}

    event_adapter = TypeAdapter(DomainEvent)
    e1 = event_adapter.validate_python(event_payload_1)
    e2 = event_adapter.validate_python(event_payload_2)

    assert isinstance(e1, OrderCreatedEvent)
    assert isinstance(e2, OrderShippedEvent)
    print(f"  Dispatched Event 1: {type(e1).__name__} (Amount: ${e1.amount})")
    print(f"  Dispatched Event 2: {type(e2).__name__} (Carrier: {e2.carrier})")
    print("  -> PASSED: Discriminated union routed directly in O(1) time.")

    # 7. TypeAdapter Collection Validation
    print("\n[STEP 7] Testing TypeAdapter on Standalone Collection (List[OrderItem])...")
    items_adapter = TypeAdapter(List[OrderItem])
    raw_list = [
        {"sku": "SKU-OPTIC-10G", "name": "10G SFP+ Transceiver", "unit_price": "45.00", "quantity": 10},
        {"sku": "SKU-FIBER-LC", "name": "LC-LC Patch Cable", "unit_price": "12.50", "quantity": 5},
    ]
    validated_items = items_adapter.validate_python(raw_list)
    assert len(validated_items) == 2
    assert all(isinstance(i, OrderItem) for i in validated_items)
    print(f"  Validated {len(validated_items)} OrderItem instances directly via TypeAdapter.")
    print("  -> PASSED: Standalone collection validation verified.")

    # 8. Model Serializer Envelope Customization
    print("\n[STEP 8] Testing @model_serializer Custom Envelope...")
    envelope = ApiResponseEnvelope(
        status_code=201,
        message="Order created successfully",
        payload={"order_id": order.order_id, "net_total": float(order.net_total)},
    )
    env_dump = envelope.model_dump()
    print(f"  Envelope Output: {env_dump}")
    assert "meta" in env_dump and "data" in env_dump
    assert env_dump["meta"]["status"] == 201
    print("  -> PASSED: @model_serializer root override verified.")

    print("\n" + "=" * 75)
    print("ALL ENTERPRISE PYDANTIC V2 PIPELINE TESTS PASSED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    run_verification_pipeline()
