# Day 45: Senior Backend Engineer Interview Q&A — Multi-Tenant SaaS Architecture

This guide provides deep technical responses to senior and staff backend engineering interview questions regarding multi-tenant architecture, cross-tenant data isolation, PostgreSQL Row-Level Security (RLS), and noisy-neighbor mitigation.

---

### Question 1: Compare the three multi-tenant data partitioning models (Shared Schema vs Separate Schema vs Separate Database). What are the operational, hardware, and compliance trade-offs at 10,000+ tenants?

#### Answer:
Choosing a multi-tenant isolation strategy is an engineering trade-off between **resource utilization density**, **operational migration complexity**, and **regulatory compliance boundaries**:

#### 1. Shared Database, Shared Schema (Discriminator Column `tenant_id`):
- **Mechanism**: All tenants share identical tables; every row contains a `tenant_id` column.
- **Hardware Efficiency**: **Highest**. Can pack 100,000+ tenants onto a single sharded PostgreSQL cluster. Zero idle database overhead. Shared connection pools and buffer caches.
- **Migration Operational Cost**: **Lowest**. Running `manage.py migrate` executes once across all tenants in seconds.
- **Failure Mode / Risks**: High risk of application-level bugs causing cross-tenant IDOR leaks if a developer omits `.filter(tenant_id=...)`. Requires defense-in-depth (PostgreSQL RLS).
- **Industry Standard**: Stripe, GitHub, Shopify, Jira.

#### 2. Shared Database, Separate Schema (PostgreSQL Schemas):
- **Mechanism**: Single database; each tenant receives an isolated PostgreSQL schema (`tenant_acme.invoices`). Requests switch schemas dynamically via `SET search_path = tenant_acme, public;`.
- **Operational Scalability Cliff**: Severe PostgreSQL catalog bloat. At > 5,000 schemas, PostgreSQL's internal metadata tables (`pg_class`, `pg_attribute`) consume gigabytes of shared memory. A single table with 10 columns across 5,000 schemas generates 50,000 table definitions and 50,000 indexes. Query planning latency degrades significantly.
- **Migration Cost**: Extremely slow. A migration must iterate and apply DDL across 5,000 schemas sequentially, taking hours and locking the catalog.

#### 3. Database per Tenant (Isolated Databases):
- **Mechanism**: Completely separate PostgreSQL instances or databases per tenant.
- **Compliance & Security**: **Maximum**. Air-gapped network security, dedicated encryption keys per tenant, easy compliance with strict statutory mandates (HIPAA, FedRAMP, PCI-DSS Level 1). A breach of one tenant's database physically cannot leak another tenant's data.
- **Hardware & Cost**: **Lowest efficiency**. Thousands of idle connections and compute nodes. High AWS RDS/Aurora licensing and hosting bills.
- **Migration Cost**: Requires complex distributed migration orchestration tools (e.g. running parallel migration workers across database fleets).

---

### Question 2: How do you prevent Insecure Direct Object References (IDOR) at both application and database engine layers in Django?

#### Answer:
Relying solely on developers remembering to append `.filter(tenant_id=...)` in views is guaranteed to fail as an engineering organization scales. Robust IDOR defense requires a **defense-in-depth dual-layer architecture**:

#### Layer 1: Application Layer (DRF ViewSet & Base Manager)
1. **Context-Driven Scoping**: Store the authenticated tenant in Python `contextvars`.
2. **Base Manager Invariant**: Models inherit from `TenantAwareManager`, which automatically injects `WHERE tenant_id = current_tenant` into every QuerySet.
3. **DRF `get_queryset()` Override**: ViewSets inherit from a `TenantScopedViewSetMixin`:
   ```python
   def get_queryset(self):
       return self.queryset.filter(tenant_id=self.request.user.tenant_id)
   ```
4. **404 Not Found Invariant**: When an attacker probes an invoice ID belonging to another company (`GET /api/v1/invoices/992/`), the query evaluates `WHERE id = 992 AND tenant_id = 'attacker'`. This returns 0 rows, and DRF automatically responds with **`404 Not Found`**.
   *Note*: Never return `403 Forbidden` for ID lookups, as returning 403 confirms to an attacker that the resource ID exists.

#### Layer 2: Database Layer (PostgreSQL Row-Level Security / RLS)
Even if an application developer writes raw SQL or bypasses the ORM, the database kernel prevents cross-tenant reads:
```sql
ALTER TABLE saas_invoice ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_policy ON saas_invoice
    AS RESTRICTIVE
    USING (tenant_id = current_setting('app.current_tenant_id', true));
```
When Django acquires a connection, middleware executes:
```sql
SET LOCAL app.current_tenant_id = 'tenant_acme';
```
PostgreSQL's storage engine enforces the policy directly during sequential scans and index seeks, guaranteeing zero data leakage at the hardware level.

---

### Question 3: How does the "Noisy Neighbor" problem manifest in multi-tenant PostgreSQL and Redis clusters, and how do you engineer defenses against it?

#### Answer:
The **Noisy Neighbor Problem** occurs when one high-volume or malicious tenant consumes an unfair share of shared infrastructure resources (CPU, disk I/O, database connections, Redis bandwidth), degrading latency and availability for all other tenants sharing the cluster.

#### Manifestations:
1. **PostgreSQL Connection Exhaustion**: A tenant triggers 200 concurrent unindexed reporting queries, claiming all available connections in the pool (`max_connections = 300`) and starving other tenants of database access.
2. **Buffer Cache Eviction**: A tenant queries a 100 GB historical table, evicting active indexes and pages of other tenants from PostgreSQL `shared_buffers` into disk swap.
3. **Redis Thread Blocking**: A tenant issues an unindexed `KEYS *` or large `HGETALL` command, blocking Redis's single-threaded event loop for 500 ms and causing timeouts across all applications.

#### Production Defenses:
1. **Tiered Rate Limiting at the API Gateway**: Implement a sliding-window rate limiter (Redis Token Bucket) keyed by `tenant_id`. Free tenants are capped at 50 req/min; Pro at 500 req/min; Enterprise at 5,000 req/min.
2. **Connection Pooling with Tenant Fair-Share Limits**: Deploy PgBouncer or AWS RDS Proxy. Configure statement timeouts (`statement_timeout = 3000ms`) to kill run-away queries automatically.
3. **Read Replicas & CQRS**: Route heavy analytic exports and report queries to dedicated asynchronous read replicas, completely isolating transactional OLTP traffic from reporting workloads.
4. **Tenant-Aware Sharding**: High-tier enterprise tenants ("whales") generating > 20% of traffic are dynamically allocated to dedicated database clusters (Hybrid Multi-Tenancy).

---

### Question 4: Explain how PostgreSQL Row-Level Security (RLS) works under the hood with Django connection pools. What are the performance and connection-leakage risks?

#### Answer:
PostgreSQL Row-Level Security rewrites the internal query parse tree, appending user-defined policy predicates (`WHERE tenant_id = current_setting('app.current_tenant_id')`) to every `SELECT`, `UPDATE`, and `DELETE` query plan before optimization.

#### Connection Pooling Mechanics:
In production, Django uses persistent database connections (`CONN_MAX_AGE > 0`) or connection poolers (PgBouncer in transaction pooling mode).
- If an application executes `SET app.current_tenant_id = 'tenant_a'`, the setting remains attached to the underlying physical PostgreSQL connection.
- If that connection is returned to the pool and later assigned to another request for `tenant_b` without clearing the session variable, **a connection leak occurs**, allowing Tenant B to view Tenant A's data!

#### Mitigation (`SET LOCAL`):
To prevent connection state leakage across pooled connections, Django must execute `SET LOCAL`:
```python
with connection.cursor() as cursor:
    cursor.execute("SET LOCAL app.current_tenant_id = %s;", [tenant_id])
```
`SET LOCAL` binds the session variable strictly to the lifetime of the **active database transaction**. When `COMMIT` or `ROLLBACK` executes, PostgreSQL automatically purges the variable, guaranteeing that the connection is completely sanitized before returning to PgBouncer.

---

### Question 5: How do you design zero-downtime database schema migrations in a multi-tenant shared-schema architecture with hundreds of millions of rows?

#### Answer:
In a table with 200,000,000 rows across 10,000 tenants, issuing a blocking DDL lock (e.g. `ALTER TABLE invoices ADD COLUMN fee numeric NOT NULL DEFAULT 0;`) takes hours, acquires an `ACCESS EXCLUSIVE` lock, and takes down the entire SaaS platform.

#### The 5-Phase Zero-Downtime Migration Blueprint:

1. **Phase 1: Add Column as Nullable (Non-blocking)**:
   ```sql
   ALTER TABLE invoices ADD COLUMN fee NUMERIC(10, 2) NULL;
   ```
   Adding a nullable column with no default in PostgreSQL (v11+) is an instant $O(1)$ metadata update requiring zero table locks.

2. **Phase 2: Deploy Dual-Writing Application Code**:
   Deploy code where new writes populate both the old schema and the new column:
   ```python
   invoice.fee = calculated_fee
   ```

3. **Phase 3: Background Backfill in Batched Tenant Chunks**:
   Run an asynchronous backfill script iterating tenant by tenant in small indexed batches:
   ```python
   for tenant in tenants:
       while True:
           updated = Invoice.objects.filter(tenant_id=tenant, fee__isnull=True)[:1000].update(fee=0)
           if not updated:
               break
           time.sleep(0.05)  # Yield CPU to prevent replication lag
   ```

4. **Phase 4: Add Check Constraint NOT VALID & Validate (Zero Lock)**:
   ```sql
   -- Instant validation: does not scan existing rows
   ALTER TABLE invoices ADD CONSTRAINT fee_not_null CHECK (fee IS NOT NULL) NOT VALID;
   -- Validates in background without holding exclusive lock!
   ALTER TABLE invoices VALIDATE CONSTRAINT fee_not_null;
   ```

5. **Phase 5: Deploy Clean Application Code**:
   Remove legacy fallback code and make the field mandatory in Django serializers.
