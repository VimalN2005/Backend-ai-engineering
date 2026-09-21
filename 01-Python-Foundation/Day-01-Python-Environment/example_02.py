"""
Day 01: Python Environment, Execution Flow & Memory References
File: example_02.py - Production Runtime Environment Validator

WHY THIS FILE EXISTS:
In production web backends (Django, FastAPI, ML inference workers), launching an
application with an incompatible Python version, unisolated global packages,
or missing critical environment variables causes silent runtime data corruption
or unexpected crashes in worker threads.

This script acts as a self-contained pre-flight health-check that can be run
during CI/CD pipelines or container entrypoints before starting the ASGI/WSGI server.
"""

import os
import sys
import platform
from pathlib import Path
from typing import Dict, List, Tuple


class EnvironmentValidationError(Exception):
    """Raised when the runtime environment violates production requirements."""
    pass


class ProductionEnvironmentValidator:
    """
    Validates host runtime, virtual environment isolation, and filesystem permissions.
    """

    def __init__(
        self,
        min_python_version: Tuple[int, int] = (3, 11),
        required_env_vars: List[str] = None
    ) -> None:
        self.min_python_version = min_python_version
        self.required_env_vars = required_env_vars or []
        self.report: Dict[str, str] = {}

    def check_python_version(self) -> None:
        """
        WHY: Modern backend patterns (PEP 604 union syntax `X | Y`, Pydantic V2,
        FastAPI 0.100+, Asyncio task groups) require Python 3.11+ for native performance.
        """
        current_version = sys.version_info[:2]
        self.report["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        self.report["implementation"] = platform.python_implementation()

        if current_version < self.min_python_version:
            raise EnvironmentValidationError(
                f"Incompatible Python version! Required: >= {self.min_python_version[0]}.{self.min_python_version[1]}, "
                f"Detected: {self.report['python_version']}"
            )

    def check_virtualenv_isolation(self) -> bool:
        """
        WHY: In production, executing in global OS site-packages invites dependency pollution,
        package version collisions, and permission escalation risks.
        
        CPython sets `sys.base_prefix` to the system Python directory and `sys.prefix`
        to the active environment directory. When isolated inside a venv, `prefix != base_prefix`.
        """
        is_in_venv = sys.prefix != sys.base_prefix
        self.report["virtualenv_active"] = str(is_in_venv)
        self.report["sys_prefix"] = sys.prefix
        self.report["sys_base_prefix"] = sys.base_prefix

        return is_in_venv

    def check_bytecode_writing(self) -> None:
        """
        WHY: In stateless production containers (Docker/Kubernetes), generating .pyc
        files at runtime wastes container storage layers and slows down read-only containers.
        Setting `PYTHONDONTWRITEBYTECODE=1` is a cloud-native best practice.
        """
        dont_write_bytecode = os.getenv("PYTHONDONTWRITEBYTECODE") == "1" or sys.dont_write_bytecode
        self.report["dont_write_bytecode"] = str(dont_write_bytecode)

    def check_environment_variables(self) -> List[str]:
        """
        WHY: Missing secrets or database URLs should crash the container immediately
        during startup (Fail-Fast principle) rather than during a user's HTTP request.
        """
        missing = [var for var in self.required_env_vars if not os.getenv(var)]
        self.report["missing_required_env_vars"] = ", ".join(missing) if missing else "None"
        return missing

    def run_all_checks(self) -> bool:
        """Executes all pre-flight inspections and prints an actionable audit."""
        print("=" * 65)
        print("  PRODUCTION RUNTIME PRE-FLIGHT ENVIRONMENT AUDIT")
        print("=" * 65)

        try:
            self.check_python_version()
            is_venv = self.check_virtualenv_isolation()
            self.check_bytecode_writing()
            missing_vars = self.check_environment_variables()

            for key, value in self.report.items():
                print(f"  [CHECK] {key.ljust(28)}: {value}")

            if not is_venv:
                print("\n  [!] WARNING: Script is running in the global OS Python environment.")
                print("      Recommended: Use an isolated virtualenv (`python -m venv .venv`).")

            if missing_vars:
                raise EnvironmentValidationError(f"Missing mandatory environment variables: {missing_vars}")

            print("\n  [PASS] All environment checks completed successfully.")
            print("=" * 65)
            return True

        except EnvironmentValidationError as error:
            print(f"\n  [FAIL] Environment check failed: {error}")
            print("=" * 65)
            return False


if __name__ == "__main__":
    # Test validator with common backend environment expectations
    validator = ProductionEnvironmentValidator(
        min_python_version=(3, 10),
        required_env_vars=["ENVIRONMENT"]  # Example test variable
    )

    # Set mock environment for demonstration purposes
    os.environ.setdefault("ENVIRONMENT", "development")
    success = validator.run_all_checks()
    sys.exit(0 if success else 1)
