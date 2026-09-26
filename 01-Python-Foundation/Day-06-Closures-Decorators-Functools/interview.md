# Day 06: Technical Interview Questions & In-Depth Engineering Answers

These questions are frequently asked in Senior Python, FastAPI Middleware, and Distributed AI Systems Engineering interviews:

---

### Q1: What happens under the hood when Python compiles the `@decorator` syntax?
**Answer:**
The `@` symbol is syntactic sugar resolved at **module compilation / import time**, not at runtime invocation. When CPython encounters:
```python
@my_decorator
def query_model(prompt: str):
    pass
```
It compiles this into an explicit higher-order function assignment:
```python
query_model = my_decorator(query_model)
```
1. CPython creates the `PyFunctionObject` for `query_model`.
2. It passes that function object as an argument into `my_decorator`.
3. The return value of `my_decorator` (typically a `wrapper` function) is rebound to the variable identifier `query_model` in the module namespace.
4. Subsequent calls to `query_model(...)` actually invoke the wrapper function.

---

### Q2: What is a closure, and how does CPython's `PyCellObject` keep captured variables alive after the outer function frame returns?
**Answer:**
A **closure** is a function that retains a reference to variables from its enclosing lexical scope ("free variables") even after the outer function has returned and its call stack frame has been destroyed.

**CPython Internal Mechanics:**
1. Under normal execution, local variables exist in the call stack frame (`PyFrameObject`) and are destroyed when the function returns.
2. If CPython detects during compilation that an inner function references an outer variable, it does not store that variable as a standard local in `fastlocals`.
3. Instead, it allocates a **`PyCellObject` on the heap**.
4. Both the outer function and the inner closure point to this shared heap cell via their `__closure__` attribute.
5. When the outer function returns and its stack frame is popped, the reference count of the `PyCellObject` remains $\ge 1$ because the inner closure still references it. Thus, the captured variable survives indefinitely on the heap.

---

### Q3: Why is `@functools.wraps` strictly mandatory when authoring decorators for web frameworks like FastAPI or Flask?
**Answer:**
When a function is wrapped, the wrapper function has its own distinct identity:
- `wrapper.__name__` is `"wrapper"`
- `wrapper.__doc__` is `None` (or the wrapper's own docstring)
- `wrapper.__annotations__` contains only the wrapper's type hints (`*args, **kwargs`)

**The Breakdown in Web Frameworks:**
Frameworks like **FastAPI** rely on runtime reflection:
1. **Endpoint Routing**: FastAPI uses the function name for unique endpoint identifiers. Without `@wraps`, all decorated routes collapse to `"wrapper"`.
2. **OpenAPI / Swagger Generation**: FastAPI reads `__doc__` for API descriptions and `__annotations__` to generate Pydantic request/response schemas. A naive decorator wipes out parameter types, causing Swagger UI to render empty schemas or fail to validate incoming JSON.
3. **`@functools.wraps(func)`** invokes `functools.update_wrapper()`, copying `__module__`, `__name__`, `__qualname__`, `__doc__`, and `__annotations__`, while setting `wrapper.__wrapped__ = func`.

---

### Q4: How do you write a decorator that accepts configuration arguments (e.g. `@retry(max_retries=5)`)? Explain the 3-tier structure.
**Answer:**
A standard decorator accepts only the target function: `decorator(func)`. If a decorator needs to accept configuration arguments, it requires **three nested tiers**:

```python
def retry(max_retries: int = 3):           # Tier 1: Configuration Factory
    def decorator(func: Callable):         # Tier 2: Actual Decorator (receives func)
        @functools.wraps(func)
        def wrapper(*args, **kwargs):      # Tier 3: Runtime Execution (receives runtime arguments)
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    pass
        return wrapper
    return decorator
```
**Execution Lifecycle:**
When Python evaluates `@retry(max_retries=5)`, it executes Tier 1 immediately. Tier 1 returns Tier 2 (`decorator`), which is then applied to the target function: `func = retry(max_retries=5)(func)`.

---

### Q5: Why is decorating an `async def` coroutine with a synchronous `def wrapper` problematic, and how do you support both?
**Answer:**
In Python, invoking an asynchronous function `async def foo()` does not execute the function immediately; it instantiates and returns a **coroutine object** that must be awaited on an active event loop (`await foo()`).

**The Problem:**
If a synchronous wrapper is used:
```python
def sync_wrapper(*args, **kwargs):
    result = func(*args, **kwargs)  # Returns a COROUTINE object, un-awaited!
    return result
```
1. Any code inside the wrapper expecting `result` to be the resolved data will receive a raw coroutine object.
2. In FastAPI, the ASGI server expects the endpoint to be an awaitable coroutine function, but the sync wrapper turns it into a standard synchronous function, running it in a worker thread pool and creating async context mismatches.

**The Solution:**
Use `inspect.iscoroutinefunction(func)` inside the decorator factory to conditionally return an `async def wrapper` that performs `await func(*args, **kwargs)` for coroutines, and a synchronous `def wrapper` for standard functions.
