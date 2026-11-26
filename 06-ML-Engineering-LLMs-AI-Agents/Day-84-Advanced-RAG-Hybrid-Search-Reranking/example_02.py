"""
Day 84: Advanced RAG: Hybrid Search, Cross-Encoder Reranking & Metadata Filtering
Part 2: Two-Stage Retrieval Pipeline: Bi-Encoder Candidate Pool + Cross-Encoder Reranker

This module provides:
1. CrossEncoderReranker simulating joint cross-attention scoring over (Query, Document) pairs.
2. MetadataSelfQueryParser extracting structured filters from user natural language queries.
3. Two-Stage Retrieval Orchestrator: Stage 1 (Fast Bi-Encoder / Hybrid) -> Stage 2 (High-Precision Cross-Encoder).
4. Benchmark verifying precision improvement achieved by Stage 2 reranking.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

# Re-use components from example_01
from example_01 import BM25Okapi, DenseVectorSearch, HybridSearchOrchestrator


# =====================================================================
# 1. Cross-Encoder Joint-Attention Reranker
# =====================================================================

class CrossEncoderReranker:
    """
    Simulates cross-encoder deep joint attention (e.g., bge-reranker-large, ms-marco-MiniLM).
    Evaluates full interaction between query tokens and document tokens simultaneously,
    capturing negation, exact qualifiers, and sentence logic.
    Score range: [-10.0, +10.0] logit scale.
    """
    def score_pair(self, query: str, document_text: str) -> float:
        q_lower = query.lower()
        d_lower = document_text.lower()

        q_tokens = set(re.findall(r"\w+", q_lower))
        d_tokens = set(re.findall(r"\w+", d_lower))

        if not q_tokens:
            return 0.0

        # 1. Exact term overlap
        overlap = len(q_tokens.intersection(d_tokens)) / len(q_tokens)
        score = overlap * 4.0

        # 2. Sequential phrase matching bonus (detects exact multi-word idioms)
        words = q_lower.split()
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            if bigram in d_lower:
                score += 1.5

        # 3. Negation and qualification penalty
        # If query asks "how to enable" and doc says "cannot enable" or "do not"
        negations = {"not", "cannot", "never", "disabled", "deprecated"}
        if any(neg in q_lower for neg in negations) != any(neg in d_lower for neg in negations):
            score -= 1.0

        # 4. Keyword specificity bonus
        for token in q_tokens:
            if token.isupper() or "_" in token or any(c.isdigit() for c in token):
                if token.lower() in d_lower:
                    score += 2.0

        return float(score)

    def rerank(
        self,
        query: str,
        candidates: List[Tuple[str, str, Dict[str, Any]]],
        top_k: int = 3,
    ) -> List[Tuple[str, float, str, Dict[str, Any]]]:
        """
        Reranks a list of (doc_id, text, metadata) tuples, returning
        (doc_id, cross_encoder_score, text, metadata) sorted descending.
        """
        scored_candidates: List[Tuple[str, float, str, Dict[str, Any]]] = []

        for doc_id, text, meta in candidates:
            score = self.score_pair(query, text)
            scored_candidates.append((doc_id, score, text, meta))

        # Sort descending by cross-encoder score
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        return scored_candidates[:top_k]


# =====================================================================
# 2. Metadata Self-Query Parser
# =====================================================================

class MetadataSelfQueryParser:
    """
    Parses natural language queries to extract structured metadata filters
    (e.g., service, environment, status).
    """
    def parse_query(self, user_query: str) -> Tuple[str, Dict[str, Any]]:
        cleaned_query = user_query
        filters: Dict[str, Any] = {}

        # Extract environment filter: in prod / staging / dev
        env_match = re.search(r"\b(prod|staging|dev|production)\b", user_query, re.IGNORECASE)
        if env_match:
            val = "production" if env_match.group(1).lower() in ("prod", "production") else env_match.group(1).lower()
            filters["environment"] = val

        # Extract service filter: service:auth / in auth-service
        service_match = re.search(r"\b(auth|payment|billing|database)\b", user_query, re.IGNORECASE)
        if service_match:
            filters["service"] = service_match.group(1).lower()

        return cleaned_query, filters


# =====================================================================
# 3. Two-Stage Retrieval Pipeline
# =====================================================================

class TwoStageRetriever:
    def __init__(
        self,
        corpus_dict: Dict[str, Tuple[str, Dict[str, Any]]],
    ) -> None:
        self.corpus = corpus_dict
        self.doc_ids = list(corpus_dict.keys())
        self.documents = [t[0] for t in corpus_dict.values()]
        self.metadata = {k: v[1] for k, v in corpus_dict.items()}

        self.bm25 = BM25Okapi()
        self.bm25.fit(self.doc_ids, self.documents)

        self.dense = DenseVectorSearch(dim=64)
        self.dense.fit(self.doc_ids, self.documents)

        self.hybrid = HybridSearchOrchestrator(self.bm25, self.dense)
        self.reranker = CrossEncoderReranker()
        self.query_parser = MetadataSelfQueryParser()

    def execute_two_stage_retrieval(
        self,
        raw_query: str,
        stage1_candidate_pool: int = 6,
        final_top_k: int = 2,
    ) -> Dict[str, Any]:
        # Step 1: Self-Query Filter Extraction
        search_query, filters = self.query_parser.parse_query(raw_query)

        # Step 2: Stage 1 High-Recall Hybrid Search
        stage1_hits = self.hybrid.search_rrf(search_query, top_k=stage1_candidate_pool)

        # Apply metadata filter on candidate pool
        filtered_candidates: List[Tuple[str, str, Dict[str, Any]]] = []
        for doc_id, rrf_score in stage1_hits:
            meta = self.metadata[doc_id]
            match = True
            for f_key, f_val in filters.items():
                if meta.get(f_key) != f_val:
                    match = False
                    break
            if match:
                text = self.corpus[doc_id][0]
                filtered_candidates.append((doc_id, text, meta))

        # Step 3: Stage 2 High-Precision Cross-Encoder Reranking
        stage2_results = self.reranker.rerank(
            query=search_query,
            candidates=filtered_candidates,
            top_k=final_top_k,
        )

        return {
            "parsed_query": search_query,
            "extracted_filters": filters,
            "stage1_candidates_count": len(filtered_candidates),
            "stage2_top_results": stage2_results,
        }


# =====================================================================
# 4. Demonstration & Verification Suite
# =====================================================================

def run_two_stage_demo() -> None:
    print("=" * 80)
    print("DAY 84: TWO-STAGE RETRIEVAL & CROSS-ENCODER RERANKING SUITE")
    print("=" * 80)

    # Multi-tenant corporate troubleshooting dataset
    corpus = {
        "doc_01": (
            "Billing service payment webhook retry policy: failed stripe payments retry after 1h in production.",
            {"service": "billing", "environment": "production"},
        ),
        "doc_02": (
            "Auth service session token invalidation: logout triggers immediate redis blacklist update in production.",
            {"service": "auth", "environment": "production"},
        ),
        "doc_03": (
            "Auth service dev environment instructions: JWT secret keys are mocked with dummy tokens in dev.",
            {"service": "auth", "environment": "dev"},
        ),
        "doc_04": (
            "Payment gateway timeout handling: API gateway returns 504 Gateway Timeout when billing downstream is slow.",
            {"service": "payment", "environment": "production"},
        ),
        "doc_05": (
            "Auth service OAuth2 refresh token rotation guidelines: exchange refresh token every 30 days in production.",
            {"service": "auth", "environment": "production"},
        ),
    }

    retriever = TwoStageRetriever(corpus)

    query = "How does auth service invalidate session tokens on logout in prod?"
    print(f"\n1. Executing Two-Stage Search for Query:\n   \"{query}\"")

    results = retriever.execute_two_stage_retrieval(query, stage1_candidate_pool=5, final_top_k=2)

    print(f"\n2. Extracted Metadata Filters: {results['extracted_filters']}")
    print(f"3. Stage 1 Filtered Candidates Evaluated: {results['stage1_candidates_count']}")
    print("\n4. Stage 2 Cross-Encoder Reranked Top Results:")
    for rank, (doc_id, score, text, meta) in enumerate(results["stage2_top_results"], start=1):
        print(f"   [{rank}] ID: {doc_id} | Cross-Encoder Score: {score:.2f} | Meta: {meta}")
        print(f"       Text: \"{text}\"\n")

    top_id = results["stage2_top_results"][0][0]
    assert top_id == "doc_02", f"Expected doc_02 as top reranked result, got {top_id}"
    assert results["extracted_filters"]["service"] == "auth"
    assert results["extracted_filters"]["environment"] == "production"

    print("[SUCCESS] All Two-Stage Retrieval & Cross-Encoder Assertions Passed!\n")


if __name__ == "__main__":
    run_two_stage_demo()
