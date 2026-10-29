# 5-Minute Rapid Revision: DRF ViewSets, Routers & Actions

A high-yield, quick-reference cheat sheet for senior backend engineers reviewing Django REST Framework (DRF) ViewSets, Routers, dynamic lifecycle hooks, and custom action endpoints.

---

## 1. The 6 Standard Action Verbs

```
ACTION VERB        HTTP METHOD     URL PATTERN             METHOD SIGNATURE
─────────────────────────────────────────────────────────────────────────────────────────────
list               GET             /{prefix}/              def list(self, request)
create             POST            /{prefix}/              def create(self, request)
retrieve           GET             /{prefix}/{pk}/         def retrieve(self, request, pk=None)
update             PUT             /{prefix}/{pk}/         def update(self, request, pk=None)
partial_update     PATCH           /{prefix}/{pk}/         def partial_update(self, request, pk=None)
destroy            DELETE          /{prefix}/{pk}/         def destroy(self, request, pk=None)
─────────────────────────────────────────────────────────────────────────────────────────────
```

---

## 2. Router Selection: `SimpleRouter` vs `DefaultRouter`

| Capability | `SimpleRouter` | `DefaultRouter` |
| :--- | :--- | :--- |
| **List & Detail URLs** | Supported | Supported |
| **API Root Endpoint (`/`)** | ❌ None (Returns 404 on `/`) | ✅ Auto-generates hyperlinked API root view |
| **Format Suffix Patterns** | Optional | ✅ Automatically appends `.{format}` regexes |
| **Best For** | Internal microservices, clean REST | Public APIs, browsable API interfaces |

```python
# Disabling trailing slash on routers:
router = DefaultRouter(trailing_slash=False)
# NOTE: Ensure settings.APPEND_SLASH = False to prevent 301 redirects!
```

---

## 3. `@action` Decorator Cheat Sheet

```python
from rest_framework.decorators import action

# 1. Instance-Level Action (Operates on single resource)
# Route: POST /{prefix}/{pk}/cancel/
@action(detail=True, methods=['post'], url_path='cancel')
def cancel(self, request, pk=None):
    obj = self.get_object() # Enforces get_queryset() & object permissions!
    # ...

# 2. Collection-Level Action (Operates on entire collection)
# Route: GET /{prefix}/export-csv/
@action(detail=False, methods=['get'], url_path='export-csv', pagination_class=None)
def export_csv(self, request):
    qs = self.get_queryset()
    # ...
```

---

## 4. The 4 Essential Dynamic Lifecycle Hooks

1. **`get_queryset(self)`**:
   - MUST override for multi-tenant scoping and IDOR protection:
     `return Model.objects.filter(tenant_id=self.request.user.tenant_id)`.
2. **`get_serializer_class(self)`**:
   - Dynamically selects serializer by action:
     `return ListSerializer if self.action == 'list' else DetailSerializer`.
3. **`get_permissions(self)`**:
   - Returns **instantiated** permission objects:
     `return [IsAdminUser()] if self.action == 'destroy' else [IsAuthenticated()]`.
4. **`get_object(self)`**:
   - Queries `self.get_queryset()` using `lookup_field` (defaults to `'pk'`) and invokes `check_object_permissions(request, obj)`.

---

## 5. The 3 Persistence Hooks

Instead of overriding `create()` or `destroy()` completely, use DRF's dedicated persistence hooks:

```python
def perform_create(self, serializer):
    # Injects tenant and user context before saving
    serializer.save(created_by=self.request.user, tenant_id=self.request.user.tenant_id)

def perform_update(self, serializer):
    serializer.save(updated_by=self.request.user)

def perform_destroy(self, instance):
    # Implements soft-delete
    instance.is_deleted = True
    instance.deleted_at = timezone.now()
    instance.save(update_fields=['is_deleted', 'deleted_at'])
```

---

## 6. Enterprise Soft-Delete ModelViewSet Boilerplate

```python
class SoftDeleteModelViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        # 1. Multi-tenant isolation + 2. Filter out soft-deleted records
        return Document.objects.filter(
            tenant_id=self.request.user.tenant_id,
            is_deleted=False,
        )

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.save(update_fields=['is_deleted', 'deleted_at'])

    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        # Look up across ALL records in tenant INCLUDING soft-deleted
        all_tenant_docs = Document.objects.filter(tenant_id=request.user.tenant_id)
        instance = get_object_or_404(all_tenant_docs, pk=pk)
        instance.is_deleted = False
        instance.deleted_at = None
        instance.save(update_fields=['is_deleted', 'deleted_at'])
        return Response({'status': 'restored', 'id': instance.id})
```

---

## 7. Production Security & Architecture Checklist

- [ ] **Did you override `get_queryset()`?** (Never expose class-level `queryset = Model.objects.all()` in multi-tenant backends).
- [ ] **Does `get_permissions()` return instantiated objects?** (`[p() for p in classes]` instead of `[classes]`).
- [ ] **Are instance actions marked `detail=True`?** (Avoids `AssertionError: Expected a primary key`).
- [ ] **Is `APPEND_SLASH = False` set if using `trailing_slash=False` on Routers?** (Prevents 301 POST body stripping).
- [ ] **Does your soft-delete model use conditional unique constraints?** (`UniqueConstraint(condition=Q(is_deleted=False))`).
- [ ] **Are custom action object lookups routed through `self.get_object()` or `self.get_queryset()`?** (Prevents cross-tenant object injection).
