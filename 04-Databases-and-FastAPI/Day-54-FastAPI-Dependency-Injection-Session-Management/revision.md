# Day 54: Rapid Revision & Cheat Sheet - FastAPI Dependency Injection

A high-density 5-minute reference sheet for **FastAPI Dependency Injection (`Depends`)**, session management, yield dependencies, and testing overrides.

---

## 1. Essential Dependency Patterns & PEP 593 Syntax

```python
from typing import Annotated, AsyncGenerator
from fastapi import FastAPI, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

app = FastAPI()

# 1. Functional Sub-Dependency
async def get_auth_token(x_token: Annotated[str, Header()]) -> str:
    if x_token != "secret":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_token

AuthToken = Annotated[str, Depends(get_auth_token)]

# 2. Yield Dependency (Session Lifecycle)
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

DbSession = Annotated[AsyncSession, Depends(get_db)]

# 3. Class-Based Parameterized Dependency
class RoleGuard:
    def __init__(self, role: str):
        self.required_role = role

    def __call__(self, token: AuthToken):
        # Enforce RBAC logic here
        return True

require_admin = RoleGuard(role="ADMIN")

# 4. Clean Route Controller Declaration
@app.get("/users/me")
async def get_current_user(
    db: DbSession,
    token: AuthToken,
    is_admin: Annotated[bool, Depends(require_admin)],
):
    return {"token": token, "status": "ok"}
```

---

## 2. Dependency Caching: `use_cache=True` vs `use_cache=False`

| Configuration | Behavior | Use Case |
| :--- | :--- | :--- |
| **`use_cache=True` (Default)** | Evaluates once per request. Reuses identical return value across all sub-dependencies in the same request. | Database sessions, authenticated users, tenant resolution, settings. |
| **`use_cache=False`** | Evaluates freshly at every injection site, even within the same request. | Non-idempotent tokens, nonces, separate isolated transactions / savepoints. |

```python
# Uncached Dependency Example
RequestId = Annotated[str, Depends(generate_uuid, use_cache=False)]
```

---

## 3. Hermetic Integration Testing with `dependency_overrides`

```python
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.mark.asyncio
async def test_endpoint_with_mock_auth():
    # 1. Define mock dependency
    async def override_auth() -> str:
        return "synthetic_test_token"

    # 2. Apply override to application instance
    app.dependency_overrides[get_auth_token] = override_auth

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/users/me")
            assert resp.status_code == 200
            assert resp.json()["token"] == "synthetic_test_token"
    finally:
        # 3. Always clear overrides after test completes
        app.dependency_overrides.clear()
```

---

## 4. Yield Dependency Teardown Checklist

```
SETUP PHASE (Before yield)
  [ ] Acquire connection from pool (`async with factory() as session:`).
  [ ] Set up schema translation or dynamic tenant parameters.

EXECUTION PHASE (yield)
  [ ] Yield raw session or Unit of Work into route handler.

TEARDOWN PHASE (finally)
  [ ] Catch exceptions, call `await session.rollback()`, and RE-RAISE.
  [ ] Commit on clean exit (`await session.commit()`).
  [ ] Close session in `finally:` block to release socket back to QueuePool.
```

---

## 5. One-Minute Flashcard Review

1. **What is Inversion of Control in FastAPI?** The framework manages the lifecycle, topological resolution, and cleanup of application resources, decoupling route handlers from object creation.
2. **When does code after `yield` execute?** Immediately after the HTTP response has been sent to the client, managed by Python's `AsyncExitStack`.
3. **What happens if an endpoint raises an exception during a yield dependency?** The exception is re-thrown into the generator via `.throw()`. Teardown in `finally:` blocks executes 100% of the time.
4. **Why use `typing.Annotated` for dependencies?** It keeps Python types clean for static type checkers (`mypy`), enables reusable domain aliases, and simplifies pure unit testing without framework mocks.
5. **How does `dependency_overrides` work?** It maps a dependency callable to a test double on the `FastAPI` instance, allowing clean testing without monkey-patching global module imports.
