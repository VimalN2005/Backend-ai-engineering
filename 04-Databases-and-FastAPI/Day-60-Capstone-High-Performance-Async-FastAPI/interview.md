# Staff & Principal Engineer Interview Guide: FastAPI, SQLAlchemy & Distributed Systems

Five rigorous architectural interview questions and authoritative answers covering asynchronous event loops, transaction lifecycle, distributed caching coherence, rate limiting algorithms, and connection pool sizing.

---

### Question 1: Explain how the ASGI event loop cooperates with Python's thread pool executor when handling `async def` vs plain `def` endpoints in FastAPI. Under what circumstances can an `async def` route perform worse than a `def` route?

#### Candidate Answer
FastAPI handles `async def` and plain synchronous `def` endpoints fundamentally differently:

1. **`async def` Endpoints**:
   - Executed **directly on the main ASGI event loop thread** (`asyncio` / `uvloop`).
   - Assumes that every I/O operation inside the handler is non-blocking and explicitly awaited (`await db.execute()`, `await client.get()`).
   - When an operation is awaited, control yields back to the OS event loop (`epoll` on Linux), allowing thousands of concurrent requests to be interleaved on a single CPU core with minimal overhead (~30MB RAM).

2. **Plain `def` Endpoints**:
   - FastAPI automatically detects that the function is synchronous and offloads its execution to an external thread pool:
     `await anyio.to_thread.run_sync(endpoint, *args)`
   - By default, Starlette/AnyIO allocates a thread pool of up to 40 worker threads.
   - The main event loop remains unblocked while the worker thread executes synchronous code.

#### When `async def` Performs Dramatically Worse
If a developer writes an `async def` endpoint but includes synchronous blocking code inside it:
```python
@app.get("/compute")
async def bad_endpoint():
    time.sleep(2.0)  # Or requests.get("...") or synchronous DB call
```
Because the function was declared `async def`, FastAPI does **not** send it to the threadpool—it executes it directly on the single-threaded event loop! 
The single event loop freezes completely for 2 seconds. All other 5,000 incoming requests waiting in socket listen queues are starved, timing out with `HTTP 504 Gateway Timeout`.

#### Rule of Thumb
- Use `async def` **only** if all underlying libraries are 100% async (`asyncpg`, `httpx`, `aiosqlite`, `redis.asyncio`).
- If you must use legacy synchronous libraries (`boto3`, `requests`, `pandas`), declare the endpoint as plain `def` or explicitly wrap calls in `run_in_threadpool`.

---

### Question 2: Trace the exact lifecycle of an SQLAlchemy 2.0 `AsyncSession` yielded by a FastAPI dependency. What happens on exception vs normal return, and why is `expire_on_commit=False` critical?

#### Candidate Answer
In FastAPI, database sessions are typically managed via a yield-based dependency:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

#### Lifecycle Trace
1. **Initialization (`__aenter__`)**:
   - `AsyncSessionLocal()` borrows an asynchronous database connection from the underlying connection pool (`QueuePool`).
   - A SQL transaction is lazily started (`BEGIN`).
2. **Execution Phase**:
   - FastAPI passes the active `session` into route handlers and dependent services via `Depends(get_db)`.
   - The route executes queries (`await session.execute(stmt)`), stages mutations (`session.add(entity)`), and flushes changes to the DB socket.
3. **Normal Exit**:
   - After the route completes and the response is serialized, execution resumes immediately after `yield session`.
   - The `try` block calls `await session.commit()`, issuing `COMMIT` to PostgreSQL.
4. **Exception Exit**:
   - If an unhandled exception or `HTTPException` is raised inside the route, the generator intercepts the exception in the `except` block.
   - `await session.rollback()` is executed, reverting all pending SQL operations.
   - The exception is re-raised so FastAPI's exception handlers can return proper HTTP error responses (e.g. 400, 404, 500).
5. **Connection Release (`__aexit__`)**:
   - The session closes its context, returning the underlying physical connection back to the connection pool clean and ready for reuse.

#### Why `expire_on_commit=False` is Mandatory in Async
In synchronous SQLAlchemy, after a `session.commit()`, all mapped attributes on loaded ORM instances are expired by default. Accessing an attribute later (e.g. `user.email` during Pydantic response serialization) triggers a **lazy load query** on the fly.
In `AsyncSession`, Python property access (`user.email`) is purely synchronous—it cannot `await` a database socket! If attributes are expired on commit, accessing them raises a fatal error:
`sqlalchemy.orm.exc.DetachedInstanceError: Parent instance is not bound to a Session; lazy load operation cannot proceed in async`.
Setting `expire_on_commit=False` keeps all loaded attributes cached in memory on the model instance post-commit, enabling seamless Pydantic serialization.

---

### Question 3: How do you architect a zero-downtime, multi-replica Redis cache invalidation strategy when web servers run on Kubernetes pods and background tasks run on Celery?

#### Candidate Answer
In distributed microservices where $N$ FastAPI pods and $M$ Celery workers read and write to a multi-replica Redis cluster with a PostgreSQL source of truth, maintaining cache coherence requires solving two fundamental problems: **Stale Cache Overwrite Races** and **Cross-Pod Local Memory Synchronization**.

#### Architecture Components
1. **Write-Invalidate (Delete, Never Overwrite)**:
   - When any service (FastAPI pod or Celery worker) modifies a database entity, it updates PostgreSQL first.
   - Strictly *after* the PostgreSQL transaction commits, the service issues an asynchronous Redis `DEL cache:entity:<id>` command.
   - Why: Overwriting the cache allows concurrent out-of-order writes (`W1 DB -> W2 DB -> W2 Cache -> W1 Cache`) to store stale data permanently. Deleting guarantees that the next read will pull fresh data lazily from the database.
2. **Two-Tier (L1 Local Memory + L2 Redis) Invalidation via Pub/Sub**:
   - High-throughput services often maintain an in-memory L1 cache (`cachetools.LRUCache`) inside each FastAPI process to avoid Redis network hops (< 1 µs latency).
   - When Pod A mutates an entity:
     1. Pod A updates PostgreSQL and deletes the L2 key in Redis.
     2. Pod A publishes an invalidation message to a Redis Pub/Sub channel:
        `PUBLISH cache:invalidations '{"entity": "doc", "id": "101"}'`
     3. All other FastAPI pods subscribe to `cache:invalidations`. Upon receiving the event, each pod purges the entity from its local L1 memory cache.
3. **Thundering Herd Shielding via Distributed Mutex**:
   - When a hot cache key is deleted and 1,000 requests arrive concurrently across pods, only 1 request is allowed to query the database using Redis `SET lock:doc:101 <uuid> NX PX 3000`.
   - The remaining 999 requests wait on a 20ms backoff loop and read from cache once warm, reducing database query spikes by 99.9%.

---

### Question 4: Compare the mechanics, memory trade-offs, and clock-skew vulnerabilities of a Redis Sliding-Window Rate Limiter (Sorted Sets) vs Token Bucket (Generic Cell Rate Algorithm / GCRA).

#### Candidate Answer

```
+───────────────────────────+────────────────────────────────────+────────────────────────────────────+
| Dimension                 | Sliding-Window (Redis Sorted Sets) | Token Bucket (GCRA / Cell Rate)    |
+───────────────────────────+────────────────────────────────────+────────────────────────────────────+
| Redis Data Structure      | ZSET (Sorted Set per client key)   | Single String / HASH storing       |
|                           |                                    | Theoretical Arrival Time (TAT)     |
| Memory Complexity         | $O(N)$ where $N$ is active requests| $O(1)$ constant memory (~32 bytes) |
|                           | in the sliding time window         | per rate-limited client            |
| Computational Cost        | ZREMRANGEBYSCORE + ZADD + ZCARD    | Single atomic EVAL Lua script      |
| Burst Traffic Handling    | Perfect window boundary precision; | Allows controlled burst capacity;  |
|                           | exact rolling 60-second quota      | smooths continuous trickle rates   |
| Clock Skew Vulnerability  | Highly vulnerable to client clock  | Relies purely on Redis server time |
|                           | variations if client sends time    | (`redis.call('TIME')`)             |
| Production Recommendation | Low-to-medium cardinality routes,  | High-throughput public APIs, CDN   |
|                           | sub-1,000 req/min precision limits | edges, millions of concurrent IPs  |
+───────────────────────────+────────────────────────────────────+────────────────────────────────────+
```

#### The GCRA (Generic Cell Rate Algorithm) Advantage
While Redis Sorted Sets provide exact rolling second precision, storing 1,000 microsecond timestamps per client consumes significant memory:
$1,000 \times 64\text{ bytes} \approx 64\text{ KB}$ per IP. For 100,000 active clients, this demands **6.4 GB of Redis RAM**.
GCRA calculates rate limits using a single mathematical formula tracking the **Theoretical Arrival Time (TAT)**:
$$\text{TAT}_{\text{new}} = \max(\text{now}, \text{TAT}_{\text{prev}}) + \text{Emission Interval}$$
If $\text{TAT}_{\text{new}} - \text{now} > \text{Burst Tolerance}$, the request is rejected immediately with zero array storage, consuming just **32 bytes per key** in Redis!

---

### Question 5: How do you prevent connection pool exhaustion across PostgreSQL (`max_connections`), Redis, and Celery when traffic spikes by 100x?

#### Candidate Answer
Connection pool exhaustion is the primary failure mode of monolithic async applications scaling up during traffic bursts. If 20 Uvicorn pods each configure `pool_size=50` and 10 Celery workers each spawn 8 prefork processes, total database connection demand is:
$$\text{Total DB Connections} = (20 \times 50) + (10 \times 8) = 1,080\text{ connections}$$
PostgreSQL default `max_connections = 100`. The database refuses connections with `FATAL: remaining connection slots are reserved for non-replication superuser connections`, cascading into full system failure.

#### Production Solution Architecture

```
[ 20 FastAPI Pods ] (pool_size=5, overflow=5) ──┐
                                                ├──▶ [ PgBouncer (Transaction Pooling) ] ──▶ [ PostgreSQL Cluster ]
[ 10 Celery Workers ] (pool_size=2) ────────────┘    (Client: 2,000 conn -> Server: 50 conn)   (max_connections=100)
```

1. **Deploy PgBouncer in Transaction Pooling Mode**:
   - Web applications rarely need permanent session state. In `pool_mode = transaction`, PgBouncer assigns a physical PostgreSQL connection to a web thread **only for the duration of an active transaction**.
   - As soon as `COMMIT` or `ROLLBACK` executes, the connection is instantly recycled for another query.
   - 2,000 client ASGI connections can easily be served by **50 physical PostgreSQL connections**, reducing DB CPU context-switching to near zero.
2. **Right-Size FastAPI Pool Parameters**:
   - Set conservative pool limits:
     `create_async_engine(DATABASE_URL, pool_size=5, max_overflow=10, pool_timeout=5.0)`
   - Never set unbounded overflows (`max_overflow=-1`). Fast failover (`pool_timeout=5.0`) prevents incoming HTTP requests from queuing indefinitely and starving server RAM.
3. **Redis Non-Blocking Connection Pool**:
   - Utilize a shared `redis.asyncio.ConnectionPool` with `max_connections=500` per pod.
   - Because Redis operations are non-blocking and execute in microseconds, a single Redis connection can handle thousands of multiplexed async operations.
4. **Celery Worker Isolation**:
   - Separate fast I/O queues from heavy database analytics queues.
   - Heavy analytical tasks run with `--concurrency=2` on dedicated worker nodes to prevent background jobs from overwhelming the database connection pool.
