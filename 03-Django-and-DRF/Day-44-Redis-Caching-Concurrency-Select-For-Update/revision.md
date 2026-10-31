# Day 44: 5-Minute Rapid Revision — Redis Caching & Concurrency

---

## 1. Caching Topology Quick Reference

| Pattern | Read Path | Write Path | Consistency | Ideal Use Case |
|:---|:---|:---|:---|:---|
| **Cache-Aside** | App checks Cache; on Miss, reads DB and populates Cache | App writes DB, then deletes/updates Cache | Eventual | High-read systems, general web APIs |
| **Write-Through**| App reads Cache | App writes DB and synchronously writes Cache | Strong | User profiles, settings, frequently read writes |
| **Write-Behind** | App reads Cache | App writes Cache immediately; worker flushes to DB | Weak | Telemetry, click streams, rate limit counters |

---

## 2. Cache Failure Modes & Production Mitigations

| Anomaly | Root Cause | Impact | Production Defense |
|:---|:---|:---|:---|
| **Cache Stampede** (Thundering Herd) | Hot key expires; 1,000 requests miss at once | DB CPU spikes to 100% | **Distributed Mutex** (`cache.add("lock:k")`) or **XFetch** |
| **Cache Avalanche** | Thousands of keys expire at the exact same second | Global DB surge | **TTL Jitter** (Add $\pm 10\% - 20\%$ random variation) |
| **Cache Penetration**| Requests for non-existent IDs bypass cache | Permanent cache miss | **Cache Null Objects** (TTL 60s) or **Bloom Filter** |

---

## 3. `select_for_update` Parameters Cheat Sheet

```python
# 1. Standard Blocking (Waits indefinitely until lock is released)
Account.objects.select_for_update().get(id=42)

# 2. Non-blocking (Raises OperationalError immediately if row is locked)
Account.objects.select_for_update(nowait=True).get(id=42)

# 3. Queue Processor (Skips locked rows; ideal for background workers)
JobQueue.objects.select_for_update(skip_locked=True).filter(status='PENDING').first()

# 4. Multi-table scoping (Lock only the parent model, not joined models)
Order.objects.select_for_update(of=('self',)).select_related('customer').get(id=10)
```

---

## 4. The Golden Rules of Concurrency & Caching

1. **Always wrap `select_for_update()` in `with transaction.atomic():`**  
   Outside of an atomic block, autocommit releases the row lock instantly!
2. **Sort multi-row IDs deterministically to prevent Deadlocks:**  
   `first_id, second_id = sorted([id_a, id_b])`
3. **Invalidate Cache ONLY inside `transaction.on_commit()`:**  
   Deleting cache before DB commits causes concurrent readers to re-cache stale data.
4. **Never make external HTTP calls while holding a database row lock:**  
   Keeps DB connection open and row locked, starving the connection pool.

---

## 5. Production `django-redis` Settings

```python
# settings.py
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {"max_connections": 100, "retry_on_timeout": True},
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
            # Security: Use JSON serializer instead of default Pickle to prevent RCE
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
        },
        "TIMEOUT": 300,
    }
}
```

---

## 6. Ten-Second Interview Defense Checklist

1. *Why did your inventory oversell during the flash sale?* -> Lost updates due to uncoordinated read-modify-write. Fix: `select_for_update()` inside `transaction.atomic()`.
2. *How do you prevent Deadlocks when transferring money between two accounts?* -> Sort account IDs deterministically (`first, second = sorted([id1, id2])`) before acquiring locks.
3. *What is `skip_locked` used for?* -> Building high-throughput, contention-free concurrent job queues where workers skip locked rows instead of blocking.
4. *Why should you never use Python pickle for Redis caching?* -> Allows Remote Code Execution (RCE) if an attacker injects malicious pickled payloads.
5. *When does cache deletion corrupt data?* -> When deleted inside an uncommitted transaction; readers fetch uncommitted old DB data and re-cache it. Fix: `transaction.on_commit(lambda: cache.delete(key))`.
