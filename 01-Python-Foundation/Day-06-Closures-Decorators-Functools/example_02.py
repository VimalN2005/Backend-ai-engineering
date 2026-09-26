"""
Day 06: Closures, Custom Decorators & Functional Python with Functools
File: example_02.py - Production Resilient LLM Gateway Decorator Suite

ALIGNMENT WITH AI BACKEND ENGINEER ROLE:
Job Description Requirement:
"Integrate LLMs... optimize for low latency, token usage, and API cost efficiency...
Work with asynchronous systems and handle rate limits."

WHY THIS ARCHITECTURE IS ESSENTIAL:
In high-throughput AI backends, external LLM APIs (OpenAI, Gemini, Anthropic) fail regularly
due to network timeouts, server-side 5xx errors, and HTTP 429 Rate Limits.
If every API route implements its own ad-hoc retry loop and timing code, the codebase
becomes fragmented, unmaintainable, and prone to Thundering Herd failures.

This module provides a production-grade composable decorator suite:
1. `@retry_with_exponential_backoff`: Automatically retries intermittent network failures
   with exponential backoff and randomized jitter to prevent cluster lockup.
2. `@audit_llm_telemetry`: Measures latency, logs token usage, and outputs structured metrics
   ready for Datadog or CloudWatch.
"""

import time
import random
import logging
import functools
from typing import Callable, Any, Dict

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class RateLimitExceededError(Exception):
    """Raised when an upstream LLM API returns HTTP 429 (Rate Limited)."""
    pass


class UpstreamServiceUnavailableError(Exception):
    """Raised when an upstream LLM API returns HTTP 503 or network drops."""
    pass


def retry_with_exponential_backoff(
    max_retries: int = 3,
    initial_delay: float = 0.1,
    backoff_multiplier: float = 2.0,
    jitter_range: float = 0.05
):
    """
    3-Tier Decorator providing exponential backoff with randomized jitter.
    
    WHY JITTER IS MANDATORY:
    When an upstream provider (e.g. OpenAI) recovers from an outage, thousands of
    concurrent worker pods retrying on exact synchronized intervals (e.g. exactly 1.0s, 2.0s)
    will crash the provider again (the Thundering Herd problem).
    Adding random jitter desynchronizes retry requests across the cluster.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (RateLimitExceededError, UpstreamServiceUnavailableError) as err:
                    if attempt == max_retries:
                        logging.error(f"[Retry Exhausted] Function '{func.__name__}' failed after {max_retries} attempts: {err}")
                        raise

                    # Calculate exponential delay with randomized jitter
                    jitter = random.uniform(-jitter_range, jitter_range)
                    actual_sleep = max(0.01, delay + jitter)
                    
                    logging.warning(
                        f"[Transient Failure] Attempt {attempt}/{max_retries} for '{func.__name__}' failed: {err}. "
                        f"Retrying in {actual_sleep:.3f}s..."
                    )
                    time.sleep(actual_sleep)
                    delay *= backoff_multiplier
        return wrapper
    return decorator


def audit_llm_telemetry(func: Callable) -> Callable:
    """
    Measures execution latency and extracts token metrics from LLM responses.
    
    WHY THIS MATTERS:
    Production SLAs require tracking Time-To-First-Token and total generation duration.
    Separating telemetry into a decorator keeps core routing logic clean.
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.perf_counter()
        correlation_id = f"req_{random.randint(1000, 9999)}"
        
        logging.info(f"[{correlation_id}] Executing LLM Call: '{func.__name__}'")
        
        result = func(*args, **kwargs)
        
        duration_ms = (time.perf_counter() - start_time) * 1000
        tokens = result.get("usage", {}).get("total_tokens", 0) if isinstance(result, dict) else 0
        
        logging.info(
            f"[{correlation_id}] Completed '{func.__name__}' in {duration_ms:.2f} ms | "
            f"Tokens Consumed: {tokens}"
        )
        return result
    return wrapper


# =====================================================================
# SIMULATION: COMPOSED PRODUCTION DECORATORS ON LLM CALLS
# =====================================================================

# Decorator Stacking Order:
# Top decorator runs first on entry, and last on exit!
# Flow: Telemetry Start -> Retry Loop -> Core API Call -> Telemetry End
@audit_llm_telemetry
@retry_with_exponential_backoff(max_retries=3, initial_delay=0.1, backoff_multiplier=2.0)
def query_foundation_model(prompt: str, model: str = "gpt-4o") -> Dict[str, Any]:
    """Simulates an API call to a foundation model with intermittent rate limiting."""
    
    # Simulate a 50% chance of transient RateLimit failure
    if random.random() < 0.6:
        raise RateLimitExceededError("HTTP 429: Rate limit exceeded on tokens per minute (TPM).")

    # Successful response payload
    return {
        "status": "success",
        "model": model,
        "completion": "RAG systems combine vector retrieval with dense semantic generation.",
        "usage": {"prompt_tokens": 15, "completion_tokens": 12, "total_tokens": 27}
    }


def run_production_simulation() -> None:
    print("=" * 65)
    print("  PRODUCTION RESILIENT LLM GATEWAY DECORATOR SIMULATION")
    print("=" * 65)

    print("\n[Test 1] Dispatching prompt with automatic backoff and telemetry tracking...")
    try:
        response = query_foundation_model("Explain RAG architectures.", model="gpt-4o")
        print("\n--- [FINAL SUCCESSFUL RESPONSE] ---")
        print(f"Status     : {response['status']}")
        print(f"Completion : {response['completion']}")
        print(f"Total Usage: {response['usage']['total_tokens']} tokens")
    except Exception as fatal_err:
        print(f"\n[FINAL FAILURE] Request could not be fulfilled: {fatal_err}")

    print("\n" + "=" * 65)


if __name__ == "__main__":
    run_production_simulation()
