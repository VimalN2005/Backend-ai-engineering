# Rapid Revision & Quick Reference: OAuth2, JWT & Scopes

A condensed, 5-minute reference guide covering FastAPI OAuth2 Password Grant, PyJWT authentication, bcrypt password hashing, and scope-based authorization.

---

## 1. Core RFC Specifications Cheat Sheet

| RFC | Standard Title | Core Takeaway |
| :--- | :--- | :--- |
| **RFC 6749** | The OAuth 2.0 Authorization Framework | Defines roles (Resource Owner, Client, Auth Server, Resource Server) & grant types. |
| **RFC 6750** | The OAuth 2.0 Bearer Token Usage | Defines HTTP `Authorization: Bearer <token>` and `WWW-Authenticate` response headers. |
| **RFC 7519** | JSON Web Token (JWT) | Compact, URL-safe claim format (`Header.Payload.Signature`). |
| **RFC 7636** | Proof Key for Code Exchange (PKCE) | Dynamic cryptographic binding preventing auth code interception in public clients. |
| **RFC 8725** | JWT Best Current Practices (BCP) | Mandates single algorithm whitelisting, explicit key types, and banning `alg: "none"`. |

---

## 2. Production Boilerplate: Bcrypt Password Hashing

```python
import bcrypt

def hash_password(plain_password: str) -> str:
    """Hash plaintext password with 12 bcrypt salt rounds."""
    salt = bcrypt.gensalt(rounds=12)
    hashed_bytes = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed_bytes.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time password verification."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )
```

---

## 3. Production Boilerplate: PyJWT Token Lifecycle

```python
from datetime import datetime, timezone, timedelta
import secrets
import jwt

SECRET_KEY = "your-256-bit-hex-key-generated-via-secrets-token-hex-32"
ALGORITHM = "HS256"

def create_access_token(subject: str, scopes: list[str], expires_delta: timedelta = timedelta(minutes=15)) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "scopes": scopes,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_hex(16),  # Unique ID for revocation blocklist
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM],  # Strict single-item whitelist
        leeway=10,               # 10s clock skew tolerance
        options={"require": ["exp", "iat", "sub"]},
    )
```

---

## 4. FastAPI OAuth2 Password Grant & Scope Security

```python
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import (
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm,
    SecurityScopes,
)

app = FastAPI()

# 1. Define OAuth2 Scheme with Declared Scopes
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/token",
    scopes={
        "users:read": "Read user account details.",
        "users:write": "Modify user account details.",
        "admin:*": "Full administrator authority.",
    },
)

# 2. Token Issuance Endpoint (Accepts application/x-www-form-urlencoded)
@app.post("/auth/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # 1. Authenticate user via form_data.username & form_data.password
    # 2. Parse requested scopes: requested_scopes = form_data.scopes
    token = create_access_token(form_data.username, scopes=form_data.scopes)
    return {"access_token": token, "token_type": "bearer", "expires_in": 900}

# 3. Scope Enforcement Dependency
async def get_current_user_with_scopes(
    security_scopes: SecurityScopes,
    token: str = Depends(oauth2_scheme),
) -> dict:
    # Set challenge header
    auth_header = f'Bearer scope="{security_scopes.scope_str}"' if security_scopes.scopes else "Bearer"

    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": auth_header},
        )

    granted_scopes = set(payload.get("scopes", []))
    for required_scope in security_scopes.scopes:
        if required_scope not in granted_scopes and "*" not in granted_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not enough permissions. Required: {required_scope}",
                headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{required_scope}"'},
            )
    return payload

# 4. Protected Endpoints via Security()
@app.get("/users/me", dependencies=[Security(get_current_user_with_scopes, scopes=["users:read"])])
async def read_users_me():
    return {"message": "Access granted to users:read"}
```

---

## 5. Scopes vs. Roles Comparison Matrix

| Property | OAuth2 Scopes | Application Roles (RBAC) |
| :--- | :--- | :--- |
| **Purpose** | Delegated client capabilities | User identity and organization privileges |
| **Origin** | OAuth2 Authorization Framework | Domain Access Control Policy |
| **Granted To** | Client Application / Access Token | End-User Identity |
| **Example** | `read:orders`, `write:invoices` | `SystemAdmin`, `BillingAuditor`, `Support` |
| **Decoupling Rule** | Never equate `scope == "admin"` | Effective Perms = User Roles $\cap$ Token Scopes |

---

## 6. Refresh Token Rotation (RTR) Lifecycle

```
[Client Login] ---> Issues (Access Token A, Refresh Token R1) [Family 101, Unconsumed]
       |
       v (Access Token A expires)
[Client Refresh with R1] ---> Server consumes R1, issues (Access Token B, Refresh Token R2)
       |
       +---> Case 1: Legitimate client uses R2 next time -> Rotates to R3 (Success)
       |
       +---> Case 2: Attacker replays consumed R1 -> REPLAY DETECTED!
                     Server revokes Family 101 completely -> R2 and R3 both invalidated.
```

---

## 7. High-Frequency Interview Flashcards

### Q1: Why must bcrypt password hashing always specify at least 12 rounds?
> **Answer**: Bcrypt is intentionally designed to be computationally slow and memory-intensive to resist GPU and ASIC brute-force attacks. Each round doubles the work factor ($2^{\text{rounds}}$ iterations). 12 rounds takes ~250–350 ms on modern server CPUs—fast enough for interactive logins, but prohibitive for attackers trying billions of passwords.

### Q2: What is the purpose of the `WWW-Authenticate` header when returning 401 or 403?
> **Answer**: RFC 6750 requires the resource server to include the `WWW-Authenticate` header in 401/403 responses. On a 401, it advertises the supported scheme (`Bearer`). On a 403 scope error, it indicates the failure reason (`error="insufficient_scope"`) and the exact required scope (`scope="users:write"`), allowing client applications to prompt users for delegated consent.

### Q3: Why is `secrets.compare_digest` mandatory when checking API keys or tokens?
> **Answer**: Standard string equality (`a == b`) terminates on the first mismatched character (short-circuit evaluation). Attackers can measure response time down to nanoseconds to guess the secret one byte at a time (side-channel timing attack). `compare_digest` executes in constant time regardless of where the mismatch occurs.

### Q4: What does the `jti` claim do in a JWT?
> **Answer**: `jti` (JWT ID) provides a unique cryptographic identifier for a specific token instance. It is required for stateful revocation (blocklisting in Redis) and replay attack prevention for one-time-use tokens.
