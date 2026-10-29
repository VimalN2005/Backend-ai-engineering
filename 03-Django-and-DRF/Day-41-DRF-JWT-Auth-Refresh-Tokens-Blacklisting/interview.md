# Senior Backend Engineering Interview: DRF JWT Authentication, Refresh Tokens & Blacklisting

This guide compiles the top 5 high-frequency architectural interview questions targeting Senior and Staff Backend Engineers working with distributed authentication, JSON Web Tokens (JWT), token rotation, and revocation systems.

---

### Question 1: Explain the fundamental trade-off between session-based authentication and JWTs. How does the Dual-Token Architecture resolve the stateless revocation dilemma?

#### Executive Summary
Session-based authentication is **stateful**: the server stores session data in Redis or PostgreSQL and checks it on every HTTP request, allowing instant server-side revocation at the cost of database/cache bottlenecks and cluster cross-talk. Pure JWTs are **stateless**: API servers verify signatures mathematically without hitting databases, enabling infinite horizontal scaling at the cost of being unable to revoke a compromised token before its expiration.

The **Dual-Token Architecture** resolves this tension by splitting authentication into two tiers:
1. **Short-Lived Access Tokens (5–15 min)**: Verified statelessly on 99% of API requests without database lookups.
2. **Long-Lived Refresh Tokens (1–7 days)**: Checked statefully against a revocation blacklist **only** when refreshing access tokens (once every 15 minutes).

#### Deep Architectural Breakdown

```
+─────────────────────────────────────────────────────────────────────────────+
|                     STATEFUL SESSIONS VS DUAL-TOKEN JWT                     |
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
| 1. Session Auth (Stateful):                                                 |
|    10,000 req/sec ──> 10,000 DB / Redis Session Queries!                    |
|    - Bottleneck: Centralized session store connection limits and latency.    |
|                                                                             |
| 2. Pure Stateless JWT (No Revocation):                                      |
|    10,000 req/sec ──> 0 DB Queries!                                         |
|    - Catastrophe: If token is leaked, attacker has access until exp expires!|
|                                                                             |
| 3. Dual-Token Architecture (Enterprise Balance):                            |
|    - 9,990 Standard API calls (Bearer Access Token) -> 0 DB Queries (Fast!) |
|    - 10 Token Refresh calls (Refresh Token) -> Check Blacklist (Secure!)    |
+─────────────────────────────────────────────────────────────────────────────+
```

#### What Interviewers Look For
- Clear articulation that JWT does not automatically mean "more secure" than sessions; it is an architectural trade-off prioritizing stateless throughput over instant revocation.
- Understanding that access tokens must remain short-lived (5–15 mins) so that the lack of revocation has a strictly bounded exposure window.
- Explaining that blacklisting is applied to refresh tokens (at the session boundary), maintaining stateless performance for the primary API.

---

### Question 2: How does Refresh Token Rotation work, and what is Automatic Token Family Reuse Detection (RFC 6749 Section 10.4)? Trace an attack scenario.

#### Executive Summary
**Token Rotation** invalidates a refresh token the moment it is used to obtain a new access token, issuing a brand new refresh token in its place. **Token Family Reuse Detection** tracks the lineage of rotated tokens using a shared `family_id`. If an already-consumed (blacklisted) refresh token is submitted again, the server detects a token hijacking incident and **immediately revokes all active tokens in that entire family**, locking out both the adversary and the victim until credentials are re-entered.

#### Deep Architectural Breakdown: The Hijacking Trace

```
T0: User logs in -> Receives Refresh Token R1 (Family 101).
T1: Network Adversary intercepts Refresh Token R1 (e.g. from insecure Wi-Fi).

PATH A (Legitimate User acts first):
T2: Legitimate User calls /api/token/refresh/ with R1.
    - Server verifies R1.
    - Server blacklists R1.
    - Server issues new Refresh Token R2 to Legitimate User.
T3: Adversary attempts to use stolen Refresh Token R1.
    - Server checks Blacklist: R1 is ALREADY BLACKLISTED!
    - 🚨 REUSE ATTACK DETECTED!
    - Server revokes Family 101: R2 is immediately blacklisted!
    - Both Adversary and Legitimate User are locked out.

PATH B (Adversary acts first):
T2: Adversary calls /api/token/refresh/ with R1.
    - Server issues new Refresh Token R2 to Adversary; blacklists R1.
T3: Legitimate User attempts to call /api/token/refresh/ with R1.
    - Server checks Blacklist: R1 is ALREADY BLACKLISTED!
    - 🚨 REUSE ATTACK DETECTED!
    - Server revokes Family 101: Adversary's R2 is instantly revoked!
    - Adversary's access is severed.
```

#### Why Token Families are Essential:
Without family tracking, in Path B the adversary would maintain persistent access forever using their new token R2, while the legitimate user would simply get an error and not realize their account was hijacked. Family tracking ensures that **any replay triggers complete session termination**.

#### What Interviewers Look For
- Deep familiarity with RFC 6749 Section 10.4 (Threat Mitigation for Refresh Tokens).
- Explaining that both Path A and Path B result in the adversary being locked out.
- Mentioning that users should be notified via security alerts when reuse is detected.

---

### Question 3: What is the exact database schema and lookup mechanism behind DRF SimpleJWT's `token_blacklist` app? How do you prevent unbounded database bloat in high-traffic applications?

#### Executive Summary
SimpleJWT's `token_blacklist` uses two relational tables: `OutstandingToken` (records all minted refresh tokens with user, JTI, and expiration) and `BlacklistedToken` (one-to-one foreign key pointing to revoked outstanding tokens). On refresh, SimpleJWT checks if the token's JTI exists in `BlacklistedToken`. To prevent unbounded table growth, a periodic maintenance command (`flushexpiredtokens`) must be scheduled via Celery Beat to purge tokens whose natural expiration timestamp has passed.

#### Database Schema Architecture

```
+─────────────────────────────────────────────────────────────+
|               token_blacklist_outstandingtoken              |
+─────────────────────────────────────────────────────────────+
| id          | BigAutoField (PK)                             |
| user_id     | ForeignKey -> auth_user.id                    |
| jti         | CharField(max_length=255, unique=True, index) |
| token       | TextField (Full encoded JWT)                  |
| created_at  | DateTimeField                                 |
| expires_at  | DateTimeField (Indexed)                       |
+─────────────────────────────────────────────────────────────+
                                ▲
                                │ 1-to-1 Foreign Key
+───────────────────────────────┴─────────────────────────────+
|               token_blacklist_blacklistedtoken              |
+─────────────────────────────────────────────────────────────+
| id          | BigAutoField (PK)                             |
| token_id    | OneToOneField -> outstandingtoken.id          |
| blacklisted_at | DateTimeField                              |
+─────────────────────────────────────────────────────────────+
```

#### Lookup & Verification Execution:
```sql
SELECT 1 
FROM token_blacklist_blacklistedtoken b
JOIN token_blacklist_outstandingtoken o ON b.token_id = o.id
WHERE o.jti = '5a4f3b7c9e1d4a82b9e6c1a7d2e3f4b5'
LIMIT 1;
```

#### Mitigating Unbounded Database Growth:
1. **The Problem**: In an app rotating tokens every 15 minutes, 100,000 active users generate $\approx 9.6\text{ million}$ rows per day in `outstandingtoken`.
2. **The Solution**: Once `expires_at < NOW()`, the token is mathematically invalid regardless of the blacklist. The database no longer needs to track it.
3. **Automated Pruning**: Schedule `manage.py flushexpiredtokens` daily via Celery Beat:
   ```python
   # CELERY BEAT SCHEDULE
   CELERY_BEAT_SCHEDULE = {
       'flush-expired-jwt-tokens-nightly': {
           'task': 'accounts.tasks.flush_expired_tokens',
           'schedule': crontab(hour=3, minute=0), # 3:00 AM daily
       },
   }
   ```
4. **Redis Alternative**: For massive scale (> 50,000 req/sec), store blacklisted JTIs in a Redis Set with `EXPIREAT = token.expires_at`, bypassing PostgreSQL completely.

---

### Question 4: Compare storing JWTs in browser `localStorage` vs `HttpOnly` Cookies. What are the specific attack vectors (XSS vs CSRF), and what is the gold-standard enterprise architecture for SPAs?

#### Executive Summary
- **`localStorage`**: Incurable vulnerability to **Cross-Site Scripting (XSS)**. Any malicious script, browser extension, or compromised npm dependency can read `window.localStorage` and exfiltrate tokens.
- **`HttpOnly` Cookies**: Immune to XSS read access, but vulnerable to **Cross-Site Request Forgery (CSRF)** because browsers automatically attach cookies to cross-origin requests.
- **Enterprise Gold-Standard Pattern**: Store the **Access Token strictly in memory** (JavaScript closure or state) and store the **Refresh Token in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie**.

#### Architectural Comparison

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| STORAGE OPTION           | XSS VULNERABILITY           | CSRF VULNERABILITY              |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 1. localStorage          | 🔴 CRITICAL: Any script     | 🟢 IMMUNE: Browser does not     |
|                          | can read and exfiltrate.    | auto-attach localStorage.       |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 2. Standard Cookie       | 🔴 CRITICAL: Readable via   | 🔴 HIGH: Auto-attached by       |
|    (No HttpOnly)         | document.cookie.            | browser on cross-origin calls.  |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 3. HttpOnly Cookie       | 🟢 IMMUNE: JavaScript       | 🟡 REQUIRES DEFENSE: Mitigated  |
|    (Secure, SameSite)    | cannot access cookie bytes. | via SameSite=Strict & CSRF token|
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

#### The Gold-Standard Enterprise Flow for React / Vue / Next.js:
1. **User logs in**: Server sets `refresh_token` in an `HttpOnly, Secure, SameSite=Strict` cookie, and returns the short-lived `access_token` in the JSON response body.
2. **Frontend stores `access_token` in memory**: Stored in a React Context, Redux store, or closure variable. It is never written to disk or `localStorage`.
3. **API Requests**: Sent with `Authorization: Bearer <memory_access_token>`.
4. **Page Refresh or Access Expiration**: JavaScript calls `POST /api/token/refresh/`. Because the refresh endpoint is on the API domain, the browser automatically sends the HttpOnly cookie. The server returns a fresh access token in memory.

---

### Question 5: Explain the `alg: "none"` exploit and the Asymmetric Key Confusion attack (RS256 vs HS256). How do you harden a DRF backend against these cryptographic vulnerabilities?

#### Executive Summary
The `alg: "none"` exploit abuses RFC 7519's provision for unsigned tokens: an attacker creates a forged payload with admin claims, sets `"alg": "none"` in the header, omits the signature, and misconfigured libraries accept it without validation. 

The **Key Confusion Attack** exploits servers supporting both RS256 and HS256: an attacker takes the server's **public RSA key** (which is publicly available) and signs a forged token using HMAC-SHA256 with the public key string as the symmetric secret key. If the server expects RS256 but allows HS256, it uses its public key to verify the HMAC signature and erroneously trusts the forged token!

#### The Key Confusion Attack Trace

```
1. Target System uses RS256:
   - Private Key: Secret (used by Auth server to sign tokens)
   - Public Key : Public (https://api.domain.com/.well-known/jwks.json)

2. Attacker crafts forged token:
   Header : {"alg": "HS256", "typ": "JWT"}  <-- Claims to be HMAC!
   Payload: {"user_id": 1, "is_superuser": true}

3. Attacker signs token using HMAC-SHA256:
   HMAC_SHA256(header + "." + payload, PUBLIC_KEY_STRING)

4. Vulnerable Backend:
   - Inspects header -> reads "alg": "HS256".
   - Retrieves its verification key: the public key!
   - Verifies HMAC using public key as secret -> SIGNATURE MATCHES!
   - Grants root administrative access!
```

#### Production Hardening:
1. **Explicit Algorithm Whitelist**: Never allow the incoming token's header to dictate which algorithm is used for verification. Hardcode the expected algorithm:
   ```python
   # settings.py - Hardened SimpleJWT
   SIMPLE_JWT = {
       'ALGORITHM': 'RS256',
       'SIGNING_KEY': env('JWT_PRIVATE_KEY'),
       'VERIFYING_KEY': env('JWT_PUBLIC_KEY'),
       'AUTH_HEADER_TYPES': ('Bearer',),
   }
   ```
2. **Strict Verification Options**: In custom PyJWT implementations, pass `algorithms=['RS256']` explicitly:
   ```python
   jwt.decode(token, public_key, algorithms=['RS256'])
   ```
   This strictly rejects any token specifying `"alg": "HS256"` or `"alg": "none"`.
3. **Use Constant-Time Comparison**: Always use `hmac.compare_digest` to prevent byte-by-byte timing attacks on symmetric verification.
