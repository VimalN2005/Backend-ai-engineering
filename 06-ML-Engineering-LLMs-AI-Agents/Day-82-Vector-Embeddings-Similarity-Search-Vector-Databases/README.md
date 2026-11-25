# Day 82: Vector Embeddings, Similarity Search & Vector Databases (pgvector, Qdrant)

In modern generative AI and retrieval-augmented generation (RAG) architectures, unstructured enterprise data—such as documents, customer interactions, source code, and audio transcripts—cannot be effectively queried using traditional lexical keyword matches or relational B-tree lookups. Instead, text is transformed into continuous, dense **vector embeddings** that map semantic meaning into high-dimensional geometric spaces.

This guide explores the mathematical foundations of vector spaces, distance metrics, approximate nearest neighbor (ANN) graph indexing algorithms, and production database engines: **pgvector** and **Qdrant**.

---

## 1. Executive Architecture Overview

```
+--------------------------------------------------------------------------------------------------+
|                           PRODUCTION VECTOR SEARCH & RETRIEVAL PIPELINE                          |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|  [ Ingestion Pipeline ]                                                                          |
|         │                                                                                        |
|         │ 1. Raw Text Chunks (e.g., 512 tokens with 10% overlap)                                 |
|         ▼                                                                                        |
|  [ Embedding Model ] (e.g., text-embedding-3-small, bge-large-en, all-MiniLM-L6-v2)             |
|         │                                                                                        |
|         │ 2. Dense Float32 Vectors: u in R^d (d in {384, 768, 1536, 3072})                       |
|         │ 3. Geometric L2 Normalization: u_norm = u / ||u||_2                                     |
|         ▼                                                                                        |
|  [ Vector Database Storage Tier ]                                                                |
|         ├─ Hybrid Storage: ACID Metadata (PostgreSQL) + Vector Store (pgvector / Qdrant)         |
|         └─ ANN Graph Index: Hierarchical Navigable Small World (HNSW) Multi-Layer Skip-List      |
|                                                                                                  |
|  [ Online Query Pipeline ]                                                                       |
|         │                                                                                        |
|         │ 4. User Prompt Query: "How do I configure distributed rate limiting?"                  |
|         ▼                                                                                        |
|  [ Query Embedding & Normalization ] ──> q in R^d                                                |
|         │                                                                                        |
|         │ 5. Filtered ANN Search: Single-Stage Graph Traversal with Predicate Pushdown           |
|         │    WHERE tenant_id = 'org_42' AND access_level <= 3                                   |
|         │    ORDER BY embedding <=> q LIMIT 5                                                    |
|         ▼                                                                                        |
|  [ Top-K Ranked Context Chunks ] ──> Injected into LLM Context Window                            |
+--------------------------------------------------------------------------------------------------+
```

---

## 2. Mathematical Foundations of Vector Embeddings

An embedding model $f_{\theta}: \mathcal{T} \to \mathbb{R}^d$ maps variable-length text strings $\mathcal{T}$ into a fixed $d$-dimensional continuous vector space.

### 2.1 Dense vs. Sparse Vector Representations

| Metric | Sparse Representations (BM25, TF-IDF, SPLADE) | Dense Representations (Transformer Embeddings) |
| :--- | :--- | :--- |
| **Dimensionality** | Massive ($V \approx 30,000 - 100,000+$) | Compact ($d \approx 384 - 3,072$) |
| **Zero Elements** | $> 99.5\%$ zeros (sparse bag-of-words) | $0\%$ zeros (every dimension holds continuous signal) |
| **Semantic Matching** | Exact lexical keywords only (`"automobile"` misses `"car"`) | Conceptual & semantic similarity (`"automobile"` $\approx$ `"car"`) |
| **Storage Engine** | Inverted Index (Lucene, Elasticsearch) | Vector Index (pgvector, Qdrant, Milvus, Faiss) |

### 2.2 Geometric $L_2$ Normalization
For a vector $v = [v_1, v_2, \dots, v_d] \in \mathbb{R}^d$, its Euclidean norm ($L_2$ norm) is defined as:

$$\|v\|_2 = \sqrt{\sum_{i=1}^{d} v_i^2}$$

Normalizing a vector scales it to unit length ($1.0$) on the $d$-dimensional hypersphere $\mathbb{S}^{d-1}$:

$$\hat{v} = \frac{v}{\|v\|_2} \implies \|\hat{v}\|_2 = 1.0$$

> [!TIP]
> When all vectors are $L_2$-normalized upfront during ingestion, **Cosine Similarity** reduces computationally to a simple **Inner Product (Dot Product)**:
> $$\text{Cosine Similarity}(u, v) = \frac{u \cdot v}{\|u\|_2 \|v\|_2} = \hat{u} \cdot \hat{v} = \sum_{i=1}^{d} \hat{u}_i \hat{v}_i$$
> Computing a raw dot product eliminates expensive square root and division operations during online query execution, doubling search throughput.

---

## 3. Distance Metrics & Similarity Functions

Vector databases evaluate semantic proximity using four standard metric formulations:

```
Distance Function Metrics
├── Cosine Distance:   D_cos(u, v) = 1 - (u · v) / (||u||_2 * ||v||_2)  [Scale: 0.0 to 2.0]
├── Euclidean (L2):    D_euc(u, v) = sqrt( sum( (u_i - v_i)^2 ) )        [Scale: 0.0 to +inf]
├── Dot Product (IP):  S_ip(u, v)  = sum( u_i * v_i )                    [Scale: -inf to +inf]
└── Manhattan (L1):    D_man(u, v) = sum( |u_i - v_i| )                  [Scale: 0.0 to +inf]
```

### 3.1 Metric Characteristics & Selection Criteria

1. **Cosine Distance**:
   - Invariant to vector magnitude; focuses strictly on angular orientation.
   - Ideal for natural language text embeddings where text length or token density varies.
2. **Euclidean Distance ($L_2$)**:
   - Sensitive to absolute magnitude. Two identical sentences with different vector scales will yield non-zero Euclidean distance.
   - Ideal for image embeddings, physical coordinates, and audio feature vectors.
3. **Inner Product (Dot Product)**:
   - Heavily rewards large magnitudes along matching dimensions.
   - Ideal for recommendation systems (collaborative filtering user-item interactions) or when embeddings are strictly pre-normalized.

---

## 4. Exact KNN vs. Approximate Nearest Neighbors (ANN)

Given $N$ stored vectors in dimension $d$, finding the $K$ closest vectors to a query vector $q$ can be approached via exact or approximate methods:

| Algorithmic Paradigm | Index Type | Query Complexity | Recall Rate | Memory Overhead | Build Time |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Exact KNN (Brute-Force)**| Flat / None | $\mathcal{O}(N \cdot d)$ | $100\%$ | $0\%$ (Raw vectors only) | Instant ($0$ ms) |
| **Inverted File (IVF)** | `ivfflat` | $\mathcal{O}\left(\frac{N}{C} \cdot n_{\text{probe}} \cdot d\right)$ | $85\% - 98\%$ | Low (Centroids list) | Fast ($k$-means clustering) |
| **Graph-Based (HNSW)** | `hnsw` | $\mathcal{O}(\log N)$ | $95\% - 99.9\%$ | Medium ($20\% - 50\%$ overhead) | Moderate (Link construction) |
| **Quantized (PQ / SQ)**| `ivfpq` / `sq8` | $\mathcal{O}\left(\frac{N}{C} \cdot n_{\text{probe}} \cdot d_{\text{code}}\right)$ | $80\% - 95\%$ | Very Low ($75\%$ compression) | High (Codebook training) |

---

## 5. Hierarchical Navigable Small World (HNSW) Architecture

HNSW (Malkov & Yashunin, 2018) is the industry standard for low-latency, high-recall vector search. It generalizes the probabilistic **Skip-List** data structure into a multi-layer graph:

```
[Layer 3 - Express Sparsity]     (Node A) -------------------------> (Node Z)
                                    │                                   │
[Layer 2 - Intermediate]         (Node A) ---------> (Node M) ------> (Node Z)
                                    │                   │               │
[Layer 1 - Dense Routing]        (Node A) -> (Node D) -> (Node M) -> (Node P) -> (Node Z)
                                    │           │       │           │       │
[Layer 0 - Complete Delaunay]    (A)─(B)─(C)─(D)─(E)─(F)─(M)─(N)─(O)─(P)─(Q)─(Z)
```

### 5.1 HNSW Algorithmic Principles
1. **Multi-Layer Hierarchy**:
   - The bottom layer (Layer 0) contains every vector in the database linked as a Delaunay-like proximity graph.
   - Upper layers contain exponentially fewer vectors (decay parameter $m_L = 1 / \ln(M)$).
2. **Greedy Skip-Routing**:
   - Search begins at the top layer with long-range "express" hops across distant clusters.
   - Upon finding the local minimum on Layer $l$, the algorithm drops down to Layer $l-1$, using the previous local minimum as the entry point.
   - Achieves logarithmic $\mathcal{O}(\log N)$ search complexity.
3. **Core HNSW Hyperparameters**:
   - **$M$ (Max Links per Node)**: Typically between $16$ and $64$. Higher $M$ improves recall on high-dimensional data at the cost of higher index memory and build time.
   - **$efConstruction$ (Construction Beam Width)**: Number of candidate neighbors evaluated during graph insertion (typically $100 - 200$).
   - **$efSearch$ (Query Beam Width)**: Size of the dynamic candidate priority queue maintained during runtime search (typically $40 - 100$). Increasing $efSearch$ directly tunes the recall vs. query latency trade-off without rebuilding the index.

---

## 6. Production Database 1: pgvector (PostgreSQL Extension)

`pgvector` transforms standard relational PostgreSQL instances into high-performance vector databases, providing ACID guarantees, standard SQL query planners, and seamless metadata joins.

### 6.1 Data Types & Distance Operators

```sql
-- Enable extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Define table with 1536-dimensional embeddings (e.g., OpenAI text-embedding-3-small)
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Distance Operators:
-- '<=>' : Cosine Distance     (1 - Cosine Similarity)
-- '<->' : Euclidean Distance  (L2 Distance)
-- '<#>' : Negative Dot Product (-1 * Inner Product)
```

### 6.2 Index Tuning: IVF vs. HNSW in pgvector

```sql
-- 1. HNSW Index (Recommended for production: superior recall & low latency)
CREATE INDEX idx_docs_hnsw ON documents 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 128);

-- Query-time parameter tuning:
SET hnsw.ef_search = 64;

-- 2. IVFFlat Index (Alternative for high-write, low-memory environments)
CREATE INDEX idx_docs_ivfflat ON documents 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 1000);  -- Rule of thumb: lists ≈ sqrt(total_rows) or total_rows / 1000

SET ivfflat.probes = 10;
```

---

## 7. Production Database 2: Qdrant Architecture

`Qdrant` is a purpose-built, cloud-native vector database written in Rust. It excels at complex payload-filtered vector search and billion-scale quantization.

### 7.1 Architectural Strengths of Qdrant
1. **Payload Storage & Indexing**: Metadata payloads are co-located with vectors in storage segments. Payload fields (e.g., `tenant_id`, `created_at`) can have their own inverted or numeric indexes.
2. **Single-Stage Filtered HNSW**: Unlike systems that filter before or after graph traversal, Qdrant traverses the HNSW graph while dynamically pruning nodes that fail payload filter predicates.
3. **Quantization Engine**:
   - **Scalar Quantization (SQ8)**: Compresses float32 (4 bytes) to int8 (1 byte), achieving $4\times$ memory reduction with $< 1\%$ recall loss.
   - **Binary Quantization (BQ)**: Quantizes continuous vectors to 1-bit binary representations, enabling ultra-fast Hamming distance operations via hardware SIMD instructions.

---

## 8. Hybrid Search Engineering: Pre-filtering vs. Post-filtering

When querying vectors with metadata constraints (e.g., `tenant_id = 'acme'` and `status = 'active'`), naive systems suffer severe failure modes:

```
+--------------------------------------------------------------------------------------------------+
|                                    FILTERING ARCHITECTURES                                       |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
| 1. POST-FILTERING (Naive Antipattern):                                                           |
|    Vector Index (Top 100) ──> Apply Filter (WHERE tenant = 'acme') ──> Result: 0 or 2 items!     |
|    * Fails due to "Result Set Starvation": matching items fell outside the top-100 vector score! |
|                                                                                                  |
| 2. PRE-FILTERING (Relational-First):                                                             |
|    Apply Filter ──> 50,000 matching IDs ──> Brute-force KNN across matching IDs                 |
|    * Graph index cannot be used; collapses to expensive unindexed scans.                         |
|                                                                                                  |
| 3. SINGLE-STAGE FILTERED GRAPH TRAVERSAL (pgvector HNSW / Qdrant):                                |
|    HNSW Graph Traversal ──[ Predicate Evaluated at Each Visited Node ]──> Guaranteed Top-K Valid |
|    * Traverses graph edges while strictly respecting filter criteria during beam search.        |
+--------------------------------------------------------------------------------------------------+
```
