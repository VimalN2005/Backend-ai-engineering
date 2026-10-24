# Day 34: Django ORM Queries, Filtering, QuerySets & Complex Aggregations

The **Django QuerySet** is one of the most powerful and sophisticated Object-Relational Mapping (ORM) abstractions in modern software engineering. It allows developers to construct, chain, slice, and compose complex SQL queries using expressive, type-safe Python syntax while abstracting database engine dialect nuances.

However, treating the ORM as a black box without understanding its **compilation pipeline, lazy evaluation model, query caching mechanics, and SQL translations** is the number one cause of production performance bottlenecks—leading to Out-Of-Memory (OOM) container crashes, connection pool exhaustion, duplicate subqueries, and catastrophic update race conditions.

---

## 1. The Django QuerySet Architecture & Compilation Pipeline

A `QuerySet` is not a database query result; it is an **immutable, declarative representation of a SQL query** waiting to be compiled and executed.

```
+-----------------------------------------------------------------------------------+
|                        DJANGO QUERYSET COMPILATION PIPELINE                       |
+-----------------------------------------------------------------------------------+
| Python ORM Layer                                                                  |
|   qs = Order.objects.filter(status='PAID').exclude(amount=0).order_by('-created') |
|                                                                                   |
| 1. QuerySet Cloning                                                               |
|    -> Every filter(), exclude(), or order_by() returns a NEW QuerySet clone.      |
|    -> The original QuerySet remains unaltered (immutability).                     |
|                                                                                   |
| 2. django.db.models.sql.query.Query                                               |
|    -> Constructs Abstract Syntax Tree (AST) of where nodes, joins, and aggregates |
|                                                                                   |
| 3. SQLCompiler (Engine Dialect Backend: PostgreSQL / MySQL / SQLite)              |
|    -> Compiles AST into engine-specific SQL with parameterized placeholders (%s)  |
|                                                                                   |
| 4. Database Execution & Cursor Retrieval                                          |
|    -> Executes via psycopg / db-api -> Fetches raw tuples -> Instantiates Models  |
+-----------------------------------------------------------------------------------+
```

### QuerySet Immutability & Chaining:
Whenever you chain a method on a QuerySet, Django **does not mutate** the existing object. It invokes `self._clone()`, creating a lightweight copy with updated query tree nodes:

```python
base_qs = Order.objects.filter(tenant_id=42)  # Generates clone 1 (0 DB queries)
paid_qs = base_qs.filter(status="PAID")       # Generates clone 2 (0 DB queries)
refunded_qs = base_qs.filter(status="REFUND") # Generates clone 3 (0 DB queries)
```
This immutability allows building reusable, modular query pipelines across repository managers without side-effects.

---

## 2. Lazy Evaluation Mechanics

A fundamental performance characteristic of Django ORM: **QuerySets are lazy**. Creating a QuerySet does not touch the database network socket. The query is executed **only when data is evaluated**.

```
+-----------------------------------------------------------------------------------+
|                           WHAT TRIGGERS QUERY EXECUTION?                          |
+-----------------------------------------------------------------------------------+
| Operation                 | Hits Database? | Behavior & Memory Impact             |
+---------------------------+----------------+--------------------------------------+
| qs = Order.objects.all()  | ❌ NO           | Instant; creates QuerySet object     |
| qs.filter(status='PAID')  | ❌ NO           | Returns clone; 0 queries             |
| qs[:10]                   | ❌ NO           | Slicing without step returns clone   |
| for order in qs:          | ✅ YES          | Executes SQL, populates _result_cache|
| list(qs)                  | ✅ YES          | Fetches all rows into memory list    |
| len(qs)                   | ✅ YES          | Fetches all rows and measures length |
| bool(qs) / if qs:         | ✅ YES          | Fetches rows to check truthiness     |
| qs.count()                | ✅ YES (OPTIMAL)| Executes SELECT COUNT(*) in SQL      |
| qs.exists()               | ✅ YES (OPTIMAL)| Executes SELECT 1 ... LIMIT 1 in SQL |
+---------------------------+----------------+--------------------------------------+
```

### Slicing vs Indexing:
- **`qs[0:10]`**: Lazy. Translates directly to SQL `LIMIT 10 OFFSET 0`.
- **`qs[0]`**: Evaluates immediately. Translates to SQL `LIMIT 1` and returns a model instance.
- **`qs[0:10:2]`**: Evaluates immediately. Step slicing requires in-memory Python list evaluation!

---

## 3. QuerySet Caching & Memory Overhead: `_result_cache`

When a QuerySet is evaluated for the first time, Django stores the fetched model instances in an internal in-memory attribute: **`qs._result_cache`**.

```
                           First Evaluation: for item in qs:
                                          │
                                          ▼
                            SELECT * FROM orders WHERE ...
                                          │
                                          ▼
                      Instantiate 50,000 Python Model Objects
                                          │
                                          ▼
                             Store in qs._result_cache
                                          │
          ┌───────────────────────────────┴───────────────────────────────┐
          │                                                               │
          ▼                                                               ▼
Subsequent: for item in qs:                               Subsequent: len(qs)
-> Reads directly from RAM                                -> Reads len(_result_cache)
-> 0 Database Queries                                     -> 0 Database Queries
```

### The Caching Double-Edged Sword:
1. **The Optimization**: Re-iterating over the same QuerySet variable or checking `len(qs)` after iteration does not re-query the database.
2. **The Memory Disaster**: If a query returns 100,000 rows, each row is instantiated into a full Django model instance (with `__dict__`, field tracking, and signal handlers). A 100,000-row table that takes 15 MB in PostgreSQL can consume **over 400 MB of Python heap RAM**, triggering an Out-Of-Memory (OOM) container kill!

---

## 4. Core Query Methods & Performance Comparisons

### `count()` vs `len()`:
```python
# ANTIPATTERN: Fetches 50,000 model instances from DB into Python RAM just to count them!
if len(Order.objects.filter(status="PENDING")) > 0:
    pass

# PRODUCTION PATTERN: Executes SELECT COUNT(*) in PostgreSQL (transfers 8 bytes over network)
if Order.objects.filter(status="PENDING").count() > 0:
    pass
```

### `exists()` vs `bool()`:
```python
# ANTIPATTERN: Fetches full model objects into memory just to check existence
if Order.objects.filter(email="user@domain.com"):
    pass

# PRODUCTION PATTERN: Translates to SELECT (1) AS "a" FROM orders WHERE email = ... LIMIT 1
if Order.objects.filter(email="user@domain.com").exists():
    pass
```

### `get()` vs `filter().first()`:
- **`get(**kwargs)`**: Expects **exactly one** record.
  - Raises `Model.DoesNotExist` if 0 rows matched.
  - Raises `Model.MultipleObjectsReturned` if $>1$ rows matched.
- **`filter(**kwargs).first()`**: Returns the first record or `None`.
  - Appends `LIMIT 1` in SQL.
  - Never raises an exception. Ideal for optional lookups.

---

## 5. Field Lookups & PostgreSQL Dialect Translations

Django uses the double-underscore (`__`) syntax for field lookups:

| Django Lookup | SQL Translation (Postgres) | Index Considerations |
|:--------------|:---------------------------|:---------------------|
| `field__exact=val` | `field = 'val'` | B-Tree index optimal |
| `field__iexact=val` | `UPPER(field) = UPPER('val')` | Requires functional index `Index(Lower('field'))` |
| `field__contains=val` | `field LIKE '%val%'` | Standard B-tree cannot be used! (Full table scan) |
| `field__icontains=val` | `field ILIKE '%val%'` | Requires `pg_trgm` GIN index for search |
| `field__in=[1, 2, 3]` | `field IN (1, 2, 3)` | B-Tree index optimal |
| `field__gt=val` | `field > val` | B-Tree range scan |
| `field__gte=val` | `field >= val` | B-Tree range scan |
| `field__isnull=True` | `field IS NULL` | B-Tree index (Postgres indexes NULLs) |
| `field__startswith=val` | `field LIKE 'val%'` | Can use B-Tree with `varchar_pattern_ops` |

---

## 6. Complex Lookups with `Q` Objects

In standard Django, passing multiple arguments to `.filter()` combines them with SQL `AND`:
```python
# SQL: WHERE status = 'PAID' AND total_amount > 100
Order.objects.filter(status="PAID", total_amount__gt=100)
```

To execute SQL `OR` (`|`) or SQL `NOT` (`~`), you must use **`Q` objects** (`django.db.models.Q`).

### 1. `OR` & `NOT` Combinations:
```python
from django.db.models import Q

# SQL: WHERE (status = 'FAILED' OR status = 'CANCELLED') AND NOT (is_internal = TRUE)
Order.objects.filter(
    (Q(status="FAILED") | Q(status="CANCELLED")) & ~Q(is_internal=True)
)
```

### 2. Dynamic `Q` Composition with `functools.reduce`:
When building search filters with arbitrary user parameters:
```python
import functools
import operator
from django.db.models import Q

search_terms = ["python", "django", "postgres"]
# Build: Q(title__icontains="python") | Q(title__icontains="django") | Q(title__icontains="postgres")
query_filter = functools.reduce(
    operator.or_,
    (Q(title__icontains=term) for term in search_terms)
)
results = Article.objects.filter(query_filter)
```

---

## 7. Race Condition Prevention with `F` Expressions

The most dangerous concurrency bug in backend systems is the **Read-Modify-Write Race Condition**.

### The Vulnerable Pattern:
```python
# THREAD A and THREAD B run simultaneously when inventory = 10:
product = Product.objects.get(id=1)     # Thread A reads inventory: 10
                                        # Thread B reads inventory: 10
product.inventory -= 1                  # Thread A calculates 9
                                        # Thread B calculates 9
product.save()                          # Thread A writes 9
                                        # Thread B writes 9 (LOST UPDATE: 2 sold, inventory is 9 instead of 8!)
```

### The `F` Expression Solution:
`F` expressions instruct Django to perform the calculation **directly inside the database engine** using the current table column value:

```python
from django.db.models import F

# Generates SQL: UPDATE products SET inventory = inventory - 1 WHERE id = 1;
Product.objects.filter(id=1).update(inventory=F("inventory") - 1)
```

```
+-----------------------------------------------------------------------------------+
|                        PYTHON READ-MODIFY-WRITE vs SQL F()                        |
+-----------------------------------------------------------------------------------+
| Python In-Memory Update (RACE HAZARD)  | SQL F() Expression (ATOMIC & SAFE)       |
+----------------------------------------+------------------------------------------+
| 1. SELECT inventory FROM products      | UPDATE products                          |
| 2. Python computes: 10 - 1 = 9         | SET inventory = inventory - 1            |
| 3. UPDATE products SET inventory = 9   | WHERE id = 1;                            |
| (Exposed to concurrent overwrite!)     | (Handled atomically via row lock in DB!) |
+----------------------------------------+------------------------------------------+
```

### Important `F` Invariant:
After updating an instance via `instance.inventory = F('inventory') - 1; instance.save()`, the Python attribute contains an `F()` expression object, **not an integer**! You must call `instance.refresh_from_db()` before reading `instance.inventory` in subsequent Python logic.

---

## 8. Aggregation vs Annotation

The difference between `aggregate()` and `annotate()` is the most common interview question on the Django ORM:

```
+-----------------------------------------------------------------------------------+
|                            AGGREGATE vs ANNOTATE                                  |
+-----------------------------------------------------------------------------------+
| Feature            | aggregate()                     | annotate()                 |
+--------------------+---------------------------------+----------------------------+
| Return Type        | Python dictionary               | QuerySet of Model objects  |
| Granularity        | Whole table / filtered set      | Per row / per group        |
| SQL Equivalent     | SELECT AVG(price) FROM products | SELECT ..., AVG(...) ...   |
|                    | (Single summary scalar)         | GROUP BY id                |
| Chaining           | Terminal; ends QuerySet         | Non-terminal; chainable    |
+--------------------+---------------------------------+----------------------------+
```

### 1. `aggregate()` Example:
```python
from django.db.models import Avg, Max, Sum

# Returns: {'total_revenue': Decimal('150420.00'), 'avg_order': Decimal('85.50')}
metrics = Order.objects.filter(status="PAID").aggregate(
    total_revenue=Sum("total_amount"),
    avg_order=Avg("total_amount"),
)
```

### 2. `annotate()` Example:
```python
from django.db.models import Count, Sum

# Annotates every customer with their total order count and total spend
customers = Customer.objects.annotate(
    order_count=Count("orders"),
    total_spend=Sum("orders__total_amount"),
).filter(order_count__gt=5)
```

---

## 9. Advanced Query Expressions: `Case`, `When` & Conditional Aggregation

Django's `Case` and `When` expressions allow writing SQL `CASE WHEN ... THEN ... ELSE ... END` logic directly in QuerySets.

### 1. Conditional Value Annotation:
```python
from django.db.models import Case, CharField, Value, When

# Categorize customers into loyalty tiers in a single SQL query
customers = Customer.objects.annotate(
    tier=Case(
        When(lifetime_spend__gte=10000, then=Value("PLATINUM")),
        When(lifetime_spend__gte=5000, then=Value("GOLD")),
        When(lifetime_spend__gte=1000, then=Value("SILVER")),
        default=Value("BRONZE"),
        output_field=CharField(),
    )
)
```

### 2. Conditional Aggregation (Filter within Aggregate):
Instead of running multiple queries, calculate distinct category totals in a single database roundtrip:
```python
from django.db.models import Count, Q, Sum

metrics = Order.objects.aggregate(
    paid_total=Sum("total_amount", filter=Q(status="PAID")),
    refunded_total=Sum("total_amount", filter=Q(status="REFUNDED")),
    pending_count=Count("id", filter=Q(status="PENDING")),
)
```

---

## 10. Optimized Projections: `values()` and `values_list()`

When you only need specific columns (e.g. generating an export CSV or computing an in-memory map), instantiating full Django model instances wastes CPU cycles and RAM.

```python
# FULL INSTANTIATION: 100,000 rows -> 100,000 Model instances (~400 MB RAM)
users = User.objects.all()

# DICTIONARY PROJECTION: 100,000 dicts with only requested keys (~45 MB RAM)
user_data = User.objects.values("id", "email", "created_at")

# TUPLE PROJECTION: 100,000 tuples (~25 MB RAM)
user_tuples = User.objects.values_list("id", "email")

# FLAT LIST PROJECTION: 100,000 scalar IDs in a single Python list (~8 MB RAM)
user_ids = User.objects.values_list("id", flat=True)
```

---

## 11. Chunking & Memory Streaming: `iterator()`

When migrating data, processing batch ETL pipelines, or exporting millions of rows, regular QuerySets cache every fetched row into `_result_cache`.

### The `iterator()` Solution:
```python
# Memory footprint: FLAT O(1) regardless of whether table has 1,000 or 10,000,000 rows!
for order in Order.objects.filter(status="PROCESSED").iterator(chunk_size=2000):
    process_order(order)
```

### How `iterator(chunk_size=N)` Works:
1. Bypasses `_result_cache` completely.
2. Uses server-side database cursors in PostgreSQL to fetch batches of $N$ rows at a time over the network socket.
3. Once each batch is yielded and garbage-collected in Python, RAM remains flat.

---

## 12. Correlated Subqueries: `Subquery` & `OuterRef`

Correlated subqueries allow querying data from a related table without performing expensive `JOIN` operations that multiply row counts.

```python
from django.db.models import OuterRef, Subquery

# Find the latest comment content for each article
latest_comments = Comment.objects.filter(
    article=OuterRef("pk")
).order_by("-created_at")

articles = Article.objects.annotate(
    latest_comment_text=Subquery(latest_comments.values("content")[:1])
)
```

### How `OuterRef` Works:
`OuterRef("pk")` references the primary key of the **outer** query (`Article`). For each article row, PostgreSQL executes the subquery targeting that specific article and extracts the scalar value.

---

## 13. Query Count Auditing & SQL Inspection

To diagnose N+1 queries and audit generated SQL:

### 1. View Generated SQL String:
```python
qs = Order.objects.filter(status="PAID")
print(qs.query)
# Outputs compiled SQL: SELECT "orders"."id", "orders"."total_amount" FROM "orders" WHERE ...
```

### 2. Inspect Query Execution Count in Tests:
```python
from django.test import TestCase

class OrderApiTest(TestCase):
    def test_order_listing_query_count(self):
        # Enforces that an endpoint takes EXACTLY 2 SQL queries regardless of item count
        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/orders/")
            self.assertEqual(response.status_code, 200)
```

### 3. Log Queries at Runtime:
```python
from django.db import connection
print(f"Executed {len(connection.queries)} queries.")
for q in connection.queries:
    print(f"[{q['time']}s] {q['sql']}")
```

---

## 14. 7 Golden Invariant Rules for Enterprise ORM Queries

1. **Never use `len(qs)` or `if qs:` to check existence**: Use `qs.exists()` or `qs.count()`.
2. **Always use `F()` expressions for concurrent updates**: Prevent lost updates on inventory, credits, and counters.
3. **Always call `refresh_from_db()` after saving an `F()` expression**: Avoid keeping an unresolved expression object in memory.
4. **Use `iterator(chunk_size=...)` for large datasets**: Avoid OOM container crashes on large exports and batch jobs.
5. **Use `values()` / `values_list(flat=True)` when model instances are unneeded**: Reduce serialization latency and memory bloat by 5x–10x.
6. **Use conditional aggregation (`filter=Q(...)`)**: Compute multiple metrics in a single SQL query instead of running 5 separate `.aggregate()` calls.
7. **Always enforce `assertNumQueries` in integration tests**: Prevent accidental N+1 query regressions from reaching production.

---

## 15. Production ORM Method Decision Matrix

| Goal | Recommended ORM Method | SQL Translation | Memory Impact |
|:-----|:-----------------------|:----------------|:--------------|
| Check if any rows exist | `qs.exists()` | `SELECT 1 ... LIMIT 1` | Zero |
| Count total matching rows | `qs.count()` | `SELECT COUNT(*)` | Zero |
| Fetch 1 record safely | `qs.filter(...).first()` | `SELECT ... LIMIT 1` | 1 instance |
| Fetch scalar IDs | `qs.values_list('id', flat=True)` | `SELECT id FROM ...` | Array of ints |
| Atomic counter increment | `qs.update(c=F('c') + 1)` | `UPDATE ... SET c = c + 1` | Zero |
| Stream 1,000,000 rows | `qs.iterator(chunk_size=2000)`| Server-side cursor | Flat $\mathcal{O}(1)$ |
| Multi-metric reporting | `qs.aggregate(a=Sum(..., filter=Q(...)))` | `SELECT SUM(CASE ...)` | Single dict |
