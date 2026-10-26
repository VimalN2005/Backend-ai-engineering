# Critical Antipatterns in Django Views, Middleware & Request Lifecycle

This guide documents 7 prevalent architectural, concurrency, and security antipatterns encountered in Django view layers and middleware pipelines, accompanied by real-world failure analyses and production-grade remediations.

---

## Antipattern 1: Placing `AuthenticationMiddleware` Before `SessionMiddleware`

### Severity: CRITICAL (Security & Authorization Failure)

### The Flawed Pattern
```python
# settings.py - BROKEN MIDDLEWARE ORDER
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware', # <-- CRITICAL FLAW!
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
]
```

### The Failure Mechanism
Django's `AuthenticationMiddleware` inspects `request.session` to extract `_auth_user_id` and lazy-load the corresponding `User` model onto `request.user`. However, `request.session` is instantiated and populated from cookies exclusively by `SessionMiddleware`.

When `AuthenticationMiddleware` runs before `SessionMiddleware`:
1. `request.session` has not been initialized yet.
2. An `AttributeError: 'HttpRequest' object has no attribute 'session'` is raised, or in some configurations `request.user` silently defaults to `AnonymousUser`.
3. Every authenticated user is treated as anonymous, causing widespread authorization rejections or security bypasses on public/private boundary checks.

### Production Remediation
Strictly follow Django's dependency hierarchy: `SessionMiddleware` MUST precede `AuthenticationMiddleware`.

```python
# settings.py - RESOLVED
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',       # 1. Populates request.session
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',   # 2. Uses request.session
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

---

## Antipattern 2: Direct In-Place Mutation of `request.GET` via `_mutable = True`

### Severity: HIGH (State Contamination & Parameter Tampering)

### The Flawed Pattern
```python
# views.py or custom middleware
def search_view(request):
    # FLAW: Mutating private internal attribute to bypass immutability
    request.GET._mutable = True
    request.GET['page_size'] = 50  # Overrides caller query param in-place!
    request.GET._mutable = False
    
    # ... downstream processing
    return render(request, 'search.html')
```

### The Failure Mechanism
`QueryDict` instances (`request.GET` and `request.POST`) are explicitly designed to be immutable. Directly tampering with private attributes (`_mutable = True`) causes:
1. **Downstream Side-Effects**: Downstream middleware, analytics interceptors, audit loggers, and caching engines receive corrupted parameters that differ from what the client actually transmitted.
2. **Cache Key Pollution**: If a response cache key is derived from `request.GET.urlencode()`, changing parameters in-place invalidates cache coherency or poisons shared cache entries.
3. **Hidden Invariants**: Python's encapsulation conventions dictate that leading underscores (`_mutable`) are internal implementation details subject to breaking changes across Django versions.

### Production Remediation
Always produce a defensive mutable copy using `.copy()`, apply transformations locally, or explicitly construct query strings for redirects/pagination:

```python
# views.py - RESOLVED
def search_view(request):
    # 1. Obtain a clean, isolated mutable clone
    clean_params = request.GET.copy()
    
    # 2. Safely normalize or provide defaults without touching request.GET
    page_size = int(clean_params.get('page_size', 50))
    page_size = min(max(page_size, 10), 100) # Enforce bound constraints
    
    # 3. Pass clean_params into service/query layer
    queryset = ProductService.search(
        query=request.GET.get('q', ''),
        page_size=page_size
    )
    return JsonResponse({'results': list(queryset)})
```

---

## Antipattern 3: Executing Database Queries or Network I/O Inside Middleware `__init__`

### Severity: CRITICAL (Cold-Start Freezes, Deadlocks & Worker Starvation)

### The Flawed Pattern
```python
# middleware.py
class FeatureFlagMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # FLAW: Blocking database query executed at server boot time!
        self.flags = {f.name: f.enabled for f in FeatureFlag.objects.all()}

    def __call__(self, request):
        request.feature_flags = self.flags
        return self.get_response(request)
```

### The Failure Mechanism
1. **Startup Execution**: Django initializes middleware classes **once** when the WSGI/ASGI application worker boots (`manage.py runserver`, `gunicorn`, `uvicorn`).
2. **Stale Cache**: The `self.flags` dictionary is loaded once into worker memory and **never updates** when database rows change in production, creating ghost state across worker processes.
3. **Deployment Deadlock**: During automated CI/CD deployments, `manage.py migrate` or `collectstatic` imports `settings.py` and initializes middleware before database tables exist, causing `django.db.utils.ProgrammingError: relation does not exist` and aborting deployments.
4. **Connection Pool Depletion**: Pre-fork web servers (like Gunicorn) opening DB connections in master process before fork cause shared connection socket bugs across child workers.

### Production Remediation
Execute dynamic state lookups inside `__call__` (per-request) backed by a low-latency caching layer (Redis / Memcached):

```python
# middleware.py - RESOLVED
from django.core.cache import cache

class FeatureFlagMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Fetch from Redis cache or fall back to DB with short TTL
        flags = cache.get("global_feature_flags")
        if flags is None:
            flags = {f.name: f.enabled for f in FeatureFlag.objects.filter(is_active=True)}
            cache.set("global_feature_flags", flags, timeout=60) # 60-second TTL
            
        request.feature_flags = flags
        return self.get_response(request)
```

---

## Antipattern 4: Placing `CorsMiddleware` After `CommonMiddleware` or View Authentication

### Severity: HIGH (CORS Preflight Failures & Unnecessary Latency)

### The Flawed Pattern
```python
# settings.py - BROKEN CORS ORDER
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'corsheaders.middleware.CorsMiddleware', # <-- FLAW: Placed too deep!
    'django.middleware.csrf.CsrfViewMiddleware',
]
```

### The Failure Mechanism
1. **OPTIONS Preflight Interception**: Modern web browsers issue an unauthenticated `OPTIONS` HTTP preflight request before submitting cross-origin POST, PUT, or DELETE requests.
2. **Unnecessary Authentication**: When `CorsMiddleware` is placed below `CommonMiddleware` or `AuthenticationMiddleware`, every browser preflight traverses session decoding, authentication verification, and URL appending before CORS headers can be evaluated.
3. **CORS Rejection**: If an upstream middleware or view returns an HTTP 401, 403, or 301 redirect before reaching `CorsMiddleware`, the response is returned **without** `Access-Control-Allow-Origin` headers, causing modern browsers to reject the request entirely.

### Production Remediation
Place `CorsMiddleware` as high as possible, immediately following `SecurityMiddleware` and prior to any middleware that can generate responses or redirects:

```python
# settings.py - RESOLVED
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',            # <-- Immediately after SecurityMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # ...
]
```

---

## Antipattern 5: Leaking Raw Exception Tracebacks in API Error Responses

### Severity: HIGH (Security Vulnerability: CWE-209 Information Exposure)

### The Flawed Pattern
```python
# views.py or custom exception handler
def payment_checkout(request):
    try:
        charge = BillingGateway.charge(request.POST['amount'])
        return JsonResponse({'status': 'success', 'id': charge.id})
    except Exception as e:
        # FLAW: Exposing raw internal exception and traceback to client!
        return JsonResponse({
            'status': 'error',
            'detail': str(e),
            'traceback': traceback.format_exc()
        }, status=500)
```

### The Failure Mechanism
Exposing raw exception strings or stack traces to clients reveals:
1. Underlying database engine, table names, and column types.
2. Internal directory paths, Python package versions, and proprietary logic.
3. Potential database credentials or API keys embedded in database connector error messages (e.g. `psycopg2.OperationalError: password authentication failed for user "db_user"`).

### Production Remediation
Normalize exceptions using a centralized middleware or exception handler. Log detailed diagnostics to private APM collectors (Sentry, Datadog), but return a sanitized, RFC 7807-compliant error payload with a correlation ID to the client:

```python
# middleware.py - RESOLVED
import logging
import uuid
from django.http import JsonResponse

logger = logging.getLogger("enterprise.errors")

class GlobalExceptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # 1. Generate or extract correlation ID
        correlation_id = getattr(request, "correlation_id", str(uuid.uuid4()))
        
        # 2. Log full diagnostic stack trace internally with correlation ID
        logger.error(
            "Unhandled exception on %s %s [TraceID: %s]: %s",
            request.method,
            request.path,
            correlation_id,
            exception,
            exc_info=True,
        )
        
        # 3. Return sanitized, opaque payload to external consumers
        return JsonResponse({
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred. Please contact support with the trace ID.",
                "trace_id": correlation_id,
            }
        }, status=500)
```

---

## Antipattern 6: Storing Request State on CBV Class Attributes

### Severity: CRITICAL (Thread Safety & Data Leakage Between Requests)

### The Flawed Pattern
```python
# views.py
from django.views import View

class UserProfileView(View):
    # FLAW: Class-level shared mutable state!
    cached_permissions = []

    def get(self, request, user_id):
        self.cached_permissions = PermissionService.get_for(user_id)
        return JsonResponse({"permissions": self.cached_permissions})

    def post(self, request, user_id):
        # FLAW: self.cached_permissions might hold another user's permissions
        # if concurrent threads execute or if modified on class!
        UserProfileView.cached_permissions.append(request.POST.get("new_perm"))
        return JsonResponse({"status": "updated"})
```

### The Failure Mechanism
1. In multi-threaded WSGI workers (e.g., Gunicorn `gthread` worker class), mutable class attributes belong to the class object in the shared heap memory.
2. When Worker Thread A writes to `UserProfileView.cached_permissions`, Worker Thread B servicing a completely different user concurrently accesses that modified list.
3. User A receives User B's permissions, leading to privilege escalation and data contamination.

### Production Remediation
Always store request-scoped data on the instance inside `dispatch()` or method handlers (`self.request`, local variables), never at the class level:

```python
# views.py - RESOLVED
class UserProfileView(View):
    def get(self, request, user_id):
        # Local variable isolated to the current request stack frame
        user_permissions = PermissionService.get_for(user_id)
        return JsonResponse({"permissions": user_permissions})

    def post(self, request, user_id):
        new_perm = request.POST.get("new_perm")
        if not new_perm:
            return JsonResponse({"error": "Missing permission"}, status=400)
            
        updated = PermissionService.grant(user_id, new_perm)
        return JsonResponse({"status": "updated", "permissions": updated})
```

---

## Antipattern 7: Implementing Endpoint-Specific Logic in Global Middleware

### Severity: MEDIUM (System-Wide Overhead & Unintended Interception)

### The Flawed Pattern
```python
# middleware.py - GLOBAL MIDDLEWARE RUNNING ON EVERY REQUEST
class PaymentSignatureVerificationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # FLAW: String parsing and HMAC verification runs on EVERY request,
        # including /healthz, /static/, /metrics, and standard user logins!
        if request.path.startswith("/api/v1/webhooks/stripe/"):
            signature = request.headers.get("Stripe-Signature")
            if not self.verify_hmac(request.body, signature):
                return HttpResponseForbidden("Invalid signature")
                
        return self.get_response(request)
```

### The Failure Mechanism
1. **Execution Tax on Every Route**: Global middleware executes on **100%** of all incoming HTTP requests. Path string parsing (`request.path.startswith(...)`) and reading `request.body` slows down high-frequency lightweight routes such as Kubernetes healthchecks (`/healthz`) and Prometheus scrapers (`/metrics`).
2. **Body Stream Depletion**: Reading `request.body` in middleware before downstream views might consume the WSGI stream prematurely if not handled cleanly.
3. **Violation of Cohesion**: Endpoint-specific business policies (like webhook signatures) belong to the specific view or route controller, not the global infrastructure envelope.

### Production Remediation
Use view decorators or custom DRF permission/authentication classes that bind directly and exclusively to the designated route:

```python
# decorators.py - RESOLVED
import functools
import hmac
import hashlib
from django.http import HttpResponseForbidden

def require_stripe_signature(view_func):
    @functools.wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        sig = request.headers.get("Stripe-Signature")
        if not sig or not verify_stripe_hmac(request.body, sig):
            return HttpResponseForbidden("Invalid or missing Stripe signature")
        return view_func(request, *args, **kwargs)
    return _wrapped_view

# urls.py
urlpatterns = [
    path('api/v1/webhooks/stripe/', require_stripe_signature(stripe_webhook_view)),
]
```
