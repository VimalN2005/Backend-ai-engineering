# Day 06: Closures, Custom Decorators & Functional Python with Functools

---

## 1. Definition
- **Closure**: A function object that retains access to variables in its enclosing lexical scope even after the outer function has completed execution and its stack frame has been destroyed.
- **Cell Object (`PyCellObject`)**: CPython's internal storage mechanism used to reference shared "free variables" across outer and inner function frames on the heap.
- **Decorator**: An elegant syntactic sugar (`@decorator`) that accepts a callable as an input, extends or alters its behavior without modifying its source code, and returns a callable.
- **`functools.wraps`**: A foundational utility that copies metadata (`__name__`, `__doc__`, `__annotations__`) from the original function to the wrapper function, preventing reflection breakage in frameworks like FastAPI.

---

## 2. Why? (Problem it Solves in AI & Backend Engineering)
1. **Separation of Cross-Cutting Concerns**: In production APIs, business logic (e.g. running an LLM prompt or querying a database) should not be cluttered with boilerplate logic such as **distributed tracing, authentication checks, retry backoffs, rate-limiting, and performance metrics**. Decorators isolate these concerns into reusable modules.
2. **Resilience & Rate-Limit Handling**: External foundation model APIs (OpenAI, Anthropic, Gemini) enforce strict Rate Limits (HTTP 429) and intermittent network timeouts. Decorators encapsulate automated **exponential backoff with jitter** cleanly.
3. **Preserving Framework Metadata**: Frameworks like FastAPI, Flask, and Celery rely heavily on function signatures and annotations to build API routes and OpenAPI documentation. Using `@functools.wraps` ensures that decorated routes maintain their parameter contracts and docstrings.

---

## 3. How? (Under the Hood / Working Principle)

### 3.1 The Decorator Desugaring Mechanism
The `@` symbol is purely syntactic sugar. When CPython encounters:
```python
@audit_latency
def generate_embedding(text: str) -> list[float]:
    pass
```
It desugars and compiles it into:
```python
generate_embedding = audit_latency(generate_embedding)
```
The decorator executes **immediately at module import time**, binding the variable name `generate_embedding` to the returned wrapper function object.

### 3.2 Cell Objects and Escaped Stack Frames
When an inner function references a variable from an outer function, that variable is called a **free variable**.
- Normally, when a function returns, its call stack frame (`PyFrameObject`) is deallocated from memory.
- If a free variable is captured, CPython allocates a **`PyCellObject`** on the heap.
- Both the enclosing function and the inner closure point to this shared heap cell via their `__closure__` tuple. Even after the outer function terminates, the heap cell keeps the captured value alive!

```text
Outer Function Execution Frame
   │
   ▼ (Captures variable 'api_key')
[ PyCellObject (Heap Allocated) ] ◄─── Inner Function .__closure__[0]
   │ (holds pointer to 'sk-...')
   ▼
Available forever during wrapper execution!
```

### 3.3 The Metadata Disaster Without `@functools.wraps`
When a function is wrapped:
```python
def my_decorator(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

@my_decorator
def calculate_tokens(text: str) -> int:
    """Calculates tokens for billing."""
    return len(text.split())
```
If you inspect `calculate_tokens.__name__`, it outputs `"wrapper"`, and `calculate_tokens.__doc__` becomes `None`!
- **FastAPI / Swagger Breakage**: Swagger docs will show the route as `wrapper()` with empty parameter docs.
- **`@functools.wraps(func)`** fixes this by copying:
  - `__name__`, `__qualname__`, `__doc__`, `__annotations__`, and `__module__`.
  - It sets `wrapper.__wrapped__ = func`, enabling unwrapped testing.

---

## 4. Syntax & Basic Contract

```python
import functools
from typing import Callable, Any

# Standard 2-Tier Decorator (No Arguments)
def log_execution(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        print(f"Calling: {func.__name__}")
        return func(*args, **kwargs)
    return wrapper

# Configurable 3-Tier Decorator (Accepts Arguments)
def retry(max_attempts: int = 3):
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as err:
                    if attempt == max_attempts:
                        raise
        return wrapper
    return decorator
```

---

## 5. Example 1: Conceptual Walkthrough
Inspecting `__closure__` cell objects and metadata preservation:

```python
import functools

def counter_factory(start: int = 0):
    count = start  # Free variable in cell object

    def increment():
        nonlocal count
        count += 1
        return count

    return increment

counter = counter_factory(10)
print(counter())  # 11
print(counter())  # 12

# Inspecting the internal CPython cell object:
print("Cell object:", counter.__closure__[0])
print("Stored value in cell:", counter.__closure__[0].cell_contents)  # 12
```

---

## 6. Example 2: Edge Cases & Gotchas

### Gotcha A: Decorator Stacking Order
Decorators execute from **bottom to top** (inside out) at definition time, and run from **top to bottom** at execution time:
```python
@decorator_one
@decorator_two
def endpoint():
    pass

# Desugars into:
# endpoint = decorator_one(decorator_two(endpoint))
```
*Rule*: Place outer middleware decorators (Authentication, Tracing) at the top, and low-level decorators (Retry, Memoization) at the bottom!

### Gotcha B: Decorating Asynchronous Coroutines (`async def`)
A synchronous wrapper will break an async endpoint!
```python
# BROKEN: Turns an async coroutine into a sync generator/function:
def broken_decorator(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs) # Returns un-awaited coroutine object!
    return wrapper

# PRODUCTION FIX: Use an async wrapper:
def async_decorator(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)
    return wrapper
```

---

## 7. Production-Grade Example
An Enterprise Resilient LLM Gateway Decorator with Exponential Backoff, Randomized Jitter, and Latency Telemetry.
*(See complete runnable code in [example_02.py](example_02.py))*

```python
import time
import random
import functools
import logging

def retry_llm_call(max_retries: int = 3, base_delay: float = 0.5):
    """Retries LLM API calls on network or rate-limit failures with exponential jitter."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError) as err:
                    if attempt == max_retries:
                        raise
                    # Add jitter to prevent Thundering Herd problem
                    jittered_delay = delay + random.uniform(0, 0.2)
                    time.sleep(jittered_delay)
                    delay *= 2.0
        return wrapper
    return decorator
```

---

## 8. Common Mistakes & Antipatterns
- ❌ **Mistake**: Forgetting `@functools.wraps(func)`, wiping out route metadata in FastAPI and Swagger UI.
- ❌ **Mistake**: Decorating `async def` route handlers with synchronous wrapper functions.
- ❌ **Mistake**: Executing business logic at decorator definition time (outside `wrapper()`) instead of call time.
- ❌ **Mistake**: Storing global mutable state directly in the decorator scope without thread locks.
- ❌ **Mistake**: Swallowing exceptions inside decorators without re-raising or logging stack traces.

---

## 9. Performance & Complexity Analysis
- **Decorator Call Overhead**: Adds approximately **100–250 nanoseconds** per function invocation (pushing a single additional stack frame in CPython). In network API calls taking 50–500 milliseconds, this overhead is effectively 0.0001% of request time.
- **`functools.lru_cache`**: Provides $\mathcal{O}(1)$ memoized lookups using an internal compact dictionary and doubly-linked list for least-recently-used eviction.
- **`functools.partial`**: Faster than creating `lambda` functions because it is implemented directly in C (`_functools.partial`).

---

## 10. Security Implications
1. **Masking Sensitive Data in Logging Decorators**: Never write a blanket `@log_request` decorator that logs `*args` and `**kwargs` directly. API payloads contain passwords, session tokens, and credit cards. Always sanitize arguments before logging!
2. **Timing Attacks in Authentication Decorators**: In custom `@require_api_key` decorators, always verify keys using constant-time comparison (`hmac.compare_digest`), never standard string `==`.

---

## 11. When to Use?
- **Cross-Cutting Concerns**: Logging, distributed tracing, request ID tagging, timing benchmarks.
- **Resilience**: Exponential backoff retries for external LLM APIs (OpenAI, Gemini).
- **Authentication & RBAC**: Verifying JWT tokens or security scopes before executing business logic.
- **Memoization (`@functools.lru_cache`)**: Caching deterministic calculations, vector model configurations, or tokenizer instances.

---

## 12. When NOT to Use?
- **Do NOT use decorators for simple internal helpers**: If a concern applies to only one function, keep the code inside the function for readability.
- **Do NOT nest 5+ decorators on a single endpoint**: Excessive decorator stacking makes debugging call stacks difficult. Refactor into an explicit middleware pipeline or service layer.

---

## 13. Top Interview Questions
1. *What happens under the hood when Python compiles the `@decorator` syntax?*
2. *What is a closure, and how does CPython's `PyCellObject` keep captured variables alive after the outer function returns?*
3. *Why is `@functools.wraps` strictly mandatory when authoring decorators for frameworks like FastAPI or Flask?*
4. *How do you write a decorator that accepts configuration arguments (e.g. `@retry(max_retries=5)`)? Explain the 3-tier structure.*
5. *Why is decorating an `async def` coroutine with a synchronous `def wrapper` problematic, and how do you support both?*

---

## 14. Practice Problems
1. Implement an `@audit_latency` decorator that measures execution duration, extracts token counts, and warns if latency exceeds a defined threshold.
2. Build a sliding-window rate-limiting decorator using closures and `nonlocal` without external dependencies.
3. Write a decorator `@type_checked` that asserts incoming arguments match type annotations at runtime.

---

## 15. 5-Minute Revision Notes
- `@decorator` is syntactic sugar for `func = decorator(func)` executed at import time.
- Closures bind outer variables via **`PyCellObject`** stored in `func.__closure__`.
- Always use **`@functools.wraps(func)`** to preserve function metadata (`__name__`, `__doc__`, `__annotations__`).
- Configurable decorators require **3 tiers**: Configuration Function ➔ Decorator Function ➔ Wrapper Function.
- Stacked decorators evaluate **bottom-to-top at import**, and execute **top-to-bottom at runtime**.
- Use **`functools.lru_cache(maxsize=128)`** for $\mathcal{O}(1)$ memoization of expensive deterministic operations.
