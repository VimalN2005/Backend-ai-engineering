# Critical Antipatterns & Pitfalls in BackgroundTasks, Middleware & Streaming

This document analyzes the 7 most destructive architectural antipatterns and security vulnerabilities encountered when implementing FastAPI background tasks, ASGI middleware, and streaming responses in production.

---

## 1. Using `BaseHTTPMiddleware` for High-Throughput & Streaming Architectures

### Failure Mechanism
Starlette provides `BaseHTTPMiddleware` as a convenient high-level class (`async def dispatch(request, call_next)`). However, its internal implementation relies on `anyio.create_task_group()`, creates Starlette `Request` and `_StreamingResponse` abstractions, and runs the downstream application in a separate child task communicating via memory streams.

This design introduces three critical production failures:
1. **Severe Latency Inflation**: Dispatches take ~150–400 µs compared to ~3–5 µs for pure ASGI middleware (**70x–80x performance penalty**).
2. **Streaming Response Buffering**: It consumes chunks and buffers streaming responses internally, breaking native TCP backpressure.
3. **ContextVar Scoping Mutations**: In certain Python versions, the AnyIO task group boundary breaks or mutates `contextvars` inheritance, leading to lost tracing IDs or cross-request pollution.

### Vulnerable Implementation
```python
# CRITICAL ANTIPATTERN: BaseHTTPMiddleware adds 75x latency and breaks streaming
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import FastAPI, Request
import time

app = FastAPI()

class SlowTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        # Creates task group, memory stream, and consumes 350+ microseconds per request!
        response = await call_next(request)
        response.headers["X-Process-Time"] = f"{time.perf_counter() - t0:.6f}"
        return response

app.add_middleware(SlowTimingMiddleware)
```

### Production Remediation
Implement **Pure ASGI Middleware** (`async def __call__(self, scope, receive, send)`). Pure ASGI operates directly on raw ASGI event dicts without allocating intermediate Python task groups or buffering streams.

```python
import time
from typing import Any, Callable, Dict

class FastASGITimingMiddleware:
    """Zero-allocation pure ASGI timing middleware (4-6 µs latency)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Dict[str, Any]], Any],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        t0 = time.perf_counter_ns()

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                duration_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
                headers = list(message.get("headers", []))
                headers.append((b"x-process-time-ms", f"{duration_ms:.3f}".encode("latin1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

---

## 2. Passing Request-Scoped Database Sessions or ORM Entities to `BackgroundTasks`

### Failure Mechanism
In FastAPI, database sessions are typically managed via a `yield` dependency (`async def get_db_session()`). As soon as the route handler returns its response model, FastAPI's `AsyncExitStack` immediately executes the `finally` block, closing the `AsyncSession` and returning the underlying database connection to the pool.

When the `BackgroundTask` executes (which occurs *after* the HTTP response has been flushed to the wire), attempting to use that session raises:
`sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside session.`

Furthermore, if an ORM entity instance (`Order`) was passed into the background task, any attempt to access an unloaded lazy relationship raises:
`sqlalchemy.orm.exc.DetachedInstanceError: Parent instance <Order at ...> is not bound to a Session; lazy load operation cannot proceed.`

### Vulnerable Implementation
```python
# CRITICAL BUG: Closed session and DetachedInstanceError
from fastapi import FastAPI, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

@app.post("/orders")
async def checkout(
    order_in: OrderCreate,
    db: AsyncSession = Depends(get_db_session),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    order = await order_service.create(db, order_in)
    
    # FATAL ERROR: db session will be CLOSED when this task executes!
    background_tasks.add_task(send_receipt_email, db, order)
    return {"order_id": order.id}

async def send_receipt_email(db: AsyncSession, order: Order):
    # CRASH: Session is already closed!
    user = await db.get(User, order.user_id)
```

### Production Remediation
1. Never pass active `AsyncSession` instances to background tasks.
2. Never pass ORM model instances across async boundaries; pass only primitive IDs (`order_id: int`).
3. Instantiate a fresh, dedicated session lifecycle inside the background task using an independent session factory.

```python
from fastapi import FastAPI, Depends, BackgroundTasks
from db.session import async_session_factory

@app.post("/orders")
async def checkout(
    order_in: OrderCreate,
    db: AsyncSession = Depends(get_db_session),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    order = await order_service.create(db, order_in)
    
    # PASS ONLY PRIMITIVE IDENTIFIERS
    background_tasks.add_task(send_receipt_email, order.id)
    return {"order_id": order.id}

async def send_receipt_email(order_id: int):
    # INDEPENDENT, ISOLATED SESSION LIFECYCLE
    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        if order:
            user = await session.get(User, order.user_id)
            await mailer.send(user.email, f"Order #{order.id} Confirmed")
```

---

## 3. Sequential Blocking with `await request.is_disconnected()` in Streaming Loops

### Failure Mechanism
To abort abandoned generation, developers often check if a client has disconnected. In Starlette, `request.is_disconnected()` works by awaiting `self._receive()`.

However, during HTTP/1.1 or HTTP/2 streaming, the client sends no incoming body frames after the initial request. Therefore, calling `await request.is_disconnected()` **blocks indefinitely**, waiting for an event to arrive on `receive()`. Because the client is still connected, the generator freezes completely, producing no tokens and hanging the connection until an idle timeout occurs!

### Vulnerable Implementation
```python
# CRITICAL BUG: Sequential await request.is_disconnected() freezes the generator
from fastapi import Request
from fastapi.responses import StreamingResponse

async def token_generator(request: Request):
    for token in ["Alpha", "Beta", "Gamma", "Delta"]:
        # DEADLOCK / HANG: If client is connected, receive() blocks forever!
        if await request.is_disconnected():
            break
        yield token
        await asyncio.sleep(0.1)

@app.get("/stream")
async def stream(request: Request):
    return StreamingResponse(token_generator(request), media_type="text/plain")
```

### Production Remediation
To detect disconnects without blocking the generator, run an independent concurrent listener task on `request.receive()`, communicating via an `asyncio.Event` flag or checking if `receive()` returns `http.disconnect`.

```python
import asyncio
from fastapi import Request
from fastapi.responses import StreamingResponse

async def safe_token_generator(request: Request):
    disconnect_event = asyncio.Event()

    async def watch_disconnect():
        try:
            while True:
                message = await request.receive()
                if message.get("type") == "http.disconnect":
                    disconnect_event.set()
                    break
        except asyncio.CancelledError:
            pass

    # Launch background watcher
    watcher_task = asyncio.create_task(watch_disconnect())

    try:
        tokens = ["Alpha", "Beta", "Gamma", "Delta"]
        for token in tokens:
            if disconnect_event.is_set():
                logger.info("Client aborted connection. Halting stream.")
                break
            yield f"{token}\n"
            await asyncio.sleep(0.05)
    finally:
        watcher_task.cancel()
        await asyncio.gather(watcher_task, return_exceptions=True)
```

---

## 4. Treating In-Process `BackgroundTasks` as an Enterprise Distributed Queue

### Failure Mechanism
FastAPI's `BackgroundTasks` executes in-process on the local Uvicorn worker thread/event loop. It provides zero durability, zero persistence, and zero horizontal scalability.

If:
- The Kubernetes pod restarts or autoscales down,
- An unhandled exception or OOM killer crashes the Uvicorn worker,
- A deployment replaces container instances,

**Every single queued background task in memory is permanently destroyed without execution.** Relying on `BackgroundTasks` for transactional workflows (payment processing, order fulfillment, audit compliance) guarantees data loss.

### Vulnerable Implementation
```python
# ARCHITECTURAL ANTIPATTERN: In-memory task queue used for critical financial operations
@app.post("/payments/webhook")
async def stripe_webhook(event: WebhookEvent, bg: BackgroundTasks):
    # FATAL FLAW: If pod scales down, payment capture is never recorded in ledger!
    bg.add_task(fulfill_subscription_and_charge_card, event.customer_id, event.amount)
    return {"status": "accepted"}
```

### Production Remediation
- Use `BackgroundTasks` **only** for ephemeral, non-critical operations (cache warming, fire-and-forget stats, non-blocking telemetry).
- Use a **durable distributed message broker** (Celery, ARQ, Redis Streams, AWS SQS) with persistent storage, exponential retry backoff, and dead-letter queues (DLQ) for all transactional workflows.

```python
from tasks.queue import celery_app

@app.post("/payments/webhook")
async def stripe_webhook(event: WebhookEvent):
    # DURABLE: Persisted in Redis/RabbitMQ with guaranteed at-least-once delivery
    celery_app.send_task(
        "tasks.fulfill_subscription",
        args=[event.customer_id, event.amount],
        countdown=0,
        retry=True,
        retry_policy={"max_retries": 5, "interval_start": 2},
    )
    return {"status": "queued"}
```

---

## 5. Mutating Response Headers During `http.response.body` (ASGI Protocol Violation)

### Failure Mechanism
In the ASGI state machine, HTTP response delivery follows two sequential phases:
1. `http.response.start`: Declares the HTTP status code and response headers.
2. `http.response.body`: Transmits body byte chunks.

Once `http.response.start` is sent via `send()`, the HTTP response headers are **permanently sealed and transmitted over the TCP socket**. Attempting to modify headers inside `http.response.body` (e.g., trying to calculate total stream duration or hashing the streamed payload) violates the ASGI specification and raises:
`RuntimeError: Caught response headers modification after http.response.start.`

### Vulnerable Implementation
```python
# CRITICAL BUG: Attempting to modify headers after start has already been dispatched
class FaultyStreamingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        total_bytes = 0

        async def send_wrapper(message):
            nonlocal total_bytes
            if message["type"] == "http.response.body":
                total_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    # CRASH! Headers are already sent to the client!
                    # You cannot inject Content-Length or checksums here!
                    pass
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

### Production Remediation
All standard HTTP headers must be calculated and finalized **before or during** `http.response.start`. If metadata can only be known after the stream completes (e.g., stream checksum, record count), use **HTTP/1.1 / HTTP/2 Trailer Headers** (`Trailer: X-Checksum`) or embed metadata directly in the terminal streaming chunk (e.g., SSE termination event `event: done\ndata: {"total_tokens": 150}`).

---

## 6. Thread-Safety Catastrophe: Using `threading.local` Across Asynchronous Middleware

### Failure Mechanism
In ASGI applications, a single OS thread concurrently executes hundreds of interleaved coroutines on the asyncio event loop. 

When developers use Python's legacy `threading.local()` to store request metadata (e.g., `current_user_id`, `tenant_id`, `correlation_id`), the context is shared across **all concurrent requests running on that same thread**. Request A sets `tenant_id = 42`, awaits database I/O; Request B arrives on the same thread and sets `tenant_id = 99`. When Request A resumes execution, it reads `tenant_id = 99`! This causes catastrophic cross-tenant data leaks and security breaches.

### Vulnerable Implementation
```python
# CRITICAL SECURITY VULNERABILITY: threading.local leaks data across async requests!
import threading
from fastapi import FastAPI, Request

thread_local = threading.local()

@app.middleware("http")
async def leaky_auth_middleware(request: Request, call_next):
    # FATAL FLAW: Concurrent coroutines overwrite each other's tenant_id!
    thread_local.tenant_id = request.headers.get("X-Tenant-ID")
    return await call_next(request)
```

### Production Remediation
Use Python's native `contextvars.ContextVar`. Context variables are coroutine-safe and bound to the specific asynchronous task tree, guaranteeing strict isolation between concurrent requests without data leakage.

```python
import contextvars
from typing import Optional

# Coroutine-safe context variable
tenant_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tenant_id", default=None)

class SafeContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            tenant = headers.get(b"x-tenant-id", b"").decode("latin1") or None
            # Strictly scoped to this coroutine and its spawned children
            token = tenant_id_ctx.set(tenant)
            try:
                await self.app(scope, receive, send)
            finally:
                tenant_id_ctx.reset(token)
        else:
            await self.app(scope, receive, send)
```

---

## 7. Buffer Bloat & Uncontrolled Producer Runaway in Streaming Generators

### Failure Mechanism
If an async generator produces data faster than a slow mobile client can consume it, and the generator does not yield control to the event loop or heed backpressure, Uvicorn's outgoing send buffer balloons in memory (**Buffer Bloat**).

For instance, yielding unthrottled 1MB chunks in an infinite loop while the client reads at 50 KB/s causes server RAM usage to skyrocket from 100 MB to 10 GB within seconds, triggering the Linux OOM killer.

### Vulnerable Implementation
```python
# CRITICAL RESOURCE EXHAUSTION: Producer floods network buffer without yielding
async def unthrottled_generator():
    while True:
        # Generates 10MB chunks continuously without yielding control
        chunk = b"X" * (10 * 1024 * 1024)
        yield chunk  # If client network is slow, server memory balloons to gigabytes!
```

### Production Remediation
1. Chunk sizes should be bounded between **8 KB and 64 KB**.
2. Avoid oversized individual chunks.
3. Trust ASGI `send()` backpressure: ASGI `send` awaits TCP socket buffer flushes. Do not spawn unbounded detached tasks that bypass `StreamingResponse`.

```python
async def bounded_stream_generator(data_source, chunk_size: int = 32 * 1024):
    """Produces 32KB chunks respecting network backpressure."""
    async for record in data_source:
        chunk = record.to_bytes()
        yield chunk
        # Implicit backpressure: StreamingResponse awaits send() for each chunk
```

---

## Summary Matrix

| Antipattern | Primary Risk | Failure Manifestation | Production Fix |
| :--- | :--- | :--- | :--- |
| **`BaseHTTPMiddleware`** | 70x+ Latency Overhead | Task group queueing & broken backpressure | Author Pure ASGI middleware (`__call__`) |
| **Session in Background** | Service Crash / Data Inconsistency | `Closed transaction` / `DetachedInstanceError` | Decoupled session factory inside task |
| **Sequential `is_disconnected`** | Infinite Request Hang | `receive()` blocks generator execution | Concurrent watcher task with `asyncio.Event` |
| **In-Memory Queue for Billing** | Permanent Data Loss | Lost tasks upon pod reboot or OOM | Persistent broker (Celery / Redis Streams) |
| **Header Mutation in Body** | Protocol Violation | `RuntimeError: Headers already sent` | Finalize headers before `http.response.start` |
| **`threading.local` in Async** | Cross-Tenant Data Breach | Coroutine thread sharing overwrites state | Python `contextvars.ContextVar` |
| **Unbounded Chunking** | Server OOM Crash | Buffer bloat on slow client networks | 8KB–64KB bounded chunks + ASGI backpressure |
