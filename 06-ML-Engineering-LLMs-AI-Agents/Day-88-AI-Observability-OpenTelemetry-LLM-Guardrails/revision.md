# Day 88: Rapid Revision & Flashcards — AI Observability & LLM Security

## 5-Minute Core Revision Cheat Sheet

### OpenTelemetry GenAI Semantic Standards
- `gen_ai.system`: Provider (`"openai"`, `"anthropic"`, `"google"`).
- `gen_ai.request.model`: Model name (`"gpt-4o"`).
- `gen_ai.usage.prompt_tokens` & `gen_ai.usage.completion_tokens`: Token usage accounting.
- **Trace Hierarchy**: Root agent span $\to$ retrieval spans $\to$ LLM reasoning turns $\to$ tool dispatches.

### Security Guardrail Architecture (OWASP LLM Top 10)
- **Direct Prompt Injection (LLM01)**: User jailbreaks model to bypass system prompts. Defended via delimiter tagging, token classifiers, and prompt hardening.
- **Indirect Prompt Injection**: RAG documents containing hidden attack strings. Defended by treating context as untrusted data.
- **Sensitive Data Disclosure (LLM06)**: PII or credentials leaked. Defended via pre-flight Presidio PII tokenization and post-flight high-entropy secret scrubbing (`sk-`, `AKIA`).
- **Insecure Output Handling (LLM02)**: LLM generating unsafe code/SQL. Defended via strict Pydantic schemas and execution sandboxing.

---

## 10 Active Recall Flashcards

1. **Q: What are the primary OpenTelemetry attributes for tracking token usage?**
   *A*: `gen_ai.usage.prompt_tokens`, `gen_ai.usage.completion_tokens`, and `gen_ai.usage.total_tokens`.

2. **Q: How does Indirect Prompt Injection differ from Direct Prompt Injection?**
   *A*: Direct injection comes from the user prompt; indirect injection is embedded within external third-party data retrieved via RAG or web search.

3. **Q: What is the Presidio bi-directional PII tokenization pattern?**
   *A*: Masking sensitive PII (emails, SSNs) with typed placeholders (`[EMAIL_1]`) before sending to the LLM, and swapping them back to original values upon response delivery.

4. **Q: What is the risk of using a heavy LLM-as-a-Judge for synchronous pre-flight input checks?**
   *A*: It adds 800ms–1500ms of latency and doubles inference costs. Solved by tiered guardrails: fast regex/heuristics first, SLMs second.

5. **Q: How do you detect secret and API key leakage in LLM outputs?**
   *A*: High-entropy regex scanners that detect provider-specific key prefixes (`sk-`, `AKIA`) and redact them to `[REDACTED_SECRET]`.

6. **Q: Why should PII never be logged directly in OpenTelemetry spans?**
   *A*: Logging unredacted customer PII in distributed monitoring sinks violates GDPR, HIPAA, and SOC-2 data compliance mandates.

7. **Q: How do you enforce per-tenant token rate limits in multi-tenant LLM platforms?**
   *A*: Tag all root spans with `tenant_id`, meter streaming usage into a time-series store, and enforce token-bucket rate limits in Redis returning HTTP 429 when limits are breached.

8. **Q: What does `gen_ai.response.finish_reasons` indicate?**
   *A*: The reason the LLM stopped generating: `"stop"` (normal completion), `"tool_calls"` (invoking a function), or `"length"` (hit token limit).

9. **Q: Why is client-side guardrail validation insufficient?**
   *A*: Attackers bypass frontend UI validation completely by sending raw HTTP API requests directly to backend endpoints.

10. **Q: How can output guardrails monitor streaming tokens without breaking TTFT?**
    *A*: By evaluating tokens through a sliding window buffer in parallel with the SSE stream, aborting the connection only if a secret or safety violation is detected.
