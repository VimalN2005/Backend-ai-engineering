# Day 45: 5-Minute Rapid Revision — Multi-Tenant SaaS Architecture

---

## 1. Multi-Tenancy Architecture Comparison Matrix

| Property | Shared Database, Shared Schema (Discriminator) | Shared Database, Separate Schema | Database per Tenant |
|:---|:---|:---|:---|
| **Isolation Level** | Logical (ORM + PostgreSQL RLS) | Schema (`search_path`) | Physical (Air-gapped) |
| **Hardware Density** | **Highest (100,000+ tenants/cluster)** | Medium (~2,000 tenants before catalog bloat) | Lowest (High idle cloud bills) |
| **Migrations** | Run once in seconds (`manage.py migrate`) | Loop through $N$ schemas (hours) | Fleet orchestration needed |
| **Connection Pooling** | Single shared pool (PgBouncer) | Complex dynamic schema switching | Massive connection overhead |
| **Ideal Use Case** | **Modern B2B SaaS (Stripe, GitHub, Shopify)** | Regulatory niche | Banks, Defense, FedRAMP |

---

## 2. Multi-Tenant Request Lifecycle Pipeline

```
1. Client Request: GET /api/v1/invoices/992/
   Host: acme.enterprise.com  |  Authorization: Bearer <JWT>
                  │
                  ▼
2. TenantResolutionMiddleware: Extracts tenant from Subdomain / JWT claim
   Sets: contextvars.ContextVar("current_tenant", "acme")
                  │
                  ▼
3. MultiTenantJWTAuthentication: Verifies HS256 signature + checks tenant_id
   Asserts: token['tenant_id'] == header['X-Tenant-ID'] (Anti-Tampering)
                  │
                  ▼
4. MultiTenantRBAC: Evaluates user role (OWNER, ADMIN, MEMBER, VIEWER)
                  │
                  ▼
5. ModelViewSet get_queryset(): Injects WHERE tenant_id = 'acme'
   (Query for ID 992 belonging to 'beta' evaluates 0 rows -> Returns 404 Not Found)
                  │
                  ▼
6. Keyset Pagination: O(1) seek via (tenant_id, created_at DESC, id DESC)
                  │
                  ▼
7. Service Layer & Outbox: Transactional Outbox + transaction.on_commit() cache eviction
```

---

## 3. The Golden Rules of Multi-Tenant Engineering

1. **The Leading Index Rule**:  
   `tenant_id` must always be the **leading (first) column** in all composite indexes:  
   `models.Index(fields=['tenant_id', 'created_at', 'id'], name='idx_tenant_created_id')`
2. **Never Return 403 on Cross-Tenant Primary Key Lookups**:  
   Always return **`404 Not Found`**. Returning 403 confirms to an attacker that the victim's resource ID exists.
3. **Always Namespace Redis Cache Keys by Tenant**:  
   `key = f"tenant:{tenant_id}:{entity_type}:{entity_id}"`  
   Never share un-namespaced keys like `product:{id}` across corporate customers!
4. **Use `SET LOCAL` for PostgreSQL Row-Level Security**:  
   `SET LOCAL app.current_tenant_id = 'acme';`  
   Guarantees that session variables are purged upon transaction commit, preventing connection-pool leaks.
5. **Scoped Uniqueness Constraints**:  
   Never enforce global uniqueness on business entity slugs or numbers:  
   `models.UniqueConstraint(fields=['tenant_id', 'sku'], name='unique_tenant_sku')`

---

## 4. Production Scoped ViewSet Template

```python
class TenantScopedViewSetMixin:
    """Enforces absolute tenant isolation across all CRUD actions."""
    def get_queryset(self):
        # 1. Scope query strictly to authenticated tenant
        tenant_id = self.request.user.tenant_id
        return self.queryset.filter(tenant_id=tenant_id)

    def perform_create(self, serializer):
        # 2. Automatically inject authenticated tenant into created record
        serializer.save(tenant_id=self.request.user.tenant_id)

class InvoiceViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission('invoices:read')]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['status']
    ordering_fields = ['created_at', 'amount', 'id']
    ordering = ['-created_at', '-id']
```

---

## 5. Ten-Second Interview Defense Checklist

1. *How do you prevent IDOR in multi-tenant SaaS?* -> Dual-layer defense: base ModelManager and ViewSet `get_queryset()` inject `WHERE tenant_id = current_tenant`; backed by PostgreSQL Row-Level Security (`SET LOCAL`).
2. *Why does separate schema fail at 10,000 tenants?* -> PostgreSQL system catalog bloat (`pg_class`, `pg_attribute`) degrades query planning; schema migrations must loop across 10,000 tables.
3. *How do you prevent cross-tenant cache contamination?* -> Explicitly prefix all Redis keys with `tenant:{tenant_id}:...` and evict only inside `transaction.on_commit()`.
4. *Why should multi-tenant indexes start with `tenant_id`?* -> Enables B-tree index seeks to jump directly to the tenant's partition in $O(\log N)$ time, avoiding expensive scans of other tenants' data.
