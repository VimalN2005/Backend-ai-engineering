"""
Day 37: Views, Request-Response Lifecycle & Custom Middleware Pipelines
Example 01: Django Request-Response Pipeline, QueryDict Immutability & View Dispatch Engine

Architecture Overview:
----------------------
This module delivers a pure Python, zero-external-dependency architectural simulation of
Django's internal HTTP request-response engine. It models:
  1. `QueryDict`: An immutable multi-value dictionary backing `request.GET` and `request.POST`.
  2. `HttpRequest` & `HttpResponse` Hierarchy: Type-safe request wrapping and response headers.
  3. Class-Based View (CBV) Engine: Thread-safe per-request instantiation via `as_view()` closure.
  4. Onion-Model Middleware Pipeline: Nested callable chaining with short-circuiting capabilities.

WHY THIS ARCHITECTURE?
----------------------
In high-throughput enterprise systems, understanding the mechanical sympathy of Django's
request lifecycle prevents state contamination across worker threads, clarifies why `QueryDict`
protects against cache poisoning and parameter tampering, and illustrates how middleware
wrappers provide composable cross-cutting concerns without polluting business views.
"""

from __future__ import annotations

import copy
import json
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# ============================================================================
# 1. QueryDict: Multi-Value Immutable Dictionary
# ============================================================================

class QueryDict:
    """
    Simulates django.http.QueryDict.

    WHY MULTI-VALUE?
    Standard Python dictionaries allow exactly one value per key. In HTTP/1.1 and HTTP/2,
    HTML forms and query strings frequently repeat keys to represent lists:
        ?filter=active&filter=pending&sort=desc
    QueryDict stores values internally as List[Any] to preserve all submitted values.

    WHY IMMUTABLE BY DEFAULT?
    HTTP GET query strings and POST bodies represent client inputs. If any middleware,
    library, or decorator could arbitrarily mutate `request.GET` in-place, downstream
    consumers (audit loggers, security filters, view handlers) would suffer from
    non-deterministic parameter pollution. To mutate, developers must explicitly call
    `querydict.copy()` to make a mutable defensive copy.
    """

    def __init__(self, query_string: str = "", mutable: bool = False) -> None:
        self._data: Dict[str, List[str]] = {}
        self._mutable: bool = mutable

        if query_string:
            parsed = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            for k, v in parsed.items():
                self._data[k] = list(v)

    def _assert_mutable(self) -> None:
        """Enforces immutability contract."""
        if not self._mutable:
            raise AttributeError("This QueryDict instance is immutable. Call .copy() to obtain a mutable clone.")

    def __getitem__(self, key: str) -> str:
        """
        Returns the LAST value submitted for the key, mirroring Django's behavior.
        Raises KeyError if key is missing.
        """
        val_list = self._data[key]
        if not val_list:
            raise KeyError(key)
        return val_list[-1]

    def __setitem__(self, key: str, value: str) -> None:
        """Sets a single value for key, wiping previous values. Requires mutability."""
        self._assert_mutable()
        self._data[key] = [str(value)]

    def __delitem__(self, key: str) -> None:
        """Deletes key. Requires mutability."""
        self._assert_mutable()
        del self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        """Returns the LAST value for key, or default if missing."""
        try:
            return self[key]
        except KeyError:
            return default

    def getlist(self, key: str, default: Optional[List[str]] = None) -> List[str]:
        """
        Returns the entire list of values submitted for the key.
        Crucial for checkboxes, multi-selects, and multi-value query params.
        """
        try:
            return list(self._data[key])
        except KeyError:
            return default if default is not None else []

    def setlist(self, key: str, list_: List[str]) -> None:
        """Replaces the value list for key. Requires mutability."""
        self._assert_mutable()
        self._data[key] = [str(item) for item in list_]

    def appendlist(self, key: str, value: str) -> None:
        """Appends value to list for key. Requires mutability."""
        self._assert_mutable()
        self._data.setdefault(key, []).append(str(value))

    def copy(self) -> "QueryDict":
        """
        Returns a deep, MUTABLE copy of this QueryDict.
        WHY: Allows views or middleware to intentionally alter query parameters
        without mutating upstream or global state.
        """
        clone = QueryDict(mutable=True)
        clone._data = copy.deepcopy(self._data)
        return clone

    def urlencode(self) -> str:
        """Encodes internal data back to standard HTTP query string format."""
        pairs: List[Tuple[str, str]] = []
        for key, values in self._data.items():
            for v in values:
                pairs.append((key, v))
        return urllib.parse.urlencode(pairs)

    def __repr__(self) -> str:
        return f"<QueryDict: {self._data}>"


# ============================================================================
# 2. HttpRequest & HttpResponse Hierarchy
# ============================================================================

class HttpRequest:
    """
    Simulates django.http.HttpRequest.

    Encapsulates client metadata, URL path, HTTP method, query strings, headers,
    and middleware-populated state (session, user, correlation IDs).
    """

    def __init__(
        self,
        method: str,
        path: str,
        query_string: str = "",
        body: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.path_info = path
        self.GET = QueryDict(query_string, mutable=False)
        self.POST = QueryDict(body.decode("utf-8", errors="ignore") if method == "POST" else "", mutable=False)
        self.body = body
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.COOKIES: Dict[str, str] = {}
        self.META: Dict[str, Any] = {"REQUEST_METHOD": self.method, "PATH_INFO": self.path}
        
        # State injected by middleware layers
        self.session: Dict[str, Any] = {}
        self.user: Optional[Dict[str, Any]] = None
        self.request_id: Optional[str] = None


class HttpResponse:
    """
    Base HTTP response object encapsulating status code, HTTP headers, and raw bytes payload.
    """

    def __init__(
        self,
        content: Union[str, bytes] = b"",
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.status_code = status
        self.headers: Dict[str, str] = {"Content-Type": content_type}
        self.cookies: Dict[str, Dict[str, Any]] = {}
        self.content = content.encode("utf-8") if isinstance(content, str) else content

    def set_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    def set_cookie(self, key: str, value: str, max_age: int = 3600, httponly: bool = True) -> None:
        self.cookies[key] = {"value": value, "max_age": max_age, "httponly": httponly}

    def __repr__(self) -> str:
        return f"<HttpResponse status_code={self.status_code} size={len(self.content)} bytes>"


class JsonResponse(HttpResponse):
    """
    Subclass of HttpResponse that automatically serializes Python dictionaries/lists
    into UTF-8 encoded JSON bytes and enforces application/json content-type.
    """

    def __init__(self, data: Any, status: int = 200, **kwargs: Any) -> None:
        dumped = json.dumps(data, indent=2)
        super().__init__(
            content=dumped,
            status=status,
            content_type="application/json; charset=utf-8",
        )


class HttpResponseBadRequest(HttpResponse):
    def __init__(self, content: str = "400 Bad Request") -> None:
        super().__init__(content=content, status=400, content_type="text/plain")


class HttpResponseForbidden(HttpResponse):
    def __init__(self, content: str = "403 Forbidden") -> None:
        super().__init__(content=content, status=403, content_type="text/plain")


class HttpResponseNotFound(HttpResponse):
    def __init__(self, content: str = "404 Not Found") -> None:
        super().__init__(content=content, status=404, content_type="text/plain")


class HttpResponseNotAllowed(HttpResponse):
    def __init__(self, permitted_methods: List[str]) -> None:
        super().__init__(content="405 Method Not Allowed", status=405, content_type="text/plain")
        self.set_header("Allow", ", ".join(m.upper() for m in permitted_methods))


# ============================================================================
# 3. View Architecture: FBV vs CBV Dispatch Mechanism
# ============================================================================

def order_detail_fbv(request: HttpRequest, order_id: int) -> HttpResponse:
    """
    Function-Based View (FBV).

    WHY FBVs?
    Simplicity, explicit procedural flow, and minimal boilerplate for straightforward endpoints.
    Drawback: Handling multiple HTTP methods requires imperative `if request.method == ...` branching.
    """
    if request.method == "GET":
        return JsonResponse({
            "order_id": order_id,
            "status": "shipped",
            "caller": request.user.get("username", "anonymous") if request.user else "anonymous",
        })
    elif request.method == "DELETE":
        return JsonResponse({"order_id": order_id, "action": "deleted"}, status=204)
    return HttpResponseNotAllowed(["GET", "DELETE"])


class View:
    """
    Base Class-Based View simulating django.views.generic.base.View.

    WHY `as_view()` AND THREAD-SAFETY?
    Django runs inside multi-threaded or multi-greenlet WSGI/ASGI workers (e.g. Gunicorn gthread).
    If a CBV was instantiated once at startup and reused, concurrent requests would write
    to `self.request` and `self.args` simultaneously, causing catastrophic race conditions.

    Django solves this by using `as_view()` as a factory closure:
    For EVERY incoming HTTP request, `as_view()` invokes `self = cls(**initkwargs)`,
    creating a brand new instance bound strictly to that thread/request.
    """

    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options", "trace"]

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.request: HttpRequest = None  # type: ignore
        self.args: Tuple[Any, ...] = ()
        self.kwargs: Dict[str, Any] = {}

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponse]:
        """
        Main entry point for request-to-view matching in urlpatterns.
        Returns a callable closure that instantiates the class on every request.
        """
        def view(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            # Step 1: Instantiate fresh instance for this specific request
            self = cls(**initkwargs)
            # Step 2: Bind request, args, and kwargs to instance
            self.setup(request, *args, **kwargs)
            # Step 3: Dispatch to corresponding HTTP method handler
            return self.dispatch(request, *args, **kwargs)

        view.view_class = cls  # type: ignore
        view.__doc__ = cls.__doc__
        return view

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        """Initializes instance attributes before dispatch is executed."""
        self.request = request
        self.args = args
        self.kwargs = kwargs

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Inspects request.method and delegates to lowercased handler (get, post, etc.).
        Returns 405 Method Not Allowed if handler is absent or method is invalid.
        """
        method = request.method.lower()
        if method in self.http_method_names:
            handler = getattr(self, method, None)
            if handler is not None and callable(handler):
                return handler(request, *args, **kwargs)
        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        allowed = [m.upper() for m in self.http_method_names if hasattr(self, m)]
        return HttpResponseNotAllowed(allowed)


class OrderAPIView(View):
    """
    Concrete CBV demonstrating declarative HTTP method separation.
    """

    def get(self, request: HttpRequest, order_id: int) -> HttpResponse:
        # Inspect query params
        include_items = request.GET.get("include_items", "false").lower() == "true"
        data = {
            "order_id": order_id,
            "status": "processing",
            "items": ["SKU-001", "SKU-992"] if include_items else [],
            "requested_by": request.user.get("username") if request.user else "guest",
        }
        return JsonResponse(data)

    def post(self, request: HttpRequest, order_id: int) -> HttpResponse:
        action = request.POST.get("action")
        if not action:
            return HttpResponseBadRequest("Missing required 'action' field in form body")
        return JsonResponse({"order_id": order_id, "action_performed": action, "status": "updated"})


# ============================================================================
# 4. Onion-Model Middleware Pipeline Simulation
# ============================================================================

class MiddlewarePipeline:
    """
    Simulates Django's WSGIHandler middleware loading and execution onion.

    In Django:
      Each middleware wraps the downstream callable:
          handler = Middleware1(Middleware2(Middleware3(view_function)))
      
      When executed:
          Request flows forward:  Middleware1 -> Middleware2 -> Middleware3 -> View
          Response flows reverse: View -> Middleware3 -> Middleware2 -> Middleware1
    """

    def __init__(self, view_callable: Callable[..., HttpResponse]) -> None:
        self.view_callable = view_callable
        self.middlewares: List[Any] = []
        self._chain: Optional[Callable[[HttpRequest], HttpResponse]] = None

    def add_middleware(self, middleware_cls: type) -> None:
        self.middlewares.append(middleware_cls)

    def build_chain(self) -> Callable[[HttpRequest], HttpResponse]:
        """
        Constructs the nested closure onion.
        Notice that we reverse the middleware list so the innermost middleware
        directly wraps the view callable.
        """
        handler = self.view_callable

        # Wrap in reverse order so first middleware in list executes first
        for middleware_cls in reversed(self.middlewares):
            handler = middleware_cls(handler)

        self._chain = handler
        return handler

    def execute(self, request: HttpRequest) -> HttpResponse:
        if self._chain is None:
            self.build_chain()
        assert self._chain is not None
        return self._chain(request)


# ----------------------------------------------------------------------------
# Middleware Layers
# ----------------------------------------------------------------------------

class SessionSimulationMiddleware:
    """Simulates SessionMiddleware: Decodes session cookie into request.session."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # --- REQUEST PHASE (OUTER -> INNER) ---
        session_id = request.headers.get("x-session-id")
        if session_id == "sess_alice_secret":
            request.session["user_id"] = 101
            request.session["role"] = "admin"
        else:
            request.session = {}

        # Delegate to next layer
        response = self.get_response(request)

        # --- RESPONSE PHASE (INNER -> OUTER) ---
        response.set_header("X-Session-Active", "true" if request.session else "false")
        return response


class AuthenticationSimulationMiddleware:
    """
    Simulates AuthenticationMiddleware.
    MUST execute after SessionMiddleware because it relies on request.session.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # --- REQUEST PHASE ---
        user_id = request.session.get("user_id")
        if user_id == 101:
            request.user = {"id": 101, "username": "alice", "is_staff": True}
        else:
            request.user = None

        response = self.get_response(request)

        # --- RESPONSE PHASE ---
        if request.user:
            response.set_header("X-Authenticated-User", request.user["username"])
        return response


class CsrfSimulationMiddleware:
    """
    Simulates CsrfViewMiddleware.
    Rejects unsafe HTTP methods (POST, PUT, DELETE, PATCH) lacking a valid CSRF token.
    Demonstrates pipeline SHORT-CIRCUITING.
    """

    SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Short-circuit check for state-mutating methods
        if request.method not in self.SAFE_METHODS:
            token = request.headers.get("x-csrftoken")
            if not token or token != "valid_csrf_token_secret":
                # SHORT-CIRCUIT: Returns HTTP 403 immediately without calling get_response()!
                # The view is NEVER invoked!
                return HttpResponseForbidden("CSRF verification failed: Missing or invalid X-CSRFToken")

        # Safe method or valid CSRF: continue downstream
        response = self.get_response(request)
        return response


class TimingTelemetryMiddleware:
    """Calculates total request execution time and injects Server-Timing header."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        start_time = time.perf_counter()
        
        response = self.get_response(request)
        
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response.set_header("Server-Timing", f"app;dur={duration_ms:.3f}")
        return response


# ============================================================================
# 5. Verification & Demonstration Suite
# ============================================================================

def test_querydict_immutability() -> None:
    print("[TEST 1] QueryDict Multi-Value and Immutability Checks...")
    raw_query = "tag=python&tag=django&tag=drf&page=2&sort=asc"
    qd = QueryDict(raw_query)

    # 1. get() returns the LAST value
    assert qd.get("tag") == "drf", f"Expected 'drf', got {qd.get('tag')}"
    assert qd["tag"] == "drf"

    # 2. getlist() returns all values
    assert qd.getlist("tag") == ["python", "django", "drf"]
    assert qd.get("page") == "2"

    # 3. Immutability assertion
    try:
        qd["page"] = "3"
        assert False, "Should have raised AttributeError for immutable modification"
    except AttributeError as e:
        print(f"  -> Caught expected immutability error: {e}")

    # 4. Copy makes it mutable
    mutable_qd = qd.copy()
    mutable_qd["page"] = "3"
    mutable_qd.appendlist("tag", "celery")
    assert mutable_qd.get("page") == "3"
    assert mutable_qd.getlist("tag") == ["python", "django", "drf", "celery"]
    # Verify original remained unchanged
    assert qd.get("page") == "2"
    assert len(qd.getlist("tag")) == 3
    print("  -> QueryDict immutability and copy semantics verified successfully.\n")


def test_cbv_as_view_thread_safety() -> None:
    print("[TEST 2] CBV as_view() Thread-Safety & Method Dispatch...")
    view_func = OrderAPIView.as_view()

    # Request 1: GET with query param
    req_get = HttpRequest("GET", "/orders/42/", query_string="include_items=true")
    resp_get = view_func(req_get, order_id=42)
    assert resp_get.status_code == 200
    payload_get = json.loads(resp_get.content.decode("utf-8"))
    assert payload_get["order_id"] == 42
    assert len(payload_get["items"]) == 2
    print("  -> CBV GET handled correctly:", payload_get)

    # Request 2: POST with invalid form body (missing action)
    req_post_invalid = HttpRequest("POST", "/orders/42/", body=b"foo=bar")
    resp_post_invalid = view_func(req_post_invalid, order_id=42)
    assert resp_post_invalid.status_code == 400
    print("  -> CBV POST validation handled correctly (HTTP 400)")

    # Request 3: Method Not Allowed (PUT)
    req_put = HttpRequest("PUT", "/orders/42/")
    resp_put = view_func(req_put, order_id=42)
    assert resp_put.status_code == 405
    assert "GET" in resp_put.headers["Allow"]
    assert "POST" in resp_put.headers["Allow"]
    print("  -> CBV 405 Method Not Allowed verified. Allow headers:", resp_put.headers["Allow"])
    print("  -> CBV dispatch and per-request lifecycle verified.\n")


def test_onion_middleware_pipeline() -> None:
    print("[TEST 3] Onion-Model Middleware Pipeline Execution...")
    cbv_callable = OrderAPIView.as_view()

    # Setup pipeline with standard enterprise order
    # Outer -> Inner:
    # Timing -> Csrf -> Session -> Auth -> View
    pipeline = MiddlewarePipeline(
        view_callable=lambda req: cbv_callable(req, order_id=999)
    )
    pipeline.add_middleware(TimingTelemetryMiddleware)
    pipeline.add_middleware(CsrfSimulationMiddleware)
    pipeline.add_middleware(SessionSimulationMiddleware)
    pipeline.add_middleware(AuthenticationSimulationMiddleware)
    pipeline.build_chain()

    # Scenario A: Authenticated GET request
    req_auth = HttpRequest(
        method="GET",
        path="/orders/999/",
        headers={"x-session-id": "sess_alice_secret"},
    )
    resp_auth = pipeline.execute(req_auth)
    assert resp_auth.status_code == 200
    assert resp_auth.headers["X-Authenticated-User"] == "alice"
    assert "Server-Timing" in resp_auth.headers
    print("  -> Scenario A (Auth GET): Success 200 OK. Headers:", dict(resp_auth.headers))

    # Scenario B: POST without CSRF token -> MUST short-circuit at CsrfSimulationMiddleware
    req_unsafe = HttpRequest(
        method="POST",
        path="/orders/999/",
        body=b"action=cancel",
        headers={"x-session-id": "sess_alice_secret"},  # Has valid session but NO CSRF token
    )
    resp_csrf_fail = pipeline.execute(req_unsafe)
    assert resp_csrf_fail.status_code == 403
    assert b"CSRF verification failed" in resp_csrf_fail.content
    # Note: SessionMiddleware and AuthMiddleware were NOT executed because CSRF rejected it upstream!
    assert "X-Authenticated-User" not in resp_csrf_fail.headers
    assert "Server-Timing" in resp_csrf_fail.headers  # Timing is outer to CSRF, so it still runs response
    print("  -> Scenario B (CSRF Block): Success 403 Forbidden. Short-circuit verified.\n")


if __name__ == "__main__":
    print("======================================================================")
    print(" Day 37: Django Request-Response Pipeline & View Dispatch Engine")
    print("======================================================================\n")
    test_querydict_immutability()
    test_cbv_as_view_thread_safety()
    test_onion_middleware_pipeline()
    print("All Day 37 Example 01 architectural tests passed successfully!")
