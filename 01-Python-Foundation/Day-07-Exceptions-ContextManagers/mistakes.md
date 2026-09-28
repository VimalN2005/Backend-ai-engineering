# Day 07: Common Mistakes & Antipatterns (Exceptions & Context Managers)

Here are the 7 most damaging mistakes developers make with exceptions, resource lifecycles, and context managers in Python backend systems:

---

### 1. Bare `except:` or Catching `BaseException`
- ❌ **The Mistake:**
  ```python
  try:
      run_worker_loop()
  except:  # Or: except BaseException:
      logger.error("Something went wrong")
  ```
- ⚠️ **The Problem:** A bare `except:` or `except BaseException:` catches **`SystemExit`** and **`KeyboardInterrupt`**. In Docker or Kubernetes, sending a `SIGTERM` / `SIGINT` signal will be caught by this block, making it impossible to stop or restart the worker container gracefully!
- ✅ **The Correction:** Always catch specific exceptions, or at most `Exception`:
  ```python
  try:
      run_worker_loop()
  except Exception as err:
      logger.error(f"Worker failure: {err}", exc_info=True)
  ```

---

### 2. Losing Root Cause Tracebacks (Missing `from err`)
- ❌ **The Mistake:**
  ```python
  try:
      execute_db_query()
  except psycopg2.OperationalError:
      # Raising fresh exception without linking:
      raise DatabaseError("Database unavailable")
  ```
- ⚠️ **The Problem:** In Sentry or CloudWatch logs, the original PostgreSQL operational error, host IP, and query stack trace are completely discarded. Engineers cannot tell if it was an authentication failure, network timeout, or query syntax error.
- ✅ **The Correction:** Use explicit exception chaining with `from`:
  ```python
  try:
      execute_db_query()
  except psycopg2.OperationalError as err:
      raise DatabaseError("Database unavailable") from err
  ```

---

### 3. Returning Values Inside a `finally` Block
- ❌ **The Mistake:**
  ```python
  def authenticate(user_token: str) -> bool:
      try:
          if not is_valid(user_token):
              raise PermissionDeniedError("Invalid token!")
          return True
      finally:
          return False  # Overrides EVERYTHING!
  ```
- ⚠️ **The Problem:** A `return` statement executed inside a `finally` block **silently discards any active exception** in the `try` block. The `PermissionDeniedError` is completely wiped out, returning `False` instead of raising an error!
- ✅ **The Correction:** Use `finally` strictly for resource cleanup, never for returning data:
  ```python
  def authenticate(user_token: str) -> bool:
      try:
          if not is_valid(user_token):
              raise PermissionDeniedError("Invalid token!")
          return True
      finally:
          close_auth_socket()
  ```

---

### 4. Accidentally Suppressing Exceptions in `__exit__`
- ❌ **The Mistake:**
  ```python
  class AuditLogger:
      def __enter__(self):
          return self
      def __exit__(self, exc_type, exc_val, exc_tb):
          print("Exiting...")
          return True  # Silently SUPPRESSES all exceptions!
  ```
- ⚠️ **The Problem:** In Python's context manager protocol, if `__exit__()` returns a truthy value (`True`), Python assumes the exception was handled and **suppresses it**. Fatal bugs in the caller's code will be ignored, leading to corrupted data states.
- ✅ **The Correction:** Return `False` or `None` unless you deliberately intend to swallow the exception:
  ```python
  class AuditLogger:
      def __enter__(self):
          return self
      def __exit__(self, exc_type, exc_val, exc_tb):
          print("Exiting...")
          return False  # Allows exceptions to bubble up!
  ```

---

### 5. Using Exceptions for Normal Business Logic (Control Flow)
- ❌ **The Mistake:**
  ```python
  def get_user_tier(user_id: int) -> str:
      try:
          return user_tiers[user_id]
      except KeyError:
          return "free"  # Relying on exception for standard default logic
  ```
- ⚠️ **The Problem:** Raising and catching exceptions in Python incurs stack frame allocation and traceback construction costs (~1–3 microseconds). In high-frequency API loops, this degrades throughput.
- ✅ **The Correction:** Use dictionary methods or guard conditions:
  ```python
  def get_user_tier(user_id: int) -> str:
      return user_tiers.get(user_id, "free")
  ```

---

### 6. Leaking Stack Traces to External API Clients
- ❌ **The Mistake:**
  ```python
  @app.exception_handler(Exception)
  async def global_handler(request, exc):
      # Returning raw traceback to the caller:
      return JSONResponse(status_code=500, content={"traceback": traceback.format_exc()})
  ```
- ⚠️ **The Problem:** Attackers analyze stack traces to discover database passwords, server directory layouts, internal IP addresses, and package vulnerabilities (CWE-209).
- ✅ **The Correction:** Log the traceback securely on the server and return a sanitized RFC 7807 error response with a tracking correlation ID:
  ```python
  @app.exception_handler(Exception)
  async def global_handler(request, exc):
      error_id = uuid.uuid4().hex
      logger.error(f"Internal error [{error_id}]: {exc}", exc_info=True)
      return JSONResponse(
          status_code=500,
          content={"error": "InternalServerError", "error_id": error_id}
      )
  ```

---

### 7. Placing Operations That Might Raise in `__enter__` After Resource Acquisition
- ❌ **The Mistake:**
  ```python
  class ConnectionManager:
      def __enter__(self):
          self.socket = open_socket()
          self.validate_handshake()  # If this raises, __exit__ is NEVER called!
          return self.socket
      def __exit__(self, *args):
          self.socket.close()
  ```
- ⚠️ **The Problem:** If an exception occurs inside `__enter__()`, Python **never executes `__exit__()`**. The socket opened in line 1 leaks permanently!
- ✅ **The Correction:** Wrap intermediate initialization steps in `try-except` inside `__enter__` to guarantee cleanup:
  ```python
  class ConnectionManager:
      def __enter__(self):
          self.socket = open_socket()
          try:
              self.validate_handshake()
          except Exception:
              self.socket.close()
              raise
          return self.socket
  ```
