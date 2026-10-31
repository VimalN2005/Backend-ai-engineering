# Day 44: Common Antipatterns in Redis Caching & Database Concurrency

This guide documents 7 critical production antipatterns encountered when implementing Redis caching layers and database concurrency locking in Django backends. Each entry details the underlying failure mechanism, vulnerable code examples, and production-grade remediations.

---

## 1. Using `select_for_update()` Outside of `transaction.atomic()`

### Failure Mechanism
In SQL engines (PostgreSQL, MySQL), row-level locks acquired via `SELECT ... FOR UPDATE` are held until the **end of the active transaction** (`COMMIT` or `ROLLBACK`). 
If `select_for_update()` is called in autocommit mode (outside a `transaction.atomic()` block):
1. Django executes `SELECT ... FOR UPDATE` on the database connection.
2. The database acquires the lock.
3. Because autocommit is active, the single-statement transaction immediately commits!
4. The row lock is **instantly released** before subsequent Python code can read or update the model.
5. The lock provides **zero protection against race conditions**, silently failing to prevent lost updates.

### Vulnerable Code
```python
# views.py or services.py
def withdraw_funds(account_id: int, amount: Decimal):
    # CRITICAL BUG: No transaction.atomic() context!
    # Lock is acquired and immediately released by autocommit!
    account = Account.objects.select_for_update().get(id=account_id)
    if account.balance >= amount:
        account.balance -= amount
        account.save()  # Separate transaction! Race conditions occur here!
```

### Production-Grade Remediation
Always enclose `select_for_update()` within an explicit `transaction.atomic()` context block. In tests, enable transaction test harnesses or static linter rules enforcing this constraint.

```python
from django.db import transaction

def withdraw_funds(account_id: int, amount: Decimal):
    with transaction.atomic():
        # Lock is held continuously until with-block exits!
        account = Account.objects.select_for_update().get(id=account_id)
        if account.balance >= amount:
            account.balance -= amount
            account.save()
        else:
            raise InsufficientFundsError("Balance insufficient.")
```

---

## 2. Non-Deterministic Lock Ordering Triggering Deadlocks

### Failure Mechanism
When transactions lock multiple database rows simultaneously (e.g. transfers between two bank accounts or reservations across multiple inventory items), acquiring locks in random or client-specified order creates circular wait dependencies:
- **Transaction A** locks Account 10, then attempts to lock Account 20.
- **Transaction B** locks Account 20, then attempts to lock Account 10.
- Result: **Deadlock**. PostgreSQL terminates one of the transactions with `DeadlockDetected`.

### Vulnerable Code
```python
def transfer_balance(sender_id: int, recipient_id: int, amount: Decimal):
    with transaction.atomic():
        # BUG: Lock order depends on who calls the function!
        # If Alice transfers to Bob while Bob transfers to Alice -> DEADLOCK!
        sender = Account.objects.select_for_update().get(id=sender_id)
        recipient = Account.objects.select_for_update().get(id=recipient_id)
        
        sender.balance -= amount
        recipient.balance += amount
        sender.save()
        recipient.save()
```

### Production-Grade Remediation
Enforce a globally deterministic lock acquisition order. Always sort primary keys before locking:

```python
def transfer_balance(sender_id: int, recipient_id: int, amount: Decimal):
    if sender_id == recipient_id:
        raise ValueError("Cannot transfer to identical account.")

    # Sort IDs deterministically: always acquire lock on lower ID first!
    first_id, second_id = sorted([sender_id, recipient_id])

    with transaction.atomic():
        first_acc = Account.objects.select_for_update().get(id=first_id)
        second_acc = Account.objects.select_for_update().get(id=second_id)

        sender = first_acc if first_acc.id == sender_id else second_acc
        recipient = second_acc if second_acc.id == recipient_id else first_acc

        if sender.balance < amount:
            raise InsufficientFundsError()

        sender.balance -= amount
        recipient.balance += amount
        sender.save()
        recipient.save()
```

---

## 3. Default Pickle Cache Serializer Enabling Remote Code Execution (RCE)

### Failure Mechanism
By default, early versions of `django-redis` and naive cache setups serialize Python objects using Python's `pickle` library. `pickle.loads()` is inherently insecure: if an attacker compromises the Redis instance (e.g. unauthenticated Redis port exposed to the VPC or via SSRF), they can inject malicious pickled byte payloads containing `__reduce__` shell execution hooks. When Django calls `cache.get()`, the payload unpickles and executes arbitrary shell commands on the application server.

### Vulnerable Code
```python
# settings.py
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        # VULNERABILITY: Missing safe serializer configuration!
        # Defaults to pickle, exposing system to RCE vulnerabilities!
    }
}
```

### Production-Grade Remediation
Configure `django-redis` to use `JSONSerializer` or `MsgPackSerializer`. Never store raw unvetted Python objects in Redis:

```python
# settings.py
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # Secure: strictly prohibits arbitrary code execution
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
        }
    }
}
```

---

## 4. Synchronized Cache Expiration Causing Cache Avalanche

### Failure Mechanism
When batch caching thousands of items (e.g. warming product catalogs or user permissions) with a uniform TTL (e.g., `timeout=3600`), all keys expire at the exact same second. When traffic arrives, all requests experience simultaneous cache misses, unleashing an avalanche of queries onto the SQL database and causing connection timeouts.

### Vulnerable Code
```python
def warm_product_cache():
    for product in Product.objects.all():
        # BUG: 100,000 keys expire at the EXACT same second!
        cache.set(f"product:{product.id}", product.to_dict(), timeout=3600)
```

### Production-Grade Remediation
Introduce random TTL jitter (variance of $\pm 10\% - 20\%$) to distribute key expirations smoothly across time:

```python
import random

def warm_product_cache():
    base_ttl = 3600
    for product in Product.objects.all():
        # Add random jitter between -300s and +300s
        jitter = random.randint(-300, 300)
        cache.set(f"product:{product.id}", product.to_dict(), timeout=base_ttl + jitter)
```

---

## 5. Cache Miss Stampede (Thundering Herd) Under Peak Traffic

### Failure Mechanism
When a high-traffic cache key expires or is invalidated, hundreds of concurrent requests encounter a cache miss in the same millisecond. Each request independently executes the database query to regenerate the cache. For expensive aggregation queries, this spikes database CPU to 100% and crashes the database.

### Vulnerable Code
```python
def get_homepage_leaderboard():
    key = "homepage_leaderboard"
    data = cache.get(key)
    if data is None:
        # BUG: If key expires, 1,000 concurrent requests execute this heavy query!
        data = list(User.objects.annotate(score=Sum('points')).order_by('-score')[:50])
        cache.set(key, data, timeout=300)
    return data
```

### Production-Grade Remediation
Implement a distributed mutex lock using atomic `cache.add()` (Redis `SETNX`). Only one thread recalculates the cache, while others wait or read stale data:

```python
import time

def get_homepage_leaderboard():
    key = "homepage_leaderboard"
    data = cache.get(key)
    if data is not None:
        return data

    lock_key = f"lock:{key}"
    # Atomic SETNX lock valid for 10 seconds
    if cache.add(lock_key, "locked", timeout=10):
        try:
            # Single winner executes query
            data = list(User.objects.annotate(score=Sum('points')).order_by('-score')[:50])
            cache.set(key, data, timeout=300)
            return data
        finally:
            cache.delete(lock_key)
    else:
        # Other threads back off and wait for winner to finish
        for _ in range(10):
            time.sleep(0.05)
            data = cache.get(key)
            if data is not None:
                return data
        return []
```

---

## 6. Holding `select_for_update()` Across Slow External Network Calls

### Failure Mechanism
Executing third-party API calls (Stripe, Twilio, SendGrid, S3) inside a `transaction.atomic()` block holding a `select_for_update` lock keeps the database row locked and holds the database connection open for seconds. Under traffic, incoming requests queue up waiting for the lock, saturating Gunicorn worker threads and exhausting database `max_connections`.

### Vulnerable Code
```python
def process_order(order_id: int):
    with transaction.atomic():
        # Acquires row lock
        order = Order.objects.select_for_update().get(id=order_id)
        
        # DISASTER: Network latency of 800ms while holding row lock and DB connection!
        charge = stripe.Charge.create(amount=order.total_cents, customer=order.customer_id)
        
        order.status = 'PAID'
        order.save()
```

### Production-Grade Remediation
Keep database transactions minimal and localized. Never hold a row lock while making an external network call:

```python
def process_order(order_id: int):
    # Step 1: Atomic state transition (Lock held for < 2 milliseconds)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != 'PENDING':
            raise InvalidStateError()
        order.status = 'PROCESSING'
        order.save()

    # Step 2: External HTTP call OUTSIDE of database transaction
    try:
        charge = stripe.Charge.create(amount=order.total_cents, customer=order.customer_id)
    except stripe.error.StripeError:
        with transaction.atomic():
            order.status = 'FAILED'
            order.save()
        raise

    # Step 3: Final state update
    with transaction.atomic():
        order.status = 'PAID'
        order.charge_id = charge.id
        order.save()
```

---

## 7. Premature Cache Invalidation Causing Race-Condition Stale Reads

### Failure Mechanism
Deleting a cache key *before* the database transaction commits permits concurrent readers to fetch uncommitted old data and immediately re-cache it, leaving the cache permanently stale.

```
Thread A (Writer)                         Thread B (Reader)
BEGIN;
UPDATE product SET price = 90;
cache.delete("product:1"); ───► Cache Evicted
                                          cache.get("product:1") -> MISS
                                          SELECT price FROM product -> 100 (Uncommitted read!)
                                          cache.set("product:1", 100); ──► Stale Cache!
COMMIT;
```

### Production-Grade Remediation
Always invalidate cache keys **strictly after database commit** using `transaction.on_commit()`:

```python
def update_product_price(product_id: int, new_price: Decimal):
    with transaction.atomic():
        product = Product.objects.select_for_update().get(id=product_id)
        product.price = new_price
        product.save()

        # Invalidate cache ONLY after the transaction has permanently committed!
        transaction.on_commit(lambda: cache.delete(f"product:{product_id}"))
```
