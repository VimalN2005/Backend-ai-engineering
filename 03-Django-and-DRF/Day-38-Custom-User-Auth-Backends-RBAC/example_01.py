"""
Day 38: Custom User Model, Authentication Backends & RBAC Permissions
Example 01: Custom User Model Architecture, PBKDF2 Password Hashing & Composable Auth Backends

Architecture Overview:
----------------------
This module delivers a pure Python, zero-external-dependency architectural implementation of
Django's user identity, password hashing, and authentication backend subsystem. It models:
  1. `AbstractBaseUser` + `PermissionsMixin`: UUID-based custom user model with email as primary key.
  2. `CustomUserManager`: Email normalization and superuser invariant enforcement.
  3. `PBKDF2PasswordHasher`: Cryptographic password hashing, salt management, and constant-time checking.
  4. Automatic Password Upgrader: Transparent migration of legacy hash iterations on successful login.
  5. Composable Authentication Backends:
     - `MultiIdentifierAuthBackend` (Email or Phone Number)
     - `ApiKeyAuthBackend` (Machine-to-Machine API tokens)
     - `TenantScopedAuthBackend` (Multi-tenant enterprise isolation)

WHY THIS ARCHITECTURE?
----------------------
Enterprise systems cannot rely on legacy username-based models. Implementing email-based identity
with composable backends allows single-sign-on (SSO), machine API keys, and multi-tenant isolation
to co-exist cleanly without polluting the view layer or coupling models to specific login protocols.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. Password Hashing Engine & Security Utilities
# ============================================================================

def constant_time_compare(val1: str, val2: str) -> bool:
    """
    Constant-time string comparison mitigating side-channel timing attacks.
    WHY: Standard '==' comparisons short-circuit on the first mismatched byte,
    allowing an attacker to deduce secret bytes by measuring nanosecond latency differences.
    """
    return hmac.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))


class PBKDF2PasswordHasher:
    """
    Simulates django.contrib.auth.hashers.PBKDF2PasswordHasher.
    Uses PBKDF2-HMAC-SHA256 with cryptographically secure random salts.
    """
    algorithm = "pbkdf2_sha256"
    default_iterations = 100_000  # Tuned for test performance (production Django uses 870,000+)

    @classmethod
    def encode(cls, password: str, salt: Optional[str] = None, iterations: Optional[int] = None) -> str:
        """
        Encodes a plaintext password into standard Django hash format:
        <algorithm>$<iterations>$<salt>$<hash>
        """
        if salt is None:
            # 16 bytes of cryptographically secure random salt encoded in base64
            salt = secrets.token_hex(8)
        if iterations is None:
            iterations = cls.default_iterations

        # Derive key via PBKDF2
        derived_key = hashlib.pbkdf2_hmac(
            hash_name="sha256",
            password=password.encode("utf-8"),
            salt=salt.encode("utf-8"),
            iterations=iterations,
            dklen=32,
        )
        hash_b64 = base64.b64encode(derived_key).decode("ascii").strip()
        return f"{cls.algorithm}${iterations}${salt}${hash_b64}"

    @classmethod
    def verify(cls, raw_password: str, encoded: str) -> bool:
        """
        Verifies plaintext password against stored hash string.
        """
        try:
            algorithm, iterations_str, salt, stored_hash = encoded.split("$", 3)
        except ValueError:
            return False

        if algorithm != cls.algorithm:
            return False

        iterations = int(iterations_str)
        # Recompute hash with identical salt and iterations
        recomputed = cls.encode(raw_password, salt=salt, iterations=iterations)
        return constant_time_compare(recomputed, encoded)

    @classmethod
    def must_update(cls, encoded: str) -> bool:
        """
        Checks if the stored hash was generated with fewer iterations than current standard.
        WHY: Allows automatic, transparent re-hashing upon user login without forcing password resets.
        """
        try:
            _, iterations_str, _, _ = encoded.split("$", 3)
            return int(iterations_str) < cls.default_iterations
        except ValueError:
            return True


# ============================================================================
# 2. Custom User Model & Manager Simulation
# ============================================================================

class CustomUserManager:
    """
    Simulates django.contrib.auth.models.BaseUserManager.
    Enforces email normalization and superuser creation invariants.
    """

    def __init__(self, user_model: type) -> None:
        self.model = user_model
        # In-memory storage table simulating database
        self._db: Dict[uuid.UUID, Any] = {}

    @staticmethod
    def normalize_email(email: str) -> str:
        """
        Normalizes email address: lowercases the domain component (and entire string
        for enterprise case-insensitive login systems).
        """
        email = email.strip()
        if "@" in email:
            local_part, domain = email.rsplit("@", 1)
            return f"{local_part}@{domain.lower()}".lower()
        return email.lower()

    def create_user(
        self,
        email: str,
        password: Optional[str] = None,
        phone_number: Optional[str] = None,
        first_name: str = "",
        last_name: str = "",
        tenant_id: str = "default",
        **extra_fields: Any,
    ) -> Any:
        """
        Creates and saves a standard User with the given email and password.
        """
        if not email:
            raise ValueError("The Email field must be set.")

        normalized_email = self.normalize_email(email)

        # Check uniqueness constraint
        for existing_user in self._db.values():
            if existing_user.email == normalized_email:
                raise ValueError("A user with that email already exists.")
            if phone_number and existing_user.phone_number == phone_number:
                raise ValueError("A user with that phone number already exists.")

        user = self.model(
            email=normalized_email,
            phone_number=phone_number,
            first_name=first_name,
            last_name=last_name,
            tenant_id=tenant_id,
            **extra_fields,
        )

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        self._db[user.id] = user
        return user

    def create_superuser(self, email: str, password: Optional[str] = None, **extra_fields: Any) -> Any:
        """
        Creates and saves a Superuser.
        WHY: Guarantees that superusers must possess administrative privileges.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class User:
    """
    Enterprise Custom User Model.
    Combines AbstractBaseUser and PermissionsMixin abstractions.
    """
    USERNAME_FIELD = "email"

    def __init__(
        self,
        email: str,
        phone_number: Optional[str] = None,
        first_name: str = "",
        last_name: str = "",
        tenant_id: str = "default",
        is_active: bool = True,
        is_staff: bool = False,
        is_superuser: bool = False,
    ) -> None:
        self.id: uuid.UUID = uuid.uuid4()
        self.email: str = email
        self.phone_number: Optional[str] = phone_number
        self.first_name: str = first_name
        self.last_name: str = last_name
        self.tenant_id: str = tenant_id
        self.is_active: bool = is_active
        self.is_staff: bool = is_staff
        self.is_superuser: bool = is_superuser
        self.date_joined: float = time.time()
        self.password: str = ""
        self.failed_login_count: int = 0

        # RBAC permissions and group memberships
        self.groups: Set[str] = set()
        self.user_permissions: Set[str] = set()
        self._perm_cache: Optional[Set[str]] = None

    def set_password(self, raw_password: str) -> None:
        """Cryptographically hashes and stores the password."""
        self.password = PBKDF2PasswordHasher.encode(raw_password)

    def set_unusable_password(self) -> None:
        """Sets an invalid hash so password login is permanently disabled (for SSO/OAuth users)."""
        self.password = "!"

    def has_usable_password(self) -> bool:
        return self.password != "" and not self.password.startswith("!")

    def check_password(self, raw_password: str) -> bool:
        """Verifies raw password against the stored cryptographic hash."""
        if not self.has_usable_password():
            return False
        return PBKDF2PasswordHasher.verify(raw_password, self.password)

    def get_all_permissions(self) -> Set[str]:
        """
        Returns combined direct and group permissions.
        WHY: Caches permissions on the instance to avoid repeating database queries
        during multiple permission checks within a single request.
        """
        if self._perm_cache is None:
            perms = set(self.user_permissions)
            # In Django, group permissions are unioned here:
            # for group in self.groups: perms.update(group.permissions)
            self._perm_cache = perms
        return self._perm_cache

    def has_perm(self, perm: str) -> bool:
        """Superusers automatically have all permissions; others check cache."""
        if not self.is_active:
            return False
        if self.is_superuser:
            return True
        return perm in self.get_all_permissions()

    def __repr__(self) -> str:
        return f"<User id={self.id} email='{self.email}' is_active={self.is_active} staff={self.is_staff}>"


# ============================================================================
# 3. Composable Authentication Backends
# ============================================================================

class MultiIdentifierAuthBackend:
    """
    Authenticates users via either Email or Phone Number.

    WHY MULTI-IDENTIFIER?
    Consumer mobile apps and modern SaaS platforms often permit users to sign in
    with either their registered telephone number or email address seamlessly.
    """

    def __init__(self, manager: CustomUserManager) -> None:
        self.manager = manager

    def authenticate(
        self,
        request: Any,
        identifier: Optional[str] = None,
        password: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[User]:
        if not identifier or not password:
            return None

        clean_id = identifier.strip().lower()

        # Search across email and phone number
        matched_user: Optional[User] = None
        for u in self.manager._db.values():
            if u.email == clean_id or (u.phone_number and u.phone_number == identifier.strip()):
                matched_user = u
                break

        if matched_user is None:
            # TIMING ATTACK MITIGATION:
            # Run PBKDF2 calculation even when the user does not exist!
            # WHY: If missing users return in 0.05ms but existing users return in 20ms,
            # an attacker can enumerate all valid registered email addresses.
            PBKDF2PasswordHasher.encode("dummy-password")
            return None

        # Check password and active status
        if matched_user.is_active and matched_user.check_password(password):
            # Check if password hash needs upgrade
            if PBKDF2PasswordHasher.must_update(matched_user.password):
                matched_user.set_password(password)

            matched_user.failed_login_count = 0
            return matched_user

        matched_user.failed_login_count += 1
        return None


class ApiKeyAuthBackend:
    """
    Authenticates machine-to-machine requests bearing an API Key token.
    """

    def __init__(self, api_keys_table: Dict[str, uuid.UUID], manager: CustomUserManager) -> None:
        # Maps hashed_api_key -> user_id
        self.api_keys_table = api_keys_table
        self.manager = manager

    def authenticate(
        self,
        request: Any,
        api_key: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[User]:
        if not api_key:
            return None

        hashed_key = hashlib.sha256(api_key.encode("utf-8")).hexdigest()

        # Constant-time comparison across stored keys
        matched_user_id: Optional[uuid.UUID] = None
        for stored_hash, uid in self.api_keys_table.items():
            if constant_time_compare(stored_hash, hashed_key):
                matched_user_id = uid
                break

        if matched_user_id and matched_user_id in self.manager._db:
            user = self.manager._db[matched_user_id]
            if user.is_active:
                return user

        return None


class TenantScopedAuthBackend:
    """
    Enforces multi-tenant isolation during authentication.
    Requires matching `tenant_id`, `email`, and `password`.
    """

    def __init__(self, manager: CustomUserManager) -> None:
        self.manager = manager

    def authenticate(
        self,
        request: Any,
        tenant_id: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[User]:
        if not tenant_id or not email or not password:
            return None

        clean_email = self.manager.normalize_email(email)

        for u in self.manager._db.values():
            if u.tenant_id == tenant_id and u.email == clean_email:
                if u.is_active and u.check_password(password):
                    return u

        return None


class AuthenticationRouter:
    """
    Simulates django.contrib.auth.authenticate.
    Iterates through configured backends until one returns a valid User.
    """

    def __init__(self, backends: List[Any]) -> None:
        self.backends = backends

    def authenticate(self, request: Any = None, **credentials: Any) -> Optional[User]:
        for backend in self.backends:
            user = backend.authenticate(request, **credentials)
            if user is not None:
                # First matching backend wins; return immediately
                return user
        return None


# ============================================================================
# 4. Comprehensive Architectural Verification Suite
# ============================================================================

def run_tests() -> None:
    print("======================================================================")
    print(" Day 38: Custom User Model & Authentication Backends Engine")
    print("======================================================================\n")

    manager = CustomUserManager(User)

    # 1. Test User Creation and Normalization
    print("[TEST 1] Email Normalization and User Creation...")
    user_alice = manager.create_user(
        email=" Alice.Smith@CORP.Acme.com ",
        password="AliceSecretPassword123!",
        phone_number="+1-555-0199",
        first_name="Alice",
        last_name="Smith",
        tenant_id="tenant_acme",
    )
    assert user_alice.email == "alice.smith@corp.acme.com", f"Failed normalization: {user_alice.email}"
    assert user_alice.check_password("AliceSecretPassword123!") is True
    assert user_alice.check_password("WrongPassword") is False
    assert user_alice.is_staff is False
    assert user_alice.is_superuser is False
    print(f"  -> Created User: {user_alice}")
    print("  -> Email normalization and PBKDF2 hash verification passed.\n")

    # 2. Test Superuser Invariants
    print("[TEST 2] Superuser Creation Invariant Enforcement...")
    admin_user = manager.create_superuser(
        email="admin@acme.com",
        password="SuperAdminPassword!",
        first_name="Root",
        last_name="Admin",
    )
    assert admin_user.is_staff is True
    assert admin_user.is_superuser is True
    assert admin_user.has_perm("any.permission.whatsoever") is True

    try:
        # FLAW: attempting to create a superuser with is_staff=False
        manager.create_superuser("badadmin@acme.com", "pass", is_staff=False)
        assert False, "Should have raised ValueError for is_staff=False"
    except ValueError as err:
        print(f"  -> Caught expected superuser validation error: {err}")
    print("  -> Superuser creation invariants passed.\n")

    # 3. Test Multi-Identifier Authentication (Email vs Phone)
    print("[TEST 3] Multi-Identifier Auth Backend (Email / Phone)...")
    multi_backend = MultiIdentifierAuthBackend(manager)

    # Login by Email
    auth_by_email = multi_backend.authenticate(None, identifier="ALICE.SMITH@corp.acme.com", password="AliceSecretPassword123!")
    assert auth_by_email is not None and auth_by_email.id == user_alice.id
    print("  -> Login via case-insensitive email succeeded.")

    # Login by Phone
    auth_by_phone = multi_backend.authenticate(None, identifier="+1-555-0199", password="AliceSecretPassword123!")
    assert auth_by_phone is not None and auth_by_phone.id == user_alice.id
    print("  -> Login via phone number succeeded.")

    # Failed login attempts increment
    failed_attempt = multi_backend.authenticate(None, identifier="alice.smith@corp.acme.com", password="BadPassword")
    assert failed_attempt is None
    assert user_alice.failed_login_count == 1
    print("  -> Bad password rejected; failed_login_count incremented to 1.\n")

    # 4. Test Automatic Password Hash Upgrading (must_update)
    print("[TEST 4] Transparent Password Re-Hashing on Login (must_update)...")
    # Simulate a legacy hash created with only 500 iterations
    legacy_hash = PBKDF2PasswordHasher.encode("AliceSecretPassword123!", iterations=500)
    assert PBKDF2PasswordHasher.must_update(legacy_hash) is True
    user_alice.password = legacy_hash

    # User logs in with valid credentials
    authenticated = multi_backend.authenticate(None, identifier="alice.smith@corp.acme.com", password="AliceSecretPassword123!")
    assert authenticated is not None
    # Verify the password was automatically re-hashed with full default iterations
    assert PBKDF2PasswordHasher.must_update(user_alice.password) is False
    print(f"  -> Password successfully upgraded in-place to {PBKDF2PasswordHasher.default_iterations:,} iterations!\n")

    # 5. Test API Key Backend & Backend Router Chain
    print("[TEST 5] Composable Backend Chain & API Key Authentication...")
    raw_api_key = "ak_live_" + secrets.token_urlsafe(24)
    hashed_key = hashlib.sha256(raw_api_key.encode("utf-8")).hexdigest()
    api_key_table = {hashed_key: user_alice.id}

    api_backend = ApiKeyAuthBackend(api_key_table, manager)
    tenant_backend = TenantScopedAuthBackend(manager)

    # Configure Router with 3 backends:
    # 1. ApiKeyAuthBackend
    # 2. MultiIdentifierAuthBackend
    # 3. TenantScopedAuthBackend
    router = AuthenticationRouter([api_backend, multi_backend, tenant_backend])

    # Case A: Authenticate with API Key
    res_api = router.authenticate(api_key=raw_api_key)
    assert res_api is not None and res_api.id == user_alice.id
    print("  -> Router authenticated machine via API Key.")

    # Case B: Authenticate via Multi-Tenant Backend
    res_tenant = router.authenticate(tenant_id="tenant_acme", email="alice.smith@corp.acme.com", password="AliceSecretPassword123!")
    assert res_tenant is not None and res_tenant.id == user_alice.id
    print("  -> Router authenticated tenant-scoped user.")

    # Case C: Cross-Tenant Breach Attempt (Alice trying to log into tenant_beta)
    res_breach = router.authenticate(tenant_id="tenant_beta", email="alice.smith@corp.acme.com", password="AliceSecretPassword123!")
    assert res_breach is None
    print("  -> Router correctly rejected cross-tenant login attempt.")

    print("\nAll Day 38 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
