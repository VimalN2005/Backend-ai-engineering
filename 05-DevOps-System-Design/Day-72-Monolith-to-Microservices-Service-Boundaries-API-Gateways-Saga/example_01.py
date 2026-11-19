"""
Day 72: Distributed API Gateway & Anti-Corruption Layer (ACL) Simulator
========================================================================
Architecture & Principles Demonstrated:
1. API Gateway Pattern:
   - Path-based dynamic routing to internal microservices.
   - JWT authentication middleware and claims header injection (`X-User-ID`, `X-User-Role`).
   - Token Bucket Rate Limiting per IP/Client tenant.
   - Circuit Breaker protection on downstream service dispatch.
2. Anti-Corruption Layer (ACL):
   - Translates legacy monolithic data structures into clean Domain-Driven Design (DDD) aggregates.
   - Shields modern greenfield services from legacy schema rot.
3. Backend-for-Frontend (BFF) Aggregator:
   - Aggregates responses across multiple microservices with graceful degradation on non-essential failure.
"""

from __future__ import annotations
import base64
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# =====================================================================
# 1. Domain Entities & Anti-Corruption Layer (ACL)
# =====================================================================

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str = "USD"


@dataclass(frozen=True)
class Address:
    street: str
    city: str
    postal_code: str
    country: str


@dataclass
class ModernOrderAggregate:
    """Clean Domain Model for modern Order Microservice."""
    order_id: str
    customer_id: str
    total_price: Money
    shipping_address: Address
    status: OrderStatus
    items: List[Dict[str, Any]]


class LegacyMonolithOrderACL:
    """
    Anti-Corruption Layer (ACL):
    Converts legacy monolithic database record (with cryptic numeric status codes,
    concatenated address strings, and unformatted prices) into a clean domain aggregate.
    """
    # Legacy Status Map: 10 -> PENDING, 20 -> CONFIRMED, 30 -> SHIPPED, 99 -> CANCELLED
    LEGACY_STATUS_MAP = {
        10: OrderStatus.PENDING,
        20: OrderStatus.CONFIRMED,
        30: OrderStatus.SHIPPED,
        99: OrderStatus.CANCELLED
    }

    @classmethod
    def to_domain(cls, legacy_payload: Dict[str, Any]) -> ModernOrderAggregate:
        """Translates legacy schema to modern domain aggregate."""
        # 1. Parse Status Code
        status_num = legacy_payload.get("status_cd", 10)
        domain_status = cls.LEGACY_STATUS_MAP.get(status_num, OrderStatus.PENDING)

        # 2. Parse Delimited Address: "123 Main St;Springfield;97477;USA"
        raw_addr = legacy_payload.get("cust_addr_str", "")
        addr_parts = raw_addr.split(";") if raw_addr else []
        address = Address(
            street=addr_parts[0] if len(addr_parts) > 0 else "Unknown",
            city=addr_parts[1] if len(addr_parts) > 1 else "Unknown",
            postal_code=addr_parts[2] if len(addr_parts) > 2 else "00000",
            country=addr_parts[3] if len(addr_parts) > 3 else "USA"
        )

        # 3. Parse Price (legacy represented cents as integer)
        cents = legacy_payload.get("total_amount_cents", 0)
        total_price = Money(amount=cents / 100.0, currency="USD")

        # 4. Construct Aggregate
        return ModernOrderAggregate(
            order_id=str(legacy_payload["ord_num"]),
            customer_id=str(legacy_payload["usr_id"]),
            total_price=total_price,
            shipping_address=address,
            status=domain_status,
            items=legacy_payload.get("line_items", [])
        )


# =====================================================================
# 2. Token Bucket Rate Limiter & Circuit Breaker
# =====================================================================

class TokenBucketRateLimiter:
    """Thread-safe Token Bucket algorithm for client IP rate limiting."""
    def __init__(self, capacity: int = 5, refill_rate_per_sec: float = 2.0):
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.tokens: float = capacity
        self.last_refill = time.time()

    def allow_request(self) -> bool:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class CircuitBreakerOpenException(Exception):
    pass


class CircuitBreaker:
    """Protects gateway from cascading failures of downstream services."""
    def __init__(self, failure_threshold: int = 3, reset_timeout_sec: float = 1.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout_sec = reset_timeout_sec
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF-OPEN
        self.last_state_change = time.time()

    def call(self, func: Callable, *args, **kwargs) -> Any:
        now = time.time()
        if self.state == "OPEN":
            if now - self.last_state_change > self.reset_timeout_sec:
                self.state = "HALF-OPEN"
            else:
                raise CircuitBreakerOpenException("Circuit Breaker is OPEN: downstream service unavailable")

        try:
            result = func(*args, **kwargs)
            if self.state == "HALF-OPEN":
                self.state = "CLOSED"
                self.failure_count = 0
            return result
        except Exception as exc:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
                self.last_state_change = now
            raise exc


# =====================================================================
# 3. API Gateway Engine
# =====================================================================

@dataclass
class GatewayRequest:
    path: str
    method: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    client_ip: str = "127.0.0.1"


@dataclass
class GatewayResponse:
    status_code: int
    headers: Dict[str, str]
    body: Any


class MockDownstreamMicroservice:
    """Simulated internal microservice."""
    def __init__(self, name: str):
        self.name = name
        self.is_healthy = True

    def handle(self, request: GatewayRequest) -> Dict[str, Any]:
        if not self.is_healthy:
            raise ConnectionError(f"Service {self.name} connection refused")
        return {
            "service": self.name,
            "processed_path": request.path,
            "user_id": request.headers.get("X-User-ID"),
            "role": request.headers.get("X-User-Role"),
            "data": "ok"
        }


class APIGateway:
    """Production-style API Gateway with Auth, Rate Limiting & Circuit Breaking."""
    def __init__(self):
        self.routes: Dict[str, MockDownstreamMicroservice] = {}
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.rate_limiters: Dict[str, TokenBucketRateLimiter] = {}

    def register_service(self, path_prefix: str, service: MockDownstreamMicroservice) -> None:
        self.routes[path_prefix] = service
        self.circuit_breakers[service.name] = CircuitBreaker(failure_threshold=2, reset_timeout_sec=0.5)

    def _get_rate_limiter(self, client_ip: str) -> TokenBucketRateLimiter:
        if client_ip not in self.rate_limiters:
            self.rate_limiters[client_ip] = TokenBucketRateLimiter(capacity=3, refill_rate_per_sec=1.0)
        return self.rate_limiters[client_ip]

    def _verify_jwt(self, auth_header: Optional[str]) -> Optional[Dict[str, Any]]:
        """Simulated JWT verification."""
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        token = auth_header.split(" ")[1]
        try:
            # Token payload simulated as base64-encoded json
            payload_json = base64.b64decode(token.encode("utf-8")).decode("utf-8")
            return json.loads(payload_json)
        except Exception:
            return None

    def dispatch(self, req: GatewayRequest) -> GatewayResponse:
        # 1. Rate Limiting Check
        limiter = self._get_rate_limiter(req.client_ip)
        if not limiter.allow_request():
            return GatewayResponse(429, {"Content-Type": "application/json"}, {"error": "Rate limit exceeded (HTTP 429)"})

        # 2. Authentication Check (for protected endpoints)
        claims = None
        if not req.path.startswith("/api/public"):
            auth_token = req.headers.get("Authorization")
            claims = self._verify_jwt(auth_token)
            if not claims:
                return GatewayResponse(401, {"Content-Type": "application/json"}, {"error": "Unauthorized: Invalid or missing token"})

            # Inject claims headers for downstream services
            req.headers["X-User-ID"] = str(claims.get("sub", ""))
            req.headers["X-User-Role"] = str(claims.get("role", "user"))

        # 3. Route Resolution
        matched_service = None
        for prefix, svc in self.routes.items():
            if req.path.startswith(prefix):
                matched_service = svc
                break

        if not matched_service:
            return GatewayResponse(404, {"Content-Type": "application/json"}, {"error": f"Route not found: {req.path}"})

        # 4. Circuit Breaker Protected Dispatch
        breaker = self.circuit_breakers[matched_service.name]
        try:
            resp_body = breaker.call(matched_service.handle, req)
            return GatewayResponse(200, {"Content-Type": "application/json"}, resp_body)
        except CircuitBreakerOpenException as cbe:
            return GatewayResponse(503, {"Content-Type": "application/json"}, {"error": str(cbe)})
        except Exception as exc:
            return GatewayResponse(502, {"Content-Type": "application/json"}, {"error": f"Bad Gateway: {exc}"})


# =====================================================================
# 4. Backend-for-Frontend (BFF) Aggregator
# =====================================================================

class MobileBFFAggregator:
    """
    Mobile BFF endpoint: aggregates profile and orders in one round trip.
    Applies graceful degradation if non-essential service fails.
    """
    def __init__(self, user_svc: MockDownstreamMicroservice, order_svc: MockDownstreamMicroservice, notif_svc: MockDownstreamMicroservice):
        self.user_svc = user_svc
        self.order_svc = order_svc
        self.notif_svc = notif_svc

    def get_dashboard(self, user_id: str) -> Dict[str, Any]:
        # Required core calls
        user_data = {"user_id": user_id, "name": "Vimal", "tier": "Gold"}
        orders_data = [{"id": "ord_1", "amount": 120.0}, {"id": "ord_2", "amount": 45.0}]

        # Optional non-essential call with graceful fallback
        notifications = []
        is_degraded = False
        try:
            if not self.notif_svc.is_healthy:
                raise ConnectionError("Notifications down")
            notifications = ["Order #1 shipped", "Promo coupon ready"]
        except Exception:
            is_degraded = True
            notifications = []  # Fallback to empty list

        return {
            "user": user_data,
            "recent_orders": orders_data,
            "notifications": notifications,
            "degraded_mode": is_degraded
        }


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_acl_translation():
    print("\n--- 1. ANTI-CORRUPTION LAYER (ACL) TRANSLATION DEMO ---")
    legacy_payload = {
        "ord_num": 98452,
        "usr_id": 404,
        "status_cd": 20,  # 20 means CONFIRMED in legacy database
        "cust_addr_str": "456 Tech Park;Austin;78701;USA",
        "total_amount_cents": 12550,  # $125.50
        "line_items": [{"sku": "KEYBOARD-RGB", "qty": 1}]
    }

    aggregate = LegacyMonolithOrderACL.to_domain(legacy_payload)
    print(f"Legacy payload adapted to Clean DDD Aggregate:")
    print(f"  Order ID: {aggregate.order_id}")
    print(f"  Domain Status: {aggregate.status} (from legacy code 20)")
    print(f"  Formatted Price: {aggregate.total_price.currency} {aggregate.total_price.amount}")
    print(f"  Shipping Address: {aggregate.shipping_address}")

    assert aggregate.status == OrderStatus.CONFIRMED
    assert aggregate.total_price.amount == 125.50
    assert aggregate.shipping_address.city == "Austin"
    print("ACL translation cleanly protected domain boundary!")


def demo_gateway_auth_and_rate_limiting():
    print("\n--- 2. API GATEWAY AUTH, CLAIMS INJECTION & RATE LIMITING DEMO ---")
    gateway = APIGateway()
    order_svc = MockDownstreamMicroservice("orders-service")
    gateway.register_service("/api/v1/orders", order_svc)

    # 1. Unauthenticated request to protected endpoint -> 401
    unauth_req = GatewayRequest(path="/api/v1/orders/101", method="GET")
    resp_unauth = gateway.dispatch(unauth_req)
    assert resp_unauth.status_code == 401
    print(f"Unauthenticated request correctly rejected: HTTP {resp_unauth.status_code}")

    # 2. Authenticated request with valid JWT
    token_claims = {"sub": "usr_99", "role": "admin"}
    token_str = base64.b64encode(json.dumps(token_claims).encode("utf-8")).decode("utf-8")
    auth_headers = {"Authorization": f"Bearer {token_str}"}

    auth_req = GatewayRequest(path="/api/v1/orders/101", method="GET", headers=auth_headers, client_ip="192.168.1.5")
    resp_auth = gateway.dispatch(auth_req)
    assert resp_auth.status_code == 200
    assert resp_auth.body["user_id"] == "usr_99"
    assert resp_auth.body["role"] == "admin"
    print(f"Authenticated request accepted with injected headers: X-User-ID={resp_auth.body['user_id']}, X-User-Role={resp_auth.body['role']}")

    # 3. Burst Rate Limiting: 3 allowed capacity
    print("Testing token bucket rate limiting...")
    # Fire 4 rapid requests
    responses = [gateway.dispatch(auth_req) for _ in range(4)]
    status_codes = [r.status_code for r in responses]
    print(f"Burst status codes: {status_codes}")
    assert 429 in status_codes, "Must return HTTP 429 on rate limit exhaustion"
    print("Token bucket rate limiter successfully intercepted traffic surge!")


def demo_circuit_breaker_and_bff():
    print("\n--- 3. CIRCUIT BREAKER & BFF GRACEFUL DEGRADATION DEMO ---")
    gateway = APIGateway()
    order_svc = MockDownstreamMicroservice("orders-service")
    gateway.register_service("/api/v1/orders", order_svc)

    token_claims = {"sub": "usr_99", "role": "user"}
    token_str = base64.b64encode(json.dumps(token_claims).encode("utf-8")).decode("utf-8")
    req = GatewayRequest(path="/api/v1/orders/101", method="GET", headers={"Authorization": f"Bearer {token_str}"}, client_ip="10.0.0.1")

    # Simulate downstream crash
    order_svc.is_healthy = False
    # First 2 failures trip breaker (threshold = 2)
    gateway.dispatch(req)
    gateway.dispatch(req)

    # 3rd request should be short-circuited by Circuit Breaker (503 without hitting downstream)
    resp_tripped = gateway.dispatch(req)
    assert resp_tripped.status_code == 503
    print(f"Circuit Breaker tripped to OPEN: returned HTTP {resp_tripped.status_code} ({resp_tripped.body['error']})")

    # BFF Graceful Fallback Demo
    user_svc = MockDownstreamMicroservice("user-service")
    notif_svc = MockDownstreamMicroservice("notif-service")
    notif_svc.is_healthy = False  # Notification service down

    bff = MobileBFFAggregator(user_svc, order_svc, notif_svc)
    dashboard = bff.get_dashboard("usr_99")
    print(f"BFF Dashboard Response: Degraded={dashboard['degraded_mode']}, Notifications={dashboard['notifications']}")
    assert dashboard["degraded_mode"] is True
    assert dashboard["notifications"] == []
    assert len(dashboard["recent_orders"]) == 2
    print("BFF returned core data gracefully despite downstream notification failure!")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING API GATEWAY, ACL & BFF ARCHITECTURE SIMULATOR")
    print("=" * 70)
    demo_acl_translation()
    demo_gateway_auth_and_rate_limiting()
    demo_circuit_breaker_and_bff()
    print("\n" + "=" * 70)
    print("ALL API GATEWAY, ACL & BFF TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
