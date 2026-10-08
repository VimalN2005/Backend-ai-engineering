# Day 14: Top 5 Technical Interview Questions

---

### Question 1: How does Pytest's fixture dependency injection system work under the hood, and how do you choose fixture scopes?

#### Expected Answer:
Pytest treats test functions and fixtures as a directed acyclic graph (DAG) of dependencies:
1. **Introspection & Resolution**:
   When Pytest collects a test function, it inspects the function's parameter names via `inspect.signature`. It searches for fixtures with matching names across the current file, `conftest.py` files in the hierarchy, and installed plugins.
2. **Lifecycle Execution (`yield`)**:
   Fixtures run code before `yield` during setup. When the test completes (or fails), Pytest executes the code after `yield` in reverse dependency order (LIFO), guaranteeing cleanup.
3. **Fixture Scoping Trade-offs**:
   - **`function` (Default)**: Creates a completely fresh instance per test. Maximum test isolation and zero state pollution, but slow if creating heavy resources (e.g. database connection pools).
   - **`session`**: Created once for the entire test suite. Extremely fast for heavy shared infrastructure (e.g., spinning up a Testcontainers PostgreSQL container or loading a 1 GB embedding index into memory).
   - **Risk**: Session fixtures must remain strictly read-only; if a test mutates a session-scoped fixture, subsequent tests will suffer from flaky, order-dependent failures.

---

### Question 2: Explain the patching rule: "Mock where the object is looked up, not where it is defined." What happens if you patch the definition?

#### Expected Answer:
In Python, `import` statements bind an object to a name in the **importing module's namespace**:

Suppose `app/clients.py` defines `class OpenAIClient: ...`
And `app/services.py` contains:
```python
from app.clients import OpenAIClient

def generate_text():
    client = OpenAIClient()
    return client.complete()
```

1. When `services.py` executes, it copies the reference of `OpenAIClient` from `app.clients` into its own local namespace (`app.services.OpenAIClient`).
2. If you patch the original location:
   ```python
   @patch("app.clients.OpenAIClient")  # ❌ INCORRECT!
   ```
   `unittest.mock.patch` modifies the attribute in `app.clients`. However, `app.services` **already holds a direct reference to the unpatched original class**!
3. Therefore, `services.generate_text()` continues to call the real, unpatched class.
4. **The Rule**: Always patch the namespace where the consumer module looks up the symbol:
   ```python
   @patch("app.services.OpenAIClient")  # ✅ CORRECT!
   ```

---

### Question 3: How do you test asynchronous code reliably, and why is `AsyncMock` required instead of standard `MagicMock`?

#### Expected Answer:
When testing asynchronous coroutines (`async def`):
1. **The Flaw of `MagicMock`**:
   Calling a standard `MagicMock` returns another `MagicMock` synchronously. When the code under test executes `await client.fetch()`, Python attempts to inspect the object's `__await__` method. Because `MagicMock` is not an awaitable coroutine, CPython raises `TypeError: object MagicMock can't be used in 'await' expression` or emits `RuntimeWarning: coroutine was never awaited`.
2. **How `AsyncMock` Works**:
   Introduced in Python 3.8, `AsyncMock` implements the async protocol:
   - When called, it returns a coroutine object that can be cleanly awaited.
   - It tracks async-specific assertions: `assert_awaited()`, `assert_awaited_once_with()`, `await_count`.
3. **Execution with `pytest-asyncio`**:
   Decorating tests with `@pytest.mark.asyncio` instructs the runner to execute the test coroutine inside an isolated event loop, ensuring full async lifecycle testing.

---

### Question 4: Why is Structured JSON Logging preferred in distributed microservices, and how does `contextvars` enable trace ID propagation?

#### Expected Answer:
In modern cloud architectures (Kubernetes, AWS ECS):
1. **Machine-Parseable Schema**:
   Unstructured text logs (`[INFO] 2026-10-08 Query took 45ms`) require complex regex parsing in log aggregators (Datadog, Elasticsearch, CloudWatch). Structured JSON logs emit key-value pairs (`{"level": "INFO", "duration_ms": 45, "user_id": 101}`), allowing indexing, dashboards, and automated threshold alerts with zero regex parsing overhead.
2. **Distributed Tracing via `contextvars`**:
   - In asynchronous applications (FastAPI/asyncio), thousands of requests interleave within a single OS thread. Traditional thread-local storage (`threading.local`) fails because multiple concurrent requests share the same thread.
   - Python's `contextvars` module provides **Context-Local Storage** that automatically flows down the asynchronous call stack per coroutine.
   - Setting a `trace_id` at the API middleware allows a custom Logging Filter or Formatter to extract and inject that exact `trace_id` into every log statement emitted during that request, stitching together logs across asynchronous pipelines.

---

### Question 5: What is the difference between Statement Coverage and Branch Coverage, and why can 100% coverage still fail in production?

#### Expected Answer:
1. **Statement (Line) Coverage**:
   Measures the percentage of lines of code that were executed during the test run.
2. **Branch Coverage (`pytest --cov-branch`)**:
   Measures whether every possible branch of conditional control structures (e.g. both the `if True` branch and the implicit/explicit `else` branch) was executed.

#### Why 100% Coverage Can Still Fail:
- **Boundary & Type Corner Cases**: A function `def divide(a, b): return a / b` can have 100% line coverage with a single test `divide(10, 2) == 5`, yet fail in production when `b = 0` (`ZeroDivisionError`) or `b = "str"` (`TypeError`).
- **Concurrency & Race Conditions**: Coverage tools do not detect race conditions or deadlocks across threads or async tasks.
- **External Dependency Drift**: Mocks can achieve 100% coverage while hiding breaking schema changes from third-party APIs.
- **Coverage measures which code ran, NOT whether the assertions were meaningful!**
