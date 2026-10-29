# Day 40: DRF ViewSets, ModelViewSets, Routers & Custom Action Endpoints

In traditional web development, managing RESTful resources often results in boilerplate explosion: writing separate view classes for listing, creating, retrieving, updating, and deleting records. Django REST Framework (DRF) solves this through **ViewSets** and **Routers**—an architectural pattern that collapses five or six scattered view classes into a single cohesive controller, automatically wired by declarative routing generators.

Mastering ViewSets requires understanding the internal mechanics of dynamic method dispatch (`as_view({'get': 'list', 'post': 'create'})`), action-specific serializer and permission switching, multi-tenant isolation in `get_queryset()`, and extending standard REST semantics with `@action` decorators.

---

## 1. The View Hierarchy: From `APIView` to `ModelViewSet`

DRF's view architecture is organized as a layered inheritance tree, where each tier introduces higher levels of abstraction:

```
+─────────────────────────────────────────────────────────────────────────────+
|                         DRF VIEW CLASS HIERARCHY                             |
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  1. APIView                                                                 |
|     -> Direct subclass of Django View.                                      |
|     -> Handles content negotiation, authentication, permissions, throttling. |
|     -> Dispatches directly to get(), post(), put(), patch(), delete().       |
|                                                                             |
|  2. GenericAPIView (inherits APIView)                                        |
|     -> Adds queryset and serializer_class attributes.                       |
|     -> Provides lifecycle hooks: get_queryset(), get_serializer(),           |
|        get_object(), filter_queryset().                                     |
|                                                                             |
|  3. GenericAPIView + Mixins                                                  |
|     -> ListModelMixin, CreateModelMixin, RetrieveModelMixin,                |
|        UpdateModelMixin, DestroyModelMixin.                                 |
|                                                                             |
|  4. ViewSet (inherits APIView, ViewSetMixin)                                |
|     -> Replaces HTTP method handlers with action verbs:                      |
|        list(), create(), retrieve(), update(), destroy().                   |
|                                                                             |
|  5. GenericViewSet (inherits GenericAPIView, ViewSetMixin)                  |
|     -> Combines GenericAPIView hooks with ViewSet action routing.           |
|                                                                             |
|  6. ReadOnlyModelViewSet (GenericViewSet + ListModelMixin + RetrieveMixin)  |
|     -> Out-of-the-box read-only endpoints (list, retrieve).                 |
|                                                                             |
|  7. ModelViewSet (GenericViewSet + All 5 Mixins)                            |
|     -> Complete out-of-the-box CRUD controller.                             |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 2. Architectural Mechanics: The `as_view({'http_method': 'action'})` Binding Engine

Unlike standard Django views or `APIView` where the incoming HTTP method (`GET`, `POST`) directly invokes a matching lowercase method on the class (`def get(self, request):`), **ViewSets do not have `get()` or `post()` methods**.

Instead, ViewSets map HTTP methods to action verbs dynamically inside `as_view()`:

```python
# What DRF Routers generate under the hood:
user_list = UserViewSet.as_view({
    'get': 'list',
    'post': 'create'
})

user_detail = UserViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'patch': 'partial_update',
    'delete': 'destroy'
})
```

### The `ViewSetMixin.as_view()` Factory Closure
When `UserViewSet.as_view(actions={'get': 'list'})` is invoked:
1. DRF constructs a closure that takes `(request, *args, **kwargs)`.
2. When an incoming HTTP request hits the closure:
   - A fresh instance of the ViewSet is instantiated.
   - The requested HTTP method (e.g. `GET`) is looked up in the `actions` dictionary: `action = self.action_map.get(request.method.lower())`.
   - The instance's `action` attribute is set to `"list"`.
   - DRF binds the target action method (`self.list`) to handle the request.

---

## 3. The Standard 6 Action Methods

A standard `ModelViewSet` exposes 6 canonical REST action methods:

```
+──────────────────┬──────────────┬────────────────────────┬──────────────────────────────────+
| ACTION VERB      | HTTP METHOD  | URL PATTERN            | PURPOSE                          |
+──────────────────┼──────────────┼────────────────────────┼──────────────────────────────────+
| list             | GET          | /api/v1/orders/        | Return paginated list of items   |
| create           | POST         | /api/v1/orders/        | Validate and insert a new item   |
| retrieve         | GET          | /api/v1/orders/{pk}/   | Return single item by lookup_field|
| update           | PUT          | /api/v1/orders/{pk}/   | Full replacement of an item      |
| partial_update   | PATCH        | /api/v1/orders/{pk}/   | Partial update of selected fields|
| destroy          | DELETE       | /api/v1/orders/{pk}/   | Remove item from database        |
+──────────────────┴──────────────┴────────────────────────┴──────────────────────────────────+
```

---

## 4. Router Architecture: `SimpleRouter` vs `DefaultRouter` Internals

DRF provides two main routers that introspect ViewSets and bind standard URL patterns:

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| FEATURE                  | SimpleRouter                | DefaultRouter                   |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| Base URL Routes          | List & Detail routes        | List & Detail routes            |
| API Root Endpoint        | None (returns 404 on '/')   | Generates hyperlinked Root API  |
|                          |                             | view at `/` listing resources   |
| Format Suffix Routes     | Optional                    | Automatically appends optional  |
|                          |                             | format suffixes: `.{format}`    |
| Best Use Case            | Clean API microservices,    | Public browsable APIs,          |
|                          | nested sub-routers.         | developer portals.              |
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

### Wiring a Router in `urls.py`
```python
from rest_framework.routers import DefaultRouter
from .views import OrderViewSet, CustomerViewSet

router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'customers', CustomerViewSet, basename='customer')

urlpatterns = [
    path('api/v1/', include(router.urls)),
]
```

---

## 5. Custom URL Routing & Trailing Slash Invariants

By default, DRF routers generate URLs with trailing slashes: `/orders/` and `/orders/{pk}/`.
In modern microservices or enterprise API gateway architectures (e.g. conforming to Google Cloud API Design Guide or Stripe API style), trailing slashes are often omitted:

```python
# Disable trailing slashes globally on router
router = DefaultRouter(trailing_slash=False)
router.register(r'orders', OrderViewSet, basename='order')
# Generates: /api/v1/orders and /api/v1/orders/{pk}
```

> [!WARNING]
> **Django APPEND_SLASH Conflict**:
> If you set `trailing_slash=False` on DRF routers, ensure `APPEND_SLASH = False` in `settings.py`. Otherwise, Django's `CommonMiddleware` will intercept requests and issue an unnecessary HTTP 301 redirect appending a trailing slash, stripping request bodies on `POST` and `PUT` calls!

---

## 6. Dynamic QuerySet Scoping: Overriding `get_queryset()` for Tenant Isolation & IDOR Defense

In an enterprise multi-tenant SaaS application, **never** rely on static `queryset = Order.objects.all()` attributes. Hardcoded querysets introduce catastrophic **Insecure Direct Object Reference (IDOR)** vulnerabilities where User A in Tenant A can view or edit User B's orders in Tenant B simply by guessing their primary key (`/api/v1/orders/10293/`).

### The Golden Rule: Always Override `get_queryset()`

```python
class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer

    def get_queryset(self):
        """
        Dynamically scopes QuerySet to the authenticated user's organization/tenant.
        Ensures tenant isolation across list(), retrieve(), update(), and destroy().
        """
        user = self.request.user
        if not user.is_authenticated:
            return Order.objects.none()

        # Superusers bypass tenant filtering
        if user.is_superuser:
            return Order.objects.all()

        # Strict Multi-Tenant Scoping:
        # Prevents IDOR attacks on get_object() automatically!
        return (
            Order.objects.filter(tenant_id=user.tenant_id)
            .select_related("customer")
            .prefetch_related("items")
        )
```

---

## 7. Dynamic Serializer Switching: Overriding `get_serializer_class()` by Action

An enterprise resource often requires different serializers depending on the action being performed:
- `list`: Lightweight summary serializer omitting large nested payloads to conserve bandwidth.
- `retrieve`: Detailed serializer including nested relationships and audit history.
- `create` / `update`: Strict write serializer validating input schemas.

```python
class OrderViewSet(viewsets.ModelViewSet):
    def get_serializer_class(self):
        """
        Dynamically selects serializer class based on self.action.
        """
        if self.action == "list":
            return OrderSummaryListSerializer
        elif self.action in ("create", "update", "partial_update"):
            return OrderWriteSerializer
        elif self.action == "retrieve":
            return OrderDetailedSerializer
        elif self.action == "cancel":
            return OrderCancellationSerializer
        return OrderSerializer
```

---

## 8. Action-Specific Permissions: Overriding `get_permissions()`

Similar to serializers, permission requirements frequently vary by action:
- `list` / `retrieve`: Public or `ReadOnly` access.
- `create` / `update`: Authenticated user.
- `destroy`: Organization Administrator only.

```python
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser


class DocumentViewSet(viewsets.ModelViewSet):
    def get_permissions(self):
        """
        Instantiates and returns the list of permissions required for self.action.
        """
        if self.action in ("list", "retrieve"):
            permission_classes = [AllowAny]
        elif self.action in ("create", "update", "partial_update"):
            permission_classes = [IsAuthenticated]
        elif self.action == "destroy":
            permission_classes = [IsAdminUser]
        else:
            permission_classes = [IsAuthenticated]

        return [permission() for permission in permission_classes]
```

---

## 9. The Object Retrieval Lifecycle: `get_object()` and Object Permissions

When an action operates on an individual resource (`retrieve`, `update`, `partial_update`, `destroy`, or custom `@action(detail=True)`), DRF retrieves the instance via **`self.get_object()`**.

### The Internal Execution Flow of `get_object()`:
```python
def get_object(self):
    # 1. Calls get_queryset() to obtain the base scoped queryset
    queryset = self.filter_queryset(self.get_queryset())

    # 2. Extracts lookup URL kwarg (defaults to 'pk')
    lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
    filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}

    # 3. Executes database query
    obj = get_object_or_404(queryset, **filter_kwargs)

    # 4. Triggers object-level permissions!
    self.check_object_permissions(self.request, obj)

    return obj
```

> [!IMPORTANT]
> **Why `get_queryset()` protects `get_object()`**:
> Because `get_object()` starts with `self.get_queryset()`, any tenant or user filtering defined in `get_queryset()` is automatically enforced during detail lookups! If an attacker tries to access `/orders/999/` where order 999 belongs to another tenant, `get_object_or_404` raises `Http404` immediately.

---

## 10. The Persistence Hooks: `perform_create()`, `perform_update()`, and `perform_destroy()`

DRF decouples serialization validation from persistence. Instead of overriding `create()` or `update()` directly, `ModelViewSet` provides dedicated persistence hooks:

```python
class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer

    def perform_create(self, serializer):
        """
        Hook called right before serializer.save().
        Injects request user and triggers audit events or async Celery tasks.
        """
        project = serializer.save(
            created_by=self.request.user,
            tenant_id=self.request.user.tenant_id,
        )
        # Trigger post-creation domain event / notification
        notify_team_project_created.delay(project.id)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        """
        Hook called to execute deletion.
        Perfect for implementing soft-deletes!
        """
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["is_deleted", "deleted_at"])
```

---

## 11. Custom Action Endpoints: Deep Dive into `@action`

Standard REST verbs (`list`, `create`, `retrieve`, `update`, `destroy`) are insufficient for complex domain logic. Business requirements often dictate operations like canceling an order, exporting a PDF, approving an expense, or resending an invite.

DRF provides the **`@action`** decorator to expose custom endpoints on a ViewSet.

### `detail=True` vs `detail=False`

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| ATTRIBUTE                | detail=True                 | detail=False                    |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| Routing Pattern          | Instance-level:             | Collection-level:               |
|                          | `/orders/{pk}/cancel/`      | `/orders/export-csv/`           |
| Instance Retrieval       | Automatically calls         | Operates on whole collection;   |
|                          | `self.get_object()`         | `self.get_queryset()`           |
| Typical Use Cases        | Transitioning entity state: | Aggregated reports, bulk batch  |
|                          | cancel, approve, verify     | actions, summary dashboards     |
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

### Implementing `@action(detail=True)`
```python
from rest_framework.decorators import action
from rest_framework.response import Response


class OrderViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["post"], url_path="cancel", url_name="cancel")
    def cancel(self, request, pk=None):
        """
        POST /api/v1/orders/{pk}/cancel/
        Transitions order state to CANCELLED.
        """
        order = self.get_object()  # Enforces get_queryset() & check_object_permissions!

        if order.status in ("SHIPPED", "COMPLETED"):
            return Response(
                {"error": f"Cannot cancel an order in '{order.status}' status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = "CANCELLED"
        order.cancelled_by = request.user
        order.save(update_fields=["status", "cancelled_by"])

        return Response(OrderSummarySerializer(order).data, status=status.HTTP_200_OK)
```

---

## 12. Overriding Serializers and Pagination on Custom Actions

An `@action` can declare its own dedicated `serializer_class`, `permission_classes`, or disable pagination:

```python
class CustomerViewSet(viewsets.ModelViewSet):
    @action(
        detail=False,
        methods=["get"],
        url_path="export-summary",
        serializer_class=CustomerExportSerializer,
        pagination_class=None,  # Return full unpaginated report
    )
    def export_summary(self, request):
        """
        GET /api/v1/customers/export-summary/
        """
        queryset = self.get_queryset().filter(is_active=True)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
```

---

## 13. Enterprise Soft-Delete Implementation in `ModelViewSet`

In financial and enterprise SaaS systems, physical database row deletion (`DELETE FROM table WHERE id = 1`) is forbidden for regulatory (SOX, GDPR audit trail) and data recovery reasons.

```python
class SoftDeleteModelViewSet(viewsets.ModelViewSet):
    """
    Abstract ModelViewSet implementing enterprise soft-delete.
    """

    def get_queryset(self):
        # Filter out soft-deleted records from standard queries
        return super().get_queryset().filter(is_deleted=False)

    def perform_destroy(self, instance):
        # Mark record as deleted rather than dropping from DB
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.deleted_by = self.request.user
        instance.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        """
        POST /api/v1/resources/{pk}/restore/
        Recovers a soft-deleted record.
        """
        # Look up across ALL records (including soft-deleted)
        base_qs = super().get_queryset()
        instance = get_object_or_404(base_qs, pk=pk)

        instance.is_deleted = False
        instance.deleted_at = None
        instance.deleted_by = None
        instance.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

        return Response({"status": "restored"}, status=status.HTTP_200_OK)
```

---

## 14. Bulk Operations & Composite RPC Actions via ViewSets

While REST strictly operates on single resources, enterprise APIs frequently require batch operations (e.g. bulk updating 100 items or composite approvals):

```python
class BatchInvoiceViewSet(viewsets.ModelViewSet):
    @action(detail=False, methods=["post"], url_path="bulk-approve")
    def bulk_approve(self, request):
        """
        POST /api/v1/invoices/bulk-approve/
        Payload: {"invoice_ids": [101, 102, 103]}
        """
        serializer = BulkApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ids = serializer.validated_data["invoice_ids"]

        # Scope strictly to current tenant to prevent cross-tenant modifications
        updated_count = self.get_queryset().filter(id__in=ids, status="PENDING").update(
            status="APPROVED", approved_by=request.user
        )

        return Response({
            "message": f"Successfully approved {updated_count} invoices.",
            "updated_count": updated_count,
        })
```

---

## 15. ViewSet Performance Profiling & Preventing Class-Level QuerySet Evaluation

### The Class-Level QuerySet Trap
```python
# DANGEROUS ANTIPATTERN:
class OrderViewSet(viewsets.ModelViewSet):
    # FLAW: If not careful, evaluating querysets at class-level can cause
    # database queries to run during Django server startup or celery boot!
    queryset = Order.objects.filter(is_active=True)
```
- Django evaluates `queryset` lazily, but calling `.all()[:10]` or inspecting it prematurely evaluates the query at boot time before database connection pools are configured.
- More critically, class-level querysets **cannot filter by `request.user` or `request.tenant_id`**.

### Senior Best Practice:
Always define `queryset = Order.objects.none()` or a base un-evaluated `Order.objects.all()` solely for schema generation (drf-spectacular / Swagger), and **always override `get_queryset()`** for live request processing.
