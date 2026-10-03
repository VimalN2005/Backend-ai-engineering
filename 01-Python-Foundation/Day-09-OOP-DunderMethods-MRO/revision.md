# Day 09: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before technical interviews and architectural design reviews:

---

- ⚡ **Instantiation Lifecycle:**
  - `__new__(cls)`: Allocates raw memory. Must return an instance. Use for Singletons, flyweights, and immutable subclasses.
  - `__init__(self)`: Configures attributes on existing instance. Must return `None`.

- ⚡ **Memory Optimization via `__slots__`:**
  - Eliminates the default `__dict__` (~152+ bytes per instance).
  - Uses static C array descriptors inside `PyObject`, reducing memory by ~60%–70%.
  - Subclasses must explicitly declare `__slots__ = ()` to prevent reintroducing `__dict__`.

- ⚡ **Representation Contract:**
  - `__repr__`: Unambiguous developer representation (`eval(repr(obj)) == obj`). Primary fallback.
  - `__str__`: User-facing pretty representation.

- ⚡ **Equality & Hashing Invariant:**
  - Mathematical rule: If `a == b` $\implies$ `hash(a) == hash(b)`.
  - Overriding `__eq__` sets `__hash__ = None` automatically.
  - Always base `__hash__` on **immutable** attributes only.

- ⚡ **Operator Overloading:**
  - Always return `NotImplemented` (not `TypeError` or `False`) for unsupported types to allow Python to attempt reverse reflection methods (`__radd__`, `__rmatmul__`).
  - `@` is overloaded via `__matmul__` (standard for dot products & matrix operations).

- ⚡ **Descriptor Protocol:**
  - Data Descriptors (`__set__` / `__delete__`) override instance `__dict__`.
  - Non-data Descriptors (`__get__` only) do not override instance `__dict__`.
  - Powers `@property`, `@classmethod`, `@staticmethod`, and ORM fields (Django/SQLAlchemy).

- ⚡ **C3 Linearization & MRO:**
  - Computes monotonic method resolution order: Subclasses before parents, left-to-right declaration order preserved.
  - Inspect via `Class.__mro__`.

- ⚡ **Cooperative Multiple Inheritance:**
  - Never call `ParentClass.__init__(self)` in multiple inheritance hierarchies.
  - Always use `super().__init__(*args, **kwargs)` so calls cooperatively advance through the runtime MRO without duplicates or skipped mixins.
