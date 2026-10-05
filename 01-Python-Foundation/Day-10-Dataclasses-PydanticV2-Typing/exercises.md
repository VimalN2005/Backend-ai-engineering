# Day 10: Practical Hands-On Exercises

Master Dataclasses, Pydantic V2 models, strict typing, and schema generation by solving these 3 production challenges.

---

### Exercise 1: Strict LLM Tool Call Validator with Annotated Types (Medium)
Build an enterprise tool schema validator for an AI Customer Support Agent that:
1. Defines a parameter model `CreateSupportTicket`:
   - `customer_id`: Valid UUID string.
   - `category`: Literal enum (`"billing"`, `"technical"`, `"account"`).
   - `priority`: Integer between 1 (Low) and 5 (Urgent).
   - `summary`: String between 10 and 150 characters, stripped of leading/trailing whitespace.
   - `contact_email`: Valid email format validated via regex or `Annotated[str, AfterValidator]`.
2. Generates an OpenAI/Gemini-compliant JSON schema using `CreateSupportTicket.model_json_schema()`.
3. Implements a function `parse_llm_tool_response(raw_json: str) -> tuple[Optional[CreateSupportTicket], list[dict]]`:
   - Returns the validated model instance if valid.
   - If invalid, returns `None` and an extracted list of diagnostic errors formatted for prompting the LLM to fix its output.

---

### Exercise 2: High-Throughput Streaming Event Serializer with Generic Models (Medium)
Build a generic event streaming model for Kafka/Redis message pipelines:
1. Create a generic model `EventEnvelope[T]`:
   - `event_id`: UUID (default `uuid4`).
   - `event_type`: String (e.g. `"order_created"`, `"vector_upserted"`).
   - `payload`: Generic payload `T`.
   - `created_at`: UTC timestamp.
2. Define two specific payloads:
   - `OrderPayload`: `order_id: int`, `amount_cents: int`, `currency: Literal["USD", "EUR"]`.
   - `VectorUpsertPayload`: `doc_id: str`, `embedding: list[float]`, `chunk_index: int`.
3. Create a benchmark function that serializes 10,000 instances of `EventEnvelope[OrderPayload]` using `.model_dump_json()` and measures throughput (events/sec).

---

### Exercise 3: Memory-Optimized Audited Domain Entity with Dataclass & Slots (Advanced)
Build a core financial transaction entity `AuditedLedgerEntry` using standard Python dataclasses:
1. Configure `@dataclass(slots=True, frozen=True, kw_only=True)`.
2. Fields:
   - `transaction_id`: UUID.
   - `sender_account`: str.
   - `receiver_account`: str.
   - `amount_cents`: int (must be > 0).
   - `tax_cents`: int (derived in `__post_init__` as 18% of `amount_cents`).
   - `total_cents`: int (derived in `__post_init__` as `amount_cents + tax_cents`).
3. In `__post_init__`:
   - Validate that `sender_account != receiver_account`.
   - Compute `tax_cents` and `total_cents` safely using `object.__setattr__`.
4. Demonstrate that the instance is immutable, hashable, can be placed into a Python `set`, and uses minimal memory compared to a standard class with `__dict__`.
