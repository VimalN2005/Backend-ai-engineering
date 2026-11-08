# Rapid Revision & Quick Reference: BackgroundTasks, Middleware & Streaming

A condensed 5-minute revision guide covering FastAPI background tasks, pure ASGI middleware, streaming responses, and Server-Sent Events (SSE).

---

## 1. Pure ASGI Middleware Boilerplate

```python
from typing import Any, Callable, Dict
import time
import uuid

class ProductionASGIMiddleware:
    """Zero-overhead pure ASGI middleware template."""

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
        headers_dict = dict(scope.get("headers", []))
        corr_id = headers_dict.get(b"x-request-id", str(uuid.uuid4()).encode("latin1")).decode("latin1")

        async def send_wrapper(message: Dict[str, Any]) -> None:
            # Modify headers strictly before or during http.response.start
            if message["type"] == "http.response.start":
                duration_ms = (time.perf_counter_ns() - t_start_ns) / 1_000_000.0
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", corr_id.encode("latin1")))
                headers.append((b"x-process-time-ms", f"{duration_ms:.3f}".encode("latin1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

---

## 2. ContextVar Scope Propagation Template

```python
import contextvars
from typing import Optional

# Coroutine-safe ContextVar bound to async task tree
correlation_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)

# Ingress Middleware
token = correlation_id_ctx.set(correlation_id)
try:
    await self.app(scope, receive, send)
finally:
    # Always reset context token to avoid pollution
    correlation_id_ctx.reset(token)

# Access anywhere down the async call tree without passing parameters
current_trace_id = correlation_id_ctx.get()
```

---

## 3. BackgroundTasks with Decoupled Session Pattern

```python
from fastapi import BackgroundTasks, Depends, FastAPI
from db.session import async_session_factory

app = FastAPI()

# 1. Background Task Callable (Receives primitive ID, NOT request session)
async def process_audit_log(order_id: int):
    async with async_session_factory() as session:
        # Independent session lifecycle isolated from HTTP response exit stack
        order = await session.get(Order, order_id)
        # Record audit event...

# 2. Route Handler
@app.post("/orders")
async def create_order(
    order_in: OrderCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session)
):
    order = await order_repo.create(db, order_in)
    # Pass primitive scalar ID only
    background_tasks.add_task(process_audit_log, order.id)
    return {"order_id": order.id}
```

---

## 4. Chunked StreamingResponse Template

```python
from typing import AsyncGenerator
from fastapi.responses import StreamingResponse

async def csv_data_streamer() -> AsyncGenerator[bytes, None]:
    yield b"id,timestamp,status\n"
    for i in range(1000):
        # Stream 8KB-64KB chunks directly to client
        chunk = f"{i},2026-11-08T00:00:00Z,ACTIVE\n".encode("utf-8")
        yield chunk

@app.get("/export/csv")
async def export_csv():
    return StreamingResponse(
        csv_data_streamer(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=export.csv"}
    )
```

---

## 5. Server-Sent Events (SSE) AI Token Streamer

```python
import json
import asyncio
from fastapi import Request
from fastapi.responses import StreamingResponse

async def token_streamer(prompt: str, request: Request):
    tokens = ["Deep", " Learning", " and", " FastAPI", " streaming."]
    for idx, token in enumerate(tokens):
        # Format compliant W3C Server-Sent Event
        payload = json.dumps({"token": token, "index": idx})
        yield f"event: token\nid: {idx}\ndata: {payload}\n\n"
        await asyncio.sleep(0.02)
    # Terminal frame
    yield "event: done\nid: 999\ndata: [DONE]\n\n"

@app.get("/ai/stream")
async def ai_stream(prompt: str, request: Request):
    return StreamingResponse(
        token_streamer(prompt, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disables reverse proxy buffering
        }
    )
```

---

## 6. Architecture Comparison Matrix

| Property | Pure ASGI Middleware | `BaseHTTPMiddleware` |
| :--- | :--- | :--- |
| **Interface** | `__call__(scope, receive, send)` | `dispatch(request, call_next)` |
| **Dispatch Latency** | **4–6 µs** (Zero allocation) | **150–350 µs** (Task group overhead) |
| **Streaming Compatibility** | **100% Native**; chunk-by-chunk | **Breaks backpressure**; buffers streams |
| **ContextVar Safety** | Preserves native task context | Can mutate or drop context in child tasks |

---

## 7. High-Frequency Interview Flashcards

### Q1: Why is `BaseHTTPMiddleware` dangerous for streaming endpoints?
> **Answer**: `BaseHTTPMiddleware` wraps downstream execution in an AnyIO task group and reads the response through an internal memory object stream. This consumes incoming chunks into memory and severs the link between the client's TCP socket and the server generator, destroying backpressure and inflating latency by 70x+.

### Q2: Why does an active `AsyncSession` passed to `BackgroundTasks` fail with an error?
> **Answer**: FastAPI route dependencies unwind their `yield` exit stack immediately after the route handler returns. By the time the background task executes post-response, the session has already been closed. Pass primitive IDs only and open an independent session factory inside the task.

### Q3: How do you prevent proxy buffering in Server-Sent Events (SSE)?
> **Answer**: Send the HTTP response headers: `Cache-Control: no-cache` and `X-Accel-Buffering: no`. This instructs reverse proxies like Nginx not to buffer chunks in proxy memory, ensuring immediate real-time token delivery to the client.

### Q4: What is the mechanical difference between async and sync background tasks?
> **Answer**: If the callable is an `async def` coroutine, Starlette awaits it directly on the main event loop (`await task()`). If it is a standard synchronous `def`, Starlette runs it inside AnyIO's worker threadpool (`anyio.to_thread.run_sync(task)`) to prevent blocking the event loop.
