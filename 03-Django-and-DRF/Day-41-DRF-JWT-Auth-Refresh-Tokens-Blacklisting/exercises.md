# Practical JWT, Token Rotation & Blacklisting Challenges

This document contains 3 enterprise-grade engineering challenges covering cryptographic JWT encoding, refresh token rotation with token family reuse detection, and high-performance token blacklisting. Each challenge includes a real-world scenario, architectural constraints, and an executable reference solution.

---

## Challenge 1: Cryptographic JWT Minting & Claims Verification Engine

### Scenario
An internal security audit at a fintech platform requires replacing third-party dependencies with a zero-dependency, verified JWT signing and verification library. You must implement an RFC 7519 compliant JWT engine from scratch that:
1. Performs Base64URL encoding and decoding without padding characters.
2. Generates and verifies HMAC-SHA256 cryptographic signatures.
3. Strictly validates standard registered claims (`exp`, `nbf`, `iat`).
4. Rejects signature tampering and strictly forbids the `alg: "none"` exploit.

### Requirements & Constraints
- Pure Python standard library only (`base64`, `hashlib`, `hmac`, `json`, `time`).
- Signature comparisons must execute in constant time (`hmac.compare_digest`).
- Expired tokens must raise a specialized `TokenExpiredError`.

### Executable Solution & Verification

```python
import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict


class TokenExpiredError(Exception):
    pass


class InvalidTokenError(Exception):
    pass


def b64url_encode(data: bytes) -> str:
    """Encodes bytes to Base64URL without trailing '=' padding."""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def b64url_decode(encoded: str) -> bytes:
    """Restores padding and decodes Base64URL string."""
    remainder = len(encoded) % 4
    if remainder > 0:
        encoded += "=" * (4 - remainder)
    return base64.urlsafe_b64decode(encoded.encode("utf-8"))


class CryptographicJWTEngine:
    @classmethod
    def encode(cls, payload: Dict[str, Any], secret_key: str) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        h_str = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        p_str = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))

        signing_input = f"{h_str}.{p_str}".encode("utf-8")
        sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{h_str}.{p_str}.{b64url_encode(sig)}"

    @classmethod
    def decode(cls, token: str, secret_key: str) -> Dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidTokenError("JWT must have exactly 3 segments separated by dots.")

        h_str, p_str, sig_str = parts

        # 1. Enforce Algorithm Whitelist
        try:
            header = json.loads(b64url_decode(h_str).decode("utf-8"))
        except Exception:
            raise InvalidTokenError("Malformed header.")

        if header.get("alg") != "HS256":
            raise InvalidTokenError(f"Unsupported algorithm: {header.get('alg')}")

        # 2. Verify Cryptographic Signature in Constant Time
        signing_input = f"{h_str}.{p_str}".encode("utf-8")
        expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()

        if not hmac.compare_digest(b64url_encode(expected_sig), sig_str):
            raise InvalidTokenError("Signature verification failed (tampered token).")

        # 3. Decode Payload & Verify Expiration
        payload = json.loads(b64url_decode(p_str).decode("utf-8"))
        now = time.time()

        if "exp" in payload and now > payload["exp"]:
            raise TokenExpiredError("Token has expired.")
        if "nbf" in payload and now < payload["nbf"]:
            raise InvalidTokenError("Token is not active yet.")

        return payload


# --- Verification Harness ---
if __name__ == "__main__":
    secret = "fintech_ultra_secure_signing_secret_99"

    # 1. Test Valid Token
    valid_payload = {"user_id": 42, "role": "trader", "exp": time.time() + 300}
    token = CryptographicJWTEngine.encode(valid_payload, secret)
    decoded = CryptographicJWTEngine.decode(token, secret)
    assert decoded["user_id"] == 42
    assert decoded["role"] == "trader"

    # 2. Test Expired Token
    expired_payload = {"user_id": 42, "exp": time.time() - 10}
    exp_token = CryptographicJWTEngine.encode(expired_payload, secret)
    try:
        CryptographicJWTEngine.decode(exp_token, secret)
        assert False, "Should raise TokenExpiredError"
    except TokenExpiredError:
        pass

    # 3. Test Signature Tampering
    parts = token.split(".")
    tampered_p = b64url_encode(json.dumps({"user_id": 42, "role": "admin"}).encode("utf-8"))
    tampered_token = f"{parts[0]}.{tampered_p}.{parts[2]}"
    try:
        CryptographicJWTEngine.decode(tampered_token, secret)
        assert False, "Should reject tampered signature"
    except InvalidTokenError:
        pass

    print("[SUCCESS] Challenge 1: Cryptographic JWT Engine verified.")
```

---

## Challenge 2: Enterprise Refresh Token Rotation & Family Reuse Detection

### Scenario
An e-commerce mobile application suffers from potential refresh token interception on unsecured public Wi-Fi networks. To protect user accounts, you must implement RFC 6749 Section 10.4 **Token Rotation with Automatic Reuse Detection**:
1. When a refresh token is presented, issue a new access and refresh token, while blacklisting the submitted refresh token.
2. Link rotated tokens together via a `family_id`.
3. If an adversary submits a refresh token that has **already been rotated and blacklisted**, immediately detect the replay attack and **revoke all tokens in that family**, locking out both the attacker and the victim until full re-authentication.

### Requirements & Constraints
- Pure Python standard library only.
- Maintain a lineage of token generations within each family.
- Verify that reusing an old token revokes the currently active descendant token.

### Executable Solution & Verification

```python
import time
import uuid
from typing import Dict, List, Set, Tuple


class TokenFamilyRotationEngine:
    def __init__(self) -> None:
        # Maps token_str -> (family_id, expires_at)
        self.outstanding: Dict[str, Tuple[str, float]] = {}
        # Set of revoked token strings
        self.blacklisted: Set[str] = set()
        # Maps family_id -> List[token_str]
        self.families: Dict[str, List[str]] = {}

    def issue_initial_family(self, user_id: str) -> str:
        family_id = str(uuid.uuid4())
        initial_token = f"rt_{uuid.uuid4().hex}"
        exp = time.time() + 86400  # 1 day

        self.outstanding[initial_token] = (family_id, exp)
        self.families[family_id] = [initial_token]
        return initial_token

    def rotate_token(self, presented_token: str) -> str:
        """
        Rotates a refresh token with automatic reuse detection.
        """
        # Step 1: Detect REUSE ATTACK!
        if presented_token in self.blacklisted:
            # Look up family ID
            family_info = self.outstanding.get(presented_token)
            if family_info:
                family_id = family_info[0]
                # Invalidate entire family!
                for t in self.families.get(family_id, []):
                    self.blacklisted.add(t)
            raise PermissionError("SECURITY ALERT: Token reuse detected! Revoked entire token family.")

        if presented_token not in self.outstanding:
            raise KeyError("Unknown refresh token.")

        family_id, exp = self.outstanding[presented_token]

        # Step 2: Blacklist the currently submitted token
        self.blacklisted.add(presented_token)

        # Step 3: Mint replacement token in the same family
        new_token = f"rt_{uuid.uuid4().hex}"
        new_exp = time.time() + 86400

        self.outstanding[new_token] = (family_id, new_exp)
        self.families[family_id].append(new_token)
        return new_token


# --- Verification Harness ---
if __name__ == "__main__":
    engine = TokenFamilyRotationEngine()

    # 1. User logs in -> receives Token A
    token_a = engine.issue_initial_family(user_id="user_alice")

    # 2. User rotates Token A -> receives Token B
    token_b = engine.rotate_token(token_a)
    assert token_a in engine.blacklisted
    assert token_b not in engine.blacklisted

    # 3. User rotates Token B -> receives Token C
    token_c = engine.rotate_token(token_b)
    assert token_b in engine.blacklisted
    assert token_c not in engine.blacklisted

    # 4. Attack Simulation: Adversary replays already-blacklisted Token A!
    try:
        engine.rotate_token(token_a)
        assert False, "Should have detected token reuse!"
    except PermissionError as exc:
        assert "Token reuse detected" in str(exc)

    # 5. Verify that Token C (the legitimate active token) was automatically revoked!
    assert token_c in engine.blacklisted, "Active token in family must be revoked on reuse attack!"

    # 6. User attempts to use Token C -> Rejected!
    try:
        engine.rotate_token(token_c)
        assert False, "Token C should be rejected after family revocation"
    except PermissionError:
        pass

    print("[SUCCESS] Challenge 2: Token Rotation & Family Reuse Detection verified.")
```

---

## Challenge 3: In-Memory Token Blacklist Registry with Expiration Pruning

### Scenario
An enterprise API gateway handles 10,000 logouts and token rotations per hour. If blacklisted tokens are stored in memory or database tables without cleanup, memory consumption grows indefinitely. 

You must implement a high-performance in-memory Token Blacklist Registry that:
1. Stores revoked token JTIs alongside their expiration timestamps.
2. Provides $O(1)$ constant-time lookup for `is_blacklisted(jti)`.
3. Implements an automatic `prune_expired()` maintenance method (simulating DRF's `flushexpiredtokens`) that purges records whose natural `exp` timestamp has passed.

### Requirements & Constraints
- Pure Python standard library only.
- Blacklist check must execute in under 1 microsecond.
- Expired tokens must be cleaned up without affecting active unexpired tokens.

### Executable Solution & Verification

```python
import time
from typing import Dict, Set


class TokenBlacklistRegistry:
    def __init__(self) -> None:
        # Maps jti -> expires_at_timestamp
        self._blacklist: Dict[str, float] = {}

    def blacklist(self, jti: str, expires_at: float) -> None:
        """Revokes a token JTI until its expiration timestamp."""
        self._blacklist[jti] = expires_at

    def is_blacklisted(self, jti: str) -> bool:
        """Returns True if the token is currently blacklisted."""
        return jti in self._blacklist

    def prune_expired(self) -> int:
        """
        Removes tokens whose natural expiration has passed.
        Returns the count of purged tokens.
        """
        now = time.time()
        expired = [jti for jti, exp in self._blacklist.items() if exp < now]
        for jti in expired:
            del self._blacklist[jti]
        return len(expired)

    def __len__(self) -> int:
        return len(self._blacklist)


# --- Verification Harness ---
if __name__ == "__main__":
    registry = TokenBlacklistRegistry()

    now = time.time()
    # Token 1: Already expired (expired 50 seconds ago)
    registry.blacklist("jti_expired_01", expires_at=now - 50)
    # Token 2: Already expired (expired 10 seconds ago)
    registry.blacklist("jti_expired_02", expires_at=now - 10)
    # Token 3: Active (expires in 300 seconds)
    registry.blacklist("jti_active_03", expires_at=now + 300)

    assert len(registry) == 3
    assert registry.is_blacklisted("jti_active_03") is True
    assert registry.is_blacklisted("jti_expired_01") is True

    # Run Pruning Maintenance
    purged_count = registry.prune_expired()
    assert purged_count == 2
    assert len(registry) == 1
    assert registry.is_blacklisted("jti_active_03") is True
    assert registry.is_blacklisted("jti_expired_01") is False

    print("[SUCCESS] Challenge 3: In-Memory Blacklist Registry with Pruning verified.")
```
