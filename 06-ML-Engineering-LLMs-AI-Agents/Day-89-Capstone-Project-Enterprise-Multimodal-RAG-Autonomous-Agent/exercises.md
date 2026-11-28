# Day 89: Exercises — Capstone Multimodal RAG & Autonomous AI Agent Backend

Three production-grade engineering exercises covering table parsing, Reciprocal Rank Fusion, and multi-agent task routing.

---

## Exercise 1: Layout-Aware Table Parser & Serializer

### Objective
Implement a parser that takes raw tabular row data, computes column-aligned Markdown table representations, and generates a natural-language summary string for dense vector indexing.

### Requirements
1. Implement `serialize_table_to_markdown(headers: List[str], rows: List[List[Any]]) -> str`.
2. Implement `generate_table_summary(table_name: str, headers: List[str], row_count: int) -> str`.
3. Ensure Markdown output contains proper pipe delimiters and header separators (`|---|`).

### Solution & Test Harness

```python
from typing import Any, List

def serialize_table_to_markdown(headers: List[str], rows: List[List[Any]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    data_lines = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header_line, separator_line] + data_lines)

def generate_table_summary(table_name: str, headers: List[str], row_count: int) -> str:
    cols_str = ", ".join(headers)
    return f"Structured table '{table_name}' containing {row_count} records across columns: {cols_str}."

# Verification
if __name__ == "__main__":
    headers = ["Quarter", "Revenue", "Cost"]
    data = [["Q1", 100, 20], ["Q2", 120, 22]]
    
    md = serialize_table_to_markdown(headers, data)
    assert "| Quarter | Revenue | Cost |" in md
    assert "| Q1 | 100 | 20 |" in md
    
    summary = generate_table_summary("financials", headers, len(data))
    assert "containing 2 records" in summary
    assert "Quarter, Revenue, Cost" in summary
    print("[Exercise 1] PASSED: Table serializer and summary generator verified.")
```

---

## Exercise 2: Reciprocal Rank Fusion (RRF) Ranking Engine

### Objective
Implement the Reciprocal Rank Fusion (RRF) algorithm to merge ranked candidate lists from sparse BM25 and dense vector retrievers without score normalization.

### Requirements
1. Implement `reciprocal_rank_fusion(ranked_lists: List[List[str]], k: int = 60) -> List[Tuple[str, float]]`.
2. Formula: $RRF(d) = \sum_{m} \frac{1}{k + r_m(d)}$ where $r_m(d)$ is 1-indexed rank.
3. Return documents sorted by descending RRF score.

### Solution & Test Harness

```python
from typing import Dict, List, Tuple

def reciprocal_rank_fusion(ranked_lists: List[List[str]], k: int = 60) -> List[Tuple[str, float]]:
    rrf_scores: Dict[str, float] = {}

    for ranked_list in ranked_lists:
        for rank, doc_id in enumerate(ranked_list, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank))

    # Sort descending by score
    sorted_candidates = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_candidates

# Verification
if __name__ == "__main__":
    # BM25 returned: doc_A (rank 1), doc_B (rank 2)
    # Dense returned: doc_B (rank 1), doc_C (rank 2)
    list_bm25 = ["doc_A", "doc_B"]
    list_dense = ["doc_B", "doc_C"]

    fused = reciprocal_rank_fusion([list_bm25, list_dense], k=60)
    
    # doc_B appeared in both (rank 2 in BM25, rank 1 in Dense) -> should have highest score!
    # doc_B: 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.03252
    # doc_A: 1/61 = 0.01639
    # doc_C: 1/62 = 0.01612
    assert fused[0][0] == "doc_B", f"Expected doc_B at rank 1, got {fused[0][0]}"
    assert abs(fused[0][1] - 0.03252) < 0.0001
    print("[Exercise 2] PASSED: Reciprocal Rank Fusion engine verified.")
```

---

## Exercise 3: Supervisor-Worker Task Routing Dispatcher

### Objective
Build a supervisor routing dispatcher that analyzes incoming user prompts and assigns sub-tasks to specialized worker queues (`retrieval_worker`, `analytics_worker`, `both`).

### Requirements
1. Implement `route_user_request(prompt: str) -> str`.
2. If query asks for facts/documentation only -> `"retrieval_worker"`.
3. If query asks for mathematical calculations or metrics variance only -> `"analytics_worker"`.
4. If query requires fetching data AND calculating metrics -> `"composite_workflow"`.

### Solution & Test Harness

```python
def route_user_request(prompt: str) -> str:
    p = prompt.lower()
    needs_retrieval = any(w in p for w in ["find", "search", "show", "what is", "report", "document", "who"])
    needs_calc = any(w in p for w in ["compute", "calculate", "percentage", "variance", "average", "growth", "margin"])

    if needs_retrieval and needs_calc:
        return "composite_workflow"
    if needs_calc:
        return "analytics_worker"
    return "retrieval_worker"

# Verification
if __name__ == "__main__":
    assert route_user_request("What is the company PTO policy?") == "retrieval_worker"
    assert route_user_request("Compute the compound interest on $5000 at 5% over 3 years") == "analytics_worker"
    assert route_user_request("Find Q3 cloud spend from the report and calculate percentage savings vs Q1") == "composite_workflow"
    print("[Exercise 3] PASSED: Supervisor routing dispatcher verified.")
```
