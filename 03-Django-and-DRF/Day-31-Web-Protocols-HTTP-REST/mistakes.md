# Critical Antipatterns & Production Traps in Web Protocols & REST Standards

Web protocols form the communication backbone of all backend systems. Violating HTTP specifications, misconfiguring CORS, or ignoring TCP connection lifecycle can lead to silent data corruption, security breaches, or complete server lockups. Below are the top 7 mistakes observed in production engineering.

---

### Mistake 1: Executing State Mutations on `GET` Requests

#### Bad Pattern
Modifying database records, deducting balances, or triggering actions inside a `GET` endpoint:

```python
# DISASTROUS: Violates HTTP Safe Contract
@app.get("/api/v1/orders/cancel")
def cancel_order(order_id: str):
    db.orders.filter(id=order_id).update(status="CANCELLED")
    return {"message": "Order cancelled"}
```

#### Correct Pattern
Enforce that `GET` is strictly **Safe and Read-Only**. Use `POST` or `PUT` for mutations:

```python
# CORRECT: State mutations require POST or PUT
@app.post("/api/v1/orders/{order_id}/cancel")
def cancel_order_safe(order_id: str):
    db.orders.filter(id=order_id).update(status="CANCELLED")
    return {"message": "Order cancelled"}
```

#### Why It Breaks
The HTTP specification defines `GET`, `HEAD`, and `OPTIONS` as **Safe Methods** ($f(x)$ leaves server state unmodified).
- **Search Engine Crawlers**: Googlebot or Bingbot indexing your links will follow `/cancel` URLs and accidentally cancel thousands of customer orders!
- **Browser Pre-fetching**: Chrome and Safari prefetch links on mouse hover or DNS lookups. Merely hovering over a button triggers the mutation.

---

### Mistake 2: Conflating `PUT` and `PATCH` Semantics

#### Bad Pattern
Treating `PUT` as a partial update, or expecting `PATCH` to guarantee idempotent replacement:

```python
# INCORRECT: PUT handler that only updates provided fields instead of full replacement
@app.put("/api/v1/users/{user_id}")
def update_user_broken(user_id: str, payload: dict):
    # If payload is {"name": "Alice"}, but user had {"bio": "Engineer", "age": 30},
    # a true PUT must REPLACE the entire resource (resetting bio and age to defaults).
    # Performing a partial update here violates RFC 9110!
    db.users.filter(id=user_id).update(**payload)
```

#### Correct Pattern
- **`PUT` (RFC 9110)**: **Full Replacement**. The client sends the complete representation. Missing fields are wiped or reset to defaults. `PUT` is strictly **Idempotent**.
- **`PATCH` (RFC 5789)**: **Partial Modification**. Modifies only the specified fields, leaving other attributes untouched.

```python
# CORRECT: Distinct contracts
@app.put("/api/v1/users/{user_id}")
def replace_user(user_id: str, new_user: FullUserSchema):
    db.users.replace(user_id, new_user.dict())  # Full replacement

@app.patch("/api/v1/users/{user_id}")
def patch_user(user_id: str, partial: PartialUserSchema):
    db.users.patch(user_id, partial.dict(exclude_unset=True))  # Partial update
```

---

### Mistake 3: Wildcard `Access-Control-Allow-Origin: *` with Credentials

#### Bad Pattern
Setting wildcard origin while enabling credentials:

```python
# BROKEN: Rejected by all modern web browsers
headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Credentials": "true",  # BROWSER ERROR!
}
```

#### Correct Pattern
Echo the validated requesting origin explicitly:

```python
# CORRECT: Explicit origin matching
ALLOWED_ORIGINS = {"https://app.enterprise.com", "https://staging.enterprise.com"}

def build_cors_headers(request_origin: str) -> dict:
    if request_origin in ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": request_origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}
```

#### Why It Breaks
W3C CORS specifications explicitly prohibit wildcard `*` when `credentials: true` (cookies or authorization headers). The browser will block the response, and JavaScript will receive an opaque network error.

---

### Mistake 4: Ephemeral Port Starvation (Missing Connection Pooling)

#### Bad Pattern
Creating a new HTTP client or raw TCP socket for every outbound microservice request:

```python
import requests

# DISASTROUS IN HIGH-THROUGHPUT: Exhausts OS sockets
def fetch_user_data(user_id: str):
    # requests.get creates a NEW TCP connection, does 3-way handshake, then closes
    return requests.get(f"http://user-service/users/{user_id}").json()
```

#### Correct Pattern
Reuse a persistent `requests.Session()` or `httpx.Client()` with connection pooling:

```python
import requests

# CORRECT: Reuses persistent TCP connections across requests
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=50)
session.mount("http://", adapter)

def fetch_user_data_pooled(user_id: str):
    return session.get(f"http://user-service/users/{user_id}").json()
```

#### Why It Breaks
When an active TCP connection closes, the socket enters the **`TIME_WAIT`** state for 60 seconds (2 MSL). At 1,000 req/sec without connection pooling, the operating system exhausts all ~60,000 ephemeral ports, causing `OSError: [Errno 99] Cannot assign requested address` and freezing all outbound traffic.

---

### Mistake 5: Non-Idempotent Financial Transactions (Double-Charging)

#### Bad Pattern
Executing billing `POST` requests without checking for client retry tokens:

```python
# DANGEROUS: If network drops after charging, client retry charges AGAIN!
@app.post("/api/v1/charge")
def charge_card(req: ChargeRequest):
    tx_id = payment_gateway.charge(req.amount, req.card_token)
    return {"status": "success", "tx_id": tx_id}
```

#### Correct Pattern
Require an `Idempotency-Key` header and store completed transaction responses in Redis:

```python
@app.post("/api/v1/charge")
def charge_card_idempotent(req: ChargeRequest, idempotency_key: str = Header(...)):
    # Check if this idempotency key was already processed
    cached_response = redis.get(f"idemp:{idempotency_key}")
    if cached_response:
        return json.loads(cached_response)

    # Process once and cache
    tx_id = payment_gateway.charge(req.amount, req.card_token)
    response = {"status": "success", "tx_id": tx_id}
    redis.set(f"idemp:{idempotency_key}", json.dumps(response), ex=86400)
    return response
```

---

### Mistake 6: Conflating `401 Unauthorized` and `403 Forbidden`

#### Bad Pattern
Returning `401` when a user lacks permissions, or returning `403` when they aren't logged in:

```python
# CONFUSING: Misleads frontend client routing
@app.get("/admin/metrics")
def get_metrics(current_user: User):
    if not current_user.is_admin:
        # WRONG: 401 causes frontend to redirect user to login page!
        raise HTTPException(status_code=401, detail="Must be admin")
```

#### Correct Pattern
- **`401 Unauthorized`**: Authentication is missing, expired, or invalid. The user must provide valid credentials (`WWW-Authenticate`).
- **`403 Forbidden`**: The server understands who the user is, but **refuses authorization**. Logging in again will not help.

```python
# CORRECT: Clear semantic boundary
if not current_user:
    raise HTTPException(status_code=401, detail="Authentication required")
if not current_user.is_admin:
    raise HTTPException(status_code=403, detail="Forbidden: Insufficient privileges")
```

---

### Mistake 7: Unbounded Outbound Socket Read Timeouts

#### Bad Pattern
Calling external APIs without setting an explicit timeout:

```python
# DANGEROUS: Default timeout is infinite in many libraries!
response = requests.get("https://third-party-api.com/data")
```

#### Correct Pattern
Always set connection and read timeouts:

```python
# CORRECT: 2s connection timeout, 5s read timeout
response = requests.get("https://third-party-api.com/data", timeout=(2.0, 5.0))
```

#### Why It Breaks
If the remote server hangs or drops packets, your worker thread blocks indefinitely. Within minutes, all Gunicorn or Uvicorn worker threads become paralyzed, bringing down your entire API gateway.
