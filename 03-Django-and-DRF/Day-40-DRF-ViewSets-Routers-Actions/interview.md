# Senior Backend Engineering Interview: DRF ViewSets, Routers & Actions

This guide compiles the top 5 high-frequency architectural interview questions targeting Senior and Staff Backend Engineers working with Django REST Framework (DRF) ViewSets, custom action routing, and multi-tenant access control.

---

### Question 1: How does DRF's `as_view({'http_method': 'action'})` binding engine work under the hood? Why don't ViewSets define `get()` or `post()` methods, and how is thread safety preserved?

#### Executive Summary
Standard Django views and `APIView` bind directly to HTTP method handlers (`get()`, `post()`). ViewSets decouple HTTP verbs from business operations by replacing them with action verbs (`list`, `create`, `retrieve`, `update`, `destroy`). The `as_view(actions={...})` classmethod is a factory closure that instantiates a **fresh ViewSet instance per incoming HTTP request**, maps the incoming HTTP method to the configured action verb, sets `self.action`, and dispatches execution to that action method.

#### Deep Architectural Breakdown

```
Incoming Request: GET /api/v1/orders/
         │
         ▼
[Router compiles]: UserViewSet.as_view({'get': 'list', 'post': 'create'})
         │
         ▼
[Factory Closure]:
  1. Instantiates fresh instance: self = cls(**initkwargs)  <-- Thread Safety Guaranteed!
  2. Resolves action: action_name = actions.get(request.method.lower()) -> 'list'
  3. Binds request metadata:
     self.action = 'list'
     self.request = request
     self.args, self.kwargs = args, kwargs
  4. Invokes target action: getattr(self, 'list')(request, *args, **kwargs)
         │
         ▼
[Action Execution]: def list(self, request): -> Returns Response
```

#### Why ViewSets Lack `get()` / `post()` Handlers
If a class defined `def get(self, request):`, that single method would have to handle both:
- `GET /orders/` (listing paginated collections)
- `GET /orders/{pk}/` (retrieving a single instance)
- `GET /orders/{pk}/export-pdf/` (custom action)

By delegating HTTP verb binding to `as_view(actions={...})`, DRF allows the same ViewSet class to be mounted onto completely different URL patterns with different action mappings:
```python
order_list = OrderViewSet.as_view({'get': 'list', 'post': 'create'})
order_detail = OrderViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'})
```

#### What Interviewers Look For
- Knowledge that `as_view()` returns a closure, guaranteeing a new instance is created per request (preventing race conditions in multi-threaded Gunicorn workers).
- Understanding that `self.action` is set at the start of request handling and can be used in `get_serializer_class()` and `get_permissions()`.
- Recognizing that unsupported methods return HTTP 405 Method Not Allowed automatically.

---

### Question 2: Why is relying on a static class-level `queryset = Model.objects.all()` considered a catastrophic security antipattern in multi-tenant SaaS? How does overriding `get_queryset()` protect both list views and `get_object()` lookups from IDOR?

#### Executive Summary
Static class-level querysets (`queryset = Model.objects.all()`) define a global, unfiltered database query evaluated without request context. In multi-tenant systems, this introduces critical **Insecure Direct Object Reference (IDOR)** vulnerabilities where any authenticated user can view, edit, or delete another organization's records by guessing the primary key. Overriding `get_queryset()` guarantees that **all** operations—including `get_object()` detail lookups and custom `@action` methods—are strictly scoped to the authenticated user's tenant.

#### Deep Architectural Breakdown

#### How `get_object()` Relies on `get_queryset()`
In `rest_framework.generics.GenericAPIView`:
```python
def get_object(self):
    # Notice: get_object() starts by calling get_queryset()!
    queryset = self.filter_queryset(self.get_queryset())

    lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
    filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}

    # Look up instance ONLY within the scoped queryset
    obj = get_object_or_404(queryset, **filter_kwargs)
    self.check_object_permissions(self.request, obj)
    return obj
```

#### IDOR Attack Scenario:
If `queryset = Invoice.objects.all()` is used:
1. Attacker (Tenant B) sends `GET /api/v1/invoices/1092/` (an invoice belonging to Tenant A).
2. `get_object()` executes: `Invoice.objects.all().get(pk=1092)`.
3. The invoice is found and returned! The tenant boundary is breached.

#### Production Remediation:
```python
class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer

    def get_queryset(self):
        """
        Dynamically scoped to user's tenant.
        Prevents IDOR: Tenant B querying /invoices/1092/ yields an empty
        queryset, causing get_object_or_404 to return HTTP 404 Not Found!
        """
        user = self.request.user
        if not user.is_authenticated:
            return Invoice.objects.none()

        if user.is_superuser:
            return Invoice.objects.all()

        return Invoice.objects.filter(tenant_id=user.tenant_id)
```

#### What Interviewers Look For
- Explaining that `get_object()` calls `get_queryset()` internally, so tenant filtering in `get_queryset()` automatically protects detail endpoints.
- Highlighting that cross-tenant access returns HTTP 404 (Not Found) rather than 403 (Forbidden), preventing resource existence enumeration.
- Awareness that static class-level querysets can cause premature query evaluation at server boot time if accessed improperly.

---

### Question 3: Walk through the internal lifecycle of `get_object()`. How does it resolve `lookup_field` vs `lookup_url_kwarg`, apply `filter_queryset()`, and enforce object-level permissions?

#### Executive Summary
`get_object()` is the central gateway for all single-instance operations in DRF. Its lifecycle consists of four steps: (1) retrieving the base QuerySet from `get_queryset()`, (2) passing it through `filter_queryset()` to apply search and filter backends, (3) querying the database using `lookup_field` and `lookup_url_kwarg` with `get_object_or_404`, and (4) executing `check_object_permissions()` across all registered permission classes.

#### Detailed Lifecycle Flow

```
1. Base QuerySet Acquisition:
   qs = self.get_queryset()

2. Filtering & Search Integration:
   qs = self.filter_queryset(qs)  # Runs DjangoFilterBackend, SearchFilter, OrderingFilter

3. URL Parameter Resolution:
   lookup_kwarg = self.lookup_url_kwarg or self.lookup_field  # e.g., 'pk' or 'slug'
   lookup_val = self.kwargs[lookup_kwarg]
   filter_dict = {self.lookup_field: lookup_val}

4. Database Lookup:
   obj = get_object_or_404(qs, **filter_dict)

5. Object-Level Authorization:
   self.check_object_permissions(self.request, obj)
   └─► Loops through self.get_permissions():
         if not permission.has_object_permission(self.request, self, obj):
             raise PermissionDenied
```

#### Key Architectural Knobs:
- **`lookup_field`**: The model attribute to filter against (e.g. `lookup_field = 'slug'` or `lookup_field = 'uuid'`).
- **`lookup_url_kwarg`**: The URL regex capture group name if it differs from the model field (e.g. `path('articles/<str:article_slug>/', ...)` -> `lookup_url_kwarg = 'article_slug'`, `lookup_field = 'slug'`).
- **`check_object_permissions()`**: Unlike table-level permissions checked at the start of a request, object permissions are evaluated **only after the instance has been retrieved from the database**.

#### What Interviewers Look For
- Articulating the difference between `has_permission()` (view-level) and `has_object_permission()` (instance-level).
- Explaining that `has_object_permission()` is never invoked for `list()` or `create()` because no object instance exists yet.
- Pointing out that `get_object()` memoizes or raises 404 before permissions run, preventing permission checks on missing records.

---

### Question 4: Compare `@action(detail=True)` vs `@action(detail=False)`. How do Routers compile their URL regexes, and how do you customize serializers, permissions, and pagination on a custom action?

#### Executive Summary
- **`@action(detail=True)`**: Operates on a single instance. Routers compile the pattern `^{prefix}/(?P<pk>[^/.]+)/{url_path}/$`. It receives `pk` in `kwargs` and calls `self.get_object()`. Used for state transitions (e.g., `/orders/42/cancel/`).
- **`@action(detail=False)`**: Operates on the collection. Routers compile the pattern `^{prefix}/{url_path}/$`. Operates on `self.get_queryset()`. Used for batch operations or reports (e.g., `/orders/export-csv/`).
- **Customization**: Custom actions can override `serializer_class`, `permission_classes`, or `pagination_class` by declaring them in the `@action` decorator arguments or checking `self.action` in `get_serializer_class()`.

#### URL Generation Comparison Matrix

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| ATTRIBUTE                | @action(detail=False)       | @action(detail=True)            |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| Generated Route          | `/{prefix}/{url_path}/`     | `/{prefix}/{pk}/{url_path}/`    |
| Method Signature         | `def action(self, request)` | `def action(self, request, pk)` |
| Instance Available?      | No                          | Yes (via self.get_object())     |
| Default Action Name      | Function name               | Function name                   |
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

#### Production Action Customization Pattern

```python
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserStandardSerializer

    @action(
        detail=True,
        methods=['post'],
        url_path='reset-mfa',
        serializer_class=ResetMfaSerializer,        # Dedicated action serializer
        permission_classes=[IsSecurityAdmin],       # Dedicated action permission
    )
    def reset_mfa(self, request, pk=None):
        user = self.get_object() # Enforces get_queryset() & IsSecurityAdmin object perm
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user.totp_device_set.all().delete()
        return Response({'status': 'MFA devices revoked'})

    def get_serializer_class(self):
        # Fallback or programmatic serializer switching
        if self.action == 'reset_mfa':
            return ResetMfaSerializer
        return super().get_serializer_class()
```

#### What Interviewers Look For
- Highlighting that `self.get_serializer()` automatically respects the `@action(serializer_class=...)` definition.
- Explaining that `@action(detail=True)` endpoints automatically trigger `check_object_permissions()` when `self.get_object()` is called.
- Mentioning that setting `pagination_class = None` on a collection action disables pagination for file exports or bulk downloads.

---

### Question 5: How do you architect an enterprise soft-delete system in a `ModelViewSet` that transparently filters out deleted records on standard reads, prevents hard SQL deletes in `perform_destroy()`, and provides a dedicated `/restore/` endpoint without breaking unique database constraints?

#### Executive Summary
An enterprise soft-delete pattern requires coordination across three layers:
1. **Model Layer**: Add `is_deleted = models.BooleanField(default=False)` and `deleted_at = models.DateTimeField(null=True)`. Replace standard unique constraints with **conditional unique constraints** (`UniqueConstraint(fields=['email'], condition=Q(is_deleted=False))`) so deleted users do not block new registrations with the same email.
2. **ViewSet Read & Delete Layer**: Filter `is_deleted=False` in `get_queryset()`. Override `perform_destroy(instance)` to set `is_deleted=True` and record audit timestamps rather than calling `instance.delete()`.
3. **Restoration Layer**: Implement an `@action(detail=True, methods=['post'], url_path='restore')` endpoint that bypasses the `is_deleted=False` filter to locate and reactivate the soft-deleted instance.

#### Production Architecture Blueprint

```python
# models.py
class Document(models.Model):
    tenant_id = models.CharField(max_length=64)
    slug = models.SlugField()
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        # Conditional Unique Constraint: Enforces uniqueness ONLY among active records!
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_id', 'slug'],
                condition=models.Q(is_deleted=False),
                name='unique_active_document_slug_per_tenant',
            )
        ]

# views.py
class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer

    def get_queryset(self):
        # Filter out soft-deleted records from standard list and retrieve
        return Document.objects.filter(
            tenant_id=self.request.user.tenant_id,
            is_deleted=False,
        )

    def perform_destroy(self, instance):
        # Soft-delete: update status without dropping from PostgreSQL
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.deleted_by = self.request.user
        instance.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])

    @action(detail=True, methods=['post'], url_path='restore', permission_classes=[IsAdminUser])
    def restore(self, request, pk=None):
        """
        POST /api/v1/documents/{pk}/restore/
        Recovers a soft-deleted document.
        """
        # Look up across ALL tenant records INCLUDING soft-deleted
        all_tenant_docs = Document.objects.filter(tenant_id=request.user.tenant_id)
        instance = get_object_or_404(all_tenant_docs, pk=pk)

        if not instance.is_deleted:
            return Response({'status': 'already_active'}, status=status.HTTP_200_OK)

        instance.is_deleted = False
        instance.deleted_at = None
        instance.deleted_by = None
        instance.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])

        return Response({'status': 'restored', 'id': instance.id}, status=status.HTTP_200_OK)
```

#### What Interviewers Look For
- Immediate identification of the unique constraint problem: If a user deletes `slug='financial-report'` and later creates a new document with `slug='financial-report'`, standard database `unique=True` constraints reject it. Mentioning PostgreSQL partial / conditional unique indexes (`models.UniqueConstraint(condition=Q(is_deleted=False))`) proves deep production experience.
- Ensuring `restore` uses a separate unscoped queryset while still respecting tenant isolation (`tenant_id=request.user.tenant_id`).
- Explaining that `perform_destroy()` is the canonical DRF hook for intercepting deletions.
