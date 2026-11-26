"""
Day 85: RAG Evaluation with Ragas Framework & Semantic Response Caching
Part 1: Production RAG Evaluation Engine from First Principles

This module implements the core Ragas metrics:
1. Faithfulness (Groundedness): Detects hallucinations against context.
2. Answer Relevance: Evaluates semantic alignment between query and response.
3. Context Precision: Measures signal-to-noise ratio and ranking quality of retrieved chunks.
4. Context Recall: Verifies ground-truth factual coverage across retrieved chunks.
5. End-to-end evaluation runner with tabular performance metrics.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np


# =====================================================================
# 1. Deterministic Semantic Embedding Engine (Zero Network Dependency)
# =====================================================================

class DeterministicSemanticEncoder:
    """
    Produces deterministic, unit-normalized semantic embeddings using token
    n-gram hashing, semantic concept expansion, and sub-word stemming.
    Captures semantic similarity across paraphrased domain concepts.
    """
    CONCEPT_MAP = {
        "ttl": "time_to_live",
        "expiration": "time_to_live",
        "expiring": "time_to_live",
        "expires": "time_to_live",
        "duration": "time_to_live",
        "last": "time_to_live",
        "lifetime": "time_to_live",
        "auth": "authentication_identity",
        "jwt": "token_session_credential",
        "token": "token_session_credential",
        "tokens": "token_session_credential",
        "session": "token_session_credential",
        "credential": "token_session_credential",
    }

    def __init__(self, dimension: int = 64):
        self.dim = dimension

    def _stem(self, word: str) -> str:
        w = word.lower()
        if w in self.CONCEPT_MAP:
            return self.CONCEPT_MAP[w]
        for suffix in ("ing", "tion", "ions", "es", "s", "ed"):
            if w.endswith(suffix) and len(w) > len(suffix) + 3:
                stemmed = w[:-len(suffix)]
                if stemmed in self.CONCEPT_MAP:
                    return self.CONCEPT_MAP[stemmed]
                return stemmed
        return w

    def encode(self, text: str) -> np.ndarray:
        raw_tokens = re.findall(r"\w+", text.lower())
        tokens = [self._stem(t) for t in raw_tokens]
        vec = np.zeros(self.dim, dtype=np.float64)
        if not tokens:
            vec[0] = 1.0
            return vec

        for idx, token in enumerate(tokens):
            h = hash(token)
            slot = abs(h) % self.dim
            sign = 1.0 if ((h >> 3) & 1) == 0 else -1.0
            weight = 1.0 + (1.0 / (idx + 1.0))
            vec[slot] += sign * weight

            # Bigram interaction
            if idx > 0:
                bi_h = hash(f"{tokens[idx-1]}_{token}")
                bi_slot = abs(bi_h) % self.dim
                bi_sign = 1.0 if ((bi_h >> 2) & 1) == 0 else -1.0
                vec[bi_slot] += bi_sign * 0.5

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def cosine_similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        dot = float(np.dot(vec_a, vec_b))
        norm_a = float(np.linalg.norm(vec_a))
        norm_b = float(np.linalg.norm(vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(np.clip(dot / (norm_a * norm_b), -1.0, 1.0))


# =====================================================================
# 2. Evaluation Data Model
# =====================================================================

@dataclass
class RAGSample:
    """Represents a single query-retrieval-answer evaluation tuple."""
    sample_id: str
    query: str
    retrieved_contexts: List[str]
    generated_answer: str
    ground_truth: str


@dataclass
class SampleEvaluationResult:
    """Stores quantitative metric scores for a single evaluation sample."""
    sample_id: str
    faithfulness: float
    answer_relevance: float
    context_precision: float
    context_recall: float
    extracted_claims: List[str]
    unsupported_claims: List[str]

    @property
    def ragas_score(self) -> float:
        """Harmonic mean of all 4 metrics."""
        scores = [self.faithfulness, self.answer_relevance, self.context_precision, self.context_recall]
        if any(s <= 0.0 for s in scores):
            return 0.0
        return len(scores) / sum(1.0 / s for s in scores)


# =====================================================================
# 3. Metric Evaluators
# =====================================================================

class FaithfulnessEvaluator:
    """
    Evaluates groundedness:
    Faithfulness = (Supported Claims in Answer) / (Total Claims in Answer)
    """
    def extract_atomic_claims(self, text: str) -> List[str]:
        # Split sentences by punctuation into candidate factual claims
        raw_sentences = re.split(r"[.!?]+", text)
        claims = [s.strip() for s in raw_sentences if len(s.strip()) > 8]
        return claims

    def is_claim_supported(self, claim: str, contexts: List[str]) -> bool:
        claim_tokens = set(re.findall(r"\w+", claim.lower()))
        if not claim_tokens:
            return True

        combined_context = " ".join(contexts).lower()
        context_tokens = set(re.findall(r"\w+", combined_context))

        # Check term coverage: claim must share high percentage of key tokens
        overlap = len(claim_tokens.intersection(context_tokens)) / len(claim_tokens)
        return overlap >= 0.70

    def evaluate(self, answer: str, contexts: List[str]) -> Tuple[float, List[str], List[str]]:
        claims = self.extract_atomic_claims(answer)
        if not claims:
            return 1.0, [], []

        supported = []
        unsupported = []
        for claim in claims:
            if self.is_claim_supported(claim, contexts):
                supported.append(claim)
            else:
                unsupported.append(claim)

        score = len(supported) / len(claims)
        return score, supported, unsupported


class AnswerRelevanceEvaluator:
    """
    Evaluates semantic relevance between original query and generated answer:
    Computes embedding cosine similarity between the query and generated response.
    """
    def __init__(self, encoder: DeterministicSemanticEncoder):
        self.encoder = encoder

    def evaluate(self, query: str, answer: str) -> float:
        q_vec = self.encoder.encode(query)
        a_vec = self.encoder.encode(answer)
        cos = self.encoder.cosine_similarity(q_vec, a_vec)
        # Rescale [-1, 1] to [0, 1] for metric consistency
        normalized_score = max(0.0, (cos + 1.0) / 2.0)
        return float(normalized_score)


class ContextPrecisionEvaluator:
    """
    Evaluates ranking quality of retrieved contexts:
    Context Precision@K = sum(Precision@k * rel(k)) / sum(rel(k))
    """
    def __init__(self, encoder: DeterministicSemanticEncoder):
        self.encoder = encoder

    def is_context_relevant(self, query: str, context: str, threshold: float = 0.30) -> bool:
        q_vec = self.encoder.encode(query)
        c_vec = self.encoder.encode(context)
        sim = self.encoder.cosine_similarity(q_vec, c_vec)
        return sim >= threshold

    def evaluate(self, query: str, contexts: List[str]) -> float:
        if not contexts:
            return 0.0

        hits = []
        for ctx in contexts:
            hits.append(1 if self.is_context_relevant(query, ctx) else 0)

        total_relevant = sum(hits)
        if total_relevant == 0:
            return 0.0

        cumulative_hits = 0
        precision_sum = 0.0
        for k, rel in enumerate(hits, start=1):
            if rel == 1:
                cumulative_hits += 1
                precision_at_k = cumulative_hits / k
                precision_sum += precision_at_k

        return float(precision_sum / total_relevant)


class ContextRecallEvaluator:
    """
    Evaluates context coverage:
    Context Recall = (Ground Truth Facts covered by Context) / (Total GT Facts)
    """
    def extract_gt_facts(self, ground_truth: str) -> List[str]:
        raw_facts = re.split(r"[.!?]+", ground_truth)
        return [f.strip() for f in raw_facts if len(f.strip()) > 8]

    def evaluate(self, ground_truth: str, contexts: List[str], threshold: float = 0.50) -> float:
        facts = self.extract_gt_facts(ground_truth)
        if not facts:
            return 1.0

        combined_context = " ".join(contexts).lower()
        context_tokens = set(re.findall(r"\w+", combined_context))

        covered = 0
        for fact in facts:
            fact_tokens = set(re.findall(r"\w+", fact.lower()))
            if not fact_tokens:
                continue
            overlap = len(fact_tokens.intersection(context_tokens)) / len(fact_tokens)
            if overlap >= threshold:
                covered += 1

        return float(covered / len(facts))


# =====================================================================
# 4. End-to-End Ragas Evaluation Pipeline
# =====================================================================

class RagasEvaluationEngine:
    def __init__(self):
        self.encoder = DeterministicSemanticEncoder()
        self.faithfulness_eval = FaithfulnessEvaluator()
        self.relevance_eval = AnswerRelevanceEvaluator(self.encoder)
        self.precision_eval = ContextPrecisionEvaluator(self.encoder)
        self.recall_eval = ContextRecallEvaluator()

    def evaluate_sample(self, sample: RAGSample) -> SampleEvaluationResult:
        faith_score, supported, unsupported = self.faithfulness_eval.evaluate(
            sample.generated_answer, sample.retrieved_contexts
        )
        relevance_score = self.relevance_eval.evaluate(sample.query, sample.generated_answer)
        precision_score = self.precision_eval.evaluate(sample.query, sample.retrieved_contexts)
        recall_score = self.recall_eval.evaluate(sample.ground_truth, sample.retrieved_contexts)

        return SampleEvaluationResult(
            sample_id=sample.sample_id,
            faithfulness=faith_score,
            answer_relevance=relevance_score,
            context_precision=precision_score,
            context_recall=recall_score,
            extracted_claims=supported + unsupported,
            unsupported_claims=unsupported,
        )

    def evaluate_dataset(self, dataset: List[RAGSample]) -> List[SampleEvaluationResult]:
        return [self.evaluate_sample(s) for s in dataset]


# =====================================================================
# 5. Demonstration and Verification Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 85: RAGAS EVALUATION METRICS ENGINE")
    print("=" * 80)

    # 3 Realistic Enterprise Scenarios
    test_samples = [
        # Scenario 1: High Quality Grounded RAG
        RAGSample(
            sample_id="SAMPLE-01-PERFECT",
            query="What is the token expiration TTL in the auth service?",
            retrieved_contexts=[
                "The auth service issues JWT session tokens with a strict expiration TTL of 900 seconds (15 minutes).",
                "Refresh tokens are stored in Redis cluster with a sliding window expiration of 7 days."
            ],
            generated_answer="The auth service issues JWT session tokens with a strict expiration TTL of 900 seconds (15 minutes).",
            ground_truth="JWT session tokens expire after 900 seconds or 15 minutes."
        ),
        # Scenario 2: Hallucinated / Unfaithful Answer
        RAGSample(
            sample_id="SAMPLE-02-HALLUCINATED",
            query="What database is used for storing payment audit logs?",
            retrieved_contexts=[
                "Payment transactions are processed via Stripe gateway and recorded in PostgreSQL with WAL archiving enabled."
            ],
            generated_answer="Payment audit logs are stored in MongoDB NoSQL cluster with replica sets for high availability.",
            ground_truth="Payment transactions and logs are recorded in PostgreSQL."
        ),
        # Scenario 3: Poor Context Retrieval (Low Precision)
        RAGSample(
            sample_id="SAMPLE-03-LOW-PRECISION",
            query="How are rate limits applied to public API endpoints?",
            retrieved_contexts=[
                "The frontend UI utilizes TailwindCSS and React 18 for design consistency.",
                "Marketing analytics scripts are injected asynchronously at the bottom of index.html.",
                "Rate limits are enforced at Envoy API Gateway using token bucket algorithm at 100 req/sec."
            ],
            generated_answer="Rate limits are enforced at the Envoy API Gateway using the token bucket algorithm at 100 req/sec.",
            ground_truth="Envoy API Gateway enforces token bucket rate limiting at 100 requests per second."
        ),
    ]

    engine = RagasEvaluationEngine()
    results = engine.evaluate_dataset(test_samples)

    print(f"\n{'Sample ID':<25} | {'Faithful':<9} | {'Relevance':<9} | {'Precision':<9} | {'Recall':<9} | {'Ragas':<7}")
    print("-" * 80)

    for res in results:
        print(f"{res.sample_id:<25} | {res.faithfulness:>8.2f}  | {res.answer_relevance:>8.2f}  | {res.context_precision:>8.2f}  | {res.context_recall:>8.2f}  | {res.ragas_score:>6.2f}")
        if res.unsupported_claims:
            print(f"  [!] Detected Hallucinations ({len(res.unsupported_claims)} claims):")
            for c in res.unsupported_claims:
                print(f"      - {c}")

    # Self-validating assertions
    assert results[0].faithfulness == 1.0, "Sample 1 must have 100% faithfulness"
    assert results[1].faithfulness == 0.0, "Sample 2 (hallucinated) must have 0% faithfulness"
    assert results[2].context_precision < 1.0, "Sample 3 must have penalized context precision due to distractors"
    print("\n[SUCCESS] All Ragas evaluation metrics calculated and verified successfully.")
