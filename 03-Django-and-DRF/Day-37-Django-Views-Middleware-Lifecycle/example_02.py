"""
Day 37: Views, Request-Response Lifecycle & Custom Middleware Pipelines
Example 02: Production Enterprise Middleware Pipeline

Architecture Overview:
----------------------
This module implements a production-grade, multi-layered middleware suite designed for
enterprise backend reliability, observability, and security in Django:

  1. `CorrelationIdMiddleware`:
     - Extracts or generates a UUID4 correlation ID (`X-Request-ID`).
     - Propagates ID via Python's `contextvars` for thread-safe asynchronous logging.
     - Injects `X-Request-ID` into outgoing HTTP response headers.

  2. `TokenBucketRateLimitMiddleware`:
     - Implements an IP-based Token Bucket rate limiting algorithm.
     - Calculates token replenishment lazily based on elapsed wall-clock time.
     - Short-circuits with RFC 6585 `HTTP 429 Too Many Requests` when bucket is empty.
     - Emits `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `Retry-After` headers.

  3. `PerformanceTelemetryMiddleware`:
     - Measures end-to-end execution latency using high-precision `time.perf_counter()`.
     - Injects W3C-compliant `Server-Timing` headers for distributed tracing and APM.
     - Flags slow requests exceeding configured SLA thresholds.

  4. `GlobalExceptionMiddleware`:
     - Emulates Django's `process_exception` hook.
     - Intercepts uncaught exceptions and serializes them into standardized JSON error envelopes.
     - Maps domain exception types to canonical HTTP status codes (404, 403, 422, 500).
     - Strips internal tracebacks to prevent information disclosure in production environments.

  5. High-Throughput Pipeline Benchmark:
     - Runs 5,000 synthetic requests across the entire middleware onion.
     - Evaluates throughput (requests/sec) and latency percentiles (p50, p95, p99).
"""

from __future__ import annotations

import contextvars
import json
import math
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Type


# ============================================================================
# 0. Context Variables for Distributed Observability
# ============================================================================

# WHY contextvars?
# Standard threading.local fails in asynchronous or coroutine-based contexts (ASGI).
# Python's `contextvars` module provides context-local storage that works seamlessly
# across both synchronous thread pools and asyncio event loops.
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_request_id", default="system-init"
)


# ============================================================================
# 1. Core HTTP Data Models (Clean Minimal Abstractions)
# ============================================================================

class HttpRequest:
    """Enterprise HTTP request object wrapping client connection metadata."""

    def __init__(
        self,
        method: str,
        path: str,
        client_ip: str = "127.0.0.1",
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b"",
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.client_ip = client_ip
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body
        self.request_id: Optional[str] = None
        self.user: Optional[Dict[str, Any]] = None


class HttpResponse:
    """Standard HTTP response container."""

    def __init__(
        self,
        content: bytes = b"",
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
    ) -> None:
        self.status_code = status
        self.headers: Dict[str, str] = {"Content-Type": content_type}
        self.content = content

    def set_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    @classmethod
    def json(cls, data: Any, status: int = 200) -> "HttpResponse":
        serialized = json.dumps(data, indent=2).encode("utf-8")
        return cls(content=serialized, status=status, content_type="application/json; charset=utf-8")

    def __repr__(self) -> str:
        return f"<HttpResponse status={self.status_code} bytes={len(self.content)}>"


# ============================================================================
# 2. Domain Exceptions
# ============================================================================

class DomainException(Exception):
    """Base class for all application-level domain errors."""
    status_code: int = 500
    error_code: str = "INTERNAL_SERVER_ERROR"


class ResourceNotFoundException(DomainException):
    status_code = 404
    error_code = "RESOURCE_NOT_FOUND"


class PermissionDeniedException(DomainException):
    status_code = 403
    error_code = "PERMISSION_DENIED"


class BusinessValidationException(DomainException):
    status_code = 422
    error_code = "UNPROCESSABLE_ENTITY"


# ============================================================================
# 3. Production Middleware Implementations
# ============================================================================

class CorrelationIdMiddleware:
    """
    Enterprise Correlation ID Middleware.

    WHY CORRELATION IDS?
    In distributed microservice and serverless topologies, a single user transaction
    triggers requests across API gateways, backend monoliths, workers, and databases.
    Attaching a globally unique `X-Request-ID` enables unified log aggregation
    (Datadog, ELK, CloudWatch) and end-to-end trace reconstruction.
    """

    HEADER_NAME = "x-request-id"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Step 1: Reuse existing client/proxy request ID or generate a new UUID4
        incoming_id = request.headers.get(self.HEADER_NAME)
        request_id = incoming_id if incoming_id else str(uuid.uuid4())

        # Step 2: Store on request object and bind to contextvar
        request.request_id = request_id
        token = current_request_id.set(request_id)

        try:
            # Step 3: Forward request downstream to next middleware / view
            response = self.get_response(request)
        finally:
            # Step 4: Reset context variable to prevent pollution across pooled threads
            current_request_id.reset(token)

        # Step 5: Mirror correlation ID in response headers for client tracking
        response.set_header("X-Request-ID", request_id)
        return response


class TokenBucketRateLimiter:
    """
    Token Bucket rate limiting state container.
    Replenishes tokens dynamically on access without background worker threads.
    """

    def __init__(self, capacity: int, refill_rate_per_sec: float) -> None:
        self.capacity = float(capacity)
        self.refill_rate = refill_rate_per_sec
        self.tokens = float(capacity)
        self.last_refill_timestamp = time.monotonic()

    def allow_request(self, tokens_needed: float = 1.0) -> Tuple[bool, int, float]:
        """
        Calculates token replenishment and deducts requested tokens if available.
        Returns: (is_allowed, remaining_tokens, retry_after_seconds)
        """
        now = time.monotonic()
        elapsed = now - self.last_refill_timestamp
        self.last_refill_timestamp = now

        # Replenish bucket up to maximum capacity
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))

        if self.tokens >= tokens_needed:
            self.tokens -= tokens_needed
            remaining = int(math.floor(self.tokens))
            return True, remaining, 0.0

        # Bucket empty: compute seconds required to accumulate enough tokens
        deficit = tokens_needed - self.tokens
        retry_after = deficit / self.refill_rate
        return False, 0, retry_after


class TokenBucketRateLimitMiddleware:
    """
    IP-Based Token Bucket Rate Limiting Middleware.

    WHY AT THE MIDDLEWARE LAYER?
    Protecting API endpoints before executing database queries or template rendering
    prevents resource exhaustion attacks (DoS) and noisy neighbor starvation.
    Short-circuiting in middleware consumes < 0.02ms vs > 50ms for a view invocation.
    """

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
        capacity: int = 5,
        refill_rate_per_sec: float = 2.0,
    ) -> None:
        self.get_response = get_response
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        # Maps IP -> TokenBucketRateLimiter
        self._buckets: Dict[str, TokenBucketRateLimiter] = {}

    def _get_limiter(self, ip: str) -> TokenBucketRateLimiter:
        limiter = self._buckets.get(ip)
        if limiter is None:
            limiter = TokenBucketRateLimiter(self.capacity, self.refill_rate)
            self._buckets[ip] = limiter
        return limiter

    def __call__(self, request: HttpRequest) -> HttpResponse:
        ip = request.client_ip
        limiter = self._get_limiter(ip)
        allowed, remaining, retry_after = limiter.allow_request(1.0)

        if not allowed:
            # SHORT-CIRCUIT: Return HTTP 429 Too Many Requests immediately
            error_payload = {
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Too many requests. Please throttle your traffic.",
                    "retry_after_seconds": round(retry_after, 2),
                    "request_id": request.request_id,
                }
            }
            response = HttpResponse.json(error_payload, status=429)
            response.set_header("Retry-After", str(int(math.ceil(retry_after))))
            response.set_header("X-RateLimit-Limit", str(self.capacity))
            response.set_header("X-RateLimit-Remaining", "0")
            return response

        # Allowed: Proceed downstream
        response = self.get_response(request)
        response.set_header("X-RateLimit-Limit", str(self.capacity))
        response.set_header("X-RateLimit-Remaining", str(remaining))
        return response


class PerformanceTelemetryMiddleware:
    """
    High-Precision Request Timing & Telemetry Middleware.

    WHY W3C SERVER-TIMING?
    The `Server-Timing` header communicates backend operational metrics to client browsers,
    CDNs, and synthetic monitoring tools without requiring custom response payloads.
    """

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
        slow_threshold_ms: float = 50.0,
    ) -> None:
        self.get_response = get_response
        self.slow_threshold_ms = slow_threshold_ms
        self.slow_requests_count = 0

    def __call__(self, request: HttpRequest) -> HttpResponse:
        start_perf = time.perf_counter()

        response = self.get_response(request)

        duration_ms = (time.perf_counter() - start_perf) * 1000.0

        if duration_ms > self.slow_threshold_ms:
            self.slow_requests_count += 1

        # Format: Server-Timing: total;dur=12.34
        response.set_header("Server-Timing", f"total;dur={duration_ms:.3f}")
        return response


class GlobalExceptionMiddleware:
    """
    Production Global Exception Interceptor (Simulates Django process_exception).

    WHY UNIFIED ERROR HANDLING?
    1. Prevents internal Python tracebacks from leaking database schema, credentials, or file paths.
    2. Guarantees every API consumer receives a consistent error structure regardless of failure point.
    3. Translates typed business logic exceptions into proper RFC 7807 status codes.
    """

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
        is_debug: bool = False,
    ) -> None:
        self.get_response = get_response
        self.is_debug = is_debug

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            return self.get_response(request)
        except Exception as exc:
            return self.process_exception(request, exc)

    def process_exception(self, request: HttpRequest, exc: Exception) -> HttpResponse:
        """
        Hook called when an unhandled exception bubbles out of downstream middleware or views.
        """
        request_id = request.request_id or current_request_id.get()

        # Handle known domain exceptions
        if isinstance(exc, DomainException):
            status_code = exc.status_code
            error_code = exc.error_code
            message = str(exc)
        else:
            # Unhandled / unexpected system failure
            status_code = 500
            error_code = "INTERNAL_SERVER_ERROR"
            message = (
                str(exc) if self.is_debug
                else "An unexpected server error occurred. Please contact support with the request ID."
            )

        envelope = {
            "error": {
                "code": error_code,
                "message": message,
                "status": status_code,
                "request_id": request_id,
                "timestamp": time.time(),
            }
        }

        response = HttpResponse.json(envelope, status=status_code)
        if request_id:
            response.set_header("X-Request-ID", request_id)
        return response


# ============================================================================
# 4. Composable Middleware Pipeline Orchestrator
# ============================================================================

class EnterpriseMiddlewarePipeline:
    """Orchestrates onion middleware wrapping around a view handler."""

    def __init__(self, view_callable: Callable[[HttpRequest], HttpResponse]) -> None:
        self.view_callable = view_callable
        self.middlewares: List[Tuple[Type[Any], Dict[str, Any]]] = []
        self._handler: Optional[Callable[[HttpRequest], HttpResponse]] = None

    def add(self, middleware_cls: Type[Any], **kwargs: Any) -> "EnterpriseMiddlewarePipeline":
        self.middlewares.append((middleware_cls, kwargs))
        self._handler = None  # invalidate cache
        return self

    def compile(self) -> Callable[[HttpRequest], HttpResponse]:
        """Wraps middlewares from innermost to outermost."""
        handler = self.view_callable
        for middleware_cls, kwargs in reversed(self.middlewares):
            handler = middleware_cls(handler, **kwargs)
        self._handler = handler
        return handler

    def dispatch(self, request: HttpRequest) -> HttpResponse:
        if self._handler is None:
            self.compile()
        assert self._handler is not None
        return self._handler(request)


# ============================================================================
# 5. Business Views for Testing
# ============================================================================

def simulated_account_view(request: HttpRequest) -> HttpResponse:
    """Handles normal business requests and raises domain exceptions on test conditions."""
    path = request.path

    if path == "/api/account/balance/":
        return HttpResponse.json({"account_id": "ACC-8910", "balance_usd": 45000.75})

    elif path == "/api/account/missing/":
        raise ResourceNotFoundException("Account record 'ACC-9999' does not exist.")

    elif path == "/api/account/transfer/":
        # Simulate validation error
        raise BusinessValidationException("Insufficient funds: required $10,000, available $4,500.")

    elif path == "/api/account/crash/":
        # Simulate unexpected zero division or system bug
        raise ZeroDivisionError("division by zero in legacy accounting ledger")

    return HttpResponse.json({"message": "Not Found"}, status=404)


# ============================================================================
# 6. Verification & Performance Benchmark
# ============================================================================

def run_middleware_verification_suite() -> None:
    print("[STAGE 1] Enterprise Middleware Suite Verification")
    print("-" * 65)

    # Build Pipeline:
    # Outer -> Inner:
    # 1. PerformanceTelemetryMiddleware (tracks total wall time)
    # 2. CorrelationIdMiddleware (attaches X-Request-ID)
    # 3. GlobalExceptionMiddleware (intercepts uncaught errors)
    # 4. TokenBucketRateLimitMiddleware (capacity=3, refill=1 token/sec)
    # 5. View (simulated_account_view)
    pipeline = EnterpriseMiddlewarePipeline(simulated_account_view)
    pipeline.add(PerformanceTelemetryMiddleware, slow_threshold_ms=10.0)
    pipeline.add(CorrelationIdMiddleware)
    pipeline.add(GlobalExceptionMiddleware, is_debug=False)
    pipeline.add(TokenBucketRateLimitMiddleware, capacity=3, refill_rate_per_sec=1.0)
    pipeline.compile()

    # 1. Test Normal Request
    req1 = HttpRequest("GET", "/api/account/balance/", client_ip="192.168.1.10")
    res1 = pipeline.dispatch(req1)
    assert res1.status_code == 200
    assert "X-Request-ID" in res1.headers
    assert "Server-Timing" in res1.headers
    assert res1.headers["X-RateLimit-Remaining"] == "2"
    print("  [PASS] 200 OK: Normal balance check with X-Request-ID and Server-Timing.")

    # 2. Test Domain Exception (404 ResourceNotFound)
    req2 = HttpRequest("GET", "/api/account/missing/", client_ip="192.168.1.10")
    res2 = pipeline.dispatch(req2)
    assert res2.status_code == 404
    data2 = json.loads(res2.content.decode("utf-8"))
    assert data2["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert data2["error"]["request_id"] == res2.headers["X-Request-ID"]
    print("  [PASS] 404 Not Found: Domain exception translated to RFC error JSON.")

    # 3. Test Domain Exception (422 Unprocessable Entity)
    req3 = HttpRequest("POST", "/api/account/transfer/", client_ip="192.168.1.10")
    res3 = pipeline.dispatch(req3)
    assert res3.status_code == 422
    data3 = json.loads(res3.content.decode("utf-8"))
    assert data3["error"]["code"] == "UNPROCESSABLE_ENTITY"
    print("  [PASS] 422 Unprocessable: Validation error properly normalized.")

    # 4. Test Rate Limit Short-Circuit (4th request from same IP should exhaust capacity=3)
    req4 = HttpRequest("GET", "/api/account/balance/", client_ip="192.168.1.10")
    res4 = pipeline.dispatch(req4)
    assert res4.status_code == 429
    assert res4.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in res4.headers
    data4 = json.loads(res4.content.decode("utf-8"))
    assert data4["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    print("  [PASS] 429 Too Many Requests: Token bucket exhausted and short-circuited.")

    # 5. Test Unexpected System Crash (Obfuscated 500 error in production mode)
    # Using a different IP so it's not rate-limited
    req5 = HttpRequest("GET", "/api/account/crash/", client_ip="10.0.0.5")
    res5 = pipeline.dispatch(req5)
    assert res5.status_code == 500
    data5 = json.loads(res5.content.decode("utf-8"))
    assert data5["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "division by zero" not in data5["error"]["message"]  # Obfuscated!
    print("  [PASS] 500 Internal Error: Unhandled crash safely obfuscated in JSON.\n")


def run_pipeline_benchmark(iterations: int = 5000) -> None:
    print(f"[STAGE 2] Performance & Throughput Benchmark ({iterations:,} iterations)")
    print("-" * 65)

    # Setup high-capacity pipeline for benchmarking
    pipeline = EnterpriseMiddlewarePipeline(simulated_account_view)
    pipeline.add(PerformanceTelemetryMiddleware, slow_threshold_ms=10.0)
    pipeline.add(CorrelationIdMiddleware)
    pipeline.add(GlobalExceptionMiddleware, is_debug=False)
    pipeline.add(TokenBucketRateLimitMiddleware, capacity=100000, refill_rate_per_sec=100000.0)
    pipeline.compile()

    latencies_us: List[float] = []
    start_total = time.perf_counter()

    for i in range(iterations):
        ip = f"10.0.{i % 50}.{i % 250}"
        req = HttpRequest("GET", "/api/account/balance/", client_ip=ip)
        
        t0 = time.perf_counter()
        resp = pipeline.dispatch(req)
        t1 = time.perf_counter()

        latencies_us.append((t1 - t0) * 1_000_000.0)
        assert resp.status_code == 200

    total_time_sec = time.perf_counter() - start_total
    throughput = iterations / total_time_sec

    latencies_us.sort()
    p50 = latencies_us[int(iterations * 0.50)]
    p95 = latencies_us[int(iterations * 0.95)]
    p99 = latencies_us[int(iterations * 0.99)]

    print(f"Total Processed     : {iterations:,} requests")
    print(f"Wall Clock Time     : {total_time_sec:.4f} seconds")
    print(f"Throughput          : {throughput:,.1f} requests/sec")
    print(f"Latency p50 (median): {p50:.2f} microseconds (us)")
    print(f"Latency p95         : {p95:.2f} microseconds (us)")
    print(f"Latency p99         : {p99:.2f} microseconds (us)")
    print("=" * 65)


if __name__ == "__main__":
    print("======================================================================")
    print(" Day 37: Production Enterprise Middleware Pipeline Benchmark")
    print("======================================================================\n")
    run_middleware_verification_suite()
    run_pipeline_benchmark(5000)
