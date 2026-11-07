"""
Day 54: Enterprise FastAPI Dependency Injection & Session Management Pipeline
=============================================================================

Production-grade runnable FastAPI pipeline demonstrating:
1. Multi-layered Dependency Injection Graph (DAG) with Depends
2. Async Yield Generator dependencies for SQLAlchemy AsyncSession management
3. Atomic transaction handling (commit on success, rollback on error, close on finally)
4. Sub-dependency composition: Header -> Auth -> Session -> Repository -> Service
5. Class-based parameterized dependencies using __call__ (RoleChecker)
6. Dependency caching mechanics (use_cache=True vs use_cache=False)
7. Automated integration testing with httpx.AsyncClient and app.dependency_overrides
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, AsyncGenerator, Dict, List, Optional
import uuid

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# 1. Database Engine & Declarative Schema (In-Memory SQLite Async)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class TenantModel(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    api_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(30), default="OPERATOR")


class OrderModel(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_slug: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    order_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# 2. Pydantic Domain DTOs
# ---------------------------------------------------------------------------

class CreateOrderDTO(BaseModel):
    order_code: str = Field(..., pattern=r"^ORD-\d{4,8}$")
    total_amount: float = Field(..., gt=0.0)


class OrderResponseDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_slug: str
    order_code: str
    total_amount: float
    status: str
    created_at: datetime


class TenantContext(BaseModel):
    slug: str
    role: str


# ---------------------------------------------------------------------------
# 3. Repository & Service Layer (Clean Architecture)
# ---------------------------------------------------------------------------

class OrderRepository:
    """Encapsulates raw database queries; injected with an AsyncSession."""
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_code(self, tenant_slug: str, order_code: str) -> Optional[OrderModel]:
        stmt = (
            select(OrderModel)
            .where(OrderModel.tenant_slug == tenant_slug, OrderModel.order_code == order_code)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, tenant_slug: str, order_code: str, total_amount: float) -> OrderModel:
        order = OrderModel(
            tenant_slug=tenant_slug,
            order_code=order_code,
            total_amount=total_amount,
            status="CONFIRMED",
        )
        self.session.add(order)
        await self.session.flush()
        return order


class OrderService:
    """Orchestrates business operations; depends on Repository and TenantContext."""
    def __init__(self, repo: OrderRepository, tenant: TenantContext):
        self.repo = repo
        self.tenant = tenant

    async def place_order(self, dto: CreateOrderDTO) -> OrderModel:
        # Check for duplicate order code within tenant
        existing = await self.repo.get_by_code(self.tenant.slug, dto.order_code)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Order with code '{dto.order_code}' already exists for tenant '{self.tenant.slug}'.",
            )
        return await self.repo.create(self.tenant.slug, dto.order_code, dto.total_amount)

    async def fetch_order(self, order_code: str) -> OrderModel:
        order = await self.repo.get_by_code(self.tenant.slug, order_code)
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order '{order_code}' not found.",
            )
        return order


# ---------------------------------------------------------------------------
# 4. Dependency Injection Graph Providers (The IoC Engine)
# ---------------------------------------------------------------------------

# Global AsyncEngine and session factory
DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Core Yield Dependency: Manages per-request database session lifecycle.
    WHY:
    - Automatically rolls back if an exception occurs.
    - Automatically closes session and returns connection to pool in finally block.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_api_key_header(x_api_key: Annotated[Optional[str], Header()] = None) -> str:
    """Root Header Dependency: Extracts and verifies presence of API key."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required 'X-API-Key' header.",
        )
    return x_api_key


async def get_current_tenant(
    api_key: Annotated[str, Depends(get_api_key_header)],
    session: DbSession,
) -> TenantContext:
    """
    Sub-Dependency: Resolves tenant identity and authorization role from database.
    Depends on both get_api_key_header and get_db_session.
    """
    stmt = select(TenantModel).where(TenantModel.api_key == api_key)
    res = await session.execute(stmt)
    tenant = res.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API Key.",
        )

    return TenantContext(slug=tenant.slug, role=tenant.role)


CurrentTenant = Annotated[TenantContext, Depends(get_current_tenant)]


def get_order_repository(session: DbSession) -> OrderRepository:
    """Dependency Provider for OrderRepository."""
    return OrderRepository(session)


def get_order_service(
    repo: Annotated[OrderRepository, Depends(get_order_repository)],
    tenant: CurrentTenant,
) -> OrderService:
    """
    High-level Service Dependency Provider:
    Injects resolved repository and authenticated tenant context.
    """
    return OrderService(repo=repo, tenant=tenant)


# Class-Based Parameterized Dependency for RBAC
class RoleChecker:
    """
    Class-based dependency parameterized with allowed roles.
    Callable instance (__call__) enforces permission policies.
    """
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, tenant: CurrentTenant) -> TenantContext:
        if tenant.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Tenant role '{tenant.role}' does not have required permissions ({self.allowed_roles}).",
            )
        return tenant


require_admin_role = RoleChecker(allowed_roles=["ADMIN", "SUPERUSER"])


# Caching demonstration dependency: use_cache=True vs use_cache=False
def generate_request_nonce() -> str:
    """Generates a unique invocation nonce."""
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# 5. FastAPI Application & Route Declarations
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DDL tables in in-memory SQLite
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed mock tenants
    async with async_session_factory() as session:
        t1 = TenantModel(slug="tenant_alpha", api_key="key_alpha_operator_123", role="OPERATOR")
        t2 = TenantModel(slug="tenant_beta", api_key="key_beta_admin_999", role="ADMIN")
        session.add_all([t1, t2])
        await session.commit()

    yield

    await engine.dispose()


app = FastAPI(
    title="Enterprise Dependency Injection API",
    lifespan=lifespan,
)

# APIRouter with router-level security dependency
orders_router = APIRouter(
    prefix="/api/v1/orders",
    tags=["Orders"],
)


@orders_router.post(
    "",
    response_model=OrderResponseDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Create Order via Service Dependency",
)
async def create_order(
    dto: CreateOrderDTO,
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    """
    Endpoint relying on multi-tier service dependency.
    Controller contains zero SQL or raw session code!
    """
    order = await order_service.place_order(dto)
    return order


@orders_router.get(
    "/{order_code}",
    response_model=OrderResponseDTO,
    summary="Retrieve Order by Code",
)
async def get_order(
    order_code: Annotated[str, Path(pattern=r"^ORD-\d{4,8}$")],
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    order = await order_service.fetch_order(order_code)
    return order


@orders_router.delete(
    "/{order_code}",
    summary="Admin-only Order Deletion",
    dependencies=[Depends(require_admin_role)],  # Enforces RBAC dependency
)
async def delete_order(
    order_code: str,
    admin_tenant: Annotated[TenantContext, Depends(require_admin_role)],
):
    return {"status": "deleted", "deleted_by": admin_tenant.slug, "order_code": order_code}


@orders_router.get(
    "/debug/caching-demo",
    summary="Demonstrate use_cache=True vs use_cache=False",
)
async def caching_demo(
    cached_1: Annotated[str, Depends(generate_request_nonce, use_cache=True)],
    cached_2: Annotated[str, Depends(generate_request_nonce, use_cache=True)],
    fresh_1: Annotated[str, Depends(generate_request_nonce, use_cache=False)],
    fresh_2: Annotated[str, Depends(generate_request_nonce, use_cache=False)],
):
    return {
        "cached_equal": cached_1 == cached_2,
        "fresh_different": fresh_1 != fresh_2,
        "cached_values": [cached_1, cached_2],
        "fresh_values": [fresh_1, fresh_2],
    }


app.include_router(orders_router)


# ---------------------------------------------------------------------------
# 6. Automated Verification Harness (httpx.AsyncClient + overrides)
# ---------------------------------------------------------------------------

async def run_verification():
    print("=" * 75)
    print("FASTAPI DEPENDENCY INJECTION & SESSION MANAGEMENT VERIFICATION")
    print("=" * 75)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with lifespan(app):
            # Test 1: Full DAG Resolution (Happy Path Order Creation)
            print("\n[TEST 1] Testing Full DAG Execution (Header -> Auth -> Session -> Repo -> Service)...")
            payload = {"order_code": "ORD-10001", "total_amount": 450.75}
            headers = {"X-API-Key": "key_alpha_operator_123"}

            resp1 = await client.post("/api/v1/orders", json=payload, headers=headers)
            print(f"  Response Status: {resp1.status_code}")
            assert resp1.status_code == 201, f"Expected 201, got {resp1.status_code}: {resp1.text}"
            data1 = resp1.json()
            print(f"  Created Order: {data1}")
            assert data1["order_code"] == "ORD-10001"
            assert data1["tenant_slug"] == "tenant_alpha"
            print("  -> PASSED: Complete 5-tier dependency graph resolved successfully.")

            # Test 2: Sub-dependency Authentication Rejection (Missing Header)
            print("\n[TEST 2] Testing Missing Header Rejection in Root Dependency...")
            resp2 = await client.post("/api/v1/orders", json=payload)
            print(f"  Response Status: {resp2.status_code}")
            assert resp2.status_code == 401
            assert "Missing required 'X-API-Key' header" in resp2.json()["detail"]
            print("  -> PASSED: Missing API key rejected at root of the DAG.")

            # Test 3: Sub-dependency Authentication Rejection (Invalid Key)
            print("\n[TEST 3] Testing Invalid API Key Rejection in Auth Sub-Dependency...")
            resp3 = await client.post(
                "/api/v1/orders", json=payload, headers={"X-API-Key": "invalid_fraud_key"}
            )
            print(f"  Response Status: {resp3.status_code}")
            assert resp3.status_code == 401
            assert "Invalid or revoked API Key" in resp3.json()["detail"]
            print("  -> PASSED: Non-existent key rejected during tenant query resolution.")

            # Test 4: Role-Based Access Control Rejection (RoleChecker __call__)
            print("\n[TEST 4] Testing Class-based RBAC Dependency (Operator denied delete)...")
            resp4 = await client.delete(
                "/api/v1/orders/ORD-10001", headers={"X-API-Key": "key_alpha_operator_123"}
            )
            print(f"  Response Status: {resp4.status_code}")
            assert resp4.status_code == 403
            assert "Forbidden: Tenant role 'OPERATOR'" in resp4.json()["detail"]
            print("  -> PASSED: Operator role cleanly rejected by RoleChecker dependency.")

            # Test 5: RoleChecker Success (Admin permitted delete)
            print("\n[TEST 5] Testing Class-based RBAC Dependency (Admin permitted delete)...")
            resp5 = await client.delete(
                "/api/v1/orders/ORD-10001", headers={"X-API-Key": "key_beta_admin_999"}
            )
            print(f"  Response Status: {resp5.status_code}")
            assert resp5.status_code == 200
            assert resp5.json()["status"] == "deleted"
            print("  -> PASSED: Admin tenant authorized by RoleChecker dependency.")

            # Test 6: Dependency Caching Verification (use_cache=True vs False)
            print("\n[TEST 6] Testing Dependency Caching (use_cache=True vs use_cache=False)...")
            resp6 = await client.get("/api/v1/orders/debug/caching-demo", headers=headers)
            cache_data = resp6.json()
            print(f"  Caching Result: {cache_data}")
            assert cache_data["cached_equal"] is True, "cached_1 and cached_2 must be identical"
            assert cache_data["fresh_different"] is True, "fresh_1 and fresh_2 must be different"
            print("  -> PASSED: Caching behavior verified.")

            # Test 7: Integration Testing with app.dependency_overrides
            print("\n[TEST 7] Testing app.dependency_overrides (Mocking Auth Layer for Fast Tests)...")
            # Override get_current_tenant with a synthetic admin mock
            async def mock_admin_tenant() -> TenantContext:
                return TenantContext(slug="tenant_mock_sandbox", role="ADMIN")

            app.dependency_overrides[get_current_tenant] = mock_admin_tenant
            try:
                # Issue request with zero real headers; mock bypasses DB lookup
                resp7 = await client.delete(
                    "/api/v1/orders/ORD-99999", headers={"X-API-Key": "any_bypass_key"}
                )
                print(f"  Override Response Status: {resp7.status_code}")
                assert resp7.status_code == 200
                assert resp7.json()["deleted_by"] == "tenant_mock_sandbox"
                print("  -> PASSED: dependency_overrides swapped production auth with mock cleanly.")
            finally:
                app.dependency_overrides.clear()

    print("\n" + "=" * 75)
    print("ALL DEPENDENCY INJECTION & SESSION MANAGEMENT TESTS PASSED!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_verification())
