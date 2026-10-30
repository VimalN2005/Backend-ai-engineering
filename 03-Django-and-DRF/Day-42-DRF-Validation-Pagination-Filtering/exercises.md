# Day 42: Advanced Exercises — DRF Validation, Pagination & Dynamic Filtering

This workbook provides 3 production-grade backend engineering challenges testing serializer validation mechanics, keyset cursor pagination algorithms, and declarative dynamic filtering pipelines. Complete reference solutions with verification assertions are included.

---

## Challenge 1: Bi-Directional Keyset (Cursor) Paginator with Opaque Tokens

### Context & Requirements
In high-throughput event logging systems, consumers need to traverse paginated results forward and backward without experiencing $O(N)$ database degradation or page drift.
Implement a `BidirectionalCursorPaginator` in pure Python standard library that:
1. Accepts a list of pre-sorted dictionary records with keys `id` (int) and `timestamp` (ISO-8601 string).
2. Generates opaque, URL-safe Base64 cursors encoding navigation direction (`"next"` or `"prev"`), target `timestamp`, and `id`.
3. Supports bidirectional queries via `?cursor=<opaque_token>`.
4. Returns next and previous cursor links in standard pagination envelope format.
5. Guarantees deterministic traversal across identical timestamps using `id` as tie-breaker.

---

### Reference Solution

```python
import base64
import json
from typing import Any, Dict, List, Optional, Tuple


class BidirectionalCursorPaginator:
    def __init__(self, page_size: int = 10, max_page_size: int = 100) -> None:
        self.page_size = min(page_size, max_page_size)
        self.max_page_size = max_page_size

    def encode_cursor(self, direction: str, timestamp: str, item_id: int) -> str:
        payload = {"d": direction, "t": timestamp, "i": item_id}
        raw_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")

    def decode_cursor(self, token: str) -> Optional[Tuple[str, str, int]]:
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            return (data["d"], data["t"], int(data["i"]))
        except Exception:
            return None

    def paginate_queryset(
        self,
        queryset: List[Dict[str, Any]],
        cursor_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Assumes queryset is sorted in descending order: (-timestamp, -id).
        """
        direction = "next"
        cur_t, cur_i = None, None

        if cursor_token:
            decoded = self.decode_cursor(cursor_token)
            if decoded:
                direction, cur_t, cur_i = decoded

        # Keyset seek
        if cur_t is not None and cur_i is not None:
            if direction == "next":
                # Seek records older than cursor
                seek_idx = 0
                for idx, r in enumerate(queryset):
                    if (r["timestamp"] < cur_t) or (r["timestamp"] == cur_t and r["id"] < cur_i):
                        seek_idx = idx
                        break
                else:
                    seek_idx = len(queryset)
                page = queryset[seek_idx : seek_idx + self.page_size]
            else:  # prev
                # Seek records newer than cursor
                seek_idx = len(queryset)
                for idx in range(len(queryset) - 1, -1, -1):
                    r = queryset[idx]
                    if (r["timestamp"] > cur_t) or (r["timestamp"] == cur_t and r["id"] > cur_i):
                        seek_idx = idx + 1
                        break
                else:
                    seek_idx = 0
                start = max(0, seek_idx - self.page_size)
                page = queryset[start:seek_idx]
        else:
            page = queryset[: self.page_size]

        # Generate cursors
        next_cursor = None
        prev_cursor = None

        if page:
            first_item = page[0]
            last_item = page[-1]

            # Check if there is an item after last_item
            last_idx = queryset.index(last_item)
            if last_idx + 1 < len(queryset):
                next_cursor = self.encode_cursor("next", last_item["timestamp"], last_item["id"])

            # Check if there is an item before first_item
            first_idx = queryset.index(first_item)
            if first_idx > 0:
                prev_cursor = self.encode_cursor("prev", first_item["timestamp"], first_item["id"])

        return {
            "results": page,
            "next": f"?cursor={next_cursor}" if next_cursor else None,
            "previous": f"?cursor={prev_cursor}" if prev_cursor else None,
            "page_size": len(page)
        }


# --- Verification Harness ---
if __name__ == "__main__":
    records = [
        {"id": 100 - i, "timestamp": f"2026-10-30T10:{50 - i:02d}:00Z"}
        for i in range(30)
    ]
    paginator = BidirectionalCursorPaginator(page_size=5)

    # Page 1
    res1 = paginator.paginate_queryset(records)
    assert len(res1["results"]) == 5
    assert res1["results"][0]["id"] == 100
    assert res1["previous"] is None
    assert res1["next"] is not None

    # Page 2 (forward)
    next_token = res1["next"].split("=")[1]
    res2 = paginator.paginate_queryset(records, cursor_token=next_token)
    assert len(res2["results"]) == 5
    assert res2["results"][0]["id"] == 95
    assert res2["previous"] is not None

    # Page 1 (reverse navigation via prev cursor)
    prev_token = res2["previous"].split("=")[1]
    res_back = paginator.paginate_queryset(records, cursor_token=prev_token)
    assert len(res_back["results"]) == 5
    assert res_back["results"][0]["id"] == 100
    print("[CHALLENGE 1 PASSED] Bidirectional Keyset Pagination verified.")
```

---

## Challenge 2: Declarative Dynamic Filter Backend with Strict Type Coercion

### Context & Requirements
Construct an enterprise filter engine `DeclarativeFilterBackend` that:
1. Validates query parameters against an explicit whitelist of allowed model fields and permitted lookup expressions (`exact`, `gte`, `lte`, `in`, `icontains`).
2. Silently drops unwhitelisted parameters to protect against SQL/NoSQL injection and schema snooping.
3. Performs type-safe coercion (integers, floats, dates, and booleans) before executing comparison logic.
4. Returns filtered lists adhering to multiple simultaneous filter conditions.

---

### Reference Solution

```python
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Tuple


class DeclarativeFilterBackend:
    def __init__(self, filter_spec: Dict[str, Tuple[type, List[str]]]) -> None:
        """
        filter_spec maps field_name to (target_type, allowed_lookups)
        Example:
        {
            'amount': (Decimal, ['gte', 'lte', 'exact']),
            'status': (str, ['exact', 'in']),
            'is_active': (bool, ['exact'])
        }
        """
        self.filter_spec = filter_spec

    def filter_queryset(
        self,
        dataset: List[Dict[str, Any]],
        query_params: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        filtered = list(dataset)

        for raw_key, raw_val in query_params.items():
            if "__" in raw_key:
                field, lookup = raw_key.split("__", 1)
            else:
                field, lookup = raw_key, "exact"

            if field not in self.filter_spec:
                continue

            field_type, allowed_lookups = self.filter_spec[field]
            if lookup not in allowed_lookups:
                continue

            filtered = [
                item for item in filtered
                if self._evaluate_condition(item.get(field), field_type, lookup, raw_val)
            ]

        return filtered

    def _coerce_type(self, value: str, target_type: type) -> Any:
        if target_type == bool:
            return value.strip().lower() in ("true", "1", "yes")
        elif target_type == Decimal:
            return Decimal(value.strip())
        elif target_type == int:
            return int(value.strip())
        elif target_type == datetime:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return str(value)

    def _evaluate_condition(
        self,
        record_val: Any,
        field_type: type,
        lookup: str,
        raw_target: str
    ) -> bool:
        if record_val is None:
            return False

        try:
            if lookup == "in":
                targets = {self._coerce_type(v, field_type) for v in raw_target.split(",")}
                return record_val in targets
            
            target = self._coerce_type(raw_target, field_type)
            
            if lookup == "exact":
                return record_val == target
            elif lookup == "gte":
                return record_val >= target
            elif lookup == "lte":
                return record_val <= target
            elif lookup == "icontains":
                return str(target).lower() in str(record_val).lower()
        except Exception:
            return False

        return False


# --- Verification Harness ---
if __name__ == "__main__":
    records = [
        {"id": 1, "amount": Decimal("100.50"), "status": "PENDING", "is_active": True},
        {"id": 2, "amount": Decimal("250.00"), "status": "COMPLETED", "is_active": True},
        {"id": 3, "amount": Decimal("50.00"), "status": "FAILED", "is_active": False},
        {"id": 4, "amount": Decimal("1200.00"), "status": "COMPLETED", "is_active": False},
    ]

    backend = DeclarativeFilterBackend({
        "amount": (Decimal, ["gte", "lte", "exact"]),
        "status": (str, ["exact", "in"]),
        "is_active": (bool, ["exact"])
    })

    # Test combined filtering
    res = backend.filter_queryset(records, {
        "status__in": "COMPLETED,PENDING",
        "amount__gte": "150.00",
        "is_active": "true",
        "unwhitelisted_param": "drop_me"
    })

    assert len(res) == 1
    assert res[0]["id"] == 2
    print("[CHALLENGE 2 PASSED] Declarative Filter Backend verified.")
```

---

## Challenge 3: Advanced Financial Transaction Serializer with Cross-Field & Idempotency Rules

### Context & Requirements
Build a `PaymentReconciliationSerializer` modeling high-risk financial transfers:
1. **Field Validation**:
   - `amount`: Must be greater than 0.00 and less than or equal to 100,000.00 USD.
   - `currency`: Must be in `['USD', 'EUR', 'GBP']`.
   - `fee`: Cannot be negative; cannot exceed 10% of `amount`.
2. **Cross-Field Validation**:
   - `net_payout`: Must strictly equal `amount - fee`.
   - `idempotency_key`: Required UUIDv4 string. Must check an in-memory idempotency cache (`self.context['idempotency_cache']`). If the key already exists with identical payload hash, return cached response; if key exists with different payload, raise a validation error (`"Idempotency key reused with different payload."`).

---

### Reference Solution

```python
import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any, Dict


class SerializerValidationError(Exception):
    def __init__(self, errors: Dict[str, Any]):
        self.errors = errors
        super().__init__(errors)


class PaymentReconciliationSerializer:
    def __init__(self, data: Dict[str, Any], context: Dict[str, Any]) -> None:
        self.initial_data = data
        self.context = context
        self.validated_data: Dict[str, Any] = {}
        self.errors: Dict[str, Any] = {}

    def is_valid(self, raise_exception: bool = False) -> bool:
        self.errors = {}
        attrs: Dict[str, Any] = {}

        # 1. Field validation: amount
        raw_amt = self.initial_data.get("amount")
        try:
            amt = Decimal(str(raw_amt))
            if amt <= Decimal("0.00") or amt > Decimal("100000.00"):
                self.errors["amount"] = ["Amount must be between 0.01 and 100,000.00."]
            else:
                attrs["amount"] = amt
        except Exception:
            self.errors["amount"] = ["Valid decimal amount required."]

        # 2. Field validation: currency
        curr = str(self.initial_data.get("currency", "")).upper()
        if curr not in ("USD", "EUR", "GBP"):
            self.errors["currency"] = ["Currency must be USD, EUR, or GBP."]
        else:
            attrs["currency"] = curr

        # 3. Field validation: fee
        raw_fee = self.initial_data.get("fee", "0.00")
        try:
            fee = Decimal(str(raw_fee))
            if fee < Decimal("0.00"):
                self.errors["fee"] = ["Fee cannot be negative."]
            elif "amount" in attrs and fee > (attrs["amount"] * Decimal("0.10")):
                self.errors["fee"] = ["Fee cannot exceed 10% of total transfer amount."]
            else:
                attrs["fee"] = fee
        except Exception:
            self.errors["fee"] = ["Valid decimal fee required."]

        # 4. Field validation: net_payout
        raw_net = self.initial_data.get("net_payout")
        try:
            attrs["net_payout"] = Decimal(str(raw_net))
        except Exception:
            self.errors["net_payout"] = ["Valid decimal net_payout required."]

        # 5. Field validation: idempotency_key
        idem_key = str(self.initial_data.get("idempotency_key", ""))
        try:
            uuid.UUID(idem_key, version=4)
            attrs["idempotency_key"] = idem_key
        except ValueError:
            self.errors["idempotency_key"] = ["Valid UUIDv4 idempotency key required."]

        # Cross-field validation (only if primitive fields passed)
        if not self.errors:
            # Check 1: net_payout invariant (amount - fee == net_payout)
            expected_net = attrs["amount"] - attrs["fee"]
            if attrs["net_payout"] != expected_net:
                self.errors["net_payout"] = [
                    f"net_payout ({attrs['net_payout']}) does not equal amount - fee ({expected_net})."
                ]

            # Check 2: Idempotency cache verification
            cache = self.context.get("idempotency_cache", {})
            payload_signature = hashlib.sha256(
                f"{attrs['amount']}|{attrs['currency']}|{attrs['fee']}".encode("utf-8")
            ).hexdigest()

            if attrs["idempotency_key"] in cache:
                cached_sig = cache[attrs["idempotency_key"]]
                if cached_sig != payload_signature:
                    self.errors["idempotency_key"] = [
                        "Idempotency key conflict: key was previously used with different transaction parameters."
                    ]

        if not self.errors:
            self.validated_data = attrs
            return True

        if raise_exception:
            raise SerializerValidationError(self.errors)
        return False


# --- Verification Harness ---
if __name__ == "__main__":
    idem_cache = {}
    valid_key = str(uuid.uuid4())

    context = {"idempotency_cache": idem_cache}
    payload = {
        "amount": "1000.00",
        "currency": "usd",
        "fee": "25.00",
        "net_payout": "975.00",
        "idempotency_key": valid_key
    }

    # 1. Valid transfer
    s1 = PaymentReconciliationSerializer(payload, context=context)
    assert s1.is_valid() is True
    # Record in cache
    idem_cache[valid_key] = hashlib.sha256(b"1000.00|USD|25.00").hexdigest()

    # 2. Invariant violation: fee > 10%
    p_high_fee = dict(payload, fee="150.00", net_payout="850.00", idempotency_key=str(uuid.uuid4()))
    s2 = PaymentReconciliationSerializer(p_high_fee, context=context)
    assert s2.is_valid() is False
    assert "fee" in s2.errors

    # 3. Invariant violation: net_payout math mismatch
    p_bad_math = dict(payload, net_payout="999.00", idempotency_key=str(uuid.uuid4()))
    s3 = PaymentReconciliationSerializer(p_bad_math, context=context)
    assert s3.is_valid() is False
    assert "net_payout" in s3.errors

    # 4. Idempotency collision with altered parameters
    p_tampered = dict(payload, amount="5000.00", net_payout="4975.00", idempotency_key=valid_key)
    s4 = PaymentReconciliationSerializer(p_tampered, context=context)
    assert s4.is_valid() is False
    assert "idempotency_key" in s4.errors
    print("[CHALLENGE 3 PASSED] Payment Reconciliation Serializer verified.")
```
