# Day 34: Common Architectural Mistakes & Antipatterns in Django ORM Queries

The Django ORM is deceptively simple: writing `Order.objects.filter(...)` looks like standard Python list manipulation. However, treating the ORM as an in-memory collection rather than a SQL compilation engine is the primary source of slow endpoints, server OOM (Out Of Memory) crashes, and race condition data corruption.

Here are the **7 most dangerous Django ORM query antipatterns**, why they fail in production, and how senior engineers remediate them.

---

### Mistake 1: Using `len(qs)` or `if qs:` to Check Record Existence

#### The Flawed Approach:
```python
# views.py - DANGEROUS MEMORY SINK
orders = Order.objects.filter(customer_id=customer_id)

# Checking if any orders exist:
if orders:  # Or if len(orders) > 0:
    notify_customer()
```

#### Why It Fails in Production:
1. In Python, evaluating `bool(orders)` or `len(orders)` forces Django to **execute the full QuerySet and load every matching row into memory**.
2. If the customer has 10,000 order records, Django issues:
   ```sql
   SELECT * FROM orders WHERE customer_id = 101;
   ```
   It transfers megabytes of data over the network, allocates 10,000 Python model instances, and stores them in `_result_cache`—all just to evaluate a boolean check!

#### The Senior Engineer Remediation:
Use `qs.exists()` for boolean checks, and `qs.count()` when the exact count is required:

```python
# PRODUCTION PATTERN: Translates to SELECT (1) AS "a" FROM orders WHERE customer_id = 101 LIMIT 1;
if Order.objects.filter(customer_id=customer_id).exists():
    notify_customer()

# If counting: SELECT COUNT(*) FROM orders WHERE customer_id = 101;
count = Order.objects.filter(customer_id=customer_id).count()
```

---

### Mistake 2: In-Memory Read-Modify-Write Race Conditions

#### The Flawed Approach:
Updating numeric values or inventory by mutating the model instance attribute in Python:

```python
# services.py - SEVERE CONCURRENCY BUG
product = Product.objects.get(id=product_id)
product.stock -= 1  # Read in memory, subtract 1
product.save()      # Write back to DB
```

#### Why It Fails in Production:
Under concurrent web requests (e.g. during a flash sale), two requests hit two different Gunicorn worker processes simultaneously:
1. **Request A** reads `stock = 10`.
2. **Request B** reads `stock = 10`.
3. Request A computes `10 - 1 = 9` and saves `stock = 9`.
4. Request B computes `10 - 1 = 9` and saves `stock = 9`.

**Result**: Two items were purchased, but stock only decreased by 1! The second write overwrote the first (Lost Update Anomaly).

#### The Senior Engineer Remediation:
Use atomic SQL `F()` expressions or database-level row locking (`select_for_update`):

```python
from django.db.models import F

# ATOMIC SQL UPDATE: UPDATE products SET stock = stock - 1 WHERE id = 1 AND stock > 0;
updated_rows = Product.objects.filter(id=product_id, stock__gt=0).update(
    stock=F("stock") - 1
)
if updated_rows == 0:
    raise OutOfStockError("Item is out of stock.")
```

---

### Mistake 3: Forgetting `refresh_from_db()` After Assigning `F()` Expressions

#### The Flawed Approach:
```python
# services.py - BUGGY STATE
order = Order.objects.get(id=1)
order.view_count = F("view_count") + 1
order.save()

# Reading attribute immediately afterwards:
print(order.view_count)  # Output: <Combined: F(view_count) + Value(1)> ! NOT AN INTEGER!
```

#### Why It Fails in Production:
When you assign an `F()` expression to an instance attribute and call `.save()`, Django generates the correct SQL `UPDATE`, but the in-memory Python attribute remains an `ExpressionNode` object.
If subsequent code tries to perform math or serialize `order.view_count` into JSON, it crashes with a `TypeError: Object of type Combined is not JSON serializable`.

#### The Senior Engineer Remediation:
Always call `refresh_from_db()` to reload the updated scalar value from the database:

```python
order = Order.objects.get(id=1)
order.view_count = F("view_count") + 1
order.save(update_fields=["view_count"])

# Reload scalar value from PostgreSQL
order.refresh_from_db(fields=["view_count"])
print(order.view_count)  # Output: 42 (int)
```

---

### Mistake 4: Memory Exhaustion (OOM) When Processing Large Datasets

#### The Flawed Approach:
Iterating directly over a QuerySet containing 100,000+ rows during a background export or migration:

```python
# tasks.py - CONTAINER CRASH HAZARD
for record in AuditLog.objects.filter(is_archived=False):
    archive_to_s3(record)
```

#### Why It Fails in Production:
As Django iterates over `AuditLog.objects.all()`, it caches **every single model instance** into `qs._result_cache`.
By row 50,000, Python heap memory swells from 80 MB to 1.5 GB. The Linux kernel's Out-Of-Memory (OOM) killer immediately terminates the Celery worker process with `SIGKILL (Exit Code 137)`.

#### The Senior Engineer Remediation:
Use `iterator(chunk_size=N)` to stream rows via server-side cursors without caching, maintaining flat $\mathcal{O}(1)$ memory:

```python
# PRODUCTION PATTERN: Streams 2,000 rows at a time with flat memory consumption
for record in AuditLog.objects.filter(is_archived=False).iterator(chunk_size=2000):
    archive_to_s3(record)
```

---

### Mistake 5: Multiple `.aggregate()` Queries Instead of Single-Pass Conditional Aggregations

#### The Flawed Approach:
Running multiple `.aggregate()` calls to generate dashboard summaries:

```python
# views.py - 4 REDUNDANT DATABASE NETWORK ROUNDTRIPS
paid_total = Order.objects.filter(status="PAID").aggregate(total=Sum("amount"))["total"]
refund_total = Order.objects.filter(status="REFUNDED").aggregate(total=Sum("amount"))["total"]
pending_count = Order.objects.filter(status="PENDING").count()
avg_order = Order.objects.all().aggregate(avg=Avg("amount"))["avg"]
```

#### Why It Fails in Production:
Each `.aggregate()` call executes an independent SQL query across the network socket. 4 queries $\times$ 30ms latency = 120ms spent waiting for database roundtrips.

#### The Senior Engineer Remediation:
Calculate all metrics in a single database roundtrip using **conditional aggregation** (`filter=Q(...)`):

```python
from django.db.models import Avg, Count, Q, Sum

# A SINGLE SQL QUERY: 1 roundtrip, ~32ms total latency
metrics = Order.objects.aggregate(
    paid_total=Sum("amount", filter=Q(status="PAID")),
    refund_total=Sum("amount", filter=Q(status="REFUNDED")),
    pending_count=Count("id", filter=Q(status="PENDING")),
    avg_order=Avg("amount"),
)
```

---

### Mistake 6: Aggregating in Python Loops Instead of SQL `annotate()`

#### The Flawed Approach:
Calculating child counts or sums in Python loops:

```python
# serializers.py - CLASSIC N+1 ACCUMULATOR
customers = Customer.objects.all()
data = []
for c in customers:
    data.append({
        "name": c.name,
        # Triggers a separate SELECT SUM(...) query for EVERY customer!
        "total_spent": c.orders.filter(status="PAID").aggregate(s=Sum("amount"))["s"] or 0
    })
```

#### Why It Fails in Production:
For 1,000 customers, this executes **1,001 SQL queries** (1 query to list customers + 1,000 queries inside the loop). The endpoint takes 5+ seconds and exhausts database connections.

#### The Senior Engineer Remediation:
Use `annotate()` with SQL `GROUP BY` to push the calculation into the database in a single query:

```python
from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce

# SINGLE QUERY with GROUP BY:
customers = Customer.objects.annotate(
    total_spent=Coalesce(
        Sum("orders__amount", filter=Q(orders__status="PAID")),
        decimal.Decimal("0.00"),
        output_field=DecimalField(),
    )
)
```

---

### Mistake 7: Full Model Instantiation When Only Scalar Values are Needed

#### The Flawed Approach:
Fetching full model objects when only 1 or 2 fields are required for a lookup table or dropdown:

```python
# views.py - 10X UNNECESSARY MEMORY & SERIALIZATION OVERHEAD
all_user_ids = [user.id for user in User.objects.all()]
```

#### Why It Fails in Production:
Instantiating full model objects populates Django internals (`_state`, field descriptors, foreign key caches). For 50,000 users, instantiating full model objects consumes **~120 MB RAM** and takes 800ms.

#### The Senior Engineer Remediation:
Use `values_list(..., flat=True)`:

```python
# FAST & LEAN: SELECT id FROM users; directly returns a list of integers (~3 MB RAM, ~40ms)
all_user_ids = list(User.objects.values_list("id", flat=True))
```

---

### Summary Checklist: Antipattern Detection Matrix

| # | Antipattern | Primary Consequence | Senior Remediation |
|---|-------------|---------------------|--------------------|
| 1 | `if qs:` or `len(qs)` | In-memory row loading; latency spike | Use `qs.exists()` or `qs.count()` |
| 2 | Python `stock -= 1` | Lost updates under concurrent writes | Use `F('stock') - 1` |
| 3 | Unrefreshed `F()` field | `CombinedExpression` serialization crash | Call `refresh_from_db()` |
| 4 | Large loop on QuerySet | OOM kill (`SIGKILL 137`) in containers | Use `iterator(chunk_size=N)` |
| 5 | Multiple `.aggregate()` | Network latency from multiple DB trips | Conditional `Sum(..., filter=Q())` |
| 6 | Aggregation in loop | N+1 database queries | Use `annotate()` with `Coalesce` |
| 7 | Full models for IDs | High memory & GC pressure | Use `values_list('id', flat=True)` |
