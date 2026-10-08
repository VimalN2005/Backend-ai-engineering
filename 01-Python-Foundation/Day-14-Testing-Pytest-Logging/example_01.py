"""Day 14: Core Pytest Fixtures, Mocking, Async Testing, and Structured Logging.

This module demonstrates:
1. Pytest Fixture Lifecycle (Setup and Teardown via yield).
2. Data-Driven Parametrization with @pytest.mark.parametrize.
3. Synchronous and Asynchronous Mocking with MagicMock and AsyncMock.
4. ContextVar Trace ID Propagation into Structured Logging.
5. Direct execution runner via pytest.main().
"""

import asyncio
import contextvars
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, Generator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =====================================================================
# 1. DISTRIBUTED TRACE ID CONTEXT & STRUCTURED JSON FORMATTER
# =====================================================================
# Thread-safe and Task-safe context variable for request tracing
TRACE_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="system-init")


class StructuredJSONFormatter(logging.Formatter):
    """Custom logging formatter outputting machine-readable JSON payloads."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": TRACE_ID_CTX.get(),
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            log_payload.update(record.extra_fields)
        return json.dumps(log_payload)


# Configure a dedicated demo logger
logger = logging.getLogger("TestDemoLogger")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(StructuredJSONFormatter())
logger.handlers.clear()
logger.addHandler(handler)
logger.propagate = False


# =====================================================================
# 2. BUSINESS LOGIC UNDER TEST
# =====================================================================
class LLMTokenBudgetManager:
    """Manages token allocation and validates prompt requests."""

    def __init__(self, max_tokens: int = 4096) -> None:
        self.max_tokens = max_tokens
        self.allocated_tokens = 0

    def estimate_tokens(self, prompt: str) -> int:
        if not prompt:
            return 0
        return max(1, len(prompt.split()))

    def allocate(self, prompt: str) -> bool:
        required = self.estimate_tokens(prompt)
        if self.allocated_tokens + required > self.max_tokens:
            return False
        self.allocated_tokens += required
        return True


async def async_fetch_llm_embedding(client: Any, text: str) -> List[float]:
    """Asynchronous client call that will be mocked in tests."""
    # Under real conditions, calls upstream OpenAI / VertexAI API
    return await client.create_embedding(text)


# =====================================================================
# 3. PYTEST FIXTURES (SETUP & TEARDOWN)
# =====================================================================
@pytest.fixture
def token_manager() -> Generator[LLMTokenBudgetManager, None, None]:
    """Provides a fresh LLMTokenBudgetManager instance for each test.

    WHY yield?
    Code before 'yield' runs during SETUP.
    Code after 'yield' runs during TEARDOWN (cleanup), guaranteed even if
    the test fails or throws an unhandled exception.
    """
    manager = LLMTokenBudgetManager(max_tokens=100)
    logger.info("Setting up LLMTokenBudgetManager fixture.")

    yield manager

    # Teardown logic
    manager.allocated_tokens = 0
    logger.info("Torn down LLMTokenBudgetManager fixture.")


# =====================================================================
# 4. UNIT TESTS & DATA-DRIVEN PARAMETRIZATION
# =====================================================================
@pytest.mark.parametrize(
    "prompt, expected_tokens",
    [
        ("", 0),
        ("hello", 1),
        ("attention is all you need", 5),
        ("multi line prompt\nwith newlines and tabs", 7),
    ],
)
def test_estimate_tokens_parametrized(
    token_manager: LLMTokenBudgetManager, prompt: str, expected_tokens: int
) -> None:
    """Test token estimation across multiple inputs using parametrization."""
    assert token_manager.estimate_tokens(prompt) == expected_tokens


def test_allocation_exhaustion(token_manager: LLMTokenBudgetManager) -> None:
    """Verify that allocation rejects requests exceeding budget."""
    small_prompt = "word " * 50  # 50 tokens
    assert token_manager.allocate(small_prompt) is True

    oversized_prompt = "word " * 60  # 50 + 60 = 110 > 100 max
    assert token_manager.allocate(oversized_prompt) is False


# =====================================================================
# 5. ASYNC MOCKING WITH AsyncMock & pytest-asyncio
# =====================================================================
def test_async_embedding_with_mock() -> None:
    """Demonstrate mocking asynchronous coroutines using unittest.mock.AsyncMock.

    WHY AsyncMock?
    Standard MagicMock returns a mock object when called.
    Calling 'await mock()' fails because standard mocks are not awaitable.
    AsyncMock is awaitable and cleanly simulates async responses.
    """
    async def _run_async_test() -> None:
        mock_ai_client = AsyncMock()
        mock_ai_client.create_embedding.return_value = [0.15, 0.72, -0.34, 0.99]

        result_vector = await async_fetch_llm_embedding(mock_ai_client, "Sample text")

        assert len(result_vector) == 4
        assert result_vector[0] == 0.15
        mock_ai_client.create_embedding.assert_awaited_once_with("Sample text")

    asyncio.run(_run_async_test())


# =====================================================================
# 6. DEMONSTRATION RUNNER (FOR STANDALONE PYTHON EXECUTION)
# =====================================================================
def run_standalone_demo() -> None:
    print("=" * 65)
    print("DEMONSTRATING STRUCTURED JSON LOGGING WITH ContextVar TRACE ID")
    print("=" * 65)

    TRACE_ID_CTX.set("trace_req_98124_prod")
    logger.info("Initializing vector indexing service.")
    logger.info("Allocating token budget for prompt.")

    print("\n" + "=" * 65)
    print("RUNNING AUTOMATED PYTEST SUITE VIA pytest.main()")
    print("=" * 65)
    # Run pytest programmatically on this file
    pytest.main(["-v", __file__])


if __name__ == "__main__":
    run_standalone_demo()
