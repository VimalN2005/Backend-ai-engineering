# Day 89: Rapid Revision & Flashcards — Capstone Multimodal RAG & AI Agents

## 5-Minute Core Revision Cheat Sheet

### Multimodal RAG Ingestion Pipeline
- **Document Decomposition**: Complex PDFs split into Text (recursive chunks with overlap), Tables (Markdown/JSON matrix + summary), and Charts (cross-modal image embeddings).
- **Hybrid Retrieval**: Combines sparse lexical BM25 (exact keyword match) with dense vector search (semantic similarity) using Reciprocal Rank Fusion ($RRF = \sum \frac{1}{60 + r_m(d)}$).
- **Two-Tier Semantic Caching**: Tier 1 (Exact Hash SHA-256 in Redis, $<0.1\text{ms}$) + Tier 2 (Vector cosine similarity $\ge 0.90$, $<2\text{ms}$).

### Autonomous Multi-Agent Topology
- **Supervisor-Worker Pattern**: Supervisor Agent decomposes queries and orchestrates specialized workers:
  - *Retrieval Specialist*: Multimodal vector querying and citation linking.
  - *Analytics Specialist*: Deterministic math and code execution in a secure sandbox.
  - *Fact-Checking Guardrail*: Grounds draft claims against retrieved evidence chunks before final response delivery.

---

## 10 Active Recall Flashcards

1. **Q: Why should complex PDF tables be stored as Markdown or JSON rather than raw text?**
   *A*: Raw text flattens row and column alignments, causing the model to misassociate headers with unrelated cells. Structured tables preserve exact data cell relationships.

2. **Q: What is Reciprocal Rank Fusion (RRF)?**
   *A*: An algorithm that blends multiple ranked candidate lists (e.g. BM25 and Dense vectors) based purely on ordinal rank positions without requiring score normalization.

3. **Q: Why should an agent never perform arithmetic calculations directly in LLM generation tokens?**
   *A*: LLMs are probabilistic token predictors prone to hallucinating arithmetic. Math must be delegated to a deterministic Python/SQL code execution sandbox.

4. **Q: What is Cross-Modal Embedding?**
   *A*: Projecting multimodal content (images, charts, text) into a single unified high-dimensional latent space so text queries can directly retrieve relevant charts.

5. **Q: How does a Fact-Checking Guardrail agent prevent hallucination cascades?**
   *A*: It checks every numerical figure and factual claim in the supervisor's draft against the raw retrieved evidence before releasing the response to the user.

6. **Q: What security controls are mandatory for an Analytics Code Interpreter sandbox?**
   *A*: Zero egress network access (`--network=none`), ephemeral microVMs/WASM, read-only DB replicas, and AST syntax tree filtering to disallow dangerous system calls.

7. **Q: How does two-tier caching optimize multimodal RAG performance?**
   *A*: It serves 40%–60% of recurring queries in sub-2ms time via Redis exact hash and vector similarity, bypassing expensive vector searches and multi-agent LLM reasoning turns.

8. **Q: What is Citation Provenance in enterprise RAG?**
   *A*: Linking every generated statement to its exact source document ID, page number, and bounding box coordinates for regulatory compliance and auditability.

9. **Q: What is the benefit of the Supervisor-Worker agent architecture over a monolithic agent?**
   *A*: It isolates tools to specialized domain sub-agents (reducing context dilution and tool hallucinations) and enables parallel execution of sub-tasks.

10. **Q: What is the standard value of constant $k$ in Reciprocal Rank Fusion, and why?**
    *A*: $k=60$; it provides an empirical balance that prevents an outlier rank 1 item from dominating while rewarding documents that appear in the top 10 across both retrievers.
