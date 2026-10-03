# Day 09: Object-Oriented Programming, Dunder Methods & Inheritance / MRO

In high-scale backend architectures and production AI engineering, Object-Oriented Programming (OOP) is not merely about defining classes and methods. It is the architectural foundation of ORMs (Django ORM, SQLAlchemy), AI model abstractions (LangChain, LlamaIndex, vLLM gateways), and high-throughput vector processing engines.

---

## 1. CPython Object Model Internals: Type vs. Instance

In Python, **everything is an object**, including integers, functions, and classes themselves:

```
+------------------------------------------------------------------+
|                          `type` Metaclass                        |
|   (The class of all classes; instance of itself: type is type)  |
+------------------------------------------------------------------+
                                 |
                     instantiates / defines
                                 v
+------------------------------------------------------------------+
|                     User Class (e.g., `LLMGateway`)              |
|        Instance of `type`; Holds `__dict__` with methods         |
+------------------------------------------------------------------+
                                 |
                     instantiates / creates
                                 v
+------------------------------------------------------------------+
|                 Instance Object (e.g., `client = LLMGateway()`)  |
|          Holds its own `__dict__` with instance state            |
+------------------------------------------------------------------+
```

- Every class is an instance of the built-in metaclass `type`.
- By default, every instance object maintains its own dynamic dictionary (`instance.__dict__`) to store its attributes, which introduces heap memory overhead (~152+ bytes per object).

---

## 2. Object Instantiation: `__new__` vs. `__init__`

Instantiation is a **two-phase process** in CPython:

```python
instance = ClassName(*args, **kwargs)
# Desugars into:
# 1. instance = ClassName.__new__(ClassName, *args, **kwargs)  <- ALLOCATION
# 2. if isinstance(instance, ClassName):
#        ClassName.__init__(instance, *args, **kwargs)         <- INITIALIZATION
```

### Key Differences:
| Phase | Method | Role | Return Value |
| :--- | :--- | :--- | :--- |
| **Phase 1: Allocation** | `__new__(cls, ...)` | Static constructor. Allocates raw memory for the object in the C heap via `super().__new__(cls)`. | **Must return** a new instance of `cls`. |
| **Phase 2: Initialization** | `__init__(self, ...)` | Instance initializer. Configures state on the already-created instance. | **Must return `None`**. |

### When to Override `__new__`:
1. **Thread-Safe Singletons / Connection Pools**: Controlling object reuse (e.g., Database connection pools, LLM client cache).
2. **Subclassing Immutable Types**: Modifying arguments before instantiation when subclassing `str`, `int`, or `tuple`.
3. **Flyweight / Interning**: Reusing existing instances to save memory on millions of repeated tokens or embeddings.

---

## 3. Memory Optimization via `__slots__`

In production AI systems processing millions of vector embeddings or search hits, the dynamic dictionary `__dict__` consumes excessive RAM:

```python
# Standard class with __dict__ overhead (~152+ bytes per instance)
class StandardVector:
    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z

# Memory-optimized class using __slots__ (~48 bytes per instance: ~70% RAM reduction)
class SlottedVector:
    __slots__ = ("x", "y", "z")
    
    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z
```

### How `__slots__` Works Internally:
- Suppresses the creation of the instance `__dict__` and `__weakref__`.
- CPython allocates a fixed-size C array of attribute descriptors directly in the `PyObject` struct.
- Disallows dynamic arbitrary attribute assignment (`obj.unplanned_attr = 5` raises `AttributeError`).

---

## 4. The Representation Contract: `__repr__` vs. `__str__`

```python
class LLMModelConfig:
    def __init__(self, model_name: str, temperature: float):
        self.model_name = model_name
        self.temperature = temperature

    # Unambiguous representation for developers & debugging logs
    def __repr__(self) -> str:
        return f"LLMModelConfig(model_name={self.model_name!r}, temperature={self.temperature})"

    # Human-readable representation for UI or end-user display
    def __str__(self) -> str:
        return f"{self.model_name} (temp={self.temperature})"
```

### The Golden Rule:
- **`__repr__`** is for developers: should ideally look like valid Python code to recreate the object (`eval(repr(obj)) == obj`).
- If `__str__` is not defined, Python automatically falls back to `__repr__`. Always implement `__repr__` first!

---

## 5. Equality & Hashing Contract: `__eq__` and `__hash__`

The Python hash table protocol (used by `dict` keys and `set` elements) relies on a strict mathematical invariant:

$$\text{If } a == b \implies \operatorname{hash}(a) == \operatorname{hash}(b)$$

### The Hashability Invariant:
1. If you override `__eq__`, CPython automatically sets `__hash__ = None`, making instances **unhashable** (cannot be stored in `set` or used as `dict` keys).
2. To restore hashability, you must explicitly implement `__hash__`.
3. **Critical Rule**: An object should only be hashable if it is **immutable**! If an object's hash value changes while it resides in a hash table bucket, it will become lost forever, causing lookup failures and memory leaks.

```python
class DocumentFingerprint:
    def __init__(self, doc_id: str, content_hash: str):
        self._doc_id = doc_id
        self._content_hash = content_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DocumentFingerprint):
            return NotImplemented
        return (self._doc_id, self._content_hash) == (other._doc_id, other._content_hash)

    def __hash__(self) -> int:
        return hash((self._doc_id, self._content_hash))
```

---

## 6. Comparison Dunders & `@functools.total_ordering`

To implement full ordering (`<`, `<=`, `>`, `>=`, `==`, `!=`) without writing 6 redundant methods, use `@functools.total_ordering`:

```python
from functools import total_ordering

@total_ordering
class TaskPriority:
    def __init__(self, level: int):
        self.level = level

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TaskPriority):
            return NotImplemented
        return self.level == other.level

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TaskPriority):
            return NotImplemented
        return self.level < other.level
    # total_ordering automatically generates <=, >, >=
```

---

## 7. Operator Overloading Protocol

Python allows custom objects to intercept native operators:

| Operator | Dunder Method | Reverse / Right-Hand | In-Place |
| :--- | :--- | :--- | :--- |
| `+` | `__add__(self, other)` | `__radd__(self, other)` | `__iadd__(self, other)` |
| `-` | `__sub__(self, other)` | `__rsub__(self, other)` | `__isub__(self, other)` |
| `*` | `__mul__(self, other)` | `__rmul__(self, other)` | `__imul__(self, other)` |
| `@` (Matrix / Dot) | `__matmul__(self, other)` | `__rmatmul__(self, other)` | `__imatmul__(self, other)` |

> **Return `NotImplemented`**: If your method does not support the given type, return `NotImplemented` instead of raising `TypeError`. This gives Python an opportunity to try the reverse operation on the other operand (`__radd__`).

---

## 8. Sequence & Container Protocols

Emulate native lists, dicts, or numpy arrays:
- `__len__(self) -> int`: Called by `len(obj)`. Must return non-negative integer.
- `__getitem__(self, key)`: Called for `obj[key]` and slicing `obj[start:stop]`.
- `__setitem__(self, key, value)`: Called for `obj[key] = value`.
- `__delitem__(self, key)`: Called for `del obj[key]`.
- `__contains__(self, item) -> bool`: Called for `item in obj`.

---

## 9. Callable Objects: `__call__`

Defining `__call__` allows instances to be invoked like regular functions:

```python
class PromptSanitizer:
    def __init__(self, blocked_keywords: list[str]):
        self.blocked_keywords = set(blocked_keywords)

    def __call__(self, prompt: str) -> str:
        for word in self.blocked_keywords:
            prompt = prompt.replace(word, "[REDACTED]")
        return prompt

# Usage:
sanitize = PromptSanitizer(blocked_keywords=["API_KEY", "SECRET_TOKEN"])
clean_prompt = sanitize("User query with SECRET_TOKEN included")
```

---

## 10. The Descriptor Protocol: The Engine of Python

The descriptor protocol powers `@property`, `@classmethod`, `@staticmethod`, and ORMs (Django models, SQLAlchemy):
- `__get__(self, instance, owner=None)`
- `__set__(self, instance, value)`
- `__delete__(self, instance)`

**Data Descriptors** implement `__set__` or `__delete__`. They take precedence over instance `__dict__`.
**Non-Data Descriptors** implement only `__get__` (e.g., standard methods).

---

## 11. Attribute Lookup Chain

When resolving `instance.attr`, CPython follows a strict order:
1. **Class-level Data Descriptor** (e.g. properties with setters).
2. **Instance `__dict__`** (or slot index).
3. **Class-level Non-Data Descriptor** (e.g. methods, read-only properties).
4. **Class `__dict__` and Base Classes (via MRO)**.
5. **Fallback to `__getattr__(self, name)`** (if defined and attribute was not found).

---

## 12. Multiple Inheritance & The Diamond Problem

When a class inherits from multiple parents with a shared ancestor, method ambiguity arises:

```
        +-------+
        |   A   |
        +-------+
         /     \
        /       \
    +---+       +---+
    | B |       | C |
    +---+       +---+
        \       /
         \     /
        +-------+
        |   D   |
        +-------+
```

Which version of a method does `D` execute? In early Python versions, depth-first search caused inconsistent behavior. Modern Python solves this deterministically with the **C3 Linearization Algorithm**.

---

## 13. C3 Linearization & Method Resolution Order (MRO)

The C3 algorithm computes a monotonic, deterministic linear order for any inheritance graph:

### The C3 Rules:
1. **Subclasses before Base Classes**: Children are visited before parents.
2. **Order of Declaration**: If `class D(B, C):`, `B` is checked before `C`.
3. **Monotonicity**: If $X$ precedes $Y$ in the MRO of any parent class, $X$ will precede $Y$ in the MRO of all derived classes.

Inspect the MRO of any class:
```python
print([cls.__name__ for cls in D.__mro__])
# Output: ['D', 'B', 'C', 'A', 'object']
```

---

## 14. Cooperative Multiple Inheritance with `super()`

Never call parent methods directly using `BaseClass.__init__(self)` in multiple inheritance! This breaks the cooperative chain and causes methods to execute multiple times or be skipped entirely.

### How `super()` Works:
- `super()` does **not** call the immediate parent. It calls the **next class in the MRO of the runtime instance** (`type(self)`).
- Every class in a cooperative hierarchy must forward arguments with `super().__init__(*args, **kwargs)`.

---

## 15. Abstract Base Classes (`abc.ABC`) & Mixin Architecture

In production backends, enforce architectural contracts using **`abc.ABC`** and `@abstractmethod`:

```python
from abc import ABC, abstractmethod

class BaseLLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Concrete subclasses must implement this method."""
        pass

    @abstractmethod
    def get_token_count(self, text: str) -> int:
        pass
```

### Composable Mixins:
Mixins are focused, single-responsibility classes intended to add reusable functionality (rate limiting, telemetry auditing, retry logic) without standing as complete independent entities.
