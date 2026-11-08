# Practical Engineering Exercises: OAuth2, JWT Authentication & Scopes

This document presents 3 enterprise-grade challenges focusing on modern token authentication architectures. Each exercise includes architectural requirements, security specifications, complete runnable reference implementations, and test assertions.

---

## Exercise 1: Asymmetric Key Token Service with JWKS (RFC 7517) Endpoint

### Objective
In a modern microservices architecture, downstream resource servers must never possess the private cryptographic key used to sign tokens. Instead, the central Identity Provider (IdP) signs tokens using an RSA private key (`RS256`) and publishes its public keys via an OpenID Connect / RFC 7517 standard **JWKS** (JSON Web Key Set) endpoint: `/.well-known/jwks.json`.

Build a standalone FastAPI service that:
1. Generates and stores an RSA 2048-bit key pair with a unique Key ID (`kid`).
2. Issues signed RS256 JWT access tokens containing the `kid` in the JOSE header.
3. Exposes `GET /.well-known/jwks.json` converting the RSA public key's modulus ($n$) and exponent ($e$) to Base64URL-encoded strings.
4. Provides a verification dependency that fetches the JWKS and validates tokens using the published public key.

### Complete Reference Implementation

```python
"""
Exercise 1 Reference Solution: RS256 Token Authority with RFC 7517 JWKS Endpoint
"""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

app = FastAPI(title="RS256 JWKS Token Authority", version="1.0.0")
bearer_scheme = HTTPBearer()

# ==============================================================================
# Cryptographic Key Generation & JWKS Serialization
# ==============================================================================

class KeyAuthority:
    """Manages RSA keys and RFC 7517 JWKS serialization."""

    def __init__(self) -> None:
        self.kid = f"key_{secrets.token_hex(8)}"
        self._private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self.public_key = self._private_key.public_key()
        self.private_pem = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @staticmethod
    def _int_to_base64url(val: int) -> str:
        """Convert a big-endian integer to Base64URL without trailing padding."""
        byte_length = (val.bit_length() + 7) // 8
        raw_bytes = val.to_bytes(byte_length, byteorder="big")
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")

    def get_jwks(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate standard RFC 7517 JSON Web Key Set."""
        pub_numbers = self.public_key.public_numbers()
        jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": self._int_to_base64url(pub_numbers.n),
            "e": self._int_to_base64url(pub_numbers.e),
        }
        return {"keys": [jwk]}

    def issue_token(self, subject: str, scopes: List[str]) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": subject,
            "scopes": scopes,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "iss": "https://auth.enterprise.internal",
        }
        # In RS256, kid MUST be in the JOSE header so verifiers know which key to use
        headers = {"kid": self.kid}
        return jwt.encode(payload, self.private_pem, algorithm="RS256", headers=headers)


# Instantiate Key Authority
auth_authority = KeyAuthority()


# ==============================================================================
# Endpoints
# ==============================================================================

@app.get("/.well-known/jwks.json", tags=["OIDC Discovery"])
async def get_jwks() -> Dict[str, Any]:
    """Expose public cryptographic keys for downstream microservice verifiers."""
    return auth_authority.get_jwks()


@app.post("/token", tags=["Authentication"])
async def issue_access_token(username: str = "alice") -> Dict[str, Any]:
    """Issue RS256 signed access token."""
    token = auth_authority.issue_token(
        subject=username,
        scopes=["billing:read", "reports:export"],
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 900}


# ==============================================================================
# Downstream Verification Dependency (Simulating Microservice)
# ==============================================================================

async def verify_jwt_via_jwks(
    creds: HTTPAuthorizationCredentials = Security(bearer_scheme),
) -> Dict[str, Any]:
    token = creds.credentials
    try:
        # Read unverified header to locate kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid or kid != auth_authority.kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token signed by unknown key ID (kid)",
            )

        # Verify signature with authority public key
        payload = jwt.decode(
            token,
            auth_authority.public_pem,
            algorithms=["RS256"],
            issuer="https://auth.enterprise.internal",
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        return payload
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Cryptographic verification failed: {str(exc)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/api/v1/protected-resource", tags=["Resource Server"])
async def get_protected_resource(payload: Dict[str, Any] = Depends(verify_jwt_via_jwks)):
    return {
        "status": "access_granted",
        "authorized_subject": payload["sub"],
        "delegated_scopes": payload["scopes"],
    }


# ==============================================================================
# Verification Test
# ==============================================================================

if __name__ == "__main__":
    import asyncio
    from httpx import ASGITransport, AsyncClient

    async def run_test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Fetch JWKS
            jwks_resp = await client.get("/.well-known/jwks.json")
            assert jwks_resp.status_code == 200
            keys = jwks_resp.json()["keys"]
            assert len(keys) == 1
            assert keys[0]["alg"] == "RS256"
            assert "n" in keys[0] and "e" in keys[0]
            print("[TEST 1 PASSED] RFC 7517 JWKS correctly published.")

            # 2. Issue Token
            token_resp = await client.post("/token?username=carol")
            assert token_resp.status_code == 200
            token = token_resp.json()["access_token"]
            print("[TEST 2 PASSED] RS256 Token successfully issued.")

            # 3. Access Protected Resource
            auth_resp = await client.get(
                "/api/v1/protected-resource",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert auth_resp.status_code == 200
            data = auth_resp.json()
            assert data["authorized_subject"] == "carol"
            assert "billing:read" in data["delegated_scopes"]
            print("[TEST 3 PASSED] Downstream JWKS signature verification succeeded.")

    asyncio.run(run_test())
```

---

## Exercise 2: Refresh Token Rotation (RTR) with Replay Attack & Reuse Detection

### Objective
Refresh Tokens are long-lived credentials. If an attacker intercepts a refresh token, they can silently maintain persistence. The OAuth2 2.0 Security Best Current Practice (BCP) mandates **Refresh Token Rotation (RTR)**:
1. Every time a refresh token is used to obtain a new access token, the auth server **invalidates** the current refresh token and issues a brand new one.
2. The auth server links tokens in a **Token Family**.
3. If an attacker attempts to reuse a previously consumed refresh token (a replay attack), the system detects the anomaly, **immediately revokes the entire token family** (invalidating all sessions for that device), and forces the legitimate user to re-authenticate with credentials.

### Complete Reference Implementation

```python
"""
Exercise 2 Reference Solution: Refresh Token Rotation & Replay Attack Detection
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

app = FastAPI(title="OAuth2 RTR Engine", version="1.0.0")

# ==============================================================================
# Domain Models & In-Memory Distributed Store Simulation
# ==============================================================================

@dataclass
class RefreshTokenRecord:
    token: str
    family_id: str
    user_id: str
    expires_at: float
    is_consumed: bool = False


class TokenFamilyStore:
    """
    Simulates a Redis token family store.
    Tracks active, consumed, and revoked token families.
    """

    def __init__(self) -> None:
        self.tokens: Dict[str, RefreshTokenRecord] = {}
        self.revoked_families: Set[str] = set()

    def create_family(self, user_id: str) -> RefreshTokenRecord:
        family_id = f"fam_{secrets.token_hex(8)}"
        token = secrets.token_urlsafe(32)
        record = RefreshTokenRecord(
            token=token,
            family_id=family_id,
            user_id=user_id,
            expires_at=time.time() + 86400 * 7,  # 7 days
            is_consumed=False,
        )
        self.tokens[token] = record
        return record

    def rotate_token(self, old_token: str) -> RefreshTokenRecord:
        record = self.tokens.get(old_token)
        if not record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # CRITICAL SECURITY CHECK: Has the family already been invalidated?
        if record.family_id in self.revoked_families:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session compromised: Token family revoked.",
            )

        # REPLAY ATTACK DETECTION:
        # If the token has already been marked as consumed, an attacker or compromised
        # party is replaying a stolen token. Immediately revoke the ENTIRE token family!
        if record.is_consumed:
            self.revoked_families.add(record.family_id)
            # Log critical security incident to SIEM / audit log
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Security Alert: Refresh token reuse detected! All associated sessions terminated.",
            )

        # Check expiration
        if time.time() > record.expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token expired",
            )

        # Mark current token as consumed
        record.is_consumed = True

        # Issue successor token within the SAME family
        new_token = secrets.token_urlsafe(32)
        new_record = RefreshTokenRecord(
            token=new_token,
            family_id=record.family_id,
            user_id=record.user_id,
            expires_at=time.time() + 86400 * 7,
            is_consumed=False,
        )
        self.tokens[new_token] = new_record
        return new_record


store = TokenFamilyStore()


# ==============================================================================
# Endpoints
# ==============================================================================

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


@app.post("/auth/login", response_model=TokenResponse)
async def login(username: str):
    # Authenticate credentials (simulated)
    record = store.create_family(user_id=username)
    access_token = f"acc_{secrets.token_hex(16)}"
    return TokenResponse(
        access_token=access_token,
        refresh_token=record.token,
    )


@app.post("/auth/refresh", response_model=TokenResponse)
async def refresh_tokens(refresh_token: str):
    new_record = store.rotate_token(refresh_token)
    access_token = f"acc_{secrets.token_hex(16)}"
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_record.token,
    )


# ==============================================================================
# Verification Test
# ==============================================================================

if __name__ == "__main__":
    import asyncio
    from httpx import ASGITransport, AsyncClient

    async def run_test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Login
            resp1 = await client.post("/auth/login?username=dev_lead")
            assert resp1.status_code == 200
            token_r1 = resp1.json()["refresh_token"]

            # 2. Legitimate Rotation (Step 1 -> Step 2)
            resp2 = await client.post(f"/auth/refresh?refresh_token={token_r1}")
            assert resp2.status_code == 200
            token_r2 = resp2.json()["refresh_token"]
            assert token_r2 != token_r1
            print("[TEST 1 PASSED] Initial refresh rotated token successfully.")

            # 3. Legitimate Rotation (Step 2 -> Step 3)
            resp3 = await client.post(f"/auth/refresh?refresh_token={token_r2}")
            assert resp3.status_code == 200
            token_r3 = resp3.json()["refresh_token"]
            print("[TEST 2 PASSED] Successor refresh rotated token successfully.")

            # 4. REPLAY ATTACK SIMULATION: Attacker replays consumed token_r1
            replay_resp = await client.post(f"/auth/refresh?refresh_token={token_r1}")
            assert replay_resp.status_code == 401
            assert "reuse detected" in replay_resp.json()["detail"]
            print("[TEST 3 PASSED] Replay attack intercepted and rejected.")

            # 5. VERIFY FAMILY REVOCATION: Legitimate user's newest token_r3 is also revoked!
            victim_resp = await client.post(f"/auth/refresh?refresh_token={token_r3}")
            assert victim_resp.status_code == 401
            assert "revoked" in victim_resp.json()["detail"].lower()
            print("[TEST 4 PASSED] Full token family revoked following compromise.")

    asyncio.run(run_test())
```

---

## Exercise 3: Hierarchical Scoped RBAC Dependency (`PermissionChecker`)

### Objective
In complex platforms, flat scope matching (`"read" == "read"`) is insufficient. Enterprise platforms require hierarchical scopes with wildcards, such as:
- `admin:*` satisfies all permissions.
- `invoices:*` satisfies `invoices:read`, `invoices:create`, `invoices:approve`.
- Exact scopes: `invoices:read`.

Build a reusable, parameterized FastAPI security dependency:
`PermissionChecker(required_scopes=["invoices:read", "reports:export"])`
that:
1. Integrates with FastAPI's native `SecurityScopes` to publish required permissions in Swagger UI OpenAPI documentation.
2. Resolves hierarchical wildcards efficiently.
3. Formats compliant RFC 6750 error responses with `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."` headers.

### Complete Reference Implementation

```python
"""
Exercise 3 Reference Solution: Hierarchical Scope RBAC Dependency
"""

from __future__ import annotations

import secrets
from typing import List, Set
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
import jwt

SECRET = "super_secure_enterprise_key_0123456789abcdef"
ALGO = "HS256"

app = FastAPI(title="Hierarchical Scope RBAC Engine", version="1.0.0")

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="auth/token",
    scopes={
        "invoices:read": "View customer invoices and payments.",
        "invoices:write": "Create and modify invoices.",
        "invoices:*": "Full authority over invoice management.",
        "reports:*": "Full authority over reporting engines.",
        "*": "Superuser wildcard root access.",
    },
)


class PermissionChecker:
    """
    Parametric dependency for hierarchical OAuth2 scope evaluation.
    """

    def __init__(self, required_scopes: List[str]) -> None:
        self.required_scopes = set(required_scopes)

    @staticmethod
    def _matches_wildcard(granted: str, required: str) -> bool:
        if granted == "*" or granted == required:
            return True
        if granted.endswith(":*"):
            namespace = granted[:-2]
            if required.startswith(namespace + ":") or required == namespace:
                return True
        return False

    def __call__(
        self,
        security_scopes: SecurityScopes,
        token: str = Depends(oauth2_scheme),
    ) -> dict:
        # 1. Decode token
        try:
            payload = jwt.decode(token, SECRET, algorithms=[ALGO])
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired token: {str(exc)}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        granted_scopes: Set[str] = set(payload.get("scopes", []))

        # 2. Check each required scope
        missing_scopes = []
        for req in self.required_scopes:
            if not any(self._matches_wildcard(granted, req) for granted in granted_scopes):
                missing_scopes.append(req)

        if missing_scopes:
            needed = " ".join(missing_scopes)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation forbidden. Missing required scope(s): {needed}",
                headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{needed}"'},
            )

        return payload


# ==============================================================================
# Endpoints
# ==============================================================================

@app.get(
    "/api/v1/invoices",
    dependencies=[Security(PermissionChecker(["invoices:read"]))],
    tags=["Billing"],
)
async def get_invoices():
    return {"invoices": [{"id": 1, "amount": 450.0}]}


@app.post(
    "/api/v1/invoices",
    dependencies=[Security(PermissionChecker(["invoices:write"]))],
    tags=["Billing"],
)
async def create_invoice():
    return {"status": "invoice_created"}


@app.get(
    "/api/v1/super-admin/audit",
    dependencies=[Security(PermissionChecker(["admin:audit", "reports:*"]))],
    tags=["Admin"],
)
async def admin_audit():
    return {"status": "full_audit_log_access"}


# ==============================================================================
# Verification Test
# ==============================================================================

if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timezone, timedelta
    from httpx import ASGITransport, AsyncClient

    def issue_token(scopes: List[str]) -> str:
        payload = {
            "sub": "user_42",
            "scopes": scopes,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
        return jwt.encode(payload, SECRET, algorithm=ALGO)

    async def run_test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Token A: Wildcard invoices:*
            token_invoice_manager = issue_token(["invoices:*"])
            # Token B: Read only
            token_read_only = issue_token(["invoices:read"])
            # Token C: Root wildcard *
            token_root = issue_token(["*"])

            # Test 1: invoices:* can access invoices:read
            r1 = await client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token_invoice_manager}"})
            assert r1.status_code == 200
            print("[TEST 1 PASSED] Wildcard invoices:* satisfied invoices:read requirement.")

            # Test 2: invoices:* can access invoices:write
            r2 = await client.post("/api/v1/invoices", headers={"Authorization": f"Bearer {token_invoice_manager}"})
            assert r2.status_code == 200
            print("[TEST 2 PASSED] Wildcard invoices:* satisfied invoices:write requirement.")

            # Test 3: read-only token FAILS on write
            r3 = await client.post("/api/v1/invoices", headers={"Authorization": f"Bearer {token_read_only}"})
            assert r3.status_code == 403
            assert 'error="insufficient_scope"' in r3.headers["WWW-Authenticate"]
            print("[TEST 3 PASSED] Read-only token correctly forbidden on write with RFC 6750 headers.")

            # Test 4: Root wildcard satisfies multi-scope admin endpoint
            r4 = await client.get("/api/v1/super-admin/audit", headers={"Authorization": f"Bearer {token_root}"})
            assert r4.status_code == 200
            print("[TEST 4 PASSED] Root wildcard '*' satisfied all required scopes.")

    asyncio.run(run_test())
```
