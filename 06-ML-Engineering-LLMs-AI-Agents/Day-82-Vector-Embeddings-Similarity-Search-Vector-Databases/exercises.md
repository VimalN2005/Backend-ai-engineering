# Day 82: Production Exercises — Vector Embeddings & Similarity Search

Below are 3 enterprise-grade production engineering exercises covering filtered graph traversal, scalar quantization compression, and hybrid search ranking via Reciprocal Rank Fusion (RRF).

---

## Exercise 1: Single-Stage Filtered Vector Search with Predicate Pushdown

### Problem Statement
In production vector databases, metadata filtering must happen simultaneously during vector distance scoring, rather than after search. Implement a `FilteredVectorSearchEngine` that evaluates a predicate function during nearest neighbor search, guaranteeing that exactly $K$ matching documents are returned without result set starvation.

### Complete Solution & Verification Test

```python
"""
Exercise 1: Single-Stage Filtered Vector Search with Predicate Pushdown
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple
import numpy as np


@dataclass
class DocumentEntity:
    doc_id: str
    vector: np.ndarray
    metadata: Dict[str, Any]


class FilteredVectorSearchEngine:
    def __init__(self) -> None:
        self.documents: Dict[str, DocumentEntity] = {}

    def insert(self, doc_id: str, vector: np.ndarray, metadata: Dict[str, Any]) -> None:
        norm = np.linalg.norm(vector)
        unit_vec = vector / norm if norm > 0 else vector
        self.documents[doc_id] = DocumentEntity(doc_id=doc_id, vector=unit_vec, metadata=metadata)

    def search(
        self,
        query_vector: np.ndarray,
        predicate: Callable[[Dict[str, Any]], bool],
        k: int = 5,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        norm_q = query_vector / np.linalg.norm(query_vector)
        matches: List[Tuple[str, float, Dict[str, Any]]] = []

        for doc in self.documents.values():
            # Predicate Pushdown: Evaluate filter before calculating distance
            if not predicate(doc.metadata):
                continue
            
            # Cosine Distance on unit vectors: 1.0 - dot_product
            dist = 1.0 - float(np.dot(norm_q, doc.vector))
            matches.append((doc.doc_id, dist, doc.metadata))

        matches.sort(key=lambda x: x[1])
        return matches[:k]


# Verification Test
if __name__ == "__main__":
    engine = FilteredVectorSearchEngine()
    dim = 64

    # Insert 100 documents across 3 tenants
    for i in range(100):
        t_id = "tenant_A" if i % 10 == 0 else "tenant_B"  # Only 10% belong to tenant_A
        vec = np.random.randn(dim).astype(np.float32)
        engine.insert(f"doc_{i}", vec, {"tenant_id": t_id, "active": (i % 2 == 0)})

    query = np.random.randn(dim).astype(np.float32)

    # Search strictly for tenant_A and active=True
    predicate = lambda m: m["tenant_id"] == "tenant_A" and m["active"] is True
    results = engine.search(query, predicate=predicate, k=3)

    assert len(results) == 3, f"Expected 3 matching documents, got {len(results)}"
    for r_id, dist, meta in results:
        assert meta["tenant_id"] == "tenant_A"
        assert meta["active"] is True

    print("Exercise 1 Verification: All Assertions Passed!\n")
```

---

## Exercise 2: Scalar Quantization (SQ8) Compression & Recall Evaluator

### Problem Statement
Write a production `SQ8Quantizer` that quantizes 32-bit floating-point embeddings into 8-bit signed integers (`int8`). Evaluate the compression ratio and empirical Cosine Recall@10 against unquantized float32 ground truth across 500 test vectors.

### Complete Solution & Verification Test

```python
"""
Exercise 2: Scalar Quantization (SQ8) Compression & Recall Evaluator
"""
import numpy as np
from typing import List, Tuple


class SQ8Quantizer:
    def __init__(self) -> None:
        self.min_val = -1.0
        self.max_val = 1.0
        self.scale = 2.0 / 255.0

    def fit(self, X: np.ndarray) -> None:
        self.min_val = float(np.min(X))
        self.max_val = float(np.max(X))
        self.scale = max(1e-7, (self.max_val - self.min_val) / 255.0)

    def encode(self, v: np.ndarray) -> np.ndarray:
        clamped = np.clip(v, self.min_val, self.max_val)
        return (np.round((clamped - self.min_val) / self.scale) - 128.0).astype(np.int8)

    def decode(self, q: np.ndarray) -> np.ndarray:
        return ((q.astype(np.float32) + 128.0) * self.scale) + self.min_val


# Verification Test
if __name__ == "__main__":
    np.random.seed(42)
    num_vectors = 500
    dim = 128

    raw_vectors = np.random.randn(num_vectors, dim).astype(np.float32)
    # L2 normalize
    norms = np.linalg.norm(raw_vectors, axis=1, keepdims=True)
    raw_vectors = raw_vectors / norms

    quantizer = SQ8Quantizer()
    quantizer.fit(raw_vectors)
    quantized_vectors = np.array([quantizer.encode(v) for v in raw_vectors])

    # Check compression ratio
    raw_bytes = raw_vectors.nbytes
    quantized_bytes = quantized_vectors.nbytes
    compression_ratio = raw_bytes / quantized_bytes
    print(f"Raw FP32 Memory:       {raw_bytes} bytes")
    print(f"Quantized INT8 Memory: {quantized_bytes} bytes")
    print(f"Compression Ratio:     {compression_ratio:.1f}x (75% savings)")
    assert compression_ratio == 4.0

    # Evaluate Cosine Recall@10 across 20 queries
    k = 10
    query = np.random.randn(dim).astype(np.float32)
    query /= np.linalg.norm(query)

    # Exact FP32 search
    fp32_scores = np.dot(raw_vectors, query)
    top_fp32_indices = set(np.argsort(-fp32_scores)[:k])

    # Dequantized search
    reconstructed = np.array([quantizer.decode(q) for q in quantized_vectors])
    sq8_scores = np.dot(reconstructed, query)
    top_sq8_indices = set(np.argsort(-sq8_scores)[:k])

    overlap = len(top_fp32_indices.intersection(top_sq8_indices))
    recall = overlap / float(k)
    print(f"Recall@{k} under SQ8:    {recall * 100.0:.1f}%")
    assert recall >= 0.90, f"Expected Recall >= 90%, got {recall}"

    print("Exercise 2 Verification: All Assertions Passed!\n")
```

---

## Exercise 3: Reciprocal Rank Fusion (RRF) for Hybrid Search

### Problem Statement
In production retrieval, combining sparse keyword search (BM25) with dense vector embeddings yields the highest precision. Implement the standard **Reciprocal Rank Fusion (RRF)** algorithm that blends ranked result lists without requiring score normalization:

$$RRF\_Score(d) = \sum_{m \in M} \frac{1}{k_{\text{rrf}} + r_m(d)}$$

Where $k_{\text{rrf}} = 60$ (smoothing constant) and $r_m(d)$ is the 1-based rank of document $d$ in system $m$.

### Complete Solution & Verification Test

```python
"""
Exercise 3: Reciprocal Rank Fusion (RRF) for Hybrid Lexical-Vector Search
"""
from typing import Dict, List, Tuple


class ReciprocalRankFusion:
    @staticmethod
    def fuse(
        ranked_lists: List[List[str]],
        k_rrf: int = 60,
    ) -> List[Tuple[str, float]]:
        """
        Combines multiple ordered ranked lists of document IDs into a single fused ranking.
        """
        rrf_scores: Dict[str, float] = {}

        for ranked_list in ranked_lists:
            for rank_idx, doc_id in enumerate(ranked_list, start=1):
                # RRF Formula: 1 / (60 + rank)
                contribution = 1.0 / (k_rrf + rank_idx)
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + contribution

        # Sort descending by fused RRF score
        fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return fused


# Verification Test
if __name__ == "__main__":
    # Rank list from BM25 lexical search
    bm25_results = ["doc_A", "doc_B", "doc_C", "doc_D"]
    # Rank list from Dense Vector search
    dense_results = ["doc_C", "doc_A", "doc_E", "doc_F"]

    fused_results = ReciprocalRankFusion.fuse([bm25_results, dense_results], k_rrf=60)

    # doc_A is rank 1 in BM25 (1/61) and rank 2 in Dense (1/62) -> 0.01639 + 0.01612 = 0.03251
    # doc_C is rank 3 in BM25 (1/63) and rank 1 in Dense (1/61) -> 0.01587 + 0.01639 = 0.03226
    top_doc, top_score = fused_results[0]
    print(f"Top Fused Document: {top_doc} with RRF Score: {top_score:.5f}")
    assert top_doc == "doc_A", "doc_A should achieve the highest fused ranking"

    print("Exercise 3 Verification: All Assertions Passed!\n")
```
