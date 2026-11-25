# Day 82: Principal AI Systems Interview Questions & Answers

## Question 1: Compare HNSW and IVF-PQ indexing architectures for billion-scale vector search. What are their hardware trade-offs regarding RAM consumption, QPS throughput, and insertion mutability?

### Deep Technical Answer
When scaling vector search to hundreds of millions or billions of vectors, engineering teams must choose between graph-based indices (**HNSW**) and inverted quantization indices (**IVF-PQ**):

1. **Memory Footprint (RAM)**:
   - **HNSW**: Stores raw FP32/FP16 vectors plus multi-layer graph link pointers ($M$ bidirectional links per node). For 1B 768-dim FP16 vectors, HNSW requires **$\approx 2.5\text{ TB} - 3.5\text{ TB}$ of RAM**. The entire index must reside in memory; paging to SSD introduces severe latency spikes.
   - **IVF-PQ**: Quantizes vectors into short byte codes (e.g. 64 bytes per vector). The same 1B vectors consume only **$\approx 64\text{ GB} - 128\text{ GB}$ of RAM**, enabling a $20\times$ smaller hardware footprint that fits into a single commodity server.

2. **QPS & Search Latency**:
   - **HNSW**: Delivers superior QPS ($1,000 - 5,000\text{ QPS}$ per node) with ultra-low latency ($< 3\text{ ms}$) and near-perfect recall ($98\% - 99.5\%$).
   - **IVF-PQ**: Incurs higher search latency ($10 - 50\text{ ms}$) and lower recall ($85\% - 95\%$) because it must compute asymmetric distance tables and scan hundreds of inverted lists.

3. **Insertion Mutability & Data Updates**:
   - **HNSW**: Supports dynamic, real-time incremental insertions and updates with zero downtime.
   - **IVF-PQ**: Requires offline $k$-means centroid training and product codebook generation. Adding vectors that deviate from initial centroids requires costly full-index retraining.

**Architectural Recommendation**: Use **HNSW** (with SQ8 or BQ quantization) for latency-critical, frequently updated RAG applications ($< 50\text{M}$ vectors). Use **IVF-PQ** for billion-scale, cost-constrained, relatively static archive retrieval.

---

## Question 2: Explain the "Result Set Starvation" problem in Post-Filtering and how Single-Stage Filtered HNSW graphs solve it.

### Deep Technical Answer
In enterprise applications, vector search is rarely unconstrained; it is bound by security and business predicates (`WHERE tenant_id = 'acme' AND access_level <= 2 AND status = 'active'`).

### The Post-Filtering Failure Mode ("Result Set Starvation")
1. The search engine executes standard vector ANN search on the graph, retrieving the global Top-$K$ candidates (e.g. $K=10$).
2. The application subsequently filters these 10 items against the metadata predicate.
3. If the filter has high selectivity (e.g., tenant `'acme'` owns only $1\%$ of database rows), the probability of finding `'acme'` records in the global top 10 is near zero.
4. The user receives an empty result set ($0$ items) or an incomplete set ($1$ item), even though thousands of matching `'acme'` documents exist in the database.

### Single-Stage Filtered HNSW Solution
Modern vector engines (Qdrant, pgvector 0.5+) solve this by **integrating metadata evaluation directly into the HNSW beam search**:
- During graph traversal, when examining neighbor nodes from candidate priority queues, the engine evaluates the metadata predicate **before** adding the node to the result set.
- If a neighbor does not match the filter, the engine can still traverse through it to explore the graph topology, but only matching nodes are added to the final Top-$K$ queue.
- This guarantees that exactly $K$ valid, authorized documents are returned on every query.

---

## Question 3: How does Scalar Quantization (SQ8) work mathematically, and why does asymmetric distance computation yield superior recall over symmetric quantization?

### Deep Technical Answer
Scalar Quantization (SQ8) compresses continuous 32-bit floating-point numbers into 8-bit signed integers ($[-128, 127]$):

### Mathematical Formulation
Given a dimension value $v_i \in [\text{min}, \text{max}]$:
$$\text{scale} = \frac{\text{max} - \text{min}}{255.0}$$
$$q_i = \text{round}\left(\frac{v_i - \text{min}}{\text{scale}}\right) - 128 \implies q_i \in \text{int8}$$

Dequantization reconstructs the floating-point approximation:
$$\tilde{v}_i = (q_i + 128) \times \text{scale} + \text{min}$$

### Symmetric vs. Asymmetric Distance Computation (ADC)
- **Symmetric Distance**: Both the query vector and database vectors are quantized to `int8`. Distance is computed between two quantized approximations ($\tilde{q} \cdot \tilde{v}$). This introduces **dual quantization error** ($E_{\text{total}} = \epsilon_q + \epsilon_v$), degrading recall below $85\%$.
- **Asymmetric Distance Computation (ADC)**: The online query vector remains in pristine **FP32** ($q$). Distance is computed directly between the exact query and the dequantized database vector ($q \cdot \tilde{v}$).
- **Why ADC is Superior**: The query vector is evaluated only once; keeping it in full float32 eliminates query quantization noise, achieving $> 98\%$ recall while retaining $75\%$ RAM savings for the stored database vectors.

---

## Question 4: How do you design and tune a multi-tenant vector database using PostgreSQL with pgvector?

### Deep Technical Answer
Multi-tenant vector search requires balancing strict data isolation, indexing overhead, and connection concurrency:

### 1. Data Modeling & Row-Level Security (RLS)
Co-locate vectors with relational metadata in a single table, enforcing PostgreSQL Row-Level Security:
```sql
CREATE TABLE document_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL,
    metadata JSONB NOT NULL,
    embedding vector(1536) NOT NULL
);

ALTER TABLE document_embeddings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_embeddings
FOR ALL USING (tenant_id = current_setting('app.current_tenant'));
```

### 2. Index Strategy
- **Shared Global HNSW Index**: If individual tenants have $< 100\text{k}$ rows each, build a global HNSW index. `pgvector` will evaluate the RLS filter during graph traversal.
- **Partitioned HNSW Indices**: For enterprise tenants with $> 500\text{k}$ vectors, use **declarative table partitioning** by `tenant_id`. Each partition maintains an independent, compact HNSW index, preventing cross-tenant index cache pollution.

### 3. Query & Connection Tuning
- Vector operations are memory-bandwidth and CPU bound. Configure PgBouncer in **transaction pooling mode** to keep active database connections bounded to $2 \times \text{CPU cores}$.
- Tune query-time parameters:
  ```sql
  SET hnsw.ef_search = 64;  -- Balances 98% recall with sub-5ms query latency
  ```

---

## Question 5: Why are Cosine Distance and Inner Product mathematically equivalent under $L_2$ normalization, and how does hardware SIMD/AVX-512 exploit this for acceleration?

### Deep Technical Answer
Cosine similarity between vectors $u$ and $v$ is defined as:

$$\cos(\theta) = \frac{u \cdot v}{\|u\|_2 \|v\|_2} = \frac{\sum u_i v_i}{\sqrt{\sum u_i^2} \sqrt{\sum v_i^2}}$$

### Equivalence Under $L_2$ Normalization
When vectors are unit-normalized ($L_2 = 1.0$) upon ingestion:
$$\|u\|_2 = 1.0 \quad \text{and} \quad \|v\|_2 = 1.0$$
$$\implies \cos(\theta) = \frac{u \cdot v}{1.0 \times 1.0} = u \cdot v = \sum_{i=1}^{d} u_i v_i$$
$$\text{Cosine Distance} = 1.0 - \cos(\theta) = 1.0 - \langle u, v \rangle$$

### Hardware SIMD / AVX-512 Acceleration
Computing true Cosine Distance on unnormalized vectors requires two norms (two square roots, $2d$ additions, and a floating-point division per vector comparison). In modern CPUs:
- A division instruction (`FDIV`) takes **$12 - 20$ CPU cycles**, and square root (`FSQRT`) takes **$15 - 30$ cycles**.
- In contrast, normalized Dot Product requires only **Fused Multiply-Add (FMA)** operations:
  $$c_i = c_i + (a_i \times b_i)$$
- Using **AVX-512** (512-bit vector registers), modern x86 CPUs compute **16 single-precision (FP32) multiply-accumulate operations in a single clock cycle**!
- Pre-normalizing vectors eliminates all divisions and square roots, transforming vector search into pure SIMD matrix-vector multiplication and boosting distance evaluation throughput by **$300\% - 500\%$**.
