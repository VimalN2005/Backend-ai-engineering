# Day 88: AI Observability & LLM Security — 7 Critical Enterprise Antipatterns

Deploying AI observability and security guardrails requires balancing low-latency execution with strict compliance and adversarial defense. Below are 7 critical production antipatterns and engineering remediations.

---

### 1. Logging Unredacted PII and Secrets in Distributed Traces
- **The Mistake**: Sending raw user prompts, model outputs, and tool payloads directly into observability sinks (LangSmith, OpenTelemetry, Datadog, Honeycomb).
- **The Damage**: Massive GDPR, HIPAA, and SOC-2 compliance breaches. Customer social security numbers, credit cards, or internal API keys logged in plaintext in third-party SaaS monitoring tools.
- **The Fix**: Enforce a mandatory **Span Attribute Sanitizer** at the tracer exporter boundary. Automatically scrub regex-matched PII, passwords, and authorization headers before spans leave the cluster.

---

### 2. High-Latency Blocking Guardrails (The 800ms Pre-Flight Tax)
- **The Mistake**: Calling a heavy secondary foundation model (e.g. GPT-4o) synchronously before every single prompt to check for toxicity and injection.
- **The Damage**: Adds 800ms–1500ms of latency to every interaction, degrading user experience and doubling token costs.
- **The Fix**: Implement a **Tiered Guardrail Architecture**:
  - *Tier 1 (Sub-5ms)*: Fast deterministic regex, delimiter checks, and token heuristics.
  - *Tier 2 (Sub-30ms)*: Lightweight specialized classification models (e.g., DeBERTa-v3 or Llama-Guard-8B quantized on ONNX/Triton).
  - Only route ambiguous high-risk prompts to heavy models.

---

### 3. Naive Regex-Only Prompt Injection Defenses
- **The Mistake**: Relying exclusively on exact substring matches (e.g., `if "ignore previous instructions" in prompt: block`).
- **The Damage**: Adversaries easily bypass static strings using unicode homoglyphs, leetspeak, Base64 encoding, rot13, or multilingual translation (e.g. asking in Persian or Base64 decoded at inference).
- **The Fix**: Normalize text (unicode canonical decomposition NFKC, Base64 decoding, whitespace collapse) before analysis. Combine heuristic pattern scanning with semantic vector distance checks against known jailbreak embedding centroids.

---

### 4. Unmetered Token Telemetry Without Tenant Cost Attribution
- **The Mistake**: Tracking aggregate token consumption at the service level without tagging `tenant_id`, `user_id`, or `agent_workflow`.
- **The Damage**: A single misconfigured enterprise tenant or runaway loop consumes \$10,000 in OpenAI API credits in a weekend without billing attribution or rate-limiting safeguards.
- **The Fix**: Mandate OpenTelemetry semantic convention tags on every root span: `tenant.id`, `user.id`, `gen_ai.usage.prompt_tokens`, and `gen_ai.usage.cost_usd`. Enforce per-tenant hard monthly budget caps with circuit breakers.

---

### 5. Absence of Post-Flight Output Secret Leakage Scanners
- **The Mistake**: Assuming that because input prompts were sanitized, model outputs will never contain sensitive credentials.
- **The Damage**: RAG vector databases often ingest internal documentation containing database connection strings (`postgres://...`), private certificates, or forgotten `sk-` API tokens. The LLM summarizes the document and outputs the secret to an untrusted user.
- **The Fix**: Implement high-entropy output regex scanners (scanning for AWS access keys, private key headers, and JWTs) that redact matches to `[REDACTED_SECRET]` before streaming tokens to the client.

---

### 6. Synchronous Trace Drop on Container Shutdown
- **The Mistake**: Using an in-memory batch span processor without registering graceful shutdown handlers (`SIGTERM` / `SIGINT`).
- **The Damage**: When Kubernetes terminates a pod or a serverless worker shuts down, the last batch of trace spans (including the critical error or panic stack trace that caused the failure) is dropped and never exported.
- **The Fix**: Register an explicit shutdown hook that invokes `tracer.force_flush(timeout=5000)` during application teardown.

---

### 7. Client-Side Guardrail Enforcement
- **The Mistake**: Implementing PII redaction or safety prompt checks only in the frontend React/TypeScript client.
- **The Damage**: Attackers bypass the frontend completely by sending raw HTTP POST requests directly to the backend API endpoints.
- **The Fix**: Guardrails are a core backend security boundary. Never trust client-side validation; always enforce input sanitization, tokenization, and output validation inside the backend agent runtime.
