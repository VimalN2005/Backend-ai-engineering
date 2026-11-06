# Day 52: FastAPI Basics: ASGI, Path/Query Params & Response Models

Welcome to Day 52 of the Enterprise Backend & AI Engineering curriculum. Today, we transition into modern asynchronous web frameworks with **FastAPI**. In high-performance backend systems, asynchronous Python web APIs have superseded traditional synchronous frameworks (such as standard Django or Flask) for high-throughput, I/O-bound microservices, AI inference proxies, and real-time streaming architectures.

FastAPI achieves this by uniting two battle-tested primitives:
1. **Starlette**: The high-performance Asynchronous Server Gateway Interface (ASGI) foundation handling routing, HTTP parsing, WebSockets, and middleware.
2. **Pydantic**: The data validation and serialization engine powered by compiled Rust core binaries (Pydantic V2).

This guide provides an exhaustive 15-point architectural breakdown of ASGI mechanics, request parameter parsing, response filtering, and concurrency behaviors in FastAPI.

---

## 1. The Evolution from WSGI to ASGI

To understand why FastAPI is fast and scalable, we must contrast Python's two server-application gateway specifications:

```
+---------------------------------------------------------------------------------+
|                               WSGI (PEP 3333)                                   |
| Synchronous, single-callable: def application(environ, start_response)          |
| 1 Thread = 1 Request. Blocked thread waits for DB/Network I/O.                  |
| High memory footprint under 10,000 concurrent socket connections.              |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
|                               ASGI (ASGI 3.0)                                   |
| Asynchronous, coroutine-based: async def app(scope, receive, send)              |
| Single event loop thread multiplexes thousands of concurrent connections.      |
| Native support for HTTP/1.1, HTTP/2, WebSockets, and Server-Sent Events (SSE).  |
+---------------------------------------------------------------------------------+
```

### Why WSGI Stalls Under Scale
In a WSGI application (Gunicorn + Flask/Django), an incoming HTTP request monopolizes an operating system worker thread until the entire response is returned. If a database query or an external LLM API call takes 2 seconds, that thread sits completely idle doing zero CPU work. Supporting 1,000 concurrent requests requires 1,000 OS threads or processes, incurring hundreds of megabytes in context-switching overhead and memory consumption.

### The ASGI Solution
ASGI decouples socket connection management from application processing. While an endpoint waits for an asynchronous database query (`await db.execute(...)`), execution yields back to the Python event loop, allowing the server to accept, parse, and handle thousands of other incoming HTTP requests concurrently.

---

## 2. ASGI Architecture & the Event Loop

At its lowest level, every FastAPI application is an ASGI 3.0 callable matching this signature:

```python
async def app(scope: dict, receive: callable, send: callable) -> None:
    ...
```

### The Three ASGI Primitives
- **`scope`**: A Python dictionary describing the incoming connection. It persists for the lifetime of the connection. For an HTTP request, `scope` includes:
  - `type`: `"http"` or `"websocket"`.
  - `method`: `"GET"`, `"POST"`, etc.
  - `path`: `"/api/v1/orders/100"`.
  - `headers`: List of 2-tuples containing raw byte headers: `[(b"host", b"api.io"), ...]`.
  - `query_string`: Raw URL query bytes: `b"page=2&limit=50"`.
- **`receive`**: An async callable (`await receive()`) invoked by the application to read chunks of incoming request data (e.g., request body bytes for file uploads or JSON payloads).
- **`send`**: An async callable (`await send(message)`) invoked by the application to transmit messages back to the server:
  - First, sending response metadata: `{"type": "http.response.start", "status": 200, "headers": [...]}`.
  - Next, streaming the response body: `{"type": "http.response.body", "body": b'{"status":"ok"}', "more_body": False}`.

FastAPI wraps this low-level streaming interface into declarative Python functions using Starlette's routing framework.

---

## 3. Starlette & Pydantic: The Twin Engines of FastAPI

FastAPI is not a ground-up reimplementation of an HTTP server; it is an architectural orchestration layer combining:

```
+-----------------------------------------------------------------------------+
|                                   FastAPI                                   |
|  - Automatic OpenAPI / Swagger Documentation Generation                     |
|  - Parameter Parsing (Path, Query, Header, Cookie, Body)                   |
|  - Dependency Injection System (`Depends`)                                  |
|  - Security & OAuth2 Utilities                                              |
+-----------------------------------------------------------------------------+
               |                                             |
               v                                             v
+-------------------------------+             +-------------------------------+
|           Starlette           |             |          Pydantic V2          |
|  - ASGI Toolkit               |             |  - Rust-backed Core           |
|  - High-performance Routing   |             |  - High-speed Data Parsing    |
|  - Middleware Pipeline        |             |  - Strict Validation Rules    |
|  - WebSocket Protocol Support |             |  - JSON Schema Serialization  |
+-------------------------------+             +-------------------------------+
```

When a request arrives:
1. Starlette routes the URL and initializes the request context.
2. FastAPI extracts arguments from URL paths, query parameters, headers, and JSON bodies.
3. Pydantic validates, coerces types, and sanitizes input data.
4. The route handler function executes.
5. Pydantic filters the returned Python dictionary/model against the route's `response_model`, serializing it to JSON.

---

## 4. FastAPI Application Lifecycle (`lifespan`)

In modern FastAPI (0.93+), application startup and shutdown events are managed via **lifespan async context managers**, replacing the deprecated `@app.on_event("startup")` decorators:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP: Executed before accepting incoming traffic
    # Initialize connection pools, load ML model weights, connect Redis
    print("[LIFESPAN] Starting up: initializing database connection pool...")
    app.state.db_pool = await init_db_pool()
    app.state.ml_model = load_embedding_model()
    
    yield  # Application runs and handles requests here
    
    # SHUTDOWN: Executed after application stops accepting traffic
    # Drain connection pools, flush log buffers, disconnect sockets
    print("[LIFESPAN] Shutting down: closing connection pools...")
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)
```

### Why `lifespan` is Superior
1. **Single Context**: Startup and shutdown code live in a unified Python block with guaranteed exception cleanup (`try...finally`).
2. **Type Safety & Shared State**: State initialized before `yield` can be cleanly attached to `app.state` and accessed inside route dependencies.

---

## 5. Path Parameters & Routing Dynamics

Path parameters extract dynamic values from the URL path. FastAPI parses parameters declared with `{param_name}` in the route decorator:

```python
from fastapi import FastAPI, Path
from typing import Annotated

app = FastAPI()

@app.get("/api/v1/organizations/{org_id}/departments/{dept_code}")
async def get_department(
    org_id: Annotated[int, Path(title="Organization ID", ge=1, le=1_000_000)],
    dept_code: Annotated[str, Path(title="Department Code", min_length=2, max_length=10, regex=r"^[A-Z0-9_-]+$")],
):
    return {"organization_id": org_id, "department_code": dept_code}
```

### Automatic Type Coercion & Validation
If a client sends `GET /api/v1/organizations/abc/departments/AI`, FastAPI automatically rejects the request with HTTP `422 Unprocessable Entity` because `'abc'` cannot be coerced to an integer.

### Advanced Path Converters
Starlette allows declaring path parameter types directly in the route string:
- `/files/{file_path:path}`: Captures the entire remaining path including forward slashes (`/files/static/images/logo.png`).

---

## 6. Query Parameters: Filtering, Pagination & Coercion

Any function parameter that is **not** matched in the path template and is a primitive scalar (e.g., `int`, `str`, `bool`) is interpreted as a **query parameter**:

```python
from fastapi import FastAPI, Query
from typing import Annotated, Optional

app = FastAPI()

@app.get("/api/v1/products")
async def list_products(
    page: Annotated[int, Query(ge=1, description="Page index")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    search: Annotated[Optional[str], Query(max_length=50)] = None,
    tags: Annotated[list[str], Query(description="Filter by multiple tags")] = [],
    is_active: Annotated[bool, Query(description="Filter active products")] = True,
):
    return {
        "pagination": {"page": page, "page_size": page_size},
        "search": search,
        "tags": tags,
        "is_active": is_active,
    }
```

### Boolean Query Coercion
FastAPI parses multiple URL representations of booleans automatically:
- `?is_active=1`, `?is_active=true`, `?is_active=True`, `?is_active=on`, `?is_active=yes` $\implies$ `True`
- `?is_active=0`, `?is_active=false`, `?is_active=no`, `?is_active=off` $\implies$ `False`

### Multi-Value Query Parameters
Calling `GET /api/v1/products?tags=electronics&tags=audio` parses into `tags = ["electronics", "audio"]`.

---

## 7. The `Annotated` Pattern & Modern Type Syntax (PEP 593)

Prior to FastAPI 0.95, parameters were configured using default value assignment:
```python
# Legacy Syntax (Pre-0.95)
async def list_items(page: int = Query(default=1, ge=1)):
    ...
```

### Why Modern FastAPI Recommends `typing.Annotated`
```python
# Modern Idiomatic Syntax (FastAPI 0.95+ / PEP 593)
async def list_items(page: Annotated[int, Query(ge=1)] = 1):
    ...
```

### Critical Advantages
1. **Static Analysis & Mypy Compatibility**: In the legacy syntax, static type checkers see `page: int = Query(...)`. To `mypy`, `Query(...)` is not an `int`, requiring custom plugin hacks. With `Annotated[int, ...]`, the type is unequivocally `int`.
2. **Reusability & Clean Code**: Type aliases can be defined once and reused across hundreds of endpoints:
   ```python
   PositiveInt = Annotated[int, Path(ge=1)]
   PaginationLimit = Annotated[int, Query(ge=1, le=100)]

   @app.get("/users/{user_id}")
   async def get_user(user_id: PositiveInt, limit: PaginationLimit = 20):
       ...
   ```

---

## 8. Request Body, Headers & Cookies Extraction

FastAPI distinguishes parameters based on how they are defined:

```python
from fastapi import FastAPI, Header, Cookie, Body
from pydantic import BaseModel, EmailStr
from typing import Annotated, Optional

app = FastAPI()

class CreateUserPayload(BaseModel):
    email: EmailStr
    full_name: str
    age: int

@app.post("/api/v1/users")
async def create_user(
    # Body parameter: complex Pydantic model parsed from JSON body
    payload: CreateUserPayload,
    # Header parameter: automatically converts hyphens to underscores (X-Client-ID -> x_client_id)
    x_client_id: Annotated[str, Header(description="Client application identifier")],
    # Optional Cookie parameter
    session_token: Annotated[Optional[str], Cookie(description="Session authentication cookie")] = None,
):
    return {
        "status": "created",
        "user": payload,
        "client_id": x_client_id,
        "session_present": session_token is not None,
    }
```

---

## 9. Response Models (`response_model`) & Data Filtering

The `response_model` argument on route decorators is one of FastAPI's most critical **security and serialization features**.

```python
from fastapi import FastAPI, status
from pydantic import BaseModel, EmailStr

app = FastAPI()

# Database representation (contains sensitive fields)
class UserInDB(BaseModel):
    id: int
    email: EmailStr
    hashed_password: str
    is_superuser: bool

# Public API contract (sensitive fields omitted)
class UserPublicResponse(BaseModel):
    id: int
    email: EmailStr

@app.get(
    "/users/{user_id}",
    response_model=UserPublicResponse,
    status_code=status.HTTP_200_OK,
)
async def get_user(user_id: int):
    # Simulating fetching user from database
    db_user = UserInDB(
        id=user_id,
        email="engineer@cloud.io",
        hashed_password="pbkdf2:sha256:100000$secret$salt",
        is_superuser=True,
    )
    # Even though db_user contains hashed_password, FastAPI filters it out!
    return db_user
```

### Security Guarantee
Returning `db_user` directly might accidentally leak `hashed_password` or `is_superuser` to the client. Because `response_model=UserPublicResponse` is specified, FastAPI's serialization engine passes the data through `UserPublicResponse`, stripping all undeclared fields before emitting JSON.

---

## 10. Fine-Grained Response Control: `exclude_unset`, `exclude_defaults`, `exclude_none`

You can control payload serialization density using response model flags:

```python
class ItemResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    tax: float = 10.0
    tags: list[str] = []

@app.get(
    "/items/{item_id}",
    response_model=ItemResponse,
    response_model_exclude_unset=True,  # Excludes attributes not explicitly set on the model
    response_model_exclude_none=True,   # Omits keys whose value is None
)
async def get_item(item_id: int):
    return ItemResponse(id=item_id, name="Laptop")
    # Output JSON: {"id": 101, "name": "Laptop", "tax": 10.0, "tags": []}
    # "description" is excluded because its value is None.
```

---

## 11. HTTP Status Codes & Dynamic Headers

Always use the standard constants provided in `fastapi.status`:

```python
from fastapi import FastAPI, Response, status

app = FastAPI()

@app.post(
    "/api/v1/sessions",
    status_code=status.HTTP_201_CREATED,
)
async def create_session(response: Response):
    # Injecting custom response headers and cookies dynamically
    response.headers["X-Server-Execution-Time-MS"] = "1.24"
    response.set_cookie(
        key="session_id",
        value="sec_token_991823",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return {"status": "session_created"}
```

---

## 12. Automatic OpenAPI Documentation Architecture

FastAPI automatically parses route decorators, parameter types, Docstrings, and Pydantic schemas to generate a complete **OpenAPI 3.1** specification.

### Interactive Documentation Endpoints
- **`/docs`**: Swagger UI interface allowing interactive API testing in the browser.
- **`/redoc`**: ReDoc interface providing clean, hierarchical documentation.
- **`/openapi.json`**: The raw machine-readable JSON Schema specification.

### Route Metadata Customization
```python
@app.get(
    "/healthz",
    tags=["System Telemetry"],
    summary="Application Health Probe",
    description="Kubernetes liveness and readiness probe endpoint. Returns 200 OK when operational.",
    response_description="Service status and timestamp",
    include_in_schema=True,
)
async def health_check():
    return {"status": "healthy"}
```

---

## 13. Error Handling & Custom Exception Handlers

FastAPI provides `HTTPException` for standard operational errors:

```python
from fastapi import FastAPI, HTTPException, status

app = FastAPI()

@app.get("/items/{item_id}")
async def get_item(item_id: int):
    if item_id > 1000:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with ID {item_id} does not exist.",
            headers={"X-Error-Code": "ERR_ITEM_NOT_FOUND"},
        )
    return {"id": item_id, "name": "Widget"}
```

### Global Unified Error Envelope (RFC 7807)
In enterprise architectures, you should register custom exception handlers to format all errors into a standardized JSON envelope:

```python
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi import Request

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_FAILED",
                "message": "Input payload failed validation rules.",
                "details": exc.errors(),
            }
        },
    )
```

---

## 14. Performance Architecture: `async def` vs `def` Endpoints

One of the most dangerous misconceptions in FastAPI is that making every endpoint `async def` makes the application faster.

```
+---------------------------------------------------------------------------------+
|                                 async def endpoint                              |
| Executed DIRECTLY on the main event loop thread!                                |
| -> If you perform blocking I/O (e.g., time.sleep, requests.get, sync DB query): |
|    THE ENTIRE SERVER BLOCKS! No other request can be processed!                 |
| -> Use ONLY with non-blocking async code: await asyncio.sleep, await httpx.get  |
+---------------------------------------------------------------------------------+
                                        vs
+---------------------------------------------------------------------------------+
|                                    def endpoint                                 |
| Offloaded by Starlette into an external thread pool (anyio.to_thread.run_sync)! |
| -> Safe for blocking I/O (e.g. legacy database drivers, CPU image processing).  |
| -> Worker thread blocks, but main event loop remains free to serve requests!    |
+---------------------------------------------------------------------------------+
```

### The Golden Rule of FastAPI Concurrency
- If your function uses `await` on non-blocking async libraries (`httpx`, `asyncpg`, `redis-py` async) $\implies$ declare as **`async def`**.
- If your function uses synchronous blocking libraries (`requests`, `psycopg2`, `time.sleep`, heavy CPU calculations) $\implies$ declare as **`def`**.

---

## 15. Production Deployment Topology

FastAPI applications are served in production using an ASGI application server, typically **Uvicorn** managed by **Gunicorn**:

```
[ Internet Traffic ]
        |
        v
[ Nginx / Traefik Reverse Proxy ] (SSL Termination, Rate Limiting, Static Files)
        |
        v (Unix Domain Socket / Localhost TCP)
[ Gunicorn Master Process ] (Process Supervision, Worker Heartbeats)
        |
        +---> [ Uvicorn Worker 1 ] (ASGI Event Loop - Core 1)
        +---> [ Uvicorn Worker 2 ] (ASGI Event Loop - Core 2)
        +---> [ Uvicorn Worker 3 ] (ASGI Event Loop - Core 3)
        +---> [ Uvicorn Worker 4 ] (ASGI Event Loop - Core 4)
```

### Worker Sizing Formula
$$\text{Worker Count} = (2 \times \text{CPU Cores}) + 1$$
On a 4-core production host, run 9 Uvicorn worker processes to maximize CPU utilization across I/O waits.

```bash
gunicorn main:app \
  --workers 9 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 60 \
  --access-logfile -
```

---
*Next Step: Explore `example_01.py` for a fully functional runnable FastAPI pipeline and `example_02.py` for an end-to-end performance and serialization benchmark.*
