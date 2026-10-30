"""
Day 42: DRF Advanced Validation, Custom Pagination & Dynamic Filtering
========================================================================

Core Architecture:
1. DRF Validation Lifecycle:
   - Field-level validation (`validate_<field>`) for single-field domain constraints.
   - Object-level validation (`validate`) for cross-field invariants & business rules.
   - Context propagation (`self.context['request']`, tenant boundaries).
2. Advanced Pagination Systems:
   - PageNumberPagination: Offset-based chunking with strict `max_page_size` clamping.
   - CursorPagination: Keyset-based O(1) pagination using opaque Base64URL tokens,
     eliminating deep paging performance cliffs and Page Drift anomalies.
3. Declarative Dynamic Filtering & Ordering:
   - DynamicFilterBackend: Query parameter whitelisting, type coercion, and ORM lookup translation.
   - OrderingFilter: Whitelisted ASC/DESC sorting with deterministic tie-breaking.

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import base64
import json
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union


# ============================================================================
# SECTION 1: VALIDATION & EXCEPTION ARCHITECTURE
# ============================================================================

class ValidationError(Exception):
    """
    Standard DRF ValidationError container supporting both field-level error dicts
    and non-field error lists.
    
    WHY:
    DRF unifies error formats so client frontends can map errors directly to input
    form fields or display global banner alerts.
    """
    def __init__(self, detail: Union[str, List[str], Dict[str, Any]]):
        if isinstance(detail, str):
            self.detail = [detail]
        elif isinstance(detail, list):
            self.detail = detail
        elif isinstance(detail, dict):
            self.detail = detail
        else:
            self.detail = [str(detail)]
        super().__init__(self.detail)


class BaseField:
    """Base class for declarative serializer fields with primitive validation."""
    def __init__(
        self,
        required: bool = True,
        allow_null: bool = False,
        default: Any = None
    ) -> None:
        self.required = required
        self.allow_null = allow_null
        self.default = default
        self.field_name: str = ""

    def to_internal_value(self, data: Any) -> Any:
        """Coerce raw input into target Python primitive type."""
        raise NotImplementedError


class CharField(BaseField):
    def __init__(
        self,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.min_length = min_length
        self.max_length = max_length

    def to_internal_value(self, data: Any) -> str:
        if data is None:
            if self.allow_null:
                return None
            raise ValidationError("This field may not be null.")
        
        value = str(data).strip()
        if self.min_length is not None and len(value) < self.min_length:
            raise ValidationError(f"Ensure this field has at least {self.min_length} characters.")
        if self.max_length is not None and len(value) > self.max_length:
            raise ValidationError(f"Ensure this field has at most {self.max_length} characters.")
        return value


class DecimalField(BaseField):
    def __init__(
        self,
        max_digits: int = 12,
        decimal_places: int = 2,
        min_value: Optional[Decimal] = None,
        max_value: Optional[Decimal] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.max_digits = max_digits
        self.decimal_places = decimal_places
        self.min_value = min_value
        self.max_value = max_value

    def to_internal_value(self, data: Any) -> Decimal:
        if data is None:
            if self.allow_null:
                return None
            raise ValidationError("This field may not be null.")
        try:
            val = Decimal(str(data))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("A valid number or decimal is required.")
        
        if self.min_value is not None and val < self.min_value:
            raise ValidationError(f"Ensure this value is greater than or equal to {self.min_value}.")
        if self.max_value is not None and val > self.max_value:
            raise ValidationError(f"Ensure this value is less than or equal to {self.max_value}.")
        return round(val, self.decimal_places)


class DateTimeField(BaseField):
    def to_internal_value(self, data: Any) -> datetime:
        if data is None:
            if self.allow_null:
                return None
            raise ValidationError("This field may not be null.")
        if isinstance(data, datetime):
            return data if data.tzinfo else data.replace(tzinfo=timezone.utc)
        try:
            # Parse ISO 8601 string
            clean_str = str(data).replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            raise ValidationError("Expected a valid ISO 8601 datetime format (YYYY-MM-DDTHH:MM:SSZ).")


class ChoiceField(BaseField):
    def __init__(self, choices: List[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.choices = choices

    def to_internal_value(self, data: Any) -> str:
        if data is None and self.allow_null:
            return None
        val = str(data)
        if val not in self.choices:
            raise ValidationError(f"'{val}' is not a valid choice. Must be one of {self.choices}.")
        return val


class SerializerMeta(type):
    """Metaclass that collects declarative fields on serializer classes."""
    def __new__(mcs, name: str, bases: Tuple[type, ...], attrs: Dict[str, Any]) -> type:
        fields: Dict[str, BaseField] = {}
        for base in reversed(bases):
            if hasattr(base, "_declared_fields"):
                fields.update(base._declared_fields)
        
        for key, value in list(attrs.items()):
            if isinstance(value, BaseField):
                value.field_name = key
                fields[key] = value
        
        attrs["_declared_fields"] = fields
        return super().__new__(mcs, name, bases, attrs)


class Serializer(metaclass=SerializerMeta):
    """
    Standard DRF Serializer lifecycle simulator.
    
    Lifecycle:
    1. `to_internal_value()` converts primitives and records field errors.
    2. `validate_<field>()` runs per-field custom rules.
    3. `validate()` executes cross-field multi-attribute business logic.
    """
    _declared_fields: Dict[str, BaseField] = {}

    def __init__(self, data: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> None:
        self.initial_data = data or {}
        self.context = context or {}
        self._errors: Dict[str, List[str]] = {}
        self._validated_data: Dict[str, Any] = {}
        self._is_valid: Optional[bool] = None

    def is_valid(self, raise_exception: bool = False) -> bool:
        self._errors = {}
        self._validated_data = {}
        attrs: Dict[str, Any] = {}

        # Stage 1: Field-level coercion and primitive checks
        for field_name, field in self._declared_fields.items():
            if field_name not in self.initial_data:
                if field.required:
                    self._errors.setdefault(field_name, []).append("This field is required.")
                else:
                    attrs[field_name] = field.default
                continue

            raw_val = self.initial_data[field_name]
            try:
                coerced_val = field.to_internal_value(raw_val)
                # Stage 2: Custom validate_<field_name> hook
                validator_hook = getattr(self, f"validate_{field_name}", None)
                if callable(validator_hook):
                    coerced_val = validator_hook(coerced_val)
                attrs[field_name] = coerced_val
            except ValidationError as exc:
                err_detail = exc.detail
                if isinstance(err_detail, dict):
                    for k, v in err_detail.items():
                        self._errors.setdefault(k, []).extend(v if isinstance(v, list) else [v])
                elif isinstance(err_detail, list):
                    self._errors.setdefault(field_name, []).extend(err_detail)
                else:
                    self._errors.setdefault(field_name, []).append(str(err_detail))

        # Stage 3: Object-level cross-field validation (only if field validation succeeded)
        if not self._errors:
            try:
                attrs = self.validate(attrs)
            except ValidationError as exc:
                err_detail = exc.detail
                if isinstance(err_detail, dict):
                    for k, v in err_detail.items():
                        self._errors.setdefault(k, []).extend(v if isinstance(v, list) else [v])
                elif isinstance(err_detail, list):
                    self._errors.setdefault("non_field_errors", []).extend(err_detail)
                else:
                    self._errors.setdefault("non_field_errors", []).append(str(err_detail))

        if not self._errors:
            self._validated_data = attrs
            self._is_valid = True
        else:
            self._is_valid = False

        if not self._is_valid and raise_exception:
            raise ValidationError(self._errors)

        return self._is_valid

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Override in subclasses for cross-field invariants."""
        return attrs

    @property
    def validated_data(self) -> Dict[str, Any]:
        if not self._is_valid:
            raise RuntimeError("You must call .is_valid() before accessing .validated_data.")
        return self._validated_data

    @property
    def errors(self) -> Dict[str, List[str]]:
        return self._errors


# ============================================================================
# CONCRETE SERIALIZER: TRANSACTION TRANSFER DEMONSTRATING ALL VALIDATION STAGES
# ============================================================================

class FundTransferSerializer(Serializer):
    """
    Demonstrates:
    - Field-level validation: validate_amount, validate_currency
    - Object-level cross-field validation: validate() enforcing debit/credit balance
      and source != destination checks.
    """
    source_account_id = CharField(min_length=5, max_length=32)
    destination_account_id = CharField(min_length=5, max_length=32)
    amount = DecimalField(min_value=Decimal("0.01"), max_digits=12, decimal_places=2)
    currency = CharField(min_length=3, max_length=3)
    memo = CharField(max_length=140, required=False, default="")

    def validate_amount(self, value: Decimal) -> Decimal:
        """
        Field-level validation:
        Enforce max transaction limit ($50,000.00).
        WHY: Single-field constraints belong in validate_<field>() to isolate errors.
        """
        MAX_SINGLE_TRANSFER = Decimal("50000.00")
        if value > MAX_SINGLE_TRANSFER:
            raise ValidationError(f"Amount exceeds maximum transfer limit of {MAX_SINGLE_TRANSFER} per transaction.")
        return value

    def validate_currency(self, value: str) -> str:
        """Field-level normalization and validation."""
        val = value.upper()
        allowed = ["USD", "EUR", "GBP", "JPY"]
        if val not in allowed:
            raise ValidationError(f"'{val}' is not a valid currency. Must be one of {allowed}.")
        return val

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cross-field validation:
        1. Source account cannot equal destination account.
        2. Context validation: Tenant scoping verification.
        WHY: Multiple fields must be inspected simultaneously to enforce invariants.
        """
        source = attrs.get("source_account_id")
        destination = attrs.get("destination_account_id")
        
        if source and destination and source == destination:
            raise ValidationError({
                "destination_account_id": ["Destination account cannot be identical to source account."]
            })
        
        # Verify tenant scoping from request context
        request = self.context.get("request")
        if request and hasattr(request, "tenant_id"):
            # Ensure source account belongs to authenticated tenant
            if not source.startswith(request.tenant_id):
                raise ValidationError({
                    "source_account_id": [f"Source account does not belong to tenant '{request.tenant_id}'."]
                })
        
        return attrs


# ============================================================================
# SECTION 2: PAGINATION ENGINES (OFFSET VS KEYSET/CURSOR)
# ============================================================================

class PageNumberPagination:
    """
    Offset-based pagination (PageNumberPagination):
    Maps page numbers to SQL OFFSET: `OFFSET (page - 1) * page_size LIMIT page_size`.
    
    Defenses:
    - `max_page_size` bounds prevent Memory Exhaustion / DoS via `?page_size=1000000`.
    - Bounds checking handles out-of-range pages cleanly.
    """
    default_page_size: int = 10
    page_size_query_param: str = "page_size"
    max_page_size: int = 100
    page_query_param: str = "page"

    def paginate_queryset(self, queryset: List[Dict[str, Any]], query_params: Dict[str, str]) -> List[Dict[str, Any]]:
        self.count = len(queryset)
        
        # Determine page size with hard upper bound
        requested_size = query_params.get(self.page_size_query_param)
        if requested_size and requested_size.isdigit():
            self.page_size = min(int(requested_size), self.max_page_size)
        else:
            self.page_size = self.default_page_size

        self.total_pages = max(1, math.ceil(self.count / self.page_size))
        
        # Determine requested page
        requested_page = query_params.get(self.page_query_param, "1")
        try:
            self.page_number = int(requested_page)
        except ValueError:
            self.page_number = 1
        
        if self.page_number < 1:
            self.page_number = 1
        elif self.page_number > self.total_pages:
            return []  # Out of range

        offset = (self.page_number - 1) * self.page_size
        return queryset[offset : offset + self.page_size]

    def get_paginated_response(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        has_next = self.page_number < self.total_pages
        has_prev = self.page_number > 1
        return {
            "count": self.count,
            "total_pages": self.total_pages,
            "current_page": self.page_number,
            "page_size": self.page_size,
            "next": f"?page={self.page_number + 1}&page_size={self.page_size}" if has_next else None,
            "previous": f"?page={self.page_number - 1}&page_size={self.page_size}" if has_prev else None,
            "results": data,
        }


class CursorPagination:
    """
    Keyset / Cursor Pagination (CursorPagination):
    
    WHY:
    - O(1) Index Seeking: Instead of scanning and discarding OFFSET rows, queries seek directly
      via B-Tree index: `WHERE (created_at, id) < (cursor_timestamp, cursor_id)`.
    - Zero Page Drift: Inserting new records at the top of the table does NOT shift
      the relative position of records under the cursor.
    - Opaque Base64 Cursors: Hides underlying database columns and sorting keys from clients.
    """
    cursor_query_param: str = "cursor"
    page_size: int = 10
    max_page_size: int = 100
    ordering: Tuple[str, str] = ("created_at", "id")  # Timestamp + Primary Key tie-breaker

    def encode_cursor(self, item: Dict[str, Any]) -> str:
        """
        Encode cursor tuple (created_at ISO string, id) into URL-safe Base64.
        """
        raw = {
            "c": item["created_at"],
            "i": item["id"]
        }
        json_bytes = json.dumps(raw, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(json_bytes).decode("ascii").rstrip("=")

    def decode_cursor(self, token: str) -> Optional[Tuple[str, int]]:
        """
        Decode URL-safe Base64 token into (created_at, id).
        Returns None if malformed or tampered.
        """
        try:
            padded = token + "=" * (-len(token) % 4)
            raw_bytes = base64.urlsafe_b64decode(padded)
            payload = json.loads(raw_bytes.decode("utf-8"))
            return (payload["c"], payload["i"])
        except Exception:
            return None

    def paginate_queryset(
        self,
        queryset: List[Dict[str, Any]],
        query_params: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """
        Simulate index-seek filtering using the decoded cursor.
        Assumes queryset is pre-ordered by (-created_at, -id).
        """
        self.cursor_token = query_params.get(self.cursor_query_param)
        self.has_previous = bool(self.cursor_token)
        
        start_idx = 0
        if self.cursor_token:
            decoded = self.decode_cursor(self.cursor_token)
            if decoded is not None:
                cur_created, cur_id = decoded
                # Keyset filter seek: find first record strictly older than cursor
                # (created_at < cur_created) OR (created_at == cur_created AND id < cur_id)
                for idx, record in enumerate(queryset):
                    rec_created = record["created_at"]
                    rec_id = record["id"]
                    if (rec_created < cur_created) or (rec_created == cur_created and rec_id < cur_id):
                        start_idx = idx
                        break
                else:
                    # Cursor is beyond the end of the dataset
                    start_idx = len(queryset)

        # Slice 1 extra item to determine if a next page exists
        page_items = queryset[start_idx : start_idx + self.page_size + 1]
        self.has_next = len(page_items) > self.page_size
        self.results = page_items[: self.page_size]

        # Compute next cursor if more results exist
        if self.has_next and self.results:
            self.next_cursor = self.encode_cursor(self.results[-1])
        else:
            self.next_cursor = None

        return self.results

    def get_paginated_response(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "next": f"?cursor={self.next_cursor}" if self.next_cursor else None,
            "previous": self.cursor_token if self.has_previous else None,
            "page_size": len(data),
            "results": data,
        }


# ============================================================================
# SECTION 3: DYNAMIC FILTERING & ORDERING BACKEND
# ============================================================================

class FilterBackend:
    """
    Declarative Dynamic Filter Backend simulating DRF `DjangoFilterBackend`.
    
    Capabilities:
    - Strict whitelisting of permitted filter fields.
    - Type-safe lookup translation (__gte, __lte, __iexact, __in, __icontains).
    - Silently ignores undeclared query parameters to prevent query injection.
    """
    def __init__(self, filter_fields: Dict[str, List[str]]) -> None:
        """
        filter_fields maps model field names to permitted lookups:
        e.g., {'status': ['exact', 'in'], 'amount': ['gte', 'lte'], 'memo': ['icontains']}
        """
        self.filter_fields = filter_fields

    def filter_queryset(
        self,
        queryset: List[Dict[str, Any]],
        query_params: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        filtered = list(queryset)

        for param_key, param_value in query_params.items():
            # Parse field name and lookup expression
            if "__" in param_key:
                field, lookup = param_key.split("__", 1)
            else:
                field, lookup = param_key, "exact"

            # Whitelist verification: must be declared
            if field not in self.filter_fields:
                continue
            if lookup not in self.filter_fields[field]:
                continue

            filtered = [
                item for item in filtered
                if self._apply_lookup(item.get(field), lookup, param_value)
            ]

        return filtered

    def _apply_lookup(self, item_val: Any, lookup: str, target_val: str) -> bool:
        if item_val is None:
            return False

        if lookup == "exact":
            return str(item_val) == target_val
        elif lookup == "iexact":
            return str(item_val).lower() == target_val.lower()
        elif lookup == "icontains":
            return target_val.lower() in str(item_val).lower()
        elif lookup == "in":
            target_set = {x.strip() for x in target_val.split(",")}
            return str(item_val) in target_set
        elif lookup == "gte":
            try:
                return Decimal(str(item_val)) >= Decimal(target_val)
            except Exception:
                return str(item_val) >= target_val
        elif lookup == "lte":
            try:
                return Decimal(str(item_val)) <= Decimal(target_val)
            except Exception:
                return str(item_val) <= target_val
        return False


class OrderingFilter:
    """
    Ordering backend simulating DRF `OrderingFilter`.
    
    Security:
    - Only fields in `ordering_fields` whitelist can be sorted.
    - ASC prefix (`+` or none) vs DESC prefix (`-`).
    - Deterministic tie-breaking: Always appends primary key (`id`) to guarantee stable ordering.
    """
    def __init__(self, ordering_fields: List[str], default_ordering: List[str]) -> None:
        self.ordering_fields = set(ordering_fields)
        self.default_ordering = default_ordering

    def filter_queryset(
        self,
        queryset: List[Dict[str, Any]],
        query_params: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        ordering_param = query_params.get("ordering")
        sort_clauses: List[Tuple[str, bool]] = []  # (field_name, reverse_flag)

        if ordering_param:
            fields = [f.strip() for f in ordering_param.split(",") if f.strip()]
            for f in fields:
                is_desc = f.startswith("-")
                clean_field = f.lstrip("+-")
                if clean_field in self.ordering_fields:
                    sort_clauses.append((clean_field, is_desc))

        if not sort_clauses:
            for f in self.default_ordering:
                is_desc = f.startswith("-")
                clean_field = f.lstrip("+-")
                sort_clauses.append((clean_field, is_desc))

        # Enforce deterministic tie-breaker
        if not any(f == "id" for f, _ in sort_clauses):
            sort_clauses.append(("id", True))

        # Perform stable sort across clauses in reverse order (standard Python sort stability)
        sorted_qs = list(queryset)
        for field, is_desc in reversed(sort_clauses):
            sorted_qs.sort(
                key=lambda item: (item.get(field) is None, item.get(field)),
                reverse=is_desc
            )

        return sorted_qs


# ============================================================================
# ARCHITECTURAL VERIFICATION & TEST HARNESS
# ============================================================================

class MockRequest:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id


def run_tests() -> None:
    print("=" * 70)
    print(" Day 42: DRF Advanced Validation, Pagination & Filtering Tests")
    print("=" * 70)

    # ------------------------------------------------------------------------
    # [TEST 1] Serializer Field-Level & Cross-Field Validation
    # ------------------------------------------------------------------------
    print("\n[TEST 1] Serializer Validation Lifecycle...")
    
    # Valid payload
    request = MockRequest(tenant_id="tenant_alpha")
    valid_payload = {
        "source_account_id": "tenant_alpha_acc_001",
        "destination_account_id": "tenant_beta_acc_002",
        "amount": "1250.50",
        "currency": "usd",
        "memo": "Vendor invoice payment"
    }
    serializer = FundTransferSerializer(data=valid_payload, context={"request": request})
    assert serializer.is_valid(raise_exception=True) is True
    validated = serializer.validated_data
    assert validated["amount"] == Decimal("1250.50")
    assert validated["currency"] == "USD"  # Normalized
    print("  -> Valid payload coerced and normalized cleanly.")

    # Single-field limit failure (amount > 50,000)
    invalid_amount_payload = dict(valid_payload, amount="99999.00")
    s_invalid_amount = FundTransferSerializer(data=invalid_amount_payload)
    assert s_invalid_amount.is_valid() is False
    assert "amount" in s_invalid_amount.errors
    print(f"  -> Caught expected field-level error: {s_invalid_amount.errors['amount']}")

    # Cross-field invariant failure (source == destination)
    identical_acc_payload = dict(valid_payload, destination_account_id="tenant_alpha_acc_001")
    s_identical = FundTransferSerializer(data=identical_acc_payload, context={"request": request})
    assert s_identical.is_valid() is False
    assert "destination_account_id" in s_identical.errors
    print(f"  -> Caught expected cross-field error: {s_identical.errors['destination_account_id']}")

    # Tenant boundary failure
    alien_tenant_payload = dict(valid_payload, source_account_id="tenant_gamma_acc_999")
    s_alien = FundTransferSerializer(data=alien_tenant_payload, context={"request": request})
    assert s_alien.is_valid() is False
    assert "source_account_id" in s_alien.errors
    print(f"  -> Caught expected tenant boundary violation: {s_alien.errors['source_account_id']}")

    # ------------------------------------------------------------------------
    # [TEST 2] PageNumberPagination (Offset Bounds & Max Limit)
    # ------------------------------------------------------------------------
    print("\n[TEST 2] PageNumberPagination Mechanics...")
    sample_data = [{"id": i, "val": f"item_{i}"} for i in range(1, 101)]  # 100 items
    
    paginator = PageNumberPagination()
    paginator.default_page_size = 10
    paginator.max_page_size = 25

    # Page 1 fetch
    p1 = paginator.paginate_queryset(sample_data, {"page": "1", "page_size": "10"})
    res1 = paginator.get_paginated_response(p1)
    assert res1["count"] == 100
    assert res1["total_pages"] == 10
    assert len(res1["results"]) == 10
    assert res1["results"][0]["id"] == 1
    assert res1["results"][-1]["id"] == 10
    assert res1["next"] == "?page=2&page_size=10"
    print("  -> Page 1 verified with next link.")

    # Max page_size clamping defense: requested 500, clamped to max_page_size=25
    p_clamped = paginator.paginate_queryset(sample_data, {"page": "1", "page_size": "500"})
    res_clamped = paginator.get_paginated_response(p_clamped)
    assert len(res_clamped["results"]) == 25
    assert res_clamped["page_size"] == 25
    print("  -> DoS prevention verified: ?page_size=500 safely clamped to 25.")

    # ------------------------------------------------------------------------
    # [TEST 3] Keyset (Cursor) Pagination: Zero Page Drift Verification
    # ------------------------------------------------------------------------
    print("\n[TEST 3] CursorPagination & Page Drift Immunity...")
    # Chronological feed ordered by (-created_at, -id)
    feed_data = [
        {"id": 100 - i, "created_at": f"2026-10-30T10:{60 - i:02d}:00Z", "title": f"Post {100 - i}"}
        for i in range(50)
    ]

    cursor_paginator = CursorPagination()
    cursor_paginator.page_size = 5

    # Step 1: Client fetches Page 1
    page_1 = cursor_paginator.paginate_queryset(feed_data, {})
    resp_1 = cursor_paginator.get_paginated_response(page_1)
    seen_ids = [item["id"] for item in resp_1["results"]]
    next_cursor = cursor_paginator.next_cursor
    print(f"  -> Page 1 IDs: {seen_ids}, Next Cursor: {next_cursor[:20]}...")

    # Step 2: SIMULATE REAL-TIME WRITES (Concurrent insertions at the top)
    # 5 new posts arrive with newer timestamps
    new_inserts = [
        {"id": 200 + k, "created_at": f"2026-10-30T11:{k:02d}:00Z", "title": f"Breaking News {k}"}
        for k in range(5)
    ]
    # In offset pagination, these 5 insertions would cause Page 2 to re-read items from Page 1!
    updated_feed = new_inserts + feed_data

    # Step 3: Client fetches Page 2 using the opaque cursor from Page 1
    page_2 = cursor_paginator.paginate_queryset(updated_feed, {"cursor": next_cursor})
    resp_2 = cursor_paginator.get_paginated_response(page_2)
    page_2_ids = [item["id"] for item in resp_2["results"]]
    print(f"  -> Page 2 IDs after concurrent insertions: {page_2_ids}")

    # Verify zero duplication between Page 1 and Page 2
    intersection = set(seen_ids).intersection(set(page_2_ids))
    assert len(intersection) == 0, f"Page Drift detected! Duplicate IDs: {intersection}"
    # Verify sequential integrity: next ID must immediately follow the last ID of Page 1
    assert page_2_ids[0] == feed_data[5]["id"]
    print("  -> Page Drift test PASSED: 0 duplicate records despite concurrent insertions.")

    # ------------------------------------------------------------------------
    # [TEST 4] Dynamic Filter Backend & Ordering Integration
    # ------------------------------------------------------------------------
    print("\n[TEST 4] Dynamic Filtering & Ordering Pipeline...")
    transactions = [
        {"id": 1, "status": "COMPLETED", "amount": Decimal("100.00"), "currency": "USD", "memo": "Coffee"},
        {"id": 2, "status": "PENDING", "amount": Decimal("500.00"), "currency": "EUR", "memo": "Office supplies"},
        {"id": 3, "status": "COMPLETED", "amount": Decimal("1200.00"), "currency": "USD", "memo": "Server hosting"},
        {"id": 4, "status": "FAILED", "amount": Decimal("50.00"), "currency": "USD", "memo": "Microtransaction"},
        {"id": 5, "status": "COMPLETED", "amount": Decimal("850.00"), "currency": "EUR", "memo": "Domain registration"},
    ]

    filter_backend = FilterBackend(filter_fields={
        "status": ["exact", "in"],
        "amount": ["gte", "lte"],
        "currency": ["exact", "iexact"],
        "memo": ["icontains"]
    })

    ordering_backend = OrderingFilter(
        ordering_fields=["amount", "id"],
        default_ordering=["-amount"]
    )

    # Query: COMPLETED status, amount >= 500, ordered by amount ASC
    query_params = {
        "status": "COMPLETED",
        "amount__gte": "500.00",
        "ordering": "+amount"
    }

    filtered_tx = filter_backend.filter_queryset(transactions, query_params)
    ordered_tx = ordering_backend.filter_queryset(filtered_tx, query_params)

    assert len(ordered_tx) == 2
    assert ordered_tx[0]["id"] == 5  # 850.00
    assert ordered_tx[1]["id"] == 3  # 1200.00
    print(f"  -> Pipeline result: {[t['memo'] for t in ordered_tx]}")
    print("  -> Dynamic filter + ordering verified successfully.")

    # Unknown parameter injection protection
    injected_params = {
        "status": "COMPLETED",
        "unwhitelisted_field": "exploit",
        "amount__evil_lookup": "100"
    }
    safe_filtered = filter_backend.filter_queryset(transactions, injected_params)
    assert len(safe_filtered) == 3  # Only status=COMPLETED is evaluated
    print("  -> Injection protection verified: undeclared params safely ignored.")

    print("\nAll Day 42 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
