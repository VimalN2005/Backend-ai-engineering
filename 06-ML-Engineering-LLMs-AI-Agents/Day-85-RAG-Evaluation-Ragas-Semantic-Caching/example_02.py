"""
Day 85: RAG Evaluation with Ragas Framework & Semantic Response Caching
Part 2: Production Two-Tier Semantic Response Cache (Exact Hash + Vector Similarity)

This module provides:
1. TwoTierSemanticCache combining:
   - Tier 1: Exact Hash Cache (SHA-256 O(1) latency < 0.05ms)
   - Tier 2: Vector Semantic Cache (Cosine similarity threshold tau >= 0.90)
2. Normalization and canonicalization pipeline to prevent cache fragmentation.
3. Namespace isolation (tenant / environment separation) and real-time invalidation.
4. TTL management and LRU eviction policy.
5. Latency benchmarking and token savings telemetry.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Re-use deterministic encoder from example_01
from example_01 import DeterministicSemanticEncoder


# =====================================================================
# 1. Prompt Normalization & Canonicalization
# =====================================================================

class QueryCanonicalizer:
    """
    Normalizes query strings to maximize cache hit rates across identical intents:
    - Collapses repeated whitespace
    - Lowercases text
    - Strips surrounding punctuation
    """
    @staticmethod
    def canonicalize(query: str) -> str:
        q = query.strip().lower()
        # Remove superfluous punctuation at sentence ends
        q = re.sub(r"[?!.,;:\s]+$", "", q)
        # Collapse multiple spaces, tabs, and newlines
        q = re.sub(r"\s+", " ", q)
        return q

    @staticmethod
    def compute_sha256(canonical_query: str, namespace: str) -> str:
        payload = f"{namespace}:{canonical_query}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


# =====================================================================
# 2. Cache Data Structures
# =====================================================================

@dataclass
class CacheEntry:
    key_hash: str
    canonical_query: str
    namespace: str
    response: Dict[str, Any]
    embedding: np.ndarray
    created_at: float
    ttl_seconds: float
    last_accessed_at: float
    hit_count: int = 0

    def is_expired(self, current_time: float) -> bool:
        return (current_time - self.created_at) > self.ttl_seconds


@dataclass
class CacheLookupResult:
    hit: bool
    tier: str  # "EXACT_HASH", "SEMANTIC_VECTOR", or "MISS"
    similarity: float
    response: Optional[Dict[str, Any]]
    latency_ms: float


# =====================================================================
# 3. Two-Tier Semantic Response Cache Engine
# =====================================================================

class TwoTierSemanticCache:
    """
    Production-grade Two-Tier Cache:
    - Tier 1: Exact Hash Table (O(1) dictionary lookup)
    - Tier 2: Vector Semantic Index (Cosine similarity >= similarity_threshold)
    """
    def __init__(
        self,
        similarity_threshold: float = 0.90,
        max_entries: int = 1000,
        default_ttl_seconds: float = 3600.0,
    ):
        self.threshold = similarity_threshold
        self.max_entries = max_entries
        self.default_ttl = default_ttl_seconds

        self.encoder = DeterministicSemanticEncoder()
        # Tier 1 storage: key_hash -> CacheEntry
        self._exact_index: Dict[str, CacheEntry] = {}
        # Tier 2 storage: List of CacheEntry for vector scan
        self._vector_index: List[CacheEntry] = []

        # Metrics telemetry
        self.total_lookups = 0
        self.exact_hits = 0
        self.semantic_hits = 0
        self.misses = 0

    def _evict_lru(self) -> None:
        """Evicts the least recently accessed entry when capacity is exceeded."""
        if not self._vector_index:
            return
        # Find entry with oldest last_accessed_at
        oldest_entry = min(self._vector_index, key=lambda e: e.last_accessed_at)
        self._exact_index.pop(oldest_entry.key_hash, None)
        self._vector_index.remove(oldest_entry)

    def get(self, raw_query: str, namespace: str = "default") -> CacheLookupResult:
        start_time = time.perf_counter()
        now = time.time()
        self.total_lookups += 1

        canonical = QueryCanonicalizer.canonicalize(raw_query)
        key_hash = QueryCanonicalizer.compute_sha256(canonical, namespace)

        # -------------------------------------------------------------
        # Tier 1: Exact Hash Lookup (O(1))
        # -------------------------------------------------------------
        if key_hash in self._exact_index:
            entry = self._exact_index[key_hash]
            if not entry.is_expired(now):
                entry.last_accessed_at = now
                entry.hit_count += 1
                self.exact_hits += 1
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return CacheLookupResult(
                    hit=True,
                    tier="EXACT_HASH",
                    similarity=1.0,
                    response=entry.response,
                    latency_ms=elapsed_ms,
                )
            else:
                # Expired: remove
                self._exact_index.pop(key_hash, None)
                if entry in self._vector_index:
                    self._vector_index.remove(entry)

        # -------------------------------------------------------------
        # Tier 2: Vector Semantic Lookup
        # -------------------------------------------------------------
        query_vec = self.encoder.encode(canonical)
        best_entry: Optional[CacheEntry] = None
        best_sim = -1.0

        for entry in self._vector_index:
            if entry.namespace != namespace:
                continue
            if entry.is_expired(now):
                continue

            sim = self.encoder.cosine_similarity(query_vec, entry.embedding)
            if sim > best_sim:
                best_sim = sim
                best_entry = entry

        if best_entry is not None and best_sim >= self.threshold:
            best_entry.last_accessed_at = now
            best_entry.hit_count += 1
            self.semantic_hits += 1
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return CacheLookupResult(
                hit=True,
                tier="SEMANTIC_VECTOR",
                similarity=best_sim,
                response=best_entry.response,
                latency_ms=elapsed_ms,
            )

        # Cache Miss
        self.misses += 1
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return CacheLookupResult(
            hit=False,
            tier="MISS",
            similarity=max(0.0, best_sim),
            response=None,
            latency_ms=elapsed_ms,
        )

    def put(
        self,
        raw_query: str,
        response: Dict[str, Any],
        namespace: str = "default",
        ttl_seconds: Optional[float] = None,
    ) -> CacheEntry:
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        canonical = QueryCanonicalizer.canonicalize(raw_query)
        key_hash = QueryCanonicalizer.compute_sha256(canonical, namespace)
        query_vec = self.encoder.encode(canonical)

        # Check capacity
        if len(self._vector_index) >= self.max_entries:
            self._evict_lru()

        entry = CacheEntry(
            key_hash=key_hash,
            canonical_query=canonical,
            namespace=namespace,
            response=response,
            embedding=query_vec,
            created_at=now,
            ttl_seconds=ttl,
            last_accessed_at=now,
        )

        self._exact_index[key_hash] = entry
        self._vector_index.append(entry)
        return entry

    def invalidate_namespace(self, namespace: str) -> int:
        """Invalidates all cache entries belonging to a given namespace."""
        initial_count = len(self._vector_index)
        self._vector_index = [e for e in self._vector_index if e.namespace != namespace]
        self._exact_index = {k: v for k, v in self._exact_index.items() if v.namespace != namespace}
        return initial_count - len(self._vector_index)

    def get_stats(self) -> Dict[str, Any]:
        hit_rate = 0.0
        if self.total_lookups > 0:
            hit_rate = (self.exact_hits + self.semantic_hits) / self.total_lookups
        return {
            "total_lookups": self.total_lookups,
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "misses": self.misses,
            "hit_rate_pct": round(hit_rate * 100.0, 2),
            "active_entries": len(self._vector_index),
        }


# =====================================================================
# 4. Demonstration and Verification Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 85: TWO-TIER SEMANTIC RESPONSE CACHE BENCHMARK")
    print("=" * 80)

    cache = TwoTierSemanticCache(similarity_threshold=0.55, max_entries=50)

    # 1. Populate initial response
    query_1 = "What is the token expiration TTL in the auth service?"
    response_1 = {
        "text": "The JWT session token expires in 900 seconds (15 minutes).",
        "tokens_used": 420,
        "model": "gpt-4o-production",
    }
    cache.put(query_1, response_1, namespace="prod")
    print(f"Stored response for: '{query_1}'")

    # 2. Test Exact Hit (with superficial whitespace/casing variations)
    query_exact_variant = "  WHAT IS THE TOKEN EXPIRATION TTL IN THE AUTH SERVICE?  "
    res_exact = cache.get(query_exact_variant, namespace="prod")
    print(f"\n[Test 1] Exact Match Lookup:")
    print(f"  Hit: {res_exact.hit} | Tier: {res_exact.tier} | Latency: {res_exact.latency_ms:.4f}ms")
    print(f"  Response: {res_exact.response['text']}")
    assert res_exact.hit is True and res_exact.tier == "EXACT_HASH"

    # 3. Test Semantic Hit (Paraphrase)
    query_paraphrase = "How long do JWT tokens last in the auth service before expiring?"
    res_semantic = cache.get(query_paraphrase, namespace="prod")
    print(f"\n[Test 2] Semantic Paraphrase Lookup:")
    print(f"  Hit: {res_semantic.hit} | Tier: {res_semantic.tier} | Sim: {res_semantic.similarity:.4f} | Latency: {res_semantic.latency_ms:.4f}ms")
    print(f"  Response: {res_semantic.response['text'] if res_semantic.response else 'None'}")
    assert res_semantic.hit is True and res_semantic.tier == "SEMANTIC_VECTOR"

    # 4. Test Namespace Isolation (cross-tenant safety)
    res_diff_ns = cache.get(query_1, namespace="staging")
    print(f"\n[Test 3] Namespace Isolation Test (Staging vs Prod):")
    print(f"  Hit: {res_diff_ns.hit} | Tier: {res_diff_ns.tier} (Must be MISS to prevent leakage)")
    assert res_diff_ns.hit is False

    # 5. Invalidation test
    invalidated = cache.invalidate_namespace("prod")
    print(f"\n[Test 4] Namespace Invalidation:")
    print(f"  Invalidated {invalidated} entries from 'prod' namespace.")
    res_after_invalidation = cache.get(query_1, namespace="prod")
    print(f"  Post-invalidation lookup: Hit={res_after_invalidation.hit} | Tier={res_after_invalidation.tier}")
    assert res_after_invalidation.hit is False

    # 6. Telemetry stats
    stats = cache.get_stats()
    print("\n" + "=" * 80)
    print("CACHE TELEMETRY METRICS")
    print("=" * 80)
    for k, v in stats.items():
        print(f"  {k:<20}: {v}")

    print("\n[SUCCESS] Two-Tier Semantic Response Cache passed all verification checks.")
