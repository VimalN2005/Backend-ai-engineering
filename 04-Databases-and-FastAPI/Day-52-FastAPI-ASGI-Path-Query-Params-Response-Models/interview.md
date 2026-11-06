# Day 52: Senior & Staff Technical Interview Mastery

This guide presents the top 5 advanced architectural interview questions focusing on **FastAPI, Starlette ASGI Internals, Concurrency Models, Response Serialization, and High-Throughput Request Lifecycles**.

---

### Question 1: Explain how ASGI functions under the hood (`scope`, `receive`, `send`), and how Starlette maps raw socket bytes into FastAPI route handlers.

#### Architectural Answer
ASGI (Asynchronous Server Gateway Interface 3.0) is the asynchronous successor to WSGI. It establishes a standard interface between Python async web servers (Uvicorn, Hypercorn) and application frameworks (Starlette, FastAPI).

An ASGI application is an async callable:
```python
async def app(scope: dict, receive: callable, send: callable) -> None:
    ...
```

```
+---------------------------------------------------------------------------------+
| Uvicorn (ASGI Web Server)                                                       |
| 1. Accepts client TCP socket and parses HTTP/1.1 or HTTP/2 frames.             |
| 2. Builds `scope` dictionary (method, path, headers, query string).             |
| 3. Invokes: await app(scope, receive, send)                                    |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Starlette / FastAPI Pipeline                                                    |
| 1. Middleware Stack: Intercepts scope, injects security/CORS headers.           |
| 2. Routing Triad: Matches scope["path"] against Radix Tree route regexes.      |
| 3. Parameter Extractor:                                                         |
|    - Path/Query: Extracted from scope["path"] and scope["query_string"].        |
|    - Body: Awaits `await receive()` in chunks to read JSON payload bytes.       |
| 4. Pydantic Engine: Validates input parameters into typed Python objects.       |
| 5. Endpoint Execution: Runs async def endpoint (or offloads def to threadpool).|
| 6. Response Serialization: Filters through response_model.                     |
| 7. Socket Output:                                                               |
|    - await send({"type": "http.response.start", "status": 200, ...})           |
|    - await send({"type": "http.response.body", "body": json_bytes})            |
+---------------------------------------------------------------------------------+
```

#### Key Architectural Advantage
Because `receive()` and `send()` are asynchronous coroutines, ASGI allows streaming request bodies (e.g. streaming file uploads) and streaming responses (Server-Sent Events, real-time LLM token streaming) with constant $O(1)$ memory consumption.

---

### Question 2: In FastAPI, what happens when you define an endpoint with `def` versus `async def`? How does Starlette's threadpool interact with the event loop?

#### Architectural Answer
One of FastAPI's most powerful—yet widely misunderstood—architectural features is its dual execution model:

| Endpoint Signature | Execution Context | Concurrency Model | Ideal Workload |
| :--- | :--- | :--- | :--- |
| **`async def endpoint()`** | Main `asyncio` Event Loop Thread | Cooperative coroutine multitasking | Non-blocking async I/O (`httpx`, `asyncpg`, `redis-py`) |
| **`def endpoint()`** | Starlette Worker Threadpool (`anyio.to_thread.run_sync`) | OS Thread preemption | Blocking synchronous I/O (`requests`, `psycopg2`, CPU tasks) |

#### The Event Loop Trap
If an engineer writes:
```python
@app.get("/blocking-disaster")
async def blocking_disaster():
    time.sleep(2.0) # OR sync_db.query(...)
    return {"status": "ok"}
```
Because the function is declared with `async def`, FastAPI executes it directly on the primary event loop thread. `time.sleep(2.0)` freezes the event loop for two full seconds. During those two seconds, the server cannot accept new TCP handshakes, execute pending coroutines, or process incoming WebSockets for any client.

#### Why `def` is Safe for Synchronous Code
If declared as `def`:
```python
@app.get("/safe-sync")
def safe_sync():
    time.sleep(2.0)
    return {"status": "ok"}
```
FastAPI detects that the callable is synchronous. It offloads execution to an AnyIO worker thread pool (by default up to 40 threads). The worker thread blocks for 2 seconds, but the primary event loop thread remains completely free to continue serving thousands of concurrent async requests!

---

### Question 3: How does FastAPI's `response_model` ensure data privacy and prevent sensitive field leakage? What are the CPU/memory trade-offs compared to raw `JSONResponse`?

#### Architectural Answer
In enterprise applications, database entities and domain models often contain sensitive operational fields:
- `hashed_password`, `salt`
- `stripe_customer_id`, `bank_account_number`
- `margin_percentage`, `internal_cost`

`response_model` acts as a **strict outbound serialization and privacy boundary**:

```
[ Database ORM Model / Internal Dict ]
(Contains 20 fields including hashed_password)
                     |
                     v
+-------------------------------------------------------------+
| FastAPI Response Pipeline                                   |
| 1. Parses raw object against response_model schema.         |
| 2. Drops any field not explicitly defined in the schema.    |
| 3. Validates and coerces output types.                      |
| 4. Serializes to JSON via Pydantic V2 Rust core.            |
+-------------------------------------------------------------+
                     |
                     v
[ Clean Outbound JSON Payload ]
(Contains only public client fields)
```

#### Performance Trade-Off Analysis
- **`response_model` Overhead**: Pydantic must reconstruct the data dictionary, validate field types, and construct the response JSON. In Pydantic V2, this is compiled in Rust (`pydantic-core`), achieving ~800–1,200 rps over large arrays (500 items).
- **Raw `JSONResponse`**: Bypasses Pydantic entirely, dumping pre-sanitized dictionaries via Starlette's `JSONResponse` or `orjson.dumps()`. This achieves ~1,100–1,500 rps (a 25–40% throughput increase).
- **Staff Recommendation**: For 98% of endpoints, prioritize `response_model` because the operational risk of leaking sensitive customer data far outweighs microsecond serialization gains. Reserve raw `JSONResponse` strictly for internal, high-frequency telemetry endpoints with verified manual filtering.

---

### Question 4: Why did FastAPI transition from parameter default assignments to `typing.Annotated` (PEP 593)? How does this affect static analysis and schema composition?

#### Architectural Answer
In legacy FastAPI (prior to version 0.95), parameters were configured using default value assignment:
```python
# Legacy Syntax
async def get_items(
    q: str = Query(..., min_length=3),
    page: int = Query(default=1, ge=1)
):
    ...
```

Modern FastAPI standardizes on Python 3.9+ / PEP 593 `typing.Annotated`:
```python
# Modern Idiomatic Syntax
async def get_items(
    q: Annotated[str, Query(min_length=3)],
    page: Annotated[int, Query(ge=1)] = 1
):
    ...
```

#### Key Architectural Advantages
1. **Type Checker Invariants**: In the legacy syntax, `page: int = Query(...)` assigns a `Query` descriptor to an `int` variable. Static type checkers (`mypy`, `pyright`) fail or require specialized framework plugins. Under `Annotated[int, Query(...)]`, the type is recognized natively as `int`.
2. **Reusability via Type Aliases**: Enterprise applications can compose domain parameter types once and reuse them across hundreds of routes:
   ```python
   TenantHeader = Annotated[str, Header(min_length=3, max_length=50, alias="X-Tenant-ID")]
   PaginationLimit = Annotated[int, Query(ge=1, le=100, description="Items per page")]

   @app.get("/users")
   async def list_users(tenant: TenantHeader, limit: PaginationLimit = 20): ...
   ```
3. **Decoupled Unit Testing**: Functions with `Annotated` parameters can be called in pure unit tests (`get_items(q="laptop", page=2)`) without needing to mock or bypass `Query` objects.

---

### Question 5: How do you implement the modern `lifespan` protocol in FastAPI to manage complex stateful resources with zero connection leaks?

#### Architectural Answer
Legacy `@app.on_event("startup")` and `@app.on_event("shutdown")` decorators are deprecated. Modern FastAPI applications manage state through the ASGI Lifespan protocol using Python's `@asynccontextmanager`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import asyncpg
import redis.asyncio as aioredis

@asynccontextmanager
async def lifespan(app: FastAPI):
    # =========================================================================
    # 1. STARTUP PHASE: Executes before server accepts the first connection
    # =========================================================================
    print("[STARTUP] Initializing database and cache connection pools...")
    db_pool = await asyncpg.create_pool(
        dsn="postgresql://user:pass@localhost:5432/db",
        min_size=10,
        max_size=30,
        timeout=10.0,
    )
    redis_client = aioredis.from_url("redis://localhost:6379", decode_responses=True)
    
    # Store initialized connections in app.state for dependency injection
    app.state.db_pool = db_pool
    app.state.redis = redis_client
    
    try:
        yield  # Server runs, handles traffic, and processes requests
    finally:
        # =====================================================================
        # 2. SHUTDOWN PHASE: Guaranteed execution when SIGINT/SIGTERM is received
        # =====================================================================
        print("[SHUTDOWN] Draining connections and releasing socket resources...")
        await db_pool.close()
        await redis_client.aclose()
        print("[SHUTDOWN] All resources cleanly disposed.")

app = FastAPI(lifespan=lifespan)
```

#### Why This Prevents Connection Leaks
1. **Guaranteed Cleanup**: Wrapping execution in `try...finally` ensures that even if an uncaught exception crashes the application or an unhandled SIGINT arrives, database pools and Redis sockets are gracefully closed, preventing PostgreSQL "idle in transaction" connection leaks.
2. **Deterministic Sequencing**: Startup dependencies are instantiated in strict topological order before traffic begins, preventing race conditions where routes attempt to query uninitialized database pools.

---
*Staff-level interviewers evaluate candidates on their ability to explain ASGI mechanics, memory safety boundaries, and concurrency semantics beyond basic framework syntax.*
