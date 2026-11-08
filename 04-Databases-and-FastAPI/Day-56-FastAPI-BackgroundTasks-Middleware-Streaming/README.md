# Day 56: FastAPI BackgroundTasks, Custom Middleware & Streaming Responses

A production-grade architectural guide to asynchronous request pipelining, non-blocking background task execution, high-performance ASGI middleware engineering, and low-latency chunked/SSE streaming in modern FastAPI systems.

---

## 1. ASGI Anatomy & The Middleware Onion Architecture

FastAPI is built on top of Starlette, an asynchronous web framework implementing the **Asynchronous Server Gateway Interface (ASGI)** specification. Unlike legacy WSGI (`environ, start_response`), which was synchronous and single-pass, ASGI defines an asynchronous callable with the signature:

$$\text{ASGIApp} = \text{Callable}[[\text{Scope}, \text{Receive}, \text{Send}], \text{Coroutine}[Any, Any, None]]$$

```
+-------------------------------------------------------------------------+
|                              ASGI Server (Uvicorn / Hypercorn)          |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Middleware 1: Correlation ID (Ingress: inject ID -> Egress: log latency)|
|   +-------------------------------------------------------------------+ |
|   | Middleware 2: Rate Limiting (Token Bucket / Redis Sliding Window) | |
|   |   +-------------------------------------------------------------+ | |
|   |   | Middleware 3: Security & CORS Header Injection              | | |
|   |   |   +-------------------------------------------------------+ | | |
|   |   |   | FastAPI Routing & Dependency Graph (Depends)          | | | |
|   |   |   |   +-------------------------------------------------+ | | | |
|   |   |   |   | Route Handler -> Generates Response / Streams   | | | | |
|   |   |   |   +-------------------------------------------------+ | | | |
|   |   |   +-------------------------------------------------------+ | | |
|   |   +-------------------------------------------------------------+ | |
|   +-------------------------------------------------------------------+ |
+-------------------------------------------------------------------------+
```

### The Three ASGI Primitives
1. **`scope: dict`**: A connection-lived dictionary containing connection metadata (protocol type `'http' | 'websocket'`, HTTP method, path, headers, client IP/port).
2. **`receive: Callable[[], Awaitable[dict]]`**: An async callable yielding incoming events from the client (e.g., `http.request`, `http.disconnect`).
3. **`send: Callable[[dict], Awaitable[None]]`**: An async callable dispatching outgoing events to the client (e.g., `http.response.start`, `http.response.body`).

The HTTP request-response cycle executes as a strict state machine:
```
Client -> [http.request (more_body=True)] -> [http.request (more_body=False)]
Server -> [http.response.start (status, headers)] -> [http.response.body (more_body=False)]
```

---

## 2. `BaseHTTPMiddleware` vs. Pure ASGI Middleware

FastAPI provides two ways to author custom middleware:
1. Subclassing Starlette's `BaseHTTPMiddleware` (`async def dispatch(request, call_next)`).
2. Implementing a **Pure ASGI Middleware** (`async def __call__(scope, receive, send)`).

### The Critical Pitfalls of `BaseHTTPMiddleware`
While `BaseHTTPMiddleware` offers a convenient, high-level `Request -> Response` interface, it has critical architectural flaws in high-throughput enterprise systems:

| Architectural Flaw | `BaseHTTPMiddleware` | Pure ASGI Middleware |
| :--- | :--- | :--- |
| **Streaming Response Buffering** | Buffers the entire response body in memory; breaks backpressure and streaming. | Streams chunks byte-by-byte without buffering. |
| **`contextvars` Propagation** | Uses `anyio.create_task_group()`, mutating context variable scoping across tasks. | Preserves native Python `contextvars` execution context. |
| **Latency Overhead** | High (~150–400 µs per request due to task group allocation and event queueing). | Negligible (~5–15 µs per request; direct coroutine invocation). |
| **Disconnect Propagation** | Obscures client disconnect events (`http.disconnect`) from streaming endpoints. | Allows native listening to client disconnect events. |

### Enterprise Recommendation
- Use `BaseHTTPMiddleware` only for simple, non-streaming endpoints where low throughput is acceptable.
- Use **Pure ASGI Middleware** for high-performance logging, rate limiting, authentication, header manipulation, and all streaming/SSE architectures.

---

## 3. Execution Order & Onion Unwinding

Middleware execution follows a strict **LIFO (Last-In, First-Out)** registration order:

```python
app = FastAPI()

app.add_middleware(MiddlewareA)  # Outer Layer (First registered, wraps everything)
app.add_middleware(MiddlewareB)  # Middle Layer
app.add_middleware(MiddlewareC)  # Inner Layer (Closest to route handler)
```

```
Request  Ingress Path : MiddlewareA -> MiddlewareB -> MiddlewareC -> Route Handler
Response Egress Path  : Route Handler -> MiddlewareC -> MiddlewareB -> MiddlewareA
```

- When `app.add_middleware(M)` is called, each middleware wraps the application instance beneath it.
- In Starlette, the **last middleware added becomes the outermost layer** encountered by incoming requests.

---

## 4. Context Propagation & `contextvars` in Asynchronous Python

In concurrent async applications, threads are shared across thousands of interleaved coroutines. Traditional thread-local storage (`threading.local`) is catastrophically broken because multiple concurrent HTTP requests execute on the same OS thread.

Python's `contextvars` module provides coroutine-safe context propagation:

```python
import contextvars
import uuid

# Global context variables bound to async coroutine task tree
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
tenant_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="")

class CorrelationIdMiddleware:
    """Pure ASGI middleware setting request context."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Extract or generate correlation ID
        headers = dict(scope.get("headers", []))
        corr_id = headers.get(b"x-request-id", str(uuid.uuid4()).encode()).decode()
        
        # Bind to contextvar for the entire task tree
        token = request_id_ctx.set(corr_id)
        try:
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers_list = list(message.get("headers", []))
                    headers_list.append((b"x-request-id", corr_id.encode()))
                    message["headers"] = headers_list
                await send(message)

            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_ctx.reset(token)
```

---

## 5. Request Correlation, Timing & Metric Injection

Modern microservices rely on distributed tracing headers (`X-Request-ID`, `traceparent`). Production middleware must:
1. Capture the exact ingress timestamp using `time.perf_counter_ns()`.
2. Intercept `http.response.start` to inject performance headers (`X-Process-Time`) and trace headers **before the response headers are transmitted to the wire**.
3. Log structured access metrics on egress.

### Crucial ASGI Rule
Once `http.response.start` has been sent via `send()`, HTTP response headers are **sealed and finalized**. Any attempt to modify headers inside `http.response.body` will raise an ASGI protocol violation error.

---

## 6. FastAPI `BackgroundTasks` Execution Lifecycle

FastAPI provides a lightweight, in-process task scheduler via `BackgroundTasks`.

```python
from fastapi import BackgroundTasks, FastAPI

app = FastAPI()

def send_welcome_email(email: str):
    # Executed AFTER the HTTP response is flushed to client
    pass

@app.post("/register")
async def register_user(email: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(send_welcome_email, email)
    return {"status": "User created. Email queued."}
```

### Lifecycle Execution Mechanics
1. **Client Dispatched Response**: The endpoint returns a response model. Starlette sends `http.response.start` and flushes all `http.response.body` chunks.
2. **Post-Response Execution**: Starlette's `Response.__call__` awaits the completion of all tasks registered in `background_tasks.tasks`.
3. **Async vs Sync Handling**:
   - If the task callable is an `async def` coroutine, Starlette awaits it directly on the event loop: `await task()`.
   - If the task callable is a standard synchronous `def`, Starlette offloads it to a thread pool via `anyio.to_thread.run_sync(task)`.

---

## 7. `BackgroundTasks` vs. Distributed Queues (Celery, ARQ, Redis Streams)

A frequent architectural failure is treating FastAPI's in-process `BackgroundTasks` as a substitute for an enterprise distributed task queue.

| Dimension | FastAPI `BackgroundTasks` | Distributed Task Queue (Celery / ARQ) |
| :--- | :--- | :--- |
| **Storage & Persistence** | In-Memory (Python heap inside Uvicorn worker) | Persistent Broker (Redis, RabbitMQ, SQS) |
| **Worker Process Crash / Restart** | **Catastrophic Data Loss**: All queued tasks are instantly lost. | **Durable**: Tasks remain queued in broker and retry on worker reboot. |
| **Horizontal Scalability** | Bound to the single web server instance handling the HTTP request. | Horizontally scalable independent worker pools. |
| **Retry & Dead Letter Queues** | No built-in retry, backoff, or dead-letter support. | Exponential backoff, jitter, dead-letter exchanges (DLQ). |
| **CPU-Bound Task Impact** | Blocks event loop or saturates web thread pool. | Isolated on dedicated CPU-optimized compute nodes. |
| **Ideal Use Cases** | Audit trail logging, lightweight metrics, non-critical webhooks. | Transactional emails, billing, PDF generation, model inference. |

---

## 8. The Database Session & Resource Lifetime Trap with `BackgroundTasks`

The most common bug in FastAPI background processing:

```python
# CRITICAL BUG: DetachedInstanceError & Closed Connection
@app.post("/orders")
async def create_order(
    order: OrderCreate,
    db: AsyncSession = Depends(get_db_session),
    background_tasks: BackgroundTasks
):
    db_order = await order_repo.create(db, order)
    # BUG: Passing request-scoped session to background task!
    background_tasks.add_task(send_order_receipt, db, db_order.id)
    return {"order_id": db_order.id}
```

### Why This Fails
1. The dependency `get_db_session` uses a `yield` statement. As soon as the route handler returns, FastAPI's `AsyncExitStack` executes the `finally` block, closing the `AsyncSession` and returning the database connection to the connection pool.
2. When `send_order_receipt` starts running, the session is already **closed**. Attempting to query the database raises:
   `sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside session.`
3. If an ORM model instance (`db_order`) is passed into the task and an unloaded relationship is accessed, SQLAlchemy throws:
   `sqlalchemy.orm.exc.DetachedInstanceError: Instance is not bound to a Session.`

### Production Solution: Decoupled Session Factory
Background tasks must receive only primitive IDs (e.g., `order_id: int`) and manage their own, independent session lifecycle using an explicit context manager:

```python
async def send_order_receipt(order_id: int):
    # Independent session lifecycle isolated from HTTP request
    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        # Process order receipt...
```

---

## 9. Streaming Responses & Chunked Transfer Encoding

Standard HTTP responses buffer the entire payload in memory, compute the `Content-Length` header, and send the response in one continuous transmission. For multi-gigabyte files, large data exports, or real-time event feeds, buffering the response exhausts server RAM and causes out-of-memory (OOM) crashes.

### Chunked Transfer Encoding Mechanics
`StreamingResponse` leverages HTTP/1.1 **Chunked Transfer Encoding** (`Transfer-Encoding: chunked`). The server does not send a `Content-Length` header. Instead, data is sent as a series of size-prefixed chunks:

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Transfer-Encoding: chunked

1a\r\n
abcdefghijklmnopqrstuvwxyz\r\n
10\r\n
1234567890abcdef\r\n
0\r\n
\r\n
```

In FastAPI:
```python
from fastapi.responses import StreamingResponse

async def fake_data_streamer():
    for i in range(100):
        yield f"data_packet_{i}\n".encode("utf-8")
        await asyncio.sleep(0.01)

@app.get("/stream")
async def stream_data():
    return StreamingResponse(fake_data_streamer(), media_type="text/plain")
```

---

## 10. Server-Sent Events (SSE) & Real-Time AI Generation

Server-Sent Events (SSE) (W3C Recommendation) provide a lightweight, unidirectional HTTP streaming protocol over standard HTTP connections, avoiding the complexity and proxy-incompatibility of WebSockets.

### SSE Framing Protocol
Every SSE packet consists of UTF-8 text ending with two consecutive newlines (`\n\n`):

```http
event: message\n
id: 1042\n
retry: 5000\n
data: {"token": "FastAPI", "index": 0}\n\n
```

- `data`: The payload string. Multi-line data prefixes each line with `data: `.
- `event`: Custom event name (listened to via `eventSource.addEventListener("eventName", ...)`).
- `id`: Event sequence ID for auto-reconnection using the `Last-Event-ID` header.
- `retry`: Reconnection delay in milliseconds if the connection drops.
- `: comment`: Comments (lines starting with `:`) used as keep-alive heartbeats.

SSE is the industry standard for **LLM token streaming** (OpenAI, Anthropic, Gemini API streaming interfaces).

---

## 11. Client Disconnects & Cancellation Handling in Streaming

When a user closes their browser tab, cancels a download, or aborts an LLM query, the server's streaming generator must stop generating tokens or querying data immediately.

### Failure Mechanism
If the generator ignores client disconnects, it continues running in the background, consuming CPU, GPU, database connections, and external API credits for an abandoned request.

### Detection Mechanism
In pure ASGI, client disconnects trigger an incoming `{"type": "http.disconnect"}` event on `receive()`.

```python
from fastapi import Request
from fastapi.responses import StreamingResponse

async def token_generator(request: Request):
    for i in range(1000):
        # Explicit disconnect check
        if await request.is_disconnected():
            logger.info("Client aborted connection. Cleaning up resources.")
            break
        yield f"token_{i} ".encode()
        await asyncio.sleep(0.1)

@app.get("/ai-stream")
async def ai_stream(request: Request):
    return StreamingResponse(token_generator(request), media_type="text/event-stream")
```

---

## 12. Backpressure, Flow Control & Buffer Bloat

### The Producer-Consumer Mismatch
In streaming architectures, the server's async generator (the **producer**) may generate data at 100 MB/s, while a slow client on a mobile network (the **consumer**) can only receive data at 50 KB/s.

Without **backpressure**, Uvicorn and Starlette buffer unconsumed chunks in an in-memory queue. If the generator produces 50,000 chunks before the client reads 100, the server's memory balloons uncontrollably (**Buffer Bloat**).

### ASGI Backpressure Mechanics
In ASGI, `send({"type": "http.response.body", "body": chunk, "more_body": True})` does **not** return immediately if the underlying TCP socket buffer is full. The `send()` awaitable suspends the generator until the operating system's TCP socket buffer has flushed bytes to the network interface card (NIC), naturally throttling the producer to match the consumer's throughput.

---

## 13. Security, CORS & Rate-Limiting Middleware

Middleware executes before routing and request validation. Authoring security layers as pure ASGI middleware provides maximum protection:
1. **DDoS & Scraping Protection**: Reject abusive requests with HTTP 429 before allocating Pydantic validation memory or initializing database sessions.
2. **CORS Protocol Enforcement**: Handle preflight `OPTIONS` requests and validate the `Origin` header against an explicit enterprise whitelist.
3. **Security Headers**: Inject `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy`, and `X-Frame-Options: DENY`.

---

## 14. Exception Handling Across the Middleware Boundary

A critical nuance of FastAPI:
- **FastAPI Exception Handlers** (`@app.exception_handler(CustomException)`) catch exceptions raised inside route handlers, dependencies, and validation layers.
- **Middleware Boundary**: If an exception is raised inside a custom middleware **before** calling `await self.app(scope, receive, send)` or **after** the inner application returns, FastAPI's exception handlers **will not catch it**.
- The unhandled exception bubbles up directly to the ASGI server (Uvicorn), causing a generic HTTP 500 error and dropping custom error schemas.

### Production Rule
Pure ASGI middleware must wrap `await self.app(scope, receive, send)` inside an explicit `try...except` block, catch internal exceptions defensively, and format an RFC 7807 problem details response directly via ASGI `send()`.

---

## 15. Production Readiness & Observability Architecture

### Enterprise Middleware Stack Checklist
- [x] **Traceability**: Unique correlation ID (`X-Request-ID`) bound to `contextvars` and injected into all log records.
- [x] **Timing & Latency Metrics**: High-precision `perf_counter_ns` timing injected into `X-Process-Time` header.
- [x] **Observability**: Prometheus metrics middleware tracking request rate, duration histograms, and status code counters.
- [x] **Clean Disconnects**: Streaming endpoints listening to `request.is_disconnected()` to terminate worker tasks promptly.
- [x] **Decoupled Session Lifecycles**: All `BackgroundTasks` manage their own session factories, never reusing request-scoped sessions.
- [x] **No Buffering in Middleware**: Pure ASGI implementations used for all streaming and SSE pipelines to prevent memory ballooning.
