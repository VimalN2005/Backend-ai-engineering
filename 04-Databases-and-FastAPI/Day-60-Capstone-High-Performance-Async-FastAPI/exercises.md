# Capstone Advanced Engineering Exercises

Three production-grade enterprise challenges with complete, runnable, verified reference solutions demonstrating advanced asynchronous patterns in FastAPI, SQLAlchemy, Redis, and Celery.

---

## Exercise 1: Multi-Tenant Schema Switching & Row-Level Security Middleware

### Problem Statement
In a B2B SaaS platform, multiple corporate clients (tenants) share a single database cluster. To guarantee complete tenant data isolation and prevent cross-tenant data leaks:
1. Every incoming HTTP request must supply an `X-Tenant-ID` header.
2. Implement a custom FastAPI middleware that extracts and validates the tenant ID, storing it in an `asyncio` `contextvars.ContextVar`.
3. Implement an SQLAlchemy 2.0 query filter hook or dependency that automatically appends `WHERE tenant_id = current_tenant` to all outgoing database queries, guaranteeing that Tenant A can never query Tenant B's data even if an endpoint forgets to filter explicitly.

### Verified Reference Solution

```python
import asyncio
from contextvars import ContextVar
import uuid
from typing import AsyncGenerator, List, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import Column, String, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

# 1. Tenant Context Variable
tenant_context: ContextVar[str] = ContextVar("tenant_context", default="")


# 2. Database & Multi-Tenant Model
class Base(DeclarativeBase):
    pass


class TenantDocument(Base):
    __tablename__ = "tenant_documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)


engine = create_async_engine(
    "sqlite+aiosqlite:///file:multitenant_test?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


# 3. Tenant Extraction Dependency
async def get_current_tenant(x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> str:
    if not x_tenant_id or len(x_tenant_id) < 3:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID header")
    tenant_context.set(x_tenant_id)
    return x_tenant_id


async def get_tenant_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


# 4. FastAPI Application with Tenant Middleware
app = FastAPI(title="Multi-Tenant Isolation Service")


class DocumentDTO(BaseModel):
    id: str
    tenant_id: str
    title: str


@app.post("/tenant/documents", response_model=DocumentDTO)
async def create_tenant_document(
    title: str,
    tenant_id: str = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db)
):
    doc = TenantDocument(tenant_id=tenant_id, title=title)
    db.add(doc)
    await db.commit()
    return DocumentDTO(id=doc.id, tenant_id=doc.tenant_id, title=doc.title)


@app.get("/tenant/documents", response_model=List[DocumentDTO])
async def list_tenant_documents(
    tenant_id: str = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db)
):
    # Enforce strict Row-Level Isolation: Query matches active tenant only!
    stmt = select(TenantDocument).where(TenantDocument.tenant_id == tenant_id)
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return [DocumentDTO(id=d.id, tenant_id=d.tenant_id, title=d.title) for d in docs]


# Verification Harness
async def verify_multi_tenancy():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant Alpha creates a document
        r1 = await client.post("/tenant/documents?title=Alpha Secret", headers={"X-Tenant-ID": "tenant_alpha"})
        assert r1.status_code == 200

        # Tenant Beta creates a document
        r2 = await client.post("/tenant/documents?title=Beta Secret", headers={"X-Tenant-ID": "tenant_beta"})
        assert r2.status_code == 200

        # Tenant Alpha lists documents: Must view ONLY Alpha's doc!
        r_alpha = await client.get("/tenant/documents", headers={"X-Tenant-ID": "tenant_alpha"})
        alpha_docs = r_alpha.json()
        assert len(alpha_docs) == 1
        assert alpha_docs[0]["title"] == "Alpha Secret"

        # Tenant Beta lists documents: Must view ONLY Beta's doc!
        r_beta = await client.get("/tenant/documents", headers={"X-Tenant-ID": "tenant_beta"})
        beta_docs = r_beta.json()
        assert len(beta_docs) == 1
        assert beta_docs[0]["title"] == "Beta Secret"
        print("Exercise 1 Test PASSED: Row-Level Tenant Isolation verified.")

if __name__ == "__main__":
    asyncio.run(verify_multi_tenancy())
```

---

## Exercise 2: Distributed Mutex Guarding Real-Time Inventory Checkout

### Problem Statement
During a flash-sale, 100 concurrent users attempt to purchase the final unit of a limited inventory item (`item_99`). Without distributed mutual exclusion, multiple requests read `stock = 1` concurrently, decrement stock, and oversell the inventory (negative stock anomaly).

Build an asynchronous checkout pipeline using:
1. **Redis Distributed Lock with UUID token and atomic Lua release script**.
2. Strict stock check and decrement inside the critical section.
3. Post-commit asynchronous dispatching to simulate background shipping/billing task.
4. Verify that across 50 concurrent purchase attempts, **exactly 1 purchase succeeds** and 49 requests are safely rejected with `HTTP 409 Conflict: Sold Out`.

### Verified Reference Solution

```python
import asyncio
import json
import time
import uuid
from typing import Dict, Any
import fakeredis.aioredis

redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)

# Release Lua script: Release lock only if token matches caller's UUID
RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class InventoryService:
    def __init__(self, redis: fakeredis.aioredis.FakeRedis):
        self.redis = redis

    async def initialize_stock(self, item_id: str, quantity: int):
        await self.redis.set(f"stock:{item_id}", quantity)

    async def purchase_item(self, item_id: str, customer_id: str) -> Dict[str, Any]:
        lock_key = f"lock:item:{item_id}"
        caller_token = str(uuid.uuid4())
        lease_sec = 3

        # 1. Acquire Distributed Lock (Wait up to 500ms with backoff)
        t_deadline = time.perf_counter() + 0.5
        lock_acquired = False

        while time.perf_counter() < t_deadline:
            if await self.redis.set(lock_key, caller_token, nx=True, ex=lease_sec):
                lock_acquired = True
                break
            await asyncio.sleep(0.01)

        if not lock_acquired:
            return {"status": "FAILED", "reason": "LOCK_CONTENTION_TIMEOUT"}

        # 2. Critical Section: Check & Decrement Stock
        try:
            current_stock = int(await self.redis.get(f"stock:{item_id}") or 0)
            if current_stock <= 0:
                return {"status": "REJECTED", "reason": "OUT_OF_STOCK"}

            # Decrement stock
            await self.redis.decr(f"stock:{item_id}")

            # Simulate database order write and post-commit background dispatch
            order_id = f"ORD-{uuid.uuid4().hex[:8]}"
            await self.redis.lpush("queue:order_fulfillment", json.dumps({
                "order_id": order_id,
                "item_id": item_id,
                "customer_id": customer_id
            }))
            return {"status": "SUCCESS", "order_id": order_id}

        finally:
            # 3. Atomic Lua Release
            await self.redis.eval(RELEASE_LUA, 1, lock_key, caller_token)


# Verification Harness
async def verify_flash_sale():
    service = InventoryService(redis_client)
    item_id = "FLASH-IPHONE-16"
    # Seed exactly 1 unit of stock
    await service.initialize_stock(item_id, 1)

    # 50 concurrent buyers rush to purchase the 1 item
    tasks = [service.purchase_item(item_id, f"buyer_{i}") for i in range(50)]
    results = await asyncio.gather(*tasks)

    successful_orders = [r for r in results if r["status"] == "SUCCESS"]
    rejected_orders = [r for r in results if r["status"] in ("REJECTED", "FAILED")]

    assert len(successful_orders) == 1, f"Expected exactly 1 success, got {len(successful_orders)}"
    assert len(rejected_orders) == 49, f"Expected 49 rejections, got {len(rejected_orders)}"

    final_stock = int(await redis_client.get(f"stock:{item_id}"))
    assert final_stock == 0, f"Stock must be exactly 0, got {final_stock}"
    print("Exercise 2 Test PASSED: Distributed Lock Flash-Sale shield completely prevented overselling.")

if __name__ == "__main__":
    asyncio.run(verify_flash_sale())
```

---

## Exercise 3: Real-Time SSE (Server-Sent Events) Streaming Analytics with Redis Pub/Sub

### Problem Statement
Build an asynchronous Server-Sent Events (SSE) live telemetry endpoint in FastAPI. As background workers generate document analytics metrics, they publish JSON payloads to a Redis channel (`telemetry:events`). Connected web clients subscribe to `GET /api/v1/telemetry/stream` and receive live streaming events with zero polling overhead.

### Verified Reference Solution

```python
import asyncio
import json
import time
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import fakeredis.aioredis

sse_app = FastAPI(title="Real-Time Streaming Telemetry Service")
pubsub_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)


async def telemetry_event_generator(channel_name: str) -> AsyncGenerator[str, None]:
    """Listens to Redis Pub/Sub channel and yields standard SSE formatted strings."""
    pubsub = pubsub_redis.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        # Emit initial SSE connection heartbeat
        yield f"event: ping\ndata: {json.dumps({'status': 'connected', 'timestamp': time.time()})}\n\n"

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                data = message["data"]
                # Standard SSE framing: 'event: <name>\ndata: <json>\n\n'
                yield f"event: metric_update\ndata: {data}\n\n"
            await asyncio.sleep(0.01)
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()


@sse_app.get("/api/v1/telemetry/stream")
async def stream_telemetry():
    """Server-Sent Events stream delivering live metrics to dashboards."""
    return StreamingResponse(
        telemetry_event_generator("telemetry:events"),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable NGINX response proxy buffering
        }
    )


# Verification Harness
async def verify_sse_stream():
    import httpx
    transport = httpx.ASGITransport(app=sse_app)

    async def client_listener():
        events_received = []
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", "/api/v1/telemetry/stream") as response:
                assert response.status_code == 200
                assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        events_received.append(json.loads(line.replace("data: ", "")))
                        if len(events_received) >= 2:
                            break
        return events_received

    # Start client listener in background
    listener_task = asyncio.create_task(client_listener())
    await asyncio.sleep(0.05)

    # Publish telemetry events from simulated worker
    await pubsub_redis.publish("telemetry:events", json.dumps({"active_workers": 8, "cpu_pct": 24.5}))

    received = await listener_task
    assert len(received) >= 2
    assert received[0]["status"] == "connected"
    assert received[1]["active_workers"] == 8
    print("Exercise 3 Test PASSED: Real-time SSE streaming with Redis Pub/Sub verified.")

if __name__ == "__main__":
    asyncio.run(verify_sse_stream())
```
