"""
Production Pipeline: BackgroundTasks, Pure ASGI Middleware & Streaming Responses
==================================================================================
Day 56: FastAPI BackgroundTasks, Custom Middleware & Streaming Responses

This module implements an enterprise-grade ASGI application featuring:
1. Pure ASGI Request Correlation & High-Precision Timing Middleware
2. Pure ASGI Token-Bucket Rate Limiter Middleware
3. BackgroundTasks with Decoupled Resource Lifecycles (Async & Sync)
4. Memory-Efficient Chunked Data Streaming (StreamingResponse)
5. Server-Sent Events (SSE) Token Stream for Real-Time AI Generation
6. Automated Integration Test Suite using httpx.AsyncClient

Run via:
    python example_01.py
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fastapi_production")

# ==============================================================================
# Context Variables for Distributed Tracing & Request Scoping
# ==============================================================================

correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="untracked"
)
client_ip_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_ip", default="127.0.0.1"
)

# ==============================================================================
# Pure ASGI Middleware 1: Correlation ID & Process Time Injector
# ==============================================================================

class RequestCorrelationMiddleware:
    """
    Pure ASGI Middleware that injects/propagates an RFC-compliant correlation ID
    and calculates high-precision wall-clock latency without buffering response bodies.
    """

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

        t_start_ns = time.perf_counter_ns()

        # 1. Extract or generate Correlation ID
        headers_dict = dict(scope.get("headers", []))
        raw_corr_id = headers_dict.get(b"x-request-id")
        corr_id = raw_corr_id.decode("latin1") if raw_corr_id else str(uuid.uuid4())

        # Extract client IP
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        # 2. Set ContextVars
        token_corr = correlation_id_ctx.set(corr_id)
        token_ip = client_ip_ctx.set(client_ip)

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                t_elapsed_ms = (time.perf_counter_ns() - t_start_ns) / 1_000_000.0
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", corr_id.encode("latin1")))
                headers.append((b"x-process-time-ms", f"{t_elapsed_ms:.3f}".encode("latin1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            correlation_id_ctx.reset(token_corr)
            client_ip_ctx.reset(token_ip)


# ==============================================================================
# Pure ASGI Middleware 2: Token Bucket Rate Limiter
# ==============================================================================

class TokenBucketRateLimiterMiddleware:
    """
    Pure ASGI Token-Bucket Rate Limiter.
    Short-circuits abusive requests before route matching and dependency resolution.
    """

    def __init__(self, app: Any, capacity: int = 10, refill_rate_per_sec: float = 5.0) -> None:
        self.app = app
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        # Map: client_ip -> (tokens, last_refill_timestamp)
        self._buckets: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()

    async def _is_allowed(self, client_ip: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            if client_ip not in self._buckets:
                self._buckets[client_ip] = [float(self.capacity) - 1.0, now]
                return True

            tokens, last_refill = self._buckets[client_ip]
            elapsed = now - last_refill
            tokens = min(float(self.capacity), tokens + elapsed * self.refill_rate)

            if tokens >= 1.0:
                self._buckets[client_ip] = [tokens - 1.0, now]
                return True
            else:
                self._buckets[client_ip] = [tokens, now]
                return False

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Dict[str, Any]], Any],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "127.0.0.1"

        if not await self._is_allowed(client_ip):
            # Send RFC 7807 429 Too Many Requests response directly via ASGI
            error_body = json.dumps({
                "type": "https://errors.enterprise.internal/rate-limited",
                "title": "Too Many Requests",
                "status": 429,
                "detail": f"Rate limit exceeded for IP '{client_ip}'. Refill rate is {self.refill_rate} req/s.",
            }).encode("utf-8")

            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(error_body)).encode("latin1")),
                    (b"retry-after", b"1"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": error_body,
                "more_body": False,
            })
            return

        await self.app(scope, receive, send)


# ==============================================================================
# Decoupled Background Task Service
# ==============================================================================

class AuditLogRepository:
    """Simulates an external audit database with independent session lifecycle."""

    def __init__(self) -> None:
        self.logs: List[Dict[str, Any]] = []

    async def record_audit_event(self, action: str, entity_id: str, corr_id: str) -> None:
        # Simulate async I/O writing to an isolated audit store
        await asyncio.sleep(0.02)
        event = {
            "action": action,
            "entity_id": entity_id,
            "correlation_id": corr_id,
            "timestamp": time.time(),
        }
        self.logs.append(event)
        logger.info(f"[AUDIT-TASK] Completed async audit for {entity_id} [corr_id: {corr_id}]")


audit_repo = AuditLogRepository()


def sync_metrics_flusher(metric_name: str, value: float) -> None:
    """Simulates a synchronous metric export running in AnyIO worker thread."""
    # Synchronous blocking call (e.g., StatsD / Prometheus pushgateway)
    time.sleep(0.01)
    logger.info(f"[METRIC-TASK] Flushed metric {metric_name}={value}")


# ==============================================================================
# FastAPI Application Construction
# ==============================================================================

app = FastAPI(
    title="High-Scale Production FastAPI Services",
    version="1.0.0",
    docs_url="/docs",
)

# Register pure ASGI middleware (Outermost to innermost execution)
app.add_middleware(RequestCorrelationMiddleware)
app.add_middleware(TokenBucketRateLimiterMiddleware, capacity=10, refill_rate_per_sec=5.0)


# ==============================================================================
# Pydantic Schemas & Route Handlers
# ==============================================================================

class RegistrationRequest(BaseModel):
    username: str = Field(..., min_length=3)
    email: str


@app.post("/api/v1/users", status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: RegistrationRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Registers a user and dispatches non-blocking async and sync background tasks.
    Crucial: Background tasks receive primitive values, NOT request-scoped database sessions.
    """
    corr_id = correlation_id_ctx.get()
    user_id = f"usr_{uuid.uuid4().hex[:8]}"

    # Enqueue Async Background Task (runs directly on event loop after HTTP response)
    background_tasks.add_task(
        audit_repo.record_audit_event,
        action="USER_REGISTERED",
        entity_id=user_id,
        corr_id=corr_id,
    )

    # Enqueue Sync Background Task (runs in AnyIO threadpool worker)
    background_tasks.add_task(sync_metrics_flusher, "user_registration_count", 1.0)

    return {
        "status": "success",
        "user_id": user_id,
        "username": payload.username,
        "correlation_id": corr_id,
    }


# ==============================================================================
# Chunked Streaming Endpoint
# ==============================================================================

async def generate_large_csv_stream(rows: int = 100) -> AsyncGenerator[bytes, None]:
    """
    Yields CSV chunks on-the-fly using HTTP Chunked Transfer Encoding.
    RAM usage remains constant (O(1)) regardless of total dataset size.
    """
    yield b"id,transaction_code,amount,currency,status\n"
    for i in range(1, rows + 1):
        row = f"{i},TXN-{uuid.uuid4().hex[:6].upper()},{100.50 + i:.2f},USD,SETTLED\n"
        yield row.encode("utf-8")
        if i % 25 == 0:
            await asyncio.sleep(0.005)  # Yield control to event loop


@app.get("/api/v1/export/transactions.csv")
async def export_transactions_csv() -> StreamingResponse:
    """Streams financial transactions in real-time without buffering in memory."""
    return StreamingResponse(
        generate_large_csv_stream(rows=50),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


# ==============================================================================
# Server-Sent Events (SSE) AI Streaming Endpoint
# ==============================================================================

async def simulate_llm_token_stream(
    prompt: str,
    request: Request,
) -> AsyncGenerator[str, None]:
    """
    Simulates token-by-token LLM output over Server-Sent Events (SSE).
    Uses compliant SSE Framing: event, id, and data terminated with double newline.
    """
    tokens = [
        "In", " modern", " enterprise", " architectures,", " streaming",
        " responses", " eliminate", " memory", " bottlenecks", " and",
        " deliver", " instant", " time-to-first-token", " (TTFT)", " latency."
    ]

    for index, token in enumerate(tokens):
        payload = json.dumps({"index": index, "token": token, "prompt": prompt})
        # Compliant SSE Framing: "event: ...\nid: ...\ndata: ...\n\n"
        yield f"event: token\nid: {index}\ndata: {payload}\n\n"
        await asyncio.sleep(0.005)

    # Termination Event
    yield "event: done\nid: 999\ndata: [DONE]\n\n"


@app.get("/api/v1/ai/completions/stream")
async def stream_ai_completions(prompt: str, request: Request) -> StreamingResponse:
    """Streams generative AI completions using Server-Sent Events (SSE)."""
    return StreamingResponse(
        simulate_llm_token_stream(prompt=prompt, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disables proxy buffering in Nginx
        },
    )


# ==============================================================================
# Automated Integration Test Suite
# ==============================================================================

def run_integration_tests() -> None:
    print("=" * 80)
    print(" FASTAPI BACKGROUND TASKS, MIDDLEWARE & STREAMING INTEGRATION TESTS")
    print("=" * 80)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:

        # ----------------------------------------------------------------------
        # Test 1: Ingress Correlation ID & Timing Header Injection
        # ----------------------------------------------------------------------
        print("\n[TEST 1] Testing Correlation ID & Process Timing Middleware...")
        resp = client.post(
            "/api/v1/users",
            json={"username": "alice_architect", "email": "alice@enterprise.internal"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}"
        assert "x-request-id" in resp.headers, "Missing X-Request-ID header"
        assert "x-process-time-ms" in resp.headers, "Missing X-Process-Time-Ms header"
        corr_id = resp.headers["x-request-id"]
        process_time = float(resp.headers["x-process-time-ms"])
        assert len(corr_id) > 10, "Invalid correlation ID format"
        assert process_time >= 0.0, "Invalid process time"
        print(f"  -> Correlation ID   : {corr_id}")
        print(f"  -> Process Time (ms): {process_time:.3f} ms")
        print("  -> PASSED: Correlation ID and high-precision timing injected correctly.")

        # ----------------------------------------------------------------------
        # Test 2: Custom Inbound Correlation ID Propagation
        # ----------------------------------------------------------------------
        print("\n[TEST 2] Testing Inbound Client Correlation ID Preservation...")
        custom_id = "trace-client-custom-987654321"
        resp2 = client.post(
            "/api/v1/users",
            json={"username": "bob_operator", "email": "bob@enterprise.internal"},
            headers={"X-Request-ID": custom_id},
        )
        assert resp2.status_code == 201
        assert resp2.headers["x-request-id"] == custom_id, "Inbound correlation ID not preserved"
        body2 = resp2.json()
        assert body2["correlation_id"] == custom_id, "ContextVar did not propagate inbound ID"
        print(f"  -> Client sent     : {custom_id}")
        print(f"  -> Ingress response: {resp2.headers['x-request-id']}")
        print("  -> PASSED: Client correlation ID preserved across async execution context.")

        # ----------------------------------------------------------------------
        # Test 3: BackgroundTasks Execution Verification
        # ----------------------------------------------------------------------
        print("\n[TEST 3] Testing BackgroundTasks Execution...")
        matched_logs = [log for log in audit_repo.logs if log["correlation_id"] == custom_id]
        assert len(matched_logs) == 1, "Background audit task was not executed"
        assert matched_logs[0]["action"] == "USER_REGISTERED"
        print(f"  -> Audit event verified: {matched_logs[0]}")
        print("  -> PASSED: BackgroundTasks executed independently after HTTP response.")

        # ----------------------------------------------------------------------
        # Test 4: Chunked CSV Streaming Response
        # ----------------------------------------------------------------------
        print("\n[TEST 4] Testing Chunked CSV Streaming Response...")
        resp_csv = client.get("/api/v1/export/transactions.csv")
        assert resp_csv.status_code == 200
        assert "text/csv" in resp_csv.headers["content-type"]
        assert "transactions.csv" in resp_csv.headers["content-disposition"]
        lines = resp_csv.text.strip().split("\n")
        assert len(lines) == 51, f"Expected 51 lines (header + 50 rows), got {len(lines)}"
        assert lines[0] == "id,transaction_code,amount,currency,status"
        print(f"  -> Streamed Rows    : {len(lines) - 1} data records + 1 header")
        print(f"  -> First Record     : {lines[1]}")
        print(f"  -> Last Record      : {lines[-1]}")
        print("  -> PASSED: Chunked streaming response completed without buffering.")

        # ----------------------------------------------------------------------
        # Test 5: Server-Sent Events (SSE) AI Completion Stream
        # ----------------------------------------------------------------------
        print("\n[TEST 5] Testing Server-Sent Events (SSE) Token Streaming...")
        resp_sse = client.get(
            "/api/v1/ai/completions/stream",
            params={"prompt": "Explain streaming"},
        )
        assert resp_sse.status_code == 200
        assert "text/event-stream" in resp_sse.headers["content-type"]

        sse_raw = resp_sse.text
        frames = [f for f in sse_raw.split("\n\n") if f.strip()]
        assert len(frames) > 10, f"Expected >10 SSE frames, received {len(frames)}"

        first_frame = frames[0]
        assert "event: token" in first_frame
        assert "id: 0" in first_frame
        assert "data: " in first_frame

        last_frame = frames[-1]
        assert "event: done" in last_frame
        assert "[DONE]" in last_frame
        print(f"  -> Total SSE Frames : {len(frames)}")
        print(f"  -> Sample Frame 0   :\n     {first_frame.replace(chr(10), chr(10) + '     ')}")
        print(f"  -> Final Frame      :\n     {last_frame.replace(chr(10), chr(10) + '     ')}")
        print("  -> PASSED: Server-Sent Events framed according to W3C specification.")

        # ----------------------------------------------------------------------
        # Test 6: Rate Limiter Pure ASGI Middleware Enforcement
        # ----------------------------------------------------------------------
        print("\n[TEST 6] Testing Token Bucket Rate Limiter Short-Circuiting...")
        rate_limited_occurred = False
        for _ in range(15):
            rl_resp = client.get("/api/v1/export/transactions.csv")
            if rl_resp.status_code == 429:
                rate_limited_occurred = True
                assert "retry-after" in rl_resp.headers
                assert "application/problem+json" in rl_resp.headers["content-type"]
                break

        assert rate_limited_occurred, "Rate limiter did not trigger HTTP 429"
        print("  -> Caught HTTP 429 Too Many Requests response with Retry-After header.")
        print("  -> PASSED: Pure ASGI middleware short-circuited abusive traffic.")

    print("\n" + "=" * 80)
    print(" ALL 6 PRODUCTION INTEGRATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_integration_tests()
