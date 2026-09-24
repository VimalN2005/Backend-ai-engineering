# Day 04: Practical Hands-On Exercises

Solve these three production-oriented challenges to master clean control flow, guard clauses, and high-performance comprehensions.

---

## Exercise 1: Guard Clause Refactor (Easy-Medium)

### Problem Statement:
The following legacy API controller validates an incoming LLM prompt request using nested `if-else` blocks.
Refactor `handle_chat_request` using the **Guard Clause** pattern so that all preconditions are validated upfront, error responses return immediately, and the core processing logic remains completely un-nested at the root indentation level.

### Legacy Code to Refactor:
```python
from typing import Dict, Any

def handle_chat_request_legacy(request: Dict[str, Any]) -> Dict[str, Any]:
    if request:
        if "user_id" in request and request["user_id"]:
            if "prompt" in request and request["prompt"].strip():
                if len(request["prompt"]) <= 2000:
                    # Core business logic
                    tokens = len(request["prompt"].split())
                    return {"status": "success", "response": f"Processed {tokens} tokens"}
                else:
                    return {"status": "error", "message": "Prompt exceeds 2000 character limit"}
            else:
                return {"status": "error", "message": "Prompt cannot be empty"}
        else:
            return {"status": "error", "message": "Missing or invalid user_id"}
    else:
        return {"status": "error", "message": "Empty request body"}
```

### Starter Template:
```python
def handle_chat_request_clean(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Refactored version using Guard Clauses (Fail-Fast pattern).
    """
    # TODO: Invert each condition, return error dictionaries immediately,
    # and keep the happy path un-indented at the bottom.
    pass
```

---

## Exercise 2: RAG Chunk Metadata Normalizer (Medium)

### Problem Statement:
When ingesting documents into a vector database (e.g., Qdrant or Pinecone), each text chunk carries metadata.
Write a function `normalize_chunk_metadata(raw_chunks: List[Dict[str, Any]], required_tags: Set[str]) -> Dict[str, Dict[str, Any]]` using **dictionary and set comprehensions** that:
1. Filters out chunks that lack an `"id"` or have empty `"content"`.
2. Normalizes tags to lowercase using a set comprehension.
3. Keeps only chunks that contain *at least one* tag from `required_tags`.
4. Returns a dictionary mapping `chunk_id -> { "token_count": ..., "tags": ... }`.

### Starter Code:
```python
from typing import List, Dict, Set, Any

def normalize_chunk_metadata(
    raw_chunks: List[Dict[str, Any]], 
    required_tags: Set[str]
) -> Dict[str, Dict[str, Any]]:
    # TODO: Use dictionary and set comprehensions with conditional filters
    pass

# Test Cases:
sample_chunks = [
    {"id": "c1", "content": "FastAPI async endpoints", "tags": ["PYTHON", "Backend"]},
    {"id": "c2", "content": "", "tags": ["python"]},  # Empty content -> discard
    {"id": "c3", "content": "Frontend UI components", "tags": ["React", "CSS"]}, # No matching tags
    {"id": "c4", "content": "RAG embeddings with Qdrant", "tags": ["ai", "VECTOR_DB"]}
]

required = {"python", "ai"}
result = normalize_chunk_metadata(sample_chunks, required)
print(result)
# Expected: Contains 'c1' and 'c4' with normalized lowercase tags and token counts
```

---

## Exercise 3: Streaming Token Batcher with Overlap (Advanced)

### Problem Statement:
In LLM fine-tuning and vector embedding pipelines, long texts must be sliced into fixed-token windows with a specified overlap to preserve context across boundaries.
Write a generator function `stream_token_batches(text: str, batch_size: int = 10, overlap: int = 2) -> Iterator[List[str]]` that:
1. Validates that `batch_size > overlap >= 0` using a guard clause.
2. Yields successive token batches lazily without buffering all batches in memory.
3. Properly shifts the sliding window by `step = batch_size - overlap`.

### Starter Code:
```python
from typing import Iterator, List

def stream_token_batches(
    text: str, 
    batch_size: int = 10, 
    overlap: int = 2
) -> Iterator[List[str]]:
    """
    Yields sliding token batches with context overlap.
    """
    # Step 1: Guard clause validation
    # Step 2: Split text into tokens
    # Step 3: Yield token slices lazily using range() with step
    pass

# Test Verification:
sample_text = "Python backend engineering for AI systems requires mastering FastAPI Django and LLMs"
batches = list(stream_token_batches(sample_text, batch_size=6, overlap=2))
for i, batch in enumerate(batches):
    print(f"Batch {i}: {' '.join(batch)}")
```
