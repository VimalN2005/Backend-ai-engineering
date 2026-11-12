"""
Day 60: Capstone Project 2 — High-Performance Async FastAPI Backend
================================================================================
Target: Enterprise Python Backend & AI Systems Engineer Portfolio
Description: A production-ready, horizontally scalable asynchronous backend
architecture integrating:
  1. FastAPI ASGI application with lifespan management and structured error handlers.
  2. SQLAlchemy 2.0 AsyncSession with async SQLite/PostgreSQL connection pooling.
  3. Pydantic V2 schemas with strict validation, DTOs, and computed fields.
  4. OAuth2 Password Grant + JWT Authentication with role-based scope enforcement.
  5. Redis-backed Sliding Window Rate Limiting dependency.
  6. Redis Cache-Aside pattern with randomized TTL Jitter & Write-Invalidation.
  7. Celery/Background Task simulation with transaction-safe post-commit dispatching.
  8. ASGI Custom Middleware for X-Request-ID correlation and Server-Timing.
  9. Automated End-to-End Integration Test Suite executed via httpx.AsyncClient.
================================================================================
"""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import fakeredis.aioredis
import httpx
from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.pool import StaticPool

import sys

# -----------------------------------------------------------------------------
# 1. Logging & Configuration
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("docustream_capstone")

SECRET_KEY = "enterprise-production-jwt-secret-do-not-leak"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_SECONDS = 3600

# -----------------------------------------------------------------------------
# 2. Database Layer (SQLAlchemy 2.0 Declarative Async Models)
# -----------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(50), default="user", nullable=False)
    scopes = Column(String(255), default="read:documents,write:documents", nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False, index=True)
    content = Column(Text, nullable=False)
    category = Column(String(50), default="general", nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    owner = relationship("User", back_populates="documents")


class TaskAudit(Base):
    __tablename__ = "task_audits"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False)
    task_name = Column(String(100), nullable=False)
    status = Column(String(50), default="PENDING", nullable=False)
    result_payload = Column(Text, nullable=True)
    completed_at = Column(DateTime, default=func.now(), nullable=False)


# In-Memory Async SQLite Database Engine with StaticPool for concurrency safety
DATABASE_URL = "sqlite+aiosqlite:///file:capstone_d60?mode=memory&cache=shared&uri=true"
async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
AsyncSessionLocal = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)

# Shared Async Redis Client - initialized lazily within active event loop
redis_client: Optional[fakeredis.aioredis.FakeRedis] = None


def get_redis() -> fakeredis.aioredis.FakeRedis:
    global redis_client
    if redis_client is None:
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return redis_client


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a request-scoped transactional AsyncSession."""
    async with AsyncSessionLocal() as session:
        yield session


# -----------------------------------------------------------------------------
# 3. Pydantic V2 Schemas & DTOs
# -----------------------------------------------------------------------------
class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2, max_length=100)
    role: str = "user"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    role: str
    scopes: List[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    scopes: List[str]


class DocumentCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    content: str = Field(min_length=10)
    category: str = "general"


class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    content: Optional[str] = Field(default=None, min_length=10)
    category: Optional[str] = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_id: str
    title: str
    content: str
    category: str
    version: int

    @computed_field
    @property
    def word_count(self) -> int:
        return len(self.content.split())

    @computed_field
    @property
    def estimated_reading_time_sec(self) -> float:
        # Standard 200 words per minute reading pace
        return round((len(self.content.split()) / 200.0) * 60.0, 1)


class AnalyticsSummaryResponse(BaseModel):
    total_documents: int
    total_words: int
    categories: Dict[str, int]
    system_load_status: str


# -----------------------------------------------------------------------------
# 4. Authentication, Cryptography & OAuth2 Scope RBAC
# -----------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def hash_password(password: str) -> str:
    """Secure deterministic PBKDF2 HMAC-SHA256 password hasher with static salt."""
    salt = b"docustream_static_enterprise_salt_2026"
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 1_000)
    return key.hex()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return hmac.compare_digest(hash_password(plain_password), hashed_password)


def create_access_token(data: dict, expires_delta_sec: int = ACCESS_TOKEN_EXPIRE_SECONDS) -> str:
    payload = data.copy()
    payload["exp"] = time.time() + expires_delta_sec
    header = {"alg": ALGORITHM, "typ": "JWT"}
    header_b64 = json.dumps(header).encode().hex()
    payload_b64 = json.dumps(payload).encode().hex()
    signature = hmac.new(SECRET_KEY.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).hexdigest()
    return f"{header_b64}.{payload_b64}.{signature}"


def decode_access_token(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format")
    header_b64, payload_b64, signature = parts
    expected_sig = hmac.new(SECRET_KEY.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature")
    payload = json.loads(bytes.fromhex(payload_b64).decode())
    if payload.get("exp", 0) < time.time():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    return payload


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_scope(required_scope: str):
    """Factory creating an async dependency that enforces RBAC OAuth2 scopes."""
    async def scope_checker(current_user: User = Depends(get_current_user)) -> User:
        user_scopes = [s.strip() for s in current_user.scopes.split(",") if s.strip()]
        if "admin:all" in user_scopes or required_scope in user_scopes:
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: Insufficient privileges. Required scope: '{required_scope}'"
        )
    return scope_checker


# -----------------------------------------------------------------------------
# 5. Redis Sliding-Window Rate Limiter Dependency
# -----------------------------------------------------------------------------
class SlidingWindowRateLimiter:
    """Distributed Sliding-Window Rate Limiter backed by Redis sorted sets."""
    def __init__(self, requests_per_minute: int = 60):
        self.limit = requests_per_minute
        self.window_sec = 60

    async def __call__(self, request: Request):
        auth_header = request.headers.get("Authorization", "")
        identifier = (
            auth_header.split(" ")[1]
            if "Bearer " in auth_header
            else (request.client.host if request.client else "unknown_ip")
        )
        key = f"ratelimit:{identifier}"
        now = time.time()
        window_start = now - self.window_sec
        r = get_redis()

        # Pipeline: Clean expired timestamps, add current timestamp, count active window
        await r.zremrangebyscore(key, 0, window_start)
        await r.zadd(key, {str(now): now})
        await r.expire(key, self.window_sec)
        current_count = await r.zcard(key)

        if current_count > self.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: Max {self.limit} requests per {self.window_sec}s window.",
                headers={"Retry-After": str(self.window_sec)}
            )


rate_limiter_standard = SlidingWindowRateLimiter(requests_per_minute=30)
rate_limiter_strict = SlidingWindowRateLimiter(requests_per_minute=5)

# -----------------------------------------------------------------------------
# 6. Redis Cache-Aside & Write-Invalidation Engine
# -----------------------------------------------------------------------------
class CacheEngine:
    """Production Redis Cache-Aside manager with randomized TTL jitter."""
    def get_doc_key(self, doc_id: str) -> str:
        return f"cache:doc:{doc_id}"

    async def get_document(self, doc_id: str) -> Optional[dict]:
        raw = await get_redis().get(self.get_doc_key(doc_id))
        return json.loads(raw) if raw else None

    async def set_document(self, doc_id: str, data: dict, base_ttl: int = 300) -> None:
        # Add +- 15% TTL jitter to prevent cache avalanche
        jitter = int(base_ttl * 0.15)
        ttl = base_ttl + (int(time.time() * 1000) % (jitter * 2)) - jitter
        await get_redis().set(self.get_doc_key(doc_id), json.dumps(data), ex=ttl)

    async def invalidate_document(self, doc_id: str) -> None:
        """Write-invalidation: Delete cache key rather than overwriting to avoid dual-write races."""
        await get_redis().delete(self.get_doc_key(doc_id))
        await get_redis().delete("cache:analytics:summary")


cache_engine = CacheEngine()

# -----------------------------------------------------------------------------
# 7. Post-Commit Background Task Simulation (Celery Pipeline Emulation)
# -----------------------------------------------------------------------------
class PostCommitTaskManager:
    """Enforces transaction-safe task dispatching strictly after database commit."""
    @staticmethod
    async def dispatch_document_vectorization(document_id: str, title: str, content: str):
        logger.info(f"[BACKGROUND WORKER] Commencing AI Vectorization for document: {document_id} ('{title}')")
        await asyncio.sleep(0.01)  # Simulate vector embedding computation
        audit_record = {
            "document_id": document_id,
            "task": "vector_embedding",
            "vectors_generated": 1536,
            "status": "COMPLETED",
            "completed_at": time.time()
        }
        await get_redis().lpush(f"task:audit:{document_id}", json.dumps(audit_record))
        logger.info(f"[BACKGROUND WORKER] Vectorization completed for: {document_id}")


# -----------------------------------------------------------------------------
# 8. Lifespan Context Manager & FastAPI Application Setup
# -----------------------------------------------------------------------------
async def init_tables():
    """Initializes tables and seeds initial administrator account."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == "usr_admin_master_001")
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if not existing:
            admin = User(
                id="usr_admin_master_001",
                email="admin@docustream.io",
                hashed_password=hash_password("SuperSecretAdminPass123!"),
                full_name="Platform Administrator",
                role="admin",
                scopes="read:documents,write:documents,analytics:read,admin:all"
            )
            session.add(admin)
            await session.commit()
    logger.info("[INIT] In-memory SQLite tables initialized and Admin account seeded.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_tables()
    yield
    logger.info("[SHUTDOWN] Lifespan shutdown complete.")


app = FastAPI(
    title="DocuStream Enterprise Async API",
    version="2.0.0",
    description="High-Performance Async Backend with FastAPI, SQLAlchemy 2.0, Redis & Celery",
    lifespan=lifespan
)

# -----------------------------------------------------------------------------
# 9. Custom ASGI Middleware: Correlation & Timing
# -----------------------------------------------------------------------------
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    # Correlation ID (X-Request-ID)
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    t0 = time.perf_counter()

    response: Response = await call_next(request)

    process_time_ms = (time.perf_counter() - t0) * 1000.0
    response.headers["X-Request-ID"] = req_id
    response.headers["X-Process-Time"] = f"{process_time_ms:.2f}ms"
    return response


# -----------------------------------------------------------------------------
# 10. API Endpoints
# -----------------------------------------------------------------------------
@app.post("/api/v1/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(req: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    """User registration endpoint enforcing email uniqueness and password hashing."""
    stmt = select(User).where(User.email == req.email)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    new_user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role=req.role,
        scopes="read:documents,write:documents" if req.role != "admin" else "admin:all"
    )
    db.add(new_user)
    await db.commit()
    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        full_name=new_user.full_name,
        role=new_user.role,
        scopes=new_user.scopes.split(",")
    )


@app.post("/api/v1/auth/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """OAuth2 password flow token generation returning cryptographically signed JWT."""
    stmt = select(User).where(User.email == form_data.username)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scopes_list = [s.strip() for s in user.scopes.split(",") if s.strip()]
    token_payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "scopes": user.scopes
    }
    token = create_access_token(token_payload)
    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_SECONDS,
        scopes=scopes_list
    )


@app.get("/api/v1/users/me", response_model=UserResponse)
async def get_current_user_profile(current_user: User = Depends(get_current_user)):
    """Returns profile for currently authenticated user."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        scopes=current_user.scopes.split(",")
    )


@app.post("/api/v1/documents", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    doc_in: DocumentCreateRequest,
    current_user: User = Depends(require_scope("write:documents")),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a document, commits transaction, invalidates analytics cache,
    and dispatches post-commit AI vectorization background task.
    """
    doc = Document(
        owner_id=current_user.id,
        title=doc_in.title,
        content=doc_in.content,
        category=doc_in.category
    )
    db.add(doc)
    await db.commit()  # Commit transaction boundary FIRST

    # Post-Commit Side Effects:
    await cache_engine.invalidate_document(doc.id)
    # Dispatch asynchronous background task
    asyncio.create_task(
        PostCommitTaskManager.dispatch_document_vectorization(doc.id, doc.title, doc.content)
    )

    return DocumentResponse.model_validate(doc)


@app.get("/api/v1/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: str,
    current_user: User = Depends(require_scope("read:documents")),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves document by ID using Redis Cache-Aside.
    Cache Hit -> Returns cached document DTO in < 1ms.
    Cache Miss -> Fetches from database and populates cache with TTL jitter.
    """
    cached_doc = await cache_engine.get_document(doc_id)
    if cached_doc:
        return DocumentResponse(**cached_doc)

    stmt = select(Document).where(Document.id == doc_id)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    response_dto = DocumentResponse.model_validate(doc)
    await cache_engine.set_document(doc_id, response_dto.model_dump())
    return response_dto


@app.put("/api/v1/documents/{doc_id}", response_model=DocumentResponse)
async def update_document(
    doc_id: str,
    update_in: DocumentUpdateRequest,
    current_user: User = Depends(require_scope("write:documents")),
    db: AsyncSession = Depends(get_db)
):
    """Updates document and invalidates stale Redis cache entries."""
    stmt = select(Document).where(Document.id == doc_id)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if doc.owner_id != current_user.id and "admin:all" not in current_user.scopes:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this document")

    if update_in.title is not None:
        doc.title = update_in.title
    if update_in.content is not None:
        doc.content = update_in.content
    if update_in.category is not None:
        doc.category = update_in.category
    doc.version += 1

    await db.commit()
    # Cache Invalidation on Write
    await cache_engine.invalidate_document(doc_id)
    return DocumentResponse.model_validate(doc)


@app.get("/api/v1/documents", response_model=List[DocumentResponse])
async def list_documents(
    skip: int = 0,
    limit: int = 20,
    category: Optional[str] = None,
    current_user: User = Depends(require_scope("read:documents")),
    db: AsyncSession = Depends(get_db)
):
    """Paginated list of documents with optional category filtering."""
    stmt = select(Document).offset(skip).limit(min(limit, 100))
    if category:
        stmt = stmt.where(Document.category == category)
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return [DocumentResponse.model_validate(d) for d in docs]


@app.get(
    "/api/v1/analytics/summary",
    response_model=AnalyticsSummaryResponse,
    dependencies=[Depends(rate_limiter_strict)]
)
async def get_analytics_summary(
    current_user: User = Depends(require_scope("analytics:read")),
    db: AsyncSession = Depends(get_db)
):
    """
    Computes system-wide analytics summary.
    Enforces strict rate limiting (5 req/min) and requires 'analytics:read' scope.
    """
    cached = await get_redis().get("cache:analytics:summary")
    if cached:
        return AnalyticsSummaryResponse(**json.loads(cached))

    stmt_docs = select(Document)
    docs = (await db.execute(stmt_docs)).scalars().all()

    total_docs = len(docs)
    total_words = sum(len(d.content.split()) for d in docs)
    cat_counts: Dict[str, int] = {}
    for d in docs:
        cat_counts[d.category] = cat_counts.get(d.category, 0) + 1

    summary = AnalyticsSummaryResponse(
        total_documents=total_docs,
        total_words=total_words,
        categories=cat_counts,
        system_load_status="OPTIMAL"
    )
    await get_redis().set("cache:analytics:summary", json.dumps(summary.model_dump()), ex=60)
    return summary


# -----------------------------------------------------------------------------
# 11. Automated Integration Test Suite (HTTPX AsyncClient)
# -----------------------------------------------------------------------------
async def run_integration_tests():
    """Automated integration test runner validating all Capstone requirements."""
    logger.info("================================================================================")
    logger.info(" STARTING CAPSTONE PROJECT 2 AUTOMATED INTEGRATION TESTS")
    logger.info("================================================================================")

    await init_tables()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Test 1: User Registration
        logger.info("\n--- TEST 1: User Registration ---")
        reg_payload = {
            "email": "engineer@docustream.io",
            "password": "SecurePassword123!",
            "full_name": "Senior Systems Engineer",
            "role": "user"
        }
        res_reg = await client.post("/api/v1/auth/register", json=reg_payload)
        assert res_reg.status_code == 201, f"Expected 201, got {res_reg.status_code}: {res_reg.text}"
        user_data = res_reg.json()
        assert user_data["email"] == "engineer@docustream.io"
        logger.info(f"User registered successfully: ID = {user_data['id']}")

        # Test 2: OAuth2 Token Issuance
        logger.info("\n--- TEST 2: OAuth2 JWT Token Issuance ---")
        login_data = {"username": "engineer@docustream.io", "password": "SecurePassword123!"}
        res_token = await client.post("/api/v1/auth/token", data=login_data)
        assert res_token.status_code == 200, f"Expected 200, got {res_token.status_code}"
        token_json = res_token.json()
        user_token = token_json["access_token"]
        auth_headers = {"Authorization": f"Bearer {user_token}"}
        logger.info(f"JWT Token issued. Scopes granted: {token_json['scopes']}")

        # Test 3: Authenticated User Profile
        logger.info("\n--- TEST 3: Authenticated Profile & Correlation Middleware ---")
        res_me = await client.get("/api/v1/users/me", headers=auth_headers)
        assert res_me.status_code == 200
        assert "X-Request-ID" in res_me.headers
        assert "X-Process-Time" in res_me.headers
        logger.info(f"Profile verified. X-Request-ID: {res_me.headers['X-Request-ID']}")

        # Test 4: Document Creation with Background Task Trigger
        logger.info("\n--- TEST 4: Document Creation & Post-Commit Hook ---")
        doc_payload = {
            "title": "Distributed Task Queue Design Document",
            "content": "Celery 5.x architecture requires careful prefetch_multiplier tuning to avoid starvation.",
            "category": "architecture"
        }
        res_doc = await client.post("/api/v1/documents", json=doc_payload, headers=auth_headers)
        assert res_doc.status_code == 201
        doc_created = res_doc.json()
        doc_id = doc_created["id"]
        assert doc_created["word_count"] > 0
        assert doc_created["estimated_reading_time_sec"] > 0
        logger.info(f"Document created: ID = {doc_id} | Word Count: {doc_created['word_count']}")

        # Allow background worker task to process
        await asyncio.sleep(0.05)
        audit_log = await get_redis().lpop(f"task:audit:{doc_id}")
        assert audit_log is not None, "Expected background vectorization audit record"
        logger.info(f"Background vectorization completed: {json.loads(audit_log)['status']}")

        # Test 5: Redis Cache-Aside Hit
        logger.info("\n--- TEST 5: Redis Cache-Aside Verification ---")
        # First GET: Populates cache
        r_get1 = await client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers)
        assert r_get1.status_code == 200
        # Inspect Redis directly to confirm cached entry exists
        cached_val = await get_redis().get(f"cache:doc:{doc_id}")
        assert cached_val is not None, "Document must be cached in Redis after first GET"
        # Second GET: Serves from Redis
        r_get2 = await client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers)
        assert r_get2.status_code == 200
        logger.info("Cache-Aside verified: Document served directly from Redis cache.")

        # Test 6: Document Mutation & Cache Invalidation
        logger.info("\n--- TEST 6: Document Mutation & Cache Invalidation ---")
        update_payload = {"title": "Updated Distributed Task Queue Specification"}
        res_up = await client.put(f"/api/v1/documents/{doc_id}", json=update_payload, headers=auth_headers)
        assert res_up.status_code == 200
        assert res_up.json()["version"] == 2
        # Verify Redis cache key was deleted
        cache_after_update = await get_redis().get(f"cache:doc:{doc_id}")
        assert cache_after_update is None, "Cache key must be invalidated after document update"
        logger.info("Cache Invalidation verified: Stale cache key successfully deleted.")

        # Test 7: Scope RBAC Enforcement
        logger.info("\n--- TEST 7: Role-Based Scope RBAC Enforcement ---")
        # User lacks 'analytics:read' scope -> Must return 403 Forbidden
        res_unauth_analytics = await client.get("/api/v1/analytics/summary", headers=auth_headers)
        assert res_unauth_analytics.status_code == 403
        logger.info("User correctly denied access to analytics endpoint (HTTP 403 Forbidden).")

        # Login as Admin (possesses 'admin:all' scope)
        admin_login = {"username": "admin@docustream.io", "password": "SuperSecretAdminPass123!"}
        res_admin_token = await client.post("/api/v1/auth/token", data=admin_login)
        admin_token = res_admin_token.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Admin requests analytics -> Must succeed with HTTP 200
        res_admin_analytics = await client.get("/api/v1/analytics/summary", headers=admin_headers)
        assert res_admin_analytics.status_code == 200
        analytics_data = res_admin_analytics.json()
        assert analytics_data["total_documents"] >= 1
        logger.info(f"Admin granted access: {analytics_data}")

        # Test 8: Sliding-Window Rate Limiting
        logger.info("\n--- TEST 8: Sliding-Window Rate Limiter Enforcement ---")
        # Endpoint allows max 5 requests per minute. Send 6 burst requests
        rate_limited_hit = False
        for req_idx in range(6):
            res_burst = await client.get("/api/v1/analytics/summary", headers=admin_headers)
            if res_burst.status_code == 429:
                rate_limited_hit = True
                logger.info(f"Burst request {req_idx + 1} successfully intercepted: HTTP 429 Rate Limit Exceeded.")
                break
        assert rate_limited_hit, "Expected rate limit (HTTP 429) to trigger on request 6"

        logger.info("================================================================================")
        logger.info(" ALL 8 CAPSTONE INTEGRATION TESTS PASSED CLEANLY!")
        logger.info("================================================================================")


if __name__ == "__main__":
    asyncio.run(run_integration_tests())
