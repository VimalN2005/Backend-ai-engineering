# Practical View & Middleware Pipeline Challenges

This document contains 3 enterprise-grade engineering challenges covering Django's internal request-response engine, middleware execution pipeline, and distributed observability patterns. Each challenge includes a real-world scenario, architectural constraints, and an executable, self-contained reference solution.

---

## Challenge 1: Immutable Multi-Value QueryDict Engine

### Scenario
In a microservices API gateway built with Django, URL query strings often contain repeated keys for filtering and faceted navigation (e.g., `?category=hardware&category=tools&sort=price_asc`). You must implement a thread-safe, immutable `QueryDict` parser from first principles that honors HTTP multi-value standards, protects against parameter tampering via immutability, and provides safe defensive copying semantics.

### Requirements & Constraints
1. **Multi-Value Storage**: Store multiple values per key in submission order.
2. **`get(key, default)` Semantics**: Must return the *last* submitted value (standard Django convention), or `default` if the key does not exist.
3. **`getlist(key, default)` Semantics**: Must return the complete list of submitted values.
4. **Immutability Contract**: By default, any attempt to mutate the instance via `__setitem__`, `__delitem__`, or `setlist()` must raise an `AttributeError`.
5. **Defensive `.copy()`**: Produces a decoupled, mutable instance where values can be safely appended, updated, or deleted without altering the parent.
6. **Round-Trip `urlencode()`**: Serializes the dictionary back into an RFC 3986 compliant query string.
7. **Zero External Dependencies**: Pure Python standard library only (`urllib.parse`, `copy`).

### Executable Solution & Verification

```python
import copy
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple


class ImmutableQueryDict:
    """
    Production-grade simulation of django.http.QueryDict.
    Preserves multi-value query parameters while enforcing immutability.
    """

    def __init__(self, query_string: str = "", mutable: bool = False) -> None:
        self._data: Dict[str, List[str]] = {}
        self._mutable: bool = mutable

        if query_string:
            # parse_qs parses query string into Dict[str, List[str]]
            parsed = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            for k, v in parsed.items():
                self._data[k] = list(v)

    def _ensure_mutable(self) -> None:
        if not self._mutable:
            raise AttributeError("This QueryDict instance is immutable. Use .copy() to obtain a mutable clone.")

    def __getitem__(self, key: str) -> str:
        values = self._data[key]
        if not values:
            raise KeyError(key)
        return values[-1]  # Return last submitted value

    def __setitem__(self, key: str, value: str) -> None:
        self._ensure_mutable()
        self._data[key] = [str(value)]

    def __delitem__(self, key: str) -> None:
        self._ensure_mutable()
        del self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def getlist(self, key: str, default: Optional[List[str]] = None) -> List[str]:
        try:
            return list(self._data[key])
        except KeyError:
            return default if default is not None else []

    def setlist(self, key: str, values: List[str]) -> None:
        self._ensure_mutable()
        self._data[key] = [str(v) for v in values]

    def appendlist(self, key: str, value: str) -> None:
        self._ensure_mutable()
        self._data.setdefault(key, []).append(str(value))

    def copy(self) -> "ImmutableQueryDict":
        clone = ImmutableQueryDict(mutable=True)
        clone._data = copy.deepcopy(self._data)
        return clone

    def urlencode(self) -> str:
        pairs: List[Tuple[str, str]] = []
        for key, values in self._data.items():
            for v in values:
                pairs.append((key, v))
        return urllib.parse.urlencode(pairs)

    def __repr__(self) -> str:
        return f"<ImmutableQueryDict: {self._data}>"


# --- Verification Harness ---
if __name__ == "__main__":
    qs = "category=electronics&category=audio&sort=rating&page=1"
    qd = ImmutableQueryDict(qs)

    # 1. Test get() returns last submitted value
    assert qd.get("category") == "audio"
    assert qd["category"] == "audio"

    # 2. Test getlist() returns all values
    assert qd.getlist("category") == ["electronics", "audio"]
    assert qd.get("page") == "1"

    # 3. Test immutability enforcement
    try:
        qd["page"] = "2"
        assert False, "Mutation should have raised AttributeError"
    except AttributeError as err:
        assert "immutable" in str(err)

    # 4. Test mutable copy
    clone = qd.copy()
    clone["page"] = "2"
    clone.appendlist("category", "gaming")
    assert clone.get("page") == "2"
    assert clone.getlist("category") == ["electronics", "audio", "gaming"]

    # Verify original unchanged
    assert qd.get("page") == "1"
    assert len(qd.getlist("category")) == 2

    # 5. Round-trip urlencode
    re_encoded = clone.urlencode()
    assert "page=2" in re_encoded
    assert "category=gaming" in re_encoded

    print("[SUCCESS] Challenge 1: Immutable QueryDict verified successfully.")
```

---

## Challenge 2: Onion Pipeline with `process_view` Short-Circuiting

### Scenario
In Django's request lifecycle, middleware classes can implement optional lifecycle hooks beyond `__call__`, most notably **`process_view(request, view_func, view_args, view_kwargs)`**. This hook executes *after* URL routing resolves the target view, but *before* the view function is invoked. If any `process_view` hook returns an `HttpResponse`, Django short-circuits: no further `process_view` hooks or view handlers are called, and execution immediately reverses into the response pipeline.

You must build an onion-model pipeline orchestrator supporting both `__call__` wrapping and `process_view` hook dispatching.

### Requirements & Constraints
1. **Pipeline Composition**: Support registering middleware classes implementing `__call__` and optional `process_view`.
2. **Execution Sequence**:
   - Outermost `__call__` request phase -> Innermost `__call__` request phase.
   - Outermost `process_view` -> Innermost `process_view`.
   - View execution (if no short-circuit occurs).
   - Innermost `__call__` response phase -> Outermost `__call__` response phase.
3. **Short-Circuiting Guarantee**: If any `process_view` returns an `HttpResponse`, downstream `process_view` hooks and the view MUST NOT execute. Response processing MUST still run for all outer middlewares.
4. **Execution Audit Log**: Maintain an execution trail verifying exact forward and reverse traversal orders.

### Executable Solution & Verification

```python
from typing import Any, Callable, Dict, List, Optional, Tuple


class Request:
    def __init__(self, path: str, user: Optional[str] = None) -> None:
        self.path = path
        self.user = user


class Response:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.headers: Dict[str, str] = {}


class LifecyclePipeline:
    def __init__(self, view_func: Callable[[Request], Response]) -> None:
        self.view_func = view_func
        self.middleware_classes: List[type] = []
        self.instances: List[Any] = []
        self.execution_log: List[str] = []

    def add_middleware(self, cls: type) -> "LifecyclePipeline":
        self.middleware_classes.append(cls)
        return self

    def execute(self, request: Request) -> Response:
        self.execution_log.clear()
        self.instances.clear()

        # Step 1: Instantiate middleware instances and build standard callable chain
        # Core view invocation wrapper:
        def core_view_handler(req: Request) -> Response:
            # Step 2: Execute process_view hooks forward (Outer -> Inner)
            for mw in self.instances:
                if hasattr(mw, "process_view"):
                    self.execution_log.append(f"{mw.__class__.__name__}.process_view")
                    result = mw.process_view(req, self.view_func, (), {})
                    if result is not None and isinstance(result, Response):
                        self.execution_log.append(f"{mw.__class__.__name__}.process_view_SHORT_CIRCUIT")
                        return result

            # Step 3: Invoke View Function
            self.execution_log.append("view.execute")
            return self.view_func(req)

        handler = core_view_handler

        # Build onion from innermost to outermost
        for mw_cls in reversed(self.middleware_classes):
            instance = mw_cls(handler, self.execution_log)
            self.instances.insert(0, instance)  # Maintain outer -> inner order in self.instances
            handler = instance

        # Step 4: Execute full pipeline
        return handler(request)


# --- Middleware Definitions ---

class AuditLogMiddleware:
    def __init__(self, get_response: Callable[[Request], Response], log: List[str]) -> None:
        self.get_response = get_response
        self.log = log

    def __call__(self, request: Request) -> Response:
        self.log.append("AuditLogMiddleware.request_in")
        response = self.get_response(request)
        self.log.append("AuditLogMiddleware.response_out")
        return response


class RolePermissionMiddleware:
    def __init__(self, get_response: Callable[[Request], Response], log: List[str]) -> None:
        self.get_response = get_response
        self.log = log

    def __call__(self, request: Request) -> Response:
        self.log.append("RolePermissionMiddleware.request_in")
        response = self.get_response(request)
        self.log.append("RolePermissionMiddleware.response_out")
        return response

    def process_view(self, request: Request, view_func: Any, args: Any, kwargs: Any) -> Optional[Response]:
        if request.user != "admin":
            # Short-circuit: Return 403 Forbidden
            return Response("403 Forbidden: Admin role required", status_code=403)
        return None


# --- Verification Harness ---
if __name__ == "__main__":
    def billing_view(req: Request) -> Response:
        return Response("Billing Data", status_code=200)

    pipeline = LifecyclePipeline(billing_view)
    pipeline.add_middleware(AuditLogMiddleware)
    pipeline.add_middleware(RolePermissionMiddleware)

    # Test 1: Non-admin request -> Should short-circuit in process_view
    req_guest = Request(path="/admin/billing", user="guest")
    res_guest = pipeline.execute(req_guest)

    assert res_guest.status_code == 403
    assert "view.execute" not in pipeline.execution_log
    assert pipeline.execution_log == [
        "AuditLogMiddleware.request_in",
        "RolePermissionMiddleware.request_in",
        "RolePermissionMiddleware.process_view",
        "RolePermissionMiddleware.process_view_SHORT_CIRCUIT",
        "RolePermissionMiddleware.response_out",
        "AuditLogMiddleware.response_out",
    ]
    print("[SUCCESS] Test 1: process_view short-circuit and reverse response order verified.")

    # Test 2: Admin request -> Should proceed to view execution
    req_admin = Request(path="/admin/billing", user="admin")
    res_admin = pipeline.execute(req_admin)

    assert res_admin.status_code == 200
    assert "view.execute" in pipeline.execution_log
    assert pipeline.execution_log == [
        "AuditLogMiddleware.request_in",
        "RolePermissionMiddleware.request_in",
        "RolePermissionMiddleware.process_view",
        "view.execute",
        "RolePermissionMiddleware.response_out",
        "AuditLogMiddleware.response_out",
    ]
    print("[SUCCESS] Test 2: Complete onion traversal to view verified.")
    print("[SUCCESS] Challenge 2: Lifecycle pipeline verified successfully.")
```

---

## Challenge 3: Distributed Request Tracing & Token Bucket Rate Limiting Suite

### Scenario
Your engineering team operates an e-commerce platform processing millions of API calls per day. During promotional flash sales, rogue crawlers and aggressive integrations overwhelm backend microservices. You must engineer a coordinated middleware suite comprising:
1. **`CorrelationIdMiddleware`**: Tracks requests across distributed service boundaries using asynchronous-safe context propagation (`contextvars`).
2. **`TokenBucketRateLimitingMiddleware`**: Implements a high-performance in-memory Token Bucket rate limiter that throttles clients by IP address, short-circuiting with RFC 6585 `HTTP 429 Too Many Requests` and standard rate limit headers.

### Requirements & Constraints
1. **Asynchronous Safety**: Use `contextvars.ContextVar` to store and access the correlation ID without cross-thread contamination.
2. **Header Propagation**:
   - Accept existing client `X-Request-ID` or generate a new `uuid4`.
   - Always inject `X-Request-ID` into outgoing response headers.
3. **Token Bucket Specification**:
   - `capacity`: Maximum burst token count (e.g., 5 tokens).
   - `refill_rate`: Tokens regenerated per second (e.g., 2 tokens/sec).
   - Dynamically compute replenishment on request arrival without background daemon threads.
4. **RFC 6585 Compliance**:
   - Emit `X-RateLimit-Limit`, `X-RateLimit-Remaining`.
   - On limit exhaustion: Return HTTP 429 with `Retry-After` header (seconds until 1 token is available).
5. **Zero External Dependencies**: Pure Python standard library only.

### Executable Solution & Verification

```python
import contextvars
import json
import math
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

# Context variable for thread-safe distributed tracing
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id_ctx", default="")


class HttpRequest:
    def __init__(self, path: str, ip: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.path = path
        self.ip = ip
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.correlation_id: str = ""


class HttpResponse:
    def __init__(self, body: str, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.headers: Dict[str, str] = {}

    def set_header(self, key: str, value: str) -> None:
        self.headers[key] = value


class CorrelationIdMiddleware:
    """Propagates or generates X-Request-ID across the application lifecycle."""

    HEADER_NAME = "x-request-id"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming_id = request.headers.get(self.HEADER_NAME)
        correlation_id = incoming_id if incoming_id else str(uuid.uuid4())
        
        request.correlation_id = correlation_id
        token = request_id_ctx.set(correlation_id)

        try:
            response = self.get_response(request)
        finally:
            request_id_ctx.reset(token)

        response.set_header("X-Request-ID", correlation_id)
        return response


class TokenBucketLimiter:
    """Token Bucket rate limiter with lazy replenishment."""

    def __init__(self, capacity: int, refill_rate: float) -> None:
        self.capacity = float(capacity)
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_update = time.monotonic()

    def consume(self, amount: float = 1.0) -> Tuple[bool, int, float]:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.last_update = now

        # Replenish tokens up to capacity
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))

        if self.tokens >= amount:
            self.tokens -= amount
            return True, int(math.floor(self.tokens)), 0.0

        # Deficit calculation
        deficit = amount - self.tokens
        retry_after = deficit / self.refill_rate
        return False, 0, retry_after


class RateLimitMiddleware:
    """IP-based Token Bucket rate limiting middleware."""

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
        capacity: int = 3,
        refill_rate: float = 1.0,
    ) -> None:
        self.get_response = get_response
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._limiters: Dict[str, TokenBucketLimiter] = {}

    def __call__(self, request: HttpRequest) -> HttpResponse:
        limiter = self._limiters.get(request.ip)
        if limiter is None:
            limiter = TokenBucketLimiter(self.capacity, self.refill_rate)
            self._limiters[request.ip] = limiter

        allowed, remaining, retry_after = limiter.consume(1.0)

        if not allowed:
            # Short-circuit with HTTP 429
            payload = json.dumps({
                "error": "RATE_LIMIT_EXCEEDED",
                "retry_after": round(retry_after, 2),
                "request_id": request_id_ctx.get(),
            })
            resp = HttpResponse(payload, status_code=429)
            resp.set_header("Retry-After", str(int(math.ceil(retry_after))))
            resp.set_header("X-RateLimit-Limit", str(self.capacity))
            resp.set_header("X-RateLimit-Remaining", "0")
            return resp

        response = self.get_response(request)
        response.set_header("X-RateLimit-Limit", str(self.capacity))
        response.set_header("X-RateLimit-Remaining", str(remaining))
        return response


# --- Verification Harness ---
if __name__ == "__main__":
    def checkout_view(req: HttpRequest) -> HttpResponse:
        return HttpResponse(json.dumps({"status": "order_placed"}), status_code=200)

    # Build Pipeline: CorrelationId -> RateLimit -> View
    pipeline_handler = CorrelationIdMiddleware(
        RateLimitMiddleware(checkout_view, capacity=2, refill_rate=1.0)
    )

    client_ip = "10.0.0.1"

    # Request 1: Allowed (Remaining = 1)
    r1 = pipeline_handler(HttpRequest("/api/checkout", ip=client_ip))
    assert r1.status_code == 200
    assert "X-Request-ID" in r1.headers
    assert r1.headers["X-RateLimit-Remaining"] == "1"

    # Request 2: Allowed (Remaining = 0)
    r2 = pipeline_handler(HttpRequest("/api/checkout", ip=client_ip))
    assert r2.status_code == 200
    assert r2.headers["X-RateLimit-Remaining"] == "0"

    # Request 3: Exhausted -> Must return 429
    r3 = pipeline_handler(HttpRequest("/api/checkout", ip=client_ip))
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers
    assert r3.headers["X-RateLimit-Remaining"] == "0"
    # Verify correlation ID is still present on 429 error
    assert "X-Request-ID" in r3.headers

    print("[SUCCESS] Challenge 3: Correlation tracing and Token Bucket rate limiting verified.")
```
