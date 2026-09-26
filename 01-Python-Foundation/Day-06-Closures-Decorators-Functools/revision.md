# Day 06: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before interviews or architectural reviews:

---

- ⚡ **Decorator Desugaring:**
  - `@decorator` ➔ `func = decorator(func)` executed at **module import time**.
  - Always keep logic meant for request time inside the `wrapper()` function.

- ⚡ **CPython Closure Mechanics:**
  - Free variables captured by closures escape the stack frame and are stored in heap **`PyCellObject`** instances.
  - Inspect closure state using `func.__closure__[i].cell_contents`.

- ⚡ **Metadata Preservation:**
  - Always use **`@functools.wraps(func)`** on the inner wrapper.
  - Prevents wiping out `__name__`, `__doc__`, and `__annotations__`, which is critical for FastAPI route resolution and OpenAPI schemas.

- ⚡ **3-Tier Decorator Pattern:**
  - Tier 1: Config Factory `def retry(max_attempts=3):`
  - Tier 2: Decorator `def decorator(func):`
  - Tier 3: Runtime Wrapper `@functools.wraps(func) def wrapper(*args, **kwargs):`

- ⚡ **Stacking Order Rule:**
  - Definition Order: **Bottom to Top** (inside-out).
  - Execution Order: **Top to Bottom** (outside-in).
  - Place Authentication/Tracing at the top, and Retries/Caching at the bottom.

- ⚡ **Async Handling Rule:**
  - Never wrap an `async def` function in a synchronous wrapper. Use `inspect.iscoroutinefunction(func)` to dispatch to an `async def wrapper` using `await`.

- ⚡ **Functools Tools:**
  - `functools.lru_cache(maxsize=N)`: $\mathcal{O}(1)$ in-memory memoization.
  - `functools.partial`: High-speed argument pre-binding implemented directly in C.
