"""
Day 88: AI Observability (LangSmith/OpenTelemetry) & LLM Security (Guardrails)
Part 1: OpenTelemetry GenAI Semantic Tracing & Span Telemetry Engine

This module implements:
1. OpenTelemetry-compliant Tracer and Span data structures conforming to GenAI semantic conventions:
   - gen_ai.system, gen_ai.request.model, gen_ai.usage.prompt_tokens, gen_ai.usage.completion_tokens
2. Context manager for automatic hierarchical parent-child span tracking.
3. Token cost attribution calculator based on token usage and model pricing tiers.
4. Complete hierarchical multi-agent trace simulation:
   - Root Agent Workflow -> Retrieval -> LLM Reasoning -> Tool Dispatch -> LLM Synthesis
5. Text-based tree visualizer and aggregate telemetry reporting with assertions.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# =====================================================================
# 1. OpenTelemetry GenAI Semantic Constants
# =====================================================================

class GenAISemantics:
    SYSTEM = "gen_ai.system"
    REQUEST_MODEL = "gen_ai.request.model"
    REQUEST_TEMPERATURE = "gen_ai.request.temperature"
    USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
    USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
    TOTAL_TOKENS = "gen_ai.usage.total_tokens"
    ESTIMATED_COST_USD = "gen_ai.usage.cost_usd"
    STATUS_CODE = "gen_ai.response.status_code"


class SpanStatus(str, Enum):
    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


# =====================================================================
# 2. Span & Tracer Data Structures
# =====================================================================

@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: SpanStatus = SpanStatus.UNSET
    error_message: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return 0.0
        return (self.end_time - self.start_time) * 1000.0

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def finish(self, status: SpanStatus = SpanStatus.OK, error_message: Optional[str] = None) -> None:
        self.end_time = time.time()
        self.status = status
        self.error_message = error_message


# Thread-safe ContextVar to track currently active span
_CURRENT_SPAN: contextvars.ContextVar[Optional[Span]] = contextvars.ContextVar(
    "current_span", default=None
)


class OpenTelemetryGenAITracer:
    """
    Manages distributed trace lifecycle, maintaining active span hierarchy.
    """
    def __init__(self, service_name: str = "agent-backend-service"):
        self.service_name = service_name
        self.recorded_spans: List[Span] = []

    def start_trace(self, root_name: str) -> SpanContextManager:
        trace_id = uuid.uuid4().hex
        return SpanContextManager(self, root_name, trace_id=trace_id, parent_span_id=None)

    def start_span(self, name: str) -> SpanContextManager:
        current = _CURRENT_SPAN.get()
        trace_id = current.trace_id if current else uuid.uuid4().hex
        parent_id = current.span_id if current else None
        return SpanContextManager(self, name, trace_id=trace_id, parent_span_id=parent_id)

    def record_span(self, span: Span) -> None:
        self.recorded_spans.append(span)


class SpanContextManager:
    def __init__(
        self,
        tracer: OpenTelemetryGenAITracer,
        name: str,
        trace_id: str,
        parent_span_id: Optional[str] = None,
    ):
        self.tracer = tracer
        self.span = Span(
            name=name,
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_span_id,
        )
        self.token = None

    def __enter__(self) -> Span:
        self.token = _CURRENT_SPAN.set(self.span)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.span.finish(status=SpanStatus.ERROR, error_message=str(exc_val))
        else:
            self.span.finish(status=SpanStatus.OK)
        _CURRENT_SPAN.reset(self.token)
        self.tracer.record_span(self.span)
        return False  # Do not suppress exceptions


# =====================================================================
# 3. Cost & Telemetry Aggregator
# =====================================================================

class CostCalculator:
    # Model pricing per 1M tokens ($USD)
    PRICING = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
        "text-embedding-3-small": {"input": 0.02, "output": 0.00},
    }

    @classmethod
    def calculate_cost(cls, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        rates = cls.PRICING.get(model, {"input": 2.00, "output": 8.00})
        cost_in = (prompt_tokens / 1_000_000.0) * rates["input"]
        cost_out = (completion_tokens / 1_000_000.0) * rates["output"]
        return cost_in + cost_out


class TraceReporter:
    @staticmethod
    def print_trace_summary(spans: List[Span]) -> Dict[str, Any]:
        total_prompt_tokens = sum(s.attributes.get(GenAISemantics.USAGE_PROMPT_TOKENS, 0) for s in spans)
        total_completion_tokens = sum(s.attributes.get(GenAISemantics.USAGE_COMPLETION_TOKENS, 0) for s in spans)
        total_tokens = total_prompt_tokens + total_completion_tokens
        total_cost = sum(s.attributes.get(GenAISemantics.ESTIMATED_COST_USD, 0.0) for s in spans)
        
        root_span = next((s for s in spans if s.parent_span_id is None), None)
        root_duration = root_span.duration_ms if root_span else 0.0

        print("\n" + "=" * 80)
        print("OPENTELEMETRY GENAI TRACE SUMMARY")
        print("=" * 80)
        print(f"Trace ID             : {root_span.trace_id if root_span else 'N/A'}")
        print(f"Total Execution Time : {root_duration:.2f} ms")
        print(f"Total Spans Recorded : {len(spans)}")
        print(f"Prompt Tokens Burned : {total_prompt_tokens}")
        print(f"Comp Tokens Burned   : {total_completion_tokens}")
        print(f"Total Token Burn     : {total_tokens}")
        print(f"Estimated Cost       : ${total_cost:.6f} USD")
        print("-" * 80)

        # Hierarchy Print
        print("Hierarchical Span Decomposition:")
        # Build parent map
        children_map: Dict[Optional[str], List[Span]] = {}
        for s in spans:
            children_map.setdefault(s.parent_span_id, []).append(s)

        def print_node(parent_id: Optional[str], depth: int = 0):
            for child in children_map.get(parent_id, []):
                indent = "  " * depth
                tokens_info = ""
                if GenAISemantics.TOTAL_TOKENS in child.attributes:
                    tokens_info = f" | {child.attributes[GenAISemantics.TOTAL_TOKENS]} tokens"
                print(f"{indent}└── [{child.name}] ({child.duration_ms:.2f}ms{tokens_info}) Status: {child.status.value}")
                print_node(child.span_id, depth + 1)

        print_node(None)
        return {
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "span_count": len(spans),
        }


# =====================================================================
# 4. Simulation of an Instrumented Agent Execution
# =====================================================================

def execute_instrumented_agent(tracer: OpenTelemetryGenAITracer, user_query: str) -> None:
    with tracer.start_trace("agent_execution_workflow") as root:
        root.set_attribute("agent.user_query", user_query)

        # 1. Intent Classification
        with tracer.start_span("intent_classifier") as s1:
            time.sleep(0.01)  # Simulated computation
            s1.set_attribute(GenAISemantics.SYSTEM, "openai")
            s1.set_attribute(GenAISemantics.REQUEST_MODEL, "gpt-4o")
            s1.set_attribute(GenAISemantics.USAGE_PROMPT_TOKENS, 45)
            s1.set_attribute(GenAISemantics.USAGE_COMPLETION_TOKENS, 12)
            s1.set_attribute(GenAISemantics.TOTAL_TOKENS, 57)
            cost = CostCalculator.calculate_cost("gpt-4o", 45, 12)
            s1.set_attribute(GenAISemantics.ESTIMATED_COST_USD, cost)

        # 2. Retrieval Span
        with tracer.start_span("context_retrieval_pipeline") as s2:
            time.sleep(0.015)
            s2.set_attribute("retrieval.top_k", 3)
            # Nested Vector Search Span
            with tracer.start_span("vector_knn_pgvector") as s2_child:
                time.sleep(0.01)
                s2_child.set_attribute("db.system", "postgresql")
                s2_child.set_attribute("db.table", "document_embeddings")

        # 3. LLM Reasoning
        with tracer.start_span("llm_reasoning_turn_1") as s3:
            time.sleep(0.025)
            s3.set_attribute(GenAISemantics.SYSTEM, "openai")
            s3.set_attribute(GenAISemantics.REQUEST_MODEL, "gpt-4o")
            s3.set_attribute(GenAISemantics.USAGE_PROMPT_TOKENS, 480)
            s3.set_attribute(GenAISemantics.USAGE_COMPLETION_TOKENS, 75)
            s3.set_attribute(GenAISemantics.TOTAL_TOKENS, 555)
            cost = CostCalculator.calculate_cost("gpt-4o", 480, 75)
            s3.set_attribute(GenAISemantics.ESTIMATED_COST_USD, cost)

        # 4. Tool Execution
        with tracer.start_span("tool_execution:query_database") as s4:
            time.sleep(0.01)
            s4.set_attribute("tool.name", "query_database")
            s4.set_attribute("tool.status", "success")

        # 5. LLM Synthesis
        with tracer.start_span("llm_synthesis_final") as s5:
            time.sleep(0.02)
            s5.set_attribute(GenAISemantics.SYSTEM, "openai")
            s5.set_attribute(GenAISemantics.REQUEST_MODEL, "gpt-4o")
            s5.set_attribute(GenAISemantics.USAGE_PROMPT_TOKENS, 610)
            s5.set_attribute(GenAISemantics.USAGE_COMPLETION_TOKENS, 140)
            s5.set_attribute(GenAISemantics.TOTAL_TOKENS, 750)
            cost = CostCalculator.calculate_cost("gpt-4o", 610, 140)
            s5.set_attribute(GenAISemantics.ESTIMATED_COST_USD, cost)


# =====================================================================
# 5. Verification Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 88: OPENTELEMETRY GENAI OBSERVABILITY SUITE")
    print("=" * 80)

    tracer = OpenTelemetryGenAITracer()
    query = "Analyze Q3 revenue variance between North America and EMEA."
    print(f"Executing instrumented agent for query: '{query}'")

    execute_instrumented_agent(tracer, query)

    metrics = TraceReporter.print_trace_summary(tracer.recorded_spans)

    # Assertions
    assert metrics["span_count"] == 7, f"Expected 7 spans, got {metrics['span_count']}"
    assert metrics["total_tokens"] > 1000, "Expected >1000 total tokens burned"
    assert metrics["total_cost"] > 0.0, "Expected non-zero USD cost calculation"
    print("\n[SUCCESS] OpenTelemetry GenAI tracing engine verified with zero errors.")
