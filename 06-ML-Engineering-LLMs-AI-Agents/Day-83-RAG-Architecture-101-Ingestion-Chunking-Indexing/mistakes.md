# Day 83: Critical RAG 101 Antipatterns & Production Remediations

Retrieval-Augmented Generation (RAG) is conceptually straightforward, but subtle flaws in ingestion, chunking, and prompt synthesis lead to high hallucination rates, missing context, and broken citations.

Below are 7 critical production antipatterns and their production-hardened remediations.

---

## 1. Zero Chunk Overlap Fracturing Boundary Facts

### The Mistake
Chunking text documents into contiguous blocks without overlap (e.g. 500 characters each, 0 overlap).

### Why It Fails
- A critical sentence or numerical rule often starts at character 480 and finishes at character 520. Slicing at 500 splits the fact into two useless fragments.
- When an embedding model embeds each half, neither half contains the full semantic concept, causing the chunk to score poorly against the user query.

### Production Remediation
Always configure a **$10\% - 15\%$ sliding overlap** (e.g., 512 tokens with 64-token overlap). This guarantees that boundary transitions appear intact in at least one chunk.

---

## 2. Naive Character Splitting Breaking Words and Numbers Mid-Thought

### The Mistake
Using simple character slicing `text[i : i + chunk_size]` without checking word or sentence boundaries.

```python
# ANTIPATTERN: Slicing characters blindly
chunks = [text[i : i + 500] for i in range(0, len(text), 500)]
# Word 'authentication' becomes 'auth' in chunk 1 and 'entication' in chunk 2!
```

### Why It Fails
- Words, numbers, and identifiers are bisected.
- The embedding model receives corrupted tokens (`"entication"`), degrading vector semantic fidelity.

### Production Remediation
Use **Recursive Character Chunking** or token-aware chunking with separators (`["\n\n", "\n", " ", ""]`).

---

## 3. Context Stuffing Ignoring the "Lost-in-the-Middle" Phenomenon

### The Mistake
Injecting 10 retrieved chunks directly in descending order of similarity $[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]$ into the prompt context window.

### Why It Fails
- Transformer LLMs exhibit U-shaped attention curves: they attend heavily to the beginning and end of the prompt context, while attention drops by $30\% - 40\%$ across middle tokens.
- Important evidence placed in the middle of a long prompt is overlooked by the model.

### Production Remediation
Reorder chunks to place the highest-confidence evidence at the beginning and end:
```python
# PRODUCTION PATTERN: Lost-in-the-Middle Reordering
# [1, 2, 3, 4, 5] -> [1, 3, 5, 4, 2]
reordered = reorder_lost_in_the_middle(retrieved_chunks)
```

---

## 4. Omitting Chunk Document IDs and Citation Enforcements

### The Mistake
Injecting raw chunk text into the prompt without unique chunk/document identifiers.

### Why It Fails
- When the model answers, end users cannot verify where the information originated.
- When hallucinations occur, it is impossible to determine whether the model hallucinated or the retrieval pipeline fetched incorrect context.

### Production Remediation
Wrap each chunk with explicit ID markers (`[DOCUMENT ID: doc_42 | Source: policy.pdf]`) and instruct the LLM: `"Every factual claim must cite its source in brackets matching the document ID, e.g. [doc_42]"`.

---

## 5. Permissive Instructions Allowing Extrapolation Beyond Context

### The Mistake
Using loose system prompts like `"Answer the user question using the context below and your own knowledge."`

### Why It Fails
- The model treats external pre-training knowledge as ground truth, blending outdated or conflicting training memories into company-specific policies.

### Production Remediation
Enforce strict closed-domain grounding fences:
```text
"Answer the query STRICTLY based on the provided retrieved context. If the answer cannot be directly deduced from the text, state: 'I do not have sufficient information in the provided documentation to answer this question.'"
```

---

## 6. Discarding Structural Metadata (Markdown Headers & Section Breadcrumbs)

### The Mistake
Stripping Markdown `#` headers or HTML section tags before chunking, flattening the document into homogeneous text blocks.

### Why It Fails
- A chunk reading `"Rate limit: 100 req/sec"` becomes ambiguous without knowing whether it applies to the `Free Tier` or the `Enterprise Tier`.
- Retrieval returns the correct sentence under the wrong parent heading.

### Production Remediation
Use **Structure-Aware Chunking** (e.g. `MarkdownHeaderChunker`) that prepends hierarchical breadcrumbs (`"Pricing > Enterprise Tier > Rate Limits"`) to the chunk text or metadata payload.

---

## 7. Treating Structured Tables and Code Blocks as Flat Prose

### The Mistake
Passing complex Markdown or HTML tables through standard 200-character chunkers.

### Why It Fails
- Column headers are separated from row data. In subsequent chunks, raw numbers lose their associated metric headers.
- Code blocks are sliced across variable definitions and function bodies.

### Production Remediation
1. Keep tables intact as single, indivisible chunks, or serialize each table row as a standalone JSON/key-value record (`{"Metric": "Latency", "SLA": "15ms"}`).
2. Use AST-aware code chunkers that split on function and class boundaries.
