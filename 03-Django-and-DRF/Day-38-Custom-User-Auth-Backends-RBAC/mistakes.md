# Critical Antipatterns in Django Custom User Models, Auth Backends & RBAC

This guide documents 7 prevalent architectural, concurrency, and security antipatterns encountered in Django user identity modeling, authentication backends, and permission systems, complete with production failure analyses and remediations.

---

## Antipattern 1: Starting a Project Without Setting `AUTH_USER_MODEL` Before Initial Migrations

### Severity: CRITICAL (Schema Deadlock & Catastrophic Migration Debt)

### The Flawed Pattern
```bash
# Day 1 of project: Developer runs migrations before defining custom user model
python manage.py migrate

# Day 90: Product team requests email login and custom user fields.
# Developer attempts to add custom User model:
# settings.py
AUTH_USER_MODEL = 'accounts.User'
python manage.py makemigrations accounts
python manage.py migrate
```

### The Failure Mechanism
1. Running `manage.py migrate` initially creates `auth_user` with integer primary keys and binds foreign key constraints in `django_admin_log`, `authtoken_token`, and session tables.
2. When `AUTH_USER_MODEL` is changed later, Django's migration dependency graph cannot resolve switching foreign keys across apps that already depend on `auth.User`.
3. Running `migrate` results in `ValueError: The field admin.LogEntry.user was declared with a lazy reference to 'accounts.user', but app 'accounts' isn't available` or foreign key type mismatches (e.g. integer vs UUID).
4. Fixing this in an active production database requires a perilous multi-step SQL dump, table faking, and foreign key rewrite migration.

### Production Remediation
**Always** configure a custom user model in your very first migration commit—even if it starts out with identical fields to `AbstractUser`:

```python
# accounts/models.py - DAY 1 COMMIT
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    """Placeholder custom user model established before initial migrations."""
    pass

# settings/base.py - DAY 1 COMMIT
AUTH_USER_MODEL = 'accounts.User'
```

---

## Antipattern 2: In-Place Equality Comparison of Secrets and API Tokens (`==`)

### Severity: CRITICAL (Side-Channel Timing Attack: CWE-208)

### The Flawed Pattern
```python
# backends.py - FLAW: Vulnerable to timing attacks
class ApiKeyBackend:
    def authenticate(self, request, api_key=None):
        stored_key = SystemConfig.get_active_api_key()
        # Vulnerable comparison: short-circuits on first mismatched character!
        if api_key == stored_key:
            return ServiceAccountUser()
        return None
```

### The Failure Mechanism
1. Standard Python string comparison (`str1 == str2`) terminates execution on the **first mismatched byte**.
2. If the secret token is `secret_abcdef123456`:
   - Guessing `x...` terminates in $\approx 25\text{ nanoseconds}$.
   - Guessing `s...` terminates in $\approx 40\text{ nanoseconds}$.
   - Guessing `se...` terminates in $\approx 55\text{ nanoseconds}$.
3. Over thousands of requests across high-speed networks, an attacker measures statistical response latency variations to reconstruct cryptographic tokens byte-by-byte.

### Production Remediation
Always use `hmac.compare_digest` (or `django.utils.crypto.constant_time_compare`):

```python
# backends.py - RESOLVED
import hmac

class ApiKeyBackend:
    def authenticate(self, request, api_key=None):
        if not api_key:
            return None
            
        stored_key = SystemConfig.get_active_api_key()
        
        # Constant-time comparison ensures identical latency regardless of match length
        if hmac.compare_digest(api_key.encode("utf-8"), stored_key.encode("utf-8")):
            return ServiceAccountUser()
        return None
```

---

## Antipattern 3: Omitting `is_staff = True` Validation in Custom `create_superuser`

### Severity: HIGH (Admin Site Lockout)

### The Flawed Pattern
```python
# managers.py
class CustomUserManager(BaseUserManager):
    def create_superuser(self, email, password, **extra_fields):
        # FLAW: Setting is_superuser but forgetting is_staff!
        extra_fields.setdefault('is_superuser', True)
        # Missing: extra_fields.setdefault('is_staff', True)
        return self.create_user(email, password, **extra_fields)
```

### The Failure Mechanism
1. Django's administrative interface (`/admin/`) strictly gates access using the `request.user.is_staff` boolean attribute, **not** `is_superuser`.
2. A user with `is_superuser=True` but `is_staff=False` will successfully pass model permission checks, but will be rejected with HTTP 403 when trying to log into the Django Admin dashboard.
3. If validation checks are omitted, running `createsuperuser` can produce orphaned administrative accounts that cannot manage the site.

### Production Remediation
Explicitly set and validate both `is_staff` and `is_superuser` in `create_superuser()`:

```python
# managers.py - RESOLVED
class CustomUserManager(BaseUserManager):
    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get('is_superuser') is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)
```

---

## Antipattern 4: Querying the Database Inside `has_perm()` Without Request Caching

### Severity: HIGH (N+1 Database Query Storm on Every View/Template Render)

### The Flawed Pattern
```python
# backends.py
class RolePermissionBackend:
    def has_perm(self, user_obj, perm, obj=None):
        # FLAW: Uncached SQL query executed on EVERY permission check!
        return UserRoleMapping.objects.filter(
            user=user_obj,
            role__permissions__codename=perm
        ).exists()
```

### The Failure Mechanism
1. A standard Django view or template checks permissions repeatedly:
   - View decorator (`@permission_required`)
   - DRF permission class (`has_permission`, `has_object_permission`)
   - Template navigation bar (`{% if perms.billing.view_invoice %}`)
   - Action buttons (`{% if perms.billing.delete_invoice %}`)
2. If `has_perm()` hits the database directly, rendering a single page with 15 conditional UI elements triggers 15 individual database queries per request, causing latency spikes and connection pool starvation.

### Production Remediation
Pre-fetch and memoize permissions on the user instance for the lifespan of the request (following Django's built-in `_perm_cache` pattern):

```python
# backends.py - RESOLVED
class RolePermissionBackend:
    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous:
            return set()
            
        # Check instance cache
        if not hasattr(user_obj, "_role_perm_cache"):
            # Single database query fetching all permission codenames
            user_obj._role_perm_cache = set(
                UserRoleMapping.objects.filter(user=user_obj)
                .values_list("role__permissions__codename", flat=True)
            )
        return user_obj._role_perm_cache

    def has_perm(self, user_obj, perm, obj=None):
        return perm in self.get_all_permissions(user_obj, obj)
```

---

## Antipattern 5: Calling `get_user_model()` at Top-Level Module Scope

### Severity: HIGH (Circular Import Deadlocks & App Registry Failures)

### The Flawed Pattern
```python
# orders/models.py - FLAW: Top-level invocation at import time
from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model() # <-- DEADLOCK! Triggered before apps are fully loaded!

class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
```

### The Failure Mechanism
1. During Django startup, `django.apps.apps.populate()` imports models across installed apps in order.
2. If `orders.models` calls `get_user_model()` at module level before `accounts.models` has registered its `User` model, Django throws:
   `django.core.exceptions.AppRegistryNotReady: Models aren't loaded yet.`
3. Even if the user model is already loaded, importing it directly causes circular import deadlocks when `accounts.models` imports helpers from other domain packages.

### Production Remediation
In `models.py`, **always** use `settings.AUTH_USER_MODEL` string syntax. Reserve `get_user_model()` strictly for functions, methods, views, or forms:

```python
# orders/models.py - RESOLVED
from django.conf import settings
from django.db import models

class Order(models.Model):
    # Lazy string reference resolved AFTER all apps are initialized
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )

# orders/views.py - RESOLVED
from django.contrib.auth import get_user_model

def get_active_customers():
    User = get_user_model() # Safe inside function/method runtime
    return User.objects.filter(is_active=True)
```

---

## Antipattern 6: Hardcoding Foreign Keys to `django.contrib.auth.models.User`

### Severity: HIGH (Incompatible Relationships & Broken Modular Apps)

### The Flawed Pattern
```python
# blog/models.py
from django.contrib.auth.models import User # <-- FLAW: Hardcoded default model!
from django.db import models

class Post(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE)
```

### The Failure Mechanism
1. If the project specifies `AUTH_USER_MODEL = 'accounts.CustomUser'`, `blog.Post` still maintains a foreign key to the legacy `auth_user` table.
2. Any query traversing `Post.objects.select_related('author')` attempts to join `auth_user`, which either does not exist or does not contain custom user records.
3. If `accounts.CustomUser` uses UUID primary keys, the foreign key column type is incompatible with integer `auth_user.id`.

### Production Remediation
Always reference `settings.AUTH_USER_MODEL`:

```python
# blog/models.py - RESOLVED
from django.conf import settings
from django.db import models

class Post(models.Model):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="posts",
    )
```

---

## Antipattern 7: Failing to Lowercase Emails on Registration (Duplicate Identity Poisoning)

### Severity: MEDIUM (Account Enumeration & Dual-Account Confusion)

### The Flawed Pattern
```python
# forms.py or serializers.py
class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def create(self, validated_data):
        # FLAW: Email stored exactly as entered by user
        # User 1 registers: 'alice@acme.com'
        # User 2 registers: 'Alice@Acme.com' (passes unique constraint in case-sensitive DBs)
        return User.objects.create_user(**validated_data)
```

### The Failure Mechanism
1. While RFC 5321 specifies that email local parts can be case-sensitive, modern mail servers (Gmail, Outlook, iCloud) treat addresses as completely case-insensitive.
2. In databases with case-sensitive collation (or when using `unique=True` on PostgreSQL text fields without `citext` or lowercase indexes):
   - `john.doe@company.com` and `John.Doe@company.com` are treated as distinct rows.
3. Attackers can register `Admin@Company.com` to impersonate `admin@company.com`, or users accidentally create multiple conflicting accounts.

### Production Remediation
Always normalize email addresses to lowercase in the custom user manager and serializers:

```python
# managers.py - RESOLVED
class CustomUserManager(BaseUserManager):
    def normalize_email(self, email: str) -> str:
        email = email.strip()
        if "@" in email:
            local_part, domain = email.rsplit("@", 1)
            # Enterprise practice: lowercase both local and domain for case-insensitive identity
            return f"{local_part.lower()}@{domain.lower()}"
        return email.lower()

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        # ...
```
