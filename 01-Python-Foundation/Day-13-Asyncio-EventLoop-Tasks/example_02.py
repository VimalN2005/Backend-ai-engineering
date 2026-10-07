"""Day 13: Production High-Concurrency Async Multi-LLM Streaming Gateway.

This module implements an enterprise-grade async AI streaming gateway:
1. Bounded Concurrency Throttling via asyncio.Semaphore (Max 5 upstream calls).
2. Streaming Token Generation via Async Generators (simulating SSE packet frames).
3. Structured Multi-Model Parallel Dispatch via asyncio.TaskGroup.
4. Hard Timeout Protection via asyncio.timeout.
5. Client Disconnect Cancellation Handling (asyncio.CancelledError).
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("AsyncLLMGateway")


# =====================================================================
# DATA CONTRACTS
# =====================================================================
@dataclass(slots=True)
class StreamChunk:
    chunk_index: int
    token: str
    is_terminal: bool


@dataclass(slots=True)
class ModelCompletionSummary:
    model_name: str
    total_tokens: int
    duration_ms: float
    full_text: str


# =====================================================================
# 1. ASYNC LLM STREAMING GATEWAY CORE
# =====================================================================
class AsyncLLMGateway:
    """Enterprise AI gateway with rate throttling, timeouts, and SSE streaming."""

    def __init__(self, max_concurrent_requests: int = 5) -> None:
        # Bounded semaphore prevents flooding upstream LLM providers
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.active_requests_count = 0

    async def stream_completion(
        self,
        model_name: str,
        prompt: str,
        timeout_seconds: float = 3.0,
    ) -> AsyncGenerator[str, None]:
        """Stream completion tokens as Server-Sent Events (SSE) formatted frames.

        WHY AsyncGenerator?
        Yields tokens one by one as they arrive from upstream TCP sockets,
        enabling sub-100ms Time-To-First-Token (TTFT) for user frontends.
        """
        async with self.semaphore:
            self.active_requests_count += 1
            logger.info(
                f"[{model_name}] Starting request. (Active Concurrency: {self.active_requests_count})"
            )

            try:
                # Wrap streaming generation in a strict timeout context
                async with asyncio.timeout(timeout_seconds):
                    # Simulated token stream response
                    tokens = [
                        "Vector ", "databases ", "enable ", "semantic ",
                        "search ", "across ", "high-dimensional ", "embeddings."
                    ]

                    for idx, token in enumerate(tokens):
                        # Simulate inter-token latency from neural network inference
                        await asyncio.sleep(0.03)

                        chunk_payload = {
                            "model": model_name,
                            "index": idx,
                            "token": token,
                        }
                        # Format as SSE protocol: event + data + double newline
                        sse_frame = f"event: message\ndata: {json.dumps(chunk_payload)}\n\n"
                        yield sse_frame

            except asyncio.CancelledError:
                # Handle client-side disconnection (e.g. user closes browser tab)
                logger.warning(f"[{model_name}] Stream CANCELLED by client disconnect! Cleaning up socket.")
                raise  # Re-raise to ensure proper task teardown

            except TimeoutError:
                logger.error(f"[{model_name}] Upstream stream timed out after {timeout_seconds}s!")
                yield f"event: error\ndata: {{\"error\": \"Upstream timeout after {timeout_seconds}s\"}}\n\n"

            finally:
                self.active_requests_count -= 1
                logger.info(f"[{model_name}] Request finalized. (Active Concurrency: {self.active_requests_count})")


# =====================================================================
# 2. STRUCTURED MULTI-MODEL COMPARISON PIPELINE
# =====================================================================
async def collect_model_response(
    gateway: AsyncLLMGateway,
    model_name: str,
    prompt: str,
) -> ModelCompletionSummary:
    """Consume an async stream to build a complete model summary."""
    start_time = time.perf_counter()
    tokens_received = []

    async for sse_frame in gateway.stream_completion(model_name, prompt):
        # Extract token from SSE data line
        for line in sse_frame.strip().split("\n"):
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if "token" in payload:
                    tokens_received.append(payload["token"])

    duration_ms = (time.perf_counter() - start_time) * 1000
    return ModelCompletionSummary(
        model_name=model_name,
        total_tokens=len(tokens_received),
        duration_ms=round(duration_ms, 2),
        full_text="".join(tokens_received),
    )


async def run_multi_model_benchmark() -> None:
    gateway = AsyncLLMGateway(max_concurrent_requests=3)
    models = ["gpt-4o", "gemini-1.5-pro", "claude-3-5-sonnet"]
    prompt = "Explain vector indexing in enterprise RAG systems."

    logger.info("=" * 65)
    logger.info("1. DISPATCHING MULTI-MODEL BENCHMARK VIA TaskGroup")
    logger.info("=" * 65)

    benchmark_start = time.perf_counter()
    results: List[ModelCompletionSummary] = []

    # Modern structured concurrency: all tasks are managed within the context
    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(collect_model_response(gateway, m, prompt))
            for m in models
        ]

    for t in tasks:
        results.append(t.result())

    total_time = time.perf_counter() - benchmark_start
    logger.info(f"Benchmark finished in {total_time:.3f}s across {len(models)} models concurrently!")

    for res in results:
        logger.info(
            f"  - Model: {res.model_name:<18} | Tokens: {res.total_tokens:2d} | "
            f"Latency: {res.duration_ms:6.2f}ms | Output: '{res.full_text}'"
        )


# =====================================================================
# 3. CLIENT DISCONNECT & CANCELLATION DEMONSTRATION
# =====================================================================
async def demonstrate_client_cancellation() -> None:
    print("\n" + "=" * 65)
    print("2. DEMONSTRATING CLIENT DISCONNECT CANCELLATION")
    print("=" * 65)

    gateway = AsyncLLMGateway()

    async def client_simulator() -> None:
        # Simulate consumer that aborts after reading 3 tokens
        token_count = 0
        async for frame in gateway.stream_completion("test-model-cancellation", "prompt"):
            token_count += 1
            print(f"  [CLIENT RECV] Frame #{token_count}")
            if token_count >= 3:
                print("  [CLIENT ACTION] User closed browser tab! Aborting request...")
                break  # Breaks iteration, triggering task cancellation/teardown

    # Run the client simulation
    client_task = asyncio.create_task(client_simulator())
    await client_task
    print("[*] Cancellation test completed cleanly without orphaned tasks.")


# =====================================================================
# MAIN RUNNER
# =====================================================================
async def main() -> None:
    await run_multi_model_benchmark()
    await demonstrate_client_cancellation()


if __name__ == "__main__":
    asyncio.run(main())
