# Rapid Revision & Quick Reference: Redis Caching & Distributed Locks

A condensed 5-minute reference guide covering Redis caching patterns, cache-aside invalidation, thundering herd mitigation, and distributed locks.

---

## 1. The 4 Fundamental Caching Patterns

| Pattern | Flow | Tradeoff | Ideal Use Case |
| :--- | :--- | :--- | :--- |
| **Cache-Aside** | App reads Cache $\to$ misses $\to$ reads DB $\to$ populates Cache. Writes update DB then delete Cache. | Simple, resilient, short staleness window. | General APIs, user profiles, catalog. |
| **Write-Through** | App writes to Cache; Cache synchronously writes to DB. | Strong consistency, slower writes. | Financial balances, real-time ledgers. |
| **Write-Behind** | App writes to Cache; background worker batches writes to DB. | High write throughput; risk of data loss on crash. | Telemetry, analytics counters, click logs. |
| **Refresh-Ahead** | Background cron/scheduler re-warms keys before TTL expires. | Eliminates read latency; wastes CPU if keys go unread. | Hot dashboard metrics, leaderboard tops. |

---

## 2. Production Boilerplate: Cache-Aside with TTL Jitter

```python
import json
import random

def get_jittered_ttl(base_ttl: int = 300, jitter_pct: float = 0.15) -> int:
    delta = int(base_ttl * jitter_pct)
    return base_ttl + random.randint(-delta, delta)

async def get_user_profile(user_id: str, redis_client, db_fetch_fn):
    cache_key = f"user:{user_id}"
    
    # 1. Read Cache
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return json.loads(cached)
        
    # 2. Read Primary Data Store
    user_data = await db_fetch_fn(user_id)
    if user_data is None:
        # Cache null sentinel for 60s to prevent penetration
        await redis_client.set(cache_key, "__NULL__", ex=60)
        return None
        
    # 3. Store in Cache with Jittered TTL (Prevents Avalanche)
    ttl = get_jittered_ttl(base_ttl=300)
    await redis_client.set(cache_key, json.dumps(user_data), ex=ttl)
    return user_data

async def update_user_profile(user_id: str, new_data: dict, redis_client, db_update_fn):
    # 1. Update and commit to primary DB first
    await db_update_fn(user_id, new_data)
    # 2. Delete cache key immediately (Invalidate)
    await redis_client.delete(f"user:{user_id}")
```

---

## 3. Production Boilerplate: Distributed Lock with Lua Release

```python
import uuid

LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

class DistributedLock:
    def __init__(self, redis_client, resource: str, lease_ms: int = 5000):
        self.redis = redis_client
        self.key = f"lock:{resource}"
        self.lease_ms = lease_ms
        self.token = str(uuid.uuid4())

    async def acquire(self) -> bool:
        # Atomic SET with NX (not exists) and PX (millisecond expiry)
        return bool(await self.redis.set(self.key, self.token, nx=True, px=self.lease_ms))

    async def release(self) -> bool:
        # Atomic release matching UUID token
        res = await self.redis.eval(LUA_RELEASE_LOCK, 1, self.key, self.token)
        return bool(res == 1)

    async def __aenter__(self):
        if not await self.acquire():
            raise TimeoutError(f"Failed to acquire lock for {self.key}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
```

---

## 4. Cache Failure Modes & Defense Checklist

| Vulnerability | Cause | Failure Outcome | Production Defense |
| :--- | :--- | :--- | :--- |
| **Cache Stampede** | Hot key expires | 5,000 concurrent DB hits | Mutex Single-Flight / XFetch early refresh |
| **Cache Avalanche** | Bulk keys expire at once | 0% hit ratio surge | Add $\pm 10\text{–}15\%$ randomized TTL Jitter |
| **Cache Penetration** | Non-existent keys queried | Every request queries DB | Cache Null objects (60s TTL) + Bloom filters |
| **Lock Split-Brain** | Blind `DEL` on expired lock | Worker A deletes Worker B's lock | Atomic Lua script matching UUID token |
| **Stale Overwrite** | Dual-write out-of-order | Redis permanently out of sync | Invalidate (Delete) cache on write |

---

## 5. High-Frequency Interview Flashcards

### Q1: Why must distributed lock release be executed via Lua scripts?
> **Answer**: If Client A's lease expires due to a network pause and Client B acquires the lock, Client A waking up must not delete Client B's lock. The Lua script atomically validates that the value stored in Redis matches Client A's unique UUID token before executing `DEL`, preventing split-brain lock theft.

### Q2: Why is deleting cache preferred over updating cache upon DB writes?
> **Answer**: Two concurrent writes ($V_1, V_2$) can arrive at Redis out of order due to network jitter, causing the cache to store old $V_1$ while DB has $V_2$. Deleting the cache removes the key regardless of arrival order, forcing subsequent reads to load the newest committed DB state.

### Q3: What is the XFetch algorithm?
> **Answer**: XFetch is a probabilistic early expiration algorithm ($\Delta \cdot \beta \cdot \ln(\text{rand()}) > \text{TTL}_{\text{remaining}}$) that triggers an asynchronous background cache refresh *before* the key expires, delivering zero user-facing latency and 100% database protection against cache stampedes.

### Q4: What is a Fencing Token in distributed systems?
> **Answer**: A monotonically increasing integer provided by the lock manager. When writing to the database, the storage engine verifies that the token is greater than any previously processed token, rejecting delayed writes from clients whose locks expired during GC pauses.
