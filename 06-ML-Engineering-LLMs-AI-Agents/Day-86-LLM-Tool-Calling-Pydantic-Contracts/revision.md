# Day 86: Rapid Revision & Flashcards — LLM Tool Calling & Pydantic Contracts

## 5-Minute Core Revision Cheat Sheet

### Tool Calling Execution Lifecycle
1. **Definition**: Define tool inputs via `Pydantic V2` (`BaseModel`, `Field(description=...)`).
2. **Schema Generation**: Export to OpenAPI/JSON Schema using `model.model_json_schema()`.
3. **Model Sampling**: LLM decides when to emit `tool_calls` with `name` and `arguments` (JSON string).
4. **Validation Boundary**: Intercept call; validate via `schema.model_validate(json.loads(args))`.
5. **Execution**: Invoke handler with validated typed object. Return result in `role: "tool"` message.
6. **Synthesis**: Foundation model reads tool output and generates final natural language response.

### Self-Healing & Error Recovery
- Catch `pydantic.ValidationError`.
- Format field diagnostics: `loc` + `msg` + `expected_type`.
- Inject corrective prompt back to LLM to request repaired arguments without crashing.
- Enforce strict `max_iterations` counter (3–5 turns) to prevent infinite loops.

---

## 10 Active Recall Flashcards

1. **Q: How does an LLM communicate its intent to call a tool?**
   *A*: It generates a structured output message containing a `tool_calls` list, where each entry contains a unique `id`, tool `name`, and an `arguments` string encoded in JSON.

2. **Q: Why should backend functions never accept raw `json.loads(tool_call.arguments)` directly?**
   *A*: Raw dictionaries bypass type coercion, schema constraints, and bound checks. Missing keys trigger `KeyError` crashes, and hallucinated keys can cause unexpected side effects.

3. **Q: What role does Pydantic V2's `model_json_schema()` play in function calling?**
   *A*: It automatically serializes Python type hints, bounds (`ge`, `le`), regex patterns, and docstrings into standard JSON Schema Draft 7/2020-12 definitions for the model.

4. **Q: What is the Self-Healing mechanism in a tool-calling agent?**
   *A*: Intercepting a `ValidationError` when a model emits malformed arguments, formatting the field-specific error diagnostics into a corrective message, and asking the model to fix the arguments.

5. **Q: How do you prevent Arbitrary Code Execution (ACE) when exposing tools to LLMs?**
   *A*: Never pass raw LLM text into `eval()` or unsanitized shell execution tools; expose only pre-compiled deterministic Python functions with constrained parameter schemas.

6. **Q: What is the role of `tool_call_id` in conversation history?**
   *A*: It establishes a unique linkage between the assistant's tool invocation request and the subsequent tool response message (`role: "tool"`), especially in parallel tool executions.

7. **Q: Why is it dangerous to let the LLM provide `tenant_id` as a tool argument?**
   *A*: Prompt injection or hallucinations can supply another customer's `tenant_id`, causing critical cross-tenant data leakage (IDOR). Authorization IDs must be injected by the app layer.

8. **Q: How should an agent handle massive tool outputs (e.g. 100,000 database rows)?**
   *A*: Enforce database-level pagination, column projection, truncate to a sensible maximum (e.g., 20 rows), or offload the raw dataset to object storage and pass only a summary link.

9. **Q: Why is field documentation in `Field(..., description="...")` critical for tool calling?**
   *A*: LLMs rely directly on schema descriptions to understand argument semantics, expected units (e.g., seconds vs minutes), and valid format examples.

10. **Q: How do you prevent infinite tool failure loops in autonomous ReAct agents?**
    *A*: Enforce a hard maximum turn limit (e.g., 5 iterations) and implement circuit breakers that terminate execution if identical error signatures repeat.
