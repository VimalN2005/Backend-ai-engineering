# Practical DRF Serializer & Validation Challenges

This document contains 3 enterprise-grade engineering challenges covering dynamic serializer field projection, atomic nested collection reconciliation, and polymorphic discriminator validation. Each challenge includes a real-world scenario, architectural constraints, and an executable reference solution.

---

## Challenge 1: Dynamic Field-Pruning Serializer Engine (`fields` / `exclude`)

### Scenario
An enterprise analytics platform serves high-frequency telemetry requests to both browser dashboards and bandwidth-constrained IoT edge devices. Returning all 25 fields of an asset record creates massive JSON serialization overhead. You must engineer a dynamic serializer base class that inspects `fields` (whitelist) and `exclude` (blacklist) arguments, dynamically pruning unnecessary field definitions in `__init__` before serialization occurs.

### Requirements & Constraints
1. **Whitelist Projection**: If `fields=['id', 'name']` is supplied, only those fields must appear in `to_representation()`.
2. **Blacklist Projection**: If `exclude=['internal_metrics', 'debug_trace']` is supplied, all fields *except* those must appear.
3. **Validation Invariant**: Whitelisted fields must exist in declared fields; passing unknown field names must raise `ValueError`.
4. **Zero External Dependencies**: Pure Python standard library only.

### Executable Solution & Verification

```python
import copy
from typing import Any, Dict, List, Optional, Set


class Field:
    def __init__(self, field_type: type = str, default: Any = None) -> None:
        self.field_type = field_type
        self.default = default


class DynamicFieldsSerializer:
    """
    Serializer providing dynamic field selection via `fields` and `exclude` parameters.
    """

    def __init__(
        self,
        instance: Optional[Any] = None,
        fields: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
    ) -> None:
        self.instance = instance
        # Deep copy declared fields to keep instance isolated
        self.fields: Dict[str, Field] = copy.deepcopy(self.get_declared_fields())

        declared_keys: Set[str] = set(self.fields.keys())

        if fields is not None:
            allowed = set(fields)
            invalid = allowed - declared_keys
            if invalid:
                raise ValueError(f"Invalid fields specified: {sorted(invalid)}. Allowed: {sorted(declared_keys)}")
            
            # Prune non-whitelisted fields
            for key in list(self.fields.keys()):
                if key not in allowed:
                    del self.fields[key]

        if exclude is not None:
            for key in exclude:
                self.fields.pop(key, None)

    def get_declared_fields(self) -> Dict[str, Field]:
        return {}

    def to_representation(self, obj: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for field_name in self.fields:
            if isinstance(obj, dict):
                val = obj.get(field_name)
            else:
                val = getattr(obj, field_name, None)
            result[field_name] = val
        return result


class DeviceTelemetrySerializer(DynamicFieldsSerializer):
    def get_declared_fields(self) -> Dict[str, Field]:
        return {
            "device_id": Field(str),
            "hostname": Field(str),
            "firmware_version": Field(str),
            "cpu_utilization": Field(float),
            "memory_free_mb": Field(int),
            "internal_ip": Field(str),
            "debug_trace": Field(str),
        }


# --- Verification Harness ---
if __name__ == "__main__":
    sample_telemetry = {
        "device_id": "EDGE-9920",
        "hostname": "gateway-us-east-1",
        "firmware_version": "v2.14.0",
        "cpu_utilization": 42.5,
        "memory_free_mb": 1024,
        "internal_ip": "10.200.4.15",
        "debug_trace": "STACK_TRACE_BUFFER_HEX_DEADBEEF",
    }

    # 1. Test Full Serialization (Default)
    s_full = DeviceTelemetrySerializer(instance=sample_telemetry)
    full_data = s_full.to_representation(sample_telemetry)
    assert len(full_data) == 7
    assert "debug_trace" in full_data

    # 2. Test Whitelist Projection (fields)
    s_whitelist = DeviceTelemetrySerializer(
        instance=sample_telemetry,
        fields=["device_id", "cpu_utilization"]
    )
    white_data = s_whitelist.to_representation(sample_telemetry)
    assert set(white_data.keys()) == {"device_id", "cpu_utilization"}
    assert white_data["cpu_utilization"] == 42.5

    # 3. Test Blacklist Projection (exclude)
    s_blacklist = DeviceTelemetrySerializer(
        instance=sample_telemetry,
        exclude=["debug_trace", "internal_ip"]
    )
    black_data = s_blacklist.to_representation(sample_telemetry)
    assert "debug_trace" not in black_data
    assert "internal_ip" not in black_data
    assert "hostname" in black_data

    # 4. Test Invalid Field Whitelist Protection
    try:
        DeviceTelemetrySerializer(fields=["device_id", "non_existent_column"])
        assert False, "Should raise ValueError for invalid field"
    except ValueError as exc:
        assert "Invalid fields specified" in str(exc)

    print("[SUCCESS] Challenge 1: Dynamic Field-Pruning Serializer Engine verified.")
```

---

## Challenge 2: Full-Reconciliation Writable Nested Collection Serializer

### Scenario
An invoicing microservice manages complex B2B `Invoice` aggregates containing multiple `InvoiceLineItem` children. When an invoice is updated, clients submit the full intended state of all line items. 

You must implement a writable nested serializer that reconciles incoming line items against existing database records:
1. **Existing Items** (ID present and matches existing line item): Update line item attributes (`description`, `quantity`, `rate`).
2. **New Items** (no ID present): Instantiate and associate new child line items.
3. **Removed Items** (existing items omitted from payload): Delete from the invoice collection.
4. **Totals Recalculation**: Automatically update `subtotal` and `tax_amount` on the parent invoice.

### Requirements & Constraints
- Pure Python standard library only.
- Must execute atomic reconciliation: if any line item is invalid, no modifications are committed.
- Reconciled collection must recalculate parent totals accurately.

### Executable Solution & Verification

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class InvoiceLineItem:
    id: int
    invoice_id: int
    description: str
    quantity: int
    unit_rate: float

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_rate, 2)


@dataclass
class Invoice:
    id: int
    client_name: str
    status: str
    tax_rate: float = 0.10  # 10% tax
    items: List[InvoiceLineItem] = field(default_factory=list)

    @property
    def subtotal(self) -> float:
        return round(sum(item.total for item in self.items), 2)

    @property
    def total_amount(self) -> float:
        return round(self.subtotal * (1.0 + self.tax_rate), 2)


class WritableInvoiceSerializer:
    def __init__(self, instance: Optional[Invoice] = None, data: Optional[Dict[str, Any]] = None) -> None:
        self.instance = instance
        self.initial_data = data
        self._validated_data: Optional[Dict[str, Any]] = None

    def is_valid(self) -> bool:
        if not self.initial_data:
            return False

        # Validate line items
        raw_items = self.initial_data.get("items", [])
        if not isinstance(raw_items, list) or len(raw_items) == 0:
            return False

        validated_items = []
        for it in raw_items:
            qty = int(it.get("quantity", 0))
            rate = float(it.get("unit_rate", 0.0))
            desc = str(it.get("description", "")).strip()

            if qty <= 0 or rate <= 0.0 or not desc:
                return False

            validated_items.append({
                "id": it.get("id"),
                "description": desc,
                "quantity": qty,
                "unit_rate": rate,
            })

        self._validated_data = {
            "client_name": self.initial_data.get("client_name", self.instance.client_name if self.instance else ""),
            "status": self.initial_data.get("status", self.instance.status if self.instance else "DRAFT"),
            "items": validated_items,
        }
        return True

    def save(self) -> Invoice:
        assert self._validated_data is not None, "Call is_valid() before save()"
        assert self.instance is not None, "Challenge focuses on nested update reconciliation"

        validated = self._validated_data
        items_payload = validated["items"]

        # 1. Update parent scalars
        self.instance.client_name = validated["client_name"]
        self.instance.status = validated["status"]

        # 2. Reconcile nested items
        existing_items_by_id = {item.id: item for item in self.instance.items}
        reconciled: List[InvoiceLineItem] = []

        # Track existing items preserved in update
        next_id = max([i.id for i in self.instance.items], default=0) + 1

        for it_data in items_payload:
            item_id = it_data.get("id")

            if item_id is not None and item_id in existing_items_by_id:
                # Case A: UPDATE existing item
                existing = existing_items_by_id[item_id]
                existing.description = it_data["description"]
                existing.quantity = it_data["quantity"]
                existing.unit_rate = it_data["unit_rate"]
                reconciled.append(existing)
            else:
                # Case B: CREATE new item
                new_item = InvoiceLineItem(
                    id=next_id,
                    invoice_id=self.instance.id,
                    description=it_data["description"],
                    quantity=it_data["quantity"],
                    unit_rate=it_data["unit_rate"],
                )
                next_id += 1
                reconciled.append(new_item)

        # Case C: DELETE omitted items (reconciled replaces existing list)
        self.instance.items = reconciled
        return self.instance


# --- Verification Harness ---
if __name__ == "__main__":
    invoice = Invoice(
        id=5001,
        client_name="Global Retailers Inc",
        status="DRAFT",
        items=[
            InvoiceLineItem(id=1, invoice_id=5001, description="Consulting Hours", quantity=10, unit_rate=150.0),
            InvoiceLineItem(id=2, invoice_id=5001, description="Server Setup Fee", quantity=1, unit_rate=500.0),
        ],
    )
    assert invoice.subtotal == 2000.0  # (10 * 150) + (1 * 500) = 2000.0
    assert invoice.total_amount == 2200.0  # 2000 + 10% tax = 2200.0

    # Execute Reconciliation:
    # - Update item 1: quantity 10 -> 20 (rate 150.0 = 3000.0)
    # - Delete item 2: (omitted)
    # - Create item 3: SSL Certificate (quantity 1, rate 200.0)
    update_payload = {
        "status": "FINALIZED",
        "items": [
            {"id": 1, "description": "Consulting Hours", "quantity": 20, "unit_rate": 150.0},
            {"description": "SSL Certificate Renewal", "quantity": 1, "unit_rate": 200.0},
        ],
    }

    serializer = WritableInvoiceSerializer(instance=invoice, data=update_payload)
    assert serializer.is_valid() is True
    updated = serializer.save()

    assert updated.status == "FINALIZED"
    assert len(updated.items) == 2
    # Verify Item 1 updated in place
    assert updated.items[0].id == 1
    assert updated.items[0].quantity == 20
    # Verify Item 2 was deleted
    assert all(i.id != 2 for i in updated.items)
    # Verify Item 3 created
    assert updated.items[1].description == "SSL Certificate Renewal"
    # Verify Recalculated Totals: Subtotal = 3000 + 200 = 3200.0; Total = 3520.0
    assert updated.subtotal == 3200.0
    assert updated.total_amount == 3520.0

    print("[SUCCESS] Challenge 2: Full-Reconciliation Writable Nested Serializer verified.")
```

---

## Challenge 3: Polymorphic Discriminator Payment Serializer

### Scenario
A multi-currency payment orchestration engine accepts transactions via three heterogeneous payment methods: `credit_card`, `bank_wire`, and `crypto`. Each method requires distinct validation criteria:
1. **`credit_card`**: 16-digit card number (Luhn checksum or numeric verification), 2-digit expiration month (1-12), 4-digit expiration year (>= 2026), 3-4 digit CVV.
2. **`bank_wire`**: Valid IBAN (alphanumeric, length 15-34), valid SWIFT/BIC (8-11 alphanumeric characters).
3. **`crypto`**: Wallet address starting with `0x` (Ethereum) or `bc1` (Bitcoin SegWit), supported currency (`BTC`, `ETH`, `USDC`).

You must implement a polymorphic payment serializer that inspects the `payment_method` discriminator in `validate()`, dispatches to specialized sub-validators, and aggregates normalized data.

### Requirements & Constraints
- Pure Python standard library only (`re`, `datetime`).
- Invalid discriminator values or malformed sub-payloads must raise descriptive validation errors.

### Executable Solution & Verification

```python
import re
from typing import Any, Dict, List


class PaymentValidationError(Exception):
    def __init__(self, detail: Dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail))


class CreditCardValidator:
    @staticmethod
    def validate(details: Dict[str, Any]) -> Dict[str, Any]:
        errors = {}
        card_num = re.sub(r"[\s\-]", "", str(details.get("card_number", "")))
        if not re.match(r"^\d{16}$", card_num):
            errors["card_number"] = ["Card number must be exactly 16 digits."]

        month = details.get("exp_month")
        if month is None or not (1 <= int(month) <= 12):
            errors["exp_month"] = ["Expiration month must be between 1 and 12."]

        year = details.get("exp_year")
        if year is None or int(year) < 2026:
            errors["exp_year"] = ["Card is expired. Year must be 2026 or later."]

        cvv = str(details.get("cvv", ""))
        if not re.match(r"^\d{3,4}$", cvv):
            errors["cvv"] = ["CVV must be 3 or 4 digits."]

        if errors:
            raise PaymentValidationError(errors)
        return {"card_last4": card_num[-4:], "exp_month": int(month), "exp_year": int(year)}


class BankWireValidator:
    @staticmethod
    def validate(details: Dict[str, Any]) -> Dict[str, Any]:
        errors = {}
        iban = re.sub(r"\s", "", str(details.get("iban", ""))).upper()
        if not (15 <= len(iban) <= 34 and iban.isalnum()):
            errors["iban"] = ["Invalid IBAN format. Must be 15-34 alphanumeric characters."]

        swift = str(details.get("swift_bic", "")).strip().upper()
        if not re.match(r"^[A-Z0-9]{8}([A-Z0-9]{3})?$", swift):
            errors["swift_bic"] = ["Invalid SWIFT/BIC code. Must be 8 or 11 characters."]

        if errors:
            raise PaymentValidationError(errors)
        return {"iban_masked": f"{iban[:4]}...{iban[-4:]}", "swift_bic": swift}


class CryptoValidator:
    SUPPORTED = {"BTC", "ETH", "USDC"}

    @staticmethod
    def validate(details: Dict[str, Any]) -> Dict[str, Any]:
        errors = {}
        addr = str(details.get("wallet_address", "")).strip()
        if not (addr.startswith("0x") or addr.startswith("bc1")) or len(addr) < 26:
            errors["wallet_address"] = ["Invalid crypto address. Must start with '0x' or 'bc1'."]

        curr = str(details.get("currency", "")).upper()
        if curr not in CryptoValidator.SUPPORTED:
            errors["currency"] = [f"Unsupported currency. Allowed: {sorted(CryptoValidator.SUPPORTED)}"]

        if errors:
            raise PaymentValidationError(errors)
        return {"wallet_address": addr, "currency": curr}


class PolymorphicPaymentSerializer:
    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data
        self.validated_data: Dict[str, Any] = {}
        self.errors: Dict[str, Any] = {}

    def is_valid(self) -> bool:
        method = self.data.get("payment_method")
        amount = self.data.get("amount")
        details = self.data.get("details")

        if amount is None or float(amount) <= 0.0:
            self.errors["amount"] = ["Amount must be greater than zero."]

        if not details or not isinstance(details, dict):
            self.errors["details"] = ["Payment details object is required."]
            return False

        try:
            if method == "credit_card":
                clean_details = CreditCardValidator.validate(details)
            elif method == "bank_wire":
                clean_details = BankWireValidator.validate(details)
            elif method == "crypto":
                clean_details = CryptoValidator.validate(details)
            else:
                self.errors["payment_method"] = [f"Invalid payment method: '{method}'."]
                return False

            self.validated_data = {
                "payment_method": method,
                "amount": float(amount),
                "details": clean_details,
            }
            return len(self.errors) == 0

        except PaymentValidationError as pve:
            self.errors["details"] = pve.detail
            return False


# --- Verification Harness ---
if __name__ == "__main__":
    # 1. Valid Credit Card Payload
    card_payload = {
        "payment_method": "credit_card",
        "amount": 250.00,
        "details": {
            "card_number": "4111-2222-3333-4444",
            "exp_month": 12,
            "exp_year": 2028,
            "cvv": "921",
        },
    }
    s_card = PolymorphicPaymentSerializer(card_payload)
    assert s_card.is_valid() is True
    assert s_card.validated_data["details"]["card_last4"] == "4444"

    # 2. Invalid Credit Card (Expired Year)
    bad_card = {
        "payment_method": "credit_card",
        "amount": 100.0,
        "details": {"card_number": "4111222233334444", "exp_month": 5, "exp_year": 2022, "cvv": "123"},
    }
    s_bad = PolymorphicPaymentSerializer(bad_card)
    assert s_bad.is_valid() is False
    assert "exp_year" in s_bad.errors["details"]

    # 3. Valid Crypto Payload
    crypto_payload = {
        "payment_method": "crypto",
        "amount": 1500.0,
        "details": {"wallet_address": "0x71C84943E753123456789ABCDEF01234567890AB", "currency": "ETH"},
    }
    s_crypto = PolymorphicPaymentSerializer(crypto_payload)
    assert s_crypto.is_valid() is True
    assert s_crypto.validated_data["details"]["currency"] == "ETH"

    print("[SUCCESS] Challenge 3: Polymorphic Discriminator Payment Serializer verified.")
```
