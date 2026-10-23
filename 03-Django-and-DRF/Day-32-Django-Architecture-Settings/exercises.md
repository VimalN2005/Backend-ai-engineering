# Day 32: Architecture & Settings Modularization Exercises

Practice and master Django project architecture, multi-environment configuration pipelines, database pooling configurations, and deployment security auditing.

---

## Exercise 1: Multi-Environment Settings Resolver & Dynamic Importer

### Problem Statement:
Design an extensible, pure-Python settings resolver that dynamically loads and composes settings dictionaries across three tiers:
1. `BaseConfig`: Universal project invariants (apps, middleware, auth models).
2. `TargetConfig`: Environment-specific overrides (`development`, `staging`, `production`, `testing`).
3. `EnvOverride`: Dynamic runtime overrides sourced from OS environment variables.

To prevent accidental runtime mutations by rogue middlewares or views, the final resolved settings object must be rendered **immutable** using `types.MappingProxyType`.

### Constraints:
- Pure Python standard library (no third-party dependencies).
- Deep merge nested dictionaries (e.g. `DATABASES['default']` must preserve base options while allowing environment overrides).
- Raise `ValueError` if the specified target environment is unrecognized.
- Return an immutable read-only mapping.

### Complete Solution:

```python
"""
Exercise 1: Multi-Environment Settings Resolver & Dynamic Importer
"""

from __future__ import annotations

import os
from types import MappingProxyType
from typing import Any, Dict


class SettingsResolver:
    """Dynamically resolves, merges, and freezes multi-tier Django settings."""
    
    BASE_CONFIG: Dict[str, Any] = {
        "PROJECT_NAME": "FinTechBackend",
        "AUTH_USER_MODEL": "users.User",
        "INSTALLED_APPS": [
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "apps.core",
            "apps.users",
        ],
        "DATABASES": {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "core_db",
                "OPTIONS": {"connect_timeout": 10},
            }
        },
    }
    
    ENVIRONMENTS: Dict[str, Dict[str, Any]] = {
        "development": {
            "DEBUG": True,
            "ALLOWED_HOSTS": ["localhost", "127.0.0.1"],
            "DATABASES": {
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": "dev.sqlite3",
                }
            },
        },
        "testing": {
            "DEBUG": False,
            "ALLOWED_HOSTS": ["testserver"],
            "DATABASES": {
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
        },
        "production": {
            "DEBUG": False,
            "ALLOWED_HOSTS": ["api.fintech.corp"],
            "SECURE_SSL_REDIRECT": True,
            "SESSION_COOKIE_SECURE": True,
            "CSRF_COOKIE_SECURE": True,
            "SECURE_HSTS_SECONDS": 31536000,
        },
    }

    @classmethod
    def _deep_merge(cls, target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merge source dictionary into target dictionary."""
        result = target.copy()
        for key, value in source.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = cls._deep_merge(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                # Unique append for list configurations (e.g. INSTALLED_APPS)
                combined = list(result[key])
                for item in value:
                    if item not in combined:
                        combined.append(item)
                result[key] = combined
            else:
                result[key] = value
        return result

    @classmethod
    def resolve(
        cls,
        environment: str,
        runtime_overrides: Dict[str, Any] | None = None,
    ) -> MappingProxyType:
        """
        Resolve the final settings mapping for the specified environment.
        Returns an immutable MappingProxyType.
        """
        env_key = environment.lower().strip()
        if env_key not in cls.ENVIRONMENTS:
            raise ValueError(
                f"Invalid environment '{environment}'. Valid options: {list(cls.ENVIRONMENTS.keys())}"
            )
        
        # 1. Start with base config
        merged = cls.BASE_CONFIG.copy()
        
        # 2. Merge environment-specific settings
        env_overrides = cls.ENVIRONMENTS[env_key]
        merged = cls._deep_merge(merged, env_overrides)
        
        # 3. Merge runtime dynamic overrides
        if runtime_overrides:
            merged = cls._deep_merge(merged, runtime_overrides)
            
        # 4. Return immutable read-only proxy
        return MappingProxyType(merged)


def test_settings_resolver() -> None:
    print("[*] Testing SettingsResolver...")
    
    # Test Development Resolution
    dev_settings = SettingsResolver.resolve("development")
    assert dev_settings["DEBUG"] is True
    assert dev_settings["DATABASES"]["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert "apps.core" in dev_settings["INSTALLED_APPS"]
    
    # Test Testing Fast Hasher
    test_settings = SettingsResolver.resolve("testing")
    assert test_settings["PASSWORD_HASHERS"] == ["django.contrib.auth.hashers.MD5PasswordHasher"]
    assert test_settings["DATABASES"]["default"]["NAME"] == ":memory:"
    
    # Test Immutability
    try:
        test_settings["DEBUG"] = True  # type: ignore
        assert False, "Should have raised TypeError for mutating MappingProxyType"
    except TypeError:
        pass
    
    # Test Invalid Environment
    try:
        SettingsResolver.resolve("staging_invalid")
        assert False, "Should have rejected unrecognized environment"
    except ValueError:
        pass
        
    print("  [+] SettingsResolver passed all tests successfully.")


if __name__ == "__main__":
    test_settings_resolver()
```

---

## Exercise 2: Strict Twelve-Factor Database Connection Pool & Failover URL Parser

### Problem Statement:
Enterprise Django backends commonly deploy read-replicas to offload read-heavy database traffic. Write queries target the primary database while reporting and read-only endpoints target a replica.

Implement a database configuration generator `generate_database_config(primary_url: str, replica_url: str | None = None)` that:
1. Decomposes URI connection strings into Django `DATABASES['default']` and optional `DATABASES['replica']`.
2. Automatically parses query parameters from the URI (e.g. `?sslmode=require&application_name=django_api`) into the nested `OPTIONS` dictionary.
3. Automatically sets `CONN_MAX_AGE` (default: 600s) and connect timeouts.
4. Raises a `ValueError` if the scheme is not `postgres` or `postgresql`.

### Constraints:
- Pure standard library (`urllib.parse`).
- Automatically unquotes passwords containing special characters (e.g. `%40` -> `@`).
- Fully typed with docstrings.

### Complete Solution:

```python
"""
Exercise 2: Strict Twelve-Factor Database Connection Pool & Failover URL Parser
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse
from typing import Any, Dict, Optional


def parse_db_uri(uri: str, default_conn_max_age: int = 600) -> Dict[str, Any]:
    """Parse a single database URI into a Django database config dictionary."""
    if not uri or not uri.strip():
        raise ValueError("Database URI string cannot be empty.")
        
    parsed = urlparse(uri.strip())
    scheme = parsed.scheme.lower()
    
    if scheme not in ("postgres", "postgresql", "pgsql"):
        raise ValueError(
            f"Unsupported scheme '{scheme}'. Only postgresql connections supported."
        )
        
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError(f"Database name missing in URI: '{uri}'")
        
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    
    # Parse query parameters (e.g. ?sslmode=require&connect_timeout=5)
    query_params = parse_qs(parsed.query)
    options: Dict[str, Any] = {"connect_timeout": 10}
    
    for key, values in query_params.items():
        if values:
            options[key] = values[0]  # Take first query parameter value
            
    # Extract conn_max_age from query if provided, else use default
    conn_max_age = default_conn_max_age
    if "conn_max_age" in options:
        conn_max_age = int(options.pop("conn_max_age"))
        
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": db_name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": port,
        "CONN_MAX_AGE": conn_max_age,
        "OPTIONS": options,
    }


def generate_database_config(
    primary_url: str,
    replica_url: Optional[str] = None,
    conn_max_age: int = 600,
) -> Dict[str, Dict[str, Any]]:
    """
    Generate complete Django DATABASES configuration containing 'default' (write primary)
    and optional 'replica' (read replica).
    """
    databases: Dict[str, Dict[str, Any]] = {
        "default": parse_db_uri(primary_url, default_conn_max_age=conn_max_age)
    }
    
    if replica_url:
        databases["replica"] = parse_db_uri(replica_url, default_conn_max_age=conn_max_age)
        
    return databases


def test_database_parser() -> None:
    print("[*] Testing generate_database_config...")
    
    primary = "postgresql://app_writer:Secr%40tP%23ss@primary.db.internal:5432/finance_db?sslmode=require"
    replica = "postgresql://app_reader:ReaderPass123@replica.db.internal:5432/finance_db?sslmode=verify-full&conn_max_age=900"
    
    config = generate_database_config(primary, replica)
    
    # Verify primary
    default_db = config["default"]
    assert default_db["ENGINE"] == "django.db.backends.postgresql"
    assert default_db["USER"] == "app_writer"
    assert default_db["PASSWORD"] == "Secr@tP#ss"  # %40 -> @, %23 -> #
    assert default_db["HOST"] == "primary.db.internal"
    assert default_db["PORT"] == 5432
    assert default_db["CONN_MAX_AGE"] == 600
    assert default_db["OPTIONS"]["sslmode"] == "require"
    assert default_db["OPTIONS"]["connect_timeout"] == 10
    
    # Verify replica
    replica_db = config["replica"]
    assert replica_db["USER"] == "app_reader"
    assert replica_db["HOST"] == "replica.db.internal"
    assert replica_db["CONN_MAX_AGE"] == 900  # Overridden via query string
    assert replica_db["OPTIONS"]["sslmode"] == "verify-full"
    
    print("  [+] Database connection pool & failover parser passed all tests.")


if __name__ == "__main__":
    test_database_parser()
```

---

## Exercise 3: Automated Django Security Deploy Pre-Flight Check Runner

### Problem Statement:
In enterprise continuous integration (CI) workflows, pull requests that alter configuration must pass automated security audits before merging.

Build a modular deploy pre-flight check runner `SecurityCheckEngine` that executes 7 non-negotiable security rules:
1. `SEC01`: `DEBUG` must be `False`.
2. `SEC02`: `ALLOWED_HOSTS` must not be empty and must not contain wildcard `'*'`.
3. `SEC03`: `SECRET_KEY` length must be $\ge 50$ characters and cannot match known insecure defaults.
4. `SEC04`: `SECURE_SSL_REDIRECT` must be `True`.
5. `SEC05`: `SESSION_COOKIE_SECURE` must be `True`.
6. `SEC06`: `SECURE_HSTS_SECONDS` must be $\ge 15,768,000$ (minimum 6 months).
7. `SEC07`: `AUTH_USER_MODEL` must not be `'auth.User'` (custom user model requirement).

The engine must return an audit report containing a boolean `passed` flag, list of violations, and recommended remediation hints.

### Complete Solution:

```python
"""
Exercise 3: Automated Django Security Deploy Pre-Flight Check Runner
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class SecurityViolation:
    rule_id: str
    setting_name: str
    message: str
    remediation_hint: str


@dataclass(frozen=True)
class AuditReport:
    passed: bool
    total_checks: int
    violation_count: int
    violations: List[SecurityViolation]


class SecurityCheckEngine:
    """Executes pre-flight deployment security audits against Django settings."""
    
    @classmethod
    def audit(cls, settings: Dict[str, Any]) -> AuditReport:
        violations: List[SecurityViolation] = []
        total_checks = 7
        
        # SEC01: DEBUG
        if settings.get("DEBUG") is not False:
            violations.append(
                SecurityViolation(
                    rule_id="SEC01",
                    setting_name="DEBUG",
                    message="DEBUG must be explicitly set to False in production.",
                    remediation_hint="Set DEBUG = False in production settings.",
                )
            )
            
        # SEC02: ALLOWED_HOSTS
        allowed_hosts = settings.get("ALLOWED_HOSTS", [])
        if not allowed_hosts or "*" in allowed_hosts:
            violations.append(
                SecurityViolation(
                    rule_id="SEC02",
                    setting_name="ALLOWED_HOSTS",
                    message="ALLOWED_HOSTS cannot be empty or include wildcard '*'.",
                    remediation_hint="Provide exact domain names or wildcard subdomains (e.g. '.company.com').",
                )
            )
            
        # SEC03: SECRET_KEY
        secret_key = str(settings.get("SECRET_KEY", ""))
        if len(secret_key) < 50 or secret_key.startswith("django-insecure-"):
            violations.append(
                SecurityViolation(
                    rule_id="SEC03",
                    setting_name="SECRET_KEY",
                    message="SECRET_KEY is too short or uses insecure placeholder.",
                    remediation_hint="Generate a 50+ character high-entropy secret key.",
                )
            )
            
        # SEC04: SECURE_SSL_REDIRECT
        if not settings.get("SECURE_SSL_REDIRECT"):
            violations.append(
                SecurityViolation(
                    rule_id="SEC04",
                    setting_name="SECURE_SSL_REDIRECT",
                    message="SECURE_SSL_REDIRECT is not enabled.",
                    remediation_hint="Set SECURE_SSL_REDIRECT = True to force HTTPS.",
                )
            )
            
        # SEC05: SESSION_COOKIE_SECURE
        if not settings.get("SESSION_COOKIE_SECURE"):
            violations.append(
                SecurityViolation(
                    rule_id="SEC05",
                    setting_name="SESSION_COOKIE_SECURE",
                    message="SESSION_COOKIE_SECURE is not enabled.",
                    remediation_hint="Set SESSION_COOKIE_SECURE = True to restrict cookie transmission to TLS.",
                )
            )
            
        # SEC06: SECURE_HSTS_SECONDS
        hsts_seconds = settings.get("SECURE_HSTS_SECONDS", 0)
        if hsts_seconds < 15_768_000:
            violations.append(
                SecurityViolation(
                    rule_id="SEC06",
                    setting_name="SECURE_HSTS_SECONDS",
                    message="SECURE_HSTS_SECONDS must be >= 15,768,000 (minimum 6 months).",
                    remediation_hint="Set SECURE_HSTS_SECONDS = 31536000 (1 year).",
                )
            )
            
        # SEC07: AUTH_USER_MODEL
        auth_model = settings.get("AUTH_USER_MODEL")
        if not auth_model or auth_model == "auth.User":
            violations.append(
                SecurityViolation(
                    rule_id="SEC07",
                    setting_name="AUTH_USER_MODEL",
                    message="AUTH_USER_MODEL is defaulting to core 'auth.User'.",
                    remediation_hint="Declare a custom User model (e.g. 'users.User') to prevent future migration deadlock.",
                )
            )
            
        passed = len(violations) == 0
        return AuditReport(
            passed=passed,
            total_checks=total_checks,
            violation_count=len(violations),
            violations=violations,
        )


def test_security_engine() -> None:
    print("[*] Testing SecurityCheckEngine...")
    
    # 1. Clean Production Config
    valid_prod = {
        "DEBUG": False,
        "ALLOWED_HOSTS": ["api.company.com"],
        "SECRET_KEY": "x9$vL#2!qP8@mN5*tR1(wU7^yO3_eB6+dF0=sC#991jKh@1920zLaQ!8831",
        "SECURE_SSL_REDIRECT": True,
        "SESSION_COOKIE_SECURE": True,
        "SECURE_HSTS_SECONDS": 31536000,
        "AUTH_USER_MODEL": "users.User",
    }
    report = SecurityCheckEngine.audit(valid_prod)
    assert report.passed is True
    assert report.violation_count == 0
    print("  [+] Valid configuration passed security audit.")
    
    # 2. Insecure Config
    insecure = {
        "DEBUG": True,
        "ALLOWED_HOSTS": ["*"],
        "SECRET_KEY": "django-insecure-short",
        "SECURE_SSL_REDIRECT": False,
        "SESSION_COOKIE_SECURE": False,
        "SECURE_HSTS_SECONDS": 0,
        "AUTH_USER_MODEL": "auth.User",
    }
    fail_report = SecurityCheckEngine.audit(insecure)
    assert fail_report.passed is False
    assert fail_report.violation_count == 7
    violated_rules = {v.rule_id for v in fail_report.violations}
    assert violated_rules == {"SEC01", "SEC02", "SEC03", "SEC04", "SEC05", "SEC06", "SEC07"}
    print("  [+] Insecure configuration caught all 7 violations.")


if __name__ == "__main__":
    test_security_engine()
```
