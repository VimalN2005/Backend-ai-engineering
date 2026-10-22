# Senior Backend & AI Systems Interview: Web Protocols & REST Standards

This document details the top 5 high-impact interview questions asked by Tier-1 tech companies (Google, Stripe, Cloudflare, Netflix, Amazon) regarding network protocols, HTTP evolution, and REST API architectural contracts.

---

### Question 1: How does the TCP 3-Way Handshake work, why is the `TIME_WAIT` state mandatory, and how do SYN Cookies defend against SYN Floods?

#### The Question
"Explain the sequence number math of the TCP 3-way handshake. When closing a connection, why must the initiating socket linger in `TIME_WAIT` for $2 \times \text{MSL}$? What happens to a high-throughput API gateway if `TIME_WAIT` sockets accumulate, and how does the OS handle SYN Flood attacks without allocating memory?"

#### Candidate Answer

#### 1. TCP 3-Way Handshake Mechanics
1. **Client sends `SYN` (Synchronize)**: Picks initial sequence number $x$ (`ISN_c`). Packet header has `SYN=1, seq=x`.
2. **Server sends `SYN-ACK`**: Acknowledges client's sequence number by setting `ack = x + 1`. Server picks its own sequence number $y$ (`ISN_s`). Packet has `SYN=1, ACK=1, seq=y, ack=x+1`.
3. **Client sends `ACK`**: Acknowledges server's sequence number with `ack = y + 1` and `seq = x + 1`. Connection is now `ESTABLISHED`.

#### 2. The `TIME_WAIT` State Invariant
When a socket initiates an active close (`FIN`), after receiving the final `ACK` from the peer, it enters the **`TIME_WAIT`** state for $2 \times \text{MSL}$ (Maximum Segment Lifetime, typically 60 seconds).
- **Why it is mandatory**:
  1. **Reliable ACK Delivery**: If the final `ACK` is dropped in transit, the peer will retransmit its `FIN`. If the initiating socket closed immediately, it would respond with `RST` (Connection Reset Error) instead of resending the `ACK`, causing an unclean close on the peer.
  2. **Preventing Old Duplicate Packet Corruption**: Delayed duplicate packets wandering through internet routers from the old connection must expire before the identical 4-tuple `(src_ip, src_port, dst_ip, dst_port)` can be reused by a new connection.
- **Production Danger**: If an API server creates a new outbound TCP socket per request to a microservice without connection pooling, it can open 60,000 connections in 30 seconds. All ephemeral ports become locked in `TIME_WAIT`, triggering `OSError: Cannot assign requested address` (**Ephemeral Port Exhaustion**).
  - **Mitigation**: Enable HTTP Keep-Alive connection pooling (`urllib3.PoolManager`, `requests.Session`).

#### 3. SYN Flood Mitigation via SYN Cookies
In a SYN flood, an attacker sends thousands of spoofed `SYN` packets, filling the server's SYN queue (`backlog`).
- **Standard Behavior**: Server allocates a Transmission Control Block (TCB) in kernel memory for each `SYN`. Backlog fills $\longrightarrow$ legitimate clients are dropped.
- **SYN Cookie Invariant**: The server **allocates zero memory** in the backlog! Instead, it encodes the entire connection state into the server's Initial Sequence Number $y$:
  $$y = \text{HMAC-SHA1}(\text{client\_ip, client\_port, server\_ip, server\_port, secret}) + t + (\text{MSS encoding})$$
  When the legitimate client returns the final `ACK` with `ack = y + 1`, the server decrypts the cookie from `ack - 1`, validates the cryptographic hash, and only *then* allocates kernel socket memory.

---

### Question 2: Explain Head-of-Line (HoL) Blocking across HTTP/1.1, HTTP/2, and HTTP/3 (QUIC).

#### The Question
"HTTP/2 introduced multiplexing to solve HTTP/1.1 pipelining issues. Why did HTTP/2 still suffer from Head-of-Line blocking, and how does HTTP/3 with QUIC over UDP solve it at the transport layer?"

#### Candidate Answer

```
+-----------------------------------+-----------------------------------+-----------------------------------+
| HTTP/1.1                          | HTTP/2                            | HTTP/3 (QUIC over UDP)            |
+-----------------------------------+-----------------------------------+-----------------------------------+
| Application-Layer HoL Blocking    | TCP-Layer HoL Blocking            | ZERO Head-of-Line Blocking        |
| Requests must be served serially  | Multiple streams multiplexed over | Streams mapped to independent UDP |
| on a single TCP connection.       | ONE single TCP stream.            | datagrams.                        |
| Slow request 1 blocks request 2.  | 1 dropped packet stalls ALL!      | Dropped packet on stream 1 NEVER  |
|                                   |                                   | stalls stream 2.                  |
+-----------------------------------+-----------------------------------+-----------------------------------+
```

#### Detailed Breakdown:
1. **HTTP/1.1 (Application-Level HoL)**:
   - On a single TCP connection, a client cannot send Request B until Response A has been fully received.
   - Pipelining attempted to send requests back-to-back, but responses still had to arrive in exact order. A slow database query on request 1 blocked all subsequent responses.
   - Workaround: Browsers opened 6 parallel TCP connections per domain.

2. **HTTP/2 (TCP-Level HoL)**:
   - Solved application-level HoL by interleaving binary frames (`DATA`, `HEADERS`) labeled with distinct `Stream ID`s over a single TCP socket.
   - **The Flaw**: TCP guarantees strict byte-stream ordering. If **Packet 3** (belonging to Stream A) is dropped on a lossy cellular network:
     - The operating system TCP buffer halts delivery of **Packet 4, 5, 6** (even though they belong to Stream B!).
     - Stream B is frozen waiting for TCP to retransmit Packet 3.

3. **HTTP/3 (QUIC over UDP)**:
   - Replaces TCP with **QUIC**, which runs in user-space over **UDP**.
   - In QUIC, streams are first-class transport primitives. Packet loss on Stream A only pauses Stream A. Stream B continues processing immediately.
   - **Bonus Features**:
     - **0-RTT Connection Resumption**: TLS 1.3 is embedded directly into QUIC's initial packet exchange.
     - **Connection Migration**: A QUIC connection is identified by a 64-bit Connection ID (CID), not a TCP 4-tuple. When a mobile user walks from Wi-Fi to 5G, the IP address changes, but the QUIC stream does not disconnect!

---

### Question 3: How do enterprise payment processors (Stripe, Shopify) implement Distributed Idempotency Keys in REST APIs?

#### The Question
"In payment APIs, a network timeout on `POST /v1/charges` leaves the client uncertain if the charge succeeded. How do you design an enterprise-grade Idempotency Key subsystem using Redis and relational databases to prevent double-billing?"

#### Candidate Answer

#### Architectural Flow:
1. **Client Contract**: The client generates a unique V4 UUID and passes it via the `Idempotency-Key: <uuid>` HTTP header.
2. **API Gateway / Middleware Interception**:
   - The gateway computes a cache key: `idemp:{tenant_id}:{idempotency_key}`.
   - It performs an atomic `SET key IN_FLIGHT NX EX 120` (Set if Not Exists with 120s lock timeout) in Redis.
3. **Execution Scenarios**:
   - **Scenario A (New Request)**: Key is acquired. Gateway forwards request to the core billing engine.
     - Transaction completes successfully in PostgreSQL.
     - Gateway writes the final HTTP status code and response payload into Redis:
       `SET idemp:{tenant_id}:{idempotency_key} <response_json> EX 86400`.
     - Returns HTTP 201 Created to client.
   - **Scenario B (Concurrent Duplicate Request)**: Another thread sends the identical key while the first is still processing (`status == IN_FLIGHT`).
     - Gateway rejects immediately with **`HTTP 409 Conflict`**: `"Concurrent request with identical idempotency key in progress"`.
   - **Scenario C (Network Retry After Timeout)**: The client disconnected before receiving the original response and retries with the same key.
     - Gateway finds key in Redis with `status == COMPLETED`.
     - **It bypasses the billing engine completely** and returns the exact cached response from Redis with an added header `Idempotent-Replayed: true`.
4. **Database-Level Guard**: At the database layer, an `idempotency_key` column on the `charges` table has a `UNIQUE CONSTRAINT` to guarantee atomicity even if the Redis cache fails.

---

### Question 4: Explain CORS mechanics, Simple Requests vs Preflight Requests, and the credentials security constraint.

#### The Question
"What problem does the Same-Origin Policy (SOP) solve, why was CORS created, what triggers a preflight `OPTIONS` request, and why does `Access-Control-Allow-Origin: *` fail when cookies or tokens are included?"

#### Candidate Answer

#### 1. Same-Origin Policy (SOP) vs CORS
- **SOP (Browser Security Model)**: An origin is `Scheme + Host + Port`. SOP blocks JavaScript on `https://evil.com` from reading authenticated API responses from `https://mybank.com`.
- **CORS (Cross-Origin Resource Sharing)**: A W3C standard that allows servers to selectively relax SOP and specify which external origins are allowed to read their data.

#### 2. Simple vs Preflighted Requests
A request is **Simple** (no preflight needed) if:
1. Method is `GET`, `HEAD`, or `POST`.
2. Headers contain only safe headers: `Accept`, `Accept-Language`, `Content-Language`, or `Content-Type` with values `text/plain`, `multipart/form-data`, or `application/x-www-form-urlencoded`.

If a request uses **`application/json`**, `PUT`, `DELETE`, or custom headers (`Authorization`, `Idempotency-Key`):
- The browser **pauses** and automatically sends an **`OPTIONS` Preflight Request** to verify permissions before sending the actual payload.

#### 3. The Credentials Constraint (`Access-Control-Allow-Credentials: true`)
When requests include cookies (`credentials: 'include'`) or HTTP Authorization headers:
- W3C security rules strictly state that **`Access-Control-Allow-Origin` CANNOT be `*` (wildcard)**.
- If a server responds with `Access-Control-Allow-Origin: *` and `Access-Control-Allow-Credentials: true`, modern browsers **reject the response** and prevent JavaScript from reading it.
- **The Correct Backend Pattern**: The server must inspect the incoming `Origin` header, match it against an internal whitelist, and reflect that exact origin string in the `Access-Control-Allow-Origin` header along with `Vary: Origin`.

---

### Question 5: How do Conditional Requests (`ETag` and `304 Not Modified`) optimize CDN edge caching and backend egress?

#### The Question
"Explain the difference between strong and weak ETags. When should an API return `304 Not Modified` instead of `200 OK`, and how does this interact with `Cache-Control` directives like `stale-while-revalidate`?"

#### Candidate Answer

#### 1. ETag (Entity Tag) Mechanics
An `ETag` is an opaque fingerprint (usually a hash of the serialized representation) assigned by the server to a specific version of a resource.
- **Strong ETag (`"33a64df5"`)**: Guarantees byte-for-byte identity of both content and headers.
- **Weak ETag (`W/"33a64df5"`)**: Guarantees semantic equivalence (e.g. JSON fields may be in a different order or timestamp formatting differs slightly, but business meaning is identical).

#### 2. The Conditional GET Handshake:
1. Client sends `GET /v1/products/123`.
2. Server responds:
   ```http
   HTTP/1.1 200 OK
   ETag: "v1_hash"
   Cache-Control: public, max-age=300, must-revalidate
   Content-Length: 50000
   ```
3. After 300 seconds, the client's cache becomes stale.
4. Client sends a **Conditional Request**:
   ```http
   GET /v1/products/123
   If-None-Match: "v1_hash"
   ```
5. Server hashes the database record:
   - If hash still matches `"v1_hash"`, the server returns **`HTTP/1.1 304 Not Modified` with zero body bytes**.
   - Network egress bandwidth drops by 99.9%, and client rendering resumes immediately.

#### 3. `stale-while-revalidate`
A modern `Cache-Control` directive:
`Cache-Control: max-age=60, stale-while-revalidate=300`
- For the first 60 seconds, the response is fresh.
- Between 60 and 360 seconds, the client/CDN **serves the stale copy immediately (0ms latency)**, while asynchronously dispatching a background conditional request to the origin to update the cache.
- Delivers instant sub-millisecond responses to users while maintaining fresh data.
