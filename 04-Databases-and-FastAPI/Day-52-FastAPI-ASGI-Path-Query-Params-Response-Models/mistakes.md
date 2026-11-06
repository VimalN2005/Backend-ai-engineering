# Day 52: Critical FastAPI Antipatterns & Architectural Pitfalls

This document explores the top 7 architectural mistakes engineers make when building applications with **FastAPI**, Starlette ASGI, path/query parameter parsing, and Pydantic response models. Each antipattern includes its underlying failure mechanism, bad code vs remediated code, and production rules.

---

## 1. Calling Synchronous Blocking Code Inside `async def` Route Handlers

### Failure Mechanism
FastAPI routes declared with `async def` are executed directly on the application's single `asyncio` event loop thread. 

When you call a blocking synchronous function (such as `time.sleep()`, `requests.get()`, `open().read()`, or a synchronous database query via `psycopg2`) inside an `async def` handler, **the entire event loop freezes**. The server cannot accept new TCP sockets, process pending coroutines, or serve other connected clients until that blocking operation finishes!

### Bad Practice
```python
# CATASTROPHIC FREEZE: Blocks the entire ASGI event loop!
import time
import requests

@app.get("/api/v1/weather")
async def get_weather(city: str):
    # time.sleep and requests.get block the thread!
    time.sleep(2.0)
    response = requests.get(f"https://api.weather.com/{city}")
    return response.json()
```

### Remediated Practice
```python
# Option A: Use non-blocking async libraries with await
import asyncio
import httpx

@app.get("/api/v1/weather")
async def get_weather(city: str):
    await asyncio.sleep(2.0)  # Yields control back to event loop
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.weather.com/{city}")
        return response.json()

# Option B: If the library is strictly synchronous, declare endpoint as `def`!
# Starlette will automatically offload it to a background worker thread pool!
@app.get("/api/v1/legacy-weather")
def get_legacy_weather(city: str):
    time.sleep(2.0)  # Safe: runs in a separate thread pool (anyio.to_thread)
    response = requests.get(f"https://api.weather.com/{city}")
    return response.json()
```

---

## 2. Returning Raw Database Entities Without `response_model` Filtering

### Failure Mechanism
When endpoints return ORM models or database dictionaries directly without specifying a `response_model`, FastAPI serializes all dictionary keys or instance attributes directly into the outbound JSON payload.

If your database model contains sensitive fields such as `hashed_password`, `two_factor_secret`, `internal_risk_score`, or deleted-flag metadata, those fields are **leaked over the public internet** (CWE-200: Exposure of Sensitive Information).

### Bad Practice
```python
# SECURITY LEAK: Exposes internal database fields to the public API
@app.get("/api/v1/users/{user_id}")
async def get_user(user_id: int):
    user = await db.get_user(user_id)
    # user dict contains: {"id": 1, "email": "admin@cloud.io", "hashed_password": "...", "stripe_token": "..."}
    return user  # Leaks hashed_password and stripe_token!
```

### Remediated Practice
```python
from pydantic import BaseModel, EmailStr

class UserPublicResponse(BaseModel):
    id: int
    email: EmailStr
    # hashed_password and stripe_token are strictly omitted!

@app.get(
    "/api/v1/users/{user_id}",
    response_model=UserPublicResponse, # Enforces strict outbound sanitization
)
async def get_user(user_id: int):
    user = await db.get_user(user_id)
    return user # FastAPI filters output through UserPublicResponse automatically
```

---

## 3. Defining Mutable Default Values in Query Parameters

### Failure Mechanism
In standard Python functions, default values are evaluated once at module load time. Assigning a mutable default such as `tags: list[str] = []` without wrapping it in `Query(default_factory=list)` can lead to unintended shared state in certain frameworks or fail strict Pydantic V2 schema validations.

In FastAPI, while `tags: list[str] = []` is tolerated for query parameter lists, omitting `Query(...)` metadata prevents specifying descriptions, validation constraints, and examples in OpenAPI.

### Bad Practice
```python
# Missing Query parameter annotations and documentation
@app.get("/items")
async def list_items(tags: list[str] = []):
    return {"tags": tags}
```

### Remediated Practice
```python
from fastapi import Query
from typing import Annotated

# Explicit typing with Annotated and default factory or empty list
@app.get("/items")
async def list_items(
    tags: Annotated[
        list[str], 
        Query(
            description="Filter items by multiple categories",
            min_length=1,
            max_length=50,
            examples=["electronics", "books"],
        )
    ] = []
):
    return {"tags": tags}
```

---

## 4. Relying on Deprecated `@app.on_event("startup")` Lifecycle Hooks

### Failure Mechanism
Older FastAPI applications used `@app.on_event("startup")` and `@app.on_event("shutdown")`. These decorators:
1. Are officially deprecated in Starlette and FastAPI.
2. Have no clean exception-boundary sharing: if an error occurs halfway through startup, shutdown handlers do not execute cleanly.
3. Cannot cleanly share typed state via `app.state` with deterministic typing.

### Bad Practice
```python
# DEPRECATED: Fragmented startup and shutdown handlers
@app.on_event("startup")
async def startup_event():
    app.state.pool = await create_db_pool()

@app.on_event("shutdown")
async def shutdown_event():
    await app.state.pool.close()
```

### Remediated Practice
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Unified startup context
    app.state.pool = await create_db_pool()
    try:
        yield
    finally:
        # Guaranteed cleanup on shutdown, even during abrupt cancellation
        await app.state.pool.close()

app = FastAPI(lifespan=lifespan)
```

---

## 5. Parameter Disambiguation: Path vs Query Collisions

### Failure Mechanism
FastAPI determines whether a parameter is a **Path parameter** or a **Query parameter** by checking if its name appears inside `{...}` in the route decorator URL string.

If a developer declares a `Path(...)` metadata on an argument whose name is **not** in the URL template, FastAPI raises a startup error or falls back to an ambiguous query parameter. Conversely, if a path parameter lacks the curly braces in the route string, it becomes a query parameter!

### Bad Practice
```python
# BUG: Route string has {id}, but handler argument is named user_id!
# Result: 'id' is unmatched in the function, and 'user_id' becomes a QUERY parameter!
@app.get("/users/{id}")
async def get_user(user_id: int):
    return {"user_id": user_id}
# Accessing /users/42 returns 422 Unprocessable Entity because user_id query param is missing!
```

### Remediated Practice
```python
# Parameter name in route decorator must match the function argument EXACTLY:
@app.get("/users/{user_id}")
async def get_user(user_id: Annotated[int, Path(ge=1)]):
    return {"user_id": user_id}
```

---

## 6. Using Legacy Parameter Default Assignment Instead of `Annotated`

### Failure Mechanism
In older FastAPI code, parameters were defined using default values:
`async def search(q: str = Query(..., min_length=3)): ...`

**The Architectural Problems**:
- Static type checkers (`mypy`, `pyright`) interpret `str = Query(...)` as assigning an instance of `FieldInfo` to a variable typed as `str`, which is technically a type violation.
- Calling the function directly in unit tests without FastAPI (`search("query")`) works, but default parameters cannot be passed without manually providing the `Query` object.
- Reusing common types across endpoints requires duplicating metadata.

### Bad Practice
```python
# Legacy syntax: Breaks pure Python type semantics
async def search(
    query: str = Query(..., min_length=3, max_length=50),
    page: int = Query(default=1, ge=1),
):
    ...
```

### Remediated Practice
```python
from typing import Annotated
from fastapi import Query

# Define reusable, PEP 593 compliant type aliases
SearchQuery = Annotated[str, Query(min_length=3, max_length=50, description="Search keyword")]
PageNumber = Annotated[int, Query(ge=1, description="Page index")]

async def search(
    query: SearchQuery,
    page: PageNumber = 1,
):
    ...
```

---

## 7. Throwing Generic Unhandled Exceptions Instead of `HTTPException`

### Failure Mechanism
When business logic raises an unhandled standard Python exception (e.g., `raise ValueError("Account suspended")`), FastAPI catches it at the top-level server middleware and returns a generic `500 Internal Server Error`.

This:
1. Masks operational errors as catastrophic server crashes in monitoring tools (Datadog/Sentry).
2. Conceals helpful validation or business logic feedback from the API client.
3. Violates RESTful HTTP specifications.

### Bad Practice
```python
@app.post("/transfer")
async def transfer_funds(account_id: int, amount: float):
    if amount <= 0:
        # BUG: Returns raw 500 Internal Server Error to client!
        raise ValueError("Transfer amount must be positive.")
```

### Remediated Practice
```python
from fastapi import HTTPException, status

@app.post("/transfer")
async def transfer_funds(account_id: int, amount: float):
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_AMOUNT",
                "message": "Transfer amount must be strictly greater than zero.",
                "provided_amount": amount,
            },
        )
```

---
*Adhering to these rules guarantees robust, secure, and lightning-fast FastAPI microservices.*
