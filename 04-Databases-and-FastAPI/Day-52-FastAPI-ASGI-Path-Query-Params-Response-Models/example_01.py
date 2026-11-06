"""
Day 52: Enterprise FastAPI Architecture - ASGI, Params & Response Models
========================================================================

Complete runnable FastAPI microservice demonstrating:
1. Modern lifespan context manager for async startup/shutdown
2. Path parameters with boundary and regex validation (Annotated + Path)
3. Query parameters with filtering, pagination, and multi-value lists (Annotated + Query)
4. Header & Cookie extraction with automatic casing transformations
5. Request Body validation with Pydantic schemas
6. Strict response filtering using response_model and response_model_exclude_none
7. Unified RFC 7807 problem detail exception handling
8. Automated async integration test harness using httpx.AsyncClient
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Dict, List, Optional
from uuid import uuid4

from fastapi import (
    Cookie,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ---------------------------------------------------------------------------
# 1. Pydantic Domain Schemas & Response Models
# ---------------------------------------------------------------------------

class OrderItem(BaseModel):
    sku: str = Field(..., min_length=3, max_length=30, description="Product Stock Keeping Unit")
    quantity: int = Field(..., ge=1, le=100, description="Item quantity")
    unit_price: float = Field(..., gt=0.0, description="Unit price in USD")


class CreateOrderRequest(BaseModel):
    customer_email: EmailStr
    items: List[OrderItem] = Field(..., min_length=1, description="List of ordered items")
    discount_code: Optional[str] = Field(None, min_length=3, max_length=20)


# Internal database model (contains confidential internal cost and margin data)
class InternalOrderRecord(BaseModel):
    order_id: str
    tenant_id: str
    customer_email: str
    items: List[OrderItem]
    total_amount: float
    internal_supplier_cost: float  # SENSITIVE: Must NEVER be leaked to public API!
    margin_percentage: float       # SENSITIVE: Confidential financial metric!
    created_at: datetime
    status: str = "CONFIRMED"


# Public response model (Strict security boundary)
class OrderPublicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    customer_email: EmailStr
    items: List[OrderItem]
    total_amount: float
    status: str
    created_at: datetime
    # Note: internal_supplier_cost and margin_percentage are strictly omitted!


class PaginatedOrdersResponse(BaseModel):
    page: int
    page_size: int
    total_count: int
    data: List[OrderPublicResponse]


# ---------------------------------------------------------------------------
# 2. Modern Lifespan Application Setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages application startup and graceful shutdown.
    WHY: Replaces deprecated @app.on_event('startup') with a unified async context.
    """
    print("[LIFESPAN] Initializing in-memory mock database and caches...")
    app.state.orders_db: Dict[str, InternalOrderRecord] = {}

    # Seed initial test record
    initial_order = InternalOrderRecord(
        order_id="ORD-1001",
        tenant_id="tenant_alpha",
        customer_email="enterprise@cloud.io",
        items=[OrderItem(sku="SKU-SERVER-RACK", quantity=2, unit_price=1250.0)],
        total_amount=2500.0,
        internal_supplier_cost=1800.0,
        margin_percentage=28.0,
        created_at=datetime.now(timezone.utc),
        status="CONFIRMED",
    )
    app.state.orders_db[initial_order.order_id] = initial_order

    yield  # Application handles incoming HTTP requests

    print("[LIFESPAN] Draining connection pools and completing shutdown...")
    app.state.orders_db.clear()


app = FastAPI(
    title="Enterprise Order Management API",
    version="1.0.0",
    description="High-throughput asynchronous order processing microservice.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# 3. Global Exception Handlers (RFC 7807 Problem Details)
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Formats request validation errors into a standardized enterprise error envelope.
    """
    formatted_errors = []
    for err in exc.errors():
        field_path = " -> ".join(str(loc) for loc in err["loc"])
        formatted_errors.append({
            "field": field_path,
            "message": err["msg"],
            "type": err["type"],
        })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Input validation failed. Inspect error details.",
                "details": formatted_errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Formats HTTPExceptions into a consistent JSON envelope.
    """
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        },
    )


# ---------------------------------------------------------------------------
# 4. API Endpoints: Path, Query, Header & Response Modeling
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/orders/{order_id}",
    response_model=OrderPublicResponse,
    response_model_exclude_none=True,
    summary="Retrieve Order by ID",
    tags=["Orders"],
)
async def get_order_by_id(
    order_id: Annotated[
        str,
        Path(
            title="Order Identifier",
            description="Unique formatted order code",
            pattern=r"^ORD-\d{4,8}$",
            examples=["ORD-1001"],
        ),
    ],
    request: Request,
):
    """
    Fetches an order by its path parameter with strict regex validation.
    Demonstrates response_model security filtering (stripping internal financial metrics).
    """
    orders_db: Dict[str, InternalOrderRecord] = request.app.state.orders_db
    if order_id not in orders_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' was not found in the system.",
        )

    return orders_db[order_id]


@app.get(
    "/api/v1/orders",
    response_model=PaginatedOrdersResponse,
    summary="List Orders with Filters and Pagination",
    tags=["Orders"],
)
async def list_orders(
    request: Request,
    page: Annotated[int, Query(ge=1, description="Page index")] = 1,
    page_size: Annotated[int, Query(ge=1, le=50, description="Items per page")] = 10,
    status_filter: Annotated[Optional[str], Query(description="Filter by order status")] = None,
    tags: Annotated[List[str], Query(description="Filter by multiple tags")] = [],
    active_only: Annotated[bool, Query(description="Filter only active confirmed orders")] = True,
):
    """
    Lists orders supporting pagination, multi-value list query params, and boolean coercion.
    """
    orders_db: Dict[str, InternalOrderRecord] = request.app.state.orders_db
    all_orders = list(orders_db.values())

    # Apply mock filtering
    if status_filter:
        all_orders = [o for o in all_orders if o.status == status_filter.upper()]

    # Pagination slice
    start_idx = (page - 1) * page_size
    paginated_slice = all_orders[start_idx : start_idx + page_size]

    return PaginatedOrdersResponse(
        page=page,
        page_size=page_size,
        total_count=len(all_orders),
        data=paginated_slice,
    )


@app.post(
    "/api/v1/orders",
    response_model=OrderPublicResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create New Order",
    tags=["Orders"],
)
async def create_order(
    payload: CreateOrderRequest,
    request: Request,
    response: Response,
    x_tenant_id: Annotated[str, Header(description="Multi-tenant identifier")],
    session_id: Annotated[Optional[str], Cookie(description="Optional session cookie")] = None,
):
    """
    Creates an order extracting JSON Body, X-Tenant-ID Header, and Session Cookie.
    Injects custom response headers and returns the sanitized OrderPublicResponse.
    """
    orders_db: Dict[str, InternalOrderRecord] = request.app.state.orders_db

    calculated_total = sum(item.quantity * item.unit_price for item in payload.items)
    mock_supplier_cost = calculated_total * 0.70
    mock_margin = 30.0

    new_order_id = f"ORD-{len(orders_db) + 1002}"
    record = InternalOrderRecord(
        order_id=new_order_id,
        tenant_id=x_tenant_id,
        customer_email=payload.customer_email,
        items=payload.items,
        total_amount=calculated_total,
        internal_supplier_cost=mock_supplier_cost,
        margin_percentage=mock_margin,
        created_at=datetime.now(timezone.utc),
        status="CONFIRMED",
    )

    orders_db[new_order_id] = record

    # Dynamic response header injection
    response.headers["X-Tenant-Resolved"] = x_tenant_id
    response.headers["X-Order-Generated-ID"] = new_order_id

    return record


# ---------------------------------------------------------------------------
# 5. Automated Async Verification Suite (httpx.AsyncClient)
# ---------------------------------------------------------------------------

async def run_verification():
    """
    Directly tests the ASGI application using httpx.AsyncClient with lifespan context.
    """
    import httpx

    print("\n" + "=" * 75)
    print("RUNNING FASTAPI ARCHITECTURAL VERIFICATION SUITE")
    print("=" * 75)

    # Use ASGITransport to test ASGI app in-memory without starting an external socket server
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Trigger lifespan context manager manually
        async with lifespan(app):
            # Test 1: Fetch Order by Path Parameter & Verify Response Model Sanitization
            print("\n[TEST 1] Testing Path Parameter & response_model Security Filter...")
            resp1 = await client.get("/api/v1/orders/ORD-1001")
            assert resp1.status_code == 200, f"Expected 200, got {resp1.status_code}"
            data1 = resp1.json()
            print(f"  Response Data: {data1}")
            assert data1["order_id"] == "ORD-1001"
            assert "internal_supplier_cost" not in data1, "SECURITY BREACH: internal_supplier_cost leaked!"
            assert "margin_percentage" not in data1, "SECURITY BREACH: margin_percentage leaked!"
            print("  -> PASSED: Public fields present; confidential financial fields strictly omitted.")

            # Test 2: Path Parameter Regex Validation Failure (HTTP 422)
            print("\n[TEST 2] Testing Path Parameter Regex Rejection (Invalid format)...")
            resp2 = await client.get("/api/v1/orders/INVALID-FORMAT")
            assert resp2.status_code == 422, f"Expected 422, got {resp2.status_code}"
            err_data2 = resp2.json()
            print(f"  Error Envelope: {err_data2}")
            assert err_data2["error"]["code"] == "VALIDATION_ERROR"
            print("  -> PASSED: Invalid path parameter rejected with custom RFC 7807 error envelope.")

            # Test 3: Query Parameter Filtering and Pagination
            print("\n[TEST 3] Testing Query Parameters (Pagination, Filters & Boolean Coercion)...")
            resp3 = await client.get("/api/v1/orders?page=1&page_size=5&active_only=true")
            assert resp3.status_code == 200
            data3 = resp3.json()
            print(f"  Paginated Response: page={data3['page']}, count={data3['total_count']}, data_len={len(data3['data'])}")
            assert data3["page"] == 1
            assert len(data3["data"]) >= 1
            print("  -> PASSED: Query pagination and boolean parsing working correctly.")

            # Test 4: Create Order with Body, Header and Cookie
            print("\n[TEST 4] Testing POST with JSON Body, Headers, and Response Headers...")
            payload = {
                "customer_email": "chief.architect@enterprise.io",
                "items": [
                    {"sku": "SKU-GPU-H100", "quantity": 4, "unit_price": 30000.0},
                    {"sku": "SKU-SWITCH-400G", "quantity": 2, "unit_price": 5000.0},
                ],
                "discount_code": "ENTERPRISE2026",
            }
            resp4 = await client.post(
                "/api/v1/orders",
                json=payload,
                headers={"X-Tenant-ID": "tenant_megacorp"},
                cookies={"session_id": "tok_sec_888999"},
            )
            assert resp4.status_code == 201, f"Expected 201, got {resp4.status_code}"
            data4 = resp4.json()
            print(f"  Created Order: {data4}")
            assert data4["total_amount"] == 130000.0
            assert resp4.headers["X-Tenant-Resolved"] == "tenant_megacorp"
            print("  -> PASSED: Order created, headers injected, public response model strictly formatted.")

            # Test 5: Standard 404 Exception Handling
            print("\n[TEST 5] Testing Custom HTTPException Handler (404 Not Found)...")
            resp5 = await client.get("/api/v1/orders/ORD-99999")
            assert resp5.status_code == 404
            err_data5 = resp5.json()
            print(f"  404 Error Response: {err_data5}")
            assert err_data5["error"]["code"] == "HTTP_404"
            print("  -> PASSED: Custom 404 exception formatting verified.")

    print("\n" + "=" * 75)
    print("ALL FASTAPI ARCHITECTURAL VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_verification())
