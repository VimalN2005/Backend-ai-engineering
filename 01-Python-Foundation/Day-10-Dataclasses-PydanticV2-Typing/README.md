# Day 10: Dataclasses, Pydantic V2 Models & Strict Production Typing

In enterprise backend engineering and production AI systems, data entering and leaving systems must be strictly typed, validated, and serialized. Pydantic V2 and Python Dataclasses form the core data validation layer of modern web frameworks (FastAPI), ORMs, and AI orchestration engines (LangChain, LlamaIndex, OpenAI Structured Outputs).

---

## 1. The Evolution of Data Modeling in Python

Python has transitioned through several paradigms to model structured domain entities:

```
+-----------------------------------------------------------------------------------------+
| Primitive Dictionaries: {"id": 1, "name": "doc"} (No type guarantees, brittle keys)     |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
| collections.namedtuple (Immutable, tuple-like, memory efficient, but weak typing)       |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
| @dataclass (Python 3.7+ standard library; fast, typed, code generator; no runtime check)|
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
| Pydantic V2 (Powered by Rust `pydantic-core`; strict runtime parsing, schema generation)|
+-----------------------------------------------------------------------------------------+
```

| Feature | Standard `@dataclass` | Pydantic V2 `BaseModel` |
| :--- | :--- | :--- |
| **Primary Purpose** | Internal domain modeling & data containers | External data parsing, boundary validation & serialization |
| **Runtime Validation** | ❌ None (types are annotations only) | ✅ Strict, deep runtime parsing & coercion |
| **Engine** | Pure Python standard library | **Rust** (`pydantic-core`) |
| **JSON Schema Generation** | ❌ Manual reflection required | ✅ Native OpenAPI / JSON Schema generation |
| **Memory Overhead** | Low (Minimal C-struct with `slots=True`) | Moderate (Optimized Rust validation tree) |
| **Best Use Case** | Internal domain services, algorithms, math | API endpoints, LLM Structured Outputs, DB boundaries |

---

## 2. Python `@dataclass` Internals

The `@dataclass` decorator inspects class annotations at import time and dynamically generates boilerplate dunder methods (`__init__`, `__repr__`, `__eq__`, `__hash__`):

```python
from dataclasses import dataclass

@dataclass
class DocumentChunk:
    chunk_id: str
    token_count: int
    score: float = 0.0
```

Under the hood, CPython generates bytecode for:
```python
def __init__(self, chunk_id: str, token_count: int, score: float = 0.0):
    self.chunk_id = chunk_id
    self.token_count = token_count
    self.score = score
```

---

## 3. Advanced Dataclass Controls

Configure dataclass generation using flags:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True, kw_only=True)
class ImmutableVector:
    vector_id: str
    dimension: int
    tags: tuple[str, ...] = field(default_factory=tuple)
```

- **`frozen=True`**: Makes instances immutable (mutations raise `FrozenInstanceError`) and automatically generates a safe `__hash__` method.
- **`slots=True`**: Allocates attributes in a static C-array descriptor instead of `__dict__`, reducing RAM by ~60%.
- **`kw_only=True`**: Enforces keyword-only arguments during instantiation (`ImmutableVector(vector_id="v1", ...)`).
- **`default_factory=...`**: Mandatory for mutable default values (`list`, `dict`, `set`) to prevent shared reference bugs across instances.

---

## 4. Dataclass Lifecycle & `__post_init__`

Use `__post_init__` for field validation or derived calculations:

```python
from dataclasses import dataclass, InitVar

@dataclass
class NormalizedEmbedding:
    raw_vector: list[float]
    normalize_on_init: InitVar[bool] = True
    magnitude: float = field(init=False)

    def __post_init__(self, normalize_on_init: bool) -> None:
        if not self.raw_vector:
            raise ValueError("Vector cannot be empty")
        self.magnitude = sum(x**2 for x in self.raw_vector) ** 0.5
        if normalize_on_init and self.magnitude > 0:
            self.raw_vector = [x / self.magnitude for x in self.raw_vector]
```

---

## 5. Architecture of Pydantic V2 & `pydantic-core` (Rust)

In Pydantic V2, validation logic was completely rewritten in **Rust** as `pydantic-core`:

```
+-------------------------------------------------------------------+
|                        Python User Layer                          |
|   `class UserPayload(BaseModel): name: str, age: int`             |
+-------------------------------------------------------------------+
                                  │
                   Constructs Schema Definition
                                  ▼
+-------------------------------------------------------------------+
|                   `pydantic-core` (Compiled Rust)                 |
|   1. Direct JSON Byte Stream Parsing (Zero Python object copy)    |
|   2. Validation Walker (Schema tree validation in Rust)           |
|   3. Fast CPython Object Allocation                               |
+-------------------------------------------------------------------+
```

### Why Pydantic V2 is Transformative:
- **Zero-Copy JSON Parsing**: `model_validate_json(raw_bytes)` skips intermediate Python dictionary allocations, parsing JSON directly in Rust.
- **5x–50x Faster**: Performance matches native C serializers while preserving rich Python type hints.

---

## 6. Pydantic V2 `BaseModel` & `Field` Constraints

Define models with rich metadata and boundary constraints:

```python
from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from datetime import datetime, timezone

class LLMGenerationRequest(BaseModel):
    request_id: UUID = Field(default_factory=uuid4, description="Unique trace identifier")
    prompt: str = Field(min_length=3, max_length=10000, description="Input user prompt")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling randomness")
    max_tokens: int = Field(default=2048, gt=0, le=32768)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

---

## 7. Strict Mode vs. Lax Mode

By default, Pydantic operates in **Lax Mode**, coercing compatible types:
- String `"123"` $\to$ integer `123`.
- String `"true"` or integer `1` $\to$ boolean `True`.

In financial transactions, security systems, and AI tool calling, lax coercion can mask subtle client bugs. Enforce **Strict Mode**:

```python
from pydantic import BaseModel, ConfigDict

class StrictPaymentTransaction(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    account_id: int
    amount_cents: int
    is_settled: bool
```
In strict mode: `StrictPaymentTransaction(account_id="101", ...)` immediately raises a `ValidationError`!

---

## 8. Field Validators (`@field_validator`)

Field validators intercept specific attributes during validation:

```python
from pydantic import BaseModel, field_validator, ValidationInfo

class APIClientConfig(BaseModel):
    endpoint_url: str
    api_key: str

    @field_validator("endpoint_url", mode="after")
    @classmethod
    def validate_secure_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("Insecure endpoint: Production APIs must use HTTPS")
        return v.rstrip("/")

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_whitespace(cls, v: Any) -> Any:
        # mode="before" runs BEFORE type checking; input can be any raw data
        if isinstance(v, str):
            return v.strip()
        return v
```

- **`mode="before"`**: Runs before Pydantic parses the type. Ideal for stripping strings, sanitizing payloads, or parsing raw JSON strings.
- **`mode="after"`** (Default): Runs after Pydantic verifies the type. Type-safe validation on the parsed Python object.

---

## 9. Model Validators (`@model_validator`)

Use model validators for **cross-field validation** and object-level invariants:

```python
from pydantic import BaseModel, model_validator

class DateRangeQuery(BaseModel):
    start_date: datetime
    end_date: datetime

    @model_validator(mode="after")
    def verify_valid_range(self) -> "DateRangeQuery":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be strictly after start_date")
        return self
```

---

## 10. Computed Fields (`@computed_field`)

Expose dynamically calculated properties in JSON exports without storing redundant data:

```python
from pydantic import BaseModel, computed_field

class RAGChunkMetrics(BaseModel):
    char_count: int
    cost_per_million_chars: float = 1.50

    @computed_field  # type: ignore[misc]
    @property
    def estimated_tokens(self) -> int:
        return self.char_count // 4

    @computed_field  # type: ignore[misc]
    @property
    def estimated_cost_usd(self) -> float:
        return (self.char_count / 1_000_000) * self.cost_per_million_chars
```

`model_dump_json()` automatically includes `estimated_tokens` and `estimated_cost_usd` in the output!

---

## 11. Serialization & Deserialization Modes

```python
data = request.model_dump()                  # Serializes to Python dict
json_str = request.model_dump_json()         # Serializes to JSON string directly in Rust
obj = RequestModel.model_validate(raw_dict)  # Deserializes from Python dict
obj = RequestModel.model_validate_json(b"{}")# Deserializes raw JSON bytes in Rust
```

Control output with `exclude_unset=True`, `exclude_none=True`, and `by_alias=True`.

---

## 12. TypeAdapter for Standalone Validation

Validate types without defining a full `BaseModel`:

```python
from pydantic import TypeAdapter

# Validate a list of integers directly
int_list_adapter = TypeAdapter(list[int])
clean_list = int_list_adapter.validate_python(["1", 2, "3"])  # Returns [1, 2, 3]
```

---

## 13. Generic Models (`Generic[T]`) for API Envelopes

Create type-safe, reusable API response envelopes:

```python
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class APIResponse(BaseModel, Generic[T]):
    success: bool
    status_code: int
    data: T
    error: str | None = None
```

Usage: `APIResponse[list[UserRecord]]` provides end-to-end type safety in IDEs, mypy, and FastAPI.

---

## 14. Composable Types with `typing.Annotated`

Decouple validation rules into reusable custom types:

```python
from typing import Annotated
from pydantic import Field, AfterValidator

def validate_openai_key(v: str) -> str:
    if not v.startswith("sk-"):
        raise ValueError("Invalid OpenAI API key format: must start with 'sk-'")
    return v

OpenAIKey = Annotated[str, Field(min_length=20), AfterValidator(validate_openai_key)]

class LLMSettings(BaseModel):
    primary_key: OpenAIKey
    fallback_key: OpenAIKey
```

---

## 15. Production AI: JSON Schema for LLM Tool Calling

Modern LLMs (OpenAI, Gemini, Claude) execute tools via JSON Schema. Pydantic models generate this schema natively:

```python
class SearchToolParameters(BaseModel):
    query: str = Field(description="Search query string")
    max_results: int = Field(default=5, ge=1, le=20, description="Maximum results to return")

# Generate OpenAI/Gemini compatible tool definition
tool_schema = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": "Searches internal RAG documents",
        "parameters": SearchToolParameters.model_json_schema()
    }
}
```
This guarantees 100% type safety and contract alignment between your backend and the LLM!
