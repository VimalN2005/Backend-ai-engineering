# Day 35 Rapid Revision: ORM Relations & N+1 Optimization

A high-yield, 5-minute reference guide covering relational descriptors, `select_related`, `prefetch_related`, `Prefetch` objects, and query budget auditing.

---

## 1. Quick Decision Matrix: `select_related` vs `prefetch_related`

| Relational Access Pattern | Cardinality | Recommended ORM Method | Under The Hood |
|:--------------------------|:------------|:-----------------------|:---------------|
| `order.customer.email` | Many-to-One | `select_related('customer')` | SQL `INNER JOIN` (1 query) |
| `user.profile.avatar` | One-to-One | `select_related('profile')` | SQL `LEFT JOIN` (1 query) |
| `customer.orders.all()` | One-to-Many | `prefetch_related('orders')` | Bulk `WHERE IN` (2 queries) |
| `team.members.all()` | Many-to-Many | `prefetch_related('members')` | Bulk `WHERE IN` (2 queries) |
| `order.items (filtered)` | Filtered 1-to-N | `Prefetch('items', queryset=..., to_attr='...')` | Bulk filtered `WHERE IN` (2 queries) |
| Deep Graph (Order->Customer->Org) | Multi-Level Single | `select_related('customer__organization')` | Chained SQL `INNER JOIN` (1 query) |

---

## 2. The 5 Non-Negotiable Relational Laws

1. **The Single-Valued Join Law**: Use `select_related` **only** on `ForeignKey` and `OneToOneField`. Never attempt `select_related` on `ManyToManyField` or reverse foreign keys.
2. **The Multi-Valued Batch Law**: Use `prefetch_related` for `ManyToManyField` and reverse foreign keys to prevent Cartesian product row explosions.
3. **The Prefetch Invalidation Law**: Never call `.filter()`, `.exclude()`, or `.count()` on a prefetched related manager inside a loop. It bypasses `_prefetched_objects_cache` and triggers an N+1 query.
4. **The `to_attr` Isolation Law**: Always specify `to_attr` when using custom `Prefetch(queryset=...)`. It attaches the filtered result as a pure Python list, avoiding data masking and query execution.
5. **The Deletion Safeguard Law**: Never use `on_delete=models.CASCADE` on core business, tenant, or financial entities. Always enforce `on_delete=models.PROTECT`.

---

## 3. Core Enterprise Code Templates

### 1. Optimal Relational Query with Deep Join & Prefetch:
```python
from django.db.models import Prefetch

# EXACTLY 2 SQL QUERIES for the entire complex object graph:
orders = Order.objects.filter(status="PAID").select_related(
    "customer__organization"  # Query 1: Orders joined with Customer and Organization
).prefetch_related(
    Prefetch(                 # Query 2: Active items filtered and stored in a list
        "items",
        queryset=OrderItem.objects.filter(is_refunded=False).order_by("-price"),
        to_attr="active_items",
    )
)

for order in orders:
    print(order.customer.organization.name)  # 0 SQL queries (read from join)
    for item in order.active_items:          # 0 SQL queries (read from memory list)
        print(item.product_name)
```

### 2. CI/CD Query Budget Assertion:
```python
from django.test import TestCase

class OrderPerformanceTest(TestCase):
    def test_order_list_query_budget(self):
        # Asserts that regardless of whether there are 5 or 500 orders,
        # the endpoint executes EXACTLY 2 queries:
        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/orders/")
            self.assertEqual(response.status_code, 200)
```

---

## 4. `on_delete` Policies Reference Table

| Policy | Behavior on Parent Deletion | Ideal Target |
|:-------|:----------------------------|:-------------|
| `models.PROTECT` | Raises `ProtectedError`; blocks deletion | Invoices, Orders, Accounts, Ledger |
| `models.CASCADE` | Deletes referencing child records | Ephemeral child items (OrderItem, LineItem) |
| `models.RESTRICT` | Blocks deletion unless cascade deletes child | Hierarchical tree structures |
| `models.SET_NULL` | Sets foreign key to `NULL` (requires `null=True`) | Optional assignments (e.g. Assigned Agent) |
| `models.SET_DEFAULT`| Sets foreign key to default value | Fallback categories / System default user |
| `models.DO_NOTHING` | Takes no action; risks DB integrity error | Unmanaged legacy tables |
