"""
Day 41: DRF Authentication: JWT (SimpleJWT), Refresh Tokens & Blacklisting
Example 01: Cryptographic JWT Engine from First Principles & DRF Authentication Backend

Architecture Overview:
----------------------
This module implements a pure Python, zero-external-dependency cryptographic engine
modeling RFC 7519 JSON Web Tokens (JWT) and DRF's authentication pipeline:
  1. Base64URL Encoding & Decoding (RFC 7515): Handling padding stripping and character translation.
  2. HMAC-SHA256 Cryptographic Signing: Constant-time signature generation and verification.
  3. Strict Algorithm & Claims Verification:
     - Defends against 'alg: none' and algorithm confusion exploits.
     - Validates expiration (`exp`), not-before (`nbf`), and issued-at (`iat`) timestamps.
  4. Dual-Token Architecture: Minting short-lived Access Tokens and long-lived Refresh Tokens.
  5. DRF Authentication Backend Simulation: `BaseAuthentication` implementing `authenticate()`.

WHY THIS ARCHITECTURE?
----------------------
Stateless JWT verification allows API gateways and Django backends to authenticate millions
of requests per second without touching database connection pools. Building the cryptographic
primitives from scratch demystifies the 3-part token structure and clarifies how DRF's
SimpleJWT plugin establishes secure user context.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# 1. Base64URL Encoding & Decoding (RFC 7515)
# ============================================================================

def base64url_encode(data: bytes) -> str:
    """
    Encodes bytes to a Base64URL string without padding (RFC 7515 Section 2).
    WHY: Standard Base64 uses '+', '/', and '=' which require URL-encoding when
    passed in query parameters or HTTP headers. Base64URL replaces '+' with '-',
    '/' with '_', and strips trailing '=' padding characters.
    """
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def base64url_decode(encoded_str: str) -> bytes:
    """
    Decodes a Base64URL string, automatically restoring stripped '=' padding.
    """
    # Restore standard padding (Base64 requires length divisible by 4)
    remainder = len(encoded_str) % 4
    if remainder > 0:
        encoded_str += "=" * (4 - remainder)
    return base64.urlsafe_b64decode(encoded_str.encode("utf-8"))


# ============================================================================
# 2. Cryptographic JWT Engine (RFC 7519)
# ============================================================================

class InvalidTokenError(Exception):
    """Base exception for invalid, tampered, or malformed tokens."""
    pass


class TokenExpiredError(InvalidTokenError):
    """Raised when current time exceeds the 'exp' timestamp."""
    pass


class JWTEngine:
    """
    Pure Python cryptographic JWT encoder, decoder, and signature verifier.
    """
    SUPPORTED_ALGORITHMS = {"HS256"}

    @classmethod
    def encode(cls, payload: Dict[str, Any], secret_key: str, algorithm: str = "HS256") -> str:
        """
        Mints a signed JWT: Base64URL(Header) . Base64URL(Payload) . Base64URL(Signature)
        """
        if algorithm not in cls.SUPPORTED_ALGORITHMS:
            raise ValueError(f"Unsupported algorithm '{algorithm}'. Allowed: {cls.SUPPORTED_ALGORITHMS}")

        header = {"alg": algorithm, "typ": "JWT"}

        # Step 1: Serialize and Base64URL encode Header & Payload
        header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

        encoded_header = base64url_encode(header_bytes)
        encoded_payload = base64url_encode(payload_bytes)

        # Step 2: Construct signing input
        signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")

        # Step 3: Compute HMAC-SHA256 signature
        signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        encoded_signature = base64url_encode(signature)

        return f"{encoded_header}.{encoded_payload}.{encoded_signature}"

    @classmethod
    def decode(
        cls,
        token_str: str,
        secret_key: str,
        expected_algorithm: str = "HS256",
        leeway_seconds: int = 0,
    ) -> Dict[str, Any]:
        """
        Cryptographically verifies and decodes a signed JWT string.
        """
        parts = token_str.split(".")
        if len(parts) != 3:
            raise InvalidTokenError("Token must contain exactly three segments separated by periods.")

        encoded_header, encoded_payload, encoded_signature = parts

        # Step 1: Decode Header & enforce algorithm whitelist
        try:
            header_data = json.loads(base64url_decode(encoded_header).decode("utf-8"))
        except Exception as exc:
            raise InvalidTokenError(f"Malformed token header: {exc}")

        alg = header_data.get("alg")
        # SECURITY INVARIANT: Defend against 'alg: none' and algorithm confusion!
        if not alg or alg != expected_algorithm:
            raise InvalidTokenError(f"Algorithm mismatch: expected '{expected_algorithm}', received '{alg}'.")

        # Step 2: Verify Cryptographic Signature in Constant Time
        signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
        expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()

        try:
            actual_sig = base64url_decode(encoded_signature)
        except Exception:
            raise InvalidTokenError("Malformed token signature encoding.")

        # Constant-time comparison mitigates side-channel timing attacks
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise InvalidTokenError("Cryptographic signature verification failed (tampered token).")

        # Step 3: Decode Payload
        try:
            payload = json.loads(base64url_decode(encoded_payload).decode("utf-8"))
        except Exception as exc:
            raise InvalidTokenError(f"Malformed token payload: {exc}")

        # Step 4: Verify Standard Registered Claims (exp, nbf)
        current_time = time.time()

        if "exp" in payload:
            exp_timestamp = float(payload["exp"])
            if current_time > (exp_timestamp + leeway_seconds):
                raise TokenExpiredError(
                    f"Token has expired at {exp_timestamp} (current time: {current_time:.0f})."
                )

        if "nbf" in payload:
            nbf_timestamp = float(payload["nbf"])
            if current_time < (nbf_timestamp - leeway_seconds):
                raise InvalidTokenError(f"Token is not active yet (nbf: {nbf_timestamp}).")

        return payload


# ============================================================================
# 3. Dual-Token Minting & Refresh Utilities
# ============================================================================

def mint_token_pair(
    user_id: str,
    tenant_id: str,
    role: str,
    secret_key: str,
    access_lifetime_seconds: int = 900,     # 15 minutes
    refresh_lifetime_seconds: int = 604800, # 7 days
) -> Dict[str, str]:
    """
    Mints a coordinated Dual-Token Pair (Access Token + Refresh Token).
    WHY: Access token is short-lived for security; Refresh token is long-lived
    with a unique JTI for rotation and blacklisting.
    """
    now = int(time.time())

    # 1. Access Token Payload (Stateless, short-lived)
    access_payload = {
        "token_type": "access",
        "jti": uuid.uuid4().hex,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + access_lifetime_seconds,
    }

    # 2. Refresh Token Payload (Long-lived, contains JTI for revocation tracking)
    refresh_payload = {
        "token_type": "refresh",
        "jti": uuid.uuid4().hex,
        "user_id": user_id,
        "iat": now,
        "exp": now + refresh_lifetime_seconds,
    }

    return {
        "access": JWTEngine.encode(access_payload, secret_key),
        "refresh": JWTEngine.encode(refresh_payload, secret_key),
    }


# ============================================================================
# 4. DRF Authentication Backend Simulation
# ============================================================================

class HttpRequest:
    """Simulates an incoming DRF request with headers."""
    def __init__(self, headers: Optional[Dict[str, str]] = None) -> None:
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class AuthenticatedUser:
    """Lightweight user object populated statelessly from JWT claims."""
    def __init__(self, user_id: str, tenant_id: str, role: str) -> None:
        self.id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.is_authenticated = True

    def __repr__(self) -> str:
        return f"<AuthenticatedUser id={self.id} tenant={self.tenant_id} role={self.role}>"


class StatelessJWTAuthentication:
    """
    Simulates rest_framework_simplejwt.authentication.JWTAuthentication.
    Validates tokens statelessly without querying the database!
    """

    def __init__(self, secret_key: str) -> None:
        self.secret_key = secret_key

    def authenticate(self, request: HttpRequest) -> Optional[Tuple[AuthenticatedUser, Dict[str, Any]]]:
        """
        DRF Protocol Contract:
          - Returns (user, token_payload) tuple if credentials match.
          - Returns None if no Authorization header is present.
          - Raises InvalidTokenError if credentials are provided but invalid.
        """
        auth_header = request.headers.get("authorization")
        if not auth_header:
            return None

        parts = auth_header.split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise InvalidTokenError("Authorization header must follow 'Bearer <token>' format.")

        raw_token = parts[1]

        # 1. Decode and cryptographically verify signature & expiration
        payload = JWTEngine.decode(raw_token, self.secret_key)

        # 2. Validate token type (must be an access token, NOT a refresh token!)
        if payload.get("token_type") != "access":
            raise InvalidTokenError("Cannot authenticate with a refresh token. Provide an access token.")

        # 3. Construct user representation from verified claims
        user = AuthenticatedUser(
            user_id=payload["user_id"],
            tenant_id=payload["tenant_id"],
            role=payload["role"],
        )

        return (user, payload)

    def authenticate_header(self, request: HttpRequest) -> str:
        return "Bearer"


# ============================================================================
# 5. Verification Suite
# ============================================================================

def run_tests() -> None:
    print("======================================================================")
    print(" Day 41: Cryptographic JWT Engine & Stateless DRF Authentication Tests")
    print("======================================================================\n")

    secret_key = "enterprise_super_secret_cryptographic_signing_key_2026"
    auth_backend = StatelessJWTAuthentication(secret_key)

    # 1. Test Base64URL Encoding & Decoding
    print("[TEST 1] Base64URL RFC 7515 Compliance...")
    raw_sample = b"Testing binary \x00\x01\x02 + / =="
    encoded = base64url_encode(raw_sample)
    assert "+" not in encoded and "/" not in encoded and "=" not in encoded
    decoded = base64url_decode(encoded)
    assert decoded == raw_sample
    print("  -> Base64URL round-trip verified (padding stripped, chars converted).")

    # 2. Test Dual-Token Minting & Claims
    print("\n[TEST 2] Dual-Token Minting (Access vs Refresh)...")
    tokens = mint_token_pair(
        user_id="user_alice_101",
        tenant_id="tenant_acme",
        role="admin",
        secret_key=secret_key,
        access_lifetime_seconds=300,
    )
    access_token = tokens["access"]
    refresh_token = tokens["refresh"]

    decoded_access = JWTEngine.decode(access_token, secret_key)
    assert decoded_access["token_type"] == "access"
    assert decoded_access["user_id"] == "user_alice_101"
    assert decoded_access["role"] == "admin"
    print("  -> Access token decoded cleanly with embedded custom claims:", decoded_access)

    decoded_refresh = JWTEngine.decode(refresh_token, secret_key)
    assert decoded_refresh["token_type"] == "refresh"
    assert "jti" in decoded_refresh
    print("  -> Refresh token decoded with unique JTI for rotation tracking:", decoded_refresh["jti"])

    # 3. Test DRF Authentication Backend Execution
    print("\n[TEST 3] DRF StatelessJWTAuthentication Dispatch...")
    valid_request = HttpRequest(headers={"Authorization": f"Bearer {access_token}"})
    auth_result = auth_backend.authenticate(valid_request)
    assert auth_result is not None
    user, token_data = auth_result
    assert user.id == "user_alice_101"
    assert user.tenant_id == "tenant_acme"
    assert user.role == "admin"
    print(f"  -> Authentication successful! Result: {user}")

    # 4. Test Rejection: Attempting to Authenticate with a Refresh Token
    print("\n[TEST 4] Token Type Enforcement (Rejecting Refresh Token on API)...")
    refresh_request = HttpRequest(headers={"Authorization": f"Bearer {refresh_token}"})
    try:
        auth_backend.authenticate(refresh_request)
        assert False, "Should reject refresh tokens used as bearer credentials!"
    except InvalidTokenError as exc:
        print("  -> Caught expected error:", exc)

    # 5. Test Cryptographic Tampering Rejection
    print("\n[TEST 5] Signature Tampering Defense...")
    # Tamper with the payload (e.g. change role from 'member' to 'superadmin')
    parts = access_token.split(".")
    tampered_payload_data = {"token_type": "access", "user_id": "user_alice_101", "role": "superadmin"}
    tampered_encoded_payload = base64url_encode(json.dumps(tampered_payload_data).encode("utf-8"))
    tampered_token = f"{parts[0]}.{tampered_encoded_payload}.{parts[2]}"

    tampered_request = HttpRequest(headers={"Authorization": f"Bearer {tampered_token}"})
    try:
        auth_backend.authenticate(tampered_request)
        assert False, "Tampered signature should fail verification!"
    except InvalidTokenError as exc:
        print("  -> Caught expected tampering rejection:", exc)

    # 6. Test Expiration Validation
    print("\n[TEST 6] Expiration (exp) Verification...")
    expired_payload = {
        "token_type": "access",
        "user_id": "bob",
        "tenant_id": "tenant_beta",
        "role": "guest",
        "exp": time.time() - 100,  # Expired 100 seconds ago!
    }
    expired_token = JWTEngine.encode(expired_payload, secret_key)
    expired_request = HttpRequest(headers={"Authorization": f"Bearer {expired_token}"})
    try:
        auth_backend.authenticate(expired_request)
        assert False, "Expired token should be rejected!"
    except TokenExpiredError as exc:
        print("  -> Caught expected expiration rejection:", exc)

    # 7. Test Defense Against 'alg: none' Exploit
    print("\n[TEST 7] Defense Against 'alg: none' Exploit...")
    none_header = base64url_encode(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
    none_payload = base64url_encode(json.dumps({"user_id": "hacker", "role": "admin"}).encode("utf-8"))
    none_token = f"{none_header}.{none_payload}."  # No signature
    none_request = HttpRequest(headers={"Authorization": f"Bearer {none_token}"})
    try:
        auth_backend.authenticate(none_request)
        assert False, "Tokens with 'alg: none' must be strictly rejected!"
    except InvalidTokenError as exc:
        print("  -> Caught expected algorithm verification rejection:", exc)

    print("\nAll Day 41 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
