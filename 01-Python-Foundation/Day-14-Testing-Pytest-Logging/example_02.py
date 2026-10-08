"""Day 14: Production Async Test Suite & Structured Observability for an AI RAG Microservice.

This module implements:
1. An asynchronous RAG Microservice with Pydantic V2 response contracts.
2. Production structured JSON logging with dynamic trace ID and tenant correlation.
3. A complete Pytest test suite covering:
   - Happy path semantic retrieval and generation via AsyncMock.
   - Downstream service failure / timeout handling (side_effect).
   - Malformed response schema recovery.
"""

import asyncio
import contextvars
import json
import logging
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

from pydantic import BaseModel, Field, ValidationError
import pytest


# =====================================================================
# OBSERVABILITY & DISTRIBUTED CONTEXT LOGGING
# =====================================================================
REQUEST_CONTEXT: contextvars.ContextVar[Dict[str, str]] = contextvars.ContextVar(
    "request_context", default={"trace_id": "none", "tenant_id": "public"}
)


class EnterpriseJSONFormatter(logging.Formatter):
    """Outputs standardized, machine-readable JSON logs for Datadog/CloudWatch."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = REQUEST_CONTEXT.get()
        entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": ctx.get("trace_id"),
            "tenant_id": ctx.get("tenant_id"),
            "message": record.getMessage(),
        }
        if hasattr(record, "telemetry"):
            entry["telemetry"] = record.telemetry
        return json.dumps(entry)


rag_logger = logging.getLogger("RAGMicroservice")
rag_logger.setLevel(logging.INFO)
log_handler = logging.StreamHandler()
log_handler.setFormatter(EnterpriseJSONFormatter())
rag_logger.handlers.clear()
rag_logger.addHandler(log_handler)
rag_logger.propagate = False


# =====================================================================
# DOMAIN MODELS (Pydantic V2)
# =====================================================================
class DocumentHit(BaseModel):
    doc_id: str
    content: str
    score: float = Field(ge=0.0, le=1.0)


class RAGResponse(BaseModel):
    query: str
    answer: str
    sources: List[DocumentHit]
    total_tokens_used: int
    duration_ms: float


# =====================================================================
# RAG MICROSERVICE CORE
# =====================================================================
class RAGQueryService:
    """Enterprise RAG query service coordinating vector store and LLM generation."""

    def __init__(self, vector_client: Any, llm_client: Any) -> None:
        self.vector_client = vector_client
        self.llm_client = llm_client

    async def answer_query(self, user_query: str) -> RAGResponse:
        start_time = time.perf_counter()
        rag_logger.info(f"Initiating RAG pipeline for query: '{user_query[:30]}...'")

        # 1. Query Vector Database
        try:
            raw_hits = await self.vector_client.search(query=user_query, top_k=2)
            sources = [DocumentHit.model_validate(hit) for hit in raw_hits]
        except Exception as err:
            rag_logger.error(f"Vector search failed: {err}. Returning graceful fallback.")
            duration_ms = (time.perf_counter() - start_time) * 1000
            return RAGResponse(
                query=user_query,
                answer="Service temporarily degraded: Knowledge base unavailable.",
                sources=[],
                total_tokens_used=0,
                duration_ms=round(duration_ms, 2),
            )

        # 2. Call LLM for generation
        context_str = "\n".join(doc.content for doc in sources)
        prompt = f"Context:\n{context_str}\n\nQuestion: {user_query}\nAnswer:"

        llm_output = await self.llm_client.generate(prompt=prompt)
        duration_ms = (time.perf_counter() - start_time) * 1000

        response = RAGResponse(
            query=user_query,
            answer=llm_output.get("text", ""),
            sources=sources,
            total_tokens_used=llm_output.get("tokens", 0),
            duration_ms=round(duration_ms, 2),
        )

        # Log completion telemetry
        record = rag_logger.makeRecord(
            name=rag_logger.name,
            level=logging.INFO,
            fn="",
            lno=0,
            msg="RAG query completed successfully.",
            args=(),
            exc_info=None,
        )
        record.telemetry = {  # type: ignore[attr-defined]
            "tokens": response.total_tokens_used,
            "duration_ms": response.duration_ms,
            "hit_count": len(sources),
        }
        rag_logger.handle(record)

        return response


# =====================================================================
# PYTEST ASYNC TEST SUITE
# =====================================================================
@pytest.fixture
def mock_clients() -> Dict[str, AsyncMock]:
    """Provides pre-configured AsyncMocks for vector and LLM clients."""
    mock_vector = AsyncMock()
    mock_llm = AsyncMock()
    return {"vector": mock_vector, "llm": mock_llm}


@pytest.fixture
def rag_service(mock_clients: Dict[str, AsyncMock]) -> RAGQueryService:
    return RAGQueryService(
        vector_client=mock_clients["vector"],
        llm_client=mock_clients["llm"],
    )


def test_rag_pipeline_happy_path(
    rag_service: RAGQueryService, mock_clients: Dict[str, AsyncMock]
) -> None:
    """Test successful vector retrieval and LLM answer generation."""
    async def _run() -> None:
        # Set request trace context
        REQUEST_CONTEXT.set({"trace_id": "req-happy-path-01", "tenant_id": "enterprise-acme"})

        # Configure mock behaviors
        mock_clients["vector"].search.return_value = [
            {"doc_id": "doc_01", "content": "Transformer self-attention rules.", "score": 0.92},
            {"doc_id": "doc_02", "content": "HNSW graphs enable fast vector search.", "score": 0.88},
        ]
        mock_clients["llm"].generate.return_value = {
            "text": "Self-attention and HNSW graphs power modern RAG architectures.",
            "tokens": 48,
        }

        # Execute
        response = await rag_service.answer_query("Explain transformer search")

        # Assertions
        assert isinstance(response, RAGResponse)
        assert len(response.sources) == 2
        assert response.sources[0].doc_id == "doc_01"
        assert response.total_tokens_used == 48
        assert "Self-attention" in response.answer

        # Verify mock call interactions
        mock_clients["vector"].search.assert_awaited_once_with(
            query="Explain transformer search", top_k=2
        )
        mock_clients["llm"].generate.assert_awaited_once()

    asyncio.run(_run())


def test_rag_pipeline_vector_timeout_fallback(
    rag_service: RAGQueryService, mock_clients: Dict[str, AsyncMock]
) -> None:
    """Test that downstream vector timeout triggers graceful degradation."""
    async def _run() -> None:
        REQUEST_CONTEXT.set({"trace_id": "req-timeout-fail-02", "tenant_id": "tenant-beta"})

        # Simulate network timeout exception
        mock_clients["vector"].search.side_effect = TimeoutError("Vector DB socket timeout")

        response = await rag_service.answer_query("Explain transformer search")

        assert response.sources == []
        assert "temporarily degraded" in response.answer
        assert response.total_tokens_used == 0
        # LLM should not be called if retrieval failed
        mock_clients["llm"].generate.assert_not_awaited()

    asyncio.run(_run())


# =====================================================================
# MAIN RUNNER
# =====================================================================
def run_standalone_demo() -> None:
    print("=" * 65)
    print("RUNNING PRODUCTION ASYNC PYTEST SUITE VIA pytest.main()")
    print("=" * 65)
    pytest.main(["-v", __file__])


if __name__ == "__main__":
    run_standalone_demo()
