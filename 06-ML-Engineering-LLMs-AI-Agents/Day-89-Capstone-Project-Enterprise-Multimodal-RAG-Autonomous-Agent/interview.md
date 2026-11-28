# Day 89: Principal AI Systems Interview Questions — Capstone Multimodal RAG & AI Agent Backend

### Q1: Walk through the end-to-end architecture for an enterprise ingestion pipeline processing 100,000 multimodal PDF documents daily.
**Answer**:
- **Distributed Ingestion Queue**: Raw PDF uploads are stored in S3/Ceph object storage. Upload events trigger Celery/Temporal distributed workers running on a GPU Kubernetes cluster.
- **Document Layout Decomposition**:
  1. *Optical Character & Layout Detection*: Uses layout models (e.g. LayoutLMv3 or Marker) to extract text blocks, tables, and bounding boxes.
  2. *Table Extraction*: Financial/data tables are converted into structured Markdown and JSON representations, accompanied by a generated 2-sentence summary.
  3. *Chart/Visual Processing*: Charts, graphs, and architecture diagrams are cropped and embedded via a cross-modal embedding model (e.g., CLIP / Gemini Multimodal).
- **Hybrid Indexing Storage**:
  - Text chunks + Table summaries are embedded into dense vectors and indexed in PostgreSQL 16 using `pgvector` HNSW indexes.
  - Sparse lexical tokens are indexed in an inverted BM25 index.
  - Chunk metadata (document ID, page number, bounding box, confidentiality classification) is indexed via B-Tree indices for rapid pre-filtering.

---

### Q2: Why is Reciprocal Rank Fusion (RRF) mathematically superior to weighted convex score blending ($w \cdot s_{\text{dense}} + (1-w) \cdot s_{\text{sparse}}$)?
**Answer**:
- **Score Scale Incompatibility**: Dense cosine similarity scores are bounded in $[-1.0, 1.0]$ or $[0.0, 1.0]$. Sparse BM25 scores are unbounded $[0.0, \infty)$ and scale with document length and term frequency. Blending them linearly requires complex min-max score calibration that shifts across queries and corpus updates.
- **The RRF Solution**: Reciprocal Rank Fusion operates exclusively on **ordinal ranks**, ignoring raw scores completely:
  $$RRF(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$
  where $k=60$ dampens the impact of extreme top ranks. Documents that rank consistently well across both sparse and dense modalities naturally rise to the top without fragile threshold tuning.

---

### Q3: How do you architect a secure, zero-trust sandbox for an Analytics / Code Interpreter Agent?
**Answer**:
- **The Threat**: An LLM agent generating arbitrary Python code or SQL queries can accidentally or maliciously delete databases, exfiltrate environment variables, or spawn reverse shells.
- **Multi-Layer Isolation Defense**:
  1. *Network Isolation*: The execution container runs with `--network=none` or eBPF/Cilium egress policies blocking all external internet connections.
  2. *Ephemeral MicroVMs / WASM*: Each execution runs in a short-lived, ephemeral Firecracker MicroVM or gVisor container with strict cgroup limits (512MB RAM, 1 vCPU, 5-second timeout).
  3. *Read-Only Data Access*: Queries execute against dedicated, read-only database replicas using database credentials with zero `DROP`, `ALTER`, `UPDATE`, or `DELETE` privileges.
  4. *AST Pre-Validation*: The code string is parsed by `ast.parse()` to disallow `import os`, `import subprocess`, `eval()`, or `__import__`.

---

### Q4: How do you prevent hallucination cascades across multi-agent supervisor-worker graphs?
**Answer**:
- **The Cascade Failure**: If Worker Agent 1 (Retriever) returns a partially incorrect chunk, Worker Agent 2 (Reasoning) builds a logical deduction on top of the error, and Worker Agent 3 (Analytics) computes figures based on the flawed premise, delivering a confident, completely fabricated answer.
- **Mitigation Architecture**:
  1. *Schema Contracts*: Communication between sub-agents must adhere to strict Pydantic models carrying explicit provenance and evidence confidence scores.
  2. *Verification Checkpoints*: The Supervisor does not blindly forward worker claims. A dedicated **Fact-Checking Guardrail Node** evaluates the draft against original retrieved chunks (NLI entailment scoring) before response synthesis.
  3. *Evidence Pruning*: Worker outputs with low retrieval confidence are stripped from the shared state scratchpad.

---

### Q5: How do you achieve sub-500ms P95 latency for a complex multimodal RAG agent in production?
**Answer**:
- **Tier 1 Semantic Response Caching**: Serve 40%–60% of recurring enterprise queries from Redis in $<2\text{ms}$ using exact hash matching and high-threshold ($\tau \ge 0.90$) vector similarity.
- **Asynchronous Speculative Execution**: As soon as the user query arrives, the gateway kicks off vector retrieval and intent routing in parallel rather than sequentially.
- **Quantized Local Rerankers**: Use INT8/ONNX-quantized cross-encoders (e.g. `bge-reranker-base` running on TensorRT/Triton) completing Stage 2 reranking in $<15\text{ms}$.
- **Streaming Response Architecture**: Stream the initial LLM synthesis tokens immediately via Server-Sent Events (SSE) while background tasks record telemetry and audit trails asynchronously.
