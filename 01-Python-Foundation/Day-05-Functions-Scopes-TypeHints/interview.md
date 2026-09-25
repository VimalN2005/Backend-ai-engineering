# Day 05: Technical Interview Questions & In-Depth Engineering Answers

These questions are frequently asked in Senior Python Backend, FastAPI Architecture, and AI Systems Engineering interviews:

---

### Q1: How does Python's LEGB scope resolution work under the hood, and what makes Local variable access significantly faster than Global access?
**Answer:**
When an identifier is evaluated inside a function, CPython resolves the variable in four hierarchical namespaces:
1. **Local (L)**: Variables declared within the current executing function frame.
2. **Enclosing (E)**: Variables in enclosing functions (closures / nested scopes).
3. **Global (G)**: Module-level variables stored in the module's `__dict__`.
4. **Built-in (B)**: Built-in functions and types (`len`, `range`, `Exception`) in the `builtins` module.

**The Performance Difference:**
- **Local Access (`LOAD_FAST`)**: During compilation, CPython counts local variables and assigns each an integer offset in a fixed-size C array within the call frame struct (`f->f_fastlocals`). Retrieving a local variable requires a simple direct C array index lookup: $\mathcal{O}(1)$ taking $\approx 15 \text{ nanoseconds}$.
- **Global Access (`LOAD_GLOBAL`)**: Global variables require a hash table lookup in the module's global dictionary `f->f_globals`, followed by a fallback lookup in `f->f_builtins` if not found: $\mathcal{O}(1)$ average but requires string hashing and collision checks, taking $\approx 45 \text{ nanoseconds}$ (~3x slower).

---

### Q2: What are Positional-Only (`/`) and Keyword-Only (`*`) parameters, and why were they introduced in PEP 570?
**Answer:**
- **Positional-Only (`/`)**: Parameters placed to the **left** of `/` must be supplied positionally and cannot be passed using keyword argument syntax (`name=val`).
- **Keyword-Only (`*`)**: Parameters placed to the **right** of `*` (or after `*args`) must be supplied explicitly using keyword syntax (`name=val`).

**Architectural Rationale:**
1. **API Backward Compatibility**: For library authors (e.g. FastAPI, LiteLLM, LangChain), positional-only parameters allow renaming internal argument names in future releases without breaking client code that might have used those names as keywords.
2. **Error Prevention at Call Sites**: For functions that accept multiple boolean flags or optional configuration values (e.g. `delete_user(user_id, True, False, True)`), forcing keyword-only parameters (`delete_user(user_id, *, soft_delete=True, notify=False, cascade=True)`) prevents dangerous argument-order mix-ups.

---

### Q3: Why do mutable default arguments retain state across subsequent function invocations in CPython?
**Answer:**
In Python, functions are first-class runtime objects (`PyFunctionObject`). When CPython executes a `def` statement, it parses and compiles the function code block and evaluates its default argument expressions **immediately at definition/import time**, not at invocation time.

The evaluated default objects are stored in the function's `__defaults__` tuple attribute on the heap.
```python
def append_item(x, target=[]):
    target.append(x)
    return target

# append_item.__defaults__ points to the EXACT same list instance in RAM!
```
When `append_item` is called without a second argument, CPython points the local parameter `target` to `append_item.__defaults__[0]`. Because a list is mutable, calling `.append()` alters that heap object in place. Subsequent calls continue referencing this exact same modified list object.

**Production Fix:** Always set the default to immutable `None` and initialize the container inside the function body.

---

### Q4: What is the practical difference between the `global` and `nonlocal` keywords?
**Answer:**
- **`global`**: Declares that an identifier should be bound to the **top-level module namespace** (`globals()`). It tells the compiler to emit `STORE_GLOBAL` instead of `STORE_FAST`.
- **`nonlocal`**: Declares that an identifier should be bound to the nearest **enclosing (outer) function's scope**, excluding global and built-in namespaces. It tells the compiler to emit `STORE_DEREF` to modify the shared "cell" object representing the closure.

**Production Use Case:**
`nonlocal` is essential when writing stateful closures, custom decorators, or in-memory rate-limiting counters where an inner wrapper function needs to mutate a counter held in the outer factory function's stack frame without polluting module-level global variables.

---

### Q5: Do Python type hints provide runtime type validation? How does FastAPI leverage them?
**Answer:**
**No.** Python type hints (PEP 484) are purely static annotations. CPython records them in the function's `__annotations__` dictionary and completely ignores them during runtime execution. Passing a string to an integer-annotated parameter executes without error.

**How FastAPI Leverages Type Hints:**
FastAPI utilizes reflection via `typing.get_type_hints()` and **Pydantic V2** to bridge this gap:
1. **Introspection at Startup**: When the FastAPI application boots, it inspects the type annotations of every path operation function (`def get_item(item_id: int):`).
2. **Runtime Casting & Validation**: When an HTTP request arrives, FastAPI extracts query, path, and body strings from the HTTP request, matches them against the expected types, and passes them to Pydantic for validation and automatic type coercion (e.g., converting `"42"` into integer `42`).
3. **OpenAPI Schema Generation**: FastAPI parses these type hints to automatically build interactive Swagger/OpenAPI documentation (`/docs`) with zero manual configuration.
