# Day 45: Common Antipatterns in Multi-Tenant SaaS Architecture

This guide examines 7 critical production antipatterns encountered when engineering multi-tenant SaaS backends in Django and Django REST Framework. Each entry details the underlying architectural failure mechanism, illustrates vulnerable code, and provides a battle-tested remediation.

---

## 1. Trusting Client-Supplied `tenant_id` Without JWT Claim Verification

### Failure Mechanism
In multi-tenant APIs, clients frequently pass `tenant_id` via request headers (`X-Tenant-ID`), URL path parameters (`/tenants/acme/invoices/`), or JSON request bodies. 
If the application uses this client-supplied value to scope database queries without cryptographically verifying that the authenticated user actually belongs to that tenant:
- An attacker logged in as a legitimate user of `tenant_evil` sends `X-Tenant-ID: tenant_victim`.
- The application scopes the QuerySet to `tenant_victim`.
- **Result**: Catastrophic Insecure Direct Object Reference (IDOR). The attacker reads, modifies, or deletes the victim company's private customer data, invoices, and credentials.

### Vulnerable Code
```python
# middleware.py or views.py
class UnsafeTenantMiddleware:
    def __call__(self, request):
        # CRITICAL VULNERABILITY: Blindly trusting header from untrusted client!
        tenant_id = request.headers.get("X-Tenant-ID")
        request.tenant_id = tenant_id  # Attacker can pass ANY company's tenant_id!
        return self.get_response(request)

class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer
    
    def get_queryset(self):
        # Scopes to whatever header the client passed!
        return Invoice.objects.filter(tenant_id=self.request.tenant_id)
```

### Production-Grade Remediation
Extract and verify tenant identity directly from the cryptographically signed JWT access token. If a client also provides an `X-Tenant-ID` header, verify that it matches the token claim; otherwise, reject the request with `403 Forbidden` / `401 Unauthorized`.

```python
class SecureTenantAuthentication(JWTAuthentication):
    def authenticate(self, request):
        header = self.get_header(request)
        if header is None:
            return None
        raw_token = self.get_raw_token(header)
        validated_token = self.get_validated_token(raw_token)
        user = self.get_user(validated_token)

        token_tenant_id = validated_token.get("tenant_id")
        header_tenant_id = request.headers.get("X-Tenant-ID")

        # Cryptographic Verification: Header must match token claim!
        if header_tenant_id and header_tenant_id != token_tenant_id:
            raise exceptions.AuthenticationFailed("Cross-tenant tampering detected: header does not match token claim.")

        request.tenant_id = token_tenant_id
        TenantContext.set_current_tenant(token_tenant_id)
        return (user, validated_token)
```

---

## 2. Omitting `tenant_id` as the Leading Column in Composite Indexes

### Failure Mechanism
In a shared-database multi-tenant table with 50,000,000 rows across 5,000 tenants, queries almost universally include `WHERE tenant_id = 'acme'`.
If an engineer indexes individual columns like `created_at` or `status` (`db_index=True`) or creates a composite index with `tenant_id` at the end (`INDEX idx (created_at, tenant_id)`):
1. The B-Tree index is sorted primarily by `created_at`.
2. PostgreSQL cannot seek directly to Tenant Acme's partition.
3. The query planner must scan millions of index pointers across all tenants before filtering out rows where `tenant_id != 'acme'`, or abandons the index completely in favor of a slow sequential table scan (`Seq Scan`).

### Vulnerable Code
```python
class Invoice(models.Model):
    tenant_id = models.CharField(max_length=64, db_index=True)  # Isolated index
    created_at = models.DateTimeField(db_index=True)            # Isolated index
    status = models.CharField(max_length=20)

    class Meta:
        # BUG: tenant_id is NOT the leading column!
        indexes = [
            models.Index(fields=['created_at', 'tenant_id'], name='idx_created_tenant')
        ]
```

### Production-Grade Remediation
Apply the **Leading Tenant Column Invariant**: `tenant_id` must always be the first column in any composite index on multi-tenant tables.

```python
class Invoice(models.Model):
    tenant_id = models.CharField(max_length=64)
    created_at = models.DateTimeField()
    status = models.CharField(max_length=20)

    class Meta:
        indexes = [
            # Leading tenant_id allows O(log N) partition seeks
            models.Index(fields=['tenant_id', 'created_at', 'id'], name='idx_tenant_created_id'),
            models.Index(fields=['tenant_id', 'status'], name='idx_tenant_status'),
        ]
```

---

## 3. Sharing Redis Cache Keys Without Tenant Namespacing

### Failure Mechanism
When caching entities in Redis, developers often use simple entity keys like `product:{id}` or `user:{id}`.
In multi-tenant SaaS:
1. Tenant Acme and Tenant Beta may both have an internal product or invoice with `id = 101`.
2. Tenant Acme updates Product 101, populating `product:101` in Redis with confidential wholesale pricing.
3. Tenant Beta requests Product 101. The application checks Redis, hits `product:101`, and returns Tenant Acme's data to Tenant Beta!
4. **Result**: Severe cross-tenant data leak violating SOC2, HIPAA, and GDPR compliance.

### Vulnerable Code
```python
def get_invoice_details(invoice_id: int):
    # BUG: Cache key is global across all tenants!
    cache_key = f"invoice:{invoice_id}"
    data = cache.get(cache_key)
    if data is None:
        data = Invoice.objects.get(id=invoice_id).to_dict()
        cache.set(cache_key, data, timeout=300)
    return data
```

### Production-Grade Remediation
Every single cache key must be explicitly prefixed with the active tenant's identifier:
$$\text{key} = \text{"tenant:"} + \text{tenant\_id} + \text{":entity:"} + \text{entity\_id}$$

```python
def get_invoice_details(tenant_id: str, invoice_id: int):
    # Tenant-namespaced cache key guarantees absolute isolation
    cache_key = f"tenant:{tenant_id}:invoice:{invoice_id}"
    data = cache.get(cache_key)
    if data is None:
        data = Invoice.objects.get(tenant_id=tenant_id, id=invoice_id).to_dict()
        cache.set(cache_key, data, timeout=300)
    return data
```

---

## 4. Declaring Static `queryset = Model.objects.all()` on DRF ViewSets

### Failure Mechanism
DRF's `ModelViewSet` allows declaring a class-level `queryset = Model.objects.all()`.
If an engineer relies on this static attribute and forgets to override `get_queryset()`:
- Calling `GET /api/v1/invoices/` without query filtering returns all invoices in the entire database across all corporate customers.
- Detail lookups (`GET /api/v1/invoices/992/`) execute `Model.objects.all().get(pk=992)`, allowing users to view any competitor's invoice simply by guessing the ID.

### Vulnerable Code
```python
class CustomerViewSet(viewsets.ModelViewSet):
    # CRITICAL VULNERABILITY: Class-level queryset spans entire database!
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    # Missing get_queryset override!
```

### Production-Grade Remediation
Never use a static unscoped `queryset` on multi-tenant ViewSets. Inherit from a `TenantScopedViewSetMixin` that strictly overrides `get_queryset()` to filter by `request.user.tenant_id`:

```python
class TenantScopedViewSetMixin:
    def get_queryset(self):
        # Enforce strict isolation on every request
        return self.queryset.filter(tenant_id=self.request.user.tenant_id)

    def perform_create(self, serializer):
        # Automatically inject authenticated tenant into created records
        serializer.save(tenant_id=self.request.user.tenant_id)

class CustomerViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
```

---

## 5. Executing Asynchronous Tasks (Celery) Without Explicit Tenant Context

### Failure Mechanism
Asynchronous background tasks (Celery, Dramatiq) run in separate worker processes outside the HTTP request-response cycle. They have **no HTTP request object, no headers, and no JWT authentication tokens**.
If a developer passes only an object ID to a task (`process_invoice_task.delay(invoice_id)`), and the task queries `Invoice.objects.get(id=invoice_id)`:
- The task runs without tenant scoping.
- If tenant context is required for database schema selection, custom encryption keys, or multi-tenant database routing, the worker executes against the default schema or fails silently.

### Vulnerable Code
```python
# tasks.py
@shared_task
def send_monthly_statements(customer_id: int):
    # BUG: No tenant context! Queries without tenant isolation!
    customer = Customer.objects.get(id=customer_id)
    generate_statement(customer)
```

### Production-Grade Remediation
Always explicitly pass `tenant_id` in Celery task payloads, and initialize the `TenantContext` inside the task body before executing any domain logic:

```python
# tasks.py
@shared_task
def send_monthly_statements(tenant_id: str, customer_id: int):
    # Explicitly establish tenant context inside background worker thread
    token = TenantContext.set_current_tenant(tenant_id)
    try:
        customer = Customer.objects.get(tenant_id=tenant_id, id=customer_id)
        generate_statement(customer)
    finally:
        TenantContext.reset(token)
```

---

## 6. Defining Global Unique Constraints Without `tenant_id`

### Failure Mechanism
In SaaS platforms, entities frequently possess human-readable identifiers such as `slug`, `invoice_number`, `username`, or `sku` (e.g. `invoice_number = "INV-001"` or `product_slug = "starter-plan"`).
If a model declares `slug = models.SlugField(unique=True)`:
- Tenant Acme creates a product with slug `"pro-plan"`.
- Tenant Beta tries to create a product with slug `"pro-plan"`.
- The database rejects Tenant Beta's product with a `UniqueConstraintViolation`!
- **Consequence**: One tenant's naming choices block all other tenants on the platform, leaking competitor information through naming collision errors.

### Vulnerable Code
```python
class Product(models.Model):
    tenant_id = models.CharField(max_length=64)
    # BUG: Global unique constraint blocks other tenants from using the same SKU!
    sku = models.CharField(max_length=32, unique=True)
```

### Production-Grade Remediation
Never enforce global uniqueness on business entity slugs or numbers. Declare composite unique constraints scoped to `tenant_id`:

```python
class Product(models.Model):
    tenant_id = models.CharField(max_length=64)
    sku = models.CharField(max_length=32)

    class Meta:
        constraints = [
            # Scoped uniqueness: SKU must be unique ONLY within each tenant
            models.UniqueConstraint(
                fields=['tenant_id', 'sku'],
                name='unique_tenant_sku'
            )
        ]
```

---

## 7. Running Large Multi-Tenant Aggregations in Synchronous Request Threads

### Failure Mechanism
Executing full-table aggregations (e.g. generating quarterly tax reports or calculating platform-wide billing) synchronously inside an HTTP request thread locks database rows, saturates Gunicorn workers, and causes HTTP 504 Gateway Timeouts.
Under high traffic, a single tenant generating a massive export can exhaust the shared database connection pool (`max_connections`), degrading API responsiveness for all other tenants (**The Noisy Neighbor Problem**).

### Vulnerable Code
```python
class ReportView(APIView):
    def get(self, request):
        # BUG: Scanning 5,000,000 rows synchronously in HTTP worker!
        # Blocks thread for 45 seconds, starving other tenants of DB connections.
        total_revenue = Invoice.objects.filter(tenant_id=request.tenant_id).aggregate(Sum('amount'))
        return Response({"total": total_revenue})
```

### Production-Grade Remediation
1. Enforce query timeouts and pagination on all read endpoints.
2. Offload heavy reports to background workers using the **Asynchronous Export Pattern**:
   - The API generates an export job (`POST /api/v1/reports/`) and returns `202 Accepted` with a job ID.
   - A dedicated background worker executes the query and uploads the result to S3.
   - The client polls or receives a webhook when the export is complete.
3. Configure PostgreSQL connection limits and rate-limit each tenant using Redis Token Bucket limiters.
