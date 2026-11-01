# Day 45: Capstone Project 1 — Enterprise Multi-Tenant DRF SaaS Backend

## 1. Executive Project Specification & Capstone Vision

The culmination of **Phase 3 (Django & DRF Production Architecture)** synthesizes all core backend engineering disciplines mastered across Days 31–44 into a production-grade, enterprise-ready **B2B Multi-Tenant SaaS Platform**.

In enterprise SaaS systems (e.g. Stripe, Shopify, Datadog, Slack), the platform serves thousands of distinct corporate customers ("tenants") from a single shared software deployment. The central engineering challenge is providing:
1. **Absolute Data Isolation**: Rendering cross-tenant data leaks (Insecure Direct Object Reference / IDOR) mathematically impossible at both ORM and database storage layers.
2. **Context-Aware Dynamic Routing**: Resolving tenant identity deterministically from subdomains, custom headers, and cryptographically verified JWT claims across the entire request-response lifecycle.
3. **Multi-Tenant Role-Based Access Control (RBAC)**: Enforcing granular roles (`OWNER`, `ADMIN`, `MEMBER`, `BILLING_ADMIN`, `AUDITOR`) scoped strictly to tenant boundaries.
4. **Deterministic Keyset Pagination & Dynamic Query Scoping**: Enabling customers to slice, filter, and paginate through millions of records with $O(1)$ B-tree index seek latency and zero page drift.
5. **Decoupled Service Layer & Transactional Outbox**: Eliminating fragile model signals in favor of explicit transactional commands, atomic outbox event recording, and deferred `transaction.on_commit()` asynchronous task dispatches.
6. **Multi-Tenant Redis Caching & Pessimistic Concurrency**: Namespacing cache keys by tenant, preventing cache stampedes, and locking shared tenant balances using `select_for_update()`.

```
========================================================================================================
                          ENTERPRISE MULTI-TENANT SAAS ARCHITECTURAL BLUEPRINT
========================================================================================================

Incoming Client Request (e.g. GET /api/v1/invoices/?status=PAID&cursor=eyJ...)
Host: acme-corp.saas.enterprise.com  |  Authorization: Bearer <JWT_Token>
                                            │
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │ 1. Tenant Resolution Middleware               │
                    │    - Subdomain parsing ("acme-corp")          │
                    │    - Header parsing ("X-Tenant-ID")           │
                    │    - Sets contextvars / threading.local       │
                    └───────────────────────────────────────────────┘
                                            │
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │ 2. DRF JWT Authentication Backend             │
                    │    - Cryptographic signature check (HS256)    │
                    │    - Token Type enforcement (access != refresh)│
                    │    - Cross-Tenant Guard: JWT tenant == Context│
                    └───────────────────────────────────────────────┘
                                            │
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │ 3. Multi-Tenant RBAC Permission Engine        │
                    │    - Evaluates role permissions for tenant    │
                    │    - Blocks privilege escalation              │
                    └───────────────────────────────────────────────┘
                                            │
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │ 4. Scoped ModelViewSet & Dynamic Filtering    │
                    │    - get_queryset():                          │
                    │      WHERE tenant_id = 'acme-corp' (Mandatory)│
                    │    - Whitelisted dynamic lookups & ordering   │
                    └───────────────────────────────────────────────┘
                                            │
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │ 5. Keyset (Cursor) Pagination Engine          │
                    │    - O(1) Index Seek:                         │
                    │      WHERE (created_at, id) < (cursor_t, id)  │
                    │    - Zero Page Drift                          │
                    └───────────────────────────────────────────────┘
                                            │
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │ 6. Service Layer & Database Engine            │
                    │    - transaction.atomic():                    │
                    │      - select_for_update() on row locks       │
                    │      - OutboxEvent persisted alongside entity │
                    │    - transaction.on_commit():                 │
                    │      - Cache eviction (tenant-namespaced)     │
                    │      - Async task queuing (Celery/Kafka)      │
                    └───────────────────────────────────────────────┘
                                            │
                                            ▼
                      200 OK Response (with RFC 5988 Link Cursors)
```

---

## 2. Multi-Tenancy Architecture Patterns: Trade-Off Analysis

When designing a multi-tenant backend, architects choose between three primary data partitioning strategies:

| Dimension | Pattern A: Shared Database, Shared Schema (Discriminator Column) | Pattern B: Shared Database, Separate Schema (PostgreSQL Schemas) | Pattern C: Database per Tenant (Isolated Databases) |
|:---|:---|:---|:---|
| **Storage Architecture** | Single database; every table has a `tenant_id` column indexed as the leading column. | Single database; each tenant has an isolated PostgreSQL schema (`CREATE SCHEMA tenant_acme`). | Completely separate PostgreSQL instances or databases per tenant. |
| **Data Isolation** | **Logical isolation** enforced by ORM / Row-Level Security (RLS). | **Schema isolation** enforced by PostgreSQL `search_path`. | **Physical isolation** at network and storage levels. |
| **Hardware Efficiency** | **Maximum** (Zero idle resource overhead; shared connection pool). | **Medium** (High table count; PostgreSQL metadata catalog bloat at > 5,000 schemas). | **Lowest** (High infrastructure cost; dozens of idle database instances). |
| **Database Migrations** | **Instant** (Run `manage.py migrate` once across all tenants). | **Slow / Complex** (Must run migrations in a loop across $N$ schemas). | **Very Complex** (Orchestrated distributed migration fleet). |
| **Operational Scalability** | **Up to 100,000+ tenants** on single sharded clusters. | Up to ~2,000–5,000 tenants before PostgreSQL performance cliffs. | Uncapped, but management complexity is extreme. |
| **Target Audience** | **Standard SaaS / B2B SaaS** (Stripe, GitHub, Jira, Shopify). | Enterprise customers requiring schema separation. | Banks, Defense, Healthcare with strict statutory physical air-gap mandates. |

**Capstone Architecture Decision**: Pattern A (**Shared Database, Shared Schema with Discriminator Column & Row-Level Security**) backed by compound B-tree indexing `(tenant_id, created_at, id)`. This delivers optimal hardware density, seamless continuous deployments, and multi-region read scalability.

---

## 3. Deterministic Tenant Resolution Pipeline

In production, requests arrive through diverse ingress routes: web browsers, automated API integrations, and webhooks. The resolution pipeline evaluates identity using a deterministic priority hierarchy:

```
Priority 1: Cryptographic JWT Claims (`payload['tenant_id']`)
     │
     ▼ (If unauthenticated or public endpoint)
Priority 2: Custom Subdomain (`tenant_slug.enterprise.com`)
     │
     ▼ (If behind API gateway / internal mesh)
Priority 3: Header Injection (`X-Tenant-ID`)
     │
     ▼ (Fallback)
400 Bad Request / 401 Unauthorized ("Tenant context missing or invalid")
```

### Context Isolation via `contextvars`
To guarantee thread-safety across synchronous WSGI and asynchronous ASGI workers, the resolved tenant must be stored in Python's `contextvars.ContextVar`, **not** raw global variables:

```python
import contextvars
from typing import Optional

_current_tenant: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_tenant", default=None
)

class TenantContext:
    @staticmethod
    def get_current_tenant() -> Optional[str]:
        return _current_tenant.get()

    @staticmethod
    def set_current_tenant(tenant_id: str) -> contextvars.Token:
        return _current_tenant.set(tenant_id)

    @staticmethod
    def reset(token: contextvars.Token) -> None:
        _current_tenant.reset(token)
```

---

## 4. The Insecure Direct Object Reference (IDOR) Threat Model

An **Insecure Direct Object Reference (IDOR)** occurs when an application exposes a reference to an internal object (e.g. `GET /api/v1/invoices/99214/`) and fails to verify that the requesting tenant owns that object.

### The Attack Vector:
1. Attacker authenticates as legitimate User in Tenant A (`tenant_id = "tenant_evil"`).
2. Attacker inspects invoice endpoint: `GET /api/v1/invoices/1001/`.
3. Attacker sweeps sequential or UUID primary keys: `GET /api/v1/invoices/1002/`, `1003/`, etc.
4. If the view relies on `Invoice.objects.get(id=pk)` without scoping to the authenticated tenant, **Tenant A reads Tenant B's confidential invoices, pricing, and customer records**.

### The Remediation: Dual-Layer Invariant Scoping
1. **Layer 1 (Manager/QuerySet Level)**: The base QuerySet automatically enforces `.filter(tenant_id=current_tenant)`.
2. **Layer 2 (View Level)**: `get_queryset()` explicitly derives from `request.user.tenant_id`. Any lookup for another tenant's primary key returns `404 Not Found` (never `403 Forbidden`, to prevent resource enumeration).

---

## 5. Global Multi-Tenant QuerySet & Manager Enforcement

To eliminate human error where a developer forgets `.filter(tenant_id=...)`, models inherit from an abstract `TenantAwareModel`:

```python
class TenantAwareQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id: str):
        return self.filter(tenant_id=tenant_id)

class TenantAwareManager(models.Manager):
    def get_queryset(self):
        qs = TenantAwareQuerySet(self.model, using=self._db)
        tenant_id = TenantContext.get_current_tenant()
        if tenant_id:
            return qs.filter(tenant_id=tenant_id)
        return qs
```

### Safety Escape Hatch (`unscoped_objects`)
For administrative cron jobs, billing aggregations, and platform migrations, a separate unscoped manager `objects_unscoped = models.Manager()` is explicitly exposed to allow cross-tenant queries only where deliberately intended.

---

## 6. PostgreSQL Row-Level Security (RLS) as Defense-in-Depth

Relying solely on application-level filtering leaves systems vulnerable to raw SQL bugs, management script errors, or ORM misconfigurations. **PostgreSQL Row-Level Security (RLS)** enforces tenant boundaries at the database kernel level:

```sql
-- Enable RLS on the table
ALTER TABLE saas_invoice ENABLE ROW LEVEL SECURITY;

-- Create policy forcing tenant isolation
CREATE POLICY tenant_isolation_policy ON saas_invoice
    AS RESTRICTIVE
    USING (tenant_id = current_setting('app.current_tenant_id', true));
```

When Django establishes or borrows a connection from the pool, middleware executes:
```sql
SET LOCAL app.current_tenant_id = 'tenant_acme';
```
Even if a developer executes `SELECT * FROM saas_invoice;` via raw SQL or ORM bypass, PostgreSQL physically filters the rows at the disk buffer level, returning only records matching `'tenant_acme'`.

---

## 7. Multi-Tenant JWT Authentication with Custom Claims

In a multi-tenant platform, an access token is bound to both a specific user identity and a specific tenant workspace:

```json
{
  "iss": "https://auth.saas.enterprise.com",
  "sub": "usr_99182312",
  "tenant_id": "tenant_acme_corp",
  "role": "ADMIN",
  "permissions": [
    "invoices:read",
    "invoices:create",
    "invoices:delete",
    "team:manage"
  ],
  "token_type": "access",
  "jti": "b48c21e4-8f92-4d2a-9e12-4c6e91a0b382",
  "iat": 1793294000,
  "exp": 1793294900
}
```

### Authentication Invariant:
When an authenticated request hits the API:
$$\text{request.tenant\_id} \equiv \text{token.payload['tenant\_id']}$$
If a user authenticates with an access token issued for `tenant_acme_corp` and attempts to send `X-Tenant-ID: tenant_competitor`, the authentication backend immediately rejects the request with `401 Unauthorized` / `CrossTenantTamperingDetected`.

---

## 8. Role-Based Access Control (RBAC) Matrix

Enterprise SaaS systems structure permissions hierarchically within tenant workspaces:

| Role | Workspace Settings | Member Management | Billing & Plans | Core Resource Write | Core Resource Read |
|:---|:---:|:---:|:---:|:---:|:---:|
| **OWNER** | Allowed | Allowed | Allowed | Allowed | Allowed |
| **ADMIN** | Allowed | Allowed | Denied | Allowed | Allowed |
| **MEMBER** | Denied | Denied | Denied | Allowed | Allowed |
| **VIEWER** | Denied | Denied | Denied | Denied | Allowed |
| **BILLING** | Denied | Denied | Allowed | Denied | Allowed |

Permissions are evaluated dynamically on DRF ViewSets using composable permission classes:
`permission_classes = [IsAuthenticated, HasTenantPermission('invoices:write')]`.

---

## 9. Scoped Keyset (Cursor) Pagination Architecture

In high-volume B2B systems (e.g. streaming event logs, audit trails, ledger invoices), offset pagination degrades database performance. Capstone 1 implements **Multi-Tenant Keyset Pagination**:

```sql
SELECT id, invoice_number, amount, created_at
FROM saas_invoice
WHERE tenant_id = 'tenant_acme' 
  AND (created_at, id) < ('2026-11-01 12:00:00+00', 489210)
ORDER BY created_at DESC, id DESC
LIMIT 51;
```

### Compound Index Alignment:
To achieve sub-5ms latency across 10,000,000 rows, the physical index must mirror the query filter and ordering clauses:
```sql
CREATE INDEX idx_tenant_created_id 
ON saas_invoice (tenant_id, created_at DESC, id DESC);
```
With this compound B-tree index, PostgreSQL performs a direct logarithmic seek ($O(\log N)$) into the tenant partition, scanning only the exact page size requested.

---

## 10. Declarative Dynamic Filtering & Whitelisting

Multi-tenant filtering must enforce two non-negotiable rules:
1. **Filtering is strictly a sub-clause of tenant scoping**: The user's query parameters (`?status=PAID&min_amount=500`) are compiled *inside* the tenant boundary.
2. **Strict Whitelisting**: Undeclared query parameters are discarded to prevent SQL injection or schema enumeration.

```python
class InvoiceFilterSet(django_filters.FilterSet):
    min_amount = django_filters.NumberFilter(field_name='amount', lookup_expr='gte')
    max_amount = django_filters.NumberFilter(field_name='amount', lookup_expr='lte')
    status = django_filters.ChoiceFilter(choices=Invoice.Status.choices)
    search = django_filters.CharFilter(method='filter_search')

    class Meta:
        model = Invoice
        fields = ['status']

    def filter_search(self, queryset, name, value):
        # Index-friendly prefix search
        return queryset.filter(invoice_number__istartswith=value)
```

---

## 11. Decoupled Service Layer & Transactional Outbox Pattern

In accordance with Day 43 principles, Capstone 1 eliminates all model signals for core business events. Model mutations, balance deductions, and audit event creation are orchestrated via typed Service Layer commands:

```python
@dataclass(frozen=True)
class IssueInvoiceCommand:
    tenant_id: str
    customer_id: int
    amount: Decimal
    items: List[Dict[str, Any]]
    actor_user_id: int
    idempotency_key: str

class InvoiceService:
    @classmethod
    def issue_invoice(cls, cmd: IssueInvoiceCommand) -> Invoice:
        with transaction.atomic():
            # 1. Pessimistic Lock on Customer Balance (Day 44)
            customer = Customer.objects.select_for_update().get(
                id=cmd.customer_id, 
                tenant_id=cmd.tenant_id
            )

            # 2. Persist Aggregate
            invoice = Invoice.objects.create(
                tenant_id=cmd.tenant_id,
                customer=customer,
                amount=cmd.amount,
                status=Invoice.Status.ISSUED,
                idempotency_key=cmd.idempotency_key
            )

            # 3. Transactional Outbox Event (Guaranteed Zero Loss)
            OutboxEvent.objects.create(
                tenant_id=cmd.tenant_id,
                aggregate_type="INVOICE",
                aggregate_id=invoice.id,
                event_type="INVOICE_ISSUED",
                payload={"invoice_id": invoice.id, "amount": str(cmd.amount)}
            )

            # 4. Safe Post-Commit Asynchronous Task
            transaction.on_commit(
                lambda: send_invoice_notification_task.delay(invoice.id)
            )

        return invoice
```

---

## 12. Tenant-Namespaced Redis Caching & Cache-Aside

To prevent cross-tenant cache contamination, all Redis cache keys are prefixed with the tenant's unique identifier:
$$\text{key} = \text{"tenant:"} + \text{tenant\_id} + \text{":entity:"} + \text{entity\_id}$$

### Cache Eviction Invariant:
When an entity is updated or deleted, cache eviction occurs **strictly inside `transaction.on_commit()`**:
```python
transaction.on_commit(
    lambda: cache.delete(f"tenant:{invoice.tenant_id}:invoice:{invoice.id}")
)
```

---

## 13. High-Concurrency Stress Testing & Verification Methodology

Capstone 1 includes a rigorous verification suite testing:
1. **Cross-Tenant IDOR Attack Simulation**: Generating requests from Tenant B targeting Tenant A's invoice IDs and verifying strict `404 Not Found` rejection.
2. **Concurrent Balance Deduction**: Executing 1,000 simultaneous transfers on a tenant balance using `select_for_update()` to verify 0 lost updates.
3. **Deep Keyset Pagination Benchmark**: Traversing 50,000 multi-tenant records with opaque Base64 cursors, demonstrating sub-5ms response times and zero page drift.
4. **Cache Stampede Mutex Protection**: Simulating 50 concurrent hits on an uncached tenant resource, verifying exactly 1 SQL query is executed.

---

## 14. Capstone Architecture Directory Structure

```
03-Django-and-DRF/Day-45-Capstone-Multi-Tenant-SaaS-Backend/
├── README.md               # 15-Point Capstone Architectural Specification
├── example_01.py           # Core Multi-Tenant SaaS Engine (Zero external dependencies)
├── example_02.py           # High-Throughput Load, IDOR Defense & Latency Benchmark
├── mistakes.md             # 7 Critical Multi-Tenant Antipatterns & Remediations
├── exercises.md            # 3 Advanced Architectural Challenges with Solutions
├── interview.md            # Top 5 Senior/Staff Multi-Tenant Interview Q&A
└── revision.md             # 5-Minute Rapid Revision Cheat Sheet
```

---

## 15. Capstone Readiness & Evaluation Rubric

| Architectural Dimension | Enterprise Standard | Capstone Implementation |
|:---|:---|:---|
| **Tenant Isolation** | Zero cross-tenant data leaks; IDOR impossible | Compound ORM scoping + ContextVar + 404 on cross-lookup |
| **Authentication** | Cryptographic token lifecycle with blacklisting | Dual-token JWT (SimpleJWT) with embedded tenant claims |
| **Pagination** | $O(1)$ constant seek time; immune to page drift | Keyset cursor pagination with deterministic tie-breaking |
| **Concurrency** | Zero lost updates; no inventory overselling | Pessimistic `select_for_update()` + deterministic ID sorting |
| **Domain Logic** | Clean architecture; no hidden signals | Explicit Service Layer + Transactional Outbox Pattern |
| **Caching** | Safe namespacing; protected against stampedes | Tenant-prefixed keys + Distributed Mutex Lock via SETNX |
