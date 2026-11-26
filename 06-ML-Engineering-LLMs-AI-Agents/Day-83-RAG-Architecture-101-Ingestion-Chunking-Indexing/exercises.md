# Day 83: Production Exercises — RAG 101 Architecture

Below are 3 enterprise-grade production engineering exercises covering sliding-window chunking, context reordering, and citation auditing.

---

## Exercise 1: Token-Aware Sliding Window Chunker with Boundary Preservation

### Problem Statement
Build a `SentencePreservingChunker` that breaks text into chunks of maximum $N$ words with $K$ words overlap, but never splits a sentence in the middle. If adding a sentence exceeds the chunk budget, close the current chunk and start a new one using the last sentence as the overlap.

### Complete Solution & Verification Test

```python
"""
Exercise 1: Sentence-Preserving Sliding Window Chunker
"""
import re
from typing import List


class SentencePreservingChunker:
    def __init__(self, max_words: int = 30) -> None:
        self.max_words = max_words

    def chunk(self, text: str) -> List[str]:
        # Split into sentences using regex
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        chunks: List[str] = []
        current_chunk: List[str] = []
        current_word_count = 0

        for sentence in sentences:
            sentence_word_count = len(sentence.split())
            if current_word_count + sentence_word_count > self.max_words and current_chunk:
                chunks.append(" ".join(current_chunk))
                # Carry over last sentence for overlap
                last_sentence = current_chunk[-1]
                current_chunk = [last_sentence, sentence]
                current_word_count = len(last_sentence.split()) + sentence_word_count
            else:
                current_chunk.append(sentence)
                current_word_count += sentence_word_count

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks


# Verification Test
if __name__ == "__main__":
    text = (
        "Enterprise RAG architectures require clean document ingestion. "
        "Chunking strategies must balance semantic context and token limits. "
        "Sentence boundaries should never be fractured mid-thought. "
        "Sliding overlap guarantees facts are preserved across splits. "
        "Vector databases index these chunks for low-latency retrieval."
    )

    chunker = SentencePreservingChunker(max_words=25)
    chunks = chunker.chunk(text)

    print(f"Generated {len(chunks)} sentence-aligned chunks:")
    for idx, c in enumerate(chunks, 1):
        print(f"  [{idx}] ({len(c.split())} words): \"{c}\"")

    assert len(chunks) >= 2
    # Verify no sentence is sliced mid-word
    for c in chunks:
        assert c.endswith((".", "!", "?")), "Every chunk must end on a sentence boundary"

    print("Exercise 1 Verification: All Assertions Passed!\n")
```

---

## Exercise 2: "Lost-in-the-Middle" Context Reorderer

### Problem Statement
Implement the bidirectional alternating context reordering algorithm that mitigates the U-shaped attention dip in long-context LLMs. Given a list of ranked documents $[d_1, d_2, d_3, d_4, d_5]$, it must return $[d_1, d_3, d_5, d_4, d_2]$ so the two highest-scoring documents are placed at the extreme beginning and end of the prompt.

### Complete Solution & Verification Test

```python
"""
Exercise 2: Lost-in-the-Middle Context Reorderer
"""
from typing import List, TypeVar

T = TypeVar("T")


def reorder_lost_in_the_middle(items: List[T]) -> List[T]:
    if len(items) <= 2:
        return list(items)

    result: List[T] = [None] * len(items)  # type: ignore
    left = 0
    right = len(items) - 1

    for idx, item in enumerate(items):
        if idx % 2 == 0:
            result[left] = item
            left += 1
        else:
            result[right] = item
            right -= 1

    return result


# Verification Test
if __name__ == "__main__":
    ranked_docs = ["Rank_1", "Rank_2", "Rank_3", "Rank_4", "Rank_5"]
    reordered = reorder_lost_in_the_middle(ranked_docs)

    print("Original Ranking: ", ranked_docs)
    print("Reordered Context:", reordered)

    assert reordered == ["Rank_1", "Rank_3", "Rank_5", "Rank_4", "Rank_2"]
    assert reordered[0] == "Rank_1", "Highest ranked doc must be at index 0"
    assert reordered[-1] == "Rank_2", "2nd highest ranked doc must be at final index"

    print("Exercise 2 Verification: All Assertions Passed!\n")
```

---

## Exercise 3: Automated Grounding & Citation Auditor

### Problem Statement
Write a verification function `audit_citations(response: str, valid_chunk_ids: Set[str])` that verifies:
1. Every citation formatted as `[doc_xyz]` exists in the set of injected `valid_chunk_ids`.
2. Flags any hallucinated citations (citations of documents that were never in the prompt context).
3. Verifies that the response contains at least one valid citation unless it states the refusal fallback string.

### Complete Solution & Verification Test

```python
"""
Exercise 3: Automated Grounding & Citation Auditor
"""
import re
from typing import Dict, Set


def audit_citations(response_text: str, valid_chunk_ids: Set[str]) -> Dict[str, Any]:
    refusal_phrase = "i do not have sufficient information"
    if refusal_phrase in response_text.lower():
        return {"valid": True, "cited_ids": [], "hallucinated_ids": [], "is_refusal": True}

    # Extract all bracketed citations e.g. [doc_001]
    cited_ids = set(re.findall(r"\[([a-zA-Z0-9_\-]+)\]", response_text))

    hallucinated = cited_ids - valid_chunk_ids
    valid_citations = cited_ids.intersection(valid_chunk_ids)

    if not valid_citations:
        return {
            "valid": False,
            "error": "Response contains zero valid citations from retrieved context",
            "cited_ids": list(cited_ids),
            "hallucinated_ids": list(hallucinated),
        }

    if hallucinated:
        return {
            "valid": False,
            "error": f"Response cited non-existent or unprovided documents: {list(hallucinated)}",
            "cited_ids": list(cited_ids),
            "hallucinated_ids": list(hallucinated),
        }

    return {
        "valid": True,
        "cited_ids": list(valid_citations),
        "hallucinated_ids": [],
        "is_refusal": False,
    }


# Verification Test
if __name__ == "__main__":
    valid_docs = {"doc_01", "doc_02", "doc_03"}

    # Test Case 1: Valid grounded response
    resp1 = "Per our agreement [doc_01], response time is 15 minutes, with full refund terms outlined in [doc_02]."
    res1 = audit_citations(resp1, valid_docs)
    assert res1["valid"] is True
    assert set(res1["cited_ids"]) == {"doc_01", "doc_02"}
    print("Test Case 1 Passed: Valid citations recognized.")

    # Test Case 2: Hallucinated citation
    resp2 = "This policy was approved by legal [doc_99]."
    res2 = audit_citations(resp2, valid_docs)
    assert res2["valid"] is False
    assert "doc_99" in res2["hallucinated_ids"]
    print("Test Case 2 Passed: Hallucinated citation caught.")

    # Test Case 3: Legitimate refusal
    resp3 = "I do not have sufficient information in the provided documentation to answer this question."
    res3 = audit_citations(resp3, valid_docs)
    assert res3["valid"] is True
    assert res3["is_refusal"] is True
    print("Test Case 3 Passed: Refusal recognized.")

    print("Exercise 3 Verification: All Assertions Passed!\n")
```
