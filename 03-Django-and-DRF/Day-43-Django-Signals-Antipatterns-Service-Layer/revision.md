# Day 43: 5-Minute Rapid Revision — Signals vs Service Layer

---

## 1. Architectural Comparison Matrix

| Dimension | Django Signals (`django.dispatch`) | Service Layer Architecture |
|:---|:---|:---|
| **Control Flow** | Implicit, reactive, invisible ("Spooky action at a distance") | Explicit, imperative, deterministic |
| **Execution Model** | **Synchronous & blocking** in the HTTP request thread | Clean orchestration; offloads async jobs post-commit |
| **Transaction Safety** | **Vulnerable**: `post_save` fires before DB `COMMIT` | **Guaranteed**: Uses `transaction.atomic()` + `on_commit()` |
| **Bulk Operations** | **Bypassed completely** (`bulk_create`, `update`, `delete`) | Explicit batch methods handle side-effects reliably |
| **Testability** | Nightmare: creating 1 model triggers 5 cascaded signals | Trivial: pure functions, clean dependency injection |
| **Context Awareness** | Poor: only receives `instance` (no request/actor context) | Complete: receives typed command with actor, IP, trace ID |
| **Ideal For** | Generic third-party apps, caching plugins | **Core enterprise business domain logic** |

---

## 2. Transaction Boundary Disaster (The Rollback Hazard)

```
[ BEGIN TRANSACTION ]
   │
   ├─► Order.objects.create(...)  ──► SQL INSERT issued
   │         │
   │         └─► post_save fires synchronously!
   │                   │
   │                   ├─► Stripe API: Customer billed!  <── [IRREVERSIBLE SIDE-EFFECT]
   │                   └─► SendGrid: Receipt dispatched! <── [CUSTOMER NOTIFIED]
   │
   ├─► Inventory.deduct() ──► FAILS! (Out of stock / DB Constraint)
   │
[ ROLLBACK TRANSACTION ]
   │
   ▼
[RESULT]: Database has 0 orders, but Stripe charged $150 and email was sent!
```

---

## 3. The Production Service Layer Template

```python
# services/order_service.py
from dataclasses import dataclass
from decimal import Decimal
from django.db import transaction
from apps.orders.models import Order
from apps.inventory.services import InventoryService
from apps.notifications.tasks import send_receipt_task
from apps.outbox.models import OutboxEvent

@dataclass(frozen=True)
class CreateOrderCommand:
    user_id: int
    amount: Decimal
    items: list[tuple[int, int]]  # [(product_id, qty), ...]
    idempotency_key: str

class OrderService:
    @classmethod
    def create_order(cls, cmd: CreateOrderCommand) -> Order:
        # 1. Domain Invariant Checks
        if cmd.amount <= Decimal("0.00"):
            raise ValueError("Order amount must be positive.")

        # 2. Atomic Database Transaction
        with transaction.atomic():
            # Inventory reservation
            for product_id, qty in cmd.items:
                InventoryService.reserve(product_id=product_id, qty=qty)

            # Persist Aggregate
            order = Order.objects.create(
                user_id=cmd.user_id,
                amount=cmd.amount,
                status=Order.Status.CONFIRMED,
                idempotency_key=cmd.idempotency_key
            )

            # Transactional Outbox (Guarantees zero event loss)
            OutboxEvent.objects.create(
                aggregate_type="ORDER",
                aggregate_id=order.id,
                event_type="ORDER_CREATED",
                payload={"order_id": order.id, "amount": str(cmd.amount)}
            )

            # 3. Post-Commit Asynchronous Side Effects
            # Guaranteed to execute ONLY after successful COMMIT! Discarded on rollback.
            transaction.on_commit(lambda: send_receipt_task.delay(order.id))

        return order
```

---

## 4. Bulk Operations Rule

```python
# THESE NEVER FIRE pre_save / post_save SIGNALS:
User.objects.filter(is_active=False).update(status='ARCHIVED')
Order.objects.bulk_create([Order(...), Order(...)])
Order.objects.bulk_update(order_list, fields=['status'])
Order.objects.filter(status='EXPIRED').delete()
```
**Takeaway**: If an audit log or cache invalidation is implemented in a signal, bulk operations silently corrupt your state. Centralize bulk operations in the Service Layer!

---

## 5. When ARE Signals Appropriate?

Use signals **only** when all of the following hold true:
1. The sender cannot know about the receiver (reusable/third-party pluggable packages).
2. The receiver is purely observational, non-blocking, and non-critical.
3. Examples:
   - Django auth framework: `user_logged_in`, `user_login_failed` (security metrics).
   - Pluggable cache managers: evicting HTML fragment cache on third-party model save.
   - Deleting local files from disk when a model is deleted (`post_delete`).

---

## 6. Ten-Second Interview Defense Checklist

1. *Why did your Celery task fail with `ObjectDoesNotExist`?* -> Signal queued task before DB transaction committed; worker picked up task before PostgreSQL wrote row. Fix: `transaction.on_commit(lambda: task.delay(id))`.
2. *Are Django signals asynchronous?* -> **No.** They execute synchronously on the active thread, blocking the HTTP response.
3. *Why did `bulk_create` skip our audit log?* -> Django compiles bulk operations to a single SQL query, deliberately skipping Python model loops and signals for performance.
4. *How does the Service Layer prevent the Dual-Write Problem?* -> Writes domain entity and outbox event in the same atomic SQL transaction; worker publishes to Kafka post-commit with at-least-once guarantee.
