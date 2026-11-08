# Senior & Staff Engineering Interview Q&A: BackgroundTasks, Middleware & Streaming

This document compiles the top 5 advanced architectural and systems-design interview questions regarding FastAPI request pipelining, ASGI middleware internals, Server-Sent Events (SSE), and background task execution, designed for Senior and Staff Backend & AI Systems Engineers.

---

### Question 1: Why does Starlette's `BaseHTTPMiddleware` degrade performance and break streaming backpressure, and how do you implement high-throughput pure ASGI middleware?

#### Conceptual Answer
`BaseHTTPMiddleware` abstracts the raw ASGI interface into a convenient `async def dispatch(request: Request, call_next)` paradigm. While intuitive, its internal mechanics introduce severe architectural penalties in high-concurrency systems:

1. **Task Group Allocation & Latency Overhead**: For every incoming request, `BaseHTTPMiddleware` creates an AnyIO task group (`anyio.create_task_group()`) and an AnyIO memory object stream (`create_memory_object_stream()`). The route handler executes in a spawned child task while the dispatch method waits for the memory stream. In benchmarks, this introduces **150–350 µs of latency overhead per request**, compared to **4–6 µs for pure ASGI middleware** (a **70x–80x performance penalty**).
2. **Streaming Response Buffering**: When downstream endpoints return a `StreamingResponse`, `BaseHTTPMiddleware` intercepts the ASGI `send` messages and buffers response chunks into internal memory streams. This destroys native TCP socket backpressure: if a client is slow, the middleware continues pulling chunks from the upstream generator, causing server-side memory ballooning.
3. **ContextVar Scope Mutations**: Spawning child tasks across AnyIO task groups can mutate or sever `contextvars` inheritance in certain Python and AnyIO versions, leading to lost distributed tracing IDs.

#### Pure ASGI Implementation
Pure ASGI middleware operates directly on the raw ASGI 3-tuple: `(scope, receive, send)`. It wraps the `send` callable to inspect or modify messages (e.g., injecting headers during `http.response.start`) without buffering chunks or spawning auxiliary task groups:

```python
from typing import Any, Callable, Dict
import time

class PureASGITimingMiddleware:
    """Production-grade zero-allocation pure ASGI middleware."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Dict[str, Any]], Any],
    ) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        t_start_ns = time.perf_counter_ns()

        async def send_wrapper(message: Dict[str, Any]) -> None:
            # Intercept response start BEFORE headers are flushed to the socket
            if message["type"] == "http.response.start":
                duration_ms = (time.perf_counter_ns() - t_start_ns) / 1_000_000.0
                headers = list(message.get("headers", []))
                headers.append((b"x-process-time-ms", f"{duration_ms:.3f}".encode("latin1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

---

### Question 2: How do Server-Sent Events (SSE) compare to WebSockets for real-time generative AI token streaming, and what are the transport tradeoffs?

#### Conceptual Answer
For generative AI applications (LLM chat, code generation, completions), streaming tokens is primarily **unidirectional** (the client sends a prompt once; the server streams dozens or hundreds of tokens sequentially back to the client).

| Architectural Dimension | Server-Sent Events (SSE) | WebSockets (`ws://`, `wss://`) |
| :--- | :--- | :--- |
| **Communication Pattern** | Unidirectional (Server -> Client) | Full Duplex Bidirectional |
| **Transport Layer** | Standard HTTP/1.1 or HTTP/2 | TCP connection upgraded from HTTP |
| **HTTP/2 Multiplexing** | **Native Multiplexing**: Dozens of SSE streams share a single TCP connection. | Bypasses HTTP/2 multiplexing; each socket requires a dedicated TCP connection. |
| **Proxy / CDN Traversal** | Transits corporate firewalls, reverse proxies, and CDNs like standard HTTP. | Frequently blocked or abruptly terminated by corporate proxies, firewalls, and WAFs. |
| **Reconnection & Resilience** | Built-in browser reconnection with `Last-Event-ID` tracking. | Requires custom application-level reconnection and heartbeat/ping logic. |
| **Backpressure Control** | Governed by standard TCP flow control via ASGI `send()`. | Governed by WebSocket buffer framing; requires manual flow control. |
| **Format** | UTF-8 text framing (`data: ...\n\n`). | Binary or Text frames. |

#### Engineering Deep Dive
- **The HTTP/1.1 Browser Connection Limit Trap**: Under HTTP/1.1, web browsers enforce a hard limit of **6 concurrent open connections per domain**. If a user opens 6 browser tabs streaming SSE over HTTP/1.1, a 7th tab will freeze completely.
- **Production Solution**: Mandate **HTTP/2 or HTTP/3** on the edge reverse proxy (Cloudflare, Nginx, Envoy). Under HTTP/2, all SSE streams multiplex seamlessly over a single underlying TCP connection, eliminating the 6-connection ceiling.
- **Proxy Buffering**: Nginx buffers responses by default. In SSE streaming, the server must emit the header `X-Accel-Buffering: no` and `Cache-Control: no-cache` to force immediate chunk flushing.

---

### Question 3: What are the architectural boundaries of FastAPI's `BackgroundTasks`, and when must you migrate to a distributed queue like Celery or ARQ?

#### Conceptual Answer
FastAPI's `BackgroundTasks` is an **in-process, memory-bound task runner** executed after an HTTP response is sent. Understanding its execution boundary is essential for production reliability:

#### Execution Boundary Mechanics
1. **Event Loop vs. Threadpool**:
   - `async def` task: Starlette awaits the coroutine directly on the main event loop (`await task()`). If an async background task performs blocking synchronous CPU math, it freezes the entire Uvicorn worker!
   - Synchronous `def` task: Starlette offloads the callable to AnyIO's threadpool worker via `anyio.to_thread.run_sync(task)`.
2. **Resource Lifecycle Isolation**: Background tasks execute *after* the request dependency exit stack unwinds. Passing request-scoped database sessions or ORM models leads to `DetachedInstanceError` or closed connection errors.

#### The 4 Critical Migration Triggers
You must immediately migrate from `BackgroundTasks` to a distributed queue (Celery, ARQ, Temporal, Redis Streams) when:

1. **Durability & Zero Data Loss**: `BackgroundTasks` stores pending jobs in Python process heap memory. A Kubernetes rolling deployment, pod eviction, OOM kill, or worker crash permanently deletes all pending tasks.
2. **Retries with Exponential Backoff**: `BackgroundTasks` has no built-in retry mechanism. If an external API blips, the task fails and is discarded. Distributed queues provide configurable retries, jittered backoff, and Dead Letter Queues (DLQ).
3. **CPU-Intensive Tasks**: Tasks like video transcoding, PDF generation, or embedding generation will saturate the web server's CPU, starving HTTP request handling.
4. **Task Rate Limiting & Prioritization**: Distributed queues allow assigning dedicated worker pools (e.g., `high-priority-billing` vs. `low-priority-analytics`) and throttling external API concurrency.

---

### Question 4: How do you prevent cross-request context leakage when propagating correlation IDs and tenant IDs across async middleware in Python?

#### Conceptual Answer
In synchronous WSGI servers (Gunicorn + Sync Workers), each request runs in its own dedicated OS process or thread. Storing request metadata in `threading.local()` was safe because the thread was exclusively dedicated to that request.

In ASGI servers (Uvicorn), a single OS thread concurrently executes thousands of interleaved coroutines. When Coroutine A awaits an asynchronous database query, the event loop switches execution to Coroutine B on the **exact same OS thread**. If `threading.local()` is used, Coroutine B overwrites Coroutine A's state! When Coroutine A resumes, it executes using Coroutine B's tenant ID, causing catastrophic security breaches.

#### The Solution: PEP 567 `contextvars`
Python's `contextvars.ContextVar` provides coroutine-safe context propagation:
- Every asyncio `Task` maintains its own internal context dictionary.
- When an async task spawns child tasks or runs coroutines, the context is inherited by reference.
- Any mutation via `.set()` is local to that task branch.

#### Production Implementation Rule
Always capture the `Token` returned by `ContextVar.set()` and reset it in a `finally` block:

```python
import contextvars
from typing import Optional

request_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)

class SafeContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            token = request_id_ctx.set(str(uuid.uuid4()))
            try:
                await self.app(scope, receive, send)
            finally:
                # MANDATORY: Prevent context leakage back to worker pool
                request_id_ctx.reset(token)
        else:
            await self.app(scope, receive, send)
```

---

### Question 5: How does HTTP backpressure work in ASGI streaming responses, and how can an uncapped generator cause an Out-Of-Memory (OOM) crash?

#### Conceptual Answer
**Backpressure** is a flow-control mechanism where a downstream consumer signals an upstream producer to slow down or pause data generation to avoid buffer saturation.

#### ASGI Backpressure Mechanics
In ASGI, streaming responses transmit body chunks via `send({"type": "http.response.body", "body": chunk, "more_body": True})`.
1. The ASGI server (Uvicorn) writes the chunk to the operating system's kernel TCP socket buffer.
2. If the client is reading slowly (e.g., a mobile device on 3G) and the TCP socket buffer fills up, the OS kernel signals socket saturation.
3. The `await send(...)` coroutine **suspends execution** and does not resolve until the client acknowledges received packets (TCP ACKs) and frees space in the socket buffer.
4. Because the server generator awaits `send()`, the generator is naturally paused.

#### How Uncapped Generators Cause OOM Crashes
Buffer Bloat and OOM crashes occur when developers bypass this cooperative backpressure mechanism:
1. **Detached Worker Queues**: If a background task reads 1,000,000 rows from a database and pushes them into an unbounded `asyncio.Queue`, while the streaming endpoint slowly drains the queue, memory balloons into gigabytes.
2. **`BaseHTTPMiddleware` Buffering**: As explored in Question 1, `BaseHTTPMiddleware` buffers chunks in an internal memory stream, severing the link between socket buffer capacity and generator execution.
3. **Oversized Chunk Sizes**: Yielding 50MB chunks in a loop forces multi-gigabyte memory allocations across concurrent streams.

#### Production Best Practice
- Rely on linear generator execution where each `yield` feeds directly into `StreamingResponse`.
- Bound chunk sizes strictly between **8 KB and 64 KB**.
- Monitor `request.is_disconnected()` to abort generation immediately when clients cancel downloads.
