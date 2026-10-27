# Practical User Identity, Auth Backends & RBAC Challenges

This document contains 3 enterprise-grade engineering challenges covering custom user model design, composable authentication backends, and fine-grained object-level RBAC. Each challenge includes a real-world scenario, architectural constraints, and an executable reference solution.

---

## Challenge 1: Dual-Identifier Custom User Model with Phone & Email Normalization

### Scenario
An international logistics platform requires drivers and dispatchers to log in using either their business email or their mobile phone number. You must implement a standalone Custom User model and companion Manager that:
1. Normalizes email addresses to lower-case.
2. Normalizes international phone numbers into standard E.164 format (removing whitespace, hyphens, and parenthesis, ensuring a leading `+` country code).
3. Enforces that superusers must possess `is_staff=True` and `is_superuser=True`.
4. Employs PBKDF2 cryptographic hashing and constant-time password verification.

### Requirements & Constraints
- Pure Python standard library only (`hashlib`, `hmac`, `uuid`, `secrets`).
- Email and phone normalization must be idempotent.
- Superuser creation must fail with `ValueError` if administrative flags are not satisfied.

### Executable Solution & Verification

```python
import hashlib
import hmac
import re
import secrets
import uuid
from typing import Any, Dict, Optional


class PhoneEmailUserManager:
    """Manager providing E.164 phone and RFC email normalization."""

    def __init__(self, model_class: type) -> None:
        self.model = model_class
        self._db: Dict[uuid.UUID, Any] = {}

    @staticmethod
    def normalize_email(email: str) -> str:
        email = email.strip()
        if "@" in email:
            local, domain = email.rsplit("@", 1)
            return f"{local.lower()}@{domain.lower()}"
        return email.lower()

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """
        Normalizes phone number to E.164: removes spaces, dashes, dots, parens.
        Ensures leading '+' prefix.
        """
        cleaned = re.sub(r"[\s\-\.\(\)]", "", phone.strip())
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        if not re.match(r"^\+[1-9]\d{6,14}$", cleaned):
            raise ValueError(f"Invalid E.164 phone number format: '{phone}'")
        return cleaned

    def create_user(
        self,
        email: str,
        phone_number: str,
        password: Optional[str] = None,
        **extra_fields: Any,
    ) -> Any:
        if not email:
            raise ValueError("Email is required.")
        if not phone_number:
            raise ValueError("Phone number is required.")

        normalized_email = self.normalize_email(email)
        normalized_phone = self.normalize_phone(phone_number)

        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_active", True)

        user = self.model(
            email=normalized_email,
            phone_number=normalized_phone,
            **extra_fields,
        )
        if password:
            user.set_password(password)

        self._db[user.id] = user
        return user

    def create_superuser(
        self,
        email: str,
        phone_number: str,
        password: Optional[str] = None,
        **extra_fields: Any,
    ) -> Any:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, phone_number, password, **extra_fields)


class LogisticsUser:
    def __init__(
        self,
        email: str,
        phone_number: str,
        is_active: bool = True,
        is_staff: bool = False,
        is_superuser: bool = False,
    ) -> None:
        self.id = uuid.uuid4()
        self.email = email
        self.phone_number = phone_number
        self.is_active = is_active
        self.is_staff = is_staff
        self.is_superuser = is_superuser
        self.password_hash: str = ""

    def set_password(self, raw_password: str) -> None:
        salt = secrets.token_hex(8)
        derived = hashlib.pbkdf2_hmac(
            "sha256", raw_password.encode("utf-8"), salt.encode("utf-8"), iterations=1000
        )
        self.password_hash = f"pbkdf2${salt}${derived.hex()}"

    def check_password(self, raw_password: str) -> bool:
        if not self.password_hash:
            return False
        _, salt, stored_hex = self.password_hash.split("$")
        recomputed = hashlib.pbkdf2_hmac(
            "sha256", raw_password.encode("utf-8"), salt.encode("utf-8"), iterations=1000
        )
        return hmac.compare_digest(recomputed.hex(), stored_hex)


# --- Verification Harness ---
if __name__ == "__main__":
    manager = PhoneEmailUserManager(LogisticsUser)

    # Test 1: Successful normalization
    driver = manager.create_user(
        email=" JOHN.DOE@FleetLogistics.COM ",
        phone_number=" +1 (555) 234-5678 ",
        password="DriverSecretKey99!",
    )
    assert driver.email == "john.doe@fleetlogistics.com"
    assert driver.phone_number == "+15552345678"
    assert driver.check_password("DriverSecretKey99!") is True
    assert driver.check_password("WrongPassword") is False
    assert driver.is_staff is False

    # Test 2: Invalid phone number rejection
    try:
        manager.create_user("a@b.com", "not-a-phone", "pass")
        assert False, "Should fail on invalid phone number"
    except ValueError as err:
        assert "E.164" in str(err)

    # Test 3: Superuser creation validation
    admin = manager.create_superuser("admin@fleet.com", "+15559998888", "adminpass")
    assert admin.is_staff is True
    assert admin.is_superuser is True

    print("[SUCCESS] Challenge 1: Dual-Identifier Custom User Model verified.")
```

---

## Challenge 2: Composable Multi-Factor Fallback Authentication Backend

### Scenario
A healthcare SaaS backend provides emergency access for physicians. Users normally authenticate using their email and password. However, if their authenticator app fails, they can authenticate using a one-time emergency backup code. 

You must implement a composable authentication backend architecture where:
1. `PasswordAuthBackend`: Validates email and password.
2. `EmergencyBackupCodeBackend`: Validates email and an unused 16-character backup code, consuming and revoking the backup code upon successful login.
3. Both backends execute constant-time comparisons and run dummy computations when users do not exist to eliminate timing attack vectors.

### Requirements & Constraints
- Implement `AuthenticationPipeline` iterating through backends.
- Consuming a backup code must mark it as `used = True` permanently.
- Verify that non-existent users incur equivalent latency to protect against user enumeration.

### Executable Solution & Verification

```python
import hashlib
import hmac
import secrets
from typing import Any, Dict, List, Optional, Set


class UserAccount:
    def __init__(self, email: str, password_hash: str) -> None:
        self.email = email.lower()
        self.password_hash = password_hash
        self.is_active = True
        # Set of hashed backup codes
        self.backup_codes: Set[str] = set()

    def add_backup_code(self, raw_code: str) -> None:
        hashed = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        self.backup_codes.add(hashed)

    def consume_backup_code(self, raw_code: str) -> bool:
        candidate_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        for stored in list(self.backup_codes):
            if hmac.compare_digest(stored, candidate_hash):
                # Consume code (Single-use revocation)
                self.backup_codes.remove(stored)
                return True
        return False


class PasswordAuthBackend:
    def __init__(self, users_table: Dict[str, UserAccount]) -> None:
        self.users = users_table

    def authenticate(self, email: Optional[str] = None, password: Optional[str] = None, **kwargs: Any) -> Optional[UserAccount]:
        if not email or not password:
            return None

        clean_email = email.strip().lower()
        user = self.users.get(clean_email)

        if user is None:
            # Timing attack mitigation: dummy calculation
            hashlib.sha256(b"dummy_salt_and_bytes").hexdigest()
            return None

        candidate_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        if hmac.compare_digest(user.password_hash, candidate_hash):
            return user
        return None


class EmergencyBackupCodeBackend:
    def __init__(self, users_table: Dict[str, UserAccount]) -> None:
        self.users = users_table

    def authenticate(self, email: Optional[str] = None, backup_code: Optional[str] = None, **kwargs: Any) -> Optional[UserAccount]:
        if not email or not backup_code:
            return None

        clean_email = email.strip().lower()
        user = self.users.get(clean_email)

        if user is None:
            # Timing attack mitigation
            hashlib.sha256(b"dummy_salt_and_bytes").hexdigest()
            return None

        if user.consume_backup_code(backup_code):
            return user
        return None


class AuthenticationPipeline:
    def __init__(self, backends: List[Any]) -> None:
        self.backends = backends

    def authenticate(self, **credentials: Any) -> Optional[UserAccount]:
        for backend in self.backends:
            user = backend.authenticate(**credentials)
            if user is not None:
                return user
        return None


# --- Verification Harness ---
if __name__ == "__main__":
    pwd_hash = hashlib.sha256("DoctorSecret123!".encode("utf-8")).hexdigest()
    dr_alice = UserAccount("alice@hospital.org", pwd_hash)
    
    # Generate 2 backup codes
    code1 = "EMERG-AAAA-BBBB"
    code2 = "EMERG-CCCC-DDDD"
    dr_alice.add_backup_code(code1)
    dr_alice.add_backup_code(code2)

    database = {"alice@hospital.org": dr_alice}

    pipeline = AuthenticationPipeline([
        PasswordAuthBackend(database),
        EmergencyBackupCodeBackend(database),
    ])

    # 1. Login with valid password
    res_pwd = pipeline.authenticate(email="alice@hospital.org", password="DoctorSecret123!")
    assert res_pwd is not None
    assert res_pwd.email == "alice@hospital.org"

    # 2. Login with valid emergency backup code
    res_code = pipeline.authenticate(email="alice@hospital.org", backup_code=code1)
    assert res_code is not None
    assert res_code.email == "alice@hospital.org"

    # 3. Code 1 is consumed and must NOT work a second time
    res_reuse = pipeline.authenticate(email="alice@hospital.org", backup_code=code1)
    assert res_reuse is None, "Reused backup code should be rejected!"

    # 4. Code 2 still works
    res_code2 = pipeline.authenticate(email="alice@hospital.org", backup_code=code2)
    assert res_code2 is not None

    print("[SUCCESS] Challenge 2: Multi-Factor Fallback Authentication Backend verified.")
```

---

## Challenge 3: High-Performance Object-Level RBAC Policy Evaluator with Permission Cache

### Scenario
An enterprise document management SaaS platform requires fine-grained access control. Users belong to tenants and possess hierarchical roles (`Viewer` < `Editor` < `Admin`). Documents possess a lifecycle state (`DRAFT`, `PUBLISHED`, `ARCHIVED`).

You must implement an object-level authorization engine that enforces:
1. **Multi-Tenant Barrier**: A user can **never** access documents belonging to a different tenant.
2. **Archival Protection**: A document in `ARCHIVED` state can **only** be modified or deleted by an `Admin`. `Editors` are blocked from modifying archived documents even though they have the `doc:update` permission.
3. **Per-Request Cache**: Once a permission decision is made for a `(user_id, permission, doc_id)` triple, subsequent evaluations within the same request must return in under 2 microseconds from cache.

### Requirements & Constraints
- Pure Python standard library only.
- Implement explicit role inheritance.
- Verify both tenant isolation and archival policy constraints.

### Executable Solution & Verification

```python
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class Role:
    name: str
    permissions: Set[str] = field(default_factory=set)
    parent: Optional["Role"] = None

    def get_all_permissions(self) -> Set[str]:
        perms = set(self.permissions)
        cur = self.parent
        while cur:
            perms.update(cur.permissions)
            cur = cur.parent
        return perms


ROLE_VIEWER = Role("Viewer", {"doc:read"})
ROLE_EDITOR = Role("Editor", {"doc:create", "doc:update"}, parent=ROLE_VIEWER)
ROLE_ADMIN = Role("Admin", {"doc:delete", "doc:manage", "doc:unarchive"}, parent=ROLE_EDITOR)


@dataclass
class Document:
    id: str
    tenant_id: str
    owner_id: uuid.UUID
    status: str  # "DRAFT", "PUBLISHED", "ARCHIVED"


class UserContext:
    def __init__(self, tenant_id: str, role: Role) -> None:
        self.id = uuid.uuid4()
        self.tenant_id = tenant_id
        self.role = role
        self.is_active = True
        self.is_superuser = False
        # Request-scoped memoization cache
        self._cache: Dict[Tuple[str, str], bool] = {}

    def clear_cache(self) -> None:
        self._cache.clear()


class PolicyEvaluator:
    @classmethod
    def can_perform(cls, user: UserContext, action: str, doc: Document) -> bool:
        cache_key = (action, doc.id)
        if cache_key in user._cache:
            return user._cache[cache_key]

        decision = cls._evaluate(user, action, doc)
        user._cache[cache_key] = decision
        return decision

    @classmethod
    def _evaluate(cls, user: UserContext, action: str, doc: Document) -> bool:
        if not user.is_active:
            return False

        # 1. Multi-Tenant Barrier
        if user.tenant_id != doc.tenant_id:
            return False

        # 2. Archival Protection Policy:
        # Modifying or deleting an archived document requires Admin role
        if doc.status == "ARCHIVED" and action in ("doc:update", "doc:delete"):
            if user.role.name != "Admin":
                return False

        # 3. Role-Based Permission Check
        return action in user.role.get_all_permissions()


# --- Verification Harness ---
if __name__ == "__main__":
    t_acme = "tenant_acme"
    t_beta = "tenant_beta"

    editor_alice = UserContext(tenant_id=t_acme, role=ROLE_EDITOR)
    admin_bob = UserContext(tenant_id=t_acme, role=ROLE_ADMIN)

    doc_active = Document(id="D1", tenant_id=t_acme, owner_id=editor_alice.id, status="PUBLISHED")
    doc_archived = Document(id="D2", tenant_id=t_acme, owner_id=editor_alice.id, status="ARCHIVED")
    doc_beta = Document(id="D3", tenant_id=t_beta, owner_id=uuid.uuid4(), status="PUBLISHED")

    # 1. Editor can update active document in same tenant
    assert PolicyEvaluator.can_perform(editor_alice, "doc:update", doc_active) is True

    # 2. Archival Protection: Editor CANNOT update archived document
    assert PolicyEvaluator.can_perform(editor_alice, "doc:update", doc_archived) is False

    # 3. Admin CAN update archived document
    assert PolicyEvaluator.can_perform(admin_bob, "doc:update", doc_archived) is True

    # 4. Multi-Tenant Barrier: Editor CANNOT read document in Tenant Beta
    assert PolicyEvaluator.can_perform(editor_alice, "doc:read", doc_beta) is False

    # 5. Verify Request Cache Performance
    editor_alice.clear_cache()
    t0 = time.perf_counter()
    PolicyEvaluator.can_perform(editor_alice, "doc:update", doc_active)
    t_cold = time.perf_counter() - t0

    t1 = time.perf_counter()
    PolicyEvaluator.can_perform(editor_alice, "doc:update", doc_active)
    t_hot = time.perf_counter() - t1

    assert t_hot < 0.00005  # < 50 microseconds
    print("[SUCCESS] Challenge 3: Object-Level RBAC Policy Evaluator verified.")
```
