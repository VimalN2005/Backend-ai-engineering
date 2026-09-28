# Day 07: Robust Exception Hierarchies, Context Managers & Resource Safety

---

## 1. Definition
- **Exception Hierarchy**: CPython organizes all errors into a structured class tree rooted at `BaseException`. Application-level exceptions inherit from `Exception`, while fatal runtime signals (`KeyboardInterrupt`, `SystemExit`) bypass `Exception`.
- **Exception Chaining (PEP 3134)**: The explicit linking of an underlying exception to a higher-level domain exception using `raise CustomError(...) from original_err`, preserving root-cause tracebacks (`__cause__`).
- **Context Manager Protocol**: A language-level contract governed by the `__enter__()` and `__exit__()` dunder methods (or `__aenter__()` and `__aexit__()` for async), guaranteeing deterministic resource allocation and deallocation (memory, sockets, database transactions, file handles).
- **`contextlib`**: A standard library module providing `@contextmanager` and `@asynccontextmanager` generator utilities to build robust context managers without writing verbose class boilerplate.

---

## 2. Why? (Problem it Solves in AI & Backend Engineering)
1. **Preventing Connection & Memory Leaks**: In high-throughput APIs handling thousands of concurrent requests, forgetting to close a database connection, Redis socket, or vector database handle exhausts system file descriptors, causing widespread 500 crashes. Context managers guarantee cleanup even when fatal exceptions occur.
2. **Domain-Specific Error Translation**: In microservice backends, low-level database errors (e.g. `psycopg2.OperationalError`) or network timeouts (`httpx.ConnectTimeout`) must not be leaked to the client. Exception hierarchies allow mapping low-level failures into clean HTTP status codes (e.g., 404, 422, 503) via global exception handlers.
3. **Atomic Transactions**: In financial and AI ingestion workflows, document ingestion or vector upserts must succeed as an atomic unit. Context managers provide clean `commit` on success and automatic `rollback` on error.

---

## 3. How? (Under the Hood / Working Principle)

### 3.1 The Python Exception Tree
```text
BaseException
 ├── SystemExit                   # Raised by sys.exit(); never catch with except Exception!
 ├── KeyboardInterrupt            # Raised on Ctrl+C; never catch with except Exception!
 ├── GeneratorExit                # Raised when a generator's close() is called
 └── Exception                    # ROOT OF ALL APPLICATION ERRORS!
      ├── ArithmeticError
      ├── LookupError (IndexError, KeyError)
      ├── OSError (FileNotFoundError, ConnectionError)
      ├── TypeError / ValueError
      └── CustomAppException      # YOUR CUSTOM DOMAIN HIERARCHY
```
> **Critical Rule**: Never write `except BaseException:`. Always catch `except Exception:` to avoid intercepting `KeyboardInterrupt` or `SystemExit`, which prevents containers from shutting down gracefully!

### 3.2 The Execution Flow of `try...except...else...finally`
```text
            ┌──────────────────┐
            │    try block     │
            └─────────┬────────┘
                      │
           Did an exception occur?
             /                  \
          [YES]                 [NO]
           /                      \
┌──────────────────────┐  ┌──────────────────────┐
│     except block     │  │      else block      │
│ (Catches & handles)  │  │ (Runs ONLY if error- │
└──────────┬───────────┘  │         free)        │
           │              └──────────┬───────────┘
           └──────────┬──────────────┘
                      │
            ┌──────────────────┐
            │  finally block   │ ──► (ALWAYS executes, even on return/error!)
            └──────────────────┘
```

### 3.3 The Context Manager Protocol (`__enter__` & `__exit__`)
When executing `with ManagedResource() as res:`:
1. CPython calls `res.__enter__()`. The return value is bound to the target identifier after `as`.
2. The inner code block executes.
3. Upon block exit (normal completion or error), CPython invokes:
   ```python
   __exit__(exc_type, exc_val, exc_tb)
   ```
   - If no error occurred, all three arguments are `None`.
   - If an error occurred, the exception type, value, and traceback are passed.
   - If `__exit__` returns `True`, the exception is **suppressed**. If it returns `False` or `None`, the exception **bubbles up**.

---

## 4. Syntax & Basic Contract

```python
import sys
from contextlib import contextmanager
from typing import Generator

# Custom Domain Exception Hierarchy
class BackendServiceError(Exception):
    """Base exception for all application errors."""
    pass

class DatabaseConnectionError(BackendServiceError):
    """Raised when database connection fails."""
    pass

# Generator-based Context Manager using contextlib
@contextmanager
def managed_resource() -> Generator[str, None, None]:
    print("[SETUP] Acquiring resource...")
    resource = "RESOURCE_POINTER"
    try:
        yield resource  # Hand control over to the caller's with-block
    finally:
        # Guarantees cleanup even if the caller raises an exception
        print("[CLEANUP] Releasing resource safely.")
```

---

## 5. Example 1: Conceptual Walkthrough
Demonstrating Exception Chaining (`raise ... from err`) and root-cause inspection:

```python
import sys

class ModelInferenceError(Exception):
    pass

def call_foundation_model():
    raise TimeoutError("Network gateway timed out after 30.0s")

def execute_chat():
    try:
        call_foundation_model()
    except TimeoutError as err:
        # Explicitly link low-level network error to domain error
        raise ModelInferenceError("Upstream LLM provider is currently unreachable") from err

try:
    execute_chat()
except ModelInferenceError as e:
    print(f"Caught Domain Error: {e}")
    print(f"Original Root Cause: {e.__cause__}")  # TimeoutError
```

---

## 6. Example 2: Edge Cases & Gotchas

### Gotcha A: Suppressing Exceptions Accidentally in `__exit__`
```python
class BadManager:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Returning True suppresses ALL errors, masking fatal crashes!
        return True

with BadManager():
    raise ZeroDivisionError("Math error!")
print("Code continues running blindly!") # Silently swallowed fatal bug!
```

### Gotcha B: Return Statements Inside `finally` Blocks
```python
# ANTIPATTERN: A return statement in finally overrides any raised exception!
def broken_cleanup():
    try:
        raise ValueError("Critical data corruption!")
    finally:
        return "Cleaned" # The ValueError is completely WIPED OUT!

print(broken_cleanup()) # Outputs 'Cleaned', error is completely lost!
```

---

## 7. Production-Grade Example
An Atomic Database & Vector Store Transaction Context Manager that automatically commits changes on success and performs a safe rollback on failure.
*(See complete runnable code in [example_02.py](example_02.py))*

```python
from contextlib import contextmanager

class DatabaseTransactionManager:
    @contextmanager
    def transaction(self):
        """Ensures ACID guarantees: auto-commit or auto-rollback."""
        print("[TX START] BEGIN TRANSACTION")
        try:
            yield self
            print("[TX SUCCESS] COMMIT")
        except Exception as err:
            print(f"[TX FAILURE] ROLLBACK due to: {err}")
            raise
        finally:
            print("[TX CLOSE] Release connection back to pool.")
```

---

## 8. Common Mistakes & Antipatterns
- ❌ **Mistake**: Writing bare `except:` without specifying an exception class.
  - ✅ **Correction**: Catch specific exceptions: `except (KeyError, ValueError):` or at least `except Exception:`.
- ❌ **Mistake**: Catching `BaseException`, accidentally blocking server shutdowns (`KeyboardInterrupt` / `SystemExit`).
- ❌ **Mistake**: Losing original tracebacks by writing `raise CustomError()` instead of `raise CustomError() from err`.
- ❌ **Mistake**: Placing cleanup logic after a `try` block without a `finally` block or context manager.
- ❌ **Mistake**: Returning values inside a `finally` block, which swallows unhandled exceptions.

---

## 9. Performance & Complexity Analysis
- **Zero Cost for Unraised Exceptions**: In modern CPython (3.11+), `try-except` blocks have **zero runtime overhead** when no exceptions are raised (Zero-cost exception handling using static tables in code objects).
- **Cost of Raised Exceptions**: Raising an exception involves allocating a traceback object and inspecting stack frames (~1–3 microseconds). Never use exceptions for normal control flow (e.g. exiting loops); use them strictly for exceptional error states.
- **Context Manager Overhead**: Entering and exiting a context manager costs $\approx 30 \text{ nanoseconds}$, negligible compared to database or network I/O.

---

## 10. Security Implications
1. **Leaking Internal Stack Traces in API Responses**: Never return raw exception tracebacks (`traceback.format_exc()`) to API clients. They reveal internal database table names, secret paths, and library versions to attackers. Log tracebacks securely in CloudWatch/Sentry, and return sanitized error schemas (RFC 7807).
2. **Resource Exhaustion (Denial of Service)**: Unclosed file handles or database connections allow attackers to trigger Denial of Service by sending concurrent requests until file descriptor limits (`ulimit -n`) are reached. Always use context managers.

---

## 11. When to Use?
- **Context Managers**: File I/O, database connections, Redis distributed locks, temporary file lifecycles, and profiling timers.
- **Custom Exceptions**: Creating structured domain errors (`UserNotFoundError`, `InsufficientTokenBalanceError`, `RateLimitExceededError`).
- **`raise ... from err`**: Whenever translating lower-level library errors into application domain exceptions.

---

## 12. When NOT to Use?
- **Do NOT use Exceptions for Standard Logic Flow**: Avoid writing `try...except IndexError` when `if index < len(arr)` is cleaner, faster, and more readable.
- **Do NOT catch exceptions you cannot handle**: If your function cannot recover from an error, let it propagate to a higher-level global exception handler.

---

## 13. Top Interview Questions
1. *What is the difference between `BaseException` and `Exception` in CPython?*
2. *How does the context management protocol work under the hood (`__enter__` and `__exit__`)?*
3. *What is exception chaining (`raise ... from err`), and why is it critical for production debugging?*
4. *What happens if a `return` statement is placed inside a `finally` block while an exception is active?*
5. *How does CPython 3.11+ implement "Zero-Cost Exceptions"?*

---

## 14. Practice Problems
1. Build an atomic file writer context manager (`atomic_write`) that writes to a temporary file and atomically renames it only if no errors occur.
2. Design a custom tiered exception hierarchy for an AI document processing microservice.
3. Implement an asynchronous context manager (`AsyncConnectionPool`) supporting `async with`.

---

## 15. 5-Minute Revision Notes
- Always inherit custom application exceptions from **`Exception`**, never `BaseException`.
- Use **`raise DomainError(...) from err`** to preserve original root-cause tracebacks (`__cause__`).
- In `try...except...else...finally`: `else` runs *only* on success; `finally` runs *unconditionally*.
- Returning inside a `finally` block wipes out active exceptions; never do this!
- Context managers require **`__enter__`** and **`__exit__`**.
- `@contextlib.contextmanager` transforms a generator containing a single `yield` into a context manager.
- Never use exceptions for normal control flow; exceptions are for exceptional events.
