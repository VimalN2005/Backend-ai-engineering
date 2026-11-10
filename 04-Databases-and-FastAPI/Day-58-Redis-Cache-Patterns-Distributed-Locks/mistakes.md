# Critical Antipatterns & Pitfalls in Redis Caching & Distributed Locks

This document analyzes the 7 most destructive architectural flaws and concurrency bugs encountered when implementing distributed caching, cache invalidation, and distributed locks with Redis in enterprise Python systems.

---

## 1. Updating Cache Instead of Invalidating (The Dual-Write Out-of-Order Race)

### Failure Mechanism
When updating a record in the database, developers frequently write:
`DB Update -> Cache Update (Overwrite)`

In concurrent environments, this creates an unavoidable race condition due to network packet re-ordering:
1. Thread A updates the database with Value 1 ($V_1$).
2. Thread B updates the database with Value 2 ($V_2$).
3. Thread B updates the Redis cache with $V_2$.
4. Thread A's network packet to Redis is delayed; it arrives *after* Thread B and overwrites the cache with $V_1$.
5. **Outcome**: The primary database contains $V_2$, but the Redis cache permanently stores stale value $V_1$ until the key's TTL elapses!

### Vulnerable Implementation
```python
# CRITICAL CONCURRENCY BUG: Dual-write out-of-order race condition
async def update_user_profile_vulnerable(user_id: int, new_data: dict):
    # 1. Update Database
    await db.execute(update(User).where(User.id == user_id).values(**new_data))
    await db.commit()
    
    # 2. Update Cache (FATAL: Can overwrite newer concurrent updates with stale data!)
    await redis.set(f"user:{user_id}", json.dumps(new_data), ex=3600)
```

### Production Remediation: Cache-Aside Invalidation (Delete on Write)
Always **Delete (Invalidate)** the cache key upon updating the database. If the key is deleted, subsequent reads trigger the Cache-Aside path, fetching the newly committed state from the database.

```python
async def update_user_profile_secure(user_id: int, new_data: dict):
    # 1. Update and commit to primary database first
    await db.execute(update(User).where(User.id == user_id).values(**new_data))
    await db.commit()
    
    # 2. Invalidate cache key immediately
    await redis.delete(f"user:{user_id}")
```

---

## 2. Non-Atomic `SETNX` Lock Acquisition (The Permanent Deadlock Trap)

### Failure Mechanism
Before Redis 2.6.12, distributed locks required two separate commands: `SETNX` (set if not exists) followed by `EXPIRE`. 

If a worker executes `SETNX`, and then immediately crashes, is terminated by an OOM killer, or the network connection blips before `EXPIRE` executes, the key is left with **no expiration**. The lock remains in Redis permanently, causing an unbreakable system-wide deadlock where no worker can ever acquire the lock again.

### Vulnerable Implementation
```python
# CRITICAL DEADLOCK: Non-atomic lock acquisition
async def acquire_lock_vulnerable(resource: str) -> bool:
    # Step 1: Set without expiration
    acquired = await redis.setnx(f"lock:{resource}", "1")
    if acquired:
        # IF WORKER CRASHES HERE -> PERMANENT DEADLOCK!
        await redis.expire(f"lock:{resource}", 10)
        return True
    return False
```

### Production Remediation: Atomic `SET ... NX PX`
Use the atomic `SET` command with `NX` and `PX` parameters (supported natively in modern Redis). This guarantees that lock acquisition and millisecond lease timeout occur as a single atomic operation.

```python
async def acquire_lock_secure(resource: str, token: str, lease_ms: int = 10000) -> bool:
    # Guaranteed single-operation atomicity
    acquired = await redis.set(
        f"lock:{resource}",
        token,
        nx=True,
        px=lease_ms,
    )
    return bool(acquired)
```

---

## 3. Releasing Locks with Plain `redis.delete()` (The Split-Brain Lock Theft)

### Failure Mechanism
Releasing a lock with a naive `redis.delete(lock_key)` creates a devastating split-brain vulnerability under GC pauses or network latency:

1. Client A acquires `lock:invoice_42` with a 10-second lease.
2. Client A enters a 12-second garbage collection pause or network freeze.
3. The 10-second lease expires in Redis. The lock key is automatically evicted.
4. Client B acquires `lock:invoice_42` with a new 10-second lease.
5. Client A wakes up from the pause, finishes its operation, and blindly executes `await redis.delete("lock:invoice_42")`.
6. **DISASTER**: Client A just deleted Client B's active lock!
7. Client C immediately acquires the lock -> **Both Client B and Client C are now executing inside the critical section simultaneously**, corrupting data!

### Vulnerable Implementation
```python
# CRITICAL SPLIT-BRAIN: Blind deletion steals other clients' locks!
async def process_transaction_vulnerable():
    token = str(uuid.uuid4())
    await redis.set("lock:tx", token, nx=True, px=5000)
    try:
        await perform_heavy_write()  # Takes 7 seconds (lease expired!)
    finally:
        # FATAL: Blindly deletes whoever currently holds the lock!
        await redis.delete("lock:tx")
```

### Production Remediation: Atomic Lua Release Script
The client must only delete the lock if the key's value **matches its unique UUID token**. This check-and-delete must execute atomically on the Redis server using a Lua script:

```python
LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

async def release_lock_secure(resource: str, token: str) -> bool:
    result = await redis.eval(
        LUA_RELEASE_LOCK,
        1,
        f"lock:{resource}",
        token,
    )
    return bool(result == 1)
```

---

## 4. Setting Uniform TTLs on Bulk Records (The Cache Avalanche)

### Failure Mechanism
When importing or batch-caching data (e.g., nightly catalog sync, search indexing), developers often set a uniform TTL:
`await redis.set(f"product:{item.id}", json_data, ex=3600)`

Because all 500,000 keys were written at the exact same minute with the exact same 1-hour expiration, **all 500,000 keys expire at the exact same second 1 hour later**. 

The cache hit ratio plunges to 0% in an instant. A wave of concurrent user requests cascades directly onto the primary database, causing database connection pool exhaustion and complete system failure (**Cache Avalanche**).

### Vulnerable Implementation
```python
# CRITICAL AVALANCHE: Uniform TTL synchronizes mass expirations
async def cache_products_bulk(products: list[Product]):
    for prod in products:
        # All keys expire at the exact same second!
        await redis.set(f"product:{prod.id}", prod.json(), ex=3600)
```

### Production Remediation: Randomized TTL Jitter
Always add randomized jitter ($\pm 10\text{–}15\%$) to base TTL values. This disperses key expirations smoothly across time, maintaining a stable cache hit ratio.

```python
import random

def get_jittered_ttl(base_ttl: int = 3600, jitter_pct: float = 0.15) -> int:
    delta = int(base_ttl * jitter_pct)
    return base_ttl + random.randint(-delta, delta)

async def cache_products_resilient(products: list[Product]):
    for prod in products:
        # Expirations distributed across [3060s, 4140s]
        ttl = get_jittered_ttl(base_ttl=3600, jitter_pct=0.15)
        await redis.set(f"product:{prod.id}", prod.json(), ex=ttl)
```

---

## 5. Ignoring Hot Key Expiration (The Thundering Herd / Cache Stampede)

### Failure Mechanism
When a single super-hot key (e.g., homepage banner, breaking news article, top 10 products) expires, thousands of concurrent requests miss the cache simultaneously. 

Without a concurrency shield, all 5,000 requests attempt to query the database and re-calculate the identical payload at the exact same time. The database is crushed by identical redundant queries.

### Vulnerable Implementation
```python
# CRITICAL STAMPEDE: Naive Cache-Aside allows thousands of concurrent DB misses
async def get_homepage_banner_vulnerable():
    data = await redis.get("homepage:banner")
    if data is None:
        # If 2,000 requests arrive here simultaneously, all 2,000 execute heavy SQL!
        data = await db.query_heavy_banner_metrics()
        await redis.set("homepage:banner", data, ex=120)
    return data
```

### Production Remediation: Mutex Single-Flight Protection
Use a short-lived distributed mutex (`stampede:lock`) so that **only the first request** queries the database. All subsequent requests wait on the lock and fetch the newly cached result.

```python
async def get_homepage_banner_resilient():
    data = await redis.get("homepage:banner")
    if data is not None:
        return data

    # Single-flight mutex
    lock_token = str(uuid.uuid4())
    if await redis.set("lock:homepage:banner", lock_token, nx=True, px=2000):
        try:
            # Double check in case leader just finished
            double_check = await redis.get("homepage:banner")
            if double_check is not None:
                return double_check
            
            # Exactly ONE worker executes the expensive DB calculation
            data = await db.query_heavy_banner_metrics()
            await redis.set("homepage:banner", data, ex=120)
            return data
        finally:
            await release_lock_secure("homepage:banner", lock_token)
    else:
        # Followers wait briefly and read newly populated cache
        await asyncio.sleep(0.05)
        return await get_homepage_banner_resilient()
```

---

## 6. Failing to Cache Null Results (The Cache Penetration Vulnerability)

### Failure Mechanism
An attacker sends millions of requests querying non-existent entities (e.g., `GET /api/v1/users/-99999` or random hashes). 

Because the ID does not exist in the database, the query returns `None`, and **nothing is cached in Redis**. Every subsequent malicious request bypasses the cache entirely and hits the relational database, causing resource exhaustion.

### Vulnerable Implementation
```python
# CRITICAL PENETRATION: Non-existent entities are never cached
async def get_user_vulnerable(user_id: int):
    val = await redis.get(f"user:{user_id}")
    if val:
        return json.loads(val)
    
    user = await db.get(User, user_id)
    if user:
        await redis.set(f"user:{user_id}", user.json(), ex=300)
    # If user is None, nothing is cached -> attacker floods DB!
    return user
```

### Production Remediation: Null Object Caching & Bloom Filters
When an entity is not found in the database, cache a sentinel null representation (`{"__null__": True}`) with a short TTL (e.g., 30–60 seconds). Subsequent requests for that non-existent entity hit Redis and return immediately without querying the database.

```python
SENTINEL_NULL = "__ENTITY_NOT_FOUND__"

async def get_user_resilient(user_id: int):
    val = await redis.get(f"user:{user_id}")
    if val == SENTINEL_NULL:
        return None
    if val:
        return json.loads(val)
    
    user = await db.get(User, user_id)
    if user is None:
        # Cache negative result with short TTL to prevent penetration
        await redis.set(f"user:{user_id}", SENTINEL_NULL, ex=60)
        return None
        
    await redis.set(f"user:{user_id}", user.json(), ex=300)
    return user
```

---

## 7. Using Python `pickle` for Cache Serialization (Remote Code Execution)

### Failure Mechanism
Python's `pickle` module can serialize arbitrary Python objects, making it tempting for caching complex model instances.

However, `pickle.loads()` executes arbitrary Python bytecode during deserialization. If an attacker gains network access to Redis, exploits an unauthenticated Redis instance, or executes a cache injection vulnerability, they can write a malicious pickle payload. When the web application retrieves and unpickles the key, the payload executes arbitrary OS commands, resulting in full **Remote Code Execution (RCE)** and system compromise.

### Vulnerable Implementation
```python
# CRITICAL SECURITY VULNERABILITY: Arbitrary code execution via pickle
import pickle

async def get_cached_session(session_id: str):
    raw = await redis.get(f"session:{session_id}")
    if raw:
        # FATAL: Executes arbitrary shell commands if payload is forged!
        return pickle.loads(raw)
```

### Production Remediation: Safe Serialization Formats
Always use secure, standardized serialization formats such as **JSON**, **Pydantic** (`model.model_dump_json()`), or **MessagePack** (`msgpack`). None of these formats permit executable bytecode deserialization.

```python
from pydantic import BaseModel

class UserSession(BaseModel):
    user_id: int
    roles: list[str]

async def get_cached_session_secure(session_id: str) -> Optional[UserSession]:
    raw = await redis.get(f"session:{session_id}")
    if raw:
        # Safe JSON parsing into validated Pydantic model
        return UserSession.model_validate_json(raw)
    return None
```

---

## Summary Matrix

| Antipattern | Primary Risk | Failure Manifestation | Production Fix |
| :--- | :--- | :--- | :--- |
| **Cache Overwrite** | Data Inconsistency | Cache permanently out of sync with DB | Delete on write (Cache Invalidation) |
| **Non-Atomic `SETNX`** | System Deadlock | Lock never expires if worker crashes | Atomic `SET resource token NX PX` |
| **Blind `DEL` Release** | Split-Brain Race | Worker deletes another worker's lock | Atomic Lua script matching UUID token |
| **Uniform TTLs** | Cache Avalanche | 0% hit ratio wave crashes database | Add $\pm 10\text{–}15\%$ randomized TTL jitter |
| **Cold Hot-Key Expiry** | Cache Stampede | 5,000 concurrent DB hits on 1 key | Mutex single-flight / XFetch algorithm |
| **Missing Null Caching** | Cache Penetration | Non-existent queries bypass cache | Cache sentinel null with 60s TTL |
| **`pickle` Serialization** | Remote Code Execution | Arbitrary shell execution on server | Use JSON, Pydantic, or MsgPack |
