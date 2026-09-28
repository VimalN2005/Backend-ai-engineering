# Day 07: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before interviews or architectural reviews:

---

- ⚡ **Zero-Cost Exceptions (Python 3.11+):**
  - Modern CPython uses static compiler tables (`co_exceptiontable`) mapping bytecode offsets to handlers.
  - `try` blocks execute with **zero runtime overhead** when no exceptions are raised.
  - Raising an exception remains expensive (~1–3 µs) due to stack frame inspection and traceback construction.

- ⚡ **BaseException vs. Exception:**
  - Never catch `BaseException` in business logic.
  - `BaseException` is the parent of critical system signals: `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit`.
  - Always inherit custom domain errors from `Exception` (or a sub-hierarchy like `AppError(Exception)`).

- ⚡ **The Complete Lifecycle (`try-else-finally`):**
  - `try`: Run protected operations. Keep this block as minimal as possible (1–2 lines).
  - `except`: Handle specific errors. Avoid naked `except:`.
  - `else`: Runs **only if no exception occurred** in `try`. Put code that should not be guarded by the except block here.
  - `finally`: Guaranteed execution for resource cleanup. **Never put `return` or `raise` inside `finally`** as it silences all unhandled exceptions.

- ⚡ **Explicit Chaining (`from`):**
  - `raise CustomError("msg") from original_exc`: Sets `__cause__`, creating a clear, professional debugging breadcrumb trail.
  - `raise CustomError("msg") from None`: Explicitly suppresses `__context__` to hide internal tracebacks and prevent leaky abstractions in public APIs.

- ⚡ **Context Manager Protocol (`with`):**
  - Calls `manager.__enter__()`. Return value is assigned to `as target`.
  - On exit, calls `manager.__exit__(exc_type, exc_val, exc_tb)`.
  - If an exception occurred:
    - Returning `True` **suppresses** the exception.
    - Returning `False` (or `None`) **propagates** the exception outward.

- ⚡ **The `@contextmanager` Protocol:**
  - Turns a generator into a context manager.
  - Everything before `yield` runs in `__enter__`.
  - The yielded value binds to `as var`.
  - Everything after `yield` runs in `__exit__`.
  - **Rule**: Always wrap `yield` in a `try...finally` block to guarantee cleanup even if the caller throws inside the with-block.

- ⚡ **Multi-Resource Coordination (`contextlib.ExitStack`):**
  - Use `ExitStack` when managing dynamic or variable numbers of context managers simultaneously.
  - Automatically handles clean teardowns in reverse LIFO order even if an intermediate resource fails during acquisition.
