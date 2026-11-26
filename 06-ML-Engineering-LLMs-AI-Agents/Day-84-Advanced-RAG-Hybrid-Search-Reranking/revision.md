# Day 84: Rapid Revision & Active Recall Cheat Sheet

## 1. High-Yield Architectural Summary (5-Minute Scan)

- **The Limitation of Naive Vector Search**:
  - Dense embeddings fail on exact alphanumeric codes (`ERR_404_TIMEOUT`), part numbers, acronyms, and negation.
- **Two-Stage Retrieval Architecture**:
  - **Stage 1 (High-Recall Hybrid Search)**: Combines sparse BM25 keyword matching with dense HNSW vector search, retrieving Top-$M$ broad candidates ($M \approx 50 - 100$).
  - **Stage 2 (High-Precision Cross-Encoder Reranking)**: Uses full joint cross-attention over `[Query, Document]` pairs to score and sort candidates down to pristine Top-$K$ ($K \approx 3 - 5$).
- **BM25 Mathematical Essentials**:
  - $\text{IDF}(q_i) = \ln\left(1 + \frac{N - n + 0.5}{n + 0.5}\right)$ (penalizes common stopwords).
  - $k_1 \approx 1.5$: Term frequency saturation parameter (diminishing returns on repeated words).
  - $b \approx 0.75$: Document length normalization penalty.
- **Score Fusion Strategies**:
  - **Reciprocal Rank Fusion (RRF)**:
    $$\text{RRF}(d) = \sum_{m \in M} \frac{1}{60 + \text{rank}_m(d)}$$
    Purely ordinal, zero normalization required, immune to score scale mismatches.
  - **Linear Weighted Sum**: $\alpha \hat{S}_{\text{dense}} + (1 - \alpha) \hat{S}_{\text{sparse}}$ (requires min-max normalization, vulnerable to outliers).
- **Bi-Encoder vs Cross-Encoder**:
  - **Bi-Encoder**: Encodes Query and Document independently. Precomputable, $\mathcal{O}(\log N)$ vector search. Zero cross-attention.
  - **Cross-Encoder**: Full joint self-attention. Highly accurate, but cannot be precomputed ($\mathcal{O}(M)$ forward passes per query).

---

## 2. Core Mathematical Formulations

| Concept | Mathematical Equation | Operational Role |
| :--- | :--- | :--- |
| **Reciprocal Rank Fusion** | $\sum_{m \in M} \frac{1}{60 + r_m(d)}$ | Merges disparate retrieval score scales into robust ordinal ranking. |
| **BM25 Term Saturation** | $\frac{f \cdot (k_1 + 1)}{f + k_1 \cdot \text{len\_norm}}$ | Prevents keyword stuffing from skewing document relevance. |
| **Mean Reciprocal Rank** | $\frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{\text{rank}_i}$ | Standard metric evaluating how early the first relevant hit appears. |
| **Linear Hybrid Score** | $\alpha \hat{S}_{\text{dense}} + (1 - \alpha) \hat{S}_{\text{sparse}}$ | Weighted hybrid combination ($\alpha \approx 0.65$). |

---

## 3. 10 Active Recall Flashcards

### Card 1
- **Q**: Why does dense vector search fail on exact error codes like `ERR_HTTP2_SESSION_TIMEOUT`?
- **A**: BPE tokenizers fragment rare alphanumeric identifiers into arbitrary subwords, and continuous embedding projections place them near generic alphanumeric clusters rather than matching the exact identifier.

### Card 2
- **Q**: What are the two stages in a Two-Stage Retrieval pipeline?
- **A**: Stage 1: Fast, broad candidate retrieval using Hybrid BM25 + Vector search (Top 50–100). Stage 2: Deep Cross-Encoder reranking using joint cross-attention (Top 3–5).

### Card 3
- **Q**: Why can't Cross-Encoders be used for initial search across 10 million documents?
- **A**: Cross-encoders compute joint cross-attention over `[Query, Document]` pairs and cannot be pre-computed offline; scanning 10M documents would require 10M neural network forward passes per query.

### Card 4
- **Q**: How does Reciprocal Rank Fusion (RRF) combine scores from BM25 and vector search?
- **A**: It ignores raw score scales entirely and assigns scores based on ordinal ranks: $RRF(d) = \sum \frac{1}{60 + \text{rank}(d)}$.

### Card 5
- **Q**: In Okapi BM25, what is the role of parameter $k_1$?
- **A**: It controls term frequency saturation; as term frequency increases, the score asymptotically caps at $k_1 + 1$, preventing keyword stuffing from dominating.

### Card 6
- **Q**: What does parameter $b$ do in the BM25 formula?
- **A**: It controls document length normalization ($b \approx 0.75$), penalizing long, wordy documents while rewarding concise, information-dense documents.

### Card 7
- **Q**: What is the primary difference in attention architecture between Bi-Encoders and Cross-Encoders?
- **A**: Bi-encoders embed Query and Document in separate forward passes with zero cross-attention; Cross-encoders concatenate Query and Document, allowing every query token to attend to every document token.

### Card 8
- **Q**: Why is linear score combination ($\alpha \cdot S_{\text{dense}} + (1 - \alpha) \cdot S_{\text{sparse}}$) fragile compared to RRF?
- **A**: It requires Min-Max normalization, which is easily distorted by a single high-score outlier, compressing all other scores and suppressing the sparse signal.

### Card 9
- **Q**: What is Metadata Self-Querying?
- **A**: Using an LLM to parse an unstructured user query into a clean semantic search string and structured filter predicates (e.g. `service: "auth"`).

### Card 10
- **Q**: What metric evaluates the position of the first relevant document in a search result list?
- **A**: Mean Reciprocal Rank (MRR): $\frac{1}{|Q|} \sum \frac{1}{\text{rank}_i}$.
