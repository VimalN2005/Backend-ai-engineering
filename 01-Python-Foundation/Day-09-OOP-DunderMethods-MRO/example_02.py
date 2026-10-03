"""Day 09: Production Extensible Multi-Provider AI Inference Gateway.

This module demonstrates an enterprise-grade AI model gateway architecture:
1. Abstract Base Classes (abc.ABC) & @abstractmethod enforcing provider contracts.
2. Data Descriptor Protocol for robust attribute validation (temperature, token limits).
3. Composable Reusable Mixins (TokenTelemetryMixin, RateLimiterMixin).
4. Deterministic C3 Method Resolution Order (MRO) with cooperative super().
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("AIGateway")


# =====================================================================
# 1. DESCRIPTOR PROTOCOL FOR STRICT ATTRIBUTE VALIDATION
# =====================================================================
class BoundedFloat:
    """Data Descriptor enforcing a bounded float range (e.g., temperature in [0.0, 2.0]).

    WHY Descriptors?
    Instead of repeating repetitive property getters and setters with boilerplates
    in every single provider class, Descriptors encapsulate reusable validation
    logic that intercepts attribute assignment directly at the class level.
    """

    def __init__(self, min_val: float, max_val: float, default: float) -> None:
        self.min_val = min_val
        self.max_val = max_val
        self.default = default
        self._name: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        # Automatically captures the variable name defined on the owning class
        self._name = f"_{name}"

    def __get__(self, instance: Optional[object], owner: Optional[type] = None) -> Any:
        if instance is None:
            return self
        return getattr(instance, self._name, self.default)

    def __set__(self, instance: object, value: float) -> None:
        if not isinstance(value, (int, float)):
            raise TypeError(f"Attribute {self._name} must be a float, got {type(value).__name__}")
        if not (self.min_val <= value <= self.max_val):
            raise ValueError(
                f"Attribute {self._name} out of bounds: {value} (Allowed: [{self.min_val}, {self.max_val}])"
            )
        setattr(instance, self._name, float(value))


# =====================================================================
# 2. ABSTRACT BASE CLASS (PROVIDER CONTRACT)
# =====================================================================
class BaseLLMProvider(ABC):
    """Abstract Base Class enforcing the contract for all LLM backend providers."""

    temperature = BoundedFloat(min_val=0.0, max_val=2.0, default=0.7)

    def __init__(self, model_name: str, api_key: str, temperature: float = 0.7, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.model_name = model_name
        self.api_key = api_key
        self.temperature = temperature  # Validated by BoundedFloat descriptor

    @abstractmethod
    def _execute_api_call(self, prompt: str) -> Dict[str, Any]:
        """Perform the actual vendor-specific API call. Subclasses MUST implement this."""
        pass

    def complete(self, prompt: str) -> Dict[str, Any]:
        """Public template method. Coordinated across Mixins and executed via _execute_api_call."""
        return self._execute_api_call(prompt)

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the unique provider identifier string."""
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model_name!r}, temp={self.temperature})"


# =====================================================================
# 3. COMPOSABLE REUSABLE MIXINS
# =====================================================================
class TokenTelemetryMixin:
    """Mixin to monitor latency, count tokens, and audit API consumption."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.total_tokens_consumed: int = 0
        self.total_requests: int = 0

    def complete(self, prompt: str) -> Dict[str, Any]:
        # WHY super().complete()?
        # Cooperative super() calls the next provider in the runtime MRO
        start_time = time.perf_counter()
        response = super().complete(prompt)  # type: ignore[misc]
        latency_ms = (time.perf_counter() - start_time) * 1000

        # Audit usage
        usage = response.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", len(prompt) // 4)
        completion_tokens = usage.get("completion_tokens", len(response.get("text", "")) // 4)
        total_tokens = prompt_tokens + completion_tokens

        self.total_tokens_consumed += total_tokens
        self.total_requests += 1

        response["telemetry"] = {
            "latency_ms": round(latency_ms, 2),
            "total_tokens": total_tokens,
            "cumulative_tokens": self.total_tokens_consumed,
        }
        logger.info(
            f"[{self.get_provider_name()}] Request #{self.total_requests} latency: "
            f"{latency_ms:.2f}ms | Tokens: {total_tokens}"
        )
        return response


class RateLimiterMixin:
    """Mixin that applies sliding-window rate limiting on requests."""

    def __init__(self, max_requests_per_window: int = 10, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.max_requests_per_window = max_requests_per_window
        self.request_timestamps: List[float] = []

    def complete(self, prompt: str) -> Dict[str, Any]:
        now = time.time()
        # Clean timestamps older than 60 seconds
        self.request_timestamps = [ts for ts in self.request_timestamps if now - ts < 60.0]

        if len(self.request_timestamps) >= self.max_requests_per_window:
            raise RuntimeError(
                f"Rate limit exceeded: Max {self.max_requests_per_window} requests per minute reached!"
            )

        self.request_timestamps.append(now)
        # Cooperatively forward down the MRO
        return super().complete(prompt)  # type: ignore[misc]


# =====================================================================
# 4. CONCRETE PROVIDER IMPLEMENTATIONS
# =====================================================================
class ProductionOpenAIProvider(TokenTelemetryMixin, RateLimiterMixin, BaseLLMProvider):
    """Production provider composed with Telemetry and Rate Limiting via MRO."""

    def get_provider_name(self) -> str:
        return "OpenAI"

    def _execute_api_call(self, prompt: str) -> Dict[str, Any]:
        # Simulated upstream API call
        time.sleep(0.015)  # Simulate 15ms network latency
        generated_text = f"[OpenAI Response to: '{prompt[:30]}...'] Contextual embeddings retrieved."
        return {
            "provider": self.get_provider_name(),
            "model": self.model_name,
            "text": generated_text,
            "usage": {
                "prompt_tokens": len(prompt) // 4,
                "completion_tokens": len(generated_text) // 4,
            },
        }


class ProductionGeminiProvider(TokenTelemetryMixin, RateLimiterMixin, BaseLLMProvider):
    """Production Gemini provider with the same shared mixins."""

    def get_provider_name(self) -> str:
        return "GoogleGemini"

    def _execute_api_call(self, prompt: str) -> Dict[str, Any]:
        time.sleep(0.010)  # Simulate 10ms network latency
        generated_text = f"[Gemini Flash Response to: '{prompt[:30]}...'] Multimodal analysis complete."
        return {
            "provider": self.get_provider_name(),
            "model": self.model_name,
            "text": generated_text,
            "usage": {
                "prompt_tokens": len(prompt) // 4,
                "completion_tokens": len(generated_text) // 4,
            },
        }


# =====================================================================
# SIMULATION & VERIFICATION RUNNER
# =====================================================================
def run_simulation() -> None:
    logger.info("Initializing Enterprise Multi-Provider AI Inference Gateway...")

    # Instantiate providers with validated descriptors
    openai_client = ProductionOpenAIProvider(
        model_name="gpt-4o",
        api_key="sk-test-openai-key-secret",
        temperature=0.4,
        max_requests_per_window=5,
    )

    gemini_client = ProductionGeminiProvider(
        model_name="gemini-1.5-pro",
        api_key="gm-test-gemini-key-secret",
        temperature=1.2,
        max_requests_per_window=10,
    )

    # 1. Demonstrate Descriptor Validation Error
    try:
        openai_client.temperature = 3.5  # Outside [0.0, 2.0]
    except ValueError as err:
        logger.info(f"[DESCRIPTOR BLOCKED] Successfully intercepted invalid temperature: {err}")

    # 2. Inspect MRO for cooperative multiple inheritance
    logger.info("Verifying C3 Method Resolution Order for ProductionOpenAIProvider:")
    for step, cls_node in enumerate(ProductionOpenAIProvider.__mro__, start=1):
        logger.info(f"   Step {step}: {cls_node.__name__}")

    # 3. Execute queries through composed pipeline
    prompts = [
        "Explain CPython memory allocation for PyObject structs.",
        "How do vector indexes implement HNSW graph traversal?",
        "Compare B-Trees with LSM Trees in database design.",
    ]

    for p in prompts:
        res = openai_client.complete(p)
        logger.info(f"  -> Generated: {res['text']}")
        logger.info(f"  -> Telemetry: {res['telemetry']}")

    # Run through Gemini provider
    gemini_res = gemini_client.complete("Summarize transformer attention mechanism.")
    logger.info(f"  -> Gemini Output: {gemini_res['text']}")
    logger.info(f"  -> Gemini Telemetry: {gemini_res['telemetry']}")

    logger.info("=" * 65)
    logger.info("GATEWAY AUDIT SUMMARY:")
    logger.info(f"OpenAI Total Requests : {openai_client.total_requests}")
    logger.info(f"OpenAI Cumulative Tokens : {openai_client.total_tokens_consumed}")
    logger.info(f"Gemini Total Requests : {gemini_client.total_requests}")
    logger.info(f"Gemini Cumulative Tokens : {gemini_client.total_tokens_consumed}")
    logger.info("=" * 65)


if __name__ == "__main__":
    run_simulation()
