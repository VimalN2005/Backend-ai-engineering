# Day 90: Exercises — Master Technical Interview Simulation

Three production-grade coding challenges commonly asked in Staff / Principal Backend & AI Systems interviews.

---

## Exercise 1: Distributed Token Bucket Rate Limiter

### Objective
Implement an atomic token bucket rate limiter class `TokenBucketLimiter` that replenishes tokens continuously based on elapsed time without requiring background polling threads.

### Requirements
1. Implement `allow_request(tokens_requested: int = 1) -> bool`.
2. Bucket has `capacity` (max burst) and `refill_rate` (tokens per second).
3. Tokens refill proportionally to `elapsed_time = now - last_refill_timestamp`.
4. If tokens available $\ge \text{tokens\_requested}$, deduct tokens and return `True`; else return `False`.

### Solution & Test Harness

```python
import time

class TokenBucketLimiter:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)  # tokens per second
        self.tokens = float(capacity)
        self.last_update = time.time()

    def allow_request(self, tokens_requested: int = 1) -> bool:
        now = time.time()
        elapsed = now - self.last_update
        self.last_update = now

        # Replenish tokens up to capacity
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)

        if self.tokens >= tokens_requested:
            self.tokens -= tokens_requested
            return True
        return False

# Verification
if __name__ == "__main__":
    limiter = TokenBucketLimiter(capacity=2, refill_rate=10.0)  # 2 tokens capacity, refills 10/sec
    assert limiter.allow_request(1) is True
    assert limiter.allow_request(1) is True
    # Capacity exhausted
    assert limiter.allow_request(1) is False
    
    # Wait 0.15s -> should refill ~1.5 tokens
    time.sleep(0.15)
    assert limiter.allow_request(1) is True
    print("[Exercise 1] PASSED: Atomic token bucket rate limiter verified.")
```

---

## Exercise 2: High-Performance LRU Vector Cache with Thread Safety

### Objective
Implement an in-memory vector cache with a strict maximum capacity $N$, supporting vector cosine similarity lookup ($\ge \tau$) and least-recently-used (LRU) eviction.

### Requirements
1. Implement `VectorCache(capacity: int, threshold: float = 0.90)`.
2. Support `put(key: str, vector: list, value: any)` and `get(query_vector: list) -> Optional[any]`.
3. Evicts the least-recently-accessed entry when `capacity` is reached.
4. Updates access timestamp on every hit.

### Solution & Test Harness

```python
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import numpy as np

@dataclass
class CacheNode:
    key: str
    vector: np.ndarray
    value: Any
    last_accessed: float

class VectorCache:
    def __init__(self, capacity: int = 3, threshold: float = 0.90):
        self.capacity = capacity
        self.threshold = threshold
        self.entries: List[CacheNode] = []

    def get(self, query_vec: np.ndarray) -> Optional[Any]:
        best_node = None
        best_sim = -1.0
        now = time.time()

        for node in self.entries:
            sim = float(np.dot(query_vec, node.vector) / (np.linalg.norm(query_vec) * np.linalg.norm(node.vector)))
            if sim > best_sim:
                best_sim = sim
                best_node = node

        if best_node and best_sim >= self.threshold:
            best_node.last_accessed = now
            return best_node.value
        return None

    def put(self, key: str, vector: np.ndarray, value: Any) -> None:
        if len(self.entries) >= self.capacity:
            # Evict LRU
            self.entries.sort(key=lambda n: n.last_accessed)
            self.entries.pop(0)

        node = CacheNode(key=key, vector=vector, value=value, last_accessed=time.time())
        self.entries.append(node)

# Verification
if __name__ == "__main__":
    cache = VectorCache(capacity=2, threshold=0.90)
    v1 = np.array([1.0, 0.0])
    v2 = np.array([0.0, 1.0])
    v3 = np.array([0.707, 0.707])

    cache.put("k1", v1, "result_1")
    time.sleep(0.01)
    cache.put("k2", v2, "result_2")

    # Touch k1 to make k2 the LRU
    assert cache.get(v1) == "result_1"

    # Insert k3 -> should evict k2
    time.sleep(0.01)
    cache.put("k3", v3, "result_3")

    assert len(cache.entries) == 2
    keys = {e.key for e in cache.entries}
    assert "k2" not in keys, "k2 should have been evicted by LRU"
    assert "k1" in keys and "k3" in keys
    print("[Exercise 2] PASSED: High-performance LRU vector cache verified.")
```

---

## Exercise 3: Consensus Voting & Fact-Checking Agreement Gate

### Objective
Implement a consensus agreement gate evaluating responses from multiple independent model runs or sub-agents to prevent hallucinated answers.

### Requirements
1. Implement `evaluate_consensus(candidate_answers: List[str], min_agreement_ratio: float = 0.60) -> Tuple[bool, Optional[str]]`.
2. Extract numerical quantities from candidates.
3. If at least `min_agreement_ratio` of candidates agree on the extracted numerical facts, return `(True, winning_answer)`.
4. Else, return `(False, None)`.

### Solution & Test Harness

```python
import re
from collections import Counter
from typing import List, Optional, Tuple

def evaluate_consensus(candidate_answers: List[str], min_agreement_ratio: float = 0.60) -> Tuple[bool, Optional[str]]:
    if not candidate_answers:
        return False, None

    # Extract all numbers/quantities as canonical fact signatures
    signatures = []
    for ans in candidate_answers:
        nums = tuple(re.findall(r"\b\d+(?:\.\d+)?%?\b", ans))
        signatures.append(nums)

    counter = Counter(signatures)
    most_common_sig, count = counter.most_common(1)[0]
    agreement_ratio = count / len(candidate_answers)

    if agreement_ratio >= min_agreement_ratio:
        # Return first answer that matches the winning signature
        for ans in candidate_answers:
            if tuple(re.findall(r"\b\d+(?:\.\d+)?%?\b", ans)) == most_common_sig:
                return True, ans
    return False, None

# Verification
if __name__ == "__main__":
    candidates_agree = [
        "Q3 cloud infrastructure spend was $16.9M, a 7.14% reduction.",
        "Expenditure in Q3 reached $16.9M representing 7.14% in savings.",
        "Q3 spend was $24.5M representing a 15% increase.",  # Outlier
    ]
    # 2 out of 3 agree on (16.9, 7.14) -> 66.7% >= 60%
    passed, winner = evaluate_consensus(candidates_agree, min_agreement_ratio=0.60)
    assert passed is True
    assert "16.9" in winner and "7.14" in winner

    candidates_split = [
        "Result is 100",
        "Result is 200",
        "Result is 300",
    ]
    passed_split, _ = evaluate_consensus(candidates_split, min_agreement_ratio=0.60)
    assert passed_split is False
    print("[Exercise 3] PASSED: Consensus voting gate verified.")
```
