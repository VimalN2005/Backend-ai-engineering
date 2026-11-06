# Day 52: Rapid Revision & Cheat Sheet - FastAPI ASGI, Params & Response Models

A high-density 5-minute reference sheet for **FastAPI**, Starlette ASGI internals, request parameters, response models, and concurrency best practices.

---

## 1. Route & Parameter Declaration Matrix

```python
from typing import Annotated, List, Optional
from fastapi import FastAPI, Path, Query, Header, Cookie, Body, status
from pydantic import BaseModel, Field

app = FastAPI()

# 1. Path Parameter: Extracted from {user_id} in URL template
UserId = Annotated[int, Path(ge=1, le=1_000_000, description="User Primary Key")]

# 2. Query Parameter: Primitive scalar not in URL template
PageNumber = Annotated[int, Query(ge=1, description="Page index")]
TagsFilter = Annotated[List[str], Query(description="Multi-value list: ?tags=a&tags=b")]

# 3. Header Parameter: Automatically maps hyphens to underscores (X-API-Key -> x_api_key)
ApiKeyHeader = Annotated[str, Header(alias="X-API-Key")]

# 4. Cookie Parameter: Extracted from request HTTP Cookie header
SessionCookie = Annotated[Optional[str], Cookie(description="Session ID")]

# 5. Body Parameter: Complex Pydantic schema parsed from JSON
class UserCreatePayload(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str
```

---

## 2. Response Model Security & Serialization

```python
from pydantic import BaseModel, ConfigDict

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True) # Enables ORM mode compatibility

    id: int
    username: str
    email: str
    # Password hashes and internal cost fields omitted!

@app.get(
    "/users/{user_id}",
    response_model=UserResponse,
    response_model_exclude_none=True,   # Drops fields whose value is None
    response_model_exclude_unset=True,  # Drops default fields that were not explicitly set
    status_code=status.HTTP_200_OK,
)
async def get_user(user_id: UserId):
    return internal_db_record # FastAPI filters through UserResponse automatically
```

---

## 3. Concurrency Decision Matrix: `async def` vs `def`

```
                      Does your route handler call
                       blocking synchronous I/O?
                                 |
                 +---------------+---------------+
                 |                               |
                YES                              NO
                 |                               |
       Declare as `def`                 Declare as `async def`
                 |                               |
    Starlette offloads to a             Runs directly on the
   worker threadpool automatically      main event loop thread
     (anyio.to_thread.run_sync)         (await non-blocking coroutines)
```

- **Use `async def` for**: `await httpx.get()`, `await db.execute()` (asyncpg/aiosqlite), `await redis.get()`, `await asyncio.sleep()`.
- **Use `def` for**: `requests.get()`, `time.sleep()`, synchronous `psycopg2`, CPU-bound image manipulation or ML numpy arrays.

---

## 4. Modern Lifespan Setup

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP: Connect pools, load models
    app.state.pool = await create_db_pool()
    try:
        yield
    finally:
        # SHUTDOWN: Guaranteed resource cleanup
        await app.state.pool.close()

app = FastAPI(lifespan=lifespan)
```

---

## 5. Standard RFC 7807 Exception Handlers

```python
from fastapi import Request, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "type": "https://api.io/errors/validation-error",
            "title": "Invalid Request Parameters",
            "status": 422,
            "errors": exc.errors(),
        },
    )
```

---

## 6. One-Minute Flashcard Review

1. **What is ASGI?** Asynchronous Server Gateway Interface (3.0 specification), enabling async Python servers to handle WebSockets, SSE, and concurrent HTTP requests via a single event loop.
2. **Why use `typing.Annotated` in FastAPI?** Keeps Python types standard for static type checkers (`mypy`), enables reusable parameter aliases, and simplifies pure unit testing.
3. **What is the primary role of `response_model`?** Acts as a strict security filter, serializing output data through a defined Pydantic schema and stripping internal or sensitive attributes.
4. **Why is `time.sleep()` in `async def` fatal?** Freezes the single `asyncio` event loop thread, stopping the entire server from processing any concurrent requests.
5. **How does boolean query coercion work?** Values like `?is_active=1`, `?is_active=true`, `?is_active=yes` parse to `True`; `?is_active=0`, `?is_active=false` parse to `False`.
