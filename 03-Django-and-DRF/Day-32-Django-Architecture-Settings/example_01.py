"""
Day 32: Example 01 - Strict Environment-Based Django Settings Loader
=====================================================================

This module implements a production-grade, pure Python environment configuration
loader designed for Django applications adhering to Twelve-Factor App methodology.

WHY THIS ARCHITECTURE MATTERS:
1. Fail-Fast Reliability: Applications should never start up in a semi-configured
   state. If a required database password, secret key, or API token is missing,
   the process must crash immediately during configuration bootstrapping, before
   binding network ports or accepting traffic.
2. Strict Type Coercion: Operating system environment variables are always strings.
   Evaluating "False" as a boolean in Python (`bool("False")`) evaluates to `True`!
   Explicit type casting eliminates critical security misconfigurations such as
   accidentally keeping `DEBUG=True` in production.
3. Secret Entropy Assurance: Insecure or default secret keys compromise cryptographic
   signing (sessions, password resets, CSRF tokens). This loader validates minimum
   entropy and forbidden fallback patterns.
4. Database URL Decomposition: Standardizes 12-factor database strings (e.g.
   `postgres://user:pass@host:5432/dbname`) into Django's multi-layered
   `DATABASES` dictionary schema without third-party dependencies (like dj-database-url).
"""

from __future__ import annotations

import os
import re
import math
import time
from urllib.parse import urlparse, unquote
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

T = TypeVar("T")


class ConfigurationError(Exception):
    """Raised when an environment variable is missing, invalid, or insecure."""
    pass


class MissingEnvironmentVariableError(ConfigurationError):
    """Raised when a non-optional environment variable is not defined."""
    pass


class InvalidTypeError(ConfigurationError):
    """Raised when an environment variable cannot be cast to the expected type."""
    pass


class InsecureSecretKeyError(ConfigurationError):
    """Raised when DJANGO_SECRET_KEY fails entropy or security checks."""
    pass


# -----------------------------------------------------------------------------
# Type Parsers & Cast Utilities
# -----------------------------------------------------------------------------

def parse_bool(val: Union[str, bool]) -> bool:
    """
    Strictly parse a string representation of a boolean.
    
    WHY: In Python, `bool("False") == True` because non-empty strings are truthy.
    Enterprise configurations must parse explicit true/false keywords strictly,
    raising an error if ambiguous input (e.g. "maybe", "2") is supplied.
    """
    if isinstance(val, bool):
        return val
    
    normalized = val.strip().lower()
    if normalized in ("true", "1", "yes", "on", "t"):
        return True
    elif normalized in ("false", "0", "no", "off", "f"):
        return False
    
    raise InvalidTypeError(
        f"Value '{val}' cannot be parsed as a boolean. Expected true/false, 1/0, yes/no, on/off."
    )


def parse_int(val: Union[str, int]) -> int:
    """Strictly convert string to integer, preserving negative values."""
    if isinstance(val, int):
        return val
    try:
        return int(val.strip())
    except ValueError as exc:
        raise InvalidTypeError(f"Value '{val}' cannot be parsed as an integer.") from exc


def parse_list(val: Union[str, List[str]], delimiter: str = ",") -> List[str]:
    """
    Parse a delimiter-separated string into a trimmed, non-empty list of strings.
    
    WHY: Common Django settings like `ALLOWED_HOSTS` or `CORS_ALLOWED_ORIGINS`
    are passed in environment variables as comma-separated strings (e.g. "api.domain.com,web.domain.com").
    """
    if isinstance(val, list):
        return val
    if not val or not val.strip():
        return []
    
    return [item.strip() for item in val.split(delimiter) if item.strip()]


def parse_database_url(url: str, conn_max_age: int = 600) -> Dict[str, Any]:
    """
    Decompose a standard 12-factor database connection URI into Django's
    hierarchical `DATABASES['default']` dictionary structure.
    
    Supported URI Schemes:
    - postgres:// or postgresql:// -> django.db.backends.postgresql
    - mysql:// or mysql2://       -> django.db.backends.mysql
    - sqlite://                   -> django.db.backends.sqlite3
    
    Example input:
      postgres://admin:s3cr3t_pass@db.internal.corp:5432/production_db
      
    WHY: Third-party packages like `dj-database-url` are convenient, but understanding
    the underlying URI parsing logic empowers engineers to debug URL encoding bugs
    (e.g., special characters in passwords) and avoid unnecessary container dependencies.
    """
    if not url or not url.strip():
        raise ConfigurationError("Database URL string cannot be empty.")
    
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    
    engine_map = {
        "postgres": "django.db.backends.postgresql",
        "postgresql": "django.db.backends.postgresql",
        "pgsql": "django.db.backends.postgresql",
        "mysql": "django.db.backends.mysql",
        "mysql2": "django.db.backends.mysql",
        "sqlite": "django.db.backends.sqlite3",
    }
    
    if scheme not in engine_map:
        raise ConfigurationError(
            f"Unsupported database scheme '{scheme}'. Supported schemes: {list(engine_map.keys())}"
        )
    
    engine = engine_map[scheme]
    
    # Handle SQLite database path
    if scheme == "sqlite":
        # sqlite:///absolute/path/to/db.sqlite3 or sqlite://:memory:
        db_path = parsed.path
        if parsed.netloc and parsed.netloc != "":
            # sqlite://relative_or_host/db.sqlite3
            db_path = f"{parsed.netloc}{parsed.path}"
        if not db_path:
            db_path = ":memory:"
        
        return {
            "ENGINE": engine,
            "NAME": db_path,
        }
    
    # Remote RDBMS parsing (PostgreSQL, MySQL)
    db_name = parsed.path.lstrip("/") if parsed.path else ""
    if not db_name:
        raise ConfigurationError(f"Database name missing in URI: '{url}'")
    
    # Unquote URL-encoded credentials (e.g., passwords with '@' encoded as '%40')
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    host = parsed.hostname or "localhost"
    port = parsed.port or (5432 if "postgres" in scheme else 3306)
    
    return {
        "ENGINE": engine,
        "NAME": db_name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": port,
        "CONN_MAX_AGE": conn_max_age,
        "OPTIONS": {
            # In production, explicit connect timeouts prevent request thread starvation
            "connect_timeout": 10,
        },
    }


def calculate_shannon_entropy(data: str) -> float:
    """
    Compute Shannon entropy (in bits per symbol) for a string.
    
    WHY: High entropy ensures randomness and unpredictability. For a strong
    cryptographic key, entropy should be greater than 3.5 bits/char.
    """
    if not data:
        return 0.0
    entropy = 0.0
    length = len(data)
    frequencies = {char: data.count(char) for char in set(data)}
    for count in frequencies.values():
        p_x = count / length
        entropy -= p_x * math.log2(p_x)
    return entropy


def validate_django_secret_key(secret_key: str, min_length: int = 50) -> None:
    """
    Validate that DJANGO_SECRET_KEY meets enterprise security benchmarks.
    
    Checks:
    1. Minimum length (Django defaults generate 50 characters).
    2. Prohibits trivial placeholders (e.g. 'django-insecure-...', 'secret', 'password').
    3. Requires minimum Shannon entropy (>= 3.2 bits/char) to block low-entropy patterns (e.g. 'aaaaa...').
    """
    if not secret_key:
        raise InsecureSecretKeyError("DJANGO_SECRET_KEY is empty or not provided.")
    
    if len(secret_key) < min_length:
        raise InsecureSecretKeyError(
            f"DJANGO_SECRET_KEY is too short ({len(secret_key)} chars). "
            f"Minimum required is {min_length} chars."
        )
    
    insecure_patterns = [
        r"^django-insecure-",
        r"changeme",
        r"secret",
        r"password",
        r"admin",
        r"12345",
    ]
    for pattern in insecure_patterns:
        if re.search(pattern, secret_key, re.IGNORECASE):
            raise InsecureSecretKeyError(
                f"DJANGO_SECRET_KEY contains insecure trivial pattern matching '{pattern}'."
            )
            
    entropy = calculate_shannon_entropy(secret_key)
    if entropy < 3.2:
        raise InsecureSecretKeyError(
            f"DJANGO_SECRET_KEY has insufficient Shannon entropy ({entropy:.2f} bits/char). "
            f"Must be >= 3.2 bits/char to guarantee cryptographic strength."
        )


# -----------------------------------------------------------------------------
# Environment Settings Provider
# -----------------------------------------------------------------------------

class EnvironmentConfig:
    """
    Encapsulates environment variable lookup with type safety and validation.
    
    Allows injecting mock environment dictionaries for testing isolation.
    """
    
    def __init__(self, env: Optional[Dict[str, str]] = None):
        # Default to system os.environ if no dictionary is explicitly supplied
        self._env = env if env is not None else os.environ
    
    def get(
        self,
        key: str,
        default: Optional[T] = None,
        cast: Optional[Callable[[str], T]] = None,
        required: bool = False,
    ) -> Any:
        """
        Retrieve and optionally cast an environment variable.
        
        Args:
            key: Name of the environment variable.
            default: Fallback value if variable is missing.
            cast: Callable parser to convert string to type T.
            required: If True, raises MissingEnvironmentVariableError when absent.
        """
        raw_val = self._env.get(key)
        
        if raw_val is None or raw_val.strip() == "":
            if required:
                raise MissingEnvironmentVariableError(
                    f"Mandatory environment variable '{key}' is missing or empty."
                )
            return default
        
        if cast is not None:
            return cast(raw_val)
        
        return raw_val
    
    def get_bool(self, key: str, default: bool = False, required: bool = False) -> bool:
        return self.get(key, default=default, cast=parse_bool, required=required)
    
    def get_int(self, key: str, default: int = 0, required: bool = False) -> int:
        return self.get(key, default=default, cast=parse_int, required=required)
    
    def get_list(
        self,
        key: str,
        default: Optional[List[str]] = None,
        delimiter: str = ",",
        required: bool = False,
    ) -> List[str]:
        default_val = default if default is not None else []
        return self.get(
            key,
            default=default_val,
            cast=lambda v: parse_list(v, delimiter=delimiter),
            required=required,
        )


# -----------------------------------------------------------------------------
# Production Settings Schema
# -----------------------------------------------------------------------------

class DjangoSettingsBootstrap:
    """
    Bootstraps, validates, and builds Django configuration dictionaries
    from environment variables according to environment type.
    """
    
    def __init__(self, config: EnvironmentConfig):
        self.config = config
    
    def load_settings(self) -> Dict[str, Any]:
        """
        Execute full environment loading and return a consolidated settings dict.
        """
        env_name = self.config.get("DJANGO_ENV", default="development").lower()
        is_production = env_name == "production"
        
        # 1. Critical Base Flags
        debug = self.config.get_bool("DJANGO_DEBUG", default=not is_production)
        
        # Security invariant: Production cannot run with DEBUG=True
        if is_production and debug:
            raise ConfigurationError(
                "CRITICAL SECURITY HAZARD: DJANGO_DEBUG cannot be True in 'production' environment."
            )
        
        # 2. Secret Key Extraction & Validation
        secret_key = self.config.get(
            "DJANGO_SECRET_KEY",
            default="django-insecure-local-dev-fallback-key-do-not-use-in-prod-12345" if not is_production else None,
            required=is_production,
        )
        if is_production:
            validate_django_secret_key(secret_key)
        
        # 3. Allowed Hosts
        allowed_hosts = self.config.get_list(
            "DJANGO_ALLOWED_HOSTS",
            default=["localhost", "127.0.0.1", "[::1]"] if not is_production else None,
            required=is_production,
        )
        if is_production and ("*" in allowed_hosts or len(allowed_hosts) == 0):
            raise ConfigurationError(
                "CRITICAL SECURITY HAZARD: Wildcard '*' or empty DJANGO_ALLOWED_HOSTS is forbidden in production."
            )
        
        # 4. Database Setup
        default_db_url = "sqlite:///db.sqlite3" if not is_production else None
        db_url = self.config.get("DATABASE_URL", default=default_db_url, required=is_production)
        conn_max_age = self.config.get_int("DJANGO_CONN_MAX_AGE", default=600 if is_production else 0)
        databases = {
            "default": parse_database_url(db_url, conn_max_age=conn_max_age)
        }
        
        # 5. Core Architectural Invariants
        auth_user_model = self.config.get("DJANGO_AUTH_USER_MODEL", default="users.User")
        
        # 6. Security Directives (Enforced for Production)
        security_settings = {}
        if is_production:
            security_settings = {
                "SECURE_SSL_REDIRECT": self.config.get_bool("SECURE_SSL_REDIRECT", default=True),
                "SESSION_COOKIE_SECURE": self.config.get_bool("SESSION_COOKIE_SECURE", default=True),
                "CSRF_COOKIE_SECURE": self.config.get_bool("CSRF_COOKIE_SECURE", default=True),
                "SECURE_HSTS_SECONDS": self.config.get_int("SECURE_HSTS_SECONDS", default=31536000),
                "SECURE_HSTS_INCLUDE_SUBDOMAINS": True,
                "SECURE_HSTS_PRELOAD": True,
                "SECURE_BROWSER_XSS_FILTER": True,
                "SECURE_CONTENT_TYPE_NOSNIFF": True,
                "X_FRAME_OPTIONS": "DENY",
            }
        
        return {
            "ENVIRONMENT": env_name,
            "DEBUG": debug,
            "SECRET_KEY": secret_key,
            "ALLOWED_HOSTS": allowed_hosts,
            "DATABASES": databases,
            "AUTH_USER_MODEL": auth_user_model,
            **security_settings,
        }


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    """Run comprehensive test suite validating all parsing, type conversions, and security checks."""
    print("[*] Running Day 32 Settings Loader Verification Suite...")
    
    # 1. Boolean Parsing Tests
    assert parse_bool("true") is True
    assert parse_bool("1") is True
    assert parse_bool("Yes") is True
    assert parse_bool("ON") is True
    assert parse_bool("false") is False
    assert parse_bool("0") is False
    assert parse_bool("NO") is False
    assert parse_bool("off") is False
    try:
        parse_bool("invalid_bool")
        assert False, "Should have raised InvalidTypeError"
    except InvalidTypeError:
        pass
    print("  [+] Boolean parser passed.")
    
    # 2. Integer Parsing Tests
    assert parse_int("42") == 42
    assert parse_int("-100") == -100
    try:
        parse_int("abc")
        assert False, "Should have raised InvalidTypeError"
    except InvalidTypeError:
        pass
    print("  [+] Integer parser passed.")
    
    # 3. List Parsing Tests
    assert parse_list("api.example.com, web.example.com ,  admin.example.com ") == [
        "api.example.com",
        "web.example.com",
        "admin.example.com",
    ]
    assert parse_list("") == []
    print("  [+] List parser passed.")
    
    # 4. Database URL Parsing Tests
    pg_url = "postgres://django_usr:p%40ssw0rd!@db-master.corp.internal:5432/ecommerce_prod"
    parsed_db = parse_database_url(pg_url, conn_max_age=300)
    assert parsed_db["ENGINE"] == "django.db.backends.postgresql"
    assert parsed_db["NAME"] == "ecommerce_prod"
    assert parsed_db["USER"] == "django_usr"
    assert parsed_db["PASSWORD"] == "p@ssw0rd!"  # Decoded from %40
    assert parsed_db["HOST"] == "db-master.corp.internal"
    assert parsed_db["PORT"] == 5432
    assert parsed_db["CONN_MAX_AGE"] == 300
    
    sqlite_url = "sqlite:///var/data/app.db"
    parsed_sqlite = parse_database_url(sqlite_url)
    assert parsed_sqlite["ENGINE"] == "django.db.backends.sqlite3"
    assert parsed_sqlite["NAME"] == "/var/data/app.db"
    print("  [+] Database URL decomposition passed.")
    
    # 5. Shannon Entropy & Secret Key Tests
    assert calculate_shannon_entropy("aaaaaaa") == 0.0
    random_key = "z!9k#Lp2$vQ8@mN5^xR1*tW4(yU7)oP3_eB6+dF0=sC"
    assert calculate_shannon_entropy(random_key) > 4.0
    
    # Test insecure pattern rejection
    try:
        validate_django_secret_key("django-insecure-development-placeholder-key-01234567890123456789")
        assert False, "Should have rejected django-insecure- prefix"
    except InsecureSecretKeyError:
        pass
        
    try:
        validate_django_secret_key("too_short_key_12345")
        assert False, "Should have rejected short key"
    except InsecureSecretKeyError:
        pass
    print("  [+] Secret key entropy and pattern audit passed.")
    
    # 6. End-to-End Production Bootstrap Test
    prod_env = {
        "DJANGO_ENV": "production",
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": "z!9k#Lp2$vQ8@mN5^xR1*tW4(yU7)oP3_eB6+dF0=sC#991jKh@1920zLaQ!991",
        "DJANGO_ALLOWED_HOSTS": "api.production.com, admin.production.com",
        "DATABASE_URL": "postgresql://prod_user:secret_prod_pass@pgbouncer.internal:6432/core_prod",
        "DJANGO_CONN_MAX_AGE": "900",
    }
    loader = DjangoSettingsBootstrap(EnvironmentConfig(prod_env))
    settings = loader.load_settings()
    assert settings["ENVIRONMENT"] == "production"
    assert settings["DEBUG"] is False
    assert settings["SECURE_SSL_REDIRECT"] is True
    assert settings["SESSION_COOKIE_SECURE"] is True
    assert settings["DATABASES"]["default"]["PORT"] == 6432
    assert settings["DATABASES"]["default"]["CONN_MAX_AGE"] == 900
    assert settings["AUTH_USER_MODEL"] == "users.User"
    print("  [+] End-to-end production bootstrap passed.")
    
    # 7. Production Security Guardrail Rejection Test
    bad_prod_env = prod_env.copy()
    bad_prod_env["DJANGO_DEBUG"] = "true"  # Fatal error in prod
    try:
        DjangoSettingsBootstrap(EnvironmentConfig(bad_prod_env)).load_settings()
        assert False, "Should have blocked DEBUG=True in production"
    except ConfigurationError:
        pass
    print("  [+] Production security guardrails successfully rejected invalid configuration.")


def benchmark_loader(iterations: int = 20_000) -> None:
    """Benchmark configuration loading latency."""
    prod_env = {
        "DJANGO_ENV": "production",
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": "z!9k#Lp2$vQ8@mN5^xR1*tW4(yU7)oP3_eB6+dF0=sC#991jKh@1920zLaQ!991",
        "DJANGO_ALLOWED_HOSTS": "api.production.com, admin.production.com",
        "DATABASE_URL": "postgresql://prod_user:secret_prod_pass@pgbouncer.internal:6432/core_prod",
        "DJANGO_CONN_MAX_AGE": "900",
    }
    config = EnvironmentConfig(prod_env)
    bootstrap = DjangoSettingsBootstrap(config)
    
    start_time = time.perf_counter()
    for _ in range(iterations):
        _ = bootstrap.load_settings()
    elapsed = time.perf_counter() - start_time
    
    latency_us = (elapsed / iterations) * 1_000_000
    ops_per_sec = iterations / elapsed
    
    print("\n" + "=" * 60)
    print("DAY 32: SETTINGS LOADER BENCHMARK")
    print("=" * 60)
    print(f"Iterations:        {iterations:,}")
    print(f"Total Time:        {elapsed:.4f} seconds")
    print(f"Average Latency:   {latency_us:.2f} microseconds per bootstrap")
    print(f"Throughput:        {ops_per_sec:,.0f} settings loads/sec")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
    benchmark_loader()
