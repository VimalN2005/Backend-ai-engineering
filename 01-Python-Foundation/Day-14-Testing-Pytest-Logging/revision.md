# Day 14: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before technical interviews and architectural design reviews:

---

- ⚡ **Pytest Assert Introspection:**
  - Pytest rewrites Python AST assertions at import time.
  - Plain `assert a == b` prints full intermediate evaluation trees and diffs without requiring verbose `self.assertEqual` boilerplate.

- ⚡ **Fixture Lifecycle (`yield`):**
  - Code before `yield`: SETUP phase.
  - Code after `yield`: TEARDOWN phase (guaranteed cleanup even if tests fail).
  - Prefer default `scope="function"` for mutable test state to eliminate flaky cross-test pollution. Use `scope="session"` only for immutable heavy infrastructure (Docker, embedding indices).

- ⚡ **The Golden Rule of Patching:**
  - **"Mock where it is used, NOT where it is defined!"**
  - If `app/services.py` imports `from app.clients import AIClient`, patch `@patch("app.services.AIClient")`. Patching `app.clients.AIClient` will fail!

- ⚡ **Async Mocking Contract:**
  - Never use `MagicMock` for coroutines (`async def`).
  - Always use `unittest.mock.AsyncMock`. It returns an awaitable coroutine and tracks `assert_awaited_once_with()`.

- ⚡ **Parametrized Testing (`@pytest.mark.parametrize`):**
  - Tests combinatorial input matrices, edge cases, and boundary constraints in a single clean test function without code duplication.

- ⚡ **Structured JSON Logging:**
  - Plain-text logs are unusable in production.
  - Emit machine-parseable JSON dictionaries containing timestamp, level, logger name, trace ID, tenant ID, and custom event metrics.
  - Indexed automatically by Datadog, Elasticsearch, and CloudWatch.

- ⚡ **Trace ID Propagation with `contextvars`:**
  - In asynchronous event loops, `threading.local` fails because multiple concurrent requests share the same thread.
  - `contextvars.ContextVar` provides task-local storage that propagates down async call stacks to attach request IDs to all log records.

- ⚡ **Coverage Truth:**
  - Branch coverage (`--cov-branch`) verifies both `True` and `False` execution paths.
  - 100% line coverage does **not** guarantee absence of bugs—it measures code execution, not assertion quality or boundary conditions!
