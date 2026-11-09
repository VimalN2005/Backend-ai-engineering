# Rapid Revision & Quick Reference: Async Integration Testing

A condensed 5-minute reference guide covering `pytest-asyncio`, HTTPX `ASGITransport`, SQLAlchemy transactional rollback fixtures, and FastAPI dependency overrides.

---

## 1. Core `pyproject.toml` Configuration

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
filterwarnings = [
    "ignore::DeprecationWarning",
    "ignore::PendingDeprecationWarning",
]
```

---

## 2. Production `conftest.py` Boilerplate

```python
import pytest
import httpx
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db.base import Base
from app.api.deps import get_db

TEST_DB_URL = "sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared&uri=true"

# 1. Session-scoped engine with StaticPool
@pytest.fixture(scope="session")
def engine():
    return create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

# 2. Initialize schema ONCE per session
@pytest.fixture(scope="session", autouse=True)
async def init_schema(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

# 3. Transactional Rollback per test (< 1ms per test)
@pytest.fixture
async def db_session(engine):
    async with engine.connect() as connection:
        trans = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()

# 4. Native HTTPX ASGITransport client bound to test transaction
@pytest.fixture
async def async_client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.pop(get_db, None)
```

---

## 3. Dependency Overrides vs Mocking Matrix

| Dimension | `app.dependency_overrides` | `unittest.mock.patch` |
| :--- | :--- | :--- |
| **Mechanism** | Framework-level DI map replacement | Python module global monkeypatching |
| **Thread & Async Safety** | 100% safe; scoped to FastAPI app | Unsafe; mutates globals across workers |
| **Contract Validation** | Preserves Pydantic schemas and types | Easily drifts from production signatures |
| **Cleanup** | Clean: `app.dependency_overrides.clear()` | Risky: Leaks if teardown encounters errors |

---

## 4. Mocking Outbound HTTP with `MockTransport`

```python
import httpx

def mock_weather_api(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/forecast"
    return httpx.Response(status_code=200, json={"temp_c": 22.5, "condition": "Sunny"})

@pytest.fixture
def mock_weather_client(app):
    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_weather_api),
        base_url="https://api.weather.internal"
    )
    app.dependency_overrides[get_weather_client] = lambda: mock_client
    yield mock_client
    app.dependency_overrides.pop(get_weather_client, None)
```

---

## 5. High-Concurrency Race Condition Test Pattern

```python
import asyncio

async def test_race_condition(async_client, seed_voucher):
    # Fire 10 simultaneous redemption requests
    tasks = [
        async_client.post(f"/api/v1/vouchers/{seed_voucher.id}/redeem")
        for _ in range(10)
    ]
    responses = await asyncio.gather(*tasks)
    
    # Assert exact distribution
    assert sum(1 for r in responses if r.status_code == 200) == 1
    assert sum(1 for r in responses if r.status_code == 409) == 9
```

---

## 6. High-Frequency Interview Flashcards

### Q1: Why is transactional rollback 50x–100x faster than dropping/creating tables?
> **Answer**: DDL operations (`CREATE`/`DROP TABLE`) invalidate database catalog caches, acquire table locks, and force disk flushes. In contrast, rolling back a transaction (`ROLLBACK`) simply discards in-memory uncommitted transaction logs, taking less than 1 millisecond.

### Q2: Why should `session.flush()` be used in test seed fixtures instead of `session.commit()`?
> **Answer**: `session.flush()` executes SQL `INSERT` statements within the open transaction, populating primary keys and validating foreign keys, while keeping the data safely inside the rollbackable transaction. Calling `commit()` permanently writes the rows, breaking test rollback isolation.

### Q3: Why does `sqlite+aiosqlite:///:memory:` fail with "no such table"?
> **Answer**: The standard `:memory:` URI creates a database tied to a single connection. When SQLAlchemy closes or recycles that connection, SQLite immediately destroys the entire database. Use `sqlite+aiosqlite:///file:memdb?mode=memory&cache=shared&uri=true` with `poolclass=StaticPool`.

### Q4: What is the main difference between Starlette's `TestClient` and HTTPX `ASGITransport`?
> **Answer**: `TestClient` executes the ASGI app on a separate background thread via AnyIO portals, breaking database transaction sharing and `contextvars`. HTTPX `ASGITransport` executes cooperatively on the caller's native event loop.
