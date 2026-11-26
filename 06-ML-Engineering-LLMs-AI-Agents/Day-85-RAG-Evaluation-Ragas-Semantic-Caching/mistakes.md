# Day 85: RAG Evaluation & Semantic Caching — 7 Critical Enterprise Antipatterns

Evaluating LLM retrieval pipelines and implementing semantic response caching introduce subtle, severe production failure modes. Below are 7 critical antipatterns and production-tested remediations.

---

### 1. Relying Exclusively on Surface n-gram Metrics (BLEU / ROUGE)
- **The Mistake**: Using BLEU, ROUGE, or METEOR to measure RAG quality. A model generating *"The server did not restart after the crash"* vs ground truth *"The server restarted after the crash"* scores over 85% on ROUGE-L despite conveying the exact opposite factual meaning.
- **The Damage**: High benchmark scores hide catastrophic factual errors and inversions in critical production domains (finance, medicine, infra).
- **The Fix**: Evaluate factual claims using claim-decomposition and groundedness checks (Faithfulness). Measure semantic similarity via cross-encoders or embeddings, not literal n-gram overlaps.

---

### 2. Overly Permissive Semantic Cache Cosine Threshold ($\tau < 0.88$)
- **The Mistake**: Setting the vector similarity cutoff too low (e.g., 0.80) to artificially boost the cache hit rate.
- **The Damage**: Semantic drift. The cache returns answers for "How do I reset password in staging?" to users asking "How do I reset password in production?", or answers to "How to delete an account?" for "How to delete an organization?".
- **The Fix**: Enforce strict similarity thresholds ($\tau \ge 0.90$ for production embeddings like `text-embedding-3-large`). Pair vector similarity with mandatory structured metadata filtering (environment, tenant ID, user role).

---

### 3. LLM-as-a-Judge Position and Verbosity Biases
- **The Mistake**: Asking an evaluation LLM to compare two answers without mitigating known heuristic biases. LLMs exhibit strong **position bias** (favoring Candidate A over Candidate B regardless of quality) and **verbosity bias** (favoring lengthy, verbose answers over concise, correct answers).
- **The Damage**: Evaluation benchmarks systematically overvalue bloated, hallucinated responses and penalize clean, direct answers.
- **The Fix**:
  1. Position Swapping: Evaluate candidate pairs twice, swapping `[Answer A, Answer B]` positions, and average the score.
  2. Length-Normalized Prompting: Explicitly instruct the judge model to penalize irrelevant fluff and reward brevity.
  3. Structured Output: Force the judge to output JSON with step-by-step reasoning before outputting the score.

---

### 4. Raw Prompt Caching Without Normalization & Canonicalization
- **The Mistake**: Computing SHA-256 cache keys directly on raw incoming prompt strings containing trailing whitespace, varying casing, and client-specific HTTP headers.
- **The Damage**: Cache fragmentation. `"What is our refund policy?"`, `"what is our refund policy?  "`, and `"What is our refund policy?\n"` produce 3 completely different cache misses, crippling Tier 1 cache hit rates.
- **The Fix**: Implement a canonicalization pipeline before cache hashing: lowercase text, strip leading/trailing whitespace, collapse contiguous spaces, and sort system prompt role tags.

---

### 5. Multi-Tenant Semantic Cache Data Leakage (Cross-Tenant Pollution)
- **The Mistake**: Using a global vector index for semantic caching across all enterprise tenants without tenant isolation.
- **The Damage**: Sensitive enterprise data leakage. An answer generated for Tenant A containing proprietary API keys or financial figures is returned via semantic cache hit to a query from Tenant B.
- **The Fix**: Enforce strict **Namespace Isolation**. Every cache key hash and vector index partition must include `tenant_id` and `user_rbac_scope`. Queries cannot cross tenant partitions under any circumstances.

---

### 6. Overfitting to Purely Synthetic Evaluation Test Sets
- **The Mistake**: Using an LLM to generate 1,000 synthetic (Query, Context, GroundTruth) tuples and using the same LLM family to evaluate production runs without human verification.
- **The Damage**: The evaluation suite tests only the narrow distribution of questions the synthetic generator imagines, completely missing real-world user queries (typos, adversarial prompts, multi-part edge cases).
- **The Fix**: Combine synthetic test generation with production query logging (human-in-the-loop curated golden datasets). Periodically sample and annotate real user interactions.

---

### 7. Missing Cache Stampede Protection (The Thundering Herd)
- **The Mistake**: Allowing hundreds of concurrent identical queries to hit the backend LLM simultaneously when a high-traffic cached entry expires.
- **The Damage**: Latency spikes, API rate limit exhaustion (HTTP 429), and massive billing spikes.
- **The Fix**: Implement **Probabilistic Early Expiration (XFetch)** or **Mutex Locking**:
  $$\text{Expire Early if } -\beta \times \delta \times \ln(U) > (\text{TTL} - (\text{now} - \text{created}))$$
  where $\delta$ is computation time, $\beta > 0$, and $U \sim \text{Uniform}(0, 1)$. The first thread encountering the early expiration window refreshes the cache asynchronously while other callers continue reading the stale cached response.
