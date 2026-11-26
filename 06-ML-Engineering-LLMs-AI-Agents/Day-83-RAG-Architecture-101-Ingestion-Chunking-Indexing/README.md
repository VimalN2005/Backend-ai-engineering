# Day 83: RAG Architecture 101: Ingestion, Chunking, Indexing & Context Injection

Large Language Models (LLMs) are frozen in time by their training cut-off date and lack access to proprietary enterprise knowledge. Furthermore, fine-tuning an LLM on dynamic organizational data is computationally expensive, prone to catastrophic forgetting, and vulnerable to hallucinations.

**Retrieval-Augmented Generation (RAG)** solves this by dynamically fetching relevant external knowledge from an indexed vector database and injecting it into the LLM prompt context window at inference time.

---

## 1. Executive RAG Architecture Blueprint

```
+--------------------------------------------------------------------------------------------------+
|                            END-TO-END PRODUCTION RAG 101 ARCHITECTURE                            |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|  [ OFFLINE INGESTION PIPELINE ]                                                                  |
|  Raw Source Documents (PDF, Markdown, HTML, Database Records)                                    |
|         │                                                                                        |
|         │ 1. Document Extraction & Cleansing (stripping HTML boilerplate, OCR)                   |
|         ▼                                                                                        |
|  [ Multi-Strategy Chunking Engine ]                                                              |
|         ├─ Fixed-Size Token Chunking with Sliding Overlap (e.g., 512 tokens, 64 overlap)         |
|         ├─ Recursive Character Chunking (Separators: \n\n -> \n -> space -> empty)               |
|         └─ Semantic / Structure-Aware Chunking (Markdown headers: #, ##, ###)                   |
|         │                                                                                        |
|         │ 2. Compute Dense Vector Embeddings (dim = 384 / 768 / 1536)                            |
|         ▼                                                                                        |
|  [ Vector Store & Metadata Index ] (pgvector / Qdrant) ──> Persistent Knowledge Base             |
|                                                                                                  |
|                                                                                                  |
|  [ ONLINE RETRIEVAL & INFERENCE PIPELINE ]                                                       |
|  User Query: "What is our company refund policy for enterprise contracts?"                       |
|         │                                                                                        |
|         │ 3. Generate Query Vector Embedding                                                     |
|         ▼                                                                                        |
|  [ Top-K Semantic Similarity Search ] (Cosine Distance <= 0.35, Limit = 5)                      |
|         │                                                                                        |
|         │ 4. Context Optimization & Reordering ("Lost-in-the-Middle" mitigation)                 |
|         ▼                                                                                        |
|  [ Context Injection & Prompt Construction ]                                                     |
|         │ 5. Structured System Prompt + Retrieved Source Context Chunks + Query                  |
|         ▼                                                                                        |
|  [ LLM Inference Engine ] (GPT-4o, Claude 3.5, Llama 3)                                          |
|         │                                                                                        |
|         ▼                                                                                        |
|  Grounded, Cited Response: "Per Section 4.2 of Enterprise Agreement [Doc #104]..."              |
+--------------------------------------------------------------------------------------------------+
```

---

## 2. Document Chunking Strategies

The choice of chunking strategy directly dictates downstream retrieval precision and LLM generation quality:

| Chunking Strategy | Algorithmic Mechanism | Optimal Use Case | Failure Mode / Trade-off |
| :--- | :--- | :--- | :--- |
| **Fixed-Size Chunking** | Splits text at fixed token count with fixed sliding overlap (e.g. 512/64). | Fast baseline, uniform vector representations. | Cuts sentences mid-thought; fractures semantic context. |
| **Recursive Character Chunking** | Recursively tests separators `["\n\n", "\n", " ", ""]` until chunk fits budget. | Articles, essays, documentation, books. | May still group unrelated bullet points together. |
| **Markdown / Structural Chunking** | Splits on heading boundaries (`#`, `##`, `###`), attaching breadcrumb paths to chunk metadata. | Technical wikis, API references, Gitbook docs. | Requires pre-formatted markdown/HTML source documents. |
| **Semantic Chunking** | Computes cosine distance between adjacent sentence embeddings; splits when distance exceeds threshold percentile. | Dense legal agreements, transcripts, research papers. | High compute overhead: requires $N$ embedding calls per document during ingestion. |

### 2.1 The Critical Role of Chunk Overlap
When slicing text into chunks, critical facts often span the boundary between two adjacent blocks. 
- **Without Overlap**: If a fact starts in Chunk $A$ and finishes in Chunk $B$, neither chunk contains sufficient context for the embedding model to match the query, resulting in false negatives.
- **With Overlap ($10\% - 15\%$)**: Slicing with sliding overlap (e.g., 512 tokens with 64-token overlap) guarantees that boundary concepts appear unbroken in at least one chunk.

---

## 3. The "Lost in the Middle" Phenomenon

Research by Liu et al. (Stanford, 2023) demonstrated that Transformer-based LLMs do not attend equally across their context window:
- **Primacy Bias**: High attention weight on tokens at the extreme beginning of the context.
- **Recency Bias**: High attention weight on tokens at the extreme end of the context.
- **U-Shaped Degradation Curve**: Retrieval recall drops by up to **$30\% - 40\%$** when relevant context chunks are placed in the middle of a multi-document prompt.

```
LLM Retrieval Attention Distribution:
100% |  ██                                                          ██
     |  ██                                                          ██
     |  ████                                                      ████
 50% |    ████                                                  ████
     |      ████                                              ████
     |        ██████████████████████████████████████████████████
  0% +-----------------------------------------------------------------
     Beginning (Rank 1, 3)             Middle (Rank 4, 5)        End (Rank 2)
```

### Mitigation: Alternating Context Reordering
Instead of injecting chunks in descending order of similarity $[1, 2, 3, 4, 5]$:
Reorder chunks such that the highest-scoring chunk ($1$) is placed at the top, the second highest ($2$) is placed at the bottom, and lower-ranked chunks ($3, 4, 5$) are placed in the middle:
$$\text{Reordered} = [1, 3, 5, 4, 2]$$

---

## 4. Context Injection & Anti-Hallucination Prompt Architecture

A production RAG prompt must enforce strict grounding, source attribution, and refusal criteria:

```text
You are an authoritative enterprise knowledge assistant. Answer the user's question STRICTLY based on the provided retrieved context below.

CRITICAL INSTRUCTIONS:
1. Base your answer ONLY on the facts directly stated in the [CONTEXT CHUNKS]. Do not extrapolate, infer, or incorporate external training knowledge.
2. If the context does not contain sufficient facts to answer the question with complete certainty, explicitly state: "I do not have sufficient information in the provided documentation to answer this question."
3. Every factual assertion in your response MUST include an explicit source citation in brackets matching the document ID, e.g. [Doc #3].

[CONTEXT CHUNKS]:
---
Chunk ID: doc_01 | Source: corporate_policy_v2.md
Text: Enterprise subscriptions include 24/7 dedicated telephone support with a 15-minute SLA for Priority 1 outages.
---
Chunk ID: doc_02 | Source: enterprise_refund_terms.pdf
Text: Refunds for annual enterprise agreements are prorated based on remaining calendar days, subject to a 10% administrative termination fee.
---

[USER QUERY]:
What is the refund policy for annual enterprise agreements?
```
