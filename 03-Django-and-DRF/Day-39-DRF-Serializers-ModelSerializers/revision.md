# 5-Minute Rapid Revision: DRF Serializers & ModelSerializers

A high-yield, quick-reference cheat sheet for senior backend engineers reviewing Django REST Framework (DRF) serializers, validation lifecycles, and nested reconciliation.

---

## 1. The 4-Stage Validation Execution Flow

```
CALL: serializer.is_valid(raise_exception=True)
   │
   ├─► 1. to_internal_value(data)        [Type coercion: string to int, ISO to date]
   │
   ├─► 2. Field Validators               [Runs MinValueValidator, UniqueValidator, Regex]
   │
   ├─► 3. validate_<field_name>(value)   [Single-field custom cleaning; returns value]
   │
   └─► 4. validate(attrs)                [Cross-field object rules; runs ONLY if 1-3 pass]
```

- **Field Level (`validate_<field>`)**: Clean or validate single fields in isolation (e.g. normalize email, sanitize slug).
- **Object Level (`validate`)**: Compare multiple fields (e.g. `start_date < end_date`, `discount <= subtotal`).

---

## 2. Data Accessors Cheat Sheet

| Accessor | Available When | Contents | Mutability / Caveat |
| :--- | :--- | :--- | :--- |
| **`serializer.initial_data`** | Immediately after `__init__` | Raw untrusted input dictionary | Do NOT trust for business logic! |
| **`serializer.validated_data`** | ONLY after `is_valid()` passes | Clean, typed Python dictionary | Accessing early raises `AssertionError`. |
| **`serializer.data`** | After `is_valid()` or on read instance | Outgoing JSON-ready `ReturnDict` | Formatted via `to_representation()`. |
| **`serializer.errors`** | ONLY after `is_valid()` fails | Dict of field-level error lists | Accessing early raises `AssertionError`. |

---

## 3. `Serializer` vs `ModelSerializer`

```
CAPABILITY                 Serializer                     ModelSerializer
─────────────────────────────────────────────────────────────────────────────────────────────
ORM Coupling               None (Decoupled)               Tightly bound to Django Model
Field Declarations         Manual (Explicit)              Auto-generated via introspections
Default Validators         None                           Auto (UniqueTogether, FK, Regex)
CRUD Methods               Must write create() & update() Built-in default create() & update()
Best For                   RPC, Analytical Aggregates,    Standard REST resources directly
                           Auth Tokens, External APIs     mirroring database tables
─────────────────────────────────────────────────────────────────────────────────────────────
```

---

## 4. Writable Nested Serializer Reconciliation Template

```python
from django.db import transaction
from rest_framework import serializers

class WritableParentSerializer(serializers.ModelSerializer):
    children = ChildSerializer(many=True)

    class Meta:
        model = Parent
        fields = ['id', 'title', 'children']

    def update(self, instance, validated_data):
        children_data = validated_data.pop('children', None)

        with transaction.atomic():
            instance = super().update(instance, validated_data)

            if children_data is not None:
                existing_children = {c.id: c for c in instance.children.all()}
                incoming_ids = set()

                to_create, to_update = [], []
                for item in children_data:
                    c_id = item.get('id')
                    if c_id and c_id in existing_children:
                        incoming_ids.add(c_id)
                        child = existing_children[c_id]
                        for attr, val in item.items():
                            if attr != 'id': setattr(child, attr, val)
                        to_update.append(child)
                    else:
                        item.pop('id', None)
                        to_create.append(Child(parent=instance, **item))

                # Delete omitted children
                to_delete = set(existing_children.keys()) - incoming_ids
                if to_delete:
                    instance.children.filter(id__in=to_delete).delete()

                # Bulk execution
                if to_create: Child.objects.bulk_create(to_create)
                if to_update: Child.objects.bulk_update(to_update, fields=['name', 'qty'])

        return instance
```

---

## 5. Dynamic Field Selection Boilerplate

```python
class DynamicFieldsSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        fields = kwargs.pop('fields', None)
        exclude = kwargs.pop('exclude', None)
        super().__init__(*args, **kwargs)

        if fields is not None:
            allowed = set(fields)
            for f in set(self.fields.keys()) - allowed:
                self.fields.pop(f, None)

        if exclude is not None:
            for f in exclude:
                self.fields.pop(f, None)
```

---

## 6. Eliminating $N+1$ in `SerializerMethodField`

- ❌ **Antipattern**: Running `obj.reviews.count()` inside `get_reviews_count(self, obj)` fires 100 queries for 100 items!
- ✅ **ORM Annotation**: Pre-calculate in View QuerySet via `annotate(cnt=models.Count('reviews'))` and bind via `serializers.IntegerField(source='cnt', read_only=True)`.
- ✅ **Batch Context**: Fetch once in bulk (e.g. Redis `mget`) and pass into `context={'cache': data}`.

---

## 7. Production Security & Architecture Checklist

- [ ] **Are password fields marked `write_only=True`?** (Prevents leaking plaintext hashes in GET responses).
- [ ] **Are system IDs and timestamps marked `read_only=True`?** (Prevents client tampering).
- [ ] **Are sensitive attributes (user, tenant) injected via `save(**kwargs)`?** (Never trust client-supplied owner IDs).
- [ ] **Does custom `__init__` pass `*args` and `**kwargs` to `super().__init__()`?**
- [ ] **Are nested updates wrapped in `transaction.atomic()`?**
- [ ] **Is `raise_exception=True` passed to `is_valid()` in API views?**
- [ ] **Are list serialization views utilizing `only()` or `values()` for high-volume exports?**
