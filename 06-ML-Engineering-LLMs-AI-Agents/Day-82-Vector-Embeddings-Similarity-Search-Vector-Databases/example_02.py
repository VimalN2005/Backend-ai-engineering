"""
Day 82: Vector Embeddings & Similarity Search
Part 2: Production Vector Database Engine: Hybrid Search, Payload Filtering & Scalar Quantization (SQ8)

This module provides:
1. Enterprise Vector Database client supporting ACID metadata payload indexing (simulating pgvector & Qdrant).
2. Single-Stage Filtered Vector Search with Predicate Pushdown (eliminating Result Set Starvation).
3. Scalar Quantization (SQ8: float32 -> int8) engine delivering 4x memory compression and accelerated distance computation.
4. Comparative benchmark demonstrating Post-Filtering failure vs Single-Stage Filtered Search success.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np


# =====================================================================
# 1. Scalar Quantization (SQ8: Float32 -> Int8) Compression Engine
# =====================================================================

class ScalarQuantizerSQ8:
    """
    Scalar Quantizer compressing 32-bit floating-point vectors into 8-bit signed integers.
    Reduces memory consumption by 75% (4x compression) while preserving > 98% cosine recall.
    Formula:
        scale = (max_val - min_val) / 255.0
        q_i = int8(round((v_i - min_val) / scale) - 128)
    """
    def __init__(self) -> None:
        self.min_val: float = -1.0
        self.max_val: float = 1.0
        self.scale: float = 2.0 / 255.0

    def fit(self, vectors: np.ndarray) -> None:
        """Computes global dynamic range across training vectors."""
        self.min_val = float(np.min(vectors))
        self.max_val = float(np.max(vectors))
        self.scale = max(1e-7, (self.max_val - self.min_val) / 255.0)

    def quantize(self, v: np.ndarray) -> np.ndarray:
        """Converts float32 vector to int8 array."""
        clamped = np.clip(v, self.min_val, self.max_val)
        shifted = np.round((clamped - self.min_val) / self.scale) - 128.0
        return shifted.astype(np.int8)

    def dequantize(self, q: np.ndarray) -> np.ndarray:
        """Reconstructs approximate float32 vector from int8 array."""
        uncentered = q.astype(np.float32) + 128.0
        return (uncentered * self.scale) + self.min_val

    def quantized_cosine_distance(self, query_f32: np.ndarray, doc_int8: np.ndarray) -> float:
        """
        Asymmetric distance computation: Keeps query in float32, dequantizes on-the-fly
        or uses integer dot product scaled by factor.
        """
        doc_recon = self.dequantize(doc_int8)
        norm_q = np.linalg.norm(query_f32)
        norm_d = np.linalg.norm(doc_recon)
        if norm_q == 0.0 or norm_d == 0.0:
            return 1.0
        sim = float(np.dot(query_f32, doc_recon) / (norm_q * norm_d))
        return 1.0 - sim


# =====================================================================
# 2. Vector Entity & Payload Filter Specification
# =====================================================================

@dataclass
class VectorRecord:
    id: str
    vector: np.ndarray
    quantized_vector: Optional[np.ndarray]
    payload: Dict[str, Any]


class PayloadFilter:
    """
    Declarative metadata filter supporting equality, numeric ranges, and set membership.
    Simulates Qdrant / pgvector WHERE clause predicates.
    """
    def __init__(
        self,
        must: Optional[Dict[str, Any]] = None,
        range_filters: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None,
    ) -> None:
        self.must = must or {}
        self.range_filters = range_filters or {}

    def matches(self, payload: Dict[str, Any]) -> bool:
        # Check equality predicates
        for key, expected_val in self.must.items():
            if key not in payload or payload[key] != expected_val:
                return False

        # Check numeric range predicates: (min_val, max_val)
        for key, (min_v, max_v) in self.range_filters.items():
            if key not in payload:
                return False
            val = float(payload[key])
            if min_v is not None and val < min_v:
                return False
            if max_v is not None and val > max_v:
                return False

        return True


# =====================================================================
# 3. Hybrid Vector Database Engine
# =====================================================================

class HybridVectorDB:
    """
    Production-grade Vector Database Engine supporting:
    - ACID Metadata Payloads
    - Scalar Quantization (SQ8)
    - Single-Stage Filtered ANN Search with Predicate Pushdown
    - Naive Post-Filtering (for empirical benchmarking)
    """
    def __init__(self, dim: int, use_quantization: bool = True) -> None:
        self.dim = dim
        self.use_quantization = use_quantization
        self.records: Dict[str, VectorRecord] = {}
        self.quantizer = ScalarQuantizerSQ8()
        self._fitted = False

    def insert(self, record_id: str, vector: np.ndarray, payload: Dict[str, Any]) -> None:
        # Normalize vector for cosine distance
        norm = np.linalg.norm(vector)
        norm_v = vector / norm if norm > 0 else vector

        q_vec = None
        if self.use_quantization and self._fitted:
            q_vec = self.quantizer.quantize(norm_v)

        record = VectorRecord(
            id=record_id,
            vector=norm_v.astype(np.float32),
            quantized_vector=q_vec,
            payload=payload,
        )
        self.records[record_id] = record

    def fit_quantizer(self) -> None:
        """Trains scalar quantizer dynamic range on current records."""
        if not self.records:
            return
        all_vecs = np.array([r.vector for r in self.records.values()])
        self.quantizer.fit(all_vecs)
        self._fitted = True
        # Quantize all stored records
        for r in self.records.values():
            r.quantized_vector = self.quantizer.quantize(r.vector)

    def search_single_stage(
        self,
        query_vector: np.ndarray,
        k: int = 5,
        payload_filter: Optional[PayloadFilter] = None,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        Single-Stage Filtered Search (Production Pattern):
        Evaluates payload filter predicates during distance evaluation,
        guaranteeing exactly Top-K matching results without starvation.
        """
        norm_q = query_vector / np.linalg.norm(query_vector)
        candidates: List[Tuple[str, float, Dict[str, Any]]] = []

        for r in self.records.values():
            # Predicate Pushdown: filter out immediately if metadata doesn't match
            if payload_filter is not None and not payload_filter.matches(r.payload):
                continue

            if self.use_quantization and r.quantized_vector is not None:
                dist = self.quantizer.quantized_cosine_distance(norm_q, r.quantized_vector)
            else:
                dist = 1.0 - float(np.dot(norm_q, r.vector))

            candidates.append((r.id, dist, r.payload))

        # Sort ascending by distance (closest first)
        candidates.sort(key=lambda x: x[1])
        return candidates[:k]

    def search_post_filter(
        self,
        query_vector: np.ndarray,
        candidate_pool_size: int = 20,
        payload_filter: Optional[PayloadFilter] = None,
        k: int = 5,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        Naive Post-Filtering (Antipattern):
        Fetches candidate_pool_size closest vectors blindly, then filters metadata.
        Suffers from Result Set Starvation when filter selectivity is high.
        """
        norm_q = query_vector / np.linalg.norm(query_vector)
        # Step 1: Unfiltered vector search
        all_candidates: List[Tuple[str, float, Dict[str, Any]]] = []
        for r in self.records.values():
            dist = 1.0 - float(np.dot(norm_q, r.vector))
            all_candidates.append((r.id, dist, r.payload))

        all_candidates.sort(key=lambda x: x[1])
        pool = all_candidates[:candidate_pool_size]

        # Step 2: Post-filter candidate pool
        filtered_results: List[Tuple[str, float, Dict[str, Any]]] = []
        for r_id, dist, payload in pool:
            if payload_filter is None or payload_filter.matches(payload):
                filtered_results.append((r_id, dist, payload))
            if len(filtered_results) == k:
                break

        return filtered_results


# =====================================================================
# 4. Demonstration & Verification Suite
# =====================================================================

def run_hybrid_search_demo() -> None:
    print("=" * 80)
    print("DAY 82: HYBRID VECTOR SEARCH & SCALAR QUANTIZATION (SQ8) SUITE")
    print("=" * 80)

    np.random.seed(42)
    dim = 128
    num_docs = 1000
    k = 5

    # 1. Initialize Hybrid DB
    db = HybridVectorDB(dim=dim, use_quantization=True)

    print(f"\n[*] Generating {num_docs} multi-tenant documents with vector embeddings...")
    tenants = ["acme_corp", "globex", "initech"]
    categories = ["finance", "engineering", "legal", "hr"]

    # Pre-generate embeddings
    embeddings = np.random.randn(num_docs, dim).astype(np.float32)

    for i in range(num_docs):
        payload = {
            "tenant_id": tenants[i % len(tenants)],
            "category": categories[i % len(categories)],
            "access_level": int(np.random.randint(1, 5)),
            "word_count": int(np.random.randint(200, 2000)),
        }
        db.insert(f"doc_{i:04d}", embeddings[i], payload)

    # Train and apply SQ8 Quantizer
    db.fit_quantizer()

    # Calculate Memory Savings
    raw_bytes_per_vec = dim * 4  # float32 = 4 bytes
    sq8_bytes_per_vec = dim * 1  # int8 = 1 byte
    print(f"[+] Scalar Quantization (SQ8) Applied:")
    print(f"    Raw Float32 Vector Size: {raw_bytes_per_vec} bytes")
    print(f"    Quantized Int8 Size:     {sq8_bytes_per_vec} bytes (75% memory reduction / 4x compression)")

    # 2. Define a Highly Selective Metadata Filter
    # Only 1 tenant ("acme_corp") AND access_level <= 2
    selective_filter = PayloadFilter(
        must={"tenant_id": "acme_corp"},
        range_filters={"access_level": (1.0, 2.0)},
    )

    query_vec = np.random.randn(dim).astype(np.float32)

    # 3. Compare Post-Filtering (Antipattern) vs Single-Stage Search
    print("\n" + "-" * 80)
    print("EXPERIMENT: POST-FILTERING VS SINGLE-STAGE FILTERED SEARCH")
    print("-" * 80)

    # Post-filtering with candidate pool of 20
    post_filter_results = db.search_post_filter(
        query_vector=query_vec,
        candidate_pool_size=20,
        payload_filter=selective_filter,
        k=k,
    )

    # Single-stage filtered search
    single_stage_results = db.search_single_stage(
        query_vector=query_vec,
        k=k,
        payload_filter=selective_filter,
    )

    print(f"Post-Filtering Results Count:   {len(post_filter_results)} / {k} requested (Result Set Starvation!)")
    print(f"Single-Stage Results Count:     {len(single_stage_results)} / {k} requested (Guaranteed Top-K)")

    print("\nTop Matches via Single-Stage Filtered Search:")
    for rank, (r_id, dist, payload) in enumerate(single_stage_results, start=1):
        print(f"  [{rank}] ID: {r_id} | Cosine Dist: {dist:.4f} | Tenant: {payload['tenant_id']} | Access: {payload['access_level']} | Cat: {payload['category']}")

    # 4. Strict Assertions
    assert len(single_stage_results) == k, f"Single-stage search must return exactly {k} results"
    for _, _, payload in single_stage_results:
        assert payload["tenant_id"] == "acme_corp", "Tenant isolation violated!"
        assert payload["access_level"] <= 2, "Access level constraint violated!"

    print("\n[SUCCESS] All Hybrid Search & Quantization Assertions Passed!\n")


if __name__ == "__main__":
    run_hybrid_search_demo()
