# Day 34: Top 5 Senior Backend Interview Questions — Django ORM Queries & Aggregations

---

### Question 1: What is the internal lifecycle of a Django QuerySet? Explain the role of `_result_cache`, when SQL compilation occurs, and why `iterator()` is mandatory for multi-gigabyte queries.

#### Architectural Answer:
A Django `QuerySet` is an immutable, lazy query builder. Constructing a QuerySet (e.g. `Order.objects.filter(status='PAID')`) does not issue a database network query.

#### The QuerySet Lifecycle & SQL Compilation:
1. **Query AST Construction**: Calling `.filter()`, `.exclude()`, or `.order_by()` invokes `self._clone()`, creating a new QuerySet instance whose internal `django.db.models.sql.query.Query` Abstract Syntax Tree (AST) is appended with `WhereNode` or `OrderBy` objects.
2. **Evaluation Trigger**: When evaluated (via `for item in qs:`, `list(qs)`, `len(qs)`, or pickling), Django invokes its database compiler (`SQLCompiler`).
3. **Compilation & Execution**: The compiler transforms the AST into an engine-specific SQL string (`SELECT ... FROM ... WHERE ...`), executes it via DB-API (e.g., `psycopg2`), and retrieves rows from the database socket.
4. **Model Instantiation**: Each raw SQL row tuple is instantiated into a full Python model instance.

#### The `_result_cache` Mechanism:
Upon first evaluation, Django stores the entire list of instantiated model instances inside `qs._result_cache`:
- If code iterates over `qs` a second time, or checks `len(qs)`, Django reads directly from `_result_cache` without issuing another database query.
- **The Memory Problem**: If the query matches 500,000 rows, all 500,000 model objects are kept in memory simultaneously. A 30 MB database result set expands into **over 1.5 GB of Python heap RAM**, triggering an Out-Of-Memory (OOM) kill.

#### Why `iterator()` is Mandatory:
Calling `qs.iterator(chunk_size=2000)` explicitly:
1. Bypasses `_result_cache` entirely.
2. Uses server-side cursors in PostgreSQL (`DECLARE name CURSOR FOR ...`).
3. Fetches rows in batches of 2,000 across the network socket, instantiates models for that batch, yields them, and allows the Python garbage collector to reclaim them immediately. Memory consumption remains strictly flat $\mathcal{O}(1)$.

---

### Question 2: How do `F` expressions prevent race conditions at the database level, and what is the exact mechanism by which Django executes `update(inventory=F('inventory') - 1)` compared to Python-level mutations?

#### Architectural Answer:

#### The Python Read-Modify-Write Flaw:
When an engineer writes:
```python
product = Product.objects.get(id=1)
product.inventory -= 1
product.save()
```
The application executes two separate SQL queries across the network:
1. `SELECT inventory FROM products WHERE id = 1;` (returns e.g. `10`)
2. Python executes in-memory arithmetic: `10 - 1 = 9`
3. `UPDATE products SET inventory = 9 WHERE id = 1;`

If two concurrent Gunicorn workers execute this block simultaneously, both read `10`, both calculate `9`, and both write `9`. Two items were sold, but inventory only decreased by 1 (**Lost Update Anomaly**).

#### The `F()` Expression Mechanism:
An `F()` expression represents the SQL column reference itself rather than a Python scalar value.
```python
Product.objects.filter(id=1).update(inventory=F("inventory") - 1)
```
Django compiles this into a **single atomic SQL statement**:
```sql
UPDATE products 
SET inventory = inventory - 1 
WHERE id = 1;
```

#### Database Concurrency Guarantee:
PostgreSQL acquires an exclusive row-level write lock (`FOR UPDATE`) on the target row during the execution of the `UPDATE` statement.
If 100 concurrent requests execute this update simultaneously, PostgreSQL serializes them at the disk engine level. Each decrement is applied atomically to the current disk state, eliminating the lost update anomaly with zero race conditions.

---

### Question 3: What is the fundamental difference between `aggregate()` and `annotate()` in terms of SQL generation, return types, and grouping? How do you perform conditional aggregation in a single query?

#### Architectural Answer:

| Feature | `aggregate()` | `annotate()` |
|:--------|:--------------|:-------------|
| **Return Value** | Python `dict` of scalar values | `QuerySet` of enriched Model instances |
| **SQL Scope** | Entire matching dataset (Terminal) | Per row / per group (Chainable) |
| **SQL Translation** | `SELECT SUM(amount) FROM ...` | `SELECT ..., SUM(amount) ... GROUP BY ...` |
| **Chaining** | No (ends query building) | Yes (can filter/order on annotated columns) |

#### Single-Pass Conditional Aggregation:
In reporting dashboards, calculating metrics across distinct subsets often leads to multiple redundant database queries:
```python
# ANTIPATTERN: 3 separate database network roundtrips
paid = Order.objects.filter(status="PAID").aggregate(s=Sum("amount"))
refunded = Order.objects.filter(status="REFUNDED").aggregate(s=Sum("amount"))
pending = Order.objects.filter(status="PENDING").count()
```

#### Senior Enterprise Pattern:
Use the `filter` keyword argument inside aggregate expressions (`filter=Q(...)`):
```python
metrics = Order.objects.aggregate(
    paid_total=Sum("amount", filter=Q(status="PAID")),
    refunded_total=Sum("amount", filter=Q(status="REFUNDED")),
    pending_count=Count("id", filter=Q(status="PENDING")),
)
```
Django compiles this into a **single SQL query** using SQL `CASE WHEN`:
```sql
SELECT 
    SUM(CASE WHEN status = 'PAID' THEN amount ELSE NULL END) AS paid_total,
    SUM(CASE WHEN status = 'REFUNDED' THEN amount ELSE NULL END) AS refunded_total,
    COUNT(CASE WHEN status = 'PENDING' THEN id ELSE NULL END) AS pending_count
FROM orders;
```
This reduces database connection usage by 66% and slashes network latency.

---

### Question 4: Compare `qs.exists()`, `qs.count()`, `len(qs)`, and `if qs:`. What SQL queries are generated for each, and when does Django reuse cached results?

#### Architectural Answer:

```
+-----------------------------------------------------------------------------------------------+
|                                QUERY EVALUATION & CACHING AUDIT                               |
+-----------------------------------------------------------------------------------------------+
| Method         | If _result_cache is Empty           | If _result_cache is Already Populated  |
+----------------+-------------------------------------+----------------------------------------+
| qs.exists()    | SELECT (1) AS "a" ... LIMIT 1       | Returns bool(_result_cache) (0 SQL)    |
| qs.count()     | SELECT COUNT(*) ...                 | Returns len(_result_cache) (0 SQL)     |
| len(qs)        | SELECT * ... (Loads ALL rows to RAM)| Returns len(_result_cache) (0 SQL)     |
| if qs:         | SELECT * ... (Loads ALL rows to RAM)| Returns bool(_result_cache) (0 SQL)    |
+----------------+-------------------------------------+----------------------------------------+
```

#### Decision Rules:
1. **Never use `len(qs)` or `if qs:` if you haven't already iterated over the query**: It executes `SELECT *` and constructs model instances for all matching rows.
2. **If you will iterate over the results anyway**:
   ```python
   # CORRECT: Evaluates and caches in one step
   if qs:
       for item in qs:  # Reuses _result_cache! No extra query!
           process(item)
   ```
   Calling `if qs.exists(): for item in qs:` would issue **two separate queries** (`SELECT 1 ... LIMIT 1` followed by `SELECT *`).
3. **If you only need to know if a record exists**: Use `qs.exists()`. PostgreSQL stops scanning the index at the very first matching row (`LIMIT 1`).

---

### Question 5: How does Django implement correlated subqueries using `Subquery` and `OuterRef`? When should you use a correlated subquery over a standard `select_related` or `JOIN`?

#### Architectural Answer:

#### Mechanism:
Django's `Subquery` and `OuterRef` allow referencing columns from the outer query inside an inner subquery.
```python
from django.db.models import OuterRef, Subquery

# Annotate each article with the title of its latest comment
latest_comment = Comment.objects.filter(
    article=OuterRef("pk")
).order_by("-created_at")

articles = Article.objects.annotate(
    latest_comment_text=Subquery(latest_comment.values("content")[:1])
)
```

#### Compiled SQL:
```sql
SELECT 
    articles.id,
    articles.title,
    (
        SELECT content FROM comments 
        WHERE comments.article_id = articles.id 
        ORDER BY created_at DESC 
        LIMIT 1
    ) AS latest_comment_text
FROM articles;
```

#### When to Use `Subquery` over `JOIN`:
1. **Row Count Multiplication**: Performing a `LEFT OUTER JOIN` on a one-to-many relationship (e.g. `Article` to `Comment`) duplicates article rows in the SQL result set for every matching comment, causing memory bloat and requiring costly `DISTINCT` operations.
2. **Top-1 / Latest Related Item**: SQL joins cannot trivially select "only the latest row per parent". A correlated subquery with `LIMIT 1` cleanly returns a scalar value per parent row.
3. **Aggregation on Multiple Related Tables**: Joining two one-to-many tables simultaneously creates a **Cartesian product**. Using subqueries avoids cartesian explosion and keeps query execution linear.
