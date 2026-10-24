# Day 34 Rapid Revision: Django ORM Queries, Filtering & Aggregations

A high-yield, 5-minute reference guide covering QuerySet lazy evaluation, caching, atomic `F()` expressions, single-pass aggregations, and query optimization.

---

## 1. QuerySet Evaluation Trigger Cheat Sheet

| Operation | Executes SQL? | Caches in `_result_cache`? | Optimal Use Case |
|:----------|:--------------|:---------------------------|:-----------------|
| `qs.filter(...)` | ❌ No | ❌ No | Query construction & chaining |
| `qs[0:10]` | ❌ No | ❌ No | SQL `LIMIT` / `OFFSET` pagination |
| `qs.exists()` | ✅ Yes | ❌ No (returns bool) | Checking record existence (`LIMIT 1`) |
| `qs.count()` | ✅ Yes | ❌ No (returns int) | Checking record totals (`COUNT(*)`) |
| `for x in qs:` | ✅ Yes | ✅ Yes (all rows) | Iterating when data is required |
| `list(qs)` | ✅ Yes | ✅ Yes (all rows) | Converting to Python list in memory |
| `len(qs)` | ✅ Yes | ✅ Yes (all rows) | Measuring length after iteration |
| `qs.iterator()` | ✅ Yes | ❌ No (streams batches) | Streaming 10k+ rows with flat memory |

---

## 2. The 5 Non-Negotiable Query Invariant Rules

1. **The Existence Invariant**: Never use `if qs:` or `len(qs)` to check existence if you don't plan to iterate over the items. Use `qs.exists()`.
2. **The Atomic Concurrency Invariant**: Never update numbers/counters with in-memory Python math (`x += 1`). Use `update(x=F('x') + 1)` to prevent lost updates under concurrency.
3. **The F-Expression Refresh Invariant**: If you assign an `F()` expression to an instance (`inst.x = F('x') + 1`), always call `inst.refresh_from_db()` before reading `inst.x` in subsequent Python code.
4. **The Single-Pass Aggregation Invariant**: Never execute multiple `.aggregate()` calls across the same table. Compute all category totals in a single roundtrip via `Sum('col', filter=Q(...))`.
5. **The Memory Streaming Invariant**: Always use `iterator(chunk_size=2000)` when iterating over massive datasets to bypass `_result_cache` and prevent container OOM crashes.

---

## 3. Core Enterprise Code Templates

### 1. Atomic Counter Increment (Concurrency Safe):
```python
from django.db.models import F

# Atomic SQL: UPDATE inventory SET stock = stock - 1 WHERE id = 1 AND stock > 0;
updated = Product.objects.filter(id=1, stock__gt=0).update(stock=F("stock") - 1)
if not updated:
    raise OutOfStockError("Item unavailable.")
```

### 2. Single-Pass Multi-Metric Aggregation:
```python
from django.db.models import Avg, Count, Q, Sum

# Single SQL Query returning multiple metrics:
kpis = Order.objects.aggregate(
    gross_revenue=Sum("amount", filter=Q(status="PAID")),
    refund_volume=Sum("amount", filter=Q(status="REFUNDED")),
    paid_count=Count("id", filter=Q(status="PAID")),
    avg_order_value=Avg("amount", filter=Q(status="PAID")),
)
```

### 3. Conditional Row Annotation (Case / When):
```python
from django.db.models import Case, CharField, Value, When

users = User.objects.annotate(
    segment=Case(
        When(lifetime_spend__gte=1000, then=Value("VIP")),
        When(lifetime_spend__gte=250, then=Value("REGULAR")),
        default=Value("NEW"),
        output_field=CharField(),
    )
)
```

### 4. Memory-Optimized Streaming & Projections:
```python
# Scalar list without model instantiation
active_ids = list(User.objects.filter(is_active=True).values_list("id", flat=True))

# Stream 500,000 rows without memory bloat
for record in LargeTable.objects.all().iterator(chunk_size=2000):
    process_row(record)
```

---

## 4. Query Count Budgeting in Tests

```python
from django.test import TestCase

class OrderPerformanceTest(TestCase):
    def test_order_list_query_budget(self):
        # Asserts that the endpoint executes EXACTLY 2 queries regardless of row count
        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/orders/")
            self.assertEqual(response.status_code, 200)
```
