"""
Day 32: Example 02 - Production Django Settings & Security Engine
==================================================================

This module simulates an enterprise-grade Django project configuration pipeline,
including settings inheritance (Base -> Development / Production / Testing),
dynamic installed apps resolution, logging `dictConfig` generation, and an automated
implementation of the Django System Check Framework (`python manage.py check --deploy`).

WHY THIS ARCHITECTURE MATTERS:
1. Object-Oriented Configuration Composition: While Django natively evaluates settings
   as module-level global variables, enterprise architectures model environments
   as inheriting classes or modular composable dictionaries. This avoids code
   duplication and prevents staging/production divergence.
2. Fast Test Execution (MD5 Password Hasher): Production Django uses Argon2 or PBKDF2
   with 600,000 iterations to resist brute-force attacks. In a test suite creating
   thousands of test users, this causes catastrophic slowdowns. Testing settings
   substitute an MD5 hasher, speeding up test suites by 10x-50x.
3. System Check Framework (`check --deploy`): Production CI/CD pipelines must run
   automated static audits on the final compiled settings dictionary before deployment.
   Catching missing SSL redirects, missing HSTS, or weak secret keys at build time
   prevents security breaches before containers are deployed to Kubernetes.
4. Structured Production Logging: Default console printing loses context in distributed
   clusters. The configuration engine generates a standardized `dictConfig` format
   routing errors to Sentry/cloud handlers and filtering logs based on DEBUG status.
"""

from __future__ import annotations

import enum
import time
from typing import Any, Dict, List, Optional, Set, Tuple


# -----------------------------------------------------------------------------
# System Check Framework Emulation (Django System Checks)
# -----------------------------------------------------------------------------

class CheckLevel(enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class CheckMessage:
    """
    Represents an audit finding produced by the System Check Framework.
    Corresponds to Django's `django.core.checks.CheckMessage`.
    """
    def __init__(
        self,
        level: CheckLevel,
        msg: str,
        hint: Optional[str] = None,
        obj: Optional[str] = None,
        id: Optional[str] = None,
    ):
        self.level = level
        self.msg = msg
        self.hint = hint
        self.obj = obj
        self.id = id

    def __repr__(self) -> str:
        return f"<{self.level.value}: ({self.id}) {self.msg}>"


# -----------------------------------------------------------------------------
# Settings Hierarchy Classes
# -----------------------------------------------------------------------------

class BaseSettings:
    """
    Universal configuration shared across all deployment targets (Local, Test, Staging, Prod).
    """
    
    # 1. Base Paths & Application Identity
    PROJECT_NAME: str = "EnterpriseCore"
    TIME_ZONE: str = "UTC"
    USE_I18N: bool = True
    USE_TZ: bool = True
    
    # 2. Universal Installed Apps
    DJANGO_APPS: List[str] = [
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
    ]
    
    THIRD_PARTY_APPS: List[str] = [
        "rest_framework",
        "corsheaders",
    ]
    
    LOCAL_APPS: List[str] = [
        "apps.core",
        "apps.users",
    ]
    
    # 3. Middleware Pipeline (Order is critical for security!)
    MIDDLEWARE: List[str] = [
        "django.middleware.security.SecurityMiddleware",
        "corsheaders.middleware.CorsMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ]
    
    ROOT_URLCONF: str = "config.urls"
    WSGI_APPLICATION: str = "config.wsgi.application"
    ASGI_APPLICATION: str = "config.asgi.application"
    
    # 4. Custom User Model Invariant (Crucial Day 1 Requirement)
    AUTH_USER_MODEL: str = "users.User"
    
    # 5. Password Validation Rules
    AUTH_PASSWORD_VALIDATORS: List[Dict[str, str]] = [
        {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
        {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
        {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    ]
    
    # 6. Default Hashers (Production-grade Argon2 + PBKDF2)
    PASSWORD_HASHERS: List[str] = [
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    ]

    @property
    def INSTALLED_APPS(self) -> List[str]:
        """Combine Django apps, third-party libraries, and local domain apps."""
        return self.DJANGO_APPS + self.THIRD_PARTY_APPS + self.LOCAL_APPS

    def to_dict(self) -> Dict[str, Any]:
        """Export all uppercase configuration variables as a standard dictionary."""
        settings_dict = {}
        for attr in dir(self):
            if attr.isupper():
                settings_dict[attr] = getattr(self, attr)
        return settings_dict


class DevelopmentSettings(BaseSettings):
    """
    Settings tailored for local developer productivity and instant introspection.
    """
    DEBUG: bool = True
    SECRET_KEY: str = "django-insecure-dev-local-secret-key-not-for-production-use-00123"
    ALLOWED_HOSTS: List[str] = ["localhost", "127.0.0.1", "[::1]"]
    
    # Database: Local developer SQLite
    DATABASES: Dict[str, Any] = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": "dev_db.sqlite3",
        }
    }
    
    # Developer Tooling: Django Debug Toolbar
    THIRD_PARTY_APPS: List[str] = BaseSettings.THIRD_PARTY_APPS + ["debug_toolbar"]
    MIDDLEWARE: List[str] = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + BaseSettings.MIDDLEWARE
    INTERNAL_IPS: List[str] = ["127.0.0.1"]
    
    # Email: Print to console rather than sending real emails
    EMAIL_BACKEND: str = "django.core.mail.backends.console.EmailBackend"
    
    # CORS: Allow all origins during local frontend development
    CORS_ALLOW_ALL_ORIGINS: bool = True


class TestingSettings(BaseSettings):
    """
    Settings optimized for speed and deterministic isolation in CI/CD pipelines.
    """
    DEBUG: bool = False
    SECRET_KEY: str = "django-insecure-testing-pipeline-isolated-mock-secret-key-12345"
    ALLOWED_HOSTS: List[str] = ["testserver", "localhost"]
    
    # Fast In-Memory Database
    DATABASES: Dict[str, Any] = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
    
    # WHY: PBKDF2/Argon2 hashing is computationally expensive by design.
    # Replacing it with MD5 in tests cuts test suite runtime by up to 90%!
    PASSWORD_HASHERS: List[str] = [
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]
    
    # Fast In-Memory Cache
    CACHES: Dict[str, Any] = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-cache",
        }
    }
    
    # Email: Keep messages in in-memory outbox
    EMAIL_BACKEND: str = "django.core.mail.backends.locmem.EmailBackend"


class ProductionSettings(BaseSettings):
    """
    Hardened enterprise production settings. Enforces strict transport security,
    connection pooling, secure cookies, and external logging.
    """
    def __init__(
        self,
        secret_key: str,
        allowed_hosts: List[str],
        db_config: Dict[str, Any],
        enable_ssl_redirect: bool = True,
    ):
        super().__init__()
        self.DEBUG: bool = False
        self.SECRET_KEY: str = secret_key
        self.ALLOWED_HOSTS: List[str] = allowed_hosts
        self.DATABASES: Dict[str, Any] = {"default": db_config}
        
        # Production HTTP & Transport Security Hardening
        self.SECURE_SSL_REDIRECT: bool = enable_ssl_redirect
        self.SESSION_COOKIE_SECURE: bool = True
        self.CSRF_COOKIE_SECURE: bool = True
        self.SECURE_HSTS_SECONDS: int = 31536000  # 1 Full Year
        self.SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = True
        self.SECURE_HSTS_PRELOAD: bool = True
        self.SECURE_CONTENT_TYPE_NOSNIFF: bool = True
        self.SECURE_BROWSER_XSS_FILTER: bool = True
        self.X_FRAME_OPTIONS: str = "DENY"
        
        # Production Database Connection Pooling
        # CONN_MAX_AGE keeps connections open across requests, avoiding TLS handshakes
        if "CONN_MAX_AGE" not in self.DATABASES["default"]:
            self.DATABASES["default"]["CONN_MAX_AGE"] = 600
        
        # Production Logging Architecture
        self.LOGGING: Dict[str, Any] = self._generate_production_logging()

    def _generate_production_logging(self) -> Dict[str, Any]:
        """
        Build production Python dictConfig structure.
        Routes errors to stdout as structured logs formatted for cloud log routers.
        """
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "verbose": {
                    "format": "[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] - %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "simple": {
                    "format": "%(levelname)s %(message)s",
                },
            },
            "filters": {
                "require_debug_false": {
                    "()": "django.utils.log.RequireDebugFalse",
                },
            },
            "handlers": {
                "console": {
                    "level": "INFO",
                    "class": "logging.StreamHandler",
                    "formatter": "verbose",
                },
                "mail_admins": {
                    "level": "ERROR",
                    "filters": ["require_debug_false"],
                    "class": "django.utils.log.AdminEmailHandler",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": "INFO",
            },
            "loggers": {
                "django": {
                    "handlers": ["console"],
                    "level": "INFO",
                    "propagate": False,
                },
                "django.request": {
                    "handlers": ["console", "mail_admins"],
                    "level": "ERROR",
                    "propagate": False,
                },
                "apps": {
                    "handlers": ["console"],
                    "level": "DEBUG",
                    "propagate": False,
                },
            },
        }


# -----------------------------------------------------------------------------
# Production Security Deploy Audit Framework (`check --deploy`)
# -----------------------------------------------------------------------------

class DeploymentSecurityAuditor:
    """
    Simulates Django's internal `django.core.checks.security` deploy checks.
    Audits an instantiated settings dictionary against enterprise security rules.
    """
    
    @classmethod
    def audit(cls, settings: Dict[str, Any]) -> List[CheckMessage]:
        findings: List[CheckMessage] = []
        
        # 1. Audit DEBUG Flag
        # Reference: security.W018
        if settings.get("DEBUG") is True:
            findings.append(
                CheckMessage(
                    level=CheckLevel.CRITICAL,
                    id="security.C018",
                    msg="You must not run Django with DEBUG = True in production.",
                    hint="Set DJANGO_DEBUG=False in your deployment environment.",
                    obj="settings.DEBUG",
                )
            )
        
        # 2. Audit Secret Key Strength & Insecure Defaults
        # Reference: security.W009
        secret_key = str(settings.get("SECRET_KEY", ""))
        if not secret_key:
            findings.append(
                CheckMessage(
                    level=CheckLevel.CRITICAL,
                    id="security.C009",
                    msg="SECRET_KEY is empty or missing.",
                    hint="Generate a strong cryptographic secret key with >= 50 characters.",
                    obj="settings.SECRET_KEY",
                )
            )
        elif secret_key.startswith("django-insecure-") or len(secret_key) < 40:
            findings.append(
                CheckMessage(
                    level=CheckLevel.ERROR,
                    id="security.E009",
                    msg="Your SECRET_KEY is insecure or using an autogenerated development placeholder.",
                    hint="Use a cryptographically secure random string with minimum 50 characters.",
                    obj="settings.SECRET_KEY",
                )
            )
            
        # 3. Audit ALLOWED_HOSTS
        # Reference: security.W020
        allowed_hosts = settings.get("ALLOWED_HOSTS", [])
        if not allowed_hosts:
            findings.append(
                CheckMessage(
                    level=CheckLevel.CRITICAL,
                    id="security.C020",
                    msg="ALLOWED_HOSTS is empty. Django will refuse all incoming HTTP Host headers.",
                    hint="Specify exact domain names (e.g. ['api.domain.com']).",
                    obj="settings.ALLOWED_HOSTS",
                )
            )
        elif "*" in allowed_hosts:
            findings.append(
                CheckMessage(
                    level=CheckLevel.WARNING,
                    id="security.W020",
                    msg="ALLOWED_HOSTS contains wildcard '*'. This enables Host Header Poisoning attacks.",
                    hint="Remove '*' and whitelist only verified domain names.",
                    obj="settings.ALLOWED_HOSTS",
                )
            )
            
        # 4. Audit SSL/TLS Redirection
        # Reference: security.W008
        if not settings.get("SECURE_SSL_REDIRECT"):
            findings.append(
                CheckMessage(
                    level=CheckLevel.WARNING,
                    id="security.W008",
                    msg="SECURE_SSL_REDIRECT is not set to True. Unencrypted HTTP traffic will not be redirected to HTTPS.",
                    hint="Set SECURE_SSL_REDIRECT = True in production.",
                    obj="settings.SECURE_SSL_REDIRECT",
                )
            )
            
        # 5. Audit Secure Session & CSRF Cookies
        # Reference: security.W012 & security.W016
        if not settings.get("SESSION_COOKIE_SECURE"):
            findings.append(
                CheckMessage(
                    level=CheckLevel.ERROR,
                    id="security.E012",
                    msg="SESSION_COOKIE_SECURE is not True. Session cookies will be transmitted over insecure HTTP.",
                    hint="Set SESSION_COOKIE_SECURE = True so cookies are only sent over TLS.",
                    obj="settings.SESSION_COOKIE_SECURE",
                )
            )
            
        if not settings.get("CSRF_COOKIE_SECURE"):
            findings.append(
                CheckMessage(
                    level=CheckLevel.ERROR,
                    id="security.E016",
                    msg="CSRF_COOKIE_SECURE is not True. CSRF tokens can be intercepted by network eavesdroppers.",
                    hint="Set CSRF_COOKIE_SECURE = True in production.",
                    obj="settings.CSRF_COOKIE_SECURE",
                )
            )
            
        # 6. Audit HSTS Header (HTTP Strict Transport Security)
        # Reference: security.W004
        hsts_seconds = settings.get("SECURE_HSTS_SECONDS", 0)
        if hsts_seconds <= 0:
            findings.append(
                CheckMessage(
                    level=CheckLevel.WARNING,
                    id="security.W004",
                    msg="SECURE_HSTS_SECONDS is not set or is 0. Browsers will not enforce HTTPS connections.",
                    hint="Set SECURE_HSTS_SECONDS = 31536000 (1 year) with include_subdomains=True.",
                    obj="settings.SECURE_HSTS_SECONDS",
                )
            )
            
        # 7. Audit Database Connection Pooling
        databases = settings.get("DATABASES", {})
        default_db = databases.get("default", {})
        conn_max_age = default_db.get("CONN_MAX_AGE", 0)
        if conn_max_age == 0:
            findings.append(
                CheckMessage(
                    level=CheckLevel.INFO,
                    id="perf.I001",
                    msg="CONN_MAX_AGE is 0. A new database TCP & TLS connection will be opened and closed on every request.",
                    hint="Set CONN_MAX_AGE = 600 or configure an external connection pooler (e.g. PgBouncer).",
                    obj="settings.DATABASES['default']['CONN_MAX_AGE']",
                )
            )
            
        # 8. Audit Custom User Model Invariant
        auth_user_model = settings.get("AUTH_USER_MODEL")
        if not auth_user_model or auth_user_model == "auth.User":
            findings.append(
                CheckMessage(
                    level=CheckLevel.WARNING,
                    id="arch.W001",
                    msg="AUTH_USER_MODEL is defaulting to 'auth.User'.",
                    hint="Declare a custom User model (e.g. 'users.User') before initial migration.",
                    obj="settings.AUTH_USER_MODEL",
                )
            )
            
        return findings


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 32 Modular Settings & Deploy Audit Test Suite...")
    
    # 1. Test Base Settings Composition
    base = BaseSettings()
    assert "django.contrib.auth" in base.INSTALLED_APPS
    assert "apps.users" in base.INSTALLED_APPS
    assert base.AUTH_USER_MODEL == "users.User"
    assert "django.middleware.security.SecurityMiddleware" == base.MIDDLEWARE[0]
    print("  [+] BaseSettings composition verified.")
    
    # 2. Test Development Settings
    dev = DevelopmentSettings()
    dev_dict = dev.to_dict()
    assert dev_dict["DEBUG"] is True
    assert "debug_toolbar" in dev_dict["INSTALLED_APPS"]
    assert dev_dict["EMAIL_BACKEND"] == "django.core.mail.backends.console.EmailBackend"
    print("  [+] DevelopmentSettings inheritance and tool injection verified.")
    
    # 3. Test Testing Settings (Fast Password Hasher Check)
    test_settings = TestingSettings()
    test_dict = test_settings.to_dict()
    assert test_dict["DEBUG"] is False
    assert test_dict["DATABASES"]["default"]["NAME"] == ":memory:"
    assert test_dict["PASSWORD_HASHERS"][0] == "django.contrib.auth.hashers.MD5PasswordHasher"
    print("  [+] TestingSettings MD5Hasher and in-memory isolation verified.")
    
    # 4. Test Production Settings
    prod_db = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "prod_db",
        "USER": "db_user",
        "PASSWORD": "strong_random_password_99!",
        "HOST": "postgres-cluster.internal",
        "PORT": 5432,
        "CONN_MAX_AGE": 600,
    }
    strong_key = "k9#vL$2!xP8@mN5^qR1*tW4(yU7)oZ3_eB6+dF0=sC#a1b2c3d4e5f6g7h8i9j0"
    prod = ProductionSettings(
        secret_key=strong_key,
        allowed_hosts=["api.enterprise.com", "app.enterprise.com"],
        db_config=prod_db,
    )
    prod_dict = prod.to_dict()
    assert prod_dict["DEBUG"] is False
    assert prod_dict["SECURE_SSL_REDIRECT"] is True
    assert prod_dict["SESSION_COOKIE_SECURE"] is True
    assert prod_dict["SECURE_HSTS_SECONDS"] == 31536000
    assert "LOGGING" in prod_dict
    assert prod_dict["LOGGING"]["version"] == 1
    print("  [+] ProductionSettings hardened security directives verified.")
    
    # 5. Run Deployment Security Auditor on Clean Production Settings
    prod_findings = DeploymentSecurityAuditor.audit(prod_dict)
    # Ensure there are NO CRITICAL, ERROR, or WARNING findings
    errors_and_warnings = [
        f for f in prod_findings if f.level in (CheckLevel.CRITICAL, CheckLevel.ERROR, CheckLevel.WARNING)
    ]
    assert len(errors_and_warnings) == 0, f"Unexpected findings: {errors_and_warnings}"
    print("  [+] Clean production settings passed deploy audit with 0 warnings/errors.")
    
    # 6. Run Deployment Security Auditor on Insecure Settings
    insecure_settings = {
        "DEBUG": True,
        "SECRET_KEY": "django-insecure-bad-key",
        "ALLOWED_HOSTS": ["*"],
        "SECURE_SSL_REDIRECT": False,
        "SESSION_COOKIE_SECURE": False,
        "CSRF_COOKIE_SECURE": False,
        "SECURE_HSTS_SECONDS": 0,
        "DATABASES": {"default": {"CONN_MAX_AGE": 0}},
        "AUTH_USER_MODEL": "auth.User",
    }
    insecure_findings = DeploymentSecurityAuditor.audit(insecure_settings)
    finding_ids = {f.id for f in insecure_findings}
    
    assert "security.C018" in finding_ids  # DEBUG=True in prod
    assert "security.E009" in finding_ids  # django-insecure secret key
    assert "security.W020" in finding_ids  # Wildcard ALLOWED_HOSTS
    assert "security.W008" in finding_ids  # No SSL redirect
    assert "security.E012" in finding_ids  # Insecure session cookie
    assert "security.E016" in finding_ids  # Insecure CSRF cookie
    assert "security.W004" in finding_ids  # No HSTS
    assert "perf.I001" in finding_ids      # CONN_MAX_AGE = 0
    assert "arch.W001" in finding_ids      # Default auth.User model
    print(f"  [+] Insecure audit successfully trapped {len(insecure_findings)} architectural defects.")


def benchmark_settings_engine(iterations: int = 50_000) -> None:
    """Benchmark full modular settings resolution and deployment audit execution."""
    prod_db = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "prod_db",
        "USER": "db_user",
        "PASSWORD": "strong_random_password_99!",
        "HOST": "postgres-cluster.internal",
        "PORT": 5432,
        "CONN_MAX_AGE": 600,
    }
    strong_key = "k9#vL$2!xP8@mN5^qR1*tW4(yU7)oZ3_eB6+dF0=sC#a1b2c3d4e5f6g7h8i9j0"
    
    start_time = time.perf_counter()
    for _ in range(iterations):
        prod = ProductionSettings(
            secret_key=strong_key,
            allowed_hosts=["api.enterprise.com"],
            db_config=prod_db,
        )
        settings_dict = prod.to_dict()
        _ = DeploymentSecurityAuditor.audit(settings_dict)
    elapsed = time.perf_counter() - start_time
    
    latency_us = (elapsed / iterations) * 1_000_000
    ops_per_sec = iterations / elapsed
    
    print("\n" + "=" * 65)
    print("DAY 32: SETTINGS ENGINE & DEPLOY AUDIT BENCHMARK")
    print("=" * 65)
    print(f"Iterations:        {iterations:,}")
    print(f"Total Time:        {elapsed:.4f} seconds")
    print(f"Average Latency:   {latency_us:.2f} microseconds per cycle")
    print(f"Throughput:        {ops_per_sec:,.0f} audit cycles/sec")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_settings_engine()
