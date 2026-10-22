"""
Production Architecture: Enterprise Ingress API Gateway HTTP Pipeline.

Demonstrates production edge proxy and API gateway architectures (e.g. Nginx, Envoy, Kong):
1. CorsMiddleware:
   - Evaluates Same-Origin Policy (SOP) and CORS Preflight handshakes (OPTIONS).
   - Dynamically checks whitelisted origins and sets Access-Control-* headers.
2. ETagConditionalCacheMiddleware:
   - Generates SHA-256 ETag content digests.
   - Evaluates `If-None-Match` headers, returning HTTP 304 Not Modified with zero body bytes.
3. DistributedIdempotencyManager:
   - Enforces exactly-once execution for state-mutating requests (POST /payments)
     using client-provided `Idempotency-Key` headers.
   - Detects concurrent duplicate in-flight requests and returns cached responses on retry.
4. ApiGatewayPipeline:
   - Integrates the entire request-response lifecycle with status code dispatching.
5. High-Throughput Performance Benchmark:
   - Evaluates gateway pipeline execution across 20,000 requests.
"""

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# =====================================================================
# DOMAIN MODELS & HTTP ENUMS
# =====================================================================

@dataclass
class HttpRequestContext:
    """Parsed HTTP Request context passing through middleware pipeline."""
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    client_ip: str = "127.0.0.1"


@dataclass
class HttpResponseContext:
    """HTTP Response context returned by gateway."""
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


# =====================================================================
# 1. CORS PREFLIGHT MIDDLEWARE
# =====================================================================

class CorsMiddleware:
    """
    Production Cross-Origin Resource Sharing (CORS) Engine.
    
    WHY CORS MATTERS:
    Browser Same-Origin Policy (SOP) blocks frontend single-page apps (SPAs)
    on `https://app.example.com` from reading API responses on `https://api.example.com`.
    This engine validates origin trust and handles OPTIONS preflight handshakes.
    """

    def __init__(
        self,
        allowed_origins: Set[str],
        allowed_methods: List[str] = None,
        allowed_headers: List[str] = None,
        max_age_seconds: int = 86400,
    ):
        self.allowed_origins = allowed_origins
        self.allowed_methods = allowed_methods or ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        self.allowed_headers = allowed_headers or ["Authorization", "Content-Type", "Idempotency-Key", "X-Requested-With"]
        self.max_age = max_age_seconds

    def handle_preflight(self, req: HttpRequestContext) -> HttpResponseContext:
        """Handles HTTP OPTIONS preflight handshake."""
        origin = req.headers.get("origin", "")
        if origin not in self.allowed_origins and "*" not in self.allowed_origins:
            # Origin not allowed -> 403 Forbidden
            return HttpResponseContext(status_code=403, body=b"CORS Origin Denied")

        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": ", ".join(self.allowed_methods),
            "Access-Control-Allow-Headers": ", ".join(self.allowed_headers),
            "Access-Control-Max-Age": str(self.max_age),
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
        return HttpResponseContext(status_code=204, headers=headers)

    def attach_cors_headers(self, req: HttpRequestContext, resp: HttpResponseContext) -> None:
        """Appends Access-Control-Allow-Origin to actual responses."""
        origin = req.headers.get("origin")
        if origin and (origin in self.allowed_origins or "*" in self.allowed_origins):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Vary"] = "Origin"


# =====================================================================
# 2. ETAG CONDITIONAL CACHING MIDDLEWARE
# =====================================================================

class ETagConditionalCacheMiddleware:
    """
    Evaluates HTTP 304 Not Modified validation caching.
    
    WHY ETAG CONDITIONAL REQUESTS?
    If an AI response or user record has not changed, transmitting the payload again
    wastes megabytes of egress bandwidth. Returning HTTP 304 with an empty body
    takes 0 payload bytes and reduces client rendering latency.
    """

    @staticmethod
    def generate_etag(content: bytes) -> str:
        """Generates a strong ETag based on SHA-256 digest."""
        digest = hashlib.sha256(content).hexdigest()[:16]
        return f'"{digest}"'

    def process_response(self, req: HttpRequestContext, resp: HttpResponseContext) -> HttpResponseContext:
        if req.method not in ("GET", "HEAD") or resp.status_code != 200:
            return resp

        # Generate ETag for current response body
        etag = self.generate_etag(resp.body)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"

        # Check client's If-None-Match header
        client_etag = req.headers.get("if-none-match")
        if client_etag and client_etag == etag:
            # Cache Hit: Body is stripped; return 304 Not Modified
            return HttpResponseContext(
                status_code=304,
                headers=resp.headers,
                body=b"",  # ZERO BODY BYTES
            )

        return resp


# =====================================================================
# 3. DISTRIBUTED IDEMPOTENCY KEY MANAGER
# =====================================================================

@dataclass
class IdempotencyRecord:
    status: str  # "IN_FLIGHT" or "COMPLETED"
    response: Optional[HttpResponseContext] = None
    created_at: float = field(default_factory=time.time)


class DistributedIdempotencyManager:
    """
    Enterprise Idempotency Engine for state-mutating requests (POST /orders).
    
    Prevents double-charging or duplicate resource creation when clients retry
    due to dropped network connections.
    """

    def __init__(self, ttl_seconds: int = 86400):
        self.ttl_seconds = ttl_seconds
        # In production, this dictionary is backed by Redis key-value storage with TTL
        self._store: Dict[str, IdempotencyRecord] = {}

    def get_or_lock(self, idempotency_key: str) -> Tuple[bool, Optional[HttpResponseContext]]:
        """
        Attempts to acquire lock for key.
        Returns: (is_new_request, cached_response_if_completed)
        """
        now = time.time()
        record = self._store.get(idempotency_key)

        if record is not None:
            # Check TTL
            if now - record.created_at > self.ttl_seconds:
                del self._store[idempotency_key]
            elif record.status == "COMPLETED" and record.response is not None:
                # Return identical cached response
                return False, record.response
            elif record.status == "IN_FLIGHT":
                # Conflict: Previous request is still processing
                conflict_resp = HttpResponseContext(
                    status_code=409,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"error": "Concurrent request in progress for this Idempotency-Key"}).encode(),
                )
                return False, conflict_resp

        # Mark as IN_FLIGHT
        self._store[idempotency_key] = IdempotencyRecord(status="IN_FLIGHT", created_at=now)
        return True, None

    def commit(self, idempotency_key: str, resp: HttpResponseContext) -> None:
        """Stores final completed response for future retries."""
        self._store[idempotency_key] = IdempotencyRecord(
            status="COMPLETED",
            response=resp,
            created_at=time.time(),
        )


# =====================================================================
# 4. FULL API GATEWAY PIPELINE
# =====================================================================

class ApiGatewayPipeline:
    """
    Production Ingress Pipeline combining CORS, ETag Caching, and Idempotency.
    """

    def __init__(self, allowed_origins: Set[str]):
        self.cors = CorsMiddleware(allowed_origins=allowed_origins)
        self.etag_cache = ETagConditionalCacheMiddleware()
        self.idempotency = DistributedIdempotencyManager()
        # Simulated database of accounts
        self.accounts: Dict[str, float] = {"user_1": 1000.0}

    def dispatch(self, req: HttpRequestContext) -> HttpResponseContext:
        # Step 1: Handle CORS Preflight
        if req.method == "OPTIONS":
            return self.cors.handle_preflight(req)

        # Step 2: Handle Idempotency for POST/PUT requests
        idempotency_key = req.headers.get("idempotency-key")
        if req.method == "POST" and idempotency_key:
            is_new, cached = self.idempotency.get_or_lock(idempotency_key)
            if not is_new and cached is not None:
                self.cors.attach_cors_headers(req, cached)
                return cached

        # Step 3: Route to application handler
        resp = self._handle_route(req)

        # Step 4: Commit Idempotency record if applicable
        if req.method == "POST" and idempotency_key and resp.is_success:
            self.idempotency.commit(idempotency_key, resp)

        # Step 5: Evaluate ETag conditional caching
        resp = self.etag_cache.process_response(req, resp)

        # Step 6: Attach CORS headers to outgoing response
        self.cors.attach_cors_headers(req, resp)

        return resp

    def _handle_route(self, req: HttpRequestContext) -> HttpResponseContext:
        """Simulated backend business logic."""
        if req.path == "/v1/account" and req.method == "GET":
            payload = json.dumps({"account_id": "user_1", "balance": self.accounts["user_1"]}).encode()
            return HttpResponseContext(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body=payload,
            )

        if req.path == "/v1/transfer" and req.method == "POST":
            try:
                data = json.loads(req.body.decode("utf-8"))
                amount = float(data.get("amount", 0))
            except Exception:
                return HttpResponseContext(status_code=400, body=b'{"error": "Invalid JSON body"}')

            if amount <= 0 or amount > self.accounts["user_1"]:
                return HttpResponseContext(status_code=422, body=b'{"error": "Unprocessable Entity: Insufficient funds"}')

            self.accounts["user_1"] -= amount
            payload = json.dumps({"status": "transferred", "remaining_balance": self.accounts["user_1"]}).encode()
            return HttpResponseContext(
                status_code=201,
                headers={"Content-Type": "application/json"},
                body=payload,
            )

        return HttpResponseContext(status_code=404, body=b'{"error": "Not Found"}')


# =====================================================================
# 5. UNIT TESTS & VERIFICATION
# =====================================================================

def run_unit_tests():
    print("=" * 70)
    print("RUNNING UNIT TESTS FOR API GATEWAY PIPELINE")
    print("=" * 70)

    gateway = ApiGatewayPipeline(allowed_origins={"https://app.enterprise.com"})

    # Test 1: CORS Preflight OPTIONS Handshake
    preflight_req = HttpRequestContext(
        method="OPTIONS",
        path="/v1/transfer",
        headers={"origin": "https://app.enterprise.com", "access-control-request-method": "POST"},
    )
    preflight_resp = gateway.dispatch(preflight_req)
    assert preflight_resp.status_code == 204
    assert preflight_resp.headers["Access-Control-Allow-Origin"] == "https://app.enterprise.com"
    print("[PASS] CORS preflight OPTIONS handshake (204 No Content) verified.")

    # Test 2: GET with ETag Generation and Subsequent 304 Not Modified
    get_req1 = HttpRequestContext(method="GET", path="/v1/account")
    get_resp1 = gateway.dispatch(get_req1)
    assert get_resp1.status_code == 200
    assert "ETag" in get_resp1.headers
    etag = get_resp1.headers["ETag"]

    # Second GET with If-None-Match header -> 304 Not Modified
    get_req2 = HttpRequestContext(method="GET", path="/v1/account", headers={"if-none-match": etag})
    get_resp2 = gateway.dispatch(get_req2)
    assert get_resp2.status_code == 304
    assert get_resp2.body == b""  # Zero payload bytes transferred!
    print(f"[PASS] ETag conditional validation (304 Not Modified) verified: {etag}.")

    # Test 3: Idempotent Payment Processing
    # Initial balance = 1000. Transfer 200 with unique idempotency key.
    tx_req1 = HttpRequestContext(
        method="POST",
        path="/v1/transfer",
        headers={"idempotency-key": "tx_uuid_999"},
        body=json.dumps({"amount": 200.0}).encode(),
    )
    tx_resp1 = gateway.dispatch(tx_req1)
    assert tx_resp1.status_code == 201
    assert gateway.accounts["user_1"] == 800.0

    # Client network fails and retries with IDENTICAL idempotency key!
    tx_resp2 = gateway.dispatch(tx_req1)
    assert tx_resp2.status_code == 201
    # Balance MUST STILL BE 800.0 (NOT 600.0)!
    assert gateway.accounts["user_1"] == 800.0, "Idempotency failed: Customer double-charged!"
    print("[PASS] Distributed Idempotency Key protection verified: Zero double-billing on retry.")
    print("All gateway pipeline unit tests passed successfully!\n")


# =====================================================================
# 6. HIGH-VOLUME PERFORMANCE BENCHMARK
# =====================================================================

def run_performance_benchmark():
    print("=" * 70)
    print("HIGH-VOLUME GATEWAY BENCHMARK: 20,000 REQUESTS")
    print("=" * 70)

    gateway = ApiGatewayPipeline(allowed_origins={"https://app.enterprise.com"})
    total_requests = 20_000

    # Simulate mixed production workload:
    # 70% GET (with ETag validation), 20% CORS Preflight, 10% Idempotent POST
    reqs: List[HttpRequestContext] = []
    # Seed account for ETag
    seed_resp = gateway.dispatch(HttpRequestContext(method="GET", path="/v1/account"))
    cached_etag = seed_resp.headers["ETag"]

    for i in range(total_requests):
        if i % 10 < 7:
            # GET with cached ETag
            reqs.append(HttpRequestContext(
                method="GET",
                path="/v1/account",
                headers={"origin": "https://app.enterprise.com", "if-none-match": cached_etag},
            ))
        elif i % 10 < 9:
            # CORS Preflight
            reqs.append(HttpRequestContext(
                method="OPTIONS",
                path="/v1/transfer",
                headers={"origin": "https://app.enterprise.com"},
            ))
        else:
            # Idempotent retry
            reqs.append(HttpRequestContext(
                method="POST",
                path="/v1/transfer",
                headers={"origin": "https://app.enterprise.com", "idempotency-key": "fixed_batch_key"},
                body=b'{"amount": 10.0}',
            ))

    print(f"Executing {total_requests:,} requests through full gateway pipeline...")
    start_time = time.perf_counter()

    status_counts = defaultdict(int)
    for r in reqs:
        resp = gateway.dispatch(r)
        status_counts[resp.status_code] += 1

    duration_ms = (time.perf_counter() - start_time) * 1000

    print(f"Total Requests Processed:      {total_requests:,}")
    print(f"HTTP 304 (Cache Not Modified): {status_counts[304]:,}")
    print(f"HTTP 204 (CORS Preflight OK):  {status_counts[204]:,}")
    print(f"HTTP 201 (Idempotent Cached):  {status_counts[201]:,}")
    print(f"Total Pipeline Duration:       {duration_ms:.2f} ms")
    print(f"Average Gateway Latency:       {(duration_ms / total_requests) * 1000:.2f} microseconds / req")
    print(f"Peak Gateway Throughput:       {(total_requests / (duration_ms / 1000)):,.0f} req/sec")
    print("=" * 70)


if __name__ == "__main__":
    run_unit_tests()
    run_performance_benchmark()
