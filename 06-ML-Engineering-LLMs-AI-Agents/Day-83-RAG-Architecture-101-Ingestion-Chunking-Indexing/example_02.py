"""
Day 83: RAG Architecture 101: Ingestion, Chunking & Indexing
Part 2: Production End-to-End In-Memory RAG 101 Pipeline with "Lost-in-the-Middle" Mitigation

This module provides:
1. In-memory semantic vector store & mock dense embedding generator.
2. Complete ingestion workflow: text normalization, recursive chunking, and vector indexing.
3. Top-K semantic retriever with Cosine Distance scoring.
4. "Lost-in-the-Middle" context reordering engine placing top citations at prompt extremes.
5. Grounded context injection prompt synthesizer with citation tracking and anti-hallucination guardrails.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Re-use chunker from example_01
from example_01 import DocumentChunk, RecursiveCharacterChunker


# =====================================================================
# 1. Deterministic Semantic Embedding Generator
# =====================================================================

class DeterministicSemanticEmbedder:
    """
    Simulates dense semantic embeddings in R^d without requiring external API calls.
    Maps words to a semantic latent space using deterministic hashing and n-gram projections.
    """
    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed_text(self, text: str) -> np.ndarray:
        words = re.findall(r"\w+", text.lower())
        vec = np.zeros(self.dim, dtype=np.float32)
        if not words:
            return vec

        for idx, word in enumerate(words):
            # Seed pseudo-random generator with word hash
            w_hash = hash(word) % (2**31)
            np.random.seed(w_hash)
            token_vec = np.random.randn(self.dim).astype(np.float32)
            # Position-weighted accumulation
            weight = 1.0 / math.sqrt(idx + 1)
            vec += token_vec * weight

        # L2 normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec


# =====================================================================
# 2. In-Memory Vector Store & Retriever
# =====================================================================

@dataclass
class IndexedDocument:
    chunk_id: str
    text: str
    metadata: Dict[str, Any]
    vector: np.ndarray


@dataclass
class RetrievalMatch:
    chunk_id: str
    text: str
    score: float  # Cosine Similarity
    distance: float  # Cosine Distance
    metadata: Dict[str, Any]


class InMemoryVectorStore:
    def __init__(self, embedder: DeterministicSemanticEmbedder) -> None:
        self.embedder = embedder
        self.index: Dict[str, IndexedDocument] = {}

    def index_chunks(self, chunks: List[DocumentChunk]) -> None:
        for c in chunks:
            vec = self.embedder.embed_text(c.text)
            self.index[c.chunk_id] = IndexedDocument(
                chunk_id=c.chunk_id,
                text=c.text,
                metadata=c.metadata,
                vector=vec,
            )

    def retrieve(self, query: str, top_k: int = 5) -> List[RetrievalMatch]:
        q_vec = self.embedder.embed_text(query)
        matches: List[RetrievalMatch] = []

        for doc in self.index.values():
            # Dot product on unit vectors = Cosine Similarity
            sim = float(np.dot(q_vec, doc.vector))
            dist = 1.0 - sim
            matches.append(
                RetrievalMatch(
                    chunk_id=doc.chunk_id,
                    text=doc.text,
                    score=sim,
                    distance=dist,
                    metadata=doc.metadata,
                )
            )

        # Sort descending by similarity score (highest score first)
        matches.sort(key=lambda x: x.score, reverse=True)
        return matches[:top_k]


# =====================================================================
# 3. Context Injection & "Lost-in-the-Middle" Reorderer
# =====================================================================

class ContextInjectionEngine:
    """
    Formats retrieved chunks into grounded system prompts.
    Implements Liu et al. (2023) Lost-in-the-Middle mitigation:
    Reorders ranked chunks [1, 2, 3, 4, 5] -> [1, 3, 5, 4, 2] so strongest
    documents occupy the extreme head and tail of the prompt context window.
    """
    @staticmethod
    def reorder_lost_in_the_middle(matches: List[RetrievalMatch]) -> List[RetrievalMatch]:
        """
        Alternates items: Top rank at index 0, 2nd at end, 3rd at index 1, etc.
        """
        if len(matches) <= 2:
            return matches

        reordered: List[Optional[RetrievalMatch]] = [None] * len(matches)
        left = 0
        right = len(matches) - 1

        for idx, m in enumerate(matches):
            if idx % 2 == 0:
                reordered[left] = m
                left += 1
            else:
                reordered[right] = m
                right -= 1

        return [m for m in reordered if m is not None]

    @staticmethod
    def construct_grounded_prompt(
        query: str,
        retrieved_chunks: List[RetrievalMatch],
        apply_lost_in_middle_reordering: bool = True,
    ) -> str:
        chunks_to_inject = (
            ContextInjectionEngine.reorder_lost_in_the_middle(retrieved_chunks)
            if apply_lost_in_middle_reordering
            else retrieved_chunks
        )

        formatted_context: List[str] = []
        for rank, match in enumerate(chunks_to_inject, start=1):
            formatted_context.append(
                f"[DOCUMENT ID: {match.chunk_id}]\n{match.text}\n"
            )

        context_block = "\n---\n".join(formatted_context)

        prompt = f"""You are an enterprise AI knowledge assistant. Answer the user query strictly using the provided documents.

RULES:
1. State facts ONLY from the documents below. Do NOT hallucinate.
2. Cite the exact document ID for every claim using brackets, e.g. [doc_001].
3. If the context does not answer the question, say: "I do not have sufficient information to answer this question."

[RETRIEVED KNOWLEDGE CONTEXT]:
{context_block}

[USER QUERY]:
{query}
"""
        return prompt


# =====================================================================
# 4. End-to-End Pipeline Demonstration
# =====================================================================

def run_rag_pipeline_demo() -> None:
    print("=" * 80)
    print("DAY 83: END-TO-END RAG 101 PIPELINE VERIFICATION")
    print("=" * 80)

    # 1. Knowledge Base Corpus
    enterprise_knowledge = """
    Document A: Cloud Infrastructure & Incident Response
    Priority 1 (P1) outages require response within 15 minutes by the on-call Site Reliability Engineering (SRE) team.
    All post-mortems must be published to the engineering wiki within 48 hours of incident resolution.
    
    Document B: Employee Leave & Travel Policy
    Full-time employees receive 25 days of paid annual vacation. Travel expenses over $500 require pre-approval by a director.
    Meal per diems are capped at $75 per day for domestic travel and $120 for international travel.

    Document C: Security & Encryption Standards
    All database storage at rest must use AES-256 encryption with automated monthly key rotation.
    Transport security enforces TLS 1.3 with forward secrecy. Deprecated TLS 1.0 and 1.1 are completely disabled.
    """

    # 2. Ingestion & Chunking
    print("\n1. Ingesting and Chunking Knowledge Corpus...")
    chunker = RecursiveCharacterChunker(chunk_size_chars=220, chunk_overlap_chars=30)
    chunks = chunker.chunk(enterprise_knowledge, doc_id="kb_corp")
    print(f"   Generated {len(chunks)} knowledge chunks from raw documents.")

    # 3. Vector Store Indexing
    print("\n2. Embedding and Indexing Chunks into Vector Store...")
    embedder = DeterministicSemanticEmbedder(dim=64)
    vector_store = InMemoryVectorStore(embedder=embedder)
    vector_store.index_chunks(chunks)
    print(f"   Indexed {len(vector_store.index)} vectors into memory.")

    # 4. Retrieval Phase
    query = "What is the P1 outage response time SLA for SREs?"
    print(f"\n3. Executing Semantic Retrieval for Query:\n   \"{query}\"")
    top_matches = vector_store.retrieve(query, top_k=4)

    print("\n   Top-4 Retrieved Candidates (Ranked by Cosine Similarity):")
    for r, m in enumerate(top_matches, start=1):
        print(f"   [{r}] ID: {m.chunk_id} | Score: {m.score:.4f} | Preview: \"{m.text[:60]}...\"")

    # 5. Lost-in-the-Middle Reordering & Context Injection
    print("\n4. Applying Lost-in-the-Middle Context Reordering...")
    reordered_matches = ContextInjectionEngine.reorder_lost_in_the_middle(top_matches)
    
    original_ids = [m.chunk_id for m in top_matches]
    reordered_ids = [m.chunk_id for m in reordered_matches]
    print(f"   Original Ranking:  {original_ids}")
    print(f"   Reordered Context: {reordered_ids} (Top doc at start, 2nd doc at end)")

    grounded_prompt = ContextInjectionEngine.construct_grounded_prompt(
        query=query,
        retrieved_chunks=top_matches,
        apply_lost_in_middle_reordering=True,
    )

    print("\n5. Synthesized Grounded RAG Prompt Preview:")
    print("-" * 80)
    print(grounded_prompt[:450] + "\n... [truncated] ...\n")
    print("-" * 80)

    # Validations
    assert len(top_matches) == 4, "Expected 4 retrieved chunks"
    assert "P1" in top_matches[0].text or "outage" in top_matches[0].text.lower()
    assert reordered_ids[0] == original_ids[0], "Top candidate must be at index 0"
    assert reordered_ids[-1] == original_ids[1], "2nd candidate must be at final index"
    assert "[RETRIEVED KNOWLEDGE CONTEXT]" in grounded_prompt

    print("[SUCCESS] End-to-End RAG 101 Pipeline Assertions Passed!\n")


if __name__ == "__main__":
    run_rag_pipeline_demo()
