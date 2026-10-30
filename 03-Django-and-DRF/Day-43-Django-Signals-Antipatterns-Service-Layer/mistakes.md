# Day 43: Common Antipatterns in Django Signals & Service Layer Architecture

This guide examines 7 critical production antipatterns encountered when utilizing Django signals and designing business service layers. Each entry breaks down the underlying architectural failure mechanism, illustrates vulnerable code, and provides a production-grade remediation.

---

## 1. Triggering External Side Effects Inside `post_save` Without `transaction.on_commit()`

### Failure Mechanism
`post_save` executes synchronously immediately after the SQL `INSERT` or `UPDATE` statement is issued to the database connection, **NOT when the database transaction commits**. If the signal handler invokes a third-party API (charging a credit card via Stripe, sending an SMS via Twilio, publishing to Kafka), and a subsequent line inside the same atomic block raises an exception, Django executes `ROLLBACK`.
- The database rolls back the record.
- The external third-party side-effect **cannot be rolled back**.
- Result: Phantom charges, ghost emails, and unrecoverable data inconsistency between internal and external systems.

### Vulnerable Code
```python
# signals.py
@receiver(post_save, sender=Order)
def on_order_created(sender, instance, created, **kwargs):
    if created:
        # CRITICAL VULNERABILITY: Fires BEFORE transaction commits!
        # If order creation fails later in the view, money is charged for a non-existent order!
        stripe.Charge.create(amount=int(instance.total * 100), customer=instance.user.stripe_id)
        send_order_receipt_email(instance.id)
```

### Production-Grade Remediation
Never trigger external side effects directly in `post_save`. Move the orchestration to an explicit Service Layer, or at minimum wrap side effects in `transaction.on_commit()`. Callbacks registered with `transaction.on_commit()` execute **only after the transaction successfully commits to the database** and are automatically discarded on rollback.

```python
# services/order_service.py
from django.db import transaction

class OrderService:
    @staticmethod
    def create_order(user, items, payment_token) -> Order:
        with transaction.atomic():
            order = Order.objects.create(user=user, status='PENDING')
            order.items.bulk_create(items)
            
            # Defer non-transactional side effects until successful COMMIT
            transaction.on_commit(lambda: process_order_payment_task.delay(order.id, payment_token))
            transaction.on_commit(lambda: send_receipt_email_task.delay(order.id))

        return order
```

---

## 2. Modifying and Saving Model Instances Inside `post_save`

### Failure Mechanism
Calling `instance.save()` inside a `post_save` receiver triggers another `post_save` signal for the same instance. Unless guarded with fragile recursion flags, Python raises `RecursionError: maximum recursion depth exceeded`.
Even when guarded using `update_fields`, saving inside `post_save` executes an extra SQL `UPDATE` statement immediately after the `INSERT`, doubling database write IOPS and creating race conditions (phantom overwrites) under concurrent requests.

### Vulnerable Code
```python
@receiver(post_save, sender=UserProfile)
def compute_referral_code(sender, instance, created, **kwargs):
    if created and not instance.referral_code:
        # BUG: Triggers post_save again!
        instance.referral_code = f"REF-{instance.user_id:06d}"
        instance.save()  # Infinite recursion hazard & redundant SQL UPDATE
```

### Production-Grade Remediation
- If the attribute is derived purely from model attributes before saving, compute it inside `pre_save` or override the model's `save()` method *before* calling `super().save()`.
- If the attribute requires the primary key or orchestration across models, generate it in the **Service Layer** prior to initial insertion.

```python
# In models.py or service layer:
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    referral_code = models.CharField(max_length=32, unique=True)

# services/user_service.py
class UserService:
    @staticmethod
    def create_profile(user) -> UserProfile:
        # Single INSERT with all fields populated up front
        referral_code = generate_secure_referral_code(user.id)
        return UserProfile.objects.create(user=user, referral_code=referral_code)
```

---

## 3. Relying on Signals for Invariants When Using Bulk Operations

### Failure Mechanism
Django ORM explicitly bypasses `pre_save`, `post_save`, `pre_delete`, and `post_delete` signals when executing set-based bulk operations (`bulk_create()`, `bulk_update()`, `QuerySet.update()`, and `QuerySet.delete()`).
If audit logging, search re-indexing, or cache invalidation logic is placed in signals, any developer executing a bulk update will **silently bypass the audit trail and leave corrupted cache states**.

### Vulnerable Code
```python
# signals.py
@receiver(post_save, sender=Product)
def invalidate_product_cache(sender, instance, **kwargs):
    cache.delete(f"product:{instance.id}")

# In a scheduled management command:
# CRITICAL BUG: Signals are NOT fired! Redis cache is now completely stale!
Product.objects.filter(category='DISCONTINUED').update(is_active=False)
```

### Production-Grade Remediation
Never rely on signals for invariants that must hold true across bulk updates. Centralize bulk updates in the Service Layer where cache invalidation and event logging are handled explicitly alongside the SQL operation.

```python
# services/product_service.py
class ProductService:
    @staticmethod
    def discontinue_category(category_name: str) -> int:
        with transaction.atomic():
            products = list(Product.objects.filter(category=category_name).values_list('id', flat=True))
            updated_count = Product.objects.filter(id__in=products).update(is_active=False)

            # Explicit batch cache eviction
            cache_keys = [f"product:{pid}" for pid in products]
            cache.delete_many(cache_keys)

            # Explicit audit logging
            AuditLog.objects.bulk_create([
                AuditLog(entity='PRODUCT', entity_id=pid, action='DISCONTINUED')
                for pid in products
            ])

        return updated_count
```

---

## 4. Assuming Django Signals Are Asynchronous Background Tasks

### Failure Mechanism
Engineers unfamiliar with Django dispatcher internals frequently mistake signals for an asynchronous event bus (like Celery or RabbitMQ). In reality, `Signal.send()` runs **synchronously on the active request thread**.
Placing synchronous HTTP requests, third-party webhook dispatches, or heavy image resizing inside a signal handler directly degrades API latency and causes HTTP timeouts.

### Vulnerable Code
```python
@receiver(post_save, sender=Document)
def generate_pdf_preview(sender, instance, created, **kwargs):
    if created:
        # CRITICAL BUG: Heavy CPU task running synchronously in HTTP worker!
        # Blocks Gunicorn worker for 10 seconds, exhausting worker threads.
        render_high_res_pdf_preview(instance.file.path)
```

### Production-Grade Remediation
Acknowledge that signals are synchronous in-process dispatches. Offload all I/O or CPU-heavy workloads to a distributed task queue (Celery, Dramatiq) scheduled via `transaction.on_commit()`.

```python
# tasks.py
@shared_task
def generate_pdf_preview_task(document_id: int):
    doc = Document.objects.get(id=document_id)
    render_high_res_pdf_preview(doc.file.path)

# services/document_service.py
class DocumentService:
    @staticmethod
    def upload_document(user, uploaded_file) -> Document:
        with transaction.atomic():
            doc = Document.objects.create(user=user, file=uploaded_file)
            # Enqueue asynchronous task ONLY after DB commit
            transaction.on_commit(lambda: generate_pdf_preview_task.delay(doc.id))
        return doc
```

---

## 5. Chaining Cascading Signals ("Spaghetti Control Flow")

### Failure Mechanism
A common antipattern is chaining signals across domain boundaries:
- `User` save triggers `UserProfile` creation via signal.
- `UserProfile` save triggers `Wallet` creation via signal.
- `Wallet` save triggers `LedgerAccount` creation via signal.
- `LedgerAccount` save triggers an external webhook via signal.

This creates unmaintainable, invisible control flow. A developer cannot trace execution paths using static analysis or debuggers. Furthermore, creating a `User` instance in a unit test inadvertently executes 15 database queries and triggers third-party HTTP calls, leading to brittle test suites.

### Vulnerable Code
```python
# apps/users/signals.py
@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

# apps/wallets/signals.py
@receiver(post_save, sender=UserProfile)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(profile=instance)
```

### Production-Grade Remediation
Eliminate the signal chain. Consolidate cross-entity provisioning into an explicit, transparent onboarding service function.

```python
# services/onboarding_service.py
class UserOnboardingService:
    @staticmethod
    def onboard_new_user(email: str, password: str) -> User:
        """Explicit orchestration: clear, debuggable, linear execution."""
        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password)
            profile = UserProfile.objects.create(user=user)
            wallet = Wallet.objects.create(profile=profile)
            
            # Post-commit notification
            transaction.on_commit(lambda: send_welcome_email.delay(user.id))

        return user
```

---

## 6. Missing `dispatch_uid` Causing Duplicate Signal Execution

### Failure Mechanism
Django reloads modules in several development and production environments (e.g., Gunicorn worker recycling, `runserver` reload, unit test runners). If `Signal.connect()` or `@receiver` registers a signal handler without a unique `dispatch_uid`, the receiver function is registered multiple times under different memory addresses.
Result: The signal fires **two or more times per event**, causing duplicate emails, double billing, or duplicate database records.

### Vulnerable Code
```python
# signals.py
# BUG: No dispatch_uid declared! Registered repeatedly during module imports.
@receiver(post_save, sender=Invoice)
def notify_accounting(sender, instance, created, **kwargs):
    if created:
        send_slack_alert(f"New invoice #{instance.id} created!")
```

### Production-Grade Remediation
If signals must be used, always specify an immutable `dispatch_uid` string to ensure the receiver is registered strictly once per process.

```python
@receiver(
    post_save,
    sender=Invoice,
    dispatch_uid="apps.billing.signals.notify_accounting_on_invoice_created"
)
def notify_accounting(sender, instance, created, **kwargs):
    if created:
        send_slack_alert(f"New invoice #{instance.id} created!")
```

---

## 7. Using Signals for Core Business Domain Logic

### Failure Mechanism
Using signals for business logic confuses **Infrastructure Inversion-of-Control** with **Domain Workflow Orchestration**. Business logic changes frequently and requires context (e.g., *who* initiated the action, *why* was it discounted, *what* channel did it come from).
Signals receive only the model `instance` and raw flags (`created`), completely stripping away the request context, actor identity, and business reason. Developers end up hacking thread-local variables or monkey-patching attributes onto model instances (e.g., `instance._request_user = request.user`) to pass context into signal receivers.

### Vulnerable Code
```python
# Hacky anti-pattern: attaching request context to model instance
class OrderCreateView(APIView):
    def post(self, request):
        order = Order(user=request.user, amount=request.data['amount'])
        order._actor = request.user  # FRAGILE MONKEY-PATCHING
        order.save()

# In signals.py
@receiver(post_save, sender=Order)
def process_order_logic(sender, instance, **kwargs):
    actor = getattr(instance, '_actor', None) # Fragile! Fails if saved from Celery or CLI
```

### Production-Grade Remediation
Never monkey-patch model instances to satisfy signals. Use the **Service Layer** and pass explicit, typed Context objects:

```python
@dataclass(frozen=True)
class ServiceContext:
    actor_user_id: int
    ip_address: str
    trace_id: str

class OrderService:
    @staticmethod
    def place_order(cmd: CreateOrderCommand, ctx: ServiceContext) -> Order:
        with transaction.atomic():
            order = Order.objects.create(
                user_id=cmd.user_id,
                amount=cmd.amount,
                created_by_id=ctx.actor_user_id
            )
            AuditLog.objects.create(
                entity='ORDER',
                entity_id=order.id,
                actor_id=ctx.actor_user_id,
                ip_address=ctx.ip_address,
                action='ORDER_PLACED'
            )
        return order
```
