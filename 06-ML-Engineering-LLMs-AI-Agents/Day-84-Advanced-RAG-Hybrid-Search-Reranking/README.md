# Day 84: Advanced RAG: Hybrid Search, Cross-Encoder Reranking & Metadata Filtering

While standard RAG (Day 83) relies exclusively on dense vector similarity search, production enterprise systems encounter catastrophic failure modes when queried on exact alphanumeric identifiers (part numbers, UUIDs, function names, error codes like `ERR_HTTP2_SESSION_TIMEOUT`) or domain-specific acronyms.

**Advanced RAG** solves this through **Two-Stage Hybrid Retrieval**:
1. **Stage 1 (High-Recall Hybrid Retrieval)**: Merges sparse keyword matching (BM25) with dense vector embeddings via Reciprocal Rank Fusion (RRF) to retrieve a broad candidate pool ($M \approx 50 - 100$).
2. **Stage 2 (High-Precision Cross-Encoder Reranking)**: Uses full cross-attention deep neural networks to score query-document pairs simultaneously, eliminating false positives and delivering pristine Top-$K$ context ($K \approx 3 - 5$).

---

## 1. Executive Advanced RAG Architecture Blueprint

```
+--------------------------------------------------------------------------------------------------+
|                            PRODUCTION TWO-STAGE ADVANCED RAG PIPELINE                            |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|  [ User Query ] ──> "How to fix Kubernetes CrashLoopBackOff error 137 in namespace prod-api?"   |
|         │                                                                                        |
|         ▼                                                                                        |
|  [ Query Decomposition & Metadata Extraction ]                                                  |
|         ├─ Semantic Search Query: "Kubernetes CrashLoopBackOff error 137"                        |
|         └─ Metadata Filters Extracted: {"namespace": "prod-api", "platform": "kubernetes"}      |
|         │                                                                                        |
|         ▼                                                                                        |
|  [ STAGE 1: HIGH-RECALL HYBRID RETRIEVAL (Parallel Execution) ]                                 |
|         │                                                                                        |
|         ├── Branch A: Sparse Lexical Retrieval (BM25)                                            |
|         │   Matches exact keywords: "CrashLoopBackOff", "137", "prod-api"                        |
|         │   Retrieves Top-50 Sparse Candidates                                                   |
|         │                                                                                        |
|         └── Branch B: Dense Semantic Retrieval (Vector Index / HNSW)                             |
|             Matches conceptual intent: container OOM killed, memory limit exhaustion            |
|             Retrieves Top-50 Dense Candidates                                                    |
|         │                                                                                        |
|         ▼                                                                                        |
|  [ Reciprocal Rank Fusion (RRF) & Metadata Filter Pushdown ]                                     |
|         │ RRF_Score(d) = sum( 1 / (60 + rank_m(d)) )                                             |
|         │ Fused Top-30 Unique Candidates                                                         |
|         ▼                                                                                        |
|  [ STAGE 2: HIGH-PRECISION CROSS-ENCODER RERANKING ]                                            |
|         │ Full Joint Attention: CrossEncoder(Query, Document_i) -> Logit Score in [-inf, +inf]  |
|         │ Captures negation, exact token alignment, and multi-sentence logic dependencies        |
|         ▼                                                                                        |
|  [ Top-5 Highest-Scoring Grounded Chunks ] ──> Injected into LLM Context Window                  |
+--------------------------------------------------------------------------------------------------+
```

---

## 2. Sparse (BM25) vs. Dense (Bi-Encoder) Retrieval

| Retrieval Dimension | Sparse Lexical Retrieval (BM25) | Dense Vector Retrieval (Bi-Encoder) |
| :--- | :--- | :--- |
| **Matching Paradigm** | Exact term overlap & inverse document frequency. | High-dimensional geometric latent proximity. |
| **Strengths** | Flawless on exact keywords, product IDs, UUIDs, code syntax. | Synonyms, paraphrasing, multilingual, conceptual queries. |
| **Weaknesses** | Vocabulary mismatch problem (`"heart attack"` misses `"myocardial infarction"`). | Blind to rare tokens, exact serial numbers, and negation (`"not cold"` $\approx$ `"cold"`). |
| **Inference Cost** | Microsecond inverted index scans ($\mathcal{O}(\text{postings})$). | Vector distance computations ($\mathcal{O}(\log N)$ HNSW). |

### 2.1 The BM25 Mathematical Formulation
The Okapi BM25 algorithm scores document $D$ for query $Q = \{q_1, \dots, q_n\}$:

$$\text{Score}_{\text{BM25}}(D, Q) = \sum_{i=1}^{n} \text{IDF}(q_i) \cdot \frac{f(q_i, D) \cdot (k_1 + 1)}{f(q_i, D) + k_1 \cdot \left(1 - b + b \cdot \frac{|D|}{\text{avgdl}}\right)}$$

Where:
- $\text{IDF}(q_i) = \ln\left(1 + \frac{N - n(q_i) + 0.5}{n(q_i) + 0.5}\right)$: Penalizes ubiquitous stopwords.
- $f(q_i, D)$: Raw term frequency of $q_i$ in document $D$.
- $|D| / \text{avgdl}$: Ratio of document length to average corpus length.
- $k_1 \approx 1.2 - 2.0$: Term frequency saturation parameter.
- $b \approx 0.75$: Document length normalization penalty.

---

## 3. Score Fusion: RRF vs. Linear Weighted Sum

When combining disparate retrieval streams (BM25 raw scores $\in [0, 40+]$ vs. Cosine Similarity $\in [-1, 1]$), raw scores cannot be directly added without distortion.

### 3.1 Reciprocal Rank Fusion (RRF)
RRF discards incompatible score scales entirely, operating purely on **ordinal ranks**:

$$\text{RRF\_Score}(d) = \sum_{m \in M} \frac{1}{k_{\text{rrf}} + r_m(d)}$$

Where $k_{\text{rrf}} = 60$ (smoothing constant) and $r_m(d)$ is the 1-based rank of document $d$ in retrieval system $m$.
- Highly robust to score distribution shifts.
- No normalization or calibration required.

### 3.2 Alpha-Weighted Linear Combination
$$\text{Score}_{\text{hybrid}}(d) = \alpha \cdot \hat{S}_{\text{dense}}(d) + (1 - \alpha) \cdot \hat{S}_{\text{sparse}}(d)$$
Requires explicit Min-Max normalization ($\hat{S} = \frac{S - S_{\min}}{S_{\max} - S_{\min}}$). Typically $\alpha \approx 0.6 - 0.7$ (favoring dense embeddings while using BM25 as an exact-match safety net).

---

## 4. Two-Stage Retrieval: Bi-Encoders vs. Cross-Encoders

```
Bi-Encoder (Retriever):
Query    ──> [ Transformer ] ──> Vector q \
                                            ├──> Dot Product (Fast: O(1))
Document ──> [ Transformer ] ──> Vector d /

Cross-Encoder (Reranker):
[CLS] Query [SEP] Document [SEP] ──> [ Transformer (Full Joint Attention) ] ──> Relevance Score
```

1. **Bi-Encoders (Stage 1)**: Embed Query and Document independently. No cross-attention between query tokens and document tokens. Fast enough to scan millions of documents in milliseconds.
2. **Cross-Encoders (Stage 2)**: Concatenates query and document into a single sequence, evaluating every query token against every document token across all Transformer self-attention heads. Deeply understands sentence structure, qualifier clauses, and negation. Computationally expensive ($\mathcal{O}(M)$ forward passes), making it suitable only for reranking the Top 30–100 candidates.
