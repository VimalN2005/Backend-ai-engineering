# Day 14: Common Testing & Logging Antipatterns

Avoid these 7 production bugs when writing automated test suites and configuring observability pipelines.

---

### 1. Mock Drift (Mocking External APIs Without Contract Validation)
- **Root Cause**: Creating mock responses with hand-written dictionaries that do not reflect upstream changes.
- **Consequence**: When OpenAI, Stripe, or a third-party vector database updates their API schema, your local unit tests continue to pass 100%, but production crashes immediately with `KeyError` or `ValidationError`.

```python
# ❌ DANGEROUS: Hand-crafted mock that easily drifts from reality
mock_client.get_embedding.return_value = {"vector": [0.1, 0.2]}  # Upstream might use "data": [{"embedding": [...]}]

# ✅ PRODUCTION PATTERN: Validate mock return values against Pydantic schema contracts
from app.schemas import EmbeddingResponse

mock_client.get_embedding.return_value = EmbeddingResponse(
    data=[{"embedding": [0.1, 0.2]}], model="text-embedding-3"
).model_dump()
```

---

### 2. Using `print()` Instead of Structured Logging
- **Root Cause**: Relying on quick `print("user logged in", user_id)` statements in backend code.
- **Consequence**: `print()` outputs unformatted plain text directly to standard output. It lacks timestamps, log levels (INFO/ERROR), trace IDs, and machine-parseable JSON keys. Centralized log indexers (Datadog, CloudWatch) cannot query or alert on `print()` output.

```python
# ❌ UNUSABLE IN PRODUCTION:
print(f"Failed to query database for user {user_id}")

# ✅ PRODUCTION PATTERN: Structured JSON logging with trace context
logger.error(
    "Failed to query database.",
    extra={"user_id": user_id, "error_code": "DB_TIMEOUT"}
)
```

---

### 3. Re-Instantiating Log Handlers Inside Functions (Duplicate Log Lines)
- **Root Cause**: Adding handlers inside a function or class `__init__` instead of at the root module configuration.
- **Consequence**: Every time the function executes or a class is instantiated, a new handler is attached to the logger. A single log statement will print 2, 4, 8, 16 times as requests accumulate!

```python
# ❌ DUPLICATE LOG LINES BUG: Adds handler on every request
def handle_request():
    logger = logging.getLogger("api")
    logger.addHandler(logging.StreamHandler())  # Memory leak & duplicate logs!
    logger.info("Processing request...")

# ✅ PRODUCTION PATTERN: Configure handlers ONCE at application entry point
logger = logging.getLogger(__name__)  # Inherits handlers from root configuration
```

---

### 4. Leaking State Across Tests via Mutable Module-Scoped Fixtures
- **Root Cause**: Defining `@pytest.fixture(scope="module")` or `scope="session"` that returns a mutable dictionary, list, or database session.
- **Consequence**: One test mutates the shared fixture (e.g. appends an item or deletes a row). Subsequent tests in the suite fail intermittently, causing frustrating, order-dependent flaky tests.

```python
# ❌ FLAKY TEST HAZARD: Shared mutable state across all tests in file
@pytest.fixture(scope="module")
def shared_cache():
    return {"counter": 0}  # Test A mutates this, breaking Test B!

# ✅ PRODUCTION PATTERN: Use default function scope for mutable test state
@pytest.fixture(scope="function")
def fresh_cache():
    return {"counter": 0}  # Guaranteed fresh, isolated instance per test
```

---

### 5. Using `MagicMock` Instead of `AsyncMock` for Coroutines
- **Root Cause**: Mocking an `async def` function with a standard `MagicMock`.
- **Consequence**: Calling the mock returns an immediate `MagicMock` object rather than an awaitable coroutine. When code executes `await client.fetch()`, CPython throws `TypeError: object MagicMock can't be used in 'await' expression` or emits `RuntimeWarning: coroutine was never awaited`.

```python
# ❌ CRASHES ON AWAIT:
from unittest.mock import MagicMock
client = MagicMock()
await client.fetch_data()  # TypeError!

# ✅ PRODUCTION PATTERN: Use AsyncMock for coroutines
from unittest.mock import AsyncMock
client = AsyncMock()
await client.fetch_data()  # Cleanly awaited
```

---

### 6. Over-Mocking (Testing the Mock Instead of the Code)
- **Root Cause**: Mocking out every single helper function, validator, and internal class until the test simply verifies that mocked functions were called in a specific sequence.
- **Consequence**: The test provides zero real confidence. If the underlying logic breaks, the test still passes because all real logic was mocked away.

```python
# ❌ VALUELESS TEST: Mocks out internal string helper
@patch("app.utils.sanitize_string")
def test_user_creation(mock_sanitize):
    mock_sanitize.return_value = "clean"
    # Testing that mock was called rather than testing if string is actually sanitized!

# ✅ PRODUCTION PATTERN: Mock only external network I/O; test domain logic for real
def test_user_creation():
    user = create_user(raw_name="  Alice  ")
    assert user.name == "Alice"  # Tests real business logic
```

---

### 7. Plaintext Logging of Sensitive PII and API Secrets
- **Root Cause**: Logging raw request headers or database entities containing JWT tokens, passwords, credit cards, or OpenAI API keys (`sk-...`).
- **Consequence**: Violates GDPR, SOC 2, and PCI-DSS compliance. Any developer or log aggregation tool with access to CloudWatch can view raw user passwords and credentials.

```python
# ❌ COMPLIANCE VIOLATION: Exposes API secret in logs
logger.info("Initializing provider with config: %s", {"api_key": "sk-proj-98214..."})

# ✅ SECURE PATTERN: Redact secrets before logging
def sanitize_log_dict(d: dict) -> dict:
    return {k: ("***REDACTED***" if "key" in k or "token" in k else v) for k, v in d.items()}

logger.info("Initializing provider with config: %s", sanitize_log_dict(config))
```
