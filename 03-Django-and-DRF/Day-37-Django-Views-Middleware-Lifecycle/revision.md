# 5-Minute Rapid Revision: Views, Lifecycle & Middleware Pipelines

A high-yield, quick-reference cheat sheet for senior backend engineers reviewing Django's request-response lifecycle, CBVs, and custom middleware pipelines.

---

## 1. The 9-Stage Request Lifecycle Matrix

```
STAGE                    COMPONENT                PRIMARY RESPONSIBILITY
─────────────────────────────────────────────────────────────────────────────────────────────
1. Reverse Proxy         Nginx / Envoy / ALB      TLS termination, static files, buffering
2. WSGI/ASGI Server      Gunicorn / Uvicorn       Socket parsing into Python `environ` dict
3. Handler Layer         WSGIHandler              Builds HttpRequest, parses QueryDict
4. Middleware (Request)  MIDDLEWARE (Forward)     Security, CORS, Sessions, CSRF, Auth
5. URL Routing           urls.resolve()           Regex matching -> view function + kwargs
6. Middleware (View)     process_view (Forward)   Pre-view permissions, method validation
7. View Execution        FBV / CBV.dispatch()     Business logic, DB queries, HttpResponse
8. Middleware (Response) MIDDLEWARE (REVERSE!)    Headers, Cookies, ETag, Compression (GZip)
9. Web Server Stream     start_response           Flushes HTTP status & bytes to client TCP
─────────────────────────────────────────────────────────────────────────────────────────────
```

---

## 2. The 5 Non-Negotiable Middleware Ordering Laws

1. **`SecurityMiddleware` FIRST**: Must enforce HTTPS redirect and HSTS before any request processing.
2. **`CorsMiddleware` SECOND**: Must precede `CommonMiddleware` to short-circuit unauthenticated browser `OPTIONS` preflight requests.
3. **`SessionMiddleware` BEFORE `AuthenticationMiddleware`**: Auth reads `request.session['_auth_user_id']`. If inverted, `request.user` is undefined or `AnonymousUser`.
4. **`CommonMiddleware` AFTER `SessionMiddleware`**: Required for `APPEND_SLASH` redirects and Content-Length generation.
5. **`GZipMiddleware` LAST (Outermost Response)**: Compressing after TLS encryption prevents BREACH attacks and ensures headers are finalized.

```python
# The Canonical Enterprise Order
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',         # 1. Security & HTTPS
    'corsheaders.middleware.CorsMiddleware',                 # 2. CORS (Preflight intercept)
    'django.contrib.sessions.middleware.SessionMiddleware',  # 3. request.session populated
    'django.middleware.common.CommonMiddleware',             # 4. URL normalization
    'django.middleware.csrf.CsrfViewMiddleware',             # 5. CSRF validation
    'django.contrib.auth.middleware.AuthenticationMiddleware', # 6. request.user populated
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

---

## 3. CBV Thread Safety & `as_view()` Mechanics

- **How it works**: `as_view()` is a factory closure.
- **Per-Request Isolation**: Every HTTP request instantiates a **new instance** of the view class:
  ```python
  def view(request, *args, **kwargs):
      self = cls(**initkwargs)  # <- Brand new instance per thread/request
      self.setup(request, *args, **kwargs)
      return self.dispatch(request, *args, **kwargs)
  ```
- **Golden Rule**: Never store request state in **class-level attributes** (`cls.state`). Only store on the instance (`self.state`) or in local function variables.

---

## 4. `QueryDict` Quick Reference

| Operation | Syntax | Behavior / Return Value |
| :--- | :--- | :--- |
| **Get Last Value** | `request.GET.get('key', 'default')` | Returns the **last** submitted value (mirrors standard dict). |
| **Get All Values** | `request.GET.getlist('key')` | Returns a `list` of all submitted values (`['v1', 'v2']`). |
| **Direct Mutation** | `request.GET['key'] = 'val'` | ❌ **Raises `AttributeError`** (immutable by design). |
| **Defensive Copy** | `clean_params = request.GET.copy()` | ✅ Returns a deep, **mutable** clone. |
| **Re-encode Query**| `clean_params.urlencode()` | Returns RFC 3986 URL query string (`key=val&k2=v2`). |

---

## 5. Enterprise Middleware Production Template

Copy-pasteable boilerplate implementing `__call__`, `process_view`, and `process_exception`:

```python
import logging
import time
import uuid
from typing import Callable, Optional
from django.http import HttpRequest, HttpResponse, JsonResponse

logger = logging.getLogger("enterprise.middleware")

class EnterpriseCustomMiddleware:
    """
    Standard enterprise middleware with request timing, tracing, and error normalization.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        # Runs ONCE at server startup. NEVER perform blocking DB queries here!
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # ====================================================================
        # INBOUND REQUEST PHASE (Outer -> Inner)
        # ====================================================================
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.request_id = request_id
        start_time = time.perf_counter()

        # Delegate downstream to next middleware / view
        response = self.get_response(request)

        # ====================================================================
        # OUTBOUND RESPONSE PHASE (Inner -> Outer in REVERSE order!)
        # ====================================================================
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response["X-Request-ID"] = request_id
        response["Server-Timing"] = f"total;dur={duration_ms:.3f}"
        return response

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable,
        view_args: tuple,
        view_kwargs: dict,
    ) -> Optional[HttpResponse]:
        """
        Runs AFTER URL routing, BEFORE view executes.
        Return None to continue, or return HttpResponse to SHORT-CIRCUIT!
        """
        return None

    def process_exception(self, request: HttpRequest, exception: Exception) -> Optional[HttpResponse]:
        """
        Catches unhandled exceptions bubbling up from views or inner middleware.
        """
        request_id = getattr(request, "request_id", "unknown")
        logger.error("Unhandled error [TraceID: %s]: %s", request_id, exception, exc_info=True)
        
        return JsonResponse({
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected server error occurred.",
                "request_id": request_id,
            }
        }, status=500)
```

---

## 6. High-Frequency Production Debugging Checklist

- [ ] **Are authenticated requests showing as `AnonymousUser`?** Check if `AuthenticationMiddleware` is listed before `SessionMiddleware`.
- [ ] **Are CORS preflight requests returning 401/403 or redirects?** Verify `CorsMiddleware` is immediately after `SecurityMiddleware` and above all auth layers.
- [ ] **Are query parameters failing to update?** Did you call `request.GET.copy()` or accidentally mutate a frozen `QueryDict`?
- [ ] **Is the server locking up during Gunicorn worker boot?** Check for database queries or remote HTTP calls inside middleware `__init__()`.
- [ ] **Are thread race conditions appearing in CBVs?** Ensure no mutable lists or dicts are defined as class-level attributes on views or decorators.
