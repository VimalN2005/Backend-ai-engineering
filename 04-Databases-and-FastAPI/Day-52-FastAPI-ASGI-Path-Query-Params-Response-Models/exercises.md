# Day 52: Advanced FastAPI Practical Exercises

Mastering FastAPI fundamentals requires fluency with ASGI request lifecycles, complex query parsing, response model security, and dynamic schema filtering. Complete the following three senior-level engineering exercises. Verified reference solutions follow each prompt.

---

## Exercise 1: Multi-Tenant Product Catalog Filter with Complex Query Parsing

### Architectural Scenario
You are designing an asynchronous e-commerce catalog API for an enterprise multi-tenant platform. Each tenant accesses their catalog via a path slug `/api/v1/tenants/{tenant_slug}/products`.

Clients need to filter products with:
- Optional category tags (`tags: list[str]`).
- Price boundary range (`min_price`, `max_price`).
- Sorting options (`sort_by: Enum["price_asc", "price_desc", "newest"]`).
- Boolean flags (`in_stock_only: bool = True`).
- Strict pagination (`page`, `page_size`).

Internal records contain supplier cost prices (`wholesale_cost`) that **must never be exposed** to tenant customers.

### Requirements
1. Implement Pydantic models for `ProductInternal` and `ProductPublicResponse`.
2. Configure path parameter validation on `tenant_slug` (lowercase alphanumeric and hyphens, 3–30 characters).
3. Use `typing.Annotated` with `Query(...)` and `Path(...)` metadata across all arguments.
4. Enforce security filtering with `response_model=PaginatedProductResponse`.
5. Return HTTP 422 if `max_price < min_price`.

### Verified Reference Solution

```python
from enum import Enum
from typing import Annotated, List, Optional
from fastapi import FastAPI, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI()

class SortOrder(str, Enum):
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NEWEST = "newest"

class ProductPublicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    category: str
    retail_price: float
    is_in_stock: bool
    # wholesale_cost is strictly excluded!

class PaginatedProductResponse(BaseModel):
    page: int
    page_size: int
    total_count: int
    products: List[ProductPublicResponse]

# Mock Database
CATALOG_DB = [
    {"sku": "SKU-LAPTOP-01", "name": "Dev Workstation", "category": "tech", "retail_price": 1899.99, "wholesale_cost": 1200.0, "is_in_stock": True},
    {"sku": "SKU-MONITOR-02", "name": "4K IPS Display", "category": "tech", "retail_price": 499.99, "wholesale_cost": 310.0, "is_in_stock": True},
    {"sku": "SKU-DESK-03", "name": "Standing Desk", "category": "furniture", "retail_price": 650.00, "wholesale_cost": 400.0, "is_in_stock": False},
]

TenantSlug = Annotated[
    str,
    Path(
        min_length=3,
        max_length=30,
        pattern=r"^[a-z0-9-]+$",
        description="Tenant identifier slug",
    ),
]

@app.get(
    "/api/v1/tenants/{tenant_slug}/products",
    response_model=PaginatedProductResponse,
    status_code=status.HTTP_200_OK,
)
async def list_tenant_products(
    tenant_slug: TenantSlug,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
    min_price: Annotated[Optional[float], Query(ge=0.0)] = None,
    max_price: Annotated[Optional[float], Query(ge=0.0)] = None,
    categories: Annotated[List[str], Query(description="Filter categories")] = [],
    sort_by: Annotated[SortOrder, Query(description="Sorting order")] = SortOrder.NEWEST,
    in_stock_only: Annotated[bool, Query(description="Only in-stock items")] = True,
):
    if min_price is not None and max_price is not None and max_price < min_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="max_price cannot be less than min_price.",
        )

    # Filter mock records
    results = CATALOG_DB
    if in_stock_only:
        results = [p for p in results if p["is_in_stock"]]
    if categories:
        results = [p for p in results if p["category"] in categories]
    if min_price is not None:
        results = [p for p in results if p["retail_price"] >= min_price]
    if max_price is not None:
        results = [p for p in results if p["retail_price"] <= max_price]

    # Sort
    if sort_by == SortOrder.PRICE_ASC:
        results.sort(key=lambda x: x["retail_price"])
    elif sort_by == SortOrder.PRICE_DESC:
        results.sort(key=lambda x: x["retail_price"], reverse=True)

    start = (page - 1) * page_size
    slice_data = results[start : start + page_size]

    return PaginatedProductResponse(
        page=page,
        page_size=page_size,
        total_count=len(results),
        products=slice_data,
    )
```

---

## Exercise 2: Implementing RFC 7807 Problem Details Error Envelopes

### Architectural Scenario
Enterprise APIs must provide consistent error payloads complying with the **RFC 7807 (Problem Details for HTTP APIs)** specification across all failure modes:
1. Pydantic schema validation failures (`RequestValidationError`).
2. Explicit application HTTP errors (`HTTPException`).
3. Uncaught server crashes (500 Internal Server Errors).

### Requirements
1. Implement a custom exception handler for `RequestValidationError` mapping errors into an RFC 7807 compliant JSON response with `type`, `title`, `status`, `detail`, and `invalid_params`.
2. Implement an exception handler for `HTTPException` preserving custom headers.
3. Implement a global catch-all exception handler for unhandled `Exception` logging the incident ID and preventing internal traceback leaks.

### Verified Reference Solution

```python
from datetime import datetime, timezone
import uuid
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def rfc7807_validation_handler(request: Request, exc: RequestValidationError):
    invalid_params = []
    for err in exc.errors():
        loc_str = "/".join(str(l) for l in err["loc"])
        invalid_params.append({
            "name": loc_str,
            "reason": err["msg"],
            "type": err["type"],
        })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        media_type="application/problem+json",
        content={
            "type": "https://api.enterprise.io/errors/validation-error",
            "title": "Invalid Request Parameters",
            "status": 422,
            "detail": "One or more request parameters failed validation rules.",
            "instance": str(request.url.path),
            "invalid_params": invalid_params,
        },
    )

@app.exception_handler(HTTPException)
async def rfc7807_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        media_type="application/problem+json",
        content={
            "type": f"https://api.enterprise.io/errors/http-{exc.status_code}",
            "title": "HTTP Request Error",
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": str(request.url.path),
        },
    )

@app.exception_handler(Exception)
async def rfc7807_catch_all_handler(request: Request, exc: Exception):
    incident_id = str(uuid.uuid4())
    # In production: log exc with incident_id to Sentry / Datadog
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        media_type="application/problem+json",
        content={
            "type": "https://api.enterprise.io/errors/internal-server-error",
            "title": "Internal Server Error",
            "status": 500,
            "detail": f"An unexpected system fault occurred. Incident reference: {incident_id}",
            "instance": str(request.url.path),
        },
    )
```

---

## Exercise 3: Dynamic Sparse Fieldsets (`?fields=...`) Response Pruning

### Architectural Scenario
Mobile clients on high-latency networks require fetching only a small subset of fields (e.g. `?fields=sku,name,retail_price`) to minimize cellular bandwidth and battery consumption.

### Requirements
1. Create a dependency or query parameter `fields: Annotated[Optional[str], Query(description="Comma-separated field list")] = None`.
2. Construct an endpoint returning complex entities.
3. If `fields` is specified, dynamically prune the outbound Pydantic model dictionary to only include the requested keys.
4. Reject invalid field names with HTTP 400 Bad Request.

### Verified Reference Solution

```python
from typing import Annotated, Optional, Set
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel

app = FastAPI()

class UserProfile(BaseModel):
    id: int
    username: str
    email: str
    full_name: str
    bio: Optional[str] = None
    avatar_url: str
    country: str
    reputation_score: int

MOCK_USER = UserProfile(
    id=42,
    username="cloud_native",
    email="dev@kubernetes.io",
    full_name="Alex River",
    bio="Staff Systems Architect",
    avatar_url="https://cdn.enterprise.io/avatars/42.png",
    country="DE",
    reputation_score=9850,
)

@app.get("/api/v1/profile")
async def get_sparse_profile(
    fields: Annotated[
        Optional[str],
        Query(
            description="Comma-separated list of sparse fields to project (e.g. 'username,avatar_url')",
            examples=["username,avatar_url", "id,email,reputation_score"],
        ),
    ] = None
):
    user_dict = MOCK_USER.model_dump()

    if not fields:
        return user_dict

    requested_fields = {f.strip() for f in fields.split(",") if f.strip()}
    valid_fields = set(UserProfile.model_fields.keys())

    invalid_fields = requested_fields - valid_fields
    if invalid_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid field(s) requested: {sorted(list(invalid_fields))}. Allowed fields: {sorted(list(valid_fields))}",
        )

    # Return dictionary projected with sparse keys
    return {k: user_dict[k] for k in requested_fields}
```

---
*Completing these exercises ensures production-level mastery of FastAPI request routing, input validation, and secure response modeling.*
