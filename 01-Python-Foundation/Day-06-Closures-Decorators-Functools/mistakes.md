# Day 06: Common Mistakes & Antipatterns (Closures & Decorators)

Here are the 7 most frequent decorator and functional programming mistakes that cause hard-to-debug crashes, metadata loss, and concurrency bugs in Python backend systems:

---

### 1. Forgetting `@functools.wraps`
- ❌ **The Mistake:**
  ```python
  def log_request(func):
      def wrapper(*args, **kwargs):
          return func(*args, **kwargs)
      return wrapper

  @log_request
  def get_user_profile(user_id: int):
      """Retrieves user profile from database."""
      pass
  ```
- ⚠️ **The Problem:** The wrapper completely overwrites `get_user_profile.__name__` to `"wrapper"` and `__doc__` to `None`. In FastAPI or Flask, this breaks automated Swagger/OpenAPI documentation generation and endpoint routing.
- ✅ **The Correction:** Always apply `@functools.wraps(func)` to the inner wrapper:
  ```python
  import functools

  def log_request(func):
      @functools.wraps(func)
      def wrapper(*args, **kwargs):
          return func(*args, **kwargs)
      return wrapper
  ```

---

### 2. Wrapping Asynchronous Coroutines with Synchronous Wrappers
- ❌ **The Mistake:**
  ```python
  def measure_time(func):
      @functools.wraps(func)
      def wrapper(*args, **kwargs):
          start = time.perf_counter()
          result = func(*args, **kwargs)  # Calling async def returns a COROUTINE object, un-awaited!
          print(f"Elapsed: {time.perf_counter() - start}")
          return result
      return wrapper

  @app.get("/items")
  @measure_time
  async def read_items():
      await asyncio.sleep(1)
  ```
- ⚠️ **The Problem:** The synchronous wrapper calls `func()` without `await`, returning an un-awaited coroutine object in 0.0001 seconds while the actual work never completes.
- ✅ **The Correction:** Provide an asynchronous wrapper for async endpoints:
  ```python
  def measure_time_async(func):
      @functools.wraps(func)
      async def wrapper(*args, **kwargs):
          start = time.perf_counter()
          result = await func(*args, **kwargs)
          print(f"Elapsed: {time.perf_counter() - start}")
          return result
      return wrapper
  ```

---

### 3. Executing Code at Decorator Definition Time Instead of Call Time
- ❌ **The Mistake:**
  ```python
  def authenticate_user(func):
      # Code placed HERE runs once when the Python module is imported, NOT when called!
      user = get_current_user_from_request() 
      if not user:
          raise PermissionError("Not authenticated")

      @functools.wraps(func)
      def wrapper(*args, **kwargs):
          return func(*args, **kwargs)
      return wrapper
  ```
- ⚠️ **The Problem:** Python decorators execute **at module import time**. Any logic outside `def wrapper()` runs when the server boots up, crashing because no HTTP request exists yet!
- ✅ **The Correction:** Place all per-invocation logic strictly inside the `wrapper()` body:
  ```python
  def authenticate_user(func):
      @functools.wraps(func)
      def wrapper(*args, **kwargs):
          user = get_current_user_from_request()
          if not user:
              raise PermissionError("Not authenticated")
          return func(*args, **kwargs)
      return wrapper
  ```

---

### 4. Incorrect Decorator Stacking Order
- ❌ **The Mistake:**
  ```python
  # Flow: Retrying authentication checks instead of retrying network calls
  @retry_on_network_error(max_retries=3)
  @verify_jwt_token
  def call_ai_service():
      pass
  ```
- ⚠️ **The Problem:** Decorators evaluate bottom-to-top at import, but **execute top-to-bottom at runtime**. Stacking `@retry` above `@verify_jwt_token` means that if a network error occurs in the AI service, it retries the JWT verification step unnecessarily on every attempt.
- ✅ **The Correction:** Stack entry-level guards (Auth, Rate Limiting) at the top, and execution-level policies (Retry, Cache) at the bottom:
  ```python
  @verify_jwt_token
  @retry_on_network_error(max_retries=3)
  def call_ai_service():
      pass
  ```

---

### 5. Swallowing Exceptions Inside Decorators
- ❌ **The Mistake:**
  ```python
  def safe_execute(func):
      @functools.wraps(func)
      def wrapper(*args, **kwargs):
          try:
              return func(*args, **kwargs)
          except Exception as err:
              print(f"Error occurred: {err}")
              return None  # Silently swallows errors!
      return wrapper
  ```
- ⚠️ **The Problem:** Returning `None` silently hides critical production database connection drops, syntax errors, and API timeouts. Downstream callers will fail with obscure `AttributeError: 'NoneType' object has no attribute '...'`.
- ✅ **The Correction:** Log with `exc_info=True` and either re-raise or return a structured domain error object:
  ```python
  def safe_execute(func):
      @functools.wraps(func)
      def wrapper(*args, **kwargs):
          try:
              return func(*args, **kwargs)
          except Exception as err:
              logger.error(f"Execution failed in {func.__name__}: {err}", exc_info=True)
              raise
      return wrapper
  ```

---

### 6. Missing `*args` and `**kwargs` Forwarding
- ❌ **The Mistake:**
  ```python
  def log_call(func):
      @functools.wraps(func)
      def wrapper(payload):  # Hardcoded single argument!
          return func(payload)
      return wrapper
  ```
- ⚠️ **The Problem:** If the decorated function accepts multiple arguments, keyword arguments, or optional parameters, the wrapper will crash with a `TypeError`.
- ✅ **The Correction:** Always accept and unpack `*args, **kwargs`:
  ```python
  def log_call(func):
      @functools.wraps(func)
      def wrapper(*args, **kwargs):
          return func(*args, **kwargs)
      return wrapper
  ```

---

### 7. Unbounded Memory Leaks with `@functools.lru_cache`
- ❌ **The Mistake:**
  ```python
  @functools.lru_cache(maxsize=None)  # Infinite cache size!
  def fetch_user_chat_summary(user_id: int):
      pass
  ```
- ⚠️ **The Problem:** Setting `maxsize=None` disables LRU eviction. If your system has 1,000,000 users, the cache grows indefinitely in RAM until the container crashes from Out-Of-Memory (OOM).
- ✅ **The Correction:** Always specify a realistic maximum capacity:
  ```python
  @functools.lru_cache(maxsize=2048)
  def fetch_user_chat_summary(user_id: int):
      pass
  ```
