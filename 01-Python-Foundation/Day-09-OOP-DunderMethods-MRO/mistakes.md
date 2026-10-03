# Day 09: Common OOP & Dunder Method Antipatterns

Avoid these 7 production bugs when architecting backend systems and AI pipelines with Python classes.

---

### 1. Overriding `__eq__` Without `__hash__` (Broken Hash Tables)
- **Root Cause**: Overriding `__eq__` signals to Python that equality is based on values rather than object identity. By default, CPython sets `__hash__ = None` to prevent hashing mutations.
- **Consequence**: Instances become unhashable. Attempting to store them in a `set` or use them as a `dict` key raises `TypeError: unhashable type: 'MyClass'`.

```python
# ❌ FATAL ANTIPATTERN: Makes instances unhashable
class Vector:
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

    def __eq__(self, other):
        return (self.x, self.y) == (other.x, other.y)

# vector_set = {Vector(1, 2)} -> Raises TypeError: unhashable type: 'Vector'!

# ✅ PRODUCTION PATTERN: Implement __hash__ alongside __eq__ (using immutable state)
class Vector:
    def __init__(self, x: float, y: float):
        self._x = x
        self._y = y

    def __eq__(self, other):
        if not isinstance(other, Vector):
            return NotImplemented
        return (self._x, self._y) == (other._x, other._y)

    def __hash__(self):
        return hash((self._x, self._y))
```

---

### 2. Hardcoding Direct Parent Invocations Instead of `super()`
- **Root Cause**: Invoking base class methods with explicit class names like `Parent.__init__(self)`.
- **Consequence**: In multiple inheritance or diamond hierarchies, explicit calls bypass the C3 Method Resolution Order (MRO). Base classes either execute multiple times (re-initializing state) or mixins get completely skipped.

```python
# ❌ FATAL ANTIPATTERN: Hardcoded parent call breaks MRO
class CustomLLMGateway(TelemetryMixin, BaseGateway):
    def __init__(self, api_key: str):
        BaseGateway.__init__(self, api_key)  # TelemetryMixin.__init__ is completely skipped!

# ✅ PRODUCTION PATTERN: Cooperative super() with forwarded kwargs
class CustomLLMGateway(TelemetryMixin, BaseGateway):
    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key=api_key, **kwargs)  # Cleanly calls next class in MRO
```

---

### 3. Mutable Class Attributes (The Accidental Shared State Trap)
- **Root Cause**: Defining mutable containers (`list`, `dict`, `set`) at the class level instead of inside `__init__`.
- **Consequence**: The container is shared across every single instance of the class. Modifying it on one instance silently mutates it for all tenants or users.

```python
# ❌ FATAL ANTIPATTERN: Shared across all instances!
class SessionManager:
    active_tokens: list = []  # Shared class-level list!

# session_1.active_tokens.append("token_a")
# print(session_2.active_tokens) -> ['token_a'] (Data leak across sessions!)

# ✅ PRODUCTION PATTERN: Instance attributes isolated in __init__
class SessionManager:
    def __init__(self):
        self.active_tokens: list = []
```

---

### 4. The Re-initialization Trap with `__new__` Singletons
- **Root Cause**: Implementing a singleton or flyweight cache in `__new__`, but failing to protect `__init__`.
- **Consequence**: When `MySingleton()` is called, CPython *always* calls `__init__` on the object returned by `__new__`, overwriting existing state.

```python
# ❌ BUGGY SINGLETON: __init__ runs every single time!
class DatabasePool:
    _instance = None
    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self.connections = []  # Wipes out existing connections on every DatabasePool() call!

# ✅ PRODUCTION PATTERN: Guard __init__ against re-entry
class DatabasePool:
    _instance = None
    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self.connections = []
        self._initialized = True
```

---

### 5. Omitting `__repr__` on Domain Models
- **Root Cause**: Forgetting to define `__repr__` or only defining `__str__`.
- **Consequence**: Logs, Sentry stack traces, and debuggers print unhelpful strings like `<Document object at 0x7fa2810a90>`. Developers cannot inspect the state of objects during post-mortem debugging.

```python
# ❌ POOR DEBUGGABILITY
class Document:
    def __init__(self, doc_id: str, title: str):
        self.doc_id = doc_id
        self.title = title

# ✅ PRODUCTION PATTERN: Informative, unambiguous representation
class Document:
    def __init__(self, doc_id: str, title: str):
        self.doc_id = doc_id
        self.title = title

    def __repr__(self) -> str:
        return f"Document(doc_id={self.doc_id!r}, title={self.title!r})"
```

---

### 6. Raising `TypeError` Instead of Returning `NotImplemented`
- **Root Cause**: When implementing comparison or arithmetic dunders (`__eq__`, `__matmul__`, `__add__`), raising `TypeError` directly when the operand type is unrecognized.
- **Consequence**: Prevents Python from falling back to the right-hand reverse method (e.g., `__radd__`, `__rmatmul__`) on the other operand, breaking duck typing and polymorphic libraries like NumPy and PyTorch.

```python
# ❌ ANTIPATTERN: Aborts before checking right-hand operand
def __eq__(self, other):
    if not isinstance(other, DenseVector):
        raise TypeError("Cannot compare DenseVector with other type")

# ✅ PRODUCTION PATTERN: Return NotImplemented to permit fallback
def __eq__(self, other):
    if not isinstance(other, DenseVector):
        return NotImplemented
    return self._values == other._values
```

---

### 7. Losing `__slots__` Optimization via Inheritance
- **Root Cause**: Defining `__slots__` on a parent class, but inheriting from it without defining `__slots__` on the child class.
- **Consequence**: Subclasses automatically create an instance `__dict__`, completely negating the memory savings of the parent.

```python
# ❌ LOST OPTIMIZATION: Child reintroduces __dict__!
class BasePoint:
    __slots__ = ("x", "y")

class ColorPoint(BasePoint):
    pass  # Has __dict__! Base slots did not protect this child.

# ✅ PRODUCTION PATTERN: Declare empty slots or child slots
class ColorPoint(BasePoint):
    __slots__ = ("color",)  # Or __slots__ = () if no new attributes
```
