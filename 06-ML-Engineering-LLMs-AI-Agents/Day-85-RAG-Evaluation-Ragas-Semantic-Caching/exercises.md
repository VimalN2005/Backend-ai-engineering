# Day 85: Exercises — RAG Evaluation & Semantic Caching

Three production-grade engineering exercises covering evaluation metrics, semantic caching, and cache eviction.

---

## Exercise 1: Multi-Claim Atomic Faithfulness Evaluator

### Objective
Implement an atomic claim decomposition evaluator that extracts individual factual assertions from a generated answer and verifies each claim against retrieved contexts, calculating the exact **Faithfulness Ratio** $\in [0.0, 1.0]$.

### Requirements
1. Extract individual sentences as atomic claims.
2. For each claim, calculate token overlap against the combined context tokens (case-insensitive).
3. If overlap $\ge 0.65$, mark claim as supported; otherwise unsupported.
4. Return `(faithfulness_score, supported_claims, unsupported_claims)`.

### Solution & Test Harness

```python
import re
from typing import List, Tuple

def evaluate_faithfulness(answer: str, contexts: List[str], threshold: float = 0.65) -> Tuple[float, List[str], List[str]]:
    raw_claims = [c.strip() for c in re.split(r"[.!?]+", answer) if len(c.strip()) > 8]
    if not raw_claims:
        return 1.0, [], []
    
    combined_ctx = " ".join(contexts).lower()
    ctx_tokens = set(re.findall(r"\w+", combined_ctx))
    
    supported, unsupported = [], []
    for claim in raw_claims:
        claim_tokens = set(re.findall(r"\w+", claim.lower()))
        if not claim_tokens:
            continue
        overlap = len(claim_tokens.intersection(ctx_tokens)) / len(claim_tokens)
        if overlap >= threshold:
            supported.append(claim)
        else:
            unsupported.append(claim)
            
    score = len(supported) / len(raw_claims)
    return score, supported, unsupported

# Verification
if __name__ == "__main__":
    ctx = ["PostgreSQL 16 handles multi-master replication through logical decoding slots."]
    ans_good = "PostgreSQL 16 handles replication through logical decoding slots."
    ans_bad = "PostgreSQL 16 stores all metadata in Redis cache instances."
    
    score_good, supp, unsupp = evaluate_faithfulness(ans_good, ctx)
    assert score_good == 1.0, f"Expected 1.0, got {score_good}"
    assert len(unsupp) == 0
    
    score_bad, supp_b, unsupp_b = evaluate_faithfulness(ans_bad, ctx)
    assert score_bad == 0.0, f"Expected 0.0, got {score_bad}"
    assert len(unsupp_b) == 1
    print("[Exercise 1] PASSED: Faithfulness evaluator verified.")
```

---

## Exercise 2: Semantic Cache with LRU Eviction

### Objective
Implement an LRU (Least Recently Used) semantic cache that maintains a strict entry capacity $C$. When capacity is exceeded, evict the least recently accessed item.

### Requirements
1. Store entries with `query`, `embedding` (vector), and `last_accessed` timestamp.
2. Support `put(query, vector, response)` and `get(query_vector, threshold=0.85)`.
3. When `put()` exceeds `max_capacity`, evict the entry with the oldest `last_accessed`.
4. Update `last_accessed` on every hit.

### Solution & Test Harness

```python
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import numpy as np

@dataclass
class LRUEntry:
    query: str
    vector: np.ndarray
    response: Any
    last_accessed: float

class LRUSemanticCache:
    def __init__(self, capacity: int = 2, threshold: float = 0.85):
        self.capacity = capacity
        self.threshold = threshold
        self.entries: List[LRUEntry] = []

    def get(self, query_vec: np.ndarray) -> Optional[Any]:
        best_entry = None
        best_sim = -1.0
        for entry in self.entries:
            sim = float(np.dot(query_vec, entry.vector) / (np.linalg.norm(query_vec) * np.linalg.norm(entry.vector)))
            if sim > best_sim:
                best_sim = sim
                best_entry = entry
                
        if best_entry and best_sim >= self.threshold:
            best_entry.last_accessed = time.time()
            return best_entry.response
        return None

    def put(self, query: str, vector: np.ndarray, response: Any):
        if len(self.entries) >= self.capacity:
            # Evict LRU
            self.entries.sort(key=lambda e: e.last_accessed)
            self.entries.pop(0)
            
        entry = LRUEntry(query=query, vector=vector, response=response, last_accessed=time.time())
        self.entries.append(entry)

# Verification
if __name__ == "__main__":
    cache = LRUSemanticCache(capacity=2, threshold=0.90)
    v1 = np.array([1.0, 0.0])
    v2 = np.array([0.0, 1.0])
    v3 = np.array([0.707, 0.707])
    
    cache.put("q1", v1, "resp1")
    time.sleep(0.01)
    cache.put("q2", v2, "resp2")
    
    # Access q1 so q2 becomes LRU
    hit = cache.get(v1)
    assert hit == "resp1"
    
    # Insert q3; q2 should be evicted
    time.sleep(0.01)
    cache.put("q3", v3, "resp3")
    
    assert len(cache.entries) == 2
    queries_in_cache = {e.query for e in cache.entries}
    assert "q2" not in queries_in_cache, "q2 should have been evicted by LRU policy"
    assert "q1" in queries_in_cache and "q3" in queries_in_cache
    print("[Exercise 2] PASSED: LRU eviction verified.")
```

---

## Exercise 3: Context Relevance & Mean Reciprocal Rank (MRR) Scorer

### Objective
Calculate Mean Reciprocal Rank (MRR) and Context Precision@K for a retrieved set of chunks evaluated against ground truth requirements.

### Requirements
1. Given a list of binary relevance judgments `[rel_1, rel_2, ..., rel_K]` where `1` = relevant, `0` = distractor.
2. Calculate MRR: reciprocal rank of the first relevant chunk ($1 / \text{first\_rank}$). If no relevant chunk, return $0.0$.
3. Calculate Context Precision@K:
   $$\text{Precision@K} = \frac{\sum_{k=1}^K (\text{Precision@k} \times \text{rel}_k)}{\sum_{k=1}^K \text{rel}_k}$$

### Solution & Test Harness

```python
from typing import List

def calculate_mrr(relevance: List[int]) -> float:
    for idx, rel in enumerate(relevance, start=1):
        if rel == 1:
            return 1.0 / idx
    return 0.0

def calculate_context_precision(relevance: List[int]) -> float:
    total_rel = sum(relevance)
    if total_rel == 0:
        return 0.0
    
    cumulative_hits = 0
    precision_sum = 0.0
    for k, rel in enumerate(relevance, start=1):
        if rel == 1:
            cumulative_hits += 1
            precision_sum += cumulative_hits / k
            
    return precision_sum / total_rel

# Verification
if __name__ == "__main__":
    # Case 1: First item is relevant
    rel_optimal = [1, 0, 1]
    assert calculate_mrr(rel_optimal) == 1.0
    prec_optimal = calculate_context_precision(rel_optimal)
    # k=1: 1/1=1.0; k=2: rel=0; k=3: 2/3=0.666; sum = 1.666 / 2 = 0.833
    assert abs(prec_optimal - 0.8333) < 0.001
    
    # Case 2: Relevant item delayed to 3rd position
    rel_delayed = [0, 0, 1]
    assert abs(calculate_mrr(rel_delayed) - (1.0 / 3.0)) < 0.001
    assert abs(calculate_context_precision(rel_delayed) - (1.0 / 3.0)) < 0.001
    
    print("[Exercise 3] PASSED: MRR and Context Precision verified.")
```
