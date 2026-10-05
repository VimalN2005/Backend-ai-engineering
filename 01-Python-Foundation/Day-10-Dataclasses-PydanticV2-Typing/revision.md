# Day 10: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before technical interviews and architectural design reviews:

---

- ⚡ **Dataclasses vs. Pydantic V2 Rule of Thumb:**
  - **Use `@dataclass(slots=True)`**: For internal algorithmic hot paths, mathematical/vector calculations, and domain logic where zero runtime validation overhead and minimal RAM are critical.
  - **Use Pydantic V2 `BaseModel`**: For external boundaries (API request/response, message queues, LLM Tool Calling schemas, DB ingestion) where strict parsing, sanitization, and schema generation are required.

- ⚡ **Dataclass Best Practices:**
  - Always use `field(default_factory=list)` for mutable collections. Never use mutable defaults directly!
  - Use `frozen=True` to enforce immutability and enable automatic hashing.
  - In frozen dataclasses, set derived fields inside `__post_init__` using `object.__setattr__(self, "field", val)`.

- ⚡ **Pydantic V2 Rust Core (`pydantic-core`):**
  - Validation engine is written in Rust, delivering 5x–50x speedups over V1.
  - `model_validate_json()` achieves zero-copy parsing by deserializing bytes directly in Rust without creating intermediate Python dictionaries.

- ⚡ **Strict Mode vs. Lax Mode:**
  - Lax Mode (Default): Coerces compatible types (`"100"` $\to$ `100`, `"true"` $\to$ `True`).
  - Strict Mode (`ConfigDict(strict=True)`): Rejects type coercion completely. Mandatory for financial and security-sensitive models.

- ⚡ **Field Validators (`@field_validator`):**
  - `mode="before"`: Runs on raw input *before* type validation. Use for string stripping and raw input normalization.
  - `mode="after"`: Runs on parsed, type-safe data *after* validation. Use for business invariant checks.

- ⚡ **Model Validators (`@model_validator(mode="after")`):**
  - Executes on the validated model instance.
  - Essential for cross-field consistency checks (e.g., verifying `end_date > start_date`).

- ⚡ **Computed Fields (`@computed_field`):**
  - Decorated properties dynamically included in `model_dump()` and `model_dump_json()`.
  - Avoids storing redundant duplicate state in memory or databases.

- ⚡ **Standalone Validation with `TypeAdapter`:**
  - Validate primitives or collections without creating a full `BaseModel`: `TypeAdapter(list[int]).validate_python(data)`.
  - Always instantiate `TypeAdapter` once at module scope to avoid re-compiling schemas in hot loops.

- ⚡ **LLM Tool Calling Schema Generation:**
  - `Model.model_json_schema()` emits standardized OpenAPI/JSON Schema contracts directly compatible with OpenAI Function Calling and Google Gemini Tools.
