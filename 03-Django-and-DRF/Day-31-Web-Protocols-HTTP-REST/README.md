# Day 31: Web Protocols — TCP/IP, DNS, HTTP 1.1/2/3 & REST Standards

Every web application, microservice mesh, and AI agent relies on the global network stack to communicate. Whether an autonomous agent invokes a tool via an API call or a distributed web service serves millions of users, the underlying infrastructure is governed by **TCP/IP, DNS resolution, the HTTP protocol family, and REST architectural standards**.

Phase 3 transitions from pure algorithmic DSA to **Enterprise Web Systems, Django & DRF Production**. A master backend engineer must understand the entire packet journey—from OS sockets and TCP handshakes to HTTP parsing, caching negotiation, and idempotency guarantees.

---

## 1. The End-to-End Life of a Network Request

When a client or AI agent dispatches an HTTPS request to `https://api.enterprise.com/v1/orders`:

```
+-----------------------------------------------------------------------------------+
|                        THE END-TO-END REQUEST LIFECYCLE                           |
+-----------------------------------------------------------------------------------+
| 1. DNS Resolution     | Domain name -> IP Address (Cache -> Resolver -> Authoritative)
| 2. TCP Handshake      | 3-Way Handshake (SYN -> SYN-ACK -> ACK) establishes stream|
| 3. TLS 1.3 Handshake  | Cryptographic key exchange (ECDH), Certificate validation |
| 4. HTTP Serialization | Client serializes Method, Headers, and Payload into bytes |
| 5. Ingress & Proxy    | AWS ALB / Nginx / Envoy routes to backend application pod |
| 6. WSGI / ASGI Server | Gunicorn / Uvicorn parses raw socket bytes into Request   |
| 7. Framework Handler  | Django / DRF routing, middleware, authentication, view    |
| 8. HTTP Response      | Status line, Response Headers, JSON body streamed back    |
+-----------------------------------------------------------------------------------+
```

---

## 2. The OSI 7-Layer Model vs The 4-Layer TCP/IP Stack

Networking models describe how data is encapsulated as it descends from software code down to physical electrical and optical pulses.

```
       OSI 7-LAYER MODEL                      TCP/IP 4-LAYER MODEL
+-----------------------------+          +-----------------------------+
| Layer 7: Application (HTTP) | <------> |                             |
| Layer 6: Presentation (TLS) | <------> | Application Layer           |
| Layer 5: Session (Sockets)  | <------> | (HTTP, DNS, TLS, SSH)       |
+-----------------------------+          +-----------------------------+
| Layer 4: Transport (TCP/UDP)| <------> | Transport Layer (TCP, UDP)  |
+-----------------------------+          +-----------------------------+
| Layer 3: Network (IP, ICMP) | <------> | Internet Layer (IPv4, IPv6) |
+-----------------------------+          +-----------------------------+
| Layer 2: Data Link (MAC)    | <------> | Network Access / Link Layer |
| Layer 1: Physical (Bits/Wire) <------> | (Ethernet, Wi-Fi, Fiber)    |
+-----------------------------+          +-----------------------------+
```

### Packet Encapsulation
As an HTTP message travels down the stack, each layer prepends a specialized header:
$$\text{[Ethernet Header] [IP Header] [TCP Header] [TLS Record] [HTTP Payload]}$$

---

## 3. TCP (Transmission Control Protocol) Mechanics

TCP provides a **reliable, connection-oriented, ordered byte-stream** abstraction over the unreliable, packet-dropping Internet Protocol (IP).

### 1. The 3-Way Handshake (Connection Establishment)
Before a single byte of HTTP data can be transmitted, the client and server must synchronize sequence numbers:

```
    Client                                                Server
      |                                                     |
      | ----------------- SYN (seq=x) --------------------> | (1 RTT)
      |                                                     |
      | <------------ SYN-ACK (seq=y, ack=x+1) ------------ |
      |                                                     |
      | ----------------- ACK (seq=x+1, ack=y+1) ---------> |
      | (Optional: HTTP Request piggybacked on ACK)         |
```

- **SYN Flood Attack**: An attacker sends thousands of `SYN` packets from spoofed IPs without completing the final `ACK`, exhausting the server's half-open connection backlog.
  - **Defense**: **SYN Cookies** (server encodes connection state inside the initial `seq=y` without allocating memory until the client's `ACK` arrives).

### 2. Congestion Control & Sliding Window Flow Control
- **Flow Control (Sliding Window)**: The receiver advertises its available buffer space (`Window Size`). The sender never transmits more bytes than the receiver can buffer.
- **Congestion Control**: Protects global network routers from bufferbloat:
  - **Slow Start**: Doubles transmission window (`cwnd`) exponentially every RTT.
  - **Congestion Avoidance**: Scales linearly (Additive Increase, Multiplicative Decrease - AIMD) when packet loss is detected.

### 3. Connection Termination & `TIME_WAIT`
Terminating a TCP stream requires a 4-way exchange (`FIN` $\to$ `ACK` $\to$ `FIN` $\to$ `ACK`).
- **The `TIME_WAIT` State**: The endpoint that initiates the active close stays in `TIME_WAIT` for $2 \times \text{MSL}$ (Maximum Segment Lifetime, typically 60 seconds).
- **Backend Danger**: High-traffic API servers opening short-lived outbound TCP connections to databases or Redis without connection pooling will exhaust the ephemeral port range (`TIME_WAIT` socket starvation).

---

## 4. DNS (Domain Name System) Resolution Architecture

DNS converts human-readable hostnames (`api.stripe.com`) into routable IP addresses (`3.220.214.209`).

```
                [ Client / Browser ]
                         |
           (1) Query: "api.stripe.com"
                         v
             [ Local / ISP Recursive Resolver ]
                 /               |               \
   (2) Root NS  /   (3) TLD NS   |   (4) Auth NS   \
      [ . ]    /      [ .com ]   |      [ stripe.com ]
```

### Essential DNS Record Types:
- **A Record**: Maps hostname to an IPv4 address (`api.stripe.com -> 3.220.214.209`).
- **AAAA Record**: Maps hostname to an IPv6 address (`2600:1f18:41c:...`).
- **CNAME (Canonical Name)**: Alias pointing one domain to another (`docs.stripe.com -> stripe.readme.io`).
- **ALIAS / ANAME**: Virtual CNAME for apex/root domains (`stripe.com`).
- **TXT Record**: Arbitrary text used for domain verification, SPF (email spoof defense), and DKIM.

---

## 5. Anatomy of an HTTP/1.1 Transaction

HTTP is an ASCII-based, stateless, application-layer request-response protocol.

```
--------------------------------------------------------------------------------
HTTP REQUEST STRUCTURE
--------------------------------------------------------------------------------
GET /api/v1/users?role=admin HTTP/1.1\r\n     <-- Request Line (Method, URI, Version)
Host: api.enterprise.com\r\n                  <-- Mandatory Host Header in HTTP/1.1
Authorization: Bearer eyJhbGciOi...\r\n      <-- Auth Credential Header
Accept: application/json\r\n                  <-- Desired response format
Connection: keep-alive\r\n                    <-- Reuse TCP connection
\r\n                                          <-- Mandatory CRLF delimiter
{"query": "active"}                           <-- Optional Request Body

--------------------------------------------------------------------------------
HTTP RESPONSE STRUCTURE
--------------------------------------------------------------------------------
HTTP/1.1 200 OK\r\n                           <-- Status Line (Version, Code, Reason)
Content-Type: application/json; charset=utf-8 <-- MIME Type of body
Content-Length: 142\r\n                       <-- Exact body byte count
ETag: "33a64df551425fcc3"\r\n                 <-- Resource version hash for caching
Cache-Control: max-age=3600, must-revalidate\r\n
\r\n                                          <-- Mandatory CRLF delimiter
[{"id": 1, "username": "admin", "active": true}] <-- Response Body
```

---

## 6. The Architectural Evolution: HTTP/1.1 vs HTTP/2 vs HTTP/3

```
+-----------------------------------+--------------------+--------------------+--------------------+
| Architectural Dimension           | HTTP/1.1           | HTTP/2             | HTTP/3 (QUIC)      |
+-----------------------------------+--------------------+--------------------+--------------------+
| Transport Layer Protocol          | TCP                | TCP                | **UDP** (via QUIC) |
| Message Encoding Format           | ASCII Text         | Binary Frames      | Binary Frames      |
| Connection Multiplexing           | No (Pipelining bad)| **Yes (Streams)**  | **Yes (Streams)**  |
| Head-of-Line (HoL) Blocking       | Severe (App-level) | TCP-level HoL      | **Zero HoL**       |
| Header Compression                | None (Redundant)   | HPACK (Static/Dyn) | QPACK (Out-of-order|
| Connection Handshake Latency      | 1-2 RTT (TCP+TLS)  | 1-2 RTT (TCP+TLS)  | **0-1 RTT (QUIC)** |
| Connection Migration (IP Change)  | Broken (TCP drops) | Broken (TCP drops) | **Supported (CID)**|
+-----------------------------------+--------------------+--------------------+--------------------+
```

### The Head-of-Line (HoL) Blocking Problem
- **In HTTP/1.1**: If a client needs 10 images, it must either open 10 separate TCP connections or wait for request 1 to complete before request 2 can be sent on the same connection.
- **In HTTP/2**: Multiplexes multiple requests as independent **binary streams** over a single TCP socket. However, if an underlying TCP packet is dropped in transit, the entire TCP connection stalls while waiting for retransmission (**TCP-level HoL blocking**).
- **In HTTP/3 (QUIC)**: QUIC runs over UDP. Each HTTP stream has independent packet delivery. A dropped packet on stream 1 does not delay or block stream 2!

---

## 7. HTTP Methods & REST Idempotency Contracts

Roy Fielding's **Representational State Transfer (REST)** defines architectural constraints for distributed hypermedia systems.

### Safe vs Idempotent Methods
- **Safe Method**: A method that does not modify resource state on the server. Read-only ($f(x)$ leaves state untouched).
- **Idempotent Method**: A method where executing it $N$ times produces the exact same server state as executing it once ($f(f(x)) = f(x)$).

```
+-----------+--------+------------+-------------------------------------------------+
| Method    | Safe?  | Idempotent?| Semantic Contract & Backend Action              |
+-----------+--------+------------+-------------------------------------------------+
| GET       | YES    | YES        | Retrieve resource representation. Zero mutations|
| HEAD      | YES    | YES        | Identical to GET, but returns headers ONLY.     |
| OPTIONS   | YES    | YES        | Query supported methods & CORS preflight rules. |
| PUT       | NO     | **YES**    | **Replace entire resource**. Overwrites target. |
| DELETE    | NO     | **YES**    | Delete resource. Second call: resource is gone. |
| POST      | NO     | **NO**     | Create new sub-resource or trigger actions.     |
| PATCH     | NO     | **NO\***   | Partial modification (RFC 5789). Can append.    |
+-----------+--------+------------+-------------------------------------------------+
```
*\*Note: `PATCH` can be implemented idempotently, but the HTTP specification does not mandate idempotency because instructions like `{"op": "increment", "amount": 10}` are not idempotent.*

---

## 8. HTTP Status Codes Classification

```
+-----------+-----------------------+-------------------------------------------------------+
| Category  | Purpose               | Critical Production Codes                             |
+-----------+-----------------------+-------------------------------------------------------+
| 1xx       | Informational         | 101 Switching Protocols (WebSockets upgrade)         |
| 2xx       | Success               | 200 OK, 201 Created (POST), 204 No Content (DELETE)   |
| 3xx       | Redirection           | 301 Moved Permanently, 304 Not Modified (Cache Hit)   |
|           |                       | 307 Temporary Redirect, 308 Permanent Redirect        |
| 4xx       | Client Errors         | 400 Bad Request, 401 Unauthorized, 403 Forbidden      |
|           |                       | 404 Not Found, 409 Conflict, 422 Unprocessable Entity |
|           |                       | 429 Too Many Requests (Rate Limited)                  |
| 5xx       | Server Errors         | 500 Internal Error, 502 Bad Gateway (Upstream down)   |
|           |                       | 503 Service Unavailable, 504 Gateway Timeout          |
+-----------+-----------------------+-------------------------------------------------------+
```

---

## 9. HTTP Caching & Conditional Headers

HTTP caching eliminates redundant network roundtrips using two complementary validation techniques:

### 1. Freshness (Expiration Caching)
- `Cache-Control: max-age=3600, must-revalidate`
- The client or CDN edge cache can serve the cached copy for 3600 seconds without contacting the origin server.

### 2. Validation (Conditional Requests)
When the cached copy becomes stale, the client validates whether the resource actually changed:
- **ETag (Entity Tag)**: Server returns a content digest hash: `ETag: "v1.2.3"`.
- Next request sends: `If-None-Match: "v1.2.3"`.
- If unchanged, server returns **`304 Not Modified` with zero body bytes**, saving bandwidth and CPU!

---

## 10. Cross-Origin Resource Sharing (CORS) Demystified

The **Same-Origin Policy (SOP)** is a browser security enforcement preventing a script on `https://evil.com` from reading authenticated API responses from `https://yourbank.com`. An origin is defined by:
$$\text{Origin} = \text{Scheme} + \text{Domain} + \text{Port}$$

### The CORS Preflight Handshake (`OPTIONS`)
When a frontend on `https://app.com` makes a complex request (`PUT`, `DELETE`, or custom headers) to `https://api.com`:
1. The browser automatically dispatches an `OPTIONS` **Preflight Request**:
   ```http
   OPTIONS /v1/orders HTTP/1.1
   Host: api.com
   Origin: https://app.com
   Access-Control-Request-Method: PUT
   Access-Control-Request-Headers: Authorization, Content-Type
   ```
2. The server responds with permitted policies:
   ```http
   HTTP/1.1 204 No Content
   Access-Control-Allow-Origin: https://app.com
   Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS
   Access-Control-Allow-Headers: Authorization, Content-Type
   Access-Control-Max-Age: 86400
   ```
3. Only if the preflight succeeds does the browser send the actual `PUT /v1/orders` request.

---

## 11. Distributed Idempotency Keys in Enterprise Payments

When processing financial transactions (Stripe, Adyen) or AI generation credits, a network drop between the client and server can leave the client uncertain whether the charge succeeded.
- If the client retries naively with `POST /charges`, the customer is double-billed.

```
       Client                                         API Gateway / Redis
         |                                                     |
         | --- POST /charges (Idempotency-Key: "uuid-123") --> |
         |                                                     | Check Redis: Not exists
         |                                                     | Acquire Lock, Process Charge
         | <--- Network Drops Before Response Reaches Client - | Store Response in Redis
         |                                                     |
         | --- POST /charges (Idempotency-Key: "uuid-123") --> | (Retry)
         |                                                     | Check Redis: FOUND!
         | <--- Return Cached Response (HTTP 200 OK) --------- | (ZERO duplicate charge!)
```

---

## 12. 7 Non-Negotiable Rules of Production HTTP & REST Engineering

1. **Never Mutate State on `GET` Requests**: `GET` must remain strictly safe and idempotent. Running write operations on `GET` triggers accidental mutations by web crawlers and prefetch engines.
2. **Always Use Connection Pooling (`Keep-Alive`)**: Reusing persistent TCP/TLS connections eliminates the 1-2 RTT handshake overhead on every API call.
3. **Return `304 Not Modified` with `ETag`**: Support conditional requests on heavy JSON endpoints to eliminate unnecessary payload transfers.
4. **Implement Distributed Idempotency Keys**: Protect state-mutating `POST` requests (payments, bookings) using unique client-provided idempotency tokens cached in Redis.
5. **Handle CORS at the API Gateway / Middleware**: Never hardcode `Access-Control-Allow-Origin: *` when handling authenticated credential cookies.
6. **Differentiate 401 Unauthorized vs 403 Forbidden**: Use `401` when the user is unauthenticated (missing/invalid token); use `403` when authenticated but lacking permissions (RBAC).
7. **Always Set Explicit Timeouts on Outbound Sockets**: Unbounded socket reads cause thread starvation in application servers when downstream third-party APIs hang.

---

## 13. Comprehensive Protocol Comparison Matrix

| Protocol | Transport | Handshake | Multiplexing | Header Compression | Best Production Fit |
|---|---|---|---|---|---|
| **HTTP/1.1** | TCP | 1x TCP + 1x TLS (2 RTT) | No (Sequential) | None (Plaintext) | Legacy APIs, Simple microservices |
| **HTTP/2** | TCP | 1x TCP + 1x TLS (2 RTT) | Yes (Binary streams) | HPACK | Web Frontend-to-Backend, gRPC |
| **HTTP/3** | UDP (QUIC)| Integrated 0-1 RTT | Yes (Independent) | QPACK | Mobile clients, Global CDNs, Edge APIs |
| **WebSockets**| TCP (Upgrade)| HTTP/1.1 Handshake | Full-Duplex Stream | Per-message deflate | Real-time chat, Live telemetry, AI streaming |
| **gRPC** | HTTP/2 | 1x TCP + 1x TLS | Yes (Protobuf binary)| HPACK | Internal microservice-to-microservice RPC |
