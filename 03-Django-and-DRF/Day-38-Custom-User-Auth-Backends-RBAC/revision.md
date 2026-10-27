# 5-Minute Rapid Revision: Custom User Models, Auth Backends & RBAC

A high-yield, quick-reference cheat sheet for senior backend engineers reviewing Django user identity models, authentication backends, password hashing internals, and RBAC permissions.

---

## 1. User Model Selection Matrix

```
PATTERN                    BASE CLASS                      BEST FOR
─────────────────────────────────────────────────────────────────────────────────────────────
AbstractUser               django.contrib.auth.models      Quick prototype keeping username,
                           .AbstractUser                   first_name, last_name + few fields.
─────────────────────────────────────────────────────────────────────────────────────────────
AbstractBaseUser +         django.contrib.auth.models      ENTERPRISE STANDARD: Email as primary
PermissionsMixin           .AbstractBaseUser               login, UUID keys, multi-tenant fields,
                           + .PermissionsMixin             complete control over schema.
─────────────────────────────────────────────────────────────────────────────────────────────
One-to-One Profile         models.Model (UserProfile)      Legacy brownfield databases where
                                                           AUTH_USER_MODEL cannot be changed.
─────────────────────────────────────────────────────────────────────────────────────────────
```

---

## 2. The 5 Invariants of `AUTH_USER_MODEL`

1. **Set on Day 1**: Must be defined in `settings.py` before running the very first `manage.py migrate`.
2. **Never Import User Directly in Models**: Use `settings.AUTH_USER_MODEL` in all `ForeignKey` and `ManyToManyField` definitions.
3. **Use `get_user_model()` in Code**: Only call `get_user_model()` inside functions, methods, views, or forms—never at the top-level of module files.
4. **`USERNAME_FIELD` Invariant**: Must name a unique field on the model (e.g. `USERNAME_FIELD = 'email'`).
5. **`REQUIRED_FIELDS` Invariant**: Must list fields prompted during `createsuperuser` (do NOT include `USERNAME_FIELD` or `password`).

---

## 3. Production Custom User Model & Manager Boilerplate

```python
# accounts/models.py
import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set.")
        email = self.normalize_email(email).lower()
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "auth_users"
```

---

## 4. Authentication Backend Protocol & Pipeline

```
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailAuthBackend',
    'accounts.backends.ApiKeyAuthBackend',
    'django.contrib.auth.backends.ModelBackend',
]
```

### The Two Required Methods:
```python
class CustomAuthBackend:
    def authenticate(self, request, **credentials):
        """
        Returns:
          - User instance if credentials match and user is allowed to log in.
          - None if this backend does not handle or match credentials (passes to next backend).
          - Raises PermissionDenied to halt pipeline immediately (strict deny).
        """
        pass

    def get_user(self, user_id):
        """
        Returns User instance or None.
        Invoked on every request by AuthenticationMiddleware via session cookie.
        """
        pass
```

---

## 5. Password Hashing Internals & Transparent Upgrades

### Format
`pbkdf2_sha256$870000$<salt>$<hash>`

### How Transparent Upgrades Work (`must_update`)
1. User logs in with valid password.
2. Django verifies password against stored hash using the legacy hasher algorithm.
3. Django checks `hasher.must_update(user.password)`:
   - If stored iterations < preferred iterations, or algorithm differs from `PASSWORD_HASHERS[0]`:
     `user.set_password(raw_password)` is called and saved to DB immediately.
4. Passwords are upgraded continuously on login with zero user friction.

### Timing Attack Defense
```python
import hmac

# NEVER USE: token == stored_token (Leaks length and byte match via timing!)
# ALWAYS USE:
hmac.compare_digest(token.encode("utf-8"), stored_token.encode("utf-8"))
```

---

## 6. Django Built-in RBAC vs Object-Level Authorization

```
BUILT-IN RBAC (ModelBackend)                OBJECT-LEVEL RBAC (Custom Backend)
─────────────────────────────────────────────────────────────────────────────────────────────
user.has_perm('app.change_invoice')         user.has_perm('app.change_invoice', invoice_obj)
Evaluates table-level permission.           Evaluates resource-level rules:
Discards `obj` parameter!                   1. Multi-Tenant isolation barrier
Uses user._perm_cache for caching.          2. Ownership policy (creator can edit draft)
                                            3. Separation of duties (cannot approve self)
                                            Uses user._object_perm_cache for caching.
─────────────────────────────────────────────────────────────────────────────────────────────
```

---

## 7. Production Security & Architecture Checklist

- [ ] **Did you set `AUTH_USER_MODEL` before running initial migrations?**
- [ ] **Are all model foreign keys referencing `settings.AUTH_USER_MODEL` instead of `User`?**
- [ ] **Does `create_superuser()` validate both `is_staff=True` and `is_superuser=True`?**
- [ ] **Are emails normalized with `.lower()` to prevent duplicate case variations?**
- [ ] **Are tokens and API keys compared using `hmac.compare_digest`?**
- [ ] **Does custom `has_perm()` memoize results on the request user to prevent $N+1$ queries?**
- [ ] **Are multi-tenant boundary checks executed before role permissions on object lookups?**
