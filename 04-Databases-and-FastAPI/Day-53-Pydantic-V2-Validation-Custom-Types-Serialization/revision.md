# Day 53: Rapid Revision & Cheat Sheet - Pydantic V2 Architecture

A high-density 5-minute reference sheet for **Pydantic V2**, `pydantic-core`, custom functional validators, discriminated unions, and high-performance serialization.

---

## 1. V1 to V2 Syntax Translation Matrix

| Operation | Pydantic V1 (Deprecated) | Pydantic V2 (Modern Standard) |
| :--- | :--- | :--- |
| **Model Configuration** | `class Config: orm_mode = True` | `model_config = ConfigDict(from_attributes=True)` |
| **Field Validation** | `@validator("field", pre=True)` | `@field_validator("field", mode="before")` |
| **Model Validation** | `@root_validator(pre=True)` | `@model_validator(mode="before")` |
| **Cross-field Post** | `@root_validator` | `@model_validator(mode="after")` |
| **Dump to Dict** | `model.dict(exclude_none=True)` | `model.model_dump(exclude_none=True)` |
| **Dump to JSON** | `model.json()` | `model.model_dump_json()` |
| **Parse Dict** | `Model.parse_obj(data)` | `Model.model_validate(data)` |
| **Parse JSON Bytes** | `Model.parse_raw(raw_bytes)` | `Model.model_validate_json(raw_bytes)` |

---

## 2. Validator Execution Modes

```
Input Data (Raw JSON / Dict)
            |
            v
+-------------------------------+
| @field_validator(mode="before") |  Operates on raw input (e.g., cleans raw string before type cast)
+-------------------------------+
            |
            v
+-------------------------------+
| Rust pydantic-core Engine     |  Type coercion, boundary checks (min/max), regex matching
+-------------------------------+
            |
            v
+-------------------------------+
| @field_validator(mode="after")  |  Operates on parsed Python types (Business rules) [Default]
+-------------------------------+
            |
            v
+-------------------------------+
| @model_validator(mode="after")  |  Operates on self (Cross-field invariants)
+-------------------------------+
```

---

## 3. Composable Domain Types via `typing.Annotated`

```python
from typing import Annotated
from pydantic import BeforeValidator, AfterValidator

# 1. Cleaner: trims whitespace
def clean_str(v: any) -> any:
    return v.strip() if isinstance(v, str) else v

# 2. Validator: checks enterprise prefix
def check_prefix(v: str) -> str:
    if not v.startswith("ENT-"):
        raise ValueError("Must start with 'ENT-'")
    return v

# Composable Type Alias
EnterpriseCode = Annotated[str, BeforeValidator(clean_str), AfterValidator(check_prefix)]
```

---

## 4. Discriminated Union Template (High-Speed O(1) Routing)

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, TypeAdapter

class TextMessage(BaseModel):
    kind: Literal["text"]
    body: str

class ImageMessage(BaseModel):
    kind: Literal["image"]
    image_url: str

# Tagged Union using 'kind' discriminator
MessagePayload = Annotated[
    Union[TextMessage, ImageMessage],
    Field(discriminator="kind"),
]

# Top-level adapter (compiled once in Rust)
MESSAGE_ADAPTER = TypeAdapter(MessagePayload)
```

---

## 5. Dynamic Computed Fields & Custom Serializers

```python
from pydantic import BaseModel, computed_field, field_serializer
from datetime import datetime, timezone

class ProductOrder(BaseModel):
    unit_price: float
    quantity: int
    created_at: datetime

    # 1. Computed Field: Automatically included in model_dump() and JSON schema!
    @computed_field
    @property
    def total_price(self) -> float:
        return round(self.unit_price * self.quantity, 2)

    # 2. Field Serializer: Formats datetime to unix epoch when dumping to JSON
    @field_serializer("created_at", when_used="json")
    def serialize_epoch(self, dt: datetime) -> int:
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
```

---

## 6. One-Minute Flashcard Review

1. **Why is Pydantic V2 faster than V1?** Core validation logic was rewritten in compiled Rust (`pydantic-core`), parsing JSON and checking types in C-ABI without Python bytecode overhead.
2. **When should you use `model_validate_json()`?** When receiving raw JSON strings or bytes from HTTP requests or message queues. It parses directly in Rust without creating intermediate Python dictionaries.
3. **What does `ConfigDict(frozen=True)` do?** Makes the model immutable and generates a deterministic `__hash__`, allowing instances to be used as dictionary keys or stored in sets.
4. **Why is `TypeAdapter` used?** To validate and serialize non-Model types (e.g., `List[int]`, `Dict[str, User]`) directly in Rust without an encapsulating model.
5. **How does `SecretStr` prevent data leaks?** Its `__repr__` and `model_dump()` mask values as `'**********'`, requiring an explicit `.get_secret_value()` call to access the secret.
