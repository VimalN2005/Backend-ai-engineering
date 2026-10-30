# Day 42: 5-Minute Rapid Revision — DRF Validation, Pagination & Dynamic Filtering

---

## 1. DRF Validation Execution Lifecycle

```
[Raw HTTP Request Data (dict/list)]
                │
                ▼
      to_internal_value()
  (Strips read-only, coerces types into Python primitives)
                │
                ▼
       Field Validators
  (RegexValidator, MinValueValidator, EmailValidator)
                │
                ▼
      validate_<field>()
  (Custom field-level business logic, uppercase normalization)
                │
                ▼
         validate(attrs)
  (Cross-field business invariants, relational checks)
                │
                ▼
      Serializer Validators
  (UniqueTogetherValidator, composite DB uniqueness checks)
                │
                ▼
       [validated_data]
```

### Golden Rules of Serializer Validation:
- **`validate_<field>(self, value)`**: Strictly for single-field logic. Always return the value.
- **`validate(self, attrs)`**: For cross-field logic. Always return the `attrs` dict.
- **Model `clean()` is NOT invoked by DRF automatically!** Call `instance.clean()` inside `validate()` or `perform_create()` if model-level validation is needed.

---

## 2. Pagination Strategy Comparison Matrix

| Property | `PageNumberPagination` | `LimitOffsetPagination` | `CursorPagination` |
|:---|:---|:---|:---|
| **SQL Translation** | `OFFSET (P-1)*S LIMIT S` | `OFFSET O LIMIT L` | `WHERE (col, id) < (cursor_col, cursor_id) LIMIT S` |
| **Complexity** | **$O(N)$** | **$O(N)$** | **$O(1)$** |
| **Deep Page Latency** | Degrades linearly (seconds) | Degrades linearly (seconds) | **Constant (< 5 ms)** |
| **Page Drift (New Rows)** | Vulnerable (duplicates/skips) | Vulnerable (duplicates/skips) | **Immune (0 duplicates)** |
| **Jump to Page 42?** | Yes (`?page=42`) | Yes (`?offset=820`) | No (Sequential traversal only) |
| **Total Count (`count`)** | Executed via `COUNT(*)` | Executed via `COUNT(*)` | None (eliminates slow table count) |
| **Recommended Scope** | Admin UI (< 10k rows) | Legacy integrations | **High-scale APIs, feeds, ledger tables** |

---

## 3. Keyset (Cursor) Pagination Quick Reference

### Opaque Token Structure (Base64URL)
```python
# Encoding cursor tuple (timestamp, id):
import base64, json

payload = {"t": 1793294286.123, "i": 492108}
token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

# Decoding:
padded = token + "=" * (-len(token) % 4)
data = json.loads(base64.urlsafe_b64decode(padded).decode())
```

### Deterministic Tie-Breaker Rule:
- Always append a unique column (`id`) to the cursor ordering:
  ```python
  class ProductionCursorPagination(CursorPagination):
      page_size = 50
      max_page_size = 100
      ordering = ('-created_at', '-id')  # Must be composite and deterministic!
  ```
- **Composite Index Requirement**:
  ```python
  models.Index(fields=['-created_at', '-id'], name='idx_created_id_cursor')
  ```

---

## 4. Declarative Dynamic Filtering (`django-filter`) Cheat Sheet

```python
import django_filters
from rest_framework import filters
from django_filters.rest_framework import DjangoFilterBackend

class LedgerFilterSet(django_filters.FilterSet):
    min_amount = django_filters.NumberFilter(field_name='amount', lookup_expr='gte')
    max_amount = django_filters.NumberFilter(field_name='amount', lookup_expr='lte')
    status = django_filters.ChoiceFilter(choices=Ledger.Status.choices)
    currency = django_filters.CharFilter(lookup_expr='iexact')
    created_after = django_filters.IsoDateTimeFilter(field_name='created_at', lookup_expr='gte')
    memo = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = Ledger
        fields = ['status', 'currency']

class LedgerViewSet(viewsets.ModelViewSet):
    queryset = Ledger.objects.all()
    serializer_class = LedgerSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = LedgerFilterSet
    search_fields = ['^reference_code', 'memo']  # ^ means startswith (B-tree index friendly)
    ordering_fields = ['amount', 'created_at', 'id']
    ordering = ['-created_at', '-id']
```

---

## 5. SearchFilter Lookup Prefix Table

| Prefix | Lookup Expression | SQL Translation | Index Impact |
|:---|:---|:---|:---|
| `^` | `istartswith` | `ILIKE 'query%'` | **Index Friendly** (Uses standard B-Tree) |
| `=` | `iexact` | `ILIKE 'query'` | **Index Friendly** (Uses B-Tree / Hash) |
| `@` | `search` | Full-text query | Requires PostgreSQL tsvector / GIN index |
| `$` | `iregex` | Regex match | **Dangerous** (Can trigger ReDoS & table scan) |
| *(None)*| `icontains` | `ILIKE '%query%'` | **Full Table Scan** (Unless GIN trigram index) |

---

## 6. Production DRF Settings Template

```python
REST_FRAMEWORK = {
    # Default pagination: enforce strict upper limit
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    
    # Global filter backends
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    
    # Standardized validation & error formatting
    'EXCEPTION_HANDLER': 'apps.core.exceptions.rfc7807_exception_handler',
}
```

---

## 7. Ten-Second Interview Defense Checklist

1. *Why did your query time out on page 1,000?* -> `OFFSET 1000000` scans and discards 1,000,000 index pointers before reading data. Solution: Keyset (Cursor) pagination.
2. *Why did the user see duplicate records across pages?* -> Page Drift caused by concurrent inserts shifting the offset window. Solution: Anchor with cursor (`WHERE created_at < cursor`).
3. *Where does cross-field validation go in DRF?* -> Inside `validate(self, attrs)`, never inside `validate_<field>()`.
4. *How do you prevent `?ordering=` from killing PostgreSQL?* -> Declare an explicit `ordering_fields` whitelist matching indexed columns and enforce a deterministic tie-breaker (`id`).
