# Senior Backend Engineering Interview: Custom User Models, Auth Backends & RBAC

This guide compiles the top 5 high-frequency architectural interview questions targeting Senior and Staff Backend Engineers working with Django authentication, identity architecture, and access control systems.

---

### Question 1: Why must `AUTH_USER_MODEL` be set before initial migrations, and what is the exact migration procedure to retrofit a custom user model on an existing, live database?

#### Executive Summary
Django's built-in apps (`admin`, `auth`, `contenttypes`) create foreign key constraints pointing to `settings.AUTH_USER_MODEL` on their initial migration. If migrations execute before defining `AUTH_USER_MODEL`, Django hardwires foreign keys to the default `auth_user` table. Changing it later breaks the dependency graph and causes foreign key type mismatches. 

Retrofitting a custom user model onto an existing database requires an **8-step zero-downtime procedure** that keeps the underlying table name as `auth_user` to avoid table rewrites while updating Django's content types and migration history.

#### Deep Architectural Breakdown

#### Why the Invariant Exists
In `django.contrib.admin.models.LogEntry`:
```python
user = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    models.CASCADE,
    verbose_name=_("user"),
)
```
When `manage.py migrate` runs with default settings:
1. `auth.0001_initial` creates `auth_user` with an integer `id` column.
2. `admin.0001_initial` creates `django_admin_log` with `user_id integer REFERENCES auth_user(id)`.
3. If you later swap `AUTH_USER_MODEL = 'accounts.User'` with UUID primary keys, existing database foreign keys are fundamentally incompatible with UUID columns.

#### The 8-Step Production Retrofit Procedure
When tasked with retrofitting a custom user model into an existing production database:

1. **Database Snapshot**: Take a full physical snapshot/dump (`pg_dump -Fc`).
2. **Model Definition with Legacy Table**: Subclass `AbstractUser` or `AbstractBaseUser` in a new `accounts` app, but set `Meta.db_table = "auth_user"`. This tells Django's ORM to reuse the existing physical database table without renaming or dropping it:
   ```python
   # accounts/models.py
   class User(AbstractUser):
       class Meta:
           db_table = "auth_user"
   ```
3. **Generate Initial App Migration**:
   ```bash
   python manage.py makemigrations accounts
   ```
4. **Fake the Initial Migration**:
   Tell Django's migration tracker that `accounts.0001_initial` has already run without executing any DDL:
   ```bash
   python manage.py migrate accounts --fake-initial
   ```
5. **Update Setting**:
   ```python
   # settings.py
   AUTH_USER_MODEL = 'accounts.User'
   ```
6. **Migrate ContentType Records**:
   Django's permission system links permissions to `django_content_type`. Update the pointer:
   ```sql
   UPDATE django_content_type 
   SET app_label = 'accounts' 
   WHERE app_label = 'auth' AND model = 'user';
   ```
7. **Fix Foreign Key Constraints**: If converting primary keys from integer to UUID, run an expand-and-contract migration adding a nullable `new_uuid_id` column, backfilling, updating foreign keys, and swapping primary key constraints.
8. **Verify Admin & Session Logging**: Run full integration test suite verifying that user authentication, session decoding, and admin audit logs function without foreign key constraint violations.

#### What Interviewers Look For
- Immediate recognition that foreign key dependencies in `admin` and `contenttypes` create the deadlock.
- Knowledge of the `db_table = "auth_user"` trick to avoid dropping/recreating tables in production.
- Familiarity with `--fake-initial` and `django_content_type` synchronization.

---

### Question 2: How do `AbstractUser` and `AbstractBaseUser` differ under the hood, and what architectural factors dictate choosing `AbstractBaseUser + PermissionsMixin`?

#### Executive Summary
- **`AbstractUser`**: Inherits the full default Django user schema (`username`, `first_name`, `last_name`, `email`, `is_staff`, `is_active`, `date_joined`, `groups`, `user_permissions`). It is suitable when retaining `username` as an identifier with minor additional fields.
- **`AbstractBaseUser`**: Bare-minimum foundation containing only `password`, `last_login`, and password hashing logic.
- **Enterprise Standard**: Large-scale architectures almost exclusively choose **`AbstractBaseUser + PermissionsMixin`** because it decouples identity from legacy username fields, enables email or UUID as primary credentials, eliminates dead columns, and grants full control over the database schema.

#### Architectural Comparison Matrix

```
+──────────────────────────┬─────────────────────────────┬─────────────────────────────────+
| CAPABILITY               | AbstractUser                | AbstractBaseUser + Mixin        |
+──────────────────────────┼─────────────────────────────┼─────────────────────────────────+
| Schema Overhead          | Heavy: 11 built-in fields   | Minimal: 3 built-in fields      |
| Mandatory Username       | Yes (Must override or hide) | No (Completely omitted)         |
| Primary Identifier       | Defaults to username        | Declarative (USERNAME_FIELD)   |
| BaseUserManager Required | No (Reuses default)         | Yes (Must implement from start) |
| Permissions Support      | Built-in                    | Provided by PermissionsMixin    |
| Custom PK (UUIDv4)       | Cumbersome override         | Clean, native definition        |
+──────────────────────────┴─────────────────────────────┴─────────────────────────────────+
```

#### Why Enterprise Teams Avoid `AbstractUser`
1. **Removing Username is Fragile**: In `AbstractUser`, `username` has a unique constraint. If you make email the primary login field, you must either set `username = None` (which breaks certain admin/form assumptions) or populate dummy usernames via signals.
2. **Dead Weight Columns**: Modern APIs rarely need separate `first_name` and `last_name` columns (preferring a single `full_name` or internationalized name fields) or legacy `date_joined` timestamps when audit timestamps (`created_at`, `updated_at`) already exist.
3. **Clean Domain Boundaries**: `AbstractBaseUser + PermissionsMixin` gives you a blank canvas to enforce strict enterprise invariants (e.g. `tenant_id`, `failed_login_count`, `locked_until`, `is_verified`).

---

### Question 3: Walk through the internals of Django's authentication pipeline: how does `authenticate()` evaluate `AUTHENTICATION_BACKENDS`, what contract must custom backends fulfill, and how does `get_user()` interact with `AuthenticationMiddleware`?

#### Executive Summary
Django's authentication system uses the **Chain of Responsibility pattern**. When `django.contrib.auth.authenticate(request, **credentials)` is called, it iterates through the list of backends defined in `AUTHENTICATION_BACKENDS`. The first backend to return a `User` instance terminates the loop. Custom backends must implement `authenticate(request, **credentials)` and `get_user(user_id)`.

#### Detailed Pipeline Flow

```
1. Client submits credentials -> View calls authenticate(request, email=..., password=...)
2. Django loads settings.AUTHENTICATION_BACKENDS:
   ['backends.OAuth2Backend', 'backends.EmailBackend', 'backends.ModelBackend']
3. Loop over backends:
   a. backend.authenticate(request, **credentials)
   b. If returns User instance:
      - Attach backend path to user: user.backend = 'backends.EmailBackend'
      - Return user immediately (Short-circuit!)
   c. If returns None: Continue to next backend.
   d. If raises PermissionDenied: Terminate pipeline immediately (Deny!).
4. If loop completes with no match: Return None.
5. On successful login -> login(request, user):
   - Stores user.id and user.backend path into request.session['_auth_user_id']
6. Subsequent Requests -> AuthenticationMiddleware:
   - Reads user_id and backend path from session.
   - Loads the exact backend that authenticated the user.
   - Calls backend.get_user(user_id) to populate request.user.
```

#### The Custom Backend Protocol Contract

```python
class CustomEnterpriseAuthBackend:
    def authenticate(self, request, **credentials):
        """
        Must return:
          - A User instance if credentials are valid.
          - None if credentials do not match or are not handled by this backend.
          - Raise PermissionDenied if authentication should be actively halted (e.g. banned IP).
        """
        pass

    def get_user(self, user_id):
        """
        Must return:
          - User instance if user_id exists and is allowed to authenticate.
          - None if user does not exist.
        """
        pass
```

#### Why `user.backend` is Stored in the Session
Different backends may attach different permissions or user representations. When a user logs in, Django records `request.session['_auth_user_backend'] = 'path.to.Backend'`. When `AuthenticationMiddleware` loads the user on subsequent requests, it invokes `get_user()` on that **specific backend**, ensuring consistency across heterogeneous auth sources (e.g., LDAP vs Local DB).

---

### Question 4: How does Django's password hashing pipeline operate? How does it transparently upgrade passwords (`must_update`) on login without forcing a mass password reset, and how are timing attacks prevented?

#### Executive Summary
Django stores passwords in a modular string envelope: `<algorithm>$<iterations>$<salt>$<hash>`. During credential verification, Django checks if the stored hasher algorithm or iteration count is outdated relative to `PASSWORD_HASHERS[0]`. If `hasher.must_update(encoded)` returns `True`, Django automatically re-hashes the plaintext password using the newest algorithm and updates the database row in-place upon successful login. Timing attacks are mitigated using constant-time digest comparisons and dummy hashing for non-existent users.

#### Deep Hashing Mechanics

```
Stored Hash Format:
pbkdf2_sha256$870000$dGhpcy1pcy1zYWx0$E3j5M6x8Q9w2e3r4t5y6u7i8...=
│             │      │                │
│             │      │                └─> Base64-encoded PBKDF2 derived key (32 bytes)
│             │      └──────────────────> Cryptographically secure random salt
│             └─────────────────────────> Iteration count (work factor)
└───────────────────────────────────────> Hasher algorithm name
```

#### 1. Transparent Password Upgrades (`must_update`)
When your engineering team upgrades from PBKDF2 with 320,000 iterations to 870,000 iterations (or migrates to Argon2id):
1. You prepend the new hasher to `PASSWORD_HASHERS` in `settings.py`.
2. When User Alice logs in with raw password `secret`:
   - Django uses the algorithm identifier in her stored hash (`pbkdf2_sha256$320000...`) to select the legacy hasher.
   - Credentials verify successfully.
   - Django checks `hasher.must_update(user.password)`. Because the stored iterations ($320,000 < 870,000$) or algorithm differs from `PASSWORD_HASHERS[0]`, it returns `True`.
   - Django invokes `user.set_password(raw_password)` and saves the updated hash to the database.
3. Over time, all active users are upgraded seamlessly without disruptive email password reset campaigns.

#### 2. Side-Channel Timing Attack Defenses
1. **Constant-Time Verification**: Django uses `hmac.compare_digest` in `constant_time_compare`. Rather than terminating on the first mismatched byte (standard `==`), it compares every byte, guaranteeing identical execution duration regardless of how many prefix characters match.
2. **User Enumeration Defense**: If an attacker submits a login request for a non-existent email `fake@corp.com`, a naive backend returns immediately (`~0.1ms`), while an existing user executes 870,000 PBKDF2 iterations (`~50ms`). Django's `ModelBackend` calls `User().set_password(password)` on a dummy instance when the user is not found, equalizing response latency to prevent user enumeration.

---

### Question 5: How does Django's built-in RBAC (`User`, `Group`, `Permission`) work, why does `ModelBackend.has_perm()` discard the `obj` parameter, and how do you architect fine-grained object-level and multi-tenant authorization?

#### Executive Summary
Django's built-in RBAC model is a normalized relational structure: Users belong to Groups, and both Users and Groups have Many-to-Many relationships to `Permission` (`<app_label>.<action>_<model>`). However, `ModelBackend.has_perm(user, perm, obj=None)` **discards `obj`** because Django's core ORM does not implement an object-level permissions schema. In multi-tenant enterprise backends, object-level authorization must be implemented via custom backends or dedicated policy engines enforcing tenant boundaries and ownership rules.

#### Why `ModelBackend` Discards `obj`
In `django.contrib.auth.backends.ModelBackend`:
```python
def has_perm(self, user_obj, perm, obj=None):
    if not user_obj.is_active:
        return False
    return perm in self.get_all_permissions(user_obj, obj)

def get_all_permissions(self, user_obj, obj=None):
    # Notice: obj is accepted in the signature for API compatibility,
    # but the default ModelBackend completely ignores it!
    if not user_obj.is_active or user_obj.is_anonymous:
        return set()
    if not hasattr(user_obj, "_perm_cache"):
        user_obj._perm_cache = {
            *self.get_user_permissions(user_obj),
            *self.get_group_permissions(user_obj),
        }
    return user_obj._perm_cache
```

#### Architecting Multi-Tenant Object-Level Authorization
In an enterprise multi-tenant application, permissions must be scoped to the organization/tenant:

```
+─────────────────────────────────────────────────────────────+
|               MULTI-TENANT AUTHORIZATION PIPELINE           |
+─────────────────────────────────────────────────────────────+
| 1. Superuser Check:                                         |
|    - If user.is_superuser -> return True                    |
|                                                             |
| 2. Tenant Boundary Barrier:                                 |
|    - If obj.tenant_id != user.tenant_id -> return False     |
|                                                             |
| 3. Account Status:                                          |
|    - If user.is_suspended or not user.is_active -> False     |
|                                                             |
| 4. Resource Ownership Policy:                               |
|    - If obj.owner_id == user.id and obj.is_draft -> True    |
|                                                             |
| 5. Role Hierarchy & Direct Permissions:                     |
|    - Check if required permission is present in             |
|      user's cached role permissions (memoized on request)   |
|                                                             |
| 6. Separation of Duties:                                    |
|    - E.g., Creator cannot approve own financial item > $10k |
|                                                             |
| 7. Default Deny                                             |
+─────────────────────────────────────────────────────────────+
```

#### Performance Invariant: The Permission Cache
Evaluating object permissions on every record in a list view of 100 items can cause an catastrophic $N+1$ query problem if the backend queries the database for user roles or object metadata on each check. High-throughput backends must:
1. Prefetch user roles and permissions onto `request.user` once per request.
2. Memoize evaluated object-level decisions in `request.user._object_perm_cache = {}` keyed by `(perm, obj.pk)`.
3. Push filtering into the SQL query layer (`Invoice.objects.filter(tenant_id=request.user.tenant_id)`) rather than fetching all rows and filtering in Python with `has_perm()`.
