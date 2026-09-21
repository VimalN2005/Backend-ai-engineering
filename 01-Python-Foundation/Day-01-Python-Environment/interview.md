# Day 01: Technical Interview Questions & In-Depth Engineering Answers

These questions are frequently asked in Mid-to-Senior Python Backend, Infrastructure, and Systems Engineering interviews:

---

### Q1: What is bytecode in CPython, and why is it stored in `.pyc` files?
**Answer:**
When CPython executes a `.py` script, it first parses the source text into an **Abstract Syntax Tree (AST)** and compiles it into an intermediate representation called **Bytecode**. Bytecode is a sequence of platform-independent, 2-byte virtual instructions executed by the **Python Virtual Machine (PVM)** evaluation loop in `ceval.c`.

CPython writes compiled bytecode into `.pyc` files inside the `__pycache__` directory to optimize **application startup time**. On subsequent executions, CPython checks the source file's timestamp and hash. If unchanged, it skips the expensive tokenization, parsing, and AST generation phases by loading the `.pyc` file directly into memory. Bytecode does not accelerate runtime loop performance, but it dramatically decreases boot time for large frameworks (e.g., Django, FastAPI, Celery).

---

### Q2: What is the fundamental difference between `is` and `==`? Under what circumstances can `is` produce surprising bugs?
**Answer:**
- `==` tests **Value Equality**: It invokes the object's `__eq__()` dunder method to determine whether two objects represent equivalent values.
- `is` tests **Reference Identity**: It compares memory addresses (`id(a) == id(b)`), confirming whether two identifiers point to the exact same `PyObject` on the heap.

**The Edge-Case Bug:**
CPython employs an internal optimization known as *small integer caching* (interning integers between `-5` and `256`) and string interning for select string constants.
```python
a = 256
b = 256
a is b  # True (CPython reuses pre-allocated singleton)

x = 1000
y = 1000
x is y  # False (Allocates distinct PyLongObject instances on heap)
x == y  # True (Values match)
```
If an engineer uses `is` to check values (e.g., `if status is "PAID":`), the condition may evaluate to `True` during testing due to string interning, but will randomly evaluate to `False` in production when values are received dynamically over HTTP or parsed from database rows.

---

### Q3: How does a Python Virtual Environment (`venv`) work under the hood?
**Answer:**
A virtual environment does not clone the entire Python binary or runtime. Instead, it creates an isolated filesystem tree with its own:
1. `pyvenv.cfg` configuration file pointing to the host Python binary (`home = ...`).
2. Local `bin/` (or `Scripts/` on Windows) directory containing lightweight symlinks or wrappers to the system Python interpreter.
3. Dedicated `lib/pythonX.Y/site-packages/` directory.

When activated, the virtual environment prepends its `bin/` path to the shell's `PATH` variable. Upon launch, CPython detects `pyvenv.cfg` and sets `sys.prefix` to the venv directory while retaining `sys.base_prefix` pointing to the host Python. The module import subsystem prioritizes `sys.prefix/lib/.../site-packages`, isolating dependencies from the host machine.

---

### Q4: What is a `PyObject`, and what fields are present in its mandatory header?
**Answer:**
In CPython, every variable, integer, string, function, and class is represented as a C structure extending `PyObject`. On a 64-bit architecture, the header (`PyObject_HEAD` macro) requires 16 bytes:
1. `ob_refcnt` (8 bytes / `Py_ssize_t`): Maintains the total count of active references pointing to this object. When this counter drops to zero, the object is deallocated immediately by CPython's reference counting garbage collector.
2. `ob_type` (8 bytes / `struct _typeobject*`): A C pointer to the type descriptor object (e.g., `&PyLong_Type`, `&PyList_Type`). This pointer determines the object's method tables, attribute resolution, and memory sizing.

---

### Q5: What is the practical difference between `sys.prefix` and `sys.base_prefix`?
**Answer:**
- `sys.base_prefix`: Points permanently to the directory where the base host Python interpreter was originally installed.
- `sys.prefix`: Points to the directory of the currently active Python execution environment.

When executing in a standard system environment, `sys.prefix == sys.base_prefix`. When executing inside an active virtual environment, `sys.prefix != sys.base_prefix`. This check is the standard, reliable pattern used by production health-checks and CI scripts to enforce virtual environment isolation.
