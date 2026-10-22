# Day 31 Rapid Revision: Web Protocols & REST Standards

A condensed 5-minute reference sheet for TCP/IP, HTTP 1.1/2/3 evolution, REST idempotency contracts, and CORS security.

---

## 1. Architectural & Protocol Decision Matrix

| Dimension | HTTP/1.1 | HTTP/2 | HTTP/3 (QUIC) |
|---|---|---|---|
| **Underlying Transport** | TCP | TCP | **UDP** |
| **Connection Framing** | Plaintext ASCII | Binary Frames | Binary Frames |
| **Multiplexing** | Sequential (Keep-Alive) | Streams over 1 TCP socket | Streams over UDP datagrams |
| **Head-of-Line Blocking** | Severe (App level) | Moderate (TCP level) | **Zero (Transport level)** |
| **Connection Handshake** | 2 RTT (TCP + TLS) | 2 RTT (TCP + TLS) | **0-1 RTT (Embedded)** |
| **Connection Migration** | Drops on IP change | Drops on IP change | **Preserved via CID** |

---

## 2. REST HTTP Methods & Invariants

| Method | Safe? | Idempotent? | Semantic Action | Production Best Practice |
|---|---|---|---|---|
| **`GET`** | **YES** | **YES** | Read-only resource retrieval | Never mutate server state. Cache with ETags. |
| **`HEAD`** | **YES** | **YES** | Headers only (no body) | Use for checking resource existence/size. |
| **`OPTIONS`**| **YES** | **YES** | CORS preflight & capabilities | Return `204 No Content` with `Access-Control-*`. |
| **`PUT`** | NO | **YES** | **Complete replacement** | Overwrites resource. Missing fields are wiped. |
| **`DELETE`**| NO | **YES** | Resource removal | Second call on deleted item is still idempotent. |
| **`POST`** | NO | **NO** | Sub-resource creation / Action | Must guard with `Idempotency-Key` on billing. |
| **`PATCH`** | NO | **NO\*** | Partial resource modification | Only alters provided fields. |

---

## 3. Core Reusable Code Templates

### Template 1: ETag Conditional Validation (HTTP 304)
```python
import hashlib

def handle_conditional_get(payload: bytes, client_if_none_match: str | None) -> tuple[int, dict, bytes]:
    etag = f'"{hashlib.sha256(payload).hexdigest()[:16]}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=3600, must-revalidate"}

    # If client has matching ETag, return 304 with ZERO body bytes
    if client_if_none_match and client_if_none_match == etag:
        return 304, headers, b""

    return 200, headers, payload
```

### Template 2: CORS Preflight Handler (`OPTIONS`)
```python
def handle_cors_preflight(request_origin: str, allowed_origins: set[str]) -> tuple[int, dict]:
    if request_origin not in allowed_origins:
        return 403, {}

    headers = {
        "Access-Control-Allow-Origin": request_origin,
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key",
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }
    return 204, headers
```

### Template 3: Distributed Idempotency Key Guard
```python
# Atomic Redis lock and check pattern
def process_idempotent_payment(idempotency_key: str, amount: float, redis_client):
    cache_key = f"idemp:{idempotency_key}"
    # Atomic Set-If-Not-Exists with 120s TTL
    acquired = redis_client.set(cache_key, "IN_FLIGHT", nx=True, ex=120)

    if not acquired:
        status = redis_client.get(cache_key)
        if status == "IN_FLIGHT":
            return 409, {"error": "Concurrent request in progress"}
        return 200, json.loads(status)  # Return cached response

    # Process charge
    charge_result = charge_card(amount)
    # Store result for future retries (24-hour retention)
    redis_client.set(cache_key, json.dumps(charge_result), ex=86400)
    return 201, charge_result
```

---

## 4. Non-Negotiable Invariants & Rules

1. **`GET` Must Be Safe**: Never trigger mutations on `GET` requests (crawlers and browser pre-fetchers will invoke them).
2. **Never Use `*` with Credentials in CORS**: `Access-Control-Allow-Origin: *` is rejected by browsers when `credentials: true`. Always reflect validated origins.
3. **Always Use Connection Pooling (`Keep-Alive`)**: Reusing persistent TCP sockets eliminates 1-2 RTT latency and prevents `TIME_WAIT` socket port exhaustion.
4. **Mandatory Socket Timeouts**: Always specify connection and read timeouts on outbound requests (e.g. `timeout=(2.0, 5.0)`) to prevent thread starvation.
5. **Differentiate 401 vs 403**: Use `401 Unauthorized` for missing/invalid auth tokens; use `403 Forbidden` for valid tokens lacking permissions (RBAC).

---

## 5. Critical HTTP Status Codes Reference Table

| Code | Name | Semantic Meaning |
|---|---|---|
| **200** | OK | Standard successful response. |
| **201** | Created | Resource successfully created via `POST` (includes `Location` header). |
| **204** | No Content | Action succeeded; response contains zero body bytes (e.g. `DELETE` or `OPTIONS`). |
| **301** | Moved Permanently | Resource moved; browsers update bookmarks permanently. |
| **304** | Not Modified | Client cached copy is still valid (ETag matched); body is empty. |
| **400** | Bad Request | Client sent malformed syntax or invalid query parameters. |
| **401** | Unauthorized | Authentication is missing or invalid. User must authenticate. |
| **403** | Forbidden | User is authenticated, but lacks permissions (RBAC denial). |
| **404** | Not Found | Requested URI does not exist on the server. |
| **409** | Conflict | Request conflicts with current server state (duplicate key in progress). |
| **422** | Unprocessable Entity | Syntax is valid JSON, but fails schema/semantic validation. |
| **429** | Too Many Requests | Rate limit exceeded. Returns `Retry-After` header. |
| **500** | Internal Error | Unhandled server exception / crash. |
| **502** | Bad Gateway | Ingress proxy received an invalid response from upstream application. |
| **503** | Service Unavailable | Server temporarily overloaded or down for maintenance. |
| **504** | Gateway Timeout | Ingress proxy timed out waiting for upstream application response. |
