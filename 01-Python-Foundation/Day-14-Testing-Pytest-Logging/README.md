# Day 14: Pytest Suite, Fixtures, Mocking, Coverage & Production Logging

In enterprise backend engineering and production AI systems, untested code is broken code. As architectures scale into distributed microservices and LLM pipelines, automated testing with **Pytest** and structured observability via Python's **Logging** subsystem are what separate fragile prototypes from resilient, multi-million-request enterprise platforms.

---

## 1. The Production Testing Pyramid for Backend & AI Systems

```
              / \
             /   \      E2E Tests (~5%)
            / E2E \     - Full system flows: Client -> API -> LLM -> Vector DB
           /───────\
          /         \   Integration Tests (~25%)
         / Integrate \  - FastAPI routes + PostgreSQL (Testcontainers) + Redis Cache
        /─────────────\
       /               \  Unit Tests (~70%)
      /      Unit       \ - Pydantic models, tokenizers, chunkers, regex, pure math
     /───────────────────\
```

- **Unit Tests**: Blazing fast (< 1ms). Test individual functions, dataclasses, and validators in complete isolation using stubs.
- **Integration Tests**: Fast (10ms–100ms). Test interactions between database sessions, cache layers, and internal service adapters.
- **End-to-End (E2E) Tests**: Slower (seconds). Verify full HTTP request/response lifecycles, user authentication, and critical business paths.

---

## 2. Pytest Architecture vs. Legacy `unittest`

Legacy `unittest` (derived from Java's JUnit in 1999) requires heavy boilerplate:
```python
# Obsolete unittest: Verbose class hierarchy, cumbersome assertions
import unittest

class TestMath(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)
```

**Pytest** uses plain functions and leverages **Python AST assertion rewriting**:
```python
# Modern Pytest: Clean, idiomatic, powerful failure introspection
def test_add():
    assert add(1, 2) == 3
```
When an assertion fails, Pytest inspects the Abstract Syntax Tree (AST) to print detailed intermediate values, diffs, and context without requiring specialized assert methods.

---

## 3. Pytest Fixtures & Dependency Injection

Fixtures provide a clean, declarative dependency injection system:

```python
import pytest
from pathlib import Path

@pytest.fixture
def temp_dataset_file(tmp_path: Path) -> Path:
    # --- SETUP PHASE (Before yield) ---
    file_path = tmp_path / "corpus.jsonl"
    file_path.write_text('{"id": "doc_1", "text": "hello"}\n', encoding="utf-8")
    
    yield file_path  # Provides resource to the test function
    
    # --- TEARDOWN PHASE (After yield) ---
    # Cleanup runs deterministically even if the test fails!
    file_path.unlink(missing_ok=True)
```

---

## 4. Fixture Scopes & Resource Lifecycle

Control how often a fixture is created and destroyed:

| Scope | Lifetime | Best Use Case |
| :--- | :--- | :--- |
| **`function`** (Default) | Recreated for every single test function | Fresh database transactions, isolated test objects. |
| **`class`** | Shared across all methods in a test class | Test classes sharing an initialized client. |
| **`module`** | Shared across all tests in a test file | Reading a static 50 MB test dataset from disk once. |
| **`package`** | Shared across an entire directory package | Subsystem initialization. |
| **`session`** | Created **once** for the entire test run | Initializing Testcontainers (PostgreSQL Docker container). |

---

## 5. Hierarchical Discovery with `conftest.py`

Pytest automatically searches parent directories for files named `conftest.py`:
- Fixtures defined in `conftest.py` are **globally available** to all test files in that directory and subdirectories **without requiring explicit imports**.
- Allows modular isolation: root `conftest.py` provides global settings, while `tests/integration/conftest.py` provides database fixtures.

---

## 6. Data-Driven Testing via `@pytest.mark.parametrize`

Eliminate redundant test functions by running test logic against a matrix of inputs:

```python
import pytest

@pytest.mark.parametrize("prompt, expected_tokens", [
    ("hello world", 2),
    ("attention is all you need", 5),
    ("", 0),
    ("   spaces   ", 1),
])
def test_token_counter(prompt: str, expected_tokens: int):
    assert count_tokens(prompt) == expected_tokens
```

---

## 7. Mocking Fundamentals (`unittest.mock.Mock` & `MagicMock`)

Mocking replaces external dependencies (payment gateways, third-party LLM APIs) with controllable test doubles:

```python
from unittest.mock import MagicMock

# Create a mock with pre-configured return values
mock_llm_client = MagicMock()
mock_llm_client.complete.return_value = {"text": "Vector search is fast.", "tokens": 6}

# Invoke and assert interactions
response = mock_llm_client.complete("Explain RAG")
assert response["tokens"] == 6
mock_llm_client.complete.assert_called_once_with("Explain RAG")
```

---

## 8. The Golden Rule of Patching: "Mock Where It Is Used!"

The most common mistake when using `unittest.mock.patch` is targeting the definition file instead of the consumer:

```python
# Suppose service.py contains:
# from app.clients import OpenAIClient

# ❌ WRONG: Patches original module; service.py still has old reference!
@patch("app.clients.OpenAIClient")

# ✅ CORRECT: Patch the reference where service.py looks it up!
@patch("app.service.OpenAIClient")
```

---

## 9. Async Mocking with `unittest.mock.AsyncMock`

When testing `async def` coroutines, using standard `MagicMock` causes coroutines to return mock objects instead of awaitables, triggering `RuntimeWarning: coroutine was never awaited`.
Always use **`AsyncMock`**:

```python
from unittest.mock import AsyncMock

mock_client = AsyncMock()
mock_client.generate_stream.return_value = ["token1", "token2"]

# Can be cleanly awaited:
result = await mock_client.generate_stream("prompt")
```

---

## 10. Testing Asynchronous Systems with `pytest-asyncio`

Use `pytest-asyncio` to test async FastAPI routes and coroutines:

```python
import pytest

@pytest.mark.asyncio
async def test_async_vector_search():
    client = AsyncVectorClient()
    results = await client.search([0.1, 0.2, 0.3])
    assert len(results) > 0
```

---

## 11. Code Coverage Metrics & Analysis (`pytest-cov`)

```bash
pytest --cov=app --cov-report=term-missing --cov-branch
```
- **Statement Coverage**: Percentage of code lines executed during tests.
- **Branch Coverage (`--cov-branch`)**: Verifies that both `True` and `False` branches of every `if` statement were evaluated.
- **Rule**: Aim for 80%–90% branch coverage on core domain logic. Avoid chasing 100% on trivial boilerplate.

---

## 12. Production Logging Architecture (PEP 282)

Python's built-in `logging` module is organized hierarchically:

```
+--------------------------------------------------------------------------+
|                              Logger Hierarchy                            |
|                                                                          |
|       Root Logger ("")                                                   |
|             │                                                            |
|             ▼                                                            |
|       "app" Logger ─────── Handler: ConsoleHandler (StreamHandler)       |
|             │                                                            |
|             ▼                                                            |
|       "app.services" ───── Handler: FileHandler (RotatingFileHandler)    |
|             │                                                            |
|             ▼                                                            |
|       "app.services.rag" (Inherits handlers & propagates upward)         |
+--------------------------------------------------------------------------+
```

### Logging Levels (Ascending Severity):
`DEBUG (10)` $\to$ `INFO (20)` $\to$ `WARNING (30)` $\to$ `ERROR (40)` $\to$ `CRITICAL (50)`.

Always instantiate loggers using:
```python
logger = logging.getLogger(__name__)
```

---

## 13. Structured JSON Logging for Distributed Systems

Unstructured text logs (`2026-10-08 INFO User logged in`) are impossible to parse and aggregate at scale across 50 microservices.
Production backends emit **Structured JSON Logs**:

```json
{
  "timestamp": "2026-10-08T18:15:26.104Z",
  "level": "INFO",
  "logger": "app.services.llm",
  "trace_id": "c9a0-4821-bf91",
  "event": "llm_completion_generated",
  "model": "gpt-4o",
  "prompt_tokens": 42,
  "completion_tokens": 128,
  "duration_ms": 234.5
}
```
Centralized log aggregators (Datadog, Elasticsearch/Logstash, AWS CloudWatch) index every JSON field, enabling instant SQL-like querying and alerts.

---

## 14. Distributed Trace ID Propagation via `contextvars`

To trace a request across multiple asynchronous functions and microservice boundaries, use Python's **`contextvars`** module:

```python
import contextvars

# Global context variable isolated per async task and thread
request_id_ctx = contextvars.ContextVar("request_id", default="N/A")

# Set at API gateway / middleware:
token = request_id_ctx.set("req_98124_uuid")

# Custom Logging Filter automatically injects request_id into every log record!
```

---

## 15. Performance & Security Best Practices in Logging

1. **Lazy String Formatting**: Never use f-strings in log calls!
   ```python
   # ❌ EXPENSIVE: Evaluates f-string even if DEBUG level is disabled!
   logger.debug(f"Computed matrix: {heavy_matrix_dump()}")

   # ✅ EFFICIENT: Evaluates formatting ONLY if level is enabled
   logger.debug("Computed matrix: %s", heavy_matrix_dump)
   ```
2. **Sensitive Data Redaction**: Filter credit cards, API keys (`sk-***`), and PII before emitting records.
3. **Avoid Re-instantiating Handlers**: Never call `logger.addHandler()` inside functions; configure handlers once at application startup.
