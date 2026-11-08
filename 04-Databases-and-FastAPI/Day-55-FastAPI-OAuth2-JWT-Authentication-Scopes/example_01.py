"""
Day 55: Enterprise OAuth2 with Password Grant, JWT Authentication & Scopes
==========================================================================

Complete runnable FastAPI security pipeline demonstrating:
1. OAuth2 Password Bearer flow with OAuth2PasswordBearer and OAuth2PasswordRequestForm
2. Secure password hashing using bcrypt (work factor 12)
3. Cryptographic JWT access token issuance and signature verification (PyJWT)
4. Granular authorization using OAuth2 Scopes and SecurityScopes
5. Dual-token lifecycle: Short-lived Access Token & Rotated Refresh Token
6. Token revocation blacklist (Deny-list) mechanics
7. Automated async test suite using httpx.AsyncClient verifying all security invariants
"""

import asyncio
from datetime import datetime, timedelta, timezone
import secrets
from typing import Annotated, Dict, List, Optional, Set
import uuid

import bcrypt
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    Security,
    status,
)
from fastapi.security import (
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm,
    SecurityScopes,
)
import httpx
import jwt
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ---------------------------------------------------------------------------
# 1. Cryptographic Security Configuration
# ---------------------------------------------------------------------------

# 256-bit cryptographically random secret key
JWT_SECRET_KEY: str = "4d8a9f3e1b7c2d5e8a0f4c6b8d2e5a7c9f1b3d5e7a0c2e4f6b8d1a3c5e7f9b0d"
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
REFRESH_TOKEN_EXPIRE_DAYS: int = 7

# Available scopes catalog
OAUTH2_SCOPES: Dict[str, str] = {
    "me": "Read own user profile information.",
    "orders:read": "Read customer orders and invoices.",
    "orders:write": "Create and mutate customer orders.",
    "admin": "Full system administrative access.",
}

# OAuth2 Password Bearer scheme declaring the token acquisition endpoint
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token",
    scopes=OAUTH2_SCOPES,
)


# ---------------------------------------------------------------------------
# 2. In-Memory Mock Storage & Security State
# ---------------------------------------------------------------------------

class UserRecord(BaseModel):
    id: str
    username: str
    email: str
    hashed_password: str
    is_active: bool = True
    assigned_scopes: List[str]


def hash_password(password: str) -> str:
    """Hashes plaintext password using bcrypt with 12 salt rounds."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies plaintext against bcrypt hash in constant-time."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


# Seed initial test users
USERS_DATABASE: Dict[str, UserRecord] = {
    "alice_operator": UserRecord(
        id="usr_01HXYZ1",
        username="alice_operator",
        email="alice@cloud.io",
        hashed_password=hash_password("OperatorPass123!"),
        assigned_scopes=["me", "orders:read"],
    ),
    "bob_admin": UserRecord(
        id="usr_01HXYZ2",
        username="bob_admin",
        email="bob@cloud.io",
        hashed_password=hash_password("AdminSecurePass999!"),
        assigned_scopes=["me", "orders:read", "orders:write", "admin"],
    ),
}

# In-memory Token Revocation Blacklist (Deny-list) storing revoked JTI tokens
REVOKED_TOKEN_JTI_BLACKLIST: Set[str] = set()

# Refresh Token Storage: {refresh_token_string: user_id}
REFRESH_TOKEN_STORE: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# 3. Pydantic DTOs & Token Schemas
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    scope: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserPublicProfile(BaseModel):
    id: str
    username: str
    email: str
    scopes: List[str]


# ---------------------------------------------------------------------------
# 4. JWT Token Generation & Verification Helpers
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: str,
    granted_scopes: List[str],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Creates a cryptographically signed JWT access token with JTI and Scopes."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    jti = str(uuid.uuid4())

    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": jti,
        "scopes": granted_scopes,
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Creates a random, cryptographically secure opaque refresh token."""
    token = secrets.token_urlsafe(48)
    REFRESH_TOKEN_STORE[token] = user_id
    return token


# ---------------------------------------------------------------------------
# 5. FastAPI Security Dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    security_scopes: SecurityScopes,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> UserRecord:
    """
    Central Authentication & Authorization Dependency:
    1. Decodes and verifies cryptographic signature.
    2. Validates expiration (exp) and check revocation blacklist (jti).
    3. Resolves user entity from database.
    4. Asserts that token scopes satisfy all endpoint-required security_scopes.
    """
    authenticate_value = (
        f'Bearer scope="{security_scopes.scope_str}"'
        if security_scopes.scopes
        else "Bearer"
    )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials or token expired.",
        headers={"WWW-Authenticate": authenticate_value},
    )

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
        user_id: Optional[str] = payload.get("sub")
        jti: Optional[str] = payload.get("jti")
        token_scopes: List[str] = payload.get("scopes", [])

        if not user_id or not jti:
            raise credentials_exception

    except jwt.PyJWTError:
        raise credentials_exception

    # Check if token has been revoked (blacklisted)
    if jti in REVOKED_TOKEN_JTI_BLACKLIST:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked. Please authenticate again.",
            headers={"WWW-Authenticate": authenticate_value},
        )

    # Locate user in database
    user = next((u for u in USERS_DATABASE.values() if u.id == user_id), None)
    if not user or not user.is_active:
        raise credentials_exception

    # Granular Scope Authorization Check
    for required_scope in security_scopes.scopes:
        if required_scope not in token_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not enough permissions. Missing required scope: '{required_scope}'.",
                headers={"WWW-Authenticate": authenticate_value},
            )

    return user


CurrentUser = Annotated[UserRecord, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# 6. FastAPI Application & Route Controllers
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Enterprise OAuth2 & JWT Security API",
    version="1.0.0",
    description="Stateless token authentication and scope-based authorization.",
)


@app.post(
    "/api/v1/auth/token",
    response_model=TokenResponse,
    summary="OAuth2 Password Grant Login",
    tags=["Authentication"],
)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """
    Standard OAuth2 Resource Owner Password Credentials endpoint.
    Accepts application/x-www-form-urlencoded credentials.
    """
    user = USERS_DATABASE.get(form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Filter requested scopes against user's permitted scopes
    requested_scopes = form_data.scopes
    if not requested_scopes:
        granted_scopes = user.assigned_scopes
    else:
        # User cannot grant themselves scopes they do not possess!
        granted_scopes = [s for s in requested_scopes if s in user.assigned_scopes]

    access_token = create_access_token(user.id, granted_scopes)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token=refresh_token,
        scope=" ".join(granted_scopes),
    )


@app.post(
    "/api/v1/auth/refresh",
    response_model=TokenResponse,
    summary="Exchange Refresh Token",
    tags=["Authentication"],
)
async def refresh_access_token(payload: RefreshTokenRequest):
    """
    Exchanges an active refresh token for a fresh access token with rotation.
    """
    user_id = REFRESH_TOKEN_STORE.pop(payload.refresh_token, None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    user = next((u for u in USERS_DATABASE.values() if u.id == user_id), None)
    if not user:
        raise HTTPException(status_code=401, detail="User account no longer exists.")

    new_access_token = create_access_token(user.id, user.assigned_scopes)
    new_refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=new_access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token=new_refresh_token,
        scope=" ".join(user.assigned_scopes),
    )


@app.post(
    "/api/v1/auth/logout",
    summary="Revoke Token (Logout)",
    tags=["Authentication"],
)
async def logout(token: Annotated[str, Depends(oauth2_scheme)]):
    """
    Blacklists the active access token's JTI.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        jti = payload.get("jti")
        if jti:
            REVOKED_TOKEN_JTI_BLACKLIST.add(jti)
    except jwt.PyJWTError:
        pass  # Token already invalid

    return {"status": "success", "message": "Token successfully revoked."}


@app.get(
    "/api/v1/users/me",
    response_model=UserPublicProfile,
    summary="Get Current User Profile",
    tags=["Users"],
)
async def read_users_me(
    current_user: Annotated[UserRecord, Security(get_current_user, scopes=["me"])],
):
    """Protected endpoint requiring the 'me' scope."""
    return UserPublicProfile(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        scopes=current_user.assigned_scopes,
    )


@app.get(
    "/api/v1/orders",
    summary="List Customer Orders",
    tags=["Orders"],
)
async def read_orders(
    current_user: Annotated[UserRecord, Security(get_current_user, scopes=["orders:read"])],
):
    """Protected endpoint requiring the 'orders:read' scope."""
    return {
        "status": "success",
        "authorized_user": current_user.username,
        "orders": [
            {"id": "ORD-901", "amount": 250.0},
            {"id": "ORD-902", "amount": 1250.0},
        ],
    }


@app.delete(
    "/api/v1/admin/purge",
    summary="Purge System Records (Admin Only)",
    tags=["Admin"],
)
async def admin_purge_system(
    current_user: Annotated[UserRecord, Security(get_current_user, scopes=["admin"])],
):
    """High-privilege endpoint requiring the 'admin' scope."""
    return {"status": "purged", "authorized_by": current_user.username}


# ---------------------------------------------------------------------------
# 7. Automated Verification Harness (httpx.AsyncClient)
# ---------------------------------------------------------------------------

async def run_verification():
    print("=" * 75)
    print("OAUTH2, JWT & GRANULAR SCOPES ARCHITECTURAL VERIFICATION SUITE")
    print("=" * 75)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://authserver") as client:
        # Test 1: Invalid Password Authentication Failure
        print("\n[TEST 1] Testing Invalid Password Authentication Failure (HTTP 400)...")
        resp1 = await client.post(
            "/api/v1/auth/token",
            data={"username": "alice_operator", "password": "WrongPassword!"},
        )
        assert resp1.status_code == 400
        assert "Incorrect username or password" in resp1.json()["detail"]
        print("  -> PASSED: Invalid password rejected with standard OAuth2 error.")

        # Test 2: Successful Operator Login & Scope Issuance
        print("\n[TEST 2] Testing Operator Login & Token Issuance...")
        resp2 = await client.post(
            "/api/v1/auth/token",
            data={"username": "alice_operator", "password": "OperatorPass123!"},
        )
        assert resp2.status_code == 200
        token_data_operator = resp2.json()
        operator_access_token = token_data_operator["access_token"]
        operator_refresh_token = token_data_operator["refresh_token"]
        print(f"  Access Token: {operator_access_token[:25]}... (Scopes: {token_data_operator['scope']})")
        assert "me" in token_data_operator["scope"]
        assert "orders:read" in token_data_operator["scope"]
        assert "admin" not in token_data_operator["scope"]
        print("  -> PASSED: Operator authenticated; scopes granted accurately.")

        # Test 3: Accessing Protected Endpoint with Valid Scope (200 OK)
        print("\n[TEST 3] Testing Scope-Protected Endpoint (orders:read)...")
        headers_operator = {"Authorization": f"Bearer {operator_access_token}"}
        resp3 = await client.get("/api/v1/orders", headers=headers_operator)
        assert resp3.status_code == 200
        print(f"  Orders Response: {resp3.json()}")
        assert resp3.json()["authorized_user"] == "alice_operator"
        print("  -> PASSED: Valid scope granted access to protected resource.")

        # Test 4: Scope Insufficiency Rejection (HTTP 403 Forbidden)
        print("\n[TEST 4] Testing Scope Insufficiency (Operator attempting Admin purge)...")
        resp4 = await client.delete("/api/v1/admin/purge", headers=headers_operator)
        assert resp4.status_code == 403
        print(f"  403 Detail: {resp4.json()['detail']}")
        print(f"  WWW-Authenticate Header: {resp4.headers.get('WWW-Authenticate')}")
        assert "Missing required scope: 'admin'" in resp4.json()["detail"]
        assert 'Bearer scope="admin"' in resp4.headers.get("WWW-Authenticate")
        print("  -> PASSED: Operator lacking 'admin' scope strictly rejected with 403 Forbidden.")

        # Test 5: Admin Login and Admin Endpoint Access
        print("\n[TEST 5] Testing Admin User Authentication and Access...")
        resp5 = await client.post(
            "/api/v1/auth/token",
            data={"username": "bob_admin", "password": "AdminSecurePass999!"},
        )
        assert resp5.status_code == 200
        admin_access_token = resp5.json()["access_token"]
        headers_admin = {"Authorization": f"Bearer {admin_access_token}"}

        resp5_admin = await client.delete("/api/v1/admin/purge", headers=headers_admin)
        assert resp5_admin.status_code == 200
        assert resp5_admin.json()["status"] == "purged"
        print("  -> PASSED: Admin with 'admin' scope authorized successfully.")

        # Test 6: Token Revocation (Logout Deny-list)
        print("\n[TEST 6] Testing Token Revocation (Logout Deny-list)...")
        resp6_logout = await client.post("/api/v1/auth/logout", headers=headers_operator)
        assert resp6_logout.status_code == 200

        # Attempting to use the revoked operator token should now fail with 401
        resp6_retry = await client.get("/api/v1/orders", headers=headers_operator)
        assert resp6_retry.status_code == 401
        assert "Token has been revoked" in resp6_retry.json()["detail"]
        print("  -> PASSED: Blacklisted JTI token rejected immediately upon reuse.")

        # Test 7: Refresh Token Rotation
        print("\n[TEST 7] Testing Refresh Token Exchange...")
        resp7 = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": operator_refresh_token},
        )
        assert resp7.status_code == 200
        new_token_data = resp7.json()
        fresh_access_token = new_token_data["access_token"]
        print(f"  New Rotated Access Token: {fresh_access_token[:25]}...")

        # Test that fresh access token works
        resp7_test = await client.get(
            "/api/v1/orders", headers={"Authorization": f"Bearer {fresh_access_token}"}
        )
        assert resp7_test.status_code == 200
        print("  -> PASSED: Refresh token successfully rotated and new access token functional.")

    print("\n" + "=" * 75)
    print("ALL OAUTH2, JWT & SCOPE VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_verification())
