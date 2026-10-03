# Day 09: Top 5 Technical Interview Questions

---

### Question 1: How does Python determine Method Resolution Order (MRO), and what are the core principles of the C3 Linearization Algorithm?

#### Expected Answer:
Python uses the **C3 Linearization algorithm** to determine a deterministic, monotonic method lookup order in multiple inheritance hierarchies.

#### The Three Core Principles:
1. **Children Before Parents (Subclass Precedence)**: A derived class is always evaluated before any of its base classes.
2. **Order of Declaration**: If a class defines `class D(B, C):`, class `B` is evaluated before class `C`.
3. **Monotonicity**: If class $X$ appears before class $Y$ in the MRO of any parent class, $X$ will appear before $Y$ in the MRO of every derived subclass. Python will raise a `TypeError: Cannot create a consistent method resolution order (MRO)` if an inheritance graph violates this condition.

You can inspect the resulting MRO on any class using `Class.__mro__` or `Class.mro()`.

---

### Question 2: What is the fundamental difference between `__new__` and `__init__`, and when would you override `__new__`?

#### Expected Answer:
Python instance creation is a two-step lifecycle:
1. **`__new__(cls, *args, **kwargs)`**:
   - A static constructor method responsible for **allocating memory** for the object.
   - Must return a new instance of `cls` (usually by delegating to `super().__new__(cls)`).
   - If `__new__` returns an instance of `cls`, CPython automatically invokes `__init__` on that instance.
2. **`__init__(self, *args, **kwargs)`**:
   - An instance initializer responsible for configuring attributes and initial state.
   - Operates on an already-allocated `self`. Must return `None`.

#### When to override `__new__`:
- **Singleton / Connection Pool caching**: Checking a cache and returning an existing instance rather than allocating a new one.
- **Subclassing immutable types**: Subclassing `tuple`, `str`, or `int`, where values must be established before memory allocation completes.
- **Metaclass and custom class creation logic**.

---

### Question 3: How does `__slots__` achieve memory optimization, and what happens at the CPython runtime level?

#### Expected Answer:
By default, every Python instance possesses a dynamic dictionary (`__dict__`) to store its attributes. In CPython, a dictionary has a minimum memory footprint of ~152+ bytes, plus extra pointer overhead.

When `__slots__ = ("x", "y")` is defined:
1. CPython **suppresses the creation of `__dict__` and `__weakref__`** for each instance.
2. Instead, attributes are stored as a fixed-size array of C pointers directly within the `PyObject` C-struct.
3. Access to slotted attributes occurs via fast C-level descriptors directly indexing memory offsets, yielding both a **40%–70% memory reduction** and slightly faster attribute lookups.

#### Production Caveats:
- Subclasses do not inherit `__slots__` automatically. A subclass must define `__slots__ = ()` to prevent CPython from re-introducing an instance `__dict__`.
- Multiple inheritance with two classes having non-empty `__slots__` raises `TypeError: multiple bases have instance lay-out conflict`.

---

### Question 4: What is the contract between `__eq__` and `__hash__`? Why does overriding `__eq__` make an object unhashable by default?

#### Expected Answer:
Python hash tables (`dict` and `set`) require the following invariant:

$$\text{If } a == b \implies \operatorname{hash}(a) == \operatorname{hash}(b)$$

1. By default, user-defined classes inherit `object.__eq__` and `object.__hash__`, which base equality and hashing on **object identity** (`id(self)`).
2. When a developer overrides `__eq__`, equality becomes value-based. Python cannot assume whether the object's fields are mutable or immutable.
3. If an object is placed into a `set` and later mutated, its hash value would change. The object would become trapped in the wrong hash bucket, causing `item in my_set` lookups to fail silently.
4. To prevent this silent corruption, CPython automatically sets `__hash__ = None` whenever `__eq__` is overridden. To make the class hashable again, the developer must explicitly implement `__hash__`, ensuring it hashes only **immutable** attributes.

---

### Question 5: How does `super()` actually work in Python? Why is calling `ParentClass.__init__(self)` an antipattern?

#### Expected Answer:
A common misconception is that `super()` calls the immediate parent class. In reality, **`super()` delegates to the next class in the runtime Method Resolution Order (MRO)** of `type(self)`.

#### Why hardcoded parent calls fail:
In diamond inheritance:
```
       Base
      /    \
    Auth   RateLimit
      \    /
      Gateway
```
If `Auth` calls `Base.__init__(self)` and `RateLimit` calls `Base.__init__(self)`:
- `Base.__init__` executes **twice**, potentially resetting initialized state or opening duplicate connection pools.
- Furthermore, if `Gateway` invokes `Auth.__init__(self)`, `RateLimit.__init__` is skipped entirely!

#### The Cooperative Pattern:
With `super().__init__(*args, **kwargs)`:
- `Gateway` calls `Auth` (first in MRO).
- `Auth`'s `super()` calls `RateLimit` (next in MRO).
- `RateLimit`'s `super()` calls `Base`.
- Every class in the hierarchy executes **exactly once** in mathematically proven order.
