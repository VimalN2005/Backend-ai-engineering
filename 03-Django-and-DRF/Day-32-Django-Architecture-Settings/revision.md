# Day 32 Rapid Revision: Django Architecture & Settings Modularization

A high-yield, 5-minute reference guide covering enterprise Django layout, split settings, 12-factor environment parsing, and production security hardening.

---

## 1. Enterprise Two-Tier Directory Blueprint

```text
my_repo/                         <-- Repository Root (Git, CI/CD, Docker)
├── .env.example                 <-- Clean template of required env vars
├── Dockerfile                   <-- Multi-stage production container build
├── docker-compose.yml           <-- Local dev services (PostgreSQL, Redis)
└── src/                         <-- Django Source Root (PYTHONPATH)
    ├── manage.py
    ├── config/                  <-- Project configuration
    │   ├── asgi.py / wsgi.py
    │   ├── urls.py
    │   └── settings/            <-- Split settings package
    │       ├── base.py          <-- Shared invariants (auth, apps, middleware)
    │       ├── development.py   <-- Local dev (DEBUG=True, SQLite, Debug Toolbar)
    │       ├── production.py    <-- Hardened prod (DEBUG=False, Sentry, S3, SSL)
    │       └── testing.py       <-- Fast CI tests (In-memory DB, MD5 hasher)
    └── apps/                    <-- Domain applications
        ├── core/                <-- Abstract models, base utilities
        └── users/               <-- Custom User model (Day 1 invariant!)
```

---

## 2. Multi-Tier Settings Decision Matrix

| Configuration Variable | `base.py` | `development.py` | `testing.py` | `production.py` |
|:-----------------------|:----------|:-----------------|:-------------|:----------------|
| `DEBUG` | — | `True` | `False` | `False` (Enforced) |
| `DATABASES['default']` | PostgreSQL base | Local SQLite3 | `:memory:` SQLite | PostgreSQL + `CONN_MAX_AGE=600` |
| `PASSWORD_HASHERS` | Argon2 + PBKDF2 | Argon2 + PBKDF2 | **`MD5PasswordHasher`** (10x faster) | Argon2 + PBKDF2 |
| `ALLOWED_HOSTS` | — | `["localhost", "127.0.0.1"]` | `["testserver"]` | Strict domain list (No `'*'`) |
| `SECURE_SSL_REDIRECT` | — | `False` | `False` | `True` |
| `SESSION_COOKIE_SECURE` | — | `False` | `False` | `True` |
| `SECURE_HSTS_SECONDS` | — | `0` | `0` | `31536000` (1 Year) |
| `EMAIL_BACKEND` | — | Console / Dummy | LocMem | SMTP / Sendgrid / SES |
| `DEFAULT_FILE_STORAGE` | — | FileSystemStorage | LocMem | S3Boto3Storage |

---

## 3. The 5 Non-Negotiable Architectural Laws

1. **Custom `AUTH_USER_MODEL` on Day 1**:
   Always declare `AUTH_USER_MODEL = 'users.User'` before running the first migration. Changing user models after foreign keys exist causes migration graph deadlocks and costly database downtime.
2. **Never Run `DEBUG = True` in Production**:
   Exposes tracebacks, local variable memory (API keys, passwords), and causes an unbounded memory leak via `connection.queries`.
3. **Parse Environment Booleans Strictly**:
   Remember that in Python: `bool("False") == True`. Always evaluate booleans using explicit string matching (`val.lower() in ("true", "1", "yes")`).
4. **No Wildcard `ALLOWED_HOSTS = ['*']`**:
   Permitting wildcard hosts opens your backend to Host Header Poisoning (password reset hijacking) and reverse-proxy cache poisoning.
5. **Enable `CONN_MAX_AGE > 0` in Production**:
   Default `CONN_MAX_AGE = 0` establishes and terminates a TCP + TLS connection on every single request. Set to `600` (10 minutes) behind Gunicorn to avoid database CPU spikes.

---

## 4. Production Security Hardening Checklist

```python
# config/settings/production.py
DEBUG = False

# HTTP & Transport Security
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HTTP Strict Transport Security (HSTS)
SECURE_HSTS_SECONDS = 31536000             # 1 Year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Browser Security Directives
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

# Persistent Database Pooling
DATABASES["default"]["CONN_MAX_AGE"] = 600
```

---

## 5. Essential Django CLI Commands

```bash
# Verify security configuration before production deployment (Fails on critical errors)
python manage.py check --deploy

# Compare active settings against Django defaults
python manage.py diffsettings --all

# Run test suite with fast testing settings
python manage.py test --settings=config.settings.testing

# Show active migrations and their application status
python manage.py showmigrations
```
