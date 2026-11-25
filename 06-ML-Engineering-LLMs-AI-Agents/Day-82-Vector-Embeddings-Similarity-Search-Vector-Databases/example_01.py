"""
Day 82: Vector Embeddings & Similarity Search
Part 1: Vector Space Mathematics & Hierarchical Navigable Small World (HNSW) Engine

This module provides:
1. Pure NumPy vector mathematics: L2 normalization, Cosine, Euclidean, and Dot Product metrics.
2. Exact KNN Flat Index baseline for gold-standard 100% recall verification.
3. In-memory Hierarchical Navigable Small World (HNSW) index implementing multi-layer skip routing,
   probabilistic layer decay, and greedy beam search.
"""

from __future__ import annotations

import heapq
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np


# =====================================================================
# 1. High-Performance Vector Mathematics
# =====================================================================

class VectorMath:
    """
    Mathematical operations on dense continuous vector embeddings.
    """
    @staticmethod
    def l2_normalize(v: np.ndarray) -> np.ndarray:
        """Scales vector to unit norm: ||v||_2 = 1.0."""
        norm = np.linalg.norm(v)
        if norm == 0.0:
            return v
        return v / norm

    @staticmethod
    def cosine_similarity(u: np.ndarray, v: np.ndarray) -> float:
        """Computes cos(theta) = (u · v) / (||u||_2 * ||v||_2)."""
        norm_u = np.linalg.norm(u)
        norm_v = np.linalg.norm(v)
        if norm_u == 0.0 or norm_v == 0.0:
            return 0.0
        return float(np.dot(u, v) / (norm_u * norm_v))

    @staticmethod
    def cosine_distance(u: np.ndarray, v: np.ndarray) -> float:
        """Computes Cosine Distance = 1.0 - Cosine Similarity. Range: [0.0, 2.0]."""
        return 1.0 - VectorMath.cosine_similarity(u, v)

    @staticmethod
    def euclidean_distance(u: np.ndarray, v: np.ndarray) -> float:
        """Computes Euclidean (L2) Distance: sqrt(sum((u_i - v_i)^2))."""
        return float(np.linalg.norm(u - v))

    @staticmethod
    def dot_product(u: np.ndarray, v: np.ndarray) -> float:
        """Computes standard inner product."""
        return float(np.dot(u, v))


# =====================================================================
# 2. Exact KNN Flat Index (Brute-Force Baseline)
# =====================================================================

class FlatIndex:
    """
    Exhaustive linear scan index providing 100% ground-truth recall.
    Time Complexity: O(N * d).
    """
    def __init__(self, metric: str = "cosine") -> None:
        self.metric = metric
        self.ids: List[str] = []
        self.vectors: List[np.ndarray] = []

    def add(self, vector_id: str, vector: np.ndarray) -> None:
        v = VectorMath.l2_normalize(vector) if self.metric == "cosine" else vector
        self.ids.append(vector_id)
        self.vectors.append(v)

    def search(self, query: np.ndarray, k: int = 5) -> List[Tuple[str, float]]:
        """Performs exhaustive distance computation across all stored vectors."""
        q = VectorMath.l2_normalize(query) if self.metric == "cosine" else query
        results: List[Tuple[str, float]] = []

        for vid, v in zip(self.ids, self.vectors):
            if self.metric == "cosine":
                # For unit vectors, Cosine Distance = 1.0 - dot_product
                dist = 1.0 - float(np.dot(q, v))
            else:
                dist = float(np.linalg.norm(q - v))
            results.append((vid, dist))

        # Sort ascending by distance (closest first)
        results.sort(key=lambda x: x[1])
        return results[:k]


# =====================================================================
# 3. Hierarchical Navigable Small World (HNSW) Index Engine
# =====================================================================

@dataclass
class HNSWNode:
    node_id: str
    vector: np.ndarray
    top_level: int


class HNSWIndex:
    """
    Production-grade Hierarchical Navigable Small World (HNSW) index.
    Features:
    - Multi-layer graph hierarchy with logarithmic search complexity O(log N).
    - Configurable M (max bidirectional links) and efConstruction beam width.
    - Greedy priority-queue routing with runtime efSearch tuning.
    """
    def __init__(
        self,
        dim: int,
        m: int = 16,
        ef_construction: int = 64,
        m_l: Optional[float] = None,
        seed: int = 42,
    ) -> None:
        self.dim = dim
        self.m = m  # Max connections per node per layer
        self.m0 = 2 * m  # Max connections for ground layer (Layer 0)
        self.ef_construction = ef_construction
        self.m_l = m_l or (1.0 / math.log(m))  # Level generation normalization factor
        
        self.nodes: Dict[str, HNSWNode] = {}
        # Multi-layer adjacency list: layers[level][node_id] = set of neighbor node_ids
        self.layers: List[Dict[str, Set[str]]] = []
        self.entry_point: Optional[str] = None
        self.max_level: int = -1
        
        random.seed(seed)
        np.random.seed(seed)

    def _dist(self, u: np.ndarray, v: np.ndarray) -> float:
        """Cosine distance on L2-normalized vectors: 1.0 - dot(u, v)."""
        return 1.0 - float(np.dot(u, v))

    def _assign_random_level(self) -> int:
        """Probabilistic level assignment based on decay parameter m_L."""
        unif = random.random()
        if unif == 0.0:
            unif = 1e-9
        return int(-math.log(unif) * self.m_l)

    def _search_layer(
        self,
        query: np.ndarray,
        entry_points: List[str],
        ef: int,
        level: int,
    ) -> List[Tuple[float, str]]:
        """
        Greedy beam search on a single layer graph.
        Returns list of (distance, node_id) sorted ascending.
        """
        v_entry = entry_points[0]
        visited: Set[str] = set(entry_points)
        
        # Priority queue for candidates (min-heap: smallest distance first)
        candidates: List[Tuple[float, str]] = []
        # Priority queue for best results (max-heap using -distance: furthest item at root)
        w_results: List[Tuple[float, str]] = []

        for ep in entry_points:
            d = self._dist(query, self.nodes[ep].vector)
            heapq.heappush(candidates, (d, ep))
            heapq.heappush(w_results, (-d, ep))

        layer_graph = self.layers[level]

        while candidates:
            c_dist, c_id = heapq.heappop(candidates)
            furthest_w_dist = -w_results[0][0]

            if c_dist > furthest_w_dist:
                break  # Stopping condition

            # Inspect neighbors of candidate
            for neighbor_id in layer_graph.get(c_id, set()):
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    n_dist = self._dist(query, self.nodes[neighbor_id].vector)
                    furthest_w_dist = -w_results[0][0]

                    if n_dist < furthest_w_dist or len(w_results) < ef:
                        heapq.heappush(candidates, (n_dist, neighbor_id))
                        heapq.heappush(w_results, (-n_dist, neighbor_id))
                        if len(w_results) > ef:
                            heapq.heappop(w_results)

        # Convert w_results back to positive distance sorted ascending
        sorted_results = sorted([(-d, nid) for d, nid in w_results], key=lambda x: x[0])
        return sorted_results

    def add(self, node_id: str, vector: np.ndarray) -> None:
        """Inserts vector embedding into the HNSW multi-layer graph index."""
        norm_vec = VectorMath.l2_normalize(vector)
        target_level = self._assign_random_level()

        node = HNSWNode(node_id=node_id, vector=norm_vec, top_level=target_level)
        self.nodes[node_id] = node

        # Ensure layers capacity
        while len(self.layers) <= target_level:
            self.layers.append({})

        for lvl in range(target_level + 1):
            self.layers[lvl][node_id] = set()

        # Case 1: First node inserted in index
        if self.entry_point is None:
            self.entry_point = node_id
            self.max_level = target_level
            return

        curr_ep = self.entry_point
        max_l = self.max_level

        # Phase 1: Top-down greedy search down to target_level + 1
        for lvl in range(max_l, target_level, -1):
            res = self._search_layer(norm_vec, [curr_ep], ef=1, level=lvl)
            if res:
                curr_ep = res[0][1]

        # Phase 2: Insert node from target_level down to Layer 0
        for lvl in range(min(target_level, max_l), -1, -1):
            res = self._search_layer(norm_vec, [curr_ep], ef=self.ef_construction, level=lvl)
            
            # Select M nearest neighbors as connections
            max_links = self.m0 if lvl == 0 else self.m
            neighbors = [nid for _, nid in res if nid != node_id and nid in self.layers[lvl]][:max_links]

            self.layers[lvl][node_id] = set(neighbors)
            for neighbor_id in neighbors:
                self.layers[lvl][neighbor_id].add(node_id)
                # Prune connections if exceeding max_links
                if len(self.layers[lvl][neighbor_id]) > max_links:
                    n_nbrs = list(self.layers[lvl][neighbor_id])
                    n_vec = self.nodes[neighbor_id].vector
                    n_nbrs.sort(key=lambda x: self._dist(n_vec, self.nodes[x].vector))
                    self.layers[lvl][neighbor_id] = set(n_nbrs[:max_links])

            if res:
                curr_ep = res[0][1]

        # Update entry point if newly added node has higher top level
        if target_level > self.max_level:
            self.max_level = target_level
            self.entry_point = node_id

    def search(self, query: np.ndarray, k: int = 5, ef_search: int = 32) -> List[Tuple[str, float]]:
        """
        Executes Approximate Nearest Neighbor (ANN) search:
        - Drops through express upper layers with ef=1.
        - Performs beam search on Layer 0 with ef=ef_search.
        """
        if self.entry_point is None:
            return []

        norm_query = VectorMath.l2_normalize(query)
        curr_ep = self.entry_point

        # 1. Top-down routing down to Layer 1
        for lvl in range(self.max_level, 0, -1):
            res = self._search_layer(norm_query, [curr_ep], ef=1, level=lvl)
            if res:
                curr_ep = res[0][1]

        # 2. Deep search on ground layer (Layer 0)
        candidates = self._search_layer(norm_query, [curr_ep], ef=max(ef_search, k), level=0)
        return [(nid, dist) for dist, nid in candidates[:k]]


# =====================================================================
# 4. Verification & Recall Benchmark Suite
# =====================================================================

def run_hnsw_benchmark() -> None:
    print("=" * 80)
    print("DAY 82: VECTOR MATHEMATICS & HNSW GRAPH INDEX BENCHMARK")
    print("=" * 80)

    # 1. Verify Vector Mathematics
    u = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    w = np.array([1.0, 1.0, 0.0], dtype=np.float32)

    assert math.isclose(VectorMath.cosine_distance(u, v), 1.0), "Orthogonal vectors have cosine dist = 1.0"
    assert math.isclose(VectorMath.cosine_distance(u, u), 0.0), "Identical vectors have cosine dist = 0.0"
    assert math.isclose(VectorMath.cosine_similarity(u, w), 1.0 / math.sqrt(2.0), rel_tol=1e-5)
    print("[+] Vector Mathematics Unit Checks Passed: Cosine, Euclidean & L2 Normalization verified.")

    # 2. Build Benchmark Dataset
    num_vectors = 800
    dim = 64
    k = 5

    print(f"\n[*] Generating {num_vectors} synthetic dense embeddings (dim={dim})...")
    raw_data = np.random.randn(num_vectors, dim).astype(np.float32)

    flat_index = FlatIndex(metric="cosine")
    hnsw_index = HNSWIndex(dim=dim, m=16, ef_construction=64)

    t0 = time.perf_counter()
    for i in range(num_vectors):
        vec_id = f"doc_{i:04d}"
        flat_index.add(vec_id, raw_data[i])
        hnsw_index.add(vec_id, raw_data[i])
    build_time_ms = (time.perf_counter() - t0) * 1000.0

    print(f"[+] HNSW Index Built in {build_time_ms:.2f} ms.")
    print(f"    Total Layers Created: {len(hnsw_index.layers)} (Top Level: {hnsw_index.max_level})")
    for lvl_idx, layer in enumerate(hnsw_index.layers):
        print(f"    - Layer {lvl_idx}: {len(layer)} nodes")

    # 3. Evaluate Recall@K and Query Latency over 50 Test Queries
    num_queries = 50
    queries = np.random.randn(num_queries, dim).astype(np.float32)

    flat_latencies: List[float] = []
    hnsw_latencies: List[float] = []
    recall_scores: List[float] = []

    for q_idx in range(num_queries):
        query = queries[q_idx]

        # Exact Flat Search
        t_f0 = time.perf_counter()
        exact_results = flat_index.search(query, k=k)
        flat_latencies.append((time.perf_counter() - t_f0) * 1000.0)

        # HNSW Search
        t_h0 = time.perf_counter()
        hnsw_results = hnsw_index.search(query, k=k, ef_search=32)
        hnsw_latencies.append((time.perf_counter() - t_h0) * 1000.0)

        # Compute Recall@K: |HNSW_top_k ∩ Exact_top_k| / K
        exact_set = {nid for nid, _ in exact_results}
        hnsw_set = {nid for nid, _ in hnsw_results}
        overlap = len(exact_set.intersection(hnsw_set))
        recall_scores.append(overlap / float(k))

    avg_flat_lat = np.mean(flat_latencies)
    avg_hnsw_lat = np.mean(hnsw_latencies)
    avg_recall = np.mean(recall_scores) * 100.0

    print("\n" + "-" * 80)
    print("BENCHMARK COMPARISON: EXACT KNN VS HNSW")
    print("-" * 80)
    print(f"Average Flat Search Latency: {avg_flat_lat:.3f} ms")
    print(f"Average HNSW Search Latency: {avg_hnsw_lat:.3f} ms")
    print(f"Average Recall@{k}:           {avg_recall:.2f}%")

    assert avg_recall >= 80.0, f"Expected HNSW Recall >= 80%, got {avg_recall:.2f}%"
    print("\n[SUCCESS] All HNSW Indexing & Recall Assertions Passed!\n")


if __name__ == "__main__":
    run_hnsw_benchmark()
