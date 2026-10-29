# Day 41: DRF Authentication: JWT (SimpleJWT), Refresh Tokens & Blacklisting

In high-concurrency, distributed backend architectures, traditional session-based authentication—where every incoming HTTP request triggers a stateful database or Redis session lookup—becomes a significant scalability bottleneck. 

To achieve horizontal scaling, modern APIs utilize **JSON Web Tokens (JWT, RFC 7519)**. However, implementing JWTs in production requires addressing the fundamental trade-off of stateless authentication: **How do you revoke or invalidate a compromised token before it naturally expires?**

This guide provides an exhaustive, production-grade deep dive into:
- The **Dual-Token Architecture** (Short-Lived Access Tokens vs Long-Lived Refresh Tokens).
- Cryptographic signing algorithms (HMAC-SHA256 vs RSA/ECDSA asymmetric pairs).
- Building custom DRF authentication backends subclassing `BaseAuthentication`.
- **Token Rotation** and **Automatic Token Family Reuse Detection** to defend against refresh token hijacking.
- **Token Blacklisting** mechanics and pruning strategies using Redis and PostgreSQL.
- Client storage security (`localStorage` XSS vs `HttpOnly` SameSite Cookie CSRF trade-offs).

---

## 1. Authentication Paradigms: Sessions vs API Keys vs JWT

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| PARADIGM                 | MECHANISM                   | ENTERPRISE TRADE-OFFS           |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 1. Session Cookies       | Opaque session ID stored in | Pro: Instant server revocation. |
|    (django.contrib.      | cookie; server queries DB / | Con: Stateful; requires shared  |
|     sessions)            | Redis on EVERY request.     | session store across clusters.  |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 2. Static Token Auth     | Single static token stored  | Pro: Simple; easy for scripts.  |
|    (authtoken_token)     | in database table           | Con: Never expires; if leaked,  |
|                          | (DRF TokenAuthentication).  | permanent access until reset.   |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 3. JSON Web Tokens       | Self-contained, signed JSON | Pro: Completely stateless; zero |
|    (SimpleJWT /          | payload containing claims   | DB lookups on valid requests.   |
|     RFC 7519)            | and expiration timestamp.   | Con: Hard to revoke without     |
|                          |                             | introducing a blacklist layer.  |
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

---

## 2. Anatomy of a JSON Web Token (RFC 7519)

A JWT is a string composed of three distinct segments separated by periods (`.`):
$$\text{JWT} = \text{Base64URL}(\text{Header}) \,.\, \text{Base64URL}(\text{Payload}) \,.\, \text{Signature}$$

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxMDEsImV4cCI6MTgwMDAwMDAwMH0.7dK3...
├───────────────────────────────────┤ ├─────────────────────────────────────────┤ ├───────┤
          1. HEADER (Base64URL)                  2. PAYLOAD (Base64URL)              3. SIGNATURE
```

### Segment 1: Header
Identifies the cryptographic algorithm and token type:
```json
{
  "alg": "HS256",
  "typ": "JWT"
}
```

### Segment 2: Payload (Claims)
Contains identity claims, access scopes, and timestamps:
```json
{
  "token_type": "access",
  "exp": 1782800000,
  "iat": 1782799100,
  "jti": "5a4f3b7c9e1d4a82b9e6c1a7d2e3f4b5",
  "user_id": "c50f772f-255d-4d1d-8ddd-b58654bdf602",
  "tenant_id": "tenant_acme",
  "role": "admin"
}
```

### Segment 3: Cryptographic Signature
Ensures that the header and payload have not been tampered with in transit:
$$\text{Signature} = \text{HMAC-SHA256}\Big(\text{Base64URL}(\text{Header}) + \text{"."} + \text{Base64URL}(\text{Payload}), \; \text{SECRET\_KEY}\Big)$$

> [!IMPORTANT]
> **Base64URL is NOT Encryption**:
> The payload of a standard JWT is **encoded, not encrypted**. Any client, proxy, or adversary can decode the payload string. Never store sensitive secrets (passwords, social security numbers, private API tokens) inside JWT claims!

---

## 3. The Dual-Token Architecture

To balance security (the ability to revoke access quickly) with scalability (stateless verification), modern APIs implement the **Dual-Token Architecture**:

```
+─────────────────────────────────────────────────────────────────────────────+
|                         THE DUAL-TOKEN LIFECYCLE                            |
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  1. Login: POST /api/token/ (email + password)                              |
|     Server validates credentials and returns:                               |
|       - Access Token  (Lifetime: 5 - 15 minutes)                            |
|       - Refresh Token (Lifetime: 1 - 7 days)                                |
|                                                                             |
|  2. Standard API Requests: GET /api/v1/orders/                              |
|     Client sends header: Authorization: Bearer <access_token>               |
|     Server verifies signature & expiration locally (Zero DB query!)         |
|                                                                             |
|  3. Token Expiration: Access Token expires after 15 minutes.                |
|     Server returns HTTP 401 Unauthorized (token_not_valid).                 |
|                                                                             |
|  4. Silent Token Refresh: POST /api/token/refresh/                          |
|     Client submits refresh_token.                                           |
|     Server validates refresh token, checks blacklist,                       |
|     and issues a fresh Access Token!                                        |
+─────────────────────────────────────────────────────────────────────────────+
```

### Why Short-Lived Access Tokens?
If an attacker intercepts an access token (via man-in-the-middle or browser leak), the compromised token is valid for only **5 to 15 minutes**. Once it expires, the attacker cannot obtain a new access token without possessing the refresh token.

---

## 4. Cryptographic Foundations: Symmetric (HS256) vs Asymmetric (RS256/EdDSA)

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| ALGORITHM                | MECHANISM                   | ENTERPRISE USE CASE             |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| HS256 (HMAC-SHA256)      | Symmetric: The SAME shared  | Monolithic Django applications   |
|                          | secret signs and verifies   | where only Django issues and    |
|                          | the token.                  | verifies tokens.                |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| RS256 / EdDSA            | Asymmetric: Private Key     | Distributed Microservices &     |
| (RSA / Ed25519)          | signs tokens (Auth Service);| Gateways. Internal services     |
|                          | Public Key verifies tokens. | verify tokens using public key  |
|                          |                             | without knowing the private key!|
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

In microservice environments, asymmetric signing eliminates the security liability of distributing the signing `SECRET_KEY` across dozens of independent microservices.

---

## 5. Standard Registered Claims (RFC 7519)

| Claim | Full Name | Description & Enterprise Significance |
| :--- | :--- | :--- |
| **`exp`** | Expiration Time | Unix timestamp after which the token is invalid. Checked on every request. |
| **`iat`** | Issued At | Unix timestamp indicating when the token was minted. |
| **`nbf`** | Not Before | Token is not valid before this timestamp (useful for scheduled tokens). |
| **`jti`** | JWT ID | Globally unique UUID identifier for this token. **Essential for blacklisting!** |
| **`sub`** | Subject | Principal identifier (typically the user's UUID or primary key). |
| **`iss`** | Issuer | Identifies the identity provider or auth service that minted the token. |
| **`aud`** | Audience | Identifies the target recipient or microservice cluster for the token. |

---

## 6. Custom Claims Injection: Extending `TokenObtainPairSerializer`

By default, SimpleJWT only includes `user_id` in the access token payload. To prevent downstream services from querying the database just to find the user's email, role, or tenant ID, inject **custom claims**:

```python
# accounts/serializers.py
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Customizes JWT claims to embed organization and permission scopes.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Custom claims embedded directly into the signed access token
        token["email"] = user.email
        token["tenant_id"] = str(user.tenant_id)
        token["role"] = user.role
        token["is_staff"] = user.is_staff

        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # Add extra fields to the initial login JSON response body
        data["user"] = {
            "id": str(self.user.id),
            "email": self.user.email,
            "role": self.user.role,
        }
        return data
```

---

## 7. DRF Authentication Engine: Subclassing `BaseAuthentication`

Under the hood, DRF's authentication subsystem invokes `authenticate(request)` on each class in `DEFAULT_AUTHENTICATION_CLASSES`:

```python
# accounts/authentication.py
from rest_framework.authentication import BaseAuthentication
from rest_framework import exceptions
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model

User = get_user_model()


class StatelessJWTAuthentication(BaseAuthentication):
    """
    Pure stateless JWT authentication backend.
    Validates token signature and expiration without hitting the database!
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None  # No credentials provided; pass to next auth backend

        raw_token = auth_header.split(" ")[1]

        try:
            # 1. Cryptographically decode and verify signature + expiration
            validated_token = AccessToken(raw_token)
        except Exception as exc:
            raise exceptions.AuthenticationFailed(f"Invalid or expired token: {exc}")

        # 2. Extract subject from token payload
        user_id = validated_token.get("user_id")
        if not user_id:
            raise exceptions.AuthenticationFailed("Token contained no recognizable user identification.")

        # 3. Retrieve user or construct lightweight proxy user
        try:
            user = User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed("User not found or account deactivated.")

        # Return (user, auth) tuple as required by DRF protocol
        return (user, validated_token)

    def authenticate_header(self, request):
        return "Bearer"
```

---

## 8. The Refresh Token Rotation Lifecycle (`ROTATE_REFRESH_TOKENS = True`)

If a static refresh token is valid for 7 days and never changes, an attacker who steals it maintains access for 7 days.

**Token Rotation** mitigates this:
Every time the client calls `/api/token/refresh/`:
1. The server validates the existing refresh token.
2. The server **blacklists/invalidates the existing refresh token immediately**.
3. The server issues a brand new access token **AND a brand new refresh token**.
4. The client discards the old refresh token and stores the new pair.

```python
# settings.py
SIMPLE_JWT = {
    'ROTATE_REFRESH_TOKENS': True,       # Issue new refresh token on every refresh
    'BLACKLIST_AFTER_ROTATION': True,   # Invalidate old refresh token immediately
}
```

---

## 9. Automatic Refresh Token Reuse Detection & Token Family Invalidation

What happens if an attacker intercepts a rotated refresh token before the legitimate user can use it?

SimpleJWT implements **Token Family Invalidation (RFC 6749 Section 10.4)**:

```
[Legitimate User] ──(Rotates Token)──> Has Refresh Token B (Old Token A Blacklisted)
       │
[Adversary] ──(Submits Token A)──> Server checks Blacklist:
                                   TOKEN A IS ALREADY CONSUMED!
                                   🚨 REUSE ATTACK DETECTED!
                                   Sever immediately:
                                   1. Invalidates Token B!
                                   2. Revokes entire Token Family for User 101!
                                   3. Forces full re-login on all devices!
```

If the server ever receives a refresh request bearing a token that has **already been rotated and blacklisted**, it assumes a token theft incident has occurred. The server immediately **revokes all descendant refresh tokens in that family**, locking out both the adversary and the legitimate user until credentials are re-entered.

---

## 10. Token Blacklisting Mechanics: Solving the Stateless Revocation Dilemma

Because access tokens are verified statelessly without database lookups, they cannot be canceled remotely before their `exp` timestamp.

The enterprise compromise:
- **Access tokens remain short-lived** (e.g. 5 minutes). Even if not explicitly blacklisted, exposure is limited to 5 minutes.
- **Refresh tokens are tracked and blacklisted** via unique `jti` identifiers:

```
+─────────────────────────────────────────────────────────────+
|                  OUTSTANDING & BLACKLISTED TOKENS           |
+─────────────────────────────────────────────────────────────+
| Table: token_blacklist_outstandingtoken                     |
| - id: UUID                                                  |
| - user_id: UUID                                             |
| - jti: String (Hex UUID, unique)                            |
| - token: Encoded refresh token string                       |
| - created_at: DateTime                                      |
| - expires_at: DateTime                                      |
|                                                             |
| Table: token_blacklist_blacklistedtoken                     |
| - id: UUID                                                  |
| - token_id: FK -> token_blacklist_outstandingtoken          |
| - blacklisted_at: DateTime                                  |
+─────────────────────────────────────────────────────────────+
```

When a user logs out (`POST /api/token/blacklist/`), the refresh token's `jti` is inserted into `blacklistedtoken`. Any subsequent attempt to use that refresh token is rejected with HTTP 401.

---

## 11. Pruning Expired Blacklisted Tokens (`flushexpiredtokens`)

Over time, an active application accumulates millions of rotated refresh tokens in `token_blacklist_outstandingtoken` and `token_blacklist_blacklistedtoken`.

Once a token's `exp` timestamp has passed in real time, it is naturally invalid and **no longer needs to be tracked in the blacklist database**.

### Production Maintenance: Periodic Celery Task
Never let blacklist tables grow indefinitely. Schedule DRF's `flushexpiredtokens` command using Celery Beat:

```python
# tasks.py
from celery import shared_task
from django.core.management import call_command


@shared_task
def cleanup_expired_jwt_tokens():
    """
    Deletes expired tokens from outstanding and blacklist tables.
    Runs daily via Celery Beat.
    """
    call_command("flushexpiredtokens")
```

---

## 12. Sliding Session Expiration & User Activity Refresh Strategies

In high-security banking and enterprise applications, user sessions must automatically terminate after 30 minutes of inactivity, but should extend seamlessly as long as the user actively interacts with the application.

```python
# SimpleJWT Sliding Token Configuration
SIMPLE_JWT = {
    'SLIDING_TOKEN_LIFETIME': timedelta(minutes=15),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(hours=2),
}
```
A **sliding token** is a single token containing two expiration timestamps:
1. `exp`: Inactivity timeout (e.g. 15 minutes).
2. `refresh_exp`: Absolute session maximum (e.g. 8 hours).
As long as the client refreshes within the 15-minute window, the session slides forward up to the absolute 8-hour cap.

---

## 13. Client-Side Storage Security: `localStorage` vs `HttpOnly` Cookies

Where should a single-page application (React, Vue, iOS) store JWTs?

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| STORAGE MECHANISM        | SECURITY VULNERABILITY      | MITIGATION                      |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 1. Web localStorage      | Vulnerable to XSS:          | Strict Content Security Policy  |
|    or sessionStorage     | Any malicious script or     | (CSP), sanitizing HTML, no      |
|                          | compromised npm package can | unescaped user input.           |
|                          | read window.localStorage.   |                                 |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 2. HttpOnly, Secure,     | Vulnerable to CSRF:         | SameSite=Strict / Lax, anti-CSRF|
|    SameSite Cookies      | Browser auto-attaches cookie| headers (X-CSRFToken), custom   |
|    (RECOMMENDED)         | on cross-site requests.     | API request headers.            |
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

### The Gold-Standard Enterprise Pattern:
- Store the **Access Token in memory** (React state / JavaScript closure variable).
- Store the **Refresh Token in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie** that JavaScript cannot access.
- On browser refresh or access token expiration, JavaScript calls `/api/token/refresh/` (browser automatically passes the HttpOnly cookie) to get a new short-lived access token in memory.

---

## 14. Cryptographic Vulnerabilities & Attack Vectors

### 1. The `alg: "none"` Exploit
Early JWT libraries allowed tokens specifying `"alg": "none"` in their header to bypass signature verification entirely. Attackers modified claims (e.g. `"role": "admin"`) and omitted the signature. Modern libraries strictly whitelist expected algorithms.

### 2. Algorithm Confusion Attack (RS256 vs HS256)
If an authentication server expects RS256 (asymmetric) but accepts HS256, an attacker can re-sign a forged token using the **public RSA key** as the HMAC secret key. Because the public key is publicly available, the server verifies the signature successfully unless algorithms are strictly enforced.

### 3. Timing Attacks on Secret Verification
Comparing HMAC digests must always use `hmac.compare_digest` to prevent byte-by-byte timing inference.

---

## 15. High-Throughput JWT Performance: Redis vs Database Blacklist

In massive-scale APIs (> 50,000 requests/sec), hitting PostgreSQL to check token blacklists degrades performance. High-throughput architectures offload blacklisting to **Redis**:

```
Client Request -> Reverse Proxy -> Django API Worker
                                         │
                                         ▼
                             1. Verify HS256 / RS256 signature locally (0.01ms)
                                         │
                                         ▼
                             2. Check Redis Set: SISMEMBER blacklist <jti> (0.2ms)
                                         │
                         ┌───────────────┴───────────────┐
                      [Found]                         [Not Found]
                         │                               │
                         ▼                               ▼
                 401 Unauthorized             Execute Business View
```

By storing active blacklisted `jti` keys in a Redis Set with an automatic TTL matching the token's remaining lifetime, blacklist lookups execute in sub-millisecond memory without touching relational database connections.
