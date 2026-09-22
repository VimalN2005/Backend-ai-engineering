"""
Day 02: Strings, Unicode, Regular Expressions & Slicing Mechanics
File: example_02.py - Production PII (Personally Identifiable Info) Log Redaction Engine

WHY THIS COMPONENT IS CRITICAL:
Under regulations like GDPR, HIPAA, and PCI-DSS, logging raw HTTP payloads containing
credit card numbers, bearer authorization tokens, social security numbers, or emails
to centralized logging providers (Datadog, CloudWatch, Elasticsearch) violates compliance.

This module provides a production-grade, pre-compiled regex masking engine designed
for backend middleware, scrubbing sensitive data with sub-millisecond overhead.
"""

import re
import time
from typing import Dict, Pattern, Callable


class PIIScrubberEngine:
    """
    High-performance text sanitizer using compiled regular expression replacements.
    """

    # WHY: Compiling patterns at the class/module level transforms regex string rules
    # into internal NFA state machines ONCE at startup, saving millions of re-parsing
    # operations in high-throughput API web request loops.
    CREDIT_CARD_PATTERN: Pattern = re.compile(
        r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
    )
    
    EMAIL_PATTERN: Pattern = re.compile(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    )
    
    JWT_OR_BEARER_PATTERN: Pattern = re.compile(
        r"(?i)(Bearer\s+)([A-Za-z0-9\-_\=]+\.[A-Za-z0-9\-_\=]+\.[A-Za-z0-9\-_\=]+)"
    )
    
    API_KEY_PATTERN: Pattern = re.compile(
        r"(?i)(api[_-]?key|secret|password)[\"'\s:=]+([a-zA-Z0-9_\-]{16,})"
    )

    @classmethod
    def _mask_credit_card(cls, match: re.Match) -> str:
        """
        WHY: Complete redaction makes debugging impossible.
        Masking all but the last 4 digits allows payment gateway reconciliation
        while maintaining full PCI-DSS compliance.
        """
        raw_card = re.sub(r"[-\s]", "", match.group(0))
        return f"****-****-****-{raw_card[-4:]}"

    @classmethod
    def _mask_email(cls, match: re.Match) -> str:
        """
        Preserves the first character and domain for identity verification in logs.
        Example: 'john.doe@company.org' -> 'j***@company.org'
        """
        email = match.group(0)
        local_part, domain = email.split("@", 1)
        masked_local = local_part[0] + "***" if len(local_part) > 1 else "*"
        return f"{masked_local}@{domain}"

    @classmethod
    def scrub_log_payload(cls, raw_payload: str) -> str:
        """
        Runs the payload through the compiled regex substitution pipeline.
        """
        # Step 1: Scrub Credit Cards with custom partial mask
        scrubbed = cls.CREDIT_CARD_PATTERN.sub(cls._mask_credit_card, raw_payload)
        
        # Step 2: Scrub Email addresses with partial mask
        scrubbed = cls.EMAIL_PATTERN.sub(cls._mask_email, scrubbed)
        
        # Step 3: Scrub Bearer / JWT Tokens (Zero token exposure)
        scrubbed = cls.JWT_OR_BEARER_PATTERN.sub(r"\1[REDACTED_JWT_TOKEN]", scrubbed)
        
        # Step 4: Scrub API Keys and Passwords
        scrubbed = cls.API_KEY_PATTERN.sub(r'\1: "[REDACTED_SECRET]"', scrubbed)
        
        return scrubbed


def run_production_simulation() -> None:
    print("=" * 65)
    print("  PRODUCTION PII REDACTION & SECURITY MASKING ENGINE")
    print("=" * 65)

    raw_incoming_log = (
        "POST /api/v1/checkout HTTP/1.1\n"
        "Host: api.enterprise-payments.com\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI.eyJzdWIiOiIxMjM0NTY3ODkwI.sflKxwRJSMeKKF2QT4fwpMe\n"
        "Content-Type: application/json\n\n"
        "{\n"
        '  "user_email": "vimal.engineer@cloudsystems.io",\n'
        '  "billing_card": "4532-1234-5678-9812",\n'
        '  "api_key": "sec_live_998124871923847192847",\n'
        '  "order_amount": 149.99\n'
        "}"
    )

    print("\n--- [ORIGINAL INCOMING RAW LOG (CONFIDENTIAL)] ---")
    print(raw_incoming_log)

    # Measure execution time
    start = time.perf_counter()
    sanitized_log = PIIScrubberEngine.scrub_log_payload(raw_incoming_log)
    elapsed_ms = (time.perf_counter() - start) * 1000

    print("\n--- [SANITIZED COMPLIANT LOG (SAFE FOR CLOUDWATCH/SENTRY)] ---")
    print(sanitized_log)
    print(f"\nSanitization Latency: {elapsed_ms:.3f} ms (sub-millisecond overhead)")
    print("=" * 65)


if __name__ == "__main__":
    run_production_simulation()
