# Critical Antipatterns in DRF ViewSets, Routers & Actions

This guide documents 7 prevalent architectural, security, and routing antipatterns encountered when designing APIs with Django REST Framework (DRF) ViewSets, complete with production failure analyses and remediations.

---

## Antipattern 1: Relying on Static Class-Level `queryset` (Insecure Direct Object Reference - IDOR)

### Severity: CRITICAL (Security Vulnerability: OWASP Top 10 Broken Access Control)

### The Flawed Pattern
```python
# views.py
class CustomerInvoiceViewSet(viewsets.ModelViewSet):
    # FLAW: Static global queryset exposed to every user and tenant!
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]
```

### The Failure Mechanism
1. DRF's `get_object()` starts with `self.filter_queryset(self.get_queryset())`.
2. When `queryset = Invoice.objects.all()` is set statically at the class level, any authenticated user can view, edit, or delete any invoice in the entire system by simply guessing or enumerating the primary key:
   `GET /api/v1/invoices/99201/` or `DELETE /api/v1/invoices/99201/`.
3. In a multi-tenant B2B SaaS platform, this leaks proprietary financial records across competing enterprise tenants, violating GDPR, HIPAA, and SOC2 compliance.

### Production Remediation
Always override `get_queryset()` to scope records strictly to the authenticated user's organization or tenant:

```python
# views.py - RESOLVED
class CustomerInvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Dynamically scopes queryset to the authenticated user's tenant.
        Guarantees that get_object() automatically returns 404 for cross-tenant IDs.
        """
        user = self.request.user
        if not user.is_authenticated:
            return Invoice.objects.none()

        if user.is_superuser:
            return Invoice.objects.all()

        return Invoice.objects.filter(tenant_id=user.tenant_id)
```

---

## Antipattern 2: Forgetting `detail=True` on Instance Actions (Calling `get_object()` in Collection Route)

### Severity: HIGH (Runtime Crash: `AssertionError` / `AttributeError`)

### The Flawed Pattern
```python
# views.py
class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

    # FLAW: Missing detail=True! Defaults to detail=False (collection-level)
    @action(methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        # Crash: self.get_object() fails because pk is not in URL kwargs!
        document = self.get_object()
        document.is_archived = True
        document.save()
        return Response({'status': 'archived'})
```

### The Failure Mechanism
1. `@action` defaults to `detail=False`.
2. When registered with a Router, the generated URL route is `/api/v1/documents/archive/` instead of `/api/v1/documents/{pk}/archive/`.
3. When `self.get_object()` executes, DRF attempts to look up `self.kwargs['pk']`, which does not exist in the collection route regex, raising `KeyError: 'pk'` or `AssertionError: Expected a primary key`.

### Production Remediation
Explicitly set `detail=True` when decorating actions that operate on a specific instance:

```python
# views.py - RESOLVED
class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        # Correct: Generates /api/v1/documents/{pk}/archive/
        document = self.get_object()
        document.is_archived = True
        document.save(update_fields=['is_archived'])
        return Response({'status': 'archived'})
```

---

## Antipattern 3: Returning Un-Instantiated Permission Classes in `get_permissions()`

### Severity: HIGH (Runtime Crash: `TypeError: 'type' object is not callable`)

### The Flawed Pattern
```python
# views.py
class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer

    def get_permissions(self):
        # FLAW: Returning list of classes instead of instances!
        if self.action == 'destroy':
            return [IsAdminUser]
        return [IsAuthenticated]
```

### The Failure Mechanism
1. Unlike `permission_classes = [IsAuthenticated]`, where DRF instantiates the classes internally, `get_permissions()` has an explicit contract: **it must return a list of initialized permission class instances**.
2. DRF's `check_permissions(request)` iterates over the list returned by `get_permissions()` and invokes `permission.has_permission(request, self)`.
3. If un-instantiated classes are returned, calling `IsAdminUser.has_permission(request, self)` fails with `TypeError: has_permission() missing 1 required positional argument: 'view'`.

### Production Remediation
Always instantiate the permission classes in the returned list comprehension:

```python
# views.py - RESOLVED
class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer

    def get_permissions(self):
        if self.action == 'destroy':
            permission_classes = [IsAdminUser]
        else:
            permission_classes = [IsAuthenticated]

        # Explicitly instantiate each permission class:
        return [permission() for permission in permission_classes]
```

---

## Antipattern 4: Conflict Between Router `trailing_slash=False` and Django `APPEND_SLASH=True`

### Severity: HIGH (POST/PUT Body Stripping & HTTP 301 Redirect Loops)

### The Flawed Pattern
```python
# urls.py
router = DefaultRouter(trailing_slash=False) # Strip trailing slash on API
router.register(r'users', UserViewSet, basename='user')

# settings.py
APPEND_SLASH = True # Default Django setting!
```

### The Failure Mechanism
1. A client submits a `POST` request to `/api/v1/users` with a JSON payload.
2. Django's `CommonMiddleware` checks `APPEND_SLASH = True`. Because `/api/v1/users` doesn't match a slash-terminated URL in standard regexes or conflicts with URL prefixes, `CommonMiddleware` issues an `HTTP 301 Moved Permanently` redirect to `/api/v1/users/`.
3. Standard HTTP clients (cURL, Axios, fetch) follow the 301 redirect by converting the request to a `GET` and dropping the `POST` request body!
4. The client receives an unexpected 405 Method Not Allowed or 400 Bad Request with an empty body.

### Production Remediation
Either keep `trailing_slash=True` on your routers (standard DRF convention) or explicitly disable `APPEND_SLASH` in your settings:

```python
# settings/base.py - RESOLVED
APPEND_SLASH = False

# urls.py - RESOLVED
router = DefaultRouter(trailing_slash=False)
router.register(r'users', UserViewSet, basename='user')
```

---

## Antipattern 5: Executing Persistence and Side Effects in `create()` Instead of `perform_create()`

### Severity: MEDIUM (Violating Layer Separation & Breaking DRF Extensibility)

### The Flawed Pattern
```python
# views.py
class OrderViewSet(viewsets.ModelViewSet):
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # FLAW: Overriding entire create() boilerplate just to inject created_by!
        order = serializer.save(created_by=request.user)
        send_order_email(order.id)
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
```

### The Failure Mechanism
1. Re-implementing `create()` duplicates 10 lines of standard DRF boilerplate (header construction, response serialization, status codes).
2. If mixins, content negotiation, or logging interceptors are attached to the ViewSet, overriding the entire `create()` method bypasses downstream hooks.

### Production Remediation
Use DRF's dedicated `perform_create(serializer)` hook, which exists specifically to handle pre-save attribute injection and post-save domain events:

```python
# views.py - RESOLVED
class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer

    def perform_create(self, serializer):
        # Clean, decoupled persistence hook:
        order = serializer.save(
            created_by=self.request.user,
            tenant_id=self.request.user.tenant_id,
        )
        send_order_email.delay(order.id)
```

---

## Antipattern 6: Storing Request-Scoped State on `self` Inside Custom `@action`

### Severity: HIGH (Thread-Safety Race Conditions Across Multi-Threaded Workers)

### The Flawed Pattern
```python
# views.py
class ReportViewSet(viewsets.ModelViewSet):
    # FLAW: Mutating class-level state or expecting self attributes to persist!
    export_filters = {}

    @action(detail=False, methods=['post'], url_path='export')
    def export(self, request):
        ReportViewSet.export_filters = request.data.get('filters')
        # ... async job starts
        return Response({'status': 'queued'})
```

### The Failure Mechanism
1. In multi-threaded WSGI/ASGI workers (e.g. Gunicorn `gthread`), class attributes are shared across all worker threads.
2. When User A writes to `ReportViewSet.export_filters`, User B concurrently executing a report in another thread has their filters overwritten, generating report data from another organization.

### Production Remediation
Always isolate state to the current request stack frame (local function variables) or pass parameters explicitly into service functions or Celery tasks:

```python
# views.py - RESOLVED
class ReportViewSet(viewsets.ModelViewSet):
    @action(detail=False, methods=['post'], url_path='export')
    def export(self, request):
        filters = request.data.get('filters', {})
        # Pass filters explicitly to asynchronous worker
        generate_report_task.delay(
            user_id=request.user.id,
            tenant_id=request.user.tenant_id,
            filters=filters,
        )
        return Response({'status': 'queued'})
```

---

## Antipattern 7: Bypassing `get_queryset()` Filtering in Custom `@action` Lookups

### Severity: HIGH (Security Vulnerability: Object Injection)

### The Flawed Pattern
```python
# views.py
class DocumentViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        # Correctly scopes to user's tenant
        return Document.objects.filter(tenant_id=self.request.user.tenant_id)

    @action(detail=False, methods=['post'], url_path='sign')
    def sign_document(self, request):
        doc_id = request.data.get('document_id')
        
        # FLAW: Querying Document.objects directly, completely bypassing get_queryset()!
        document = get_object_or_404(Document, id=doc_id)
        document.sign(request.user)
        return Response({'status': 'signed'})
```

### The Failure Mechanism
1. Even though `get_queryset()` has rock-solid multi-tenant scoping, the developer queried the raw model manager directly: `Document.objects.get(id=doc_id)`.
2. An attacker passes `document_id=999` belonging to a rival company, successfully signing or altering documents outside their tenant scope.

### Production Remediation
Always query against `self.get_queryset()` when retrieving objects in collection actions:

```python
# views.py - RESOLVED
class DocumentViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return Document.objects.filter(tenant_id=self.request.user.tenant_id)

    @action(detail=False, methods=['post'], url_path='sign')
    def sign_document(self, request):
        doc_id = request.data.get('document_id')
        
        # Safe: Scoped strictly through self.get_queryset()!
        document = get_object_or_404(self.get_queryset(), id=doc_id)
        document.sign(request.user)
        return Response({'status': 'signed'})
```
