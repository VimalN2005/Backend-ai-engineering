# Practical Engineering Exercises: BackgroundTasks, Middleware & Streaming

This document presents 3 enterprise-grade challenges focusing on asynchronous request processing, custom ASGI middleware, and streaming architectures in FastAPI. Each exercise includes architectural specifications, production-ready reference implementations, and verification harnesses.

---

## Exercise 1: Pure ASGI Dynamic Gzip Compression Middleware

### Objective
High-volume APIs stream large payloads (JSON, CSV, logs). Transferring uncompressed text over the wire wastes network bandwidth and degrades client latency. However, using standard buffering gzip middleware forces the entire response into server RAM, destroying the benefits of `StreamingResponse`.

Build a **Pure ASGI Streaming Gzip Middleware** that:
1. Inspects client `Accept-Encoding` header for `gzip`.
2. Intercepts `http.response.start`, replaces/appends `Content-Encoding: gzip`, and removes `Content-Length` (enforcing Chunked Transfer Encoding).
3. Compresses `http.response.body` chunks dynamically using Python's `zlib.compressobj(wbits=31)` on-the-fly.
4. Preserves streaming chunked transfer without buffering the entire body in memory.

### Complete Reference Implementation

```python
"""
Exercise 1 Reference Solution: Pure ASGI Streaming Gzip Compression Middleware
"""

from __future__ import annotations

import gzip
import io
import zlib
from typing import Any, Callable, Dict, List
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient

app = FastAPI(title="Pure ASGI Gzip Streaming Engine", version="1.0.0")


class PureASGIGzipMiddleware:
    """
    Pure ASGI middleware providing streaming Gzip compression.
    Compresses chunks on-the-fly without buffering the entire response in memory.
    """

    def __init__(self, app: Any, minimum_size: int = 128) -> None:
        self.app = app
        self.minimum_size = minimum_size

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Dict[str, Any]], Any],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check client Accept-Encoding
        headers_dict = dict(scope.get("headers", []))
        accept_encoding = headers_dict.get(b"accept-encoding", b"").decode("latin1")
        if "gzip" not in accept_encoding:
            await self.app(scope, receive, send)
            return

        # Initialize zlib streaming compressor with GZIP framing (wbits = 16 + MAX_WBITS = 31)
        compressor = zlib.compressobj(level=6, method=zlib.DEFLATED, wbits=31)
        initial_message_sent = False

        async def send_wrapper(message: Dict[str, Any]) -> None:
            nonlocal initial_message_sent

            if message["type"] == "http.response.start":
                # Filter out original Content-Length and inject Content-Encoding: gzip
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"content-length"
                ]
                headers.append((b"content-encoding", b"gzip"))
                message["headers"] = headers
                initial_message_sent = True
                await send(message)

            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)

                compressed_body = compressor.compress(body)
                if not more_body:
                    # Flush the remaining zlib buffers on the final chunk
                    compressed_body += compressor.flush()

                message["body"] = compressed_body
                await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(PureASGIGzipMiddleware)


@app.get("/data/large-text")
async def get_large_text():
    # Return repetitive text that compresses significantly
    return PlainTextResponse("FastAPI Pure ASGI Streaming Gzip Middleware! " * 50)


# ==============================================================================
# Verification Harness
# ==============================================================================

if __name__ == "__main__":
    with TestClient(app) as client:
        # Request with gzip accept encoding
        resp = client.get("/data/large-text", headers={"Accept-Encoding": "gzip"})
        assert resp.status_code == 200
        assert resp.headers["content-encoding"] == "gzip"

        # Verify compressed bytes
        raw_bytes = resp.content
        uncompressed = gzip.decompress(raw_bytes).decode("utf-8")
        assert "FastAPI Pure ASGI Streaming Gzip Middleware!" in uncompressed
        print("[TEST PASSED] Pure ASGI Gzip compression correctly compressed and decompressed on wire.")
        print(f"  Raw uncompressed length: {len(uncompressed)} bytes")
        print(f"  Gzip compressed length  : {len(raw_bytes)} bytes")
        print(f"  Compression Ratio       : {len(uncompressed) / len(raw_bytes):.1f}x reduction")
```

---

## Exercise 2: Resilient Client-Disconnect-Aware LLM Streaming Pipeline (SSE)

### Objective
In real-time generative AI applications (LLM chat, code completion), streaming tokens consume expensive GPU resources and API provider tokens. When a user navigates away or closes their browser, continuing to generate tokens burns money and blocks worker threads.

Build a **Resilient SSE Streaming Pipeline** that:
1. Emits standard W3C Server-Sent Events (`event: token\nid: <seq>\ndata: <json>\n\n`).
2. Concurrently monitors incoming ASGI `http.disconnect` events using a background listener task without blocking the token generation loop.
3. Immediately terminates generation upon detecting a disconnect, cancels in-flight LLM calls, and logs a structured telemetry event detailing tokens generated vs. tokens aborted.

### Complete Reference Implementation

```python
"""
Exercise 2 Reference Solution: Disconnect-Aware LLM Streaming Pipeline
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

app = FastAPI(title="Disconnect-Aware SSE Streamer", version="1.0.0")
logger = logging.getLogger("ai_streaming")


class LLMStreamContext:
    def __init__(self) -> None:
        self.tokens_emitted = 0
        self.aborted = False


async def robust_llm_token_streamer(
    prompt: str,
    request: Request,
) -> AsyncGenerator[str, None]:
    """
    Yields SSE tokens while concurrently detecting client disconnects.
    """
    ctx = LLMStreamContext()
    disconnect_event = asyncio.Event()

    async def disconnect_monitor():
        """Watches the raw ASGI receive channel for http.disconnect."""
        try:
            while True:
                message = await request.receive()
                if message.get("type") == "http.disconnect":
                    disconnect_event.set()
                    break
        except asyncio.CancelledError:
            pass

    # Launch disconnect listener concurrently
    monitor_task = asyncio.create_task(disconnect_monitor())

    simulated_tokens = [
        "Language", " models", " stream", " output", " token", " by", " token.",
        " Fast", " detection", " of", " client", " drops", " saves", " computing", " costs."
    ]

    try:
        for idx, token in enumerate(simulated_tokens):
            if disconnect_event.is_set():
                ctx.aborted = True
                logger.warning(
                    f"[STREAM-ABORT] Client dropped at token {idx}/{len(simulated_tokens)}. Halting generation."
                )
                break

            ctx.tokens_emitted += 1
            payload = json.dumps({"token": token, "index": idx})
            yield f"event: token\nid: {idx}\ndata: {payload}\n\n"
            await asyncio.sleep(0.01)

        if not ctx.aborted:
            yield "event: complete\nid: 999\ndata: [DONE]\n\n"

    finally:
        # Guarantee monitor task cancellation to prevent task leaks
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)


@app.get("/api/v1/generate")
async def generate_completion(prompt: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        robust_llm_token_streamer(prompt, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ==============================================================================
# Verification Harness
# ==============================================================================

if __name__ == "__main__":
    with TestClient(app) as client:
        # Full stream test
        resp = client.get("/api/v1/generate?prompt=AI")
        assert resp.status_code == 200
        frames = [f for f in resp.text.split("\n\n") if f.strip()]
        assert len(frames) == 16  # 15 tokens + 1 complete frame
        assert "event: complete" in frames[-1]
        print("[TEST PASSED] Resilient SSE stream successfully generated all tokens.")
```

---

## Exercise 3: Decoupled Outbox Pattern via FastAPI `BackgroundTasks`

### Objective
A classic enterprise distributed systems problem: An HTTP request creates an entity in the primary database and must publish an event to an external message broker (RabbitMQ/Kafka). If the server crashes or the network blips between saving to the database and sending to the broker:
- **Dual Write Trap**: The order is saved, but the event is never published, leading to distributed state drift.

Implement the **Transactional Outbox Pattern** with decoupled FastAPI `BackgroundTasks`:
1. The route handler writes both the `Order` and an `OutboxEvent` (status: `PENDING`) to an in-memory transactional database.
2. The endpoint returns HTTP 201 immediately and registers a `BackgroundTask` passing **only** the `outbox_id`.
3. The `BackgroundTask` opens an **independent database session**, reads the `OutboxEvent`, dispatches it to the simulated broker, and marks the event `PUBLISHED`.
4. If the background task fails, the event remains `PENDING` in the database, allowing an asynchronous recovery sweeper to republish it with zero message loss.

### Complete Reference Implementation

```python
"""
Exercise 3 Reference Solution: Transactional Outbox Pattern with BackgroundTasks
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from pydantic import BaseModel
from fastapi.testclient import TestClient

app = FastAPI(title="Transactional Outbox Engine", version="1.0.0")


class OutboxStatus(str, Enum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


@dataclass
class OutboxRecord:
    id: str
    event_type: str
    payload: dict
    status: OutboxStatus = OutboxStatus.PENDING
    published_at: Optional[float] = None


class MockDatabase:
    """Simulates a transactional database store."""

    def __init__(self) -> None:
        self.orders: Dict[str, dict] = {}
        self.outbox: Dict[str, OutboxRecord] = {}

    def commit_order_with_outbox(self, order_data: dict) -> Tuple[str, str]:
        order_id = f"ord_{uuid.uuid4().hex[:8]}"
        outbox_id = f"evt_{uuid.uuid4().hex[:8]}"

        # Atomically commit order and outbox record
        self.orders[order_id] = {"id": order_id, **order_data}
        self.outbox[outbox_id] = OutboxRecord(
            id=outbox_id,
            event_type="ORDER_CREATED",
            payload={"order_id": order_id, **order_data},
            status=OutboxStatus.PENDING,
        )
        return order_id, outbox_id


db = MockDatabase()
broker_published_messages: List[dict] = []


class MockMessageBroker:
    """Simulates external messaging broker (Kafka / RabbitMQ)."""

    @staticmethod
    async def publish(topic: str, payload: dict) -> None:
        await asyncio.sleep(0.01)  # Simulate network hop
        broker_published_messages.append({"topic": topic, "data": payload, "ts": time.time()})


# ==============================================================================
# Decoupled Background Worker
# ==============================================================================

async def outbox_dispatcher_task(outbox_id: str) -> None:
    """
    Decoupled task: Reads outbox record, publishes to broker, updates status.
    """
    record = db.outbox.get(outbox_id)
    if not record or record.status != OutboxStatus.PENDING:
        return

    try:
        # Publish to external message broker
        await MockMessageBroker.publish(topic="orders.events", payload=record.payload)
        record.status = OutboxStatus.PUBLISHED
        record.published_at = time.time()
    except Exception:
        record.status = OutboxStatus.FAILED


# ==============================================================================
# Route Handler
# ==============================================================================

class OrderCreateRequest(BaseModel):
    customer_id: str
    total_amount: float


@app.post("/api/v1/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    request: OrderCreateRequest,
    background_tasks: BackgroundTasks,
):
    # 1. Atomic write in primary storage
    order_id, outbox_id = db.commit_order_with_outbox(request.model_dump())

    # 2. Enqueue decoupled background dispatch using primitive ID only
    background_tasks.add_task(outbox_dispatcher_task, outbox_id=outbox_id)

    return {
        "status": "created",
        "order_id": order_id,
        "outbox_event_id": outbox_id,
    }


# ==============================================================================
# Verification Harness
# ==============================================================================

if __name__ == "__main__":
    with TestClient(app) as client:
        # 1. Create Order
        resp = client.post(
            "/api/v1/orders",
            json={"customer_id": "cust_12345", "total_amount": 250.75},
        )
        assert resp.status_code == 201
        data = resp.json()
        outbox_id = data["outbox_event_id"]

        # Verify Outbox record was updated to PUBLISHED by background task
        outbox_entry = db.outbox[outbox_id]
        assert outbox_entry.status == OutboxStatus.PUBLISHED
        assert len(broker_published_messages) == 1
        assert broker_published_messages[0]["topic"] == "orders.events"
        assert broker_published_messages[0]["data"]["customer_id"] == "cust_12345"

        print("[TEST PASSED] Transactional outbox event created and dispatched via BackgroundTasks.")
        print(f"  Order ID : {data['order_id']}")
        print(f"  Outbox ID: {outbox_id} (Status: {outbox_entry.status})")
```
