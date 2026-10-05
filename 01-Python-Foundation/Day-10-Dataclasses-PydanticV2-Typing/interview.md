# Day 10: Top 5 Technical Interview Questions

---

### Question 1: What architectural changes were introduced in Pydantic V2, and why is `pydantic-core` (Rust) so much faster than V1?

#### Expected Answer:
In Pydantic V1, validation was executed in pure Python by traversing dictionaries, executing Python loops, and creating intermediate `PyObject` instances for every field. This introduced heavy CPU overhead in web frameworks like FastAPI.

In Pydantic V2:
1. **Separation of Concerns**: Python is used solely to define classes, inspect type annotations, and build the validation schema tree.
2. **`pydantic-core` in Rust**: The entire validation, serialization, and JSON parsing logic was rewritten in compiled Rust.
3. **Zero-Copy JSON Parsing**: When calling `model_validate_json(raw_bytes)`, the Rust engine parses JSON bytes directly into validated structs without allocating intermediate Python dictionaries (`dict`).
4. **Result**: Validation and serialization speeds increased by **5x to 50x**, making Python API boundary validation competitive with native compiled languages like Go and Rust.

---

### Question 2: When would you use a Python `@dataclass(slots=True)` over a Pydantic V2 `BaseModel` in production?

#### Expected Answer:
Both tools model structured data, but they target different architectural layers:

#### Choose Python `@dataclass(slots=True)`:
- **Internal Domain Logic & Algorithmic Hot Paths**: Mathematical operations, vector manipulation, and graph traversal where hundreds of thousands of objects are created and discarded per second.
- **Zero Validation Overhead**: Python dataclasses perform no runtime type checking at initialization, avoiding validation CPU cycles.
- **Ultra-low Memory Footprint**: With `slots=True`, instances allocate only raw C pointers for fields without dictionary or validation state overhead.

#### Choose Pydantic V2 `BaseModel`:
- **System Boundaries & External Inputs**: API endpoints (FastAPI request/response), message queue consumers (Kafka/RabbitMQ), and database boundaries where incoming untrusted data must be sanitized.
- **Schema Generation**: Generating OpenAPI documentation or JSON Schemas for LLM Tool Calling.
- **Complex Invariant Enforcement**: Cross-field interdependent validation, regex matching, and string sanitization.

---

### Question 3: What is the difference between Lax Mode and Strict Mode in Pydantic V2? Why is Strict Mode critical in financial and security systems?

#### Expected Answer:
By default, Pydantic V2 operates in **Lax Mode**, which attempts to coerce compatible types:
- String `"100"` is coerced to `int` `100`.
- String `"true"`, `"1"`, or int `1` is coerced to boolean `True`.
- Float `12.0` is coerced to `int` `12`.

#### The Danger:
In financial transactions, security access tokens, or LLM tool execution, lax coercion can hide severe client defects:
- An account identifier `"0123"` may be coerced to `123`, losing critical leading zeros.
- An accidental string `"false"` passed into a truthy non-empty check might trigger unexpected behaviors in loosely typed systems.

#### The Solution:
Enforcing `ConfigDict(strict=True)` disables type coercion. Pydantic requires exact type matching (`int` must be `int`, `bool` must be `bool`), ensuring API contracts are strictly respected.

---

### Question 4: How do `@field_validator(mode="before")` and `@field_validator(mode="after")` differ, and when should each be used?

#### Expected Answer:
The `mode` argument dictates where in the validation pipeline the validator runs:

#### `mode="before"`:
- Executes **before** Pydantic performs any type validation or schema parsing.
- The input value can be of any arbitrary raw type (e.g. `Any`).
- **Use Case**: Sanitizing and normalizing incoming raw client data—such as stripping leading/trailing whitespace, splitting comma-separated strings into lists, or parsing raw JSON strings into dicts.

#### `mode="after"` (Default):
- Executes **after** Pydantic has verified that the input matches the field's declared type.
- The input value is guaranteed to be type-safe and already parsed.
- **Use Case**: Domain invariant checks on typed values (e.g., verifying that a string URL starts with `https://`, or that an integer amount is within an allowed business threshold).

---

### Question 5: How does Pydantic V2 power reliable Structured Outputs and Tool Calling in modern LLM architectures?

#### Expected Answer:
LLMs produce unstructured probabilistic text by default. To integrate LLMs reliably into enterprise backend pipelines:
1. **Schema Generation via `model_json_schema()`**:
   Pydantic models automatically convert Python type hints, field descriptions, and constraints into JSON Schema definitions. These schemas are passed directly to LLM provider APIs (OpenAI Function Calling, Google Gemini Tools).
2. **Deterministic Response Parsing**:
   When the LLM outputs a JSON tool call argument string, Pydantic's `model_validate_json()` parses and validates the arguments against the schema.
3. **Automated Error Remediation**:
   If the LLM hallucinates extra fields, violates boundaries (`gt`/`le`), or passes incorrect types, Pydantic raises a `ValidationError`. Calling `err.errors()` yields precise field-level diagnostics (`loc`, `input`, `msg`), which the backend can inject directly into the next LLM prompt to instruct the model to correct its mistake autonomously.
