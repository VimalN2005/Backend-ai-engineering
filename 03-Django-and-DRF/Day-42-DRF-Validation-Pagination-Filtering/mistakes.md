# Day 42: Common Antipatterns in DRF Validation, Pagination & Dynamic Filtering

This guide documents 7 critical production antipatterns encountered when engineering validation lifecycles, pagination algorithms, and dynamic query filtering pipelines in Django REST Framework. Each entry details the underlying failure mechanism, vulnerable code examples, and production-grade remediations.

---

## 1. Relying on `OFFSET` Pagination for High-Volume or Write-Heavy Datasets

### Failure Mechanism
Standard `PageNumberPagination` and `LimitOffsetPagination` compile to SQL `OFFSET N LIMIT M`. Relational databases (PostgreSQL, MySQL) do not jump directly to offset $N$; they must scan and discard $N$ index or heap entries while evaluating row visibility. 
- At $N = 1,000,000$, query latency spikes from 2 milliseconds to 15+ seconds, thrashing the shared buffer cache and starving worker processes.
- Furthermore, inserting new rows between client requests causes **Page Drift**, where users observe duplicate records or miss items entirely across consecutive pages.

### Vulnerable Code
```python
# settings.py or views.py
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50
}

# View querying a 20-million row ledger
class AuditLogListView(generics.ListAPIView):
    queryset = AuditLog.objects.all().order_by('-created_at')
    serializer_class = AuditLogSerializer
```

### Production-Grade Remediation
Switch high-volume, append-only, or time-series endpoints to Keyset (Cursor) pagination. Keyset pagination translates to `WHERE (created_at, id) < (cursor_timestamp, cursor_id)`, executing in constant $O(1)$ time via a composite B-tree index.

```python
from rest_framework.pagination import CursorPagination

class AuditLogCursorPagination(CursorPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100
    ordering = ('-created_at', '-id')  # Deterministic composite keyset

class AuditLogListView(generics.ListAPIView):
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    pagination_class = AuditLogCursorPagination
```

---

## 2. Uncapped `page_size` Allowing Memory Exhaustion DoS

### Failure Mechanism
When exposing `page_size_query_param = 'page_size'` without declaring `max_page_size`, an external client can request `GET /api/v1/orders/?page_size=2000000`. DRF attempts to serialize millions of Model instances into memory simultaneously, causing Python process Out-Of-Memory (OOM) termination and database connection pool saturation.

### Vulnerable Code
```python
class UnboundedPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    # VULNERABILITY: Missing max_page_size!
    # A client can request ?page_size=500000 and crash Gunicorn worker threads.
```

### Production-Grade Remediation
Always declare a strict `max_page_size` cap. Any client query exceeding this limit is clamped to the maximum allowed page size.

```python
class ProductionPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100  # Enforces strict upper bound
    
    def get_page_size(self, request):
        if self.page_size_query_param:
            try:
                requested = int(request.query_params[self.page_size_query_param])
                return min(max(1, requested), self.max_page_size)
            except (KeyError, ValueError):
                pass
        return self.page_size
```

---

## 3. Performing Cross-Field Validation Inside `validate_<field>()`

### Failure Mechanism
`validate_<field>(self, value)` receives only the primitive value of that individual field. Attempting to access other fields via `self.initial_data` bypasses data sanitization, type coercion, and individual field validation rules of the other fields. Furthermore, if the dependent field is missing or invalid, accessing `self.initial_data` causes `KeyError` or operates on unvalidated raw strings.

### Vulnerable Code
```python
class DateRangeSerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate_end_date(self, value):
        # BUG: Accessing raw unvalidated string from initial_data!
        raw_start = self.initial_data.get('start_date')
        if raw_start:
            # Requires duplicate parsing; ignores start_date's own field validation!
            start_date = datetime.strptime(raw_start, "%Y-%m-%d").date()
            if value <= start_date:
                raise serializers.ValidationError("end_date must be after start_date.")
        return value
```

### Production-Grade Remediation
Keep `validate_<field>()` strictly scoped to single-field constraints. Enforce all multi-field relationships and business invariants inside `validate(self, attrs)`, where all attributes have already been coerced into validated Python types.

```python
class DateRangeSerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')

        if start_date and end_date and end_date <= start_date:
            raise serializers.ValidationError({
                'end_date': ["The end date must be strictly after the start date."]
            })
        return attrs
```

---

## 4. Mutating `data` Instead of `attrs` (or Mutating Input Dictionaries)

### Failure Mechanism
In DRF, `self.initial_data` represents the immutable incoming HTTP request body. Modifying `self.initial_data` does not affect `serializer.validated_data`. Conversely, modifying dictionaries in place across nested serializers can introduce subtle data-corruption side effects across shared references.

### Vulnerable Code
```python
class UserProfileSerializer(serializers.Serializer):
    username = serializers.CharField()
    email = serializers.EmailField()

    def validate_username(self, value):
        # BUG: Mutating initial_data does nothing to validated_data!
        self.initial_data['username'] = value.lower().strip()
        return value  # Returns un-sanitized value!
```

### Production-Grade Remediation
Transform and return the value directly from the validation hook. For object-level transformations, mutate and return the `attrs` dictionary provided to `validate()`.

```python
class UserProfileSerializer(serializers.Serializer):
    username = serializers.CharField()
    email = serializers.EmailField()

    def validate_username(self, value: str) -> str:
        # Proper normalization: return sanitized value
        return value.lower().strip()

    def validate_email(self, value: str) -> str:
        return value.lower().strip()

    def validate(self, attrs: dict) -> dict:
        # Enrich or normalize attrs directly
        attrs['normalized_lookup'] = f"{attrs['username']}#{attrs['email']}"
        return attrs
```

---

## 5. Exposing Unconstrained `ordering_fields = '__all__'`

### Failure Mechanism
Setting `ordering_fields = '__all__'` permits clients to pass arbitrary model columns in the `?ordering=` parameter.
1. **Denial of Service**: If a client orders by an unindexed text column (`?ordering=notes`), the database must perform an expensive full table scan and an on-disk external sort.
2. **Schema Information Leakage**: Clients can discover internal column names and relationships via trial-and-error error responses.
3. **Database Crashes**: Concurrent queries sorting millions of unindexed rows trigger disk temp file exhaustion.

### Vulnerable Code
```python
class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    filter_backends = [filters.OrderingFilter]
    # CRITICAL VULNERABILITY: Unindexed column sorting
    ordering_fields = '__all__'
```

### Production-Grade Remediation
Explicitly whitelist only columns backed by B-tree indexes, and always provide a deterministic default tie-breaker (`id` or `uuid`).

```python
class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    filter_backends = [filters.OrderingFilter]
    
    # Whitelist indexed columns only
    ordering_fields = ['created_at', 'total_amount', 'id']
    ordering = ['-created_at', '-id']  # Deterministic tie-breaker
```

---

## 6. Applying Dynamic Filters Before Tenant Scoping

### Failure Mechanism
If dynamic filters are applied to the base model `QuerySet` without first constraining the query to the authenticated tenant, user-supplied filter parameters can trigger cross-tenant data leaks (Insecure Direct Object Reference / IDOR).
For example, if a custom filter backend builds queries based on `request.query_params.items()` and assigns `tenant_id = param`, an attacker can inject another company's identifier.

### Vulnerable Code
```python
class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    filter_backends = [DynamicFilterBackend]

    # BUG: get_queryset applies filters before scoping tenant!
    def get_queryset(self):
        qs = Document.objects.all()
        # If user provides ?tenant_id=victim, filter backend filters by victim!
        return qs
```

### Production-Grade Remediation
Enforce multi-tenant boundaries at the root `get_queryset()` level before any `filter_backends` execute. The filter backend can only further restrict an already-isolated QuerySet.

```python
class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'category']

    def get_queryset(self):
        # 1. Enforce strict isolation at the root QuerySet
        user = self.request.user
        return Document.objects.filter(tenant_id=user.tenant_id)
```

---

## 7. Non-Deterministic Ordering in Keyset (Cursor) Pagination

### Failure Mechanism
Keyset pagination relies on a strictly monotonic sequence. If the ordering column contains duplicate values (e.g., multiple transactions created in the exact same millisecond or sharing the same `status`) and lacks a unique secondary tie-breaker column, the cursor condition `WHERE created_at < cursor` will skip all subsequent records that share that identical timestamp. This causes silent record omissions and infinite pagination loops.

### Vulnerable Code
```python
class InsecureCursorPagination(CursorPagination):
    page_size = 25
    # BUG: created_at is NOT unique! Multiple rows can share the exact same timestamp.
    ordering = '-created_at'
```

### Production-Grade Remediation
Always include a unique column (such as the primary key `id` or a sequential UUID) as the final tie-breaking element in the keyset ordering tuple. Ensure a corresponding composite index exists in the database.

```python
class DeterministicCursorPagination(CursorPagination):
    page_size = 25
    # Compound ordering: Primary key tie-breaker guarantees strict monotonicity
    ordering = ('-created_at', '-id')

# Accompanying Django Model Index:
class LedgerTransaction(models.Model):
    created_at = models.DateTimeField(db_index=True)
    tenant_id = models.CharField(max_length=64, db_index=True)

    class Meta:
        indexes = [
            # Compound index perfectly matching the cursor ordering
            models.Index(fields=['tenant_id', '-created_at', '-id'], name='idx_ledger_tenant_cursor')
        ]
```
