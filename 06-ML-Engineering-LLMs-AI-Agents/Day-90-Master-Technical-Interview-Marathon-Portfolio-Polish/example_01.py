"""
Day 90: Master Technical Interview Marathon & Portfolio Polish
Part 1: Comprehensive Backend & AI Engineering Technical Competency Testing Suite

This module implements:
1. TechnicalCompetencyEvaluator assessing mastery across all 6 curriculum modules:
   - Module 1: Python Systems Internals & Memory Management
   - Module 2: High-Performance Networking & gRPC / WebSockets
   - Module 3: Database Storage Engines, MVCC & Redis
   - Module 4: Distributed Systems, Kafka & Event-Driven Architecture
   - Module 5: Cloud Engineering, Security, CI/CD & Observability
   - Module 6: ML Engineering, Vector DBs, RAG & Autonomous LangGraph Agents
2. Quantitative scoring engine grading architectural tradeoff reasoning.
3. Formatted diagnostic scorecard with senior engineering leveling benchmarks.
4. Comprehensive verification assertions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Tuple


class EngineeringLevel(str, Enum):
    PRINCIPAL_STAFF = "Principal / Staff AI Systems Engineer"
    SENIOR_ENGINEER = "Senior Backend & AI Engineer"
    MID_LEVEL = "Mid-Level Software Engineer"
    FOUNDATIONAL = "Associate Engineer"


@dataclass
class DomainQuestion:
    question_id: str
    domain: str
    prompt: str
    expected_keywords: List[str]
    weight: float = 1.0


@dataclass
class EvaluationReport:
    total_score: float
    max_possible_score: float
    percentage: float
    level: EngineeringLevel
    domain_breakdown: Dict[str, float]
    passed_audit: bool


class TechnicalCompetencyEvaluator:
    """
    Automated evaluation engine scoring mastery of the complete 90-day curriculum.
    """
    def __init__(self):
        self.question_bank: List[DomainQuestion] = [
            # Module 1: Python Core & Systems
            DomainQuestion(
                question_id="M1_01",
                domain="Python Systems Internals",
                prompt="How does CPython manage memory and handle cyclic references?",
                expected_keywords=["reference counting", "garbage collector", "generational", "cyclic references", "tp_traverse"],
            ),
            # Module 2: Networking & Protocols
            DomainQuestion(
                question_id="M2_01",
                domain="High-Performance Networking",
                prompt="Why does gRPC offer superior throughput compared to REST over HTTP/1.1?",
                expected_keywords=["http/2", "protocol buffers", "binary serialization", "multiplexing", "single tcp connection"],
            ),
            # Module 3: Database Engineering
            DomainQuestion(
                question_id="M3_01",
                domain="Database Storage Engines",
                prompt="Explain the write-amplification and read-performance tradeoffs between B-Trees and LSM-Trees.",
                expected_keywords=["b-tree", "lsm-tree", "in-place updates", "memtable", "sstable", "compaction"],
            ),
            # Module 4: Distributed Systems
            DomainQuestion(
                question_id="M4_01",
                domain="Distributed Systems & Kafka",
                prompt="How does Apache Kafka guarantee exactly-once processing (EOS) semantics?",
                expected_keywords=["transactional producer", "idempotent producer", "two-phase commit", "consumer offset commit"],
            ),
            # Module 5: Cloud & Observability
            DomainQuestion(
                question_id="M5_01",
                domain="Cloud & Observability",
                prompt="How do OpenTelemetry GenAI semantic conventions track token usage and cost attribution?",
                expected_keywords=["gen_ai.usage.prompt_tokens", "gen_ai.usage.completion_tokens", "spans", "distributed tracing"],
            ),
            # Module 6: ML & AI Agents
            DomainQuestion(
                question_id="M6_01",
                domain="AI Agents & LangGraph",
                prompt="How do state reducers and checkpoints enable fault-tolerant cyclic agent workflows?",
                expected_keywords=["state reducer", "operator.add", "checkpoint", "postgres", "human-in-the-loop", "cycles"],
            ),
        ]

    def evaluate_candidate_answers(self, candidate_responses: Dict[str, str]) -> EvaluationReport:
        total_score = 0.0
        max_score = 0.0
        domain_scores: Dict[str, float] = {}
        domain_counts: Dict[str, float] = {}

        for q in self.question_bank:
            max_score += q.weight
            answer = candidate_responses.get(q.question_id, "").lower()
            
            # Count keyword hits
            matches = sum(1 for kw in q.expected_keywords if kw in answer)
            score_ratio = matches / len(q.expected_keywords) if q.expected_keywords else 1.0
            earned = score_ratio * q.weight

            total_score += earned
            domain_scores[q.domain] = domain_scores.get(q.domain, 0.0) + earned
            domain_counts[q.domain] = domain_counts.get(q.domain, 0.0) + q.weight

        pct = (total_score / max_score) * 100.0 if max_score > 0 else 0.0

        if pct >= 85.0:
            level = EngineeringLevel.PRINCIPAL_STAFF
        elif pct >= 70.0:
            level = EngineeringLevel.SENIOR_ENGINEER
        elif pct >= 50.0:
            level = EngineeringLevel.MID_LEVEL
        else:
            level = EngineeringLevel.FOUNDATIONAL

        domain_percentages = {
            dom: round((domain_scores[dom] / domain_counts[dom]) * 100.0, 1)
            for dom in domain_scores
        }

        return EvaluationReport(
            total_score=round(total_score, 2),
            max_possible_score=round(max_score, 2),
            percentage=round(pct, 1),
            level=level,
            domain_breakdown=domain_percentages,
            passed_audit=pct >= 75.0,
        )


# =====================================================================
# 2. Verification Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 90: MASTER TECHNICAL INTERVIEW COMPETENCY EVALUATION SUITE")
    print("=" * 80)

    evaluator = TechnicalCompetencyEvaluator()

    # Candidate submission demonstrating Principal/Staff level mastery across all 6 modules
    mastery_submission = {
        "M1_01": "CPython uses reference counting for immediate cleanup and a generational garbage collector with tp_traverse to resolve cyclic references.",
        "M2_01": "gRPC leverages HTTP/2 multiplexing over a single TCP connection, streaming headers and using binary serialization with Protocol Buffers.",
        "M3_01": "B-Tree engines do in-place updates optimized for read-heavy workloads, whereas LSM-Tree engines write sequentially to a Memtable and flush to SSTable with compaction.",
        "M4_01": "Kafka achieves exactly-once processing using an idempotent producer with sequence numbers, combined with a transactional producer and consumer offset commit.",
        "M5_01": "OpenTelemetry records GenAI spans with gen_ai.usage.prompt_tokens, gen_ai.usage.completion_tokens, and distributed tracing metadata for cost attribution.",
        "M6_01": "LangGraph uses state reducer rules like operator.add to merge parallel node updates, and durable checkpoint stores in Postgres to handle cycles and human-in-the-loop gates.",
    }

    report = evaluator.evaluate_candidate_answers(mastery_submission)

    print(f"Overall Score      : {report.total_score} / {report.max_possible_score} ({report.percentage}%)")
    print(f"Assessed Level     : {report.level.value}")
    print(f"Mastery Audit Status: {'PASSED [100% Curriculum Certification]' if report.passed_audit else 'NEEDS REVIEW'}")
    print("\nDomain-by-Domain Competency Breakdown:")
    print("-" * 80)
    for domain, score_pct in report.domain_breakdown.items():
        print(f"  - {domain:<35}: {score_pct:>5.1f}%")

    # Assertions
    assert report.percentage >= 90.0, f"Expected >=90% mastery score, got {report.percentage}%"
    assert report.level == EngineeringLevel.PRINCIPAL_STAFF
    assert report.passed_audit is True

    print("\n[SUCCESS] Master Technical Competency Evaluation Suite passed all verification tests.")
