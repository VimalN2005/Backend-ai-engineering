# Day 55: OAuth2 with Password Grant, JWT Authentication & Scopes

Welcome to Day 55 of the Enterprise Backend & AI Engineering curriculum. Today, we master the industry-standard security architecture for modern asynchronous web APIs: **OAuth2 with Password Grant, JSON Web Token (JWT) Authentication, and Granular OAuth2 Scopes**.

In high-concurrency distributed backends, session-cookie architectures tied to centralized server memory fail to scale across multi-region clusters and microservice boundaries. Stateless JWT tokens cryptographically signed by an identity provider allow decentralized resource servers to authenticate requests, enforce role-based access control (RBAC), and evaluate granular permission scopes without making blocking database round-trips for every incoming HTTP request.

This guide provides an exhaustive 15-point architectural deep dive into OAuth2 flows, JWT cryptographic standards, password hashing mechanics, and FastAPI's native security dependency pipelines.

---

## 1. The OAuth2 Authorization Framework (RFC 6749) Overview

OAuth2 is not an authentication protocol; it is an **authorization framework** that enables third-party applications to obtain limited access to an HTTP service. However, combined with identity standards (such as OpenID Connect or JWT bearer tokens), it forms the global foundation of modern API security.

### Fundamental Distinction: AuthN vs AuthZ
- **Authentication (AuthN)**: *Who are you?* (Verifying credentials: username, password, MFA token).
- **Authorization (AuthZ)**: *What are you allowed to do?* (Permissions: reading orders, deleting users, administering billing).

### The Four OAuth2 Roles
```
+---------------------------------------------------------------------------------+
|                                 OAuth2 Roles                                    |
|                                                                                 |
|  1. Resource Owner  : The user who owns the data (e.g. Alice).                  |
|  2. Client          : The application requesting access (SPA, Mobile App, CLI). |
|  3. Auth Server     : Issues tokens after authenticating the Resource Owner.    |
|  4. Resource Server : The FastAPI microservice hosting protected endpoints.     |
+---------------------------------------------------------------------------------+
```

---

## 2. OAuth2 Grant Types & The Password Grant

RFC 6749 defines multiple **Grant Types** (workflows for obtaining an access token):
1. **Authorization Code Grant (with PKCE)**: Standard for third-party apps, Single Page Applications (React/Vue), and Mobile apps.
2. **Client Credentials Grant**: Machine-to-Machine (M2M) communication where no human user is involved.
3. **Refresh Token Grant**: Exchanging a long-lived refresh token for a fresh short-lived access token.
4. **Resource Owner Password Credentials Grant (Password Grant)**: The user provides their `username` and `password` directly to the client application, which forwards them in an `application/x-www-form-urlencoded` POST request to the token endpoint.

### When to Use Password Grant
The Password Grant is designed strictly for **trusted, first-party clients** (e.g., your company's own official mobile app or internal admin dashboard communicating directly with your own backend). In third-party scenarios, Authorization Code with PKCE is mandatory to prevent credential harvesting.

---

## 3. JSON Web Token (JWT) Anatomy & Cryptographic Standards (RFC 7519)

A JSON Web Token is a compact, URL-safe means of representing claims between two parties. It consists of three Base64URL-encoded segments separated by periods (`.`):

$$\text{JWT} = \text{Header} \,.\, \text{Payload} \,.\, \text{Signature}$$

```
+---------------------------------------------------------------------------------+
|                                  JWT Structure                                  |
|                                                                                 |
| 1. Header (Algorithm & Token Type):                                             |
|    {"alg": "HS256", "typ": "JWT"}                                               |
|                                                                                 |
| 2. Payload (Claims / Data):                                                     |
|    {                                                                            |
|      "sub": "user_98124",            <- Subject (User Identifier)               |
|      "exp": 1731114000,              <- Expiration Time (Unix Epoch)            |
|      "iat": 1731110400,              <- Issued At (Unix Epoch)                  |
|      "scopes": ["orders:read", ...]  <- Delegated Permissions                   |
|    }                                                                            |
|                                                                                 |
| 3. Cryptographic Signature:                                                     |
|    HMACSHA256(                                                                  |
|      base64UrlEncode(header) + "." + base64UrlEncode(payload),                  |
|      SECRET_KEY                                                                 |
|    )                                                                            |
+---------------------------------------------------------------------------------+
```

### Critical Security Truth: Encoding vs Encryption
By default, JWTs are **signed, not encrypted**. The payload is merely Base64URL-encoded. Anyone who intercepts the token can decode and view its claims. **Never store passwords, credit card numbers, or sensitive PII inside a standard JWT payload!**

---

## 4. Symmetric (HS256) vs Asymmetric (RS256/ES256) Signing

| Feature | Symmetric Signing (HMAC HS256) | Asymmetric Signing (RSA RS256 / ECDSA ES256) |
| :--- | :--- | :--- |
| **Key Architecture** | Single shared secret key (`SECRET_KEY`) | Keypair: Private Key (signs) + Public Key (verifies) |
| **Trust Model** | Every service verifying tokens must know the secret key | Only the Auth Server knows the Private Key; all Resource Servers know only the Public Key |
| **Security Risk** | If one microservice is compromised, attackers can forge valid tokens for the entire ecosystem | If a Resource Server is compromised, the attacker cannot forge new tokens |
| **Key Distribution** | Environment variables / Secret vaults | Public keys published over HTTPS via JWKS (`/.well-known/jwks.json`) |
| **Performance** | Extremely fast (~1.5x faster than RSA) | Higher CPU cost during cryptographic verification |
| **Best Used For** | Monolithic backends, tightly coupled internal services | Microservices, multi-tenant SaaS, enterprise federated identity |

---

## 5. FastAPI Security Utilities: `OAuth2PasswordBearer` & `OAuth2PasswordRequestForm`

FastAPI provides two dedicated classes that bridge OAuth2 specifications directly into the dependency injection engine:

```python
from fastapi import FastAPI, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

app = FastAPI()

# Declares the token acquisition URL.
# Automatically injects the "Authorize" button into Swagger UI docs (/docs)!
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token",
    scopes={
        "orders:read": "Read access to customer orders.",
        "orders:write": "Create and modify customer orders.",
        "admin": "Full administrative management access.",
    },
)
```

### How `OAuth2PasswordBearer` Operates
1. Inspects incoming request headers for `Authorization: Bearer <token>`.
2. If missing or malformed, automatically raises HTTP `401 Unauthorized` with `WWW-Authenticate: Bearer`.
3. If present, extracts the raw string token and injects it into dependent functions.

---

## 6. Password Hashing Mechanics & Salt Rounds

Storing passwords in plaintext or using fast hashing algorithms (MD5, SHA-1, SHA-256) is a severe vulnerability. Modern GPUs can compute over 100 billion SHA-256 hashes per second, making dictionary attacks trivial.

### Key Derivation Functions (KDFs): `bcrypt`
Passwords must be hashed using a deliberately slow, memory-hard Key Derivation Function. `bcrypt` incorporates:
1. **Cryptographic Salt**: A unique 16-byte random string generated per password. Prevents Rainbow Table attacks.
2. **Work Factor (Cost Parameter)**: Exponential iteration count (default $2^{12} = 4,096$ rounds). Tune to require ~250–350ms of CPU time per hash, slowing down brute-force attacks while maintaining acceptable login latency.

```python
import bcrypt

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    hashed_bytes = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed_bytes.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Constant-time comparison prevents timing attacks!
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
```

---

## 7. Access Tokens vs Refresh Tokens Architecture

A secure authentication system must balance user convenience (not logging in every 15 minutes) with security containment (stolen tokens must expire rapidly).

```
+---------------------------------------------------------------------------------+
|                        Token Dual-Lifecycle Pattern                             |
|                                                                                 |
| 1. Login Request (POST /auth/token with username & password)                    |
|    <--- Returns: { access_token (15m TTL), refresh_token (7d TTL) }             |
|                                                                                 |
| 2. API Requests (GET /api/v1/orders)                                            |
|    ---> Sends access_token in Authorization: Bearer <token>                     |
|    <--- 200 OK                                                                  |
|                                                                                 |
| 3. Access Token Expires (After 15 minutes)                                      |
|    ---> Sends expired access_token                                              |
|    <--- 401 Unauthorized (Token Expired)                                        |
|                                                                                 |
| 4. Silent Token Refresh (POST /auth/refresh with refresh_token)                  |
|    ---> Validates refresh_token against database or Redis                       |
|    <--- Returns: fresh access_token + ROTATED new refresh_token                 |
+---------------------------------------------------------------------------------+
```

### Refresh Token Rotation & Reuse Detection
When a refresh token is used, it should be immediately invalidated, and a new refresh token issued. If a client attempts to use an already-invalidated refresh token, the Auth Server flags a **Token Theft Incident**, immediately revoking all refresh tokens issued to that user account.

---

## 8. OAuth2 Scopes: Granular Permission Delegation (RFC 6749 Section 3.3)

OAuth2 scopes define the **specific permission boundaries** granted to an access token.

```python
from fastapi import Security, HTTPException, status
from fastapi.security import SecurityScopes

async def get_current_user(
    security_scopes: SecurityScopes,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> AuthenticatedUser:
    # 1. Decode JWT and extract token scopes
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    token_scopes = payload.get("scopes", [])
    
    # 2. Check if token satisfies the endpoint's required scopes
    for required_scope in security_scopes.scopes:
        if required_scope not in token_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not enough permissions. Required scope: '{required_scope}'",
                headers={"WWW-Authenticate": f'Bearer scope="{security_scopes.scope_str}"'},
            )
            
    return AuthenticatedUser(id=payload["sub"], scopes=token_scopes)
```

---

## 9. Scopes vs Roles: RBAC vs Scoped Permissions

Engineers frequently confuse Roles with Scopes:

```
+---------------------------------------------------------------------------------+
|                                 Role (RBAC)                                     |
| Attribute of the USER entity stored in the database (e.g. role: "ADMIN").       |
| Defines the maximum theoretical capabilities of the human operator.            |
+---------------------------------------------------------------------------------+
                                       |
                   User delegates subset of authority
                                       v
+---------------------------------------------------------------------------------+
|                                 OAuth2 Scope                                    |
| Attribute of the ACCESS TOKEN itself (e.g. scopes: ["reports:read"]).           |
| An Admin might log in via a mobile client and request only read scopes.         |
| The resource server authorizes based on the TOKEN'S scopes, not user title!     |
+---------------------------------------------------------------------------------+
```

---

## 10. Token Revocation & Blacklisting Strategies

Because JWTs are stateless, they remain mathematically valid until their `exp` timestamp elapses. If a user changes their password, is terminated, or reports a stolen device, you must be able to invalidate active tokens immediately.

### Production Revocation Strategies
1. **Redis Deny-List (Token Blacklisting)**:
   - When a token is revoked (e.g. at `/auth/logout`), store its unique JWT ID (`jti` claim) in Redis:
     `SET blacklist:jti:<uuid> 1 EX <remaining_seconds_until_exp>`
   - The key auto-expires when the token naturally reaches its expiration time.
   - Resource servers check Redis before accepting a token.
2. **User Token Epoch / Versioning**:
   - Store `token_version: int = 1` on the User database record.
   - Embed `token_version: 1` inside the JWT claims.
   - When logging out of all devices, increment the user's `token_version` to `2`. Any token presenting `token_version < 2` is rejected.

---

## 11. Security Vulnerabilities & Attack Vectors

### A. Algorithm Confusion Attack
An attacker alters the JWT header from `{"alg": "RS256"}` to `{"alg": "HS256"}`. If the backend improperly verifies an asymmetric token using an HMAC algorithm, it may use the public key string as the HMAC secret! Since the public key is known to the attacker, they can forge arbitrary signatures.
- **Remediation**: Always pass an explicit whitelist of allowed algorithms: `jwt.decode(token, key, algorithms=["RS256"])`.

### B. The `alg: "none"` Exploit
The JWT specification allows `alg: "none"` for unsigned tokens. Vulnerable libraries accept tokens with no signature.
- **Remediation**: Modern PyJWT strictly forbids `alg: "none"` unless explicitly and dangerously requested.

### C. Weak Secret Keys
If the symmetric secret key is short or predictable (`"secret"`, `"password123"`), attackers can brute-force the key offline using Hashcat at billions of guesses per second.
- **Remediation**: Generate cryptographically random 256-bit keys: `openssl rand -hex 32`.

---

## 12. The `Security` Dependency vs Standard `Depends`

In FastAPI, route protection can use either `Depends` or `Security`:

```python
# Standard Depends: Enforces dependency but does NOT declare scopes to OpenAPI
user = Annotated[User, Depends(get_current_user)]

# Security Dependency: Declares required scopes directly to OpenAPI / Swagger UI!
@app.get(
    "/api/v1/orders",
    dependencies=[Security(get_current_user, scopes=["orders:read"])],
)
async def list_orders():
    ...
```

### Swagger UI Integration
Using `Security(..., scopes=[...])` instructs FastAPI to render interactive scope badges in Swagger UI. Clicking the "Authorize" padlock in the browser displays checkboxes for the required scopes.

---

## 13. Hierarchical Authentication Pipeline in FastAPI

```
                    [ Incoming HTTP Request ]
                                |
                                v
               [ OAuth2PasswordBearer Dependency ]
               - Extracts token from Authorization header
               - Rejects with 401 if header is absent
                                |
                                v
                   [ get_current_user Dependency ]
               - Validates cryptographic signature
               - Asserts expiration time (`exp`)
               - Checks Redis token revocation deny-list
               - Hydrates user context from database
                                |
                                v
                   [ SecurityScopes Verification ]
               - Compares token scopes against route scopes
               - Raises 403 Forbidden if scopes are missing
                                |
                                v
                    [ Protected Route Handler ]
```

---

## 14. Token Storage: `localStorage` vs HttpOnly Cookies

| Storage Location | XSS Vulnerability | CSRF Vulnerability | Production Recommendation |
| :--- | :--- | :--- | :--- |
| **Browser `localStorage`** | **CRITICAL**: Any JavaScript injected via XSS can read and exfiltrate the token | Immune to CSRF | Unacceptable for high-value financial/enterprise applications |
| **`HttpOnly; Secure; SameSite=Strict` Cookie** | **IMMUNE**: JavaScript cannot access the token | Vulnerable to CSRF (mitigated by SameSite=Strict + CSRF tokens) | Recommended for browser SPAs |
| **Authorization Header** | Managed in application memory | Immune to CSRF | Ideal for Mobile Apps, Microservices, and CLI tools |

---

## 15. Production Deployment Checklist

1. **Secret Key Entropy**: Use a 256-bit or 512-bit cryptographically secure key generated via `secrets.token_hex(32)`.
2. **Short Access Token Lifespan**: Set access token expiration to 10–15 minutes.
3. **Always Set `algorithm` Explicitly**: Never omit the `algorithms=["HS256"]` parameter during decode calls.
4. **HTTPS Enforcement**: Never transmit bearer tokens over unencrypted HTTP.
5. **Rate-Limit the Token Endpoint**: Apply strict rate limiting (e.g. 5 attempts/minute) on `/auth/token` to prevent credential-stuffing attacks.

---
*Next Step: Explore `example_01.py` for a fully functional runnable OAuth2/JWT pipeline, and `example_02.py` for signature throughput benchmarking and timing-attack defense diagnostics.*
