# Day 89: Capstone 4 — 7 Critical Enterprise Multimodal RAG & Multi-Agent Antipatterns

Deploying enterprise-scale multimodal RAG and autonomous multi-agent backends introduces severe architectural failure modes. Below are 7 critical antipatterns and production-grade remediations.

---

### 1. Flattening Complex Tables into Raw Unstructured Text Strings
- **The Mistake**: Using standard OCR or naive PDF text scrapers that strip column alignments and flatten structured tables into raw unstructured paragraphs.
- **The Damage**: The spatial matrix relationship between headers and values is destroyed. The LLM associates Row 4's revenue with Row 5's operating margin, answering financial questions with hallucinated numbers.
- **The Fix**: Use a layout-aware table parser (e.g., Table Transformers, Unstructured, or Marker) that converts tables into explicit **Markdown Tables** or structured JSON objects, paired with an LLM-generated natural language summary for dense retrieval.

---

### 2. Treating Charts and Diagrams as Text-Only Captions
- **The Mistake**: Replacing charts and schematics with a 1-sentence static caption and discarding the image.
- **The Damage**: Nuanced information (trend lines, bar chart deltas, architectural data flows) is completely lost.
- **The Fix**: Use **Cross-Modal Embeddings** (e.g. CLIP / Gemini Multimodal Embeddings) that project images and text into a shared vector space, and store the chart image reference alongside its structured tabular data in pgvector.

---

### 3. Relying on LLMs for Mathematical Calculations
- **The Mistake**: Asking the reasoning model to calculate percentage differences, compound annual growth rates, or currency conversions directly in generation tokens.
- **The Damage**: Foundation models are probabilistic token predictors, not calculators. They routinely make elementary arithmetic mistakes (e.g. `(18.2 - 16.9) / 18.2` calculated as `12.5%` instead of `7.14%`).
- **The Fix**: Strictly delegate all numerical calculations to a deterministic **Analytics Agent / Code Interpreter** running in a sandboxed Python runtime.

---

### 4. Missing Citation Provenance and Bounding Boxes
- **The Mistake**: Delivering synthesized enterprise answers without verifiable document citations, page numbers, or image bounding boxes.
- **The Damage**: Compliance and legal teams cannot audit the source of the facts, destroying user trust in regulated industries (finance, healthcare, legal).
- **The Fix**: Require every retrieved evidence chunk to carry source metadata: `{"doc_id": "report.pdf", "page": 12, "bbox": [x1, y1, x2, y2]}`. Inject explicit inline citation tags (`[Doc: report.pdf, Page 12]`) into the final output.

---

### 5. Unbounded Sub-Agent Fan-Out (The Rate Limit Stampede)
- **The Mistake**: Allowing a Supervisor Agent to dynamically spawn 20+ parallel worker sub-agents across multiple reasoning branches simultaneously.
- **The Damage**: Instantly triggers HTTP 429 (`RateLimitExceeded`) from LLM API providers, runs up extreme token expenses, and causes cascading timeouts across the backend.
- **The Fix**: Enforce strict concurrency limits on sub-agent pools (e.g., `asyncio.Semaphore(3)` or worker queue pools) with exponential backoff and jitter.

---

### 6. Ignoring Cross-Modal Latent Space Disconnect
- **The Mistake**: Indexing image descriptions using standard text embeddings (e.g., `text-embedding-3-small`) while querying them with visual features, or vice versa.
- **The Damage**: Near-zero retrieval recall for non-text artifacts. Queries for "cloud architecture schematic" fail to retrieve the diagram because text-only models fail to bridge visual-text semantic alignment.
- **The Fix**: Standardize on a unified multimodal embedding model where both visual tokens and text queries are projected into the same unit hypersphere.

---

### 7. Skipping the Fact-Checking & Groundedness Loop
- **The Mistake**: Directly streaming the Supervisor's first draft response to the end-user without an automated factuality check.
- **The Damage**: Even when retrieved context is 100% accurate, models occasionally hallucinate when synthesizing across multiple retrieved chunks.
- **The Fix**: Place a lightweight **Fact-Checking Guardrail Node** in the graph before the terminal state. Verify that all numerical figures and named entities in the draft exist in the retrieved evidence before releasing the response.
