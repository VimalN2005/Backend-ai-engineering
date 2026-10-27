# Day 38: Custom User Model, Authentication Backends & RBAC Permissions

Identity, authentication, and authorization constitute the security foundation of every enterprise application. In Django, failing to configure a custom user model before running the initial database migration is widely considered the single most costly architectural mistake a backend team can make.

This guide provides an exhaustive, production-grade deep dive into:
- Architecting an enterprise **Custom User Model** using `AbstractBaseUser` and `PermissionsMixin`.
- Writing a rock-solid **Custom `BaseUserManager`** with email normalization and superuser validation.
- The mechanics of **Password Hashing**, timing attack mitigation, and automated hasher upgrades on login.
- Composable **Authentication Backends** (`AUTHENTICATION_BACKENDS`) supporting multi-identifier login (email, phone, username), external tokens, and API keys.
- **Role-Based Access Control (RBAC)**, group hierarchies, and object-level permissions for multi-tenant data isolation.

---

## 1. Why Django's Default User Model is an Enterprise Trap

Django ships with an out-of-the-box user model: `django.contrib.auth.models.User`. While convenient for basic tutorials and prototypes, relying on it in an enterprise codebase introduces critical architectural liabilities:

1. **Username Mandatory**: The default model enforces a mandatory `username` column with a 150-character limit. Modern enterprise applications almost universally use **email addresses**, **phone numbers**, or **SSO identity provider identifiers (IdP GUIDs)** as primary credentials.
2. **Missing Essential Enterprise Fields**: Real-world systems require tenant isolation (`tenant_id` / `organization_id`), account status flags (`is_verified`, `is_suspended`), contact metadata (`phone_number`), and security audit trails (`failed_login_attempts`, `password_changed_at`).
3. **Rigid Database Constraints**: Adding foreign keys, unique constraints, or composite indexes to `auth_user` requires fragile database monkey-patching or separate profile tables that incur unnecessary SQL JOINs on every authenticated request.
4. **Hard Foreign Key Lock-in**: Once foreign keys in other apps point to `auth.User`, switching to a custom user model in an active database with historical migrations is an excruciating, high-risk migration process involving schema downtime, foreign key rewrites, and database locks.

> [!IMPORTANT]
> **Django Documentation Official Rule**:
> *"If you're starting a new project, it's highly recommended to set up a custom user model, even if the default User model is sufficient for you. This model behaves just like the default user model, but you'll be able to customize it in the future if the need arises."*

---

## 2. `AbstractUser` vs `AbstractBaseUser` vs One-to-One Profile

When customizing user identity in Django, there are three distinct architectural approaches:

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| ARCHITECTURAL PATTERN    | WHEN TO USE                 | TRADE-OFFS                      |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 1. One-to-One Profile    | Legacy systems where        | Requires extra SQL JOIN (N+1)   |
|    (UserProfile model)   | AUTH_USER_MODEL cannot      | on every request; fragmented    |
|                          | be changed.                 | signals; two models per user.   |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 2. AbstractUser          | When default fields         | Retains username and legacy     |
|    (Subclasses Django    | (first_name, last_name,     | fields; harder to make email    |
|     standard user)       | username) are acceptable    | the sole unique identifier      |
|                          | with a few extra fields.    | cleanly without boilerplate.    |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| 3. AbstractBaseUser +    | ENTERPRISE STANDARD:        | Requires implementing custom    |
|    PermissionsMixin      | Full control over schema,   | BaseUserManager from scratch.   |
|                          | email as primary login,     | Highly flexible and clean.      |
|                          | UUID primary keys, RBAC.    |                                 |
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

---

## 3. Designing the Enterprise Custom User Model

The industry standard for production enterprise backends subclasses **`AbstractBaseUser`** (which supplies `password`, `last_login`, and hashing utilities) combined with **`PermissionsMixin`** (which supplies `groups`, `user_permissions`, `is_superuser`, and the permission checking methods `has_perm` and `has_module_perms`).

```python
# accounts/models.py
import uuid
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from .managers import CustomUserManager


class User(AbstractBaseUser, PermissionsMixin):
    """
    Enterprise Custom User Model.
    Uses UUID as the primary key and lowercase email as the unique username field.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Globally unique identifier preventing enumeration attacks."
    )
    email = models.EmailField(
        verbose_name="email address",
        max_length=255,
        unique=True,
        db_index=True,
        error_messages={
            "unique": "A user with that email address already exists.",
        },
    )
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=32, blank=True, null=True, unique=True)
    
    # Status flags
    is_active = models.BooleanField(
        default=True,
        help_text="Designates whether this user account should be treated as active."
    )
    is_staff = models.BooleanField(
        default=False,
        help_text="Designates whether the user can log into the Django admin site."
    )
    is_verified = models.BooleanField(
        default=False,
        help_text="Designates whether the user's primary email has been verified."
    )
    
    # Enterprise Audit Trails
    date_joined = models.DateTimeField(default=timezone.now)
    last_password_change = models.DateTimeField(null=True, blank=True)
    failed_login_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    # The field used as the unique identifier for authentication
    USERNAME_FIELD = "email"

    # Fields prompted for when running `python manage.py createsuperuser`
    # Note: USERNAME_FIELD and password are required by default; do NOT include them here.
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        db_table = "auth_users"
        verbose_name = "user"
        verbose_name_plural = "users"
        indexes = [
            models.Index(fields=["email", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.email

    def get_full_name(self) -> str:
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.email

    def get_short_name(self) -> str:
        return self.first_name or self.email
```

---

## 4. Custom `BaseUserManager` Internals

When using `AbstractBaseUser`, Django requires a companion manager subclassing `django.contrib.auth.models.BaseUserManager`. This manager handles email normalization and user creation for both standard users and administrative superusers.

```python
# accounts/managers.py
from django.contrib.auth.models import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class CustomUserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifier
    for authentication instead of usernames.
    """

    def create_user(self, email: str, password: str = None, **extra_fields):
        """
        Creates and saves a standard User with the given email and password.
        """
        if not email:
            raise ValueError("The Email field must be set.")

        # 1. Normalize email domain part to lowercase (RFC 5321 standard)
        email = self.normalize_email(email).lower()

        # 2. Validate email structure
        try:
            validate_email(email)
        except ValidationError:
            raise ValueError("Invalid email format provided.")

        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        user = self.model(email=email, **extra_fields)

        # 3. Securely hash password via AbstractBaseUser.set_password()
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str = None, **extra_fields):
        """
        Creates and saves a Superuser with the given email and password.
        Guarantees is_staff=True and is_superuser=True.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)
```

### The `normalize_email()` Method
`BaseUserManager.normalize_email(email)` splits the email address at the `@` symbol and lowercases only the **domain component**, keeping the local part intact according to RFC standards. However, in production systems where email uniqueness is case-insensitive, normalizing the entire string with `.lower()` ensures `Alice@Acme.com` and `alice@acme.com` map to the same database row.

---

## 5. The `AUTH_USER_MODEL` Setting & Initial Migration Invariant

To register the custom user model with Django, `settings.py` must define:

```python
# settings/base.py
AUTH_USER_MODEL = 'accounts.User'
```

### The Invariant Rule
`AUTH_USER_MODEL` MUST be set before executing the very first migration (`manage.py migrate`). Why?
- Django's built-in apps (`admin`, `auth`, `sessions`, `contenttypes`) create foreign key constraints pointing to `settings.AUTH_USER_MODEL`.
- If migrations run before setting `AUTH_USER_MODEL`, Django creates `auth_user` and generates foreign keys in `django_admin_log` pointing to `auth_user.id` (integer).
- Changing `AUTH_USER_MODEL` afterward causes foreign key type mismatches (e.g., Integer vs UUID) and breaks the migration graph.

### Referencing the User Model in Foreign Keys
Never import the User model directly in model files:
```python
# WRONG - Hardcoded coupling
from accounts.models import User
class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)

# CORRECT - Dynamic string reference avoiding circular imports
from django.conf import settings
class Order(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
```

In view code, helper functions, or forms, retrieve the runtime class using:
```python
from django.contrib.auth import get_user_model

User = get_user_model()
```

---

## 6. Retrofitting a Custom User Model on an Existing Database

If a legacy production database already ran initial migrations with default `auth.User`, migrating to a custom user model requires an **8-step zero-data-loss migration procedure**:

```
+─────────────────────────────────────────────────────────────────────────────+
|               RETROFITTING CUSTOM USER MODEL ON EXISTING DB                 |
+─────────────────────────────────────────────────────────────────────────────+
| 1. Backup: Full PostgreSQL pg_dump snapshot                                |
| 2. Create Custom User Model matching existing auth_user columns             |
| 3. Set db_table = 'auth_user' on custom model (avoids table rename)         |
| 4. Create initial migration for accounts app:                               |
|    manage.py makemigrations accounts                                        |
| 5. Fake initial accounts migration:                                         |
|    manage.py migrate accounts --fake-initial                                |
| 6. Update settings.AUTH_USER_MODEL = 'accounts.User'                        |
| 7. Update django_content_type records in DB:                                |
|    UPDATE django_content_type SET app_label='accounts'                      |
|    WHERE app_label='auth' AND model='user';                                 |
| 8. Verify foreign keys in admin_log and sessions run cleanly                |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 7. Password Hashing Internals & Hashers Pipeline

Django does not store raw passwords; it stores a cryptographically salted one-way hash string in the format:
```
<algorithm>$<iterations>$<salt>$<hash>
```

Example PBKDF2 hash:
```
pbkdf2_sha256$870000$dGhpcy1pcy1zYWx0$E3j5M6x8Q9...=
```

### The Password Hashers Setting
```python
# settings.py
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',       # Default (SHA256, 870k iterations)
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',   # Legacy fallback
    'django.contrib.auth.hashers.Argon2PasswordHasher',       # Memory-hard (Argon2id)
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher', # BCrypt fallback
]
```

When verifying credentials:
1. `check_password(raw_password, user.password)` extracts the algorithm name from the stored string.
2. It looks up the matching hasher class from `PASSWORD_HASHERS`.
3. It computes the hash using the stored salt and iteration count.
4. It compares the newly computed hash with the stored hash using **constant-time comparison**.

---

## 8. Automated Password Upgrades on Successful Login (`must_update`)

Security recommendations continuously evolve. As GPU computing power advances, hash iteration counts must increase (e.g. from 100,000 iterations to 870,000 iterations), or systems may migrate from PBKDF2 to Argon2id.

Django solves password migration transparently without forcing a mass password reset:
1. When a user logs in with valid credentials, `hasher.must_update(encoded)` checks if:
   - The stored hasher is no longer the preferred (first) hasher in `PASSWORD_HASHERS`.
   - The stored iteration count is lower than the hasher's current configuration.
2. If `must_update` returns `True`, Django automatically rehashes the cleartext password using the newest algorithm and updates the database record:

```python
# Internal Django authenticate workflow
if user.check_password(password):
    if user.has_usable_password() and hasher.must_update(user.password):
        user.set_password(password)
        user.save(update_fields=["password"])
```

---

## 9. Timing Attack Defenses & Constant-Time String Comparison

A **Timing Attack** is a side-channel attack where an adversary measures the exact microsecond response time of a server during password or token verification. 

Standard string equality (`secret == input`) short-circuits on the first non-matching byte:
- An incorrect first character fails in $\approx 20\text{ ns}$.
- A string matching the first 15 characters before failing takes $\approx 150\text{ ns}$.
- By sending thousands of requests and measuring latency distributions, attackers can deduce secrets byte-by-byte.

### Django's Defense: `constant_time_compare`
Django uses `django.utils.crypto.constant_time_compare` (backed by Python standard library `hmac.compare_digest`):

```python
import hmac

def constant_time_compare(val1: str, val2: str) -> bool:
    """
    Returns True if the two strings are equal, False otherwise.
    The time taken is independent of the number of characters that match.
    """
    return hmac.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))
```

---

## 10. Authentication Backend Architecture (`authenticate()` & `get_user()`)

Authentication in Django is handled by a chain of **Authentication Backends** configured in `settings.py`:

```python
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailOrUsernameBackend',
    'accounts.backends.ApiKeyAuthenticationBackend',
    'django.contrib.auth.backends.ModelBackend',
]
```

### The Protocol Contract
Every authentication backend must implement two methods:
1. **`authenticate(request, **credentials)`**:
   - Takes incoming credentials (`email`, `username`, `password`, `token`, etc.).
   - Returns a `User` instance if credentials are valid and user is allowed to authenticate.
   - Returns `None` if credentials do not match or this backend cannot process them.
   - Raises `PermissionDenied` to halt further backend evaluation immediately.
2. **`get_user(user_id)`**:
   - Takes a user ID (from session cookie) and returns the corresponding `User` object or `None`.
   - Invoked on every authenticated request by `AuthenticationMiddleware`.

### Evaluation Pipeline Flow

```
django.contrib.auth.authenticate(request, **credentials)
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 1. EmailOrUsernameBackend    │ ──[Valid User]──> Return User (Stop)
   └──────────────┬───────────────┘
                  │ (Returns None)
                  ▼
   ┌──────────────────────────────┐
   │ 2. ApiKeyBackend             │ ──[Valid User]──> Return User (Stop)
   └──────────────┬───────────────┘
                  │ (Returns None)
                  ▼
   ┌──────────────────────────────┐
   │ 3. ModelBackend (Fallback)   │ ──[Valid User]──> Return User (Stop)
   └──────────────┬───────────────┘
                  │ (Returns None)
                  ▼
            Return None (Authentication Failed: 401)
```

---

## 11. Custom Multi-Identifier Authentication Backend

Allows users to log in seamlessly using either their **Email**, **Phone Number**, or legacy **Username**:

```python
# accounts/backends.py
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


class MultiIdentifierAuthBackend(ModelBackend):
    """
    Authenticates against settings.AUTH_USER_MODEL by resolving
    email, phone_number, or username interchangeably.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get("email") or kwargs.get("phone")
        if not username or not password:
            return None

        # Clean search identifier
        identifier = username.strip().lower()

        try:
            # Query across multiple identity fields
            user = User.objects.get(
                Q(email__iexact=identifier) |
                Q(phone_number__iexact=identifier)
            )
        except User.DoesNotExist:
            # Run the default password hasher once to mitigate user enumeration timing attacks
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            # Ambiguity safeguard: reject if multiple users share an identifier
            return None

        # Verify password and active status
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
```

---

## 12. External Token & API-Key Authentication Backend

For machine-to-machine (M2M) API calls and microservice authentication:

```python
# accounts/backends.py
import hmac
from django.contrib.auth.backends import BaseBackend
from .models import ApiKey


class ApiKeyAuthenticationBackend(BaseBackend):
    """
    Authenticates requests bearing an X-API-Key header.
    """

    def authenticate(self, request, api_key_token=None, **kwargs):
        if not api_key_token and request:
            api_key_token = request.headers.get("X-API-Key")

        if not api_key_token:
            return None

        try:
            key_obj = ApiKey.objects.select_related("user").get(
                key_prefix=api_key_token[:8],
                is_active=True
            )
        except ApiKey.DoesNotExist:
            return None

        # Verify full hashed secret in constant time
        if hmac.compare_digest(key_obj.hashed_secret, ApiKey.hash_token(api_key_token)):
            key_obj.record_usage()
            return key_obj.user

        return None
```

---

## 13. Django Built-in RBAC: Users, Groups, and Permissions Model

Django provides a normalized relational Role-Based Access Control schema:

```
+───────────────────+             +────────────────────+
|     auth_users    |             |     auth_group     |
+───────────────────+             +────────────────────+
| id (UUID)         |             | id (Int)           |
| email             |             | name               |
+─────────┬─────────+             +─────────┬──────────+
          │                                 │
          │ M2M: user_groups                │ M2M: group_permissions
          ▼                                 ▼
+──────────────────────────────────────────────────────+
|                   auth_permission                    |
+──────────────────────────────────────────────────────+
| id (Int)                                             |
| content_type_id (FK -> django_content_type)          |
| codename (e.g., 'change_invoice', 'view_report')     |
| name                                                 |
+──────────────────────────────────────────────────────+
```

### Permission Format: `<app_label>.<codename>`
- `billing.view_invoice`
- `billing.add_invoice`
- `billing.change_invoice`
- `billing.delete_invoice`

### Permission Checking Methods on `User`:
- `user.has_perm('billing.change_invoice')`: Returns `True` if user has permission directly or via group membership. (Always returns `True` if `user.is_superuser`).
- `user.has_perms(['billing.view_invoice', 'billing.change_invoice'])`: Returns `True` if user has ALL listed permissions.
- `user.get_all_permissions()`: Returns a set of strings representing all effective permissions. **Django caches this set on `request.user._perm_cache`** to prevent querying the database on every permission check during a single HTTP request.

---

## 14. Object-Level Permissions & Multi-Tenant Access Control

Standard `ModelBackend.has_perm(user, perm, obj=None)` **ignores `obj`**. If you pass an object:
```python
user.has_perm('billing.change_invoice', invoice_instance)
```
Django's default backend evaluates model-level permission and discards `invoice_instance`!

### Writing an Object-Level Permission Backend

```python
# permissions/backends.py
from django.contrib.auth.backends import ModelBackend


class TenantObjectPermissionBackend(ModelBackend):
    """
    Enforces tenant boundaries and resource ownership.
    """

    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_authenticated:
            return False

        # Superusers bypass object checks
        if user_obj.is_superuser:
            return True

        # If no object provided, fall back to model-level check
        if obj is None:
            return super().has_perm(user_obj, perm)

        # 1. Multi-Tenant Boundary: Object must belong to user's tenant
        if hasattr(obj, "tenant_id") and hasattr(user_obj, "tenant_id"):
            if obj.tenant_id != user_obj.tenant_id:
                return False

        # 2. Ownership check: Direct owner has full write permissions
        if hasattr(obj, "owner_id") and obj.owner_id == user_obj.id:
            return True

        # 3. Manager/Admin role within the same tenant
        if user_obj.groups.filter(name="TenantAdmin").exists():
            return True

        return False
```

---

## 15. Enterprise Role Hierarchies & Permission Evaluation Pipeline

In enterprise SaaS platforms, roles form a directed hierarchy where higher roles inherit all capabilities of lower roles:

```
[SuperAdmin]
     │
     ▼
[TenantAdmin] (Organization-level control)
     │
     ▼
[ProjectManager] (Manage projects, assign tickets)
     │
     ▼
[Developer] (Read, write, edit assigned tasks)
     │
     ▼
[Viewer] (Read-only access)
```

### The Evaluation Order
When evaluating authorization for an action on a resource:
1. **Superuser Override**: If `user.is_superuser`, grant access immediately.
2. **Tenant Isolation Check**: Reject immediately if `user.tenant_id != resource.tenant_id`.
3. **Explicit Deny Rules**: Check if user or account is suspended / locked.
4. **Direct User Permissions**: Check explicit permissions assigned directly to `user_permissions`.
5. **Role / Group Permissions**: Check permissions inherited through `user.groups`.
6. **Object Ownership Policy**: Check if user is the designated author or owner of the resource.
7. **Default Deny**: Reject if no positive rule matched.
