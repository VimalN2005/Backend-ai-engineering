# Day 82: Rapid Revision & Active Recall Cheat Sheet

## 1. High-Yield Architectural Summary (5-Minute Scan)

- **Vector Embeddings**:
  - Dense continuous vectors in $\mathbb{R}^d$ ($d \in \{384, 768, 1536, 3072\}$).
  - Captures conceptual semantic meaning vs sparse lexical keyword matching (BM25).
- **Geometric Normalization**:
  - Unit $L_2$ Normalization: $\hat{v} = v / \|v\|_2 \implies \|\hat{v}\|_2 = 1.0$.
  - Under unit norm, **Cosine Similarity** equals **Dot Product**: $\cos(\theta) = \hat{u} \cdot \hat{v}$.
  - Enables ultra-fast SIMD Fused Multiply-Add (FMA) instructions, avoiding expensive divisions and square roots.
- **ANN Search Indexing**:
  - **Flat (Exact)**: $\mathcal{O}(N \cdot d)$ linear scan. 100% recall, zero index memory.
  - **IVFFlat**: $\mathcal{O}(N/C \cdot n_{\text{probe}} \cdot d)$. Partitions space into Voronoi cells via $k$-means. Low index overhead, but vulnerable to centroid drift.
  - **HNSW (Graph)**: $\mathcal{O}(\log N)$. Multi-layer skip-graph hierarchy. High recall ($98\% - 99.5\%$), sub-5ms query latency, supports real-time concurrent insertions.
- **HNSW Hyperparameters**:
  - $M$: Max bidirectional links per node ($16 - 64$).
  - $efConstruction$: Insertion beam width ($64 - 200$).
  - $efSearch$: Query beam width ($32 - 100$). Directly controls latency vs recall at query time without rebuilding the index.
- **pgvector vs Qdrant**:
  - **pgvector**: Seamless PostgreSQL extension, ACID compliance, standard SQL joins, operators: `<=>` (cosine), `<->` ($L_2$), `<#>` (inner product).
  - **Qdrant**: Purpose-built Rust engine, Single-Stage Filtered HNSW, Scalar Quantization (SQ8: 75% RAM savings) & Binary Quantization (BQ).
- **Filtering Patterns**:
  - **Post-Filtering**: Fails via "Result Set Starvation" on selective filters.
  - **Single-Stage Filtered Search**: Evaluates predicates directly during HNSW graph traversal. Guaranteed Top-$K$ authorized results.

---

## 2. Distance Metrics & Operator Matrix

| Metric Name | Mathematical Formula | Range | pgvector Operator | Optimal Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **Cosine Distance** | $1 - \frac{u \cdot v}{\|u\|_2 \|v\|_2}$ | $[0.0, 2.0]$ | `<=>` | Natural language text embeddings |
| **Euclidean ($L_2$)**| $\sqrt{\sum (u_i - v_i)^2}$ | $[0.0, +\infty)$ | `<->` | Image, audio, spatial features |
| **Negative Inner Product** | $-1 \times (u \cdot v)$ | $(-\infty, +\infty)$ | `<#>` | Recommendation scores, unit vectors |
| **Manhattan ($L_1$)** | $\sum \|u_i - v_i\|$ | $[0.0, +\infty)$ | (Custom) | High-dimensional grid spaces |

---

## 3. 10 Active Recall Flashcards

### Card 1
- **Q**: What is the computational advantage of pre-normalizing vectors to unit $L_2$ norm?
- **A**: Cosine distance reduces to $1.0 - \text{dot\_product}$, eliminating square root and division instructions and enabling hardware SIMD/AVX-512 FMA acceleration.

### Card 2
- **Q**: What causes "Result Set Starvation" in vector search?
- **A**: Post-filtering retrieves the global Top-$K$ vectors first, then discards non-matching metadata. When filter selectivity is high, few or zero matching items remain in the candidate set.

### Card 3
- **Q**: How does HNSW achieve logarithmic $\mathcal{O}(\log N)$ search complexity?
- **A**: It uses a multi-layer skip-graph hierarchy: long-range express hops on sparse upper layers quickly route queries near the target cluster before descending to dense lower layers.

### Card 4
- **Q**: What is the operational purpose of tuning `efSearch` at runtime?
- **A**: `efSearch` sets the priority queue beam width during query execution. Higher `efSearch` increases recall at the cost of higher query latency without requiring index rebuilds.

### Card 5
- **Q**: How much memory compression does Scalar Quantization (SQ8) provide?
- **A**: It compresses 32-bit floats (4 bytes) to 8-bit signed integers (1 byte), achieving a $75\%$ memory reduction ($4\times$ compression) with $< 1\%$ recall loss.

### Card 6
- **Q**: Why is Asymmetric Distance Computation (ADC) preferred in quantized search?
- **A**: The query vector remains in full FP32 precision, while only database vectors are stored in INT8. This avoids dual quantization noise and preserves high recall.

### Card 7
- **Q**: In pgvector, what does the `<=>` operator compute?
- **A**: Cosine distance: $1.0 - \text{cosine\_similarity}(u, v)$.

### Card 8
- **Q**: When would you choose pgvector over a standalone vector database like Qdrant or Milvus?
- **A**: When you already rely on PostgreSQL and need strict ACID transactions, relational foreign key constraints, Row-Level Security, and unified backups without operating an additional database cluster.

### Card 9
- **Q**: What is the primary limitation of IVFFlat indices compared to HNSW?
- **A**: IVFFlat requires offline centroid training and suffers severe recall degradation when data distribution shifts, whereas HNSW handles continuous real-time insertions natively.

### Card 10
- **Q**: What is Reciprocal Rank Fusion (RRF), and why is it used in hybrid search?
- **A**: An algorithm that combines ranked lists from disparate retrieval systems (e.g. BM25 and dense vector search) using $RRF(d) = \sum \frac{1}{60 + \text{rank}(d)}$, eliminating the need to calibrate or normalize incompatible raw score distributions.
