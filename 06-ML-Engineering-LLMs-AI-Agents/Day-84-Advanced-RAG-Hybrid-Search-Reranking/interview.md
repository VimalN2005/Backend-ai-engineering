# Day 84: Principal AI Systems Interview Questions & Answers

## Question 1: Explain the architectural trade-offs between Bi-Encoders and Cross-Encoders. Why can't we use Cross-Encoders for initial retrieval across 10 million vectors?

### Deep Technical Answer
In modern retrieval systems, Bi-Encoders and Cross-Encoders occupy fundamentally different points on the latency vs. precision pareto frontier:

### 1. Bi-Encoder Architecture (Retriever)
- **Mechanics**: The Query and the Document are passed through two separate (or Siamese) Transformer forward passes:
  $$q = \text{Encoder}(Q), \quad d = \text{Encoder}(D)$$
- **Decoupled Offline Pre-computation**: Every document in the database is encoded into a dense vector once during ingestion and stored in a vector index (HNSW / IVF).
- **Online Complexity**: Online search requires only encoding the single query vector ($\mathcal{O}(1)$), followed by sub-5ms vector distance operations ($\mathcal{O}(\log N)$).
- **Limitation**: **Zero Cross-Attention**. The query and document tokens never interact directly in attention layers. Information is compressed into a single vector, losing subtle syntactic relations and negation.

### 2. Cross-Encoder Architecture (Reranker)
- **Mechanics**: The Query and Document are concatenated into a single joint sequence separated by delimiter tokens:
  $$\text{Input} = \text{[CLS]} \circ Q \circ \text{[SEP]} \circ D \circ \text{[SEP]}$$
- **Full Joint Self-Attention**: Every query token computes dot-product attention against every document token across all layers. It deeply evaluates exact qualifiers, syntactic dependencies, and complex conditionality.
- **Why It Cannot Scale to 10M Vectors**:
  - Documents **cannot be pre-computed offline** because attention depends dynamically on the specific query tokens.
  - Searching 10,000,000 documents requires running **10,000,000 full Transformer forward passes per query**.
  - At 5 ms per forward pass, a single search would take **13.8 hours of GPU compute**!

**Production Solution (Two-Stage Pipeline)**:
Bi-Encoders or Hybrid BM25+Vector engines retrieve the Top-100 candidates in 8 ms (Stage 1). The Cross-Encoder reranks only those 100 candidates in 25 ms (Stage 2), achieving cross-attention precision at low total latency.

---

## Question 2: How does BM25 handle term frequency saturation and document length normalization, and why does pure dense vector search fail on exact keyword queries?

### Deep Technical Answer

### The Mathematical Mechanics of BM25
Unlike standard TF-IDF, which scales logarithmically or linearly with term repetitions, Okapi BM25 introduces two crucial non-linear controls:

1. **Term Frequency Saturation ($k_1$)**:
   $$\text{TF\_Component} = \frac{f(q_i, D) \cdot (k_1 + 1)}{f(q_i, D) + k_1 \cdot \text{len\_norm}}$$
   - As $f(q_i, D) \to \infty$, the term frequency component asymptotically approaches $k_1 + 1$.
   - A document mentioning a keyword 50 times does not score $10\times$ higher than a document mentioning it 5 times. This prevents keyword-stuffed documents from hijacking search results.
2. **Document Length Normalization ($b$)**:
   $$\text{len\_norm} = 1 - b + b \cdot \left(\frac{|D|}{\text{avgdl}}\right)$$
   - Long documents naturally contain more words. Parameter $b \in [0, 1]$ penalizes long documents for verbosity while rewarding concise, information-dense documents.

### Why Pure Dense Search Fails on Exact Keywords
Dense embedding models map text to continuous semantic latent spaces. In high-dimensional spaces:
- Exact part numbers (`PART-9842-X`), error codes (`ERR_502_BAD_GATEWAY`), and UUIDs are broken into arbitrary subword tokens by BPE tokenizers.
- Their vector representations drift near generic alphanumeric clusters, failing to match the exact query. BM25 indexes exact tokens in an inverted list, achieving 100% precision on exact identifiers.

---

## Question 3: Compare Reciprocal Rank Fusion (RRF) with Convex Linear Combination ($\alpha \cdot \text{Dense} + (1 - \alpha) \cdot \text{Sparse}$). Under what operational conditions does RRF outperform linear weighting?

### Deep Technical Answer

### 1. Convex Linear Combination
$$\text{Score}(d) = \alpha \hat{S}_{\text{dense}}(d) + (1 - \alpha) \hat{S}_{\text{sparse}}(d)$$
- **Requirement**: Both score streams must be normalized to $[0, 1]$ via Min-Max or Softmax normalization.
- **Operational Vulnerability**: Min-Max normalization is hypersensitive to **outliers**. A single outlier document with a massive BM25 score compresses all other BM25 scores near zero, artificially suppressing the sparse signal for that query.

### 2. Reciprocal Rank Fusion (RRF)
$$\text{RRF}(d) = \sum_{m \in M} \frac{1}{k_{\text{rrf}} + r_m(d)}$$
- **Invariance**: Evaluates strictly **ordinal ranks**, ignoring raw score distributions completely.
- **Why RRF Outperforms Linear Weighting**:
  1. *Zero Calibration*: Requires no per-collection score tuning or score distribution assumptions.
  2. *Robustness Across Query Intent*: For exact keyword queries, BM25 ranks the document #1, giving it maximum reciprocal weight regardless of whether the vector score is low. For conceptual queries, dense search ranks it #1.
  3. *Constant $k_{\text{rrf}} = 60$*: Prevents high-ranking documents from dominating exponentially while maintaining smooth decay across the top 20 items.

---

## Question 4: How do you implement Metadata Self-Querying in production RAG systems? What latency and failure recovery mechanisms are required?

### Deep Technical Answer
Metadata Self-Querying uses an LLM to decompose an unstructured user query into a clean semantic search query alongside structured metadata filter predicates.

### Architecture & Execution Flow
1. **Pydantic Filter Schema**:
   Define an explicit Pydantic model for target filters:
   ```python
   class QueryFilter(BaseModel):
       cleaned_semantic_query: str
       environment: Optional[Literal["prod", "staging", "dev"]]
       service_name: Optional[str]
       created_after_year: Optional[int]
   ```
2. **Small-Model Extraction**:
   Use a low-latency model (e.g. `gpt-4o-mini`, `claude-3-5-haiku`, or fine-tuned Llama-3-8B) with structured outputs / JSON mode. Extraction takes sub-150ms.
3. **Failure Recovery Mechanisms**:
   - *Filter Relaxation*: If the structured filter yields zero candidates from the vector store, automatically fall back to an unconstrained hybrid search with a telemetry flag.
   - *Schema Validation Guardrail*: If the LLM generates an invalid filter field, catch the Pydantic ValidationError and discard the filter rather than crashing the database query.

---

## Question 5: What are the key offline evaluation metrics for retrieval quality in Advanced RAG?

### Deep Technical Answer
Evaluating the retrieval stage independently of generation is essential for diagnosing RAG bottlenecks:

1. **Recall@K**:
   $$\text{Recall}@K = \frac{|\text{Retrieved Top-K} \cap \text{Ground Truth Relevant}|}{|\text{Ground Truth Relevant}|}$$
   Measures what percentage of all relevant documents were successfully surfaced in the candidate pool.
2. **Precision@K**:
   $$\text{Precision}@K = \frac{|\text{Retrieved Top-K} \cap \text{Ground Truth Relevant}|}{K}$$
   Measures the signal-to-noise ratio in the retrieved context. High precision prevents context window pollution.
3. **Mean Reciprocal Rank (MRR@K)**:
   $$\text{MRR} = \frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{\text{rank}_i}$$
   Evaluates how close to the top the first relevant document was placed. Essential for single-answer lookup tasks.
4. **Normalized Discounted Cumulative Gain (NDCG@K)**:
   Accounts for multi-level graded relevance (e.g. highly relevant vs partially relevant) and heavily penalizes placing relevant documents lower in the ranking list.
