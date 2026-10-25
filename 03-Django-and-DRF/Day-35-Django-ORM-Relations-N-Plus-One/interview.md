# Day 35: Top 5 Senior Backend Interview Questions — ORM Relations & N+1 Optimization

---

### Question 1: Explain the exact architectural difference between `select_related` and `prefetch_related` under the hood. Why can't `select_related` be used on `ManyToManyField` or reverse `ForeignKey`?

#### Architectural Answer:
`select_related` and `prefetch_related` are two fundamentally different optimization strategies designed for different relationship cardinalities:

```
+-----------------------------------------------------------------------------------------+
|                  SELECT_RELATED vs PREFETCH_RELATED DEEP DIVE                           |
+-----------------------------------------------------------------------------------------+
| Strategy              | select_related                    | prefetch_related            |
+-----------------------+-----------------------------------+-----------------------------+
| Query Count           | Always EXACTLY 1 SQL Query        | 1 + K Queries (K = relation)|
| Mechanism             | SQL INNER / LEFT OUTER JOIN       | Separate SELECT ... WHERE IN|
| Cardinality           | Single-Valued (FK, OneToOne)      | Multi-Valued (M2M, Rev FK)  |
| Execution Point       | PostgreSQL Engine (Disk/Buffer)   | Python Heap Memory (Hash Map|
| Duplication / Bloat   | Wide columns (network overhead)   | Narrow compact row sets     |
+-----------------------+-----------------------------------+-----------------------------+
```

#### Why `select_related` Fails on Multi-Valued Relations:
`select_related` operates by appending `JOIN` clauses to the SQL query.
If you attempt to perform an SQL `JOIN` between an `Order` and its `OrderItem` collection (one-to-many):
1. **Cartesian Row Multiplication**: If an order has 10 items, the database returns 10 duplicate copies of the order row (order ID, customer ID, created timestamp, shipping address) joined with each item.
2. If an order has 10 items and 5 discount codes, a dual join generates $10 \times 5 = 50$ duplicate rows for a single order!
3. Across 1,000 orders, the database wire protocol transmits hundreds of thousands of redundant bytes, overwhelming network bandwidth and Python parsing buffers.

#### How `prefetch_related` Solves This:
Instead of a join, `prefetch_related`:
1. Executes Query 1: `SELECT * FROM orders WHERE ...;`
2. Extracts all parent IDs in Python: `order_ids = [o.id for o in orders]`
3. Executes Query 2: `SELECT * FROM order_items WHERE order_id IN (1, 2, 3, ...);`
4. Uses an $\mathcal{O}(K)$ hash map in Python memory to group items by `order_id` and attaches them to `order._prefetched_objects_cache`.
Total queries: **Exactly 2**, zero duplicate parent columns transmitted.

---

### Question 2: What is the "Prefetch Cache Invalidation" trap in Django? If a developer prefetches related records and then calls `.filter()` or `.count()` on the related manager inside a loop, what happens? How do you solve it using `Prefetch(to_attr=...)`?

#### Architectural Answer:

#### The Trap:
When you prefetch a related collection via `Order.objects.prefetch_related("items")`, Django loads all related items into an internal in-memory cache on the instance (`order._prefetched_objects_cache["items"]`).
If a developer subsequently writes:
```python
for order in orders:
    active_items = order.items.filter(is_active=True)  # CRITICAL BUG!
```
Django's related manager recognizes that `.filter()` has modified the query parameters. Because the in-memory cache contains *all* items and not the filtered subset, **Django bypasses the cache entirely and executes a brand-new SQL query against the database**!

#### The Consequence:
For 100 orders, you pay the memory cost of prefetching all items in Query 2, and then execute **100 additional queries** in the loop (102 queries total). The N+1 bug is silently re-introduced!

#### The Senior Solution (`Prefetch` with `to_attr`):
Use Django's `Prefetch` object with the `to_attr` argument:
```python
from django.db.models import Prefetch

orders = Order.objects.prefetch_related(
    Prefetch(
        "items",
        queryset=OrderItem.objects.filter(is_active=True),
        to_attr="active_items",  # Stores filtered records as a pure Python list!
    )
).all()

for order in orders:
    for item in order.active_items:  # Pure in-memory list iteration! 0 SQL queries!
        process(item)
```
Using `to_attr`:
1. Pre-filters the items in the second query (`SELECT * FROM order_items WHERE order_id IN (...) AND is_active = TRUE`).
2. Attaches the result as a standard Python `list` on `order.active_items`.
3. Leaves the default `order.items` manager untouched, preventing accidental data pollution.

---

### Question 3: Why should enterprise backends avoid `on_delete=models.CASCADE` on core business models, and what is the difference between `PROTECT` and `RESTRICT` in PostgreSQL?

#### Architectural Answer:

#### The Danger of `CASCADE`:
When a model has `on_delete=models.CASCADE`, deleting a parent record triggers Django’s relational deletion collector. Django executes deep recursive queries to locate **every referencing child, grandchild, and related object across the entire database**, issuing bulk `DELETE` commands.
- If a user account or organization is deleted, `CASCADE` can wipe out invoices, payments, audit logs, and customer support tickets.
- Even if soft-deletes are intended, an accidental hard-delete via Django Admin or a script will trigger irreversible data loss.

#### `PROTECT` vs `RESTRICT`:
Both prevent parent deletion if referenced children exist, but they differ in how they evaluate cascading chains:

1. **`models.PROTECT` (Django Application Level)**:
   - Evaluated by Django's Python deletion collector before sending SQL to PostgreSQL.
   - Raises `django.db.models.ProtectedError` containing references to all blocking child objects.
   - Strictly blocks deletion even if the child would have been deleted by another cascading path.

2. **`models.RESTRICT` (Database & Engine Level - introduced in Django 3.1)**:
   - Emulates ANSI SQL `ON DELETE RESTRICT`.
   - Raises `django.db.models.RestrictedError` if children exist.
   - **Key Difference**: If a child record is referenced by the object being deleted, but that same child record is *also* slated for deletion via a cascading relationship from a common parent, `RESTRICT` permits the deletion, whereas `PROTECT` raises an error!

#### Enterprise Invariant:
Core financial, tenant, and audit entities should **always use `on_delete=models.PROTECT`**.

---

### Question 4: How does Django implement Many-to-Many relationships? Why should production schemas almost always define an explicit `through` model instead of relying on Django's auto-generated junction table?

#### Architectural Answer:
Under the hood, a `ManyToManyField` creates an intermediate junction table containing two foreign keys: one pointing to the source model, and one pointing to the target model, backed by a composite unique constraint: `UNIQUE(source_id, target_id)`.

#### Why Default M2M Tables are Insufficient in Production:
When Django auto-generates the junction table (e.g. `User.groups`), the table contains **only 3 columns**: `id`, `user_id`, and `group_id`.

In real-world business domains, relationships are rarely bare links. They almost always require **relational metadata**:
1. **Audit Timestamps**: When was the user added to the organization? (`created_at`)
2. **Actor Auditing**: Who invited or approved this membership? (`invited_by_id`)
3. **Role & Permissions**: What is the user's permission scope in this team? (`role = "ADMIN" | "MEMBER"`)
4. **Lifecycle State**: Is the membership active, pending invite, or suspended? (`status`)

#### Migrating Later is Expensive:
If you start with an auto-generated M2M table and later need to add a `role` field, migrating from an automatic table to an explicit `through` model requires:
- Creating a new custom model.
- Writing manual database data migrations to copy existing records.
- Dropping and recreating constraints without locking the database.

**Enterprise Rule**: Always declare an explicit `through` model on Day 1 for core Many-to-Many relationships.

---

### Question 5: How would you design a CI/CD pipeline and automated test suite to prevent N+1 query regressions from ever reaching production?

#### Architectural Answer:
Preventing N+1 queries requires a multi-layered defense strategy spanning unit tests, integration assertions, and runtime telemetry.

#### 1. Deterministic Query Budget Assertions (`assertNumQueries`):
In Django test suites, wrap list and detail API view tests in `self.assertNumQueries(K)`:
```python
from django.test import TestCase

class OrderListApiTest(TestCase):
    def test_query_count_is_constant_regardless_of_row_count(self):
        # Seed 5 orders
        self.create_orders(count=5)
        with self.assertNumQueries(2):
            self.client.get("/api/v1/orders/")

        # Seed 50 more orders (10x increase!)
        self.create_orders(count=50)
        # MUST STILL BE EXACTLY 2 QUERIES!
        with self.assertNumQueries(2):
            self.client.get("/api/v1/orders/")
```
If an engineer introduces an un-prefetched field in a serializer, `assertNumQueries(2)` immediately fails in GitHub Actions CI, blocking the pull request.

#### 2. Automated Static Linting & Middleware:
- Use **`django-silk`** or **`django-debug-toolbar`** in local/staging environments to flag duplicate queries.
- Deploy the **`nplusone`** package in staging environments: it hooks into Django's relational descriptors and raises immediate exceptions if a query is executed inside an iteration loop.

#### 3. APM Production Alerts (Datadog / New Relic):
Configure alerts on **query count per request**. If a transaction issues $> 25$ database queries during a single HTTP request, trigger an anomaly alert to the backend engineering team.
