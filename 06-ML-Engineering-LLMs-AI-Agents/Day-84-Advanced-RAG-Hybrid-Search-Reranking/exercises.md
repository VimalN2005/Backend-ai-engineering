# Day 84: Production Exercises — Advanced RAG & Reranking

Below are 3 enterprise-grade production engineering exercises covering BM25 indexing, Reciprocal Rank Fusion, and Two-Stage Cross-Encoder precision evaluation.

---

## Exercise 1: Production Okapi BM25 Scorer with Term Frequency Saturation

### Problem Statement
Implement the exact Okapi BM25 document scoring function that calculates the score for a single document given a multi-token query, document term frequencies, corpus inverted document frequencies (IDF), document length, and average corpus length.

### Complete Solution & Verification Test

```python
"""
Exercise 1: Okapi BM25 Scorer with Length Normalization
"""
import math
from typing import Dict, List


def calculate_bm25_score(
    query_tokens: List[str],
    doc_tf: Dict[str, int],
    idf_map: Dict[str, float],
    doc_len: int,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    score = 0.0
    # Length normalization penalty
    b_norm = 1.0 - b + b * (doc_len / avg_doc_len if avg_doc_len > 0 else 1.0)

    for token in query_tokens:
        if token not in doc_tf:
            continue
        f = doc_tf[token]
        idf = idf_map.get(token, 0.0)

        # Okapi term frequency saturation
        tf_component = (f * (k1 + 1.0)) / (f + k1 * b_norm)
        score += idf * tf_component

    return score


# Verification Test
if __name__ == "__main__":
    query = ["database", "timeout"]
    
    # Document A: Short doc with both terms
    doc_a_tf = {"database": 2, "timeout": 1, "error": 3}
    doc_a_len = 6

    # Document B: Very long document with single mention
    doc_b_tf = {"database": 1, "other": 50}
    doc_b_len = 51

    avg_len = 20.0
    idf = {"database": 1.2, "timeout": 2.5}

    score_a = calculate_bm25_score(query, doc_a_tf, idf, doc_a_len, avg_len)
    score_b = calculate_bm25_score(query, doc_b_tf, idf, doc_b_len, avg_len)

    print(f"Doc A Score (Short & Relevant): {score_a:.4f}")
    print(f"Doc B Score (Long & Diluted):  {score_b:.4f}")

    assert score_a > score_b, "Doc A must achieve higher BM25 score than diluted Doc B"
    print("Exercise 1 Verification: All Assertions Passed!\n")
```

---

## Exercise 2: Multi-Stream Reciprocal Rank Fusion (RRF) with Channel Weights

### Problem Statement
Standard RRF gives equal weight to all retrieval channels. Build a `WeightedRRF` engine that allows assigning different channel multipliers ($w_{\text{dense}} = 0.7$, $w_{\text{bm25}} = 0.3$) while retaining rank-based scaling:

$$\text{Weighted\_RRF}(d) = \sum_{m \in M} w_m \cdot \frac{1}{k_{\text{rrf}} + r_m(d)}$$

### Complete Solution & Verification Test

```python
"""
Exercise 2: Multi-Stream Weighted Reciprocal Rank Fusion
"""
from typing import Dict, List, Tuple


class WeightedRRF:
    @staticmethod
    def fuse(
        channel_results: List[Tuple[float, List[str]]],
        k_rrf: int = 60,
    ) -> List[Tuple[str, float]]:
        """
        channel_results: List of (channel_weight, [ranked_doc_ids])
        """
        fused_scores: Dict[str, float] = {}

        for weight, doc_list in channel_results:
            for rank_idx, doc_id in enumerate(doc_list, start=1):
                contribution = weight * (1.0 / (k_rrf + rank_idx))
                fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + contribution

        sorted_results = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results


# Verification Test
if __name__ == "__main__":
    # Channel 1: Dense Vector (Weight 0.70)
    dense_channel = (0.70, ["doc_X", "doc_Y", "doc_Z"])
    # Channel 2: BM25 Lexical (Weight 0.30)
    sparse_channel = (0.30, ["doc_Y", "doc_W", "doc_X"])

    fused = WeightedRRF.fuse([dense_channel, sparse_channel], k_rrf=60)
    print("Fused Results with Channel Weights:")
    for doc_id, score in fused:
        print(f"  {doc_id}: {score:.5f}")

    # doc_Y is rank 2 in dense (0.7 / 62 = 0.01129) and rank 1 in sparse (0.3 / 61 = 0.00491) -> 0.01621
    # doc_X is rank 1 in dense (0.7 / 61 = 0.01147) and rank 3 in sparse (0.3 / 63 = 0.00476) -> 0.01623
    assert fused[0][0] == "doc_X", "doc_X should lead due to dense weight dominance"
    print("Exercise 2 Verification: All Assertions Passed!\n")
```

---

## Exercise 3: Mean Reciprocal Rank (MRR@K) Precision Evaluator

### Problem Statement
Write an evaluation harness that calculates the Mean Reciprocal Rank at $K$ ($\text{MRR}@K$) across a benchmark evaluation set:

$$\text{MRR}@K = \frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{\text{rank}_i}$$

Where $\text{rank}_i$ is the rank of the first relevant document in the top-$K$ results (or 0 if not found).

### Complete Solution & Verification Test

```python
"""
Exercise 3: Mean Reciprocal Rank (MRR@K) Evaluator
"""
from typing import Dict, List, Set


def calculate_mrr(
    evaluation_runs: List[Tuple[List[str], Set[str]]],
    k: int = 5,
) -> float:
    """
    evaluation_runs: List of (retrieved_doc_ids, ground_truth_relevant_ids)
    """
    reciprocal_ranks: List[float] = []

    for retrieved, ground_truth in evaluation_runs:
        found_rank = 0
        for rank_idx, doc_id in enumerate(retrieved[:k], start=1):
            if doc_id in ground_truth:
                found_rank = rank_idx
                break
        
        rr = (1.0 / found_rank) if found_rank > 0 else 0.0
        reciprocal_ranks.append(rr)

    return sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0


# Verification Test
if __name__ == "__main__":
    # Query 1: Relevant doc found at rank 1 -> RR = 1.0
    run1 = (["doc_A", "doc_B", "doc_C"], {"doc_A"})
    # Query 2: Relevant doc found at rank 2 -> RR = 0.5
    run2 = (["doc_X", "doc_Y", "doc_Z"], {"doc_Y"})
    # Query 3: Relevant doc not in top 3 -> RR = 0.0
    run3 = (["doc_1", "doc_2", "doc_3"], {"doc_9"})

    mrr = calculate_mrr([run1, run2, run3], k=3)
    print(f"Calculated MRR@3: {mrr:.4f}")

    # Expected: (1.0 + 0.5 + 0.0) / 3 = 0.5000
    assert abs(mrr - 0.5000) < 1e-4
    print("Exercise 3 Verification: All Assertions Passed!\n")
```
