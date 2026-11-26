# Day 86: Principal AI Systems Interview Questions — LLM Tool Calling & Pydantic Contracts

### Q1: How does LLM Tool Calling work at the protocol level, and how does Pydantic V2 guarantee type safety at the runtime boundary?
**Answer**:
- **Protocol Level**: Foundation models do not execute code. During inference, tool definitions are serialized into the JSON Schema format and injected into the model's system prompt or specialized tool API parameters. When the model decides to invoke a tool, constrained sampling prompts it to emit a structured JSON object specifying `name` and `arguments`.
- **Runtime Boundary**:
  1. The agent orchestrator intercepts the response and parses `arguments` with `json.loads`.
  2. The raw dictionary is passed into `PydanticModel.model_validate(raw_dict)`.
  3. Pydantic performs C-speed type coercion, regex pattern validation, and bound checks.
  4. If validation succeeds, a strongly-typed model instance is delivered to the tool handler. If validation fails, `pydantic.ValidationError` is captured, preventing untrusted or malformed data from ever touching the backend function.

---

### Q2: How do you design a self-healing tool execution loop when an LLM hallucinates non-existent arguments or violates schema constraints?
**Answer**:
- **Diagnostic Extraction**: When `ValidationError` is raised, we extract the structured error list (`err['loc']`, `err['msg']`, `err['type']`).
- **Corrective Feedback Injection**: We format this into a structured correction prompt and inject it back into the conversation history with `role: "tool"` or `role: "user"`.
  ```json
  {
    "status": "VALIDATION_ERROR",
    "details": [{"field": "limit", "error": "Value must be <= 50, got 1000"}],
    "instruction": "Please repair the arguments for 'query_database' and retry."
  }
  ```
- **Loop Bounding**: To prevent infinite error loops, we enforce a hard ceiling (e.g. `max_healing_turns = 2`). If the model fails repeatedly on the same tool call, the agent degrades gracefully and prompts the user for clarification rather than crashing.

---

### Q3: What security architectures prevent Arbitrary Code Execution and Prompt Injection from weaponizing tool calls in production?
**Answer**:
- **No Dynamic Interpreters**: We never expose raw `eval()`, `exec()`, or unparameterized shell tools.
- **Strict Parameter Allowlisting**: We use `Literal` enums for table names, file paths, and environment selectors rather than raw strings.
- **Session Auth Decoupling**: Tenant IDs, user IDs, and permissions scopes are never exposed to the LLM as tool arguments. Instead, the application layer injects them from authenticated session JWTs before invoking backend handlers.
- **Read-Only Separation**: We isolate read-only reporting queries to dedicated database read replicas using database-level read-only connection credentials.

---

### Q4: How do you handle high-latency or asynchronous long-running tools (e.g. 5-minute batch jobs or human approval)?
**Answer**:
- **Asynchronous Tool Calling (Webhook / Task Queue Pattern)**:
  1. When the LLM calls `start_data_migration(source, target)`, the handler immediately dispatches an async job to Celery/Temporal/Kafka and returns a `job_id`:
     `{"status": "QUEUED", "job_id": "job_9481", "estimated_seconds": 180}`.
  2. The LLM informs the user: *"Your data migration job #9481 has been scheduled. I will monitor it for you."*
  3. The agent suspends execution and persists conversation state.
  4. Upon task completion, a webhook revives the agent turn, appends the completion event to the conversation history, and prompts the LLM to deliver the final status update.

---

### Q5: How do you address the context window explosion problem when tool outputs return large JSON datasets?
**Answer**:
- **Extractive Truncation**: Enforce strict result limits (e.g., maximum 20 records) in the database query layer.
- **Schema Filtering / Projection**: Strip unnecessary internal columns (e.g. raw password hashes, internal timestamps, binary blobs) before formatting the tool response message.
- **Summarization Intermediate Layer**: For naturally large payloads (e.g., 200 customer reviews), pass the raw payload through a fast, lightweight model (e.g., Gemini 1.5 Flash / GPT-4o-mini) to generate a concise 3-bullet summary before injecting the summary into the main reasoning agent's context.
- **Artifact Offloading**: Store large payloads in S3/Blob storage, returning a URI link `{"artifact_url": "s3://reports/export_123.csv", "row_count": 50000, "sample": [...]}` so the model can reason over the summary and provide the link to the user.
