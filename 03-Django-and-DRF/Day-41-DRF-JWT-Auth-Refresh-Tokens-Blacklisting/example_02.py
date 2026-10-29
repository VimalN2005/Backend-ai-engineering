"""
Day 41: DRF Authentication: JWT (SimpleJWT), Refresh Tokens & Blacklisting
Example 02: Enterprise Refresh Token Rotation, Blacklisting & Token Family Reuse Detection

Architecture Overview:
----------------------
This module implements an enterprise-grade Token Management & Revocation Engine modeling:
  1. Refresh Token Rotation (`ROTATE_REFRESH_TOKENS = True`):
     - Atomically invalidates the submitted refresh token upon every refresh request.
     - Mints a fresh access token and fresh refresh token pair.
  2. Automatic Token Family Reuse Detection (RFC 6749 Section 10.4):
     - Tracks token lineage using a `family_id` linking parent and rotated descendant tokens.
     - If an already-blacklisted refresh token is re-submitted (indicating token theft),
       the engine detects reuse and immediately revokes the ENTIRE token family!
  3. In-Memory Blacklist Registry (Simulating Redis / PostgreSQL `token_blacklist`):
     - Sub-millisecond `jti` membership testing.
     - Automatic pruning of naturally expired tokens.
  4. Explicit Logout Revocation (`/api/token/blacklist/`):
     - Instant server-side termination of user sessions.
  5. High-Throughput Production Benchmark:
     - Benchmarks 10,000 dual-token rotations, verifications, and blacklist checks.

WHY THIS ARCHITECTURE?
----------------------
Pure stateless JWTs cannot be revoked before their natural expiration date.
The Dual-Token Architecture with Rotation and Reuse Detection provides the golden mean:
high-speed stateless access verification for standard APIs, combined with centralized,
stateful revocation control at the refresh boundary to neutralize token theft immediately.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. Cryptographic Primitives (HS256 & Base64URL)
# ============================================================================

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def b64url_decode(encoded_str: str) -> bytes:
    remainder = len(encoded_str) % 4
    if remainder > 0:
        encoded_str += "=" * (4 - remainder)
    return base64.urlsafe_b64decode(encoded_str.encode("utf-8"))


class JWTUtil:
    @staticmethod
    def encode(payload: Dict[str, Any], secret: str) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        h_str = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        p_str = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        sig = hmac.new(secret.encode("utf-8"), f"{h_str}.{p_str}".encode("utf-8"), hashlib.sha256).digest()
        return f"{h_str}.{p_str}.{b64url_encode(sig)}"

    @staticmethod
    def decode(token: str, secret: str) -> Dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format.")
        h_str, p_str, sig_str = parts
        sig = hmac.new(secret.encode("utf-8"), f"{h_str}.{p_str}".encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(b64url_encode(sig), sig_str):
            raise ValueError("Signature mismatch.")
        payload = json.loads(b64url_decode(p_str).decode("utf-8"))
        if payload.get("exp", 0) < time.time():
            raise TimeoutError("Token expired.")
        return payload


# ============================================================================
# 2. Blacklist & Outstanding Token Entities
# ============================================================================

@dataclass
class OutstandingTokenRecord:
    id: str
    user_id: str
    jti: str
    family_id: str
    expires_at: float
    is_blacklisted: bool = False


class TokenBlacklistRegistry:
    """
    Simulates Django's rest_framework_simplejwt.token_blacklist database tables
    and high-performance Redis cache layer.
    """

    def __init__(self) -> None:
        # Maps jti -> OutstandingTokenRecord
        self.outstanding: Dict[str, OutstandingTokenRecord] = {}
        # Set of blacklisted JTIs for sub-microsecond SISMEMBER testing
        self.blacklisted_jtis: Set[str] = set()
        # Maps family_id -> List[jti] (for cascading family revocation)
        self.families: Dict[str, List[str]] = {}

    def register_outstanding(self, user_id: str, jti: str, family_id: str, expires_at: float) -> None:
        """Registers a freshly minted refresh token."""
        rec = OutstandingTokenRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            jti=jti,
            family_id=family_id,
            expires_at=expires_at,
            is_blacklisted=False,
        )
        self.outstanding[jti] = rec
        self.families.setdefault(family_id, []).append(jti)

    def is_blacklisted(self, jti: str) -> bool:
        """Checks if a token JTI has been revoked."""
        return jti in self.blacklisted_jtis

    def blacklist_jti(self, jti: str) -> bool:
        """Blacklists a single token JTI."""
        if jti in self.outstanding:
            self.outstanding[jti].is_blacklisted = True
        self.blacklisted_jtis.add(jti)
        return True

    def revoke_family(self, family_id: str) -> int:
        """
        SECURITY INVARIANT (REUSE DETECTION):
        Revokes an ENTIRE token family when an already-blacklisted token is reused!
        """
        revoked_count = 0
        all_family_jtis = self.families.get(family_id, [])
        for jti in all_family_jtis:
            self.blacklist_jti(jti)
            revoked_count += 1
        return revoked_count

    def prune_expired(self) -> int:
        """
        Simulates DRF's `flushexpiredtokens` maintenance command.
        Removes naturally expired tokens from memory to prevent memory bloat.
        """
        now = time.time()
        expired_jtis = [jti for jti, rec in self.outstanding.items() if rec.expires_at < now]
        for jti in expired_jtis:
            del self.outstanding[jti]
            self.blacklisted_jtis.discard(jti)
        return len(expired_jtis)


# ============================================================================
# 3. Enterprise Token Service
# ============================================================================

class EnterpriseTokenService:
    """
    Central authentication and token lifecycle coordinator.
    """

    def __init__(self, secret_key: str, blacklist_registry: TokenBlacklistRegistry) -> None:
        self.secret_key = secret_key
        self.blacklist = blacklist_registry
        self.access_lifetime = 900       # 15 minutes
        self.refresh_lifetime = 604800   # 7 days

    def create_initial_session(self, user_id: str, tenant_id: str, role: str) -> Dict[str, str]:
        """
        Invoked on initial login (POST /api/token/).
        Creates a new Token Family.
        """
        family_id = str(uuid.uuid4())
        return self._mint_pair(user_id, tenant_id, role, family_id)

    def refresh_session(self, raw_refresh_token: str) -> Dict[str, str]:
        """
        Invoked on silent session refresh (POST /api/token/refresh/).
        Implements Token Rotation and Automatic Reuse Detection.
        """
        # Step 1: Cryptographically verify token
        try:
            payload = JWTUtil.decode(raw_refresh_token, self.secret_key)
        except TimeoutError:
            raise PermissionError("Refresh token has expired. Please log in again.")
        except Exception:
            raise PermissionError("Invalid refresh token signature.")

        if payload.get("token_type") != "refresh":
            raise PermissionError("Provided token is not a refresh token.")

        jti = payload["jti"]
        family_id = payload["family_id"]
        user_id = payload["user_id"]
        tenant_id = payload["tenant_id"]
        role = payload["role"]

        # Step 2: CHECK FOR REUSE ATTACK!
        # If this refresh token's JTI is already in the blacklist, an adversary has
        # intercepted a previously rotated token!
        if self.blacklist.is_blacklisted(jti):
            # REUSE DETECTED: Immediately invalidate the entire family!
            revoked_count = self.blacklist.revoke_family(family_id)
            raise PermissionError(
                f"SECURITY ALERT: Refresh token reuse detected! Revoked all {revoked_count} tokens in family."
            )

        # Step 3: TOKEN ROTATION
        # Invalidate the currently submitted refresh token immediately
        self.blacklist.blacklist_jti(jti)

        # Step 4: Mint brand new access token AND brand new refresh token in same family
        return self._mint_pair(user_id, tenant_id, role, family_id)

    def logout_session(self, raw_refresh_token: str) -> None:
        """
        Invoked on explicit logout (POST /api/token/blacklist/).
        """
        try:
            payload = JWTUtil.decode(raw_refresh_token, self.secret_key)
            jti = payload.get("jti")
            if jti:
                self.blacklist.blacklist_jti(jti)
        except Exception:
            pass  # Token already invalid or malformed

    def _mint_pair(self, user_id: str, tenant_id: str, role: str, family_id: str) -> Dict[str, str]:
        now = int(time.time())

        # Access Token
        access_payload = {
            "token_type": "access",
            "jti": uuid.uuid4().hex,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "iat": now,
            "exp": now + self.access_lifetime,
        }
        access_token = JWTUtil.encode(access_payload, self.secret_key)

        # Refresh Token
        refresh_jti = uuid.uuid4().hex
        refresh_exp = now + self.refresh_lifetime
        refresh_payload = {
            "token_type": "refresh",
            "jti": refresh_jti,
            "family_id": family_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "iat": now,
            "exp": refresh_exp,
        }
        refresh_token = JWTUtil.encode(refresh_payload, self.secret_key)

        # Record outstanding refresh token
        self.blacklist.register_outstanding(user_id, refresh_jti, family_id, refresh_exp)

        return {"access": access_token, "refresh": refresh_token}


# ============================================================================
# 4. Verification & Performance Benchmark
# ============================================================================

def run_rotation_verification() -> None:
    print("[STAGE 1] Enterprise Token Rotation & Reuse Detection Verification")
    print("-" * 65)

    secret = "enterprise_jwt_production_secret_key_2026"
    registry = TokenBlacklistRegistry()
    service = EnterpriseTokenService(secret, registry)

    # 1. Initial Login: Mint Token Pair A
    session_a = service.create_initial_session(user_id="user_101", tenant_id="tenant_acme", role="editor")
    refresh_a = session_a["refresh"]
    jti_a = JWTUtil.decode(refresh_a, secret)["jti"]
    print("  [PASS] Initial Login: Session created. Refresh Token A JTI:", jti_a[:8])

    # 2. Legitimate User Rotates Token A -> Receives Token B
    session_b = service.refresh_session(refresh_a)
    refresh_b = session_b["refresh"]
    jti_b = JWTUtil.decode(refresh_b, secret)["jti"]
    assert registry.is_blacklisted(jti_a) is True, "Token A must be blacklisted after rotation!"
    assert registry.is_blacklisted(jti_b) is False, "Token B must be active!"
    print(f"  [PASS] Token Rotation: Token A ({jti_a[:8]}) blacklisted. Minted Token B ({jti_b[:8]}).")

    # 3. Legitimate User Rotates Token B -> Receives Token C
    session_c = service.refresh_session(refresh_b)
    refresh_c = session_c["refresh"]
    jti_c = JWTUtil.decode(refresh_c, secret)["jti"]
    assert registry.is_blacklisted(jti_b) is True
    assert registry.is_blacklisted(jti_c) is False
    print(f"  [PASS] Token Rotation: Token B ({jti_b[:8]}) blacklisted. Minted Token C ({jti_c[:8]}).")

    # 4. Adversary Intercepts and Replays Old Token A (REUSE DETECTION TEST)
    # The attacker tries to use Token A (which was already consumed in Step 2)!
    print("  [ATTACK SIMULATION] Adversary submits already-blacklisted Token A...")
    try:
        service.refresh_session(refresh_a)
        assert False, "Replay of blacklisted token MUST trigger security alert!"
    except PermissionError as exc:
        print("  ->", exc)

    # Verify that the ENTIRE Token Family (including the legitimate user's Token C!) was revoked!
    assert registry.is_blacklisted(jti_c) is True, "Token C must be revoked due to family invalidation!"
    print("  [PASS] Token Family Invalidation: Legitimate Token C was automatically revoked to lock out attacker.")

    # 5. Legitimate User Tries to use Token C now -> REJECTED
    try:
        service.refresh_session(refresh_c)
        assert False, "Token C should be rejected after family revocation!"
    except PermissionError:
        print("  [PASS] Session Lockdown: Compromised session completely locked out.\n")


def run_jwt_benchmark(iterations: int = 10_000) -> None:
    print(f"[STAGE 2] High-Throughput JWT Rotation & Verification Benchmark ({iterations:,} iterations)")
    print("-" * 65)

    secret = "benchmark_jwt_secret_key"
    registry = TokenBlacklistRegistry()
    service = EnterpriseTokenService(secret, registry)

    # Initial session
    current_tokens = service.create_initial_session("bench_user", "tenant_bench", "member")

    latencies_us: List[float] = []
    start_total = time.perf_counter()

    for i in range(iterations):
        t0 = time.perf_counter()
        
        # Simulate: 1. Verify access token statelessly
        access_payload = JWTUtil.decode(current_tokens["access"], secret)
        
        # Simulate: 2. Periodic refresh (every 5 requests)
        if i % 5 == 0:
            current_tokens = service.refresh_session(current_tokens["refresh"])

        t1 = time.perf_counter()
        latencies_us.append((t1 - t0) * 1_000_000.0)

    total_time_sec = time.perf_counter() - start_total
    throughput = iterations / total_time_sec

    latencies_us.sort()
    p50 = latencies_us[int(iterations * 0.50)]
    p95 = latencies_us[int(iterations * 0.95)]
    p99 = latencies_us[int(iterations * 0.99)]

    print(f"Total Operations    : {iterations:,} JWT verify + rotation ops")
    print(f"Wall Clock Time     : {total_time_sec:.4f} seconds")
    print(f"Throughput          : {throughput:,.1f} operations/sec")
    print(f"Latency p50 (median): {p50:.2f} microseconds (us)")
    print(f"Latency p95         : {p95:.2f} microseconds (us)")
    print(f"Latency p99         : {p99:.2f} microseconds (us)")
    print("=" * 65)


if __name__ == "__main__":
    print("======================================================================")
    print(" Day 41: Enterprise Refresh Token Rotation & Blacklist Benchmark")
    print("======================================================================\n")
    run_rotation_verification()
    run_jwt_benchmark(10_000)
