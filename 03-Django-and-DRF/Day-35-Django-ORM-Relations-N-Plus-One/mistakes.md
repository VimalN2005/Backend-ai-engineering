# Day 35: Common Architectural Mistakes & Antipatterns in Django ORM Relations

Navigating entity relationships using Django's dot notation (`order.customer.organization.name`) feels like standard Python object traversal. However, in an un-optimized codebase, every dot can silently trigger an unindexed database query over the network socket.

Here are the **7 most prevalent and destructive relationship antipatterns**, why they cause production outages, and how senior engineers remediate them.

---

### Mistake 1: Accessing Foreign Keys in Loops Without `select_related`

#### The Flawed Approach:
```python
# serializers.py - THE CLASSIC N+1 CATASTROPHE
orders = Order.objects.filter(status="PAID")[:200]

data = []
for order in orders:
    # Triggers an individual SELECT * FROM customers WHERE id = ... on EVERY iteration!
    data.append({
        "order_id": order.id,
        "customer_name": order.customer.name,
        "customer_email": order.customer.email,
    })
```

#### Why It Fails in Production:
For 200 orders, this executes **201 SQL queries** (1 query to fetch orders + 200 individual queries for each customer).
Over a cloud network with 10ms database latency, the endpoint takes **2+ seconds** just waiting for network ping-pong, while connection pools become saturated.

#### The Senior Engineer Remediation:
Use `select_related` to push the relationship traversal into an SQL `INNER JOIN` executed in a single query:

```python
# PRODUCTION PATTERN: Exactly 1 SQL Query!
orders = Order.objects.filter(status="PAID").select_related("customer")[:200]

data = [
    {
        "order_id": order.id,
        "customer_name": order.customer.name,
        "customer_email": order.customer.email,
    }
    for order in orders  # 0 additional database queries!
]
```

---

### Mistake 2: The Prefetch Invalidation Trap (Filtering on Prefetched Managers)

#### The Flawed Approach:
```python
# services.py - THE SILENT PREFETCH CACHE BUST
orders = Order.objects.prefetch_related("items").all()

for order in orders:
    # ❌ DISASTER: Chaining .filter() on a prefetched manager BYPASSES the in-memory cache!
    # Django constructs a brand-new QuerySet and sends an independent SQL query to PostgreSQL!
    active_items = order.items.filter(is_active=True)
```

#### Why It Fails in Production:
Engineers assume `prefetch_related("items")` caches the items for all subsequent operations. However, in Django, **any chained method that alters the query (`.filter()`, `.exclude()`, `.order_by()`, `.count()`) invalidates the prefetch cache and triggers an immediate database query**.
You end up with the worst of both worlds: you paid the memory cost to prefetch all items, and then still executed N+1 queries in the loop!

#### The Senior Engineer Remediation:
Either filter in Python memory, or use a custom `Prefetch` object with `to_attr`:

```python
# SOLUTION A: In-memory list comprehension (0 SQL queries)
for order in orders:
    active_items = [item for item in order.items.all() if item.is_active]

# SOLUTION B: Pre-filtered Prefetch with to_attr (Optimal for large sets)
from django.db.models import Prefetch

orders = Order.objects.prefetch_related(
    Prefetch(
        "items",
        queryset=OrderItem.objects.filter(is_active=True),
        to_attr="active_items",  # Stored as a pure Python list!
    )
).all()

for order in orders:
    for item in order.active_items:  # Pure memory lookup!
        process(item)
```

---

### Mistake 3: Using `select_related` on Multi-Valued Relationships

#### The Flawed Approach:
```python
# views.py - TYPE ERROR / CARTESIAN JOIN HAZARD
# Attempting to select_related a Many-to-Many or reverse ForeignKey:
teams = Team.objects.select_related("members").all()
```

#### Why It Fails in Production:
1. In Django, attempting to call `select_related()` on a `ManyToManyField` or reverse `ForeignKey` raises a `FieldError: Invalid field name(s) given in select_related: 'members'. Non-relational field or relation to many.`
2. Even in raw SQL, performing a table join on a Many-to-Many relationship causes **row count explosion**: if a team has 100 members, the database returns 100 duplicate team rows over the wire!

#### The Senior Engineer Remediation:
- **Single-Valued (`ForeignKey`, `OneToOneField`)**: Use `select_related` (SQL `JOIN`).
- **Multi-Valued (`ManyToManyField`, reverse `ForeignKey`)**: Use `prefetch_related` (Two compact queries + Python hash map stitching).

```python
# CORRECT:
teams = Team.objects.prefetch_related("members").all()
```

---

### Mistake 4: Unchecked `on_delete=models.CASCADE` on Core Business Entities

#### The Flawed Approach:
```python
# models.py - CATASTROPHIC ACCIDENTAL DELETION RISK
class Invoice(models.Model):
    organization = models.ForeignKey("Organization", on_delete=models.CASCADE)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
```

#### Why It Fails in Production:
If a support representative or automated script deletes an `Organization` record, Django's deletion collector cascades through the entire database, **silently deleting all historical invoices, payments, and audit logs** associated with that organization.
Restoring cascading deletions from PostgreSQL backups takes hours and violates statutory financial compliance rules.

#### The Senior Engineer Remediation:
Always enforce `on_delete=models.PROTECT` on core business and financial entities:

```python
# models.py - PRODUCTION ACID SAFEGUARD
class Invoice(models.Model):
    # Raises ProtectedError preventing organization deletion if invoices exist!
    organization = models.ForeignKey("Organization", on_delete=models.PROTECT)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
```

---

### Mistake 5: Omitting Explicit `related_name` on Foreign Keys

#### The Flawed Approach:
```python
# models.py - NAMING COLLISION & UNCLEAR CODE
class Task(models.Model):
    assigned_user = models.ForeignKey(User, on_delete=models.PROTECT)
    creator_user = models.ForeignKey(User, on_delete=models.PROTECT)
```

#### Why It Fails in Production:
1. Django tries to auto-generate `user.task_set` for both foreign keys, causing a **reverse accessor collision error during system checks**:
   `ERRORS: Task.assigned_user: (fields.E304) Reverse accessor for 'Task.assigned_user' clashes with reverse accessor for 'Task.creator_user'.`
2. Default `<model>_set` naming makes domain logic ambiguous in large codebases.

#### The Senior Engineer Remediation:
Always provide explicit, descriptive `related_name` values:

```python
# models.py - CLEAN REVERSE ACCESSORS
class Task(models.Model):
    assigned_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="assigned_tasks",  # user.assigned_tasks.all()
    )
    creator_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="created_tasks",   # user.created_tasks.all()
    )
```

---

### Mistake 6: Cartesian Explosion from Prefetching Multiple Deep M2M Relations

#### The Flawed Approach:
```python
# Excessive deep prefetching without projections:
articles = Article.objects.prefetch_related(
    "tags",
    "co_authors",
    "categories",
    "comments__author__profile",
    "comments__reactions",
).all()
```

#### Why It Fails in Production:
While `prefetch_related` avoids Cartesian row multiplication in SQL, fetching 5 multi-valued relations simultaneously:
1. Executes **6 independent SQL queries**.
2. Loads hundreds of thousands of child objects into Python memory.
3. Consumes hundreds of megabytes of RAM while building Python dictionary mappings for `_prefetched_objects_cache`.

#### The Senior Engineer Remediation:
Only prefetch relations that will actually be rendered on the current screen. For secondary collections (e.g. comments or reactions), load them asynchronously via dedicated API endpoints or pagination.

---

### Mistake 7: Iterating Over `related.all()` Just to Check Count or Existence

#### The Flawed Approach:
```python
# views.py - UNNECESSARY ROW MATERIALIZATION
for customer in Customer.objects.all():
    # If customer has 5,000 orders, loads 5,000 Order instances to check length!
    if len(customer.orders.all()) > 0:
        promote_to_vip(customer)
```

#### Why It Fails in Production:
`len(customer.orders.all())` materializes every related row into a Python model instance.
Even if `prefetch_related("orders")` was applied, holding 100,000 order objects in memory across 20 customers causes extreme memory bloat.

#### The Senior Engineer Remediation:
Use `annotate(Count(...))` in the primary query, or call `customer.orders.exists()`:

```python
# OPTION A: Annotate in a single SQL query with GROUP BY
customers = Customer.objects.annotate(order_count=Count("orders")).filter(order_count__gt=0)
for customer in customers:
    promote_to_vip(customer)

# OPTION B: Check existence via SQL LIMIT 1
if customer.orders.exists():
    promote_to_vip(customer)
```

---

### Summary Checklist: Antipattern Detection Matrix

| # | Antipattern | Primary Failure Mode | Senior Remediation |
|---|-------------|----------------------|--------------------|
| 1 | Unoptimized loop FK access | N+1 database queries | Use `select_related('foreign_key')` |
| 2 | `.filter()` on prefetched relation | Cache invalidation & hidden N+1 | In-memory list comprehension or `to_attr` |
| 3 | `select_related` on M2M | FieldError / Query crashes | Use `prefetch_related` |
| 4 | `on_delete=CASCADE` on entities | Accidental irreversible data loss | Use `on_delete=models.PROTECT` |
| 5 | Omitting `related_name` | Reverse accessor collisions | Always declare explicit `related_name` |
| 6 | Massive multi-M2M prefetch | Python heap bloat & slow GC | Prefetch only active view attributes |
| 7 | `len(rel.all())` for counts | Memory bloat & row instantiation | Use `annotate(Count('rel'))` |
