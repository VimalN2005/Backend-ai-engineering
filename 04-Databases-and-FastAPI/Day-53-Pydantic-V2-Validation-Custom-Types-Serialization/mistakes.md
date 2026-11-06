# Day 53: Critical Pydantic V2 Antipatterns & Architectural Pitfalls

This document explores the top 7 architectural mistakes engineers make when transitioning to and developing with **Pydantic V2**. Each antipattern includes its underlying failure mechanism, bad code vs remediated code, and production rules.

---

## 1. Using Deprecated V1 Methods in V2 Codebases

### Failure Mechanism
In Pydantic V2, legacy V1 methods (`.dict()`, `.json()`, `.parse_obj()`, `.parse_raw()`, and inner `class Config:`) are maintained only as a thin backward-compatibility shim. 

Using them:
- Triggers runtime `PydanticDeprecatedSince20` deprecation warnings on every request.
- Bypasses the compiled Rust `pydantic-core` fast paths, routing calls through slower Python wrapper shims.
- In future minor/major releases, these shims will be removed entirely.

### Bad Practice
```python
# DEPRECATED V1 SYNTAX IN V2
class User(BaseModel):
    name: str
    class Config:
        orm_mode = True

user = User.parse_obj({"name": "Alice"})
data_dict = user.dict()
json_bytes = user.json()
```

### Remediated Practice
```python
# MODERN IDIOMATIC V2 SYNTAX
from pydantic import BaseModel, ConfigDict

class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str

user = User.model_validate({"name": "Alice"})
data_dict = user.model_dump()
json_bytes = user.model_dump_json()
```

---

## 2. Omitting `@classmethod` on Field & Model Validators

### Failure Mechanism
In Pydantic V2:
- `@field_validator` methods **must** be `@classmethod`s.
- `@model_validator(mode="before")` methods **must** be `@classmethod`s because the model instance has not yet been allocated in memory.

If you forget `@classmethod`, Python binds the method as an instance method. When Pydantic core calls the validator during parsing, the first argument is passed as the value rather than `cls`, causing confusing `TypeError: validator() missing 1 required positional argument` or invalid state corruption.

### Bad Practice
```python
class Account(BaseModel):
    account_number: str

    # BUG: Missing @classmethod decorator!
    @field_validator("account_number")
    def validate_account(self, value: str) -> str:
        return value.strip()
```

### Remediated Practice
```python
class Account(BaseModel):
    account_number: str

    @field_validator("account_number", mode="after")
    @classmethod
    def validate_account(cls, value: str) -> str:
        return value.strip()
```

---

## 3. Attempting to Mutate Sibling Fields via `values` / `info.data`

### Failure Mechanism
In Pydantic V1, `@validator` accepted a `values` dictionary containing previously validated fields, and developers frequently mutated `values` in-place.

In Pydantic V2, `info.data` inside `ValidationInfo` is a **read-only mapping**. Attempting to assign `info.data["other_field"] = "new"` raises a `TypeError: 'dict' object does not support item assignment` or silently fails. Sibling fields cannot be altered from inside an individual `@field_validator`.

### Bad Practice
```python
class Registration(BaseModel):
    password: str
    password_hash: Optional[str] = None

    @field_validator("password")
    @classmethod
    def hash_password(cls, v: str, info: ValidationInfo) -> str:
        # BUG: info.data is immutable in V2!
        info.data["password_hash"] = compute_hash(v)
        return v
```

### Remediated Practice
```python
class Registration(BaseModel):
    password: str
    password_hash: Optional[str] = None

    # Use @model_validator(mode="after") for cross-field mutations
    @model_validator(mode="after")
    def hash_password(self) -> "Registration":
        self.password_hash = compute_hash(self.password)
        return self
```

---

## 4. Using Bare `Union` Instead of Discriminated Union for Polymorphism

### Failure Mechanism
When parsing polymorphic models with a bare `Union[TypeA, TypeB, TypeC]`:
1. **$O(N)$ Backtracking**: Pydantic validates sequentially against `TypeA`. If it fails, it rolls back and tries `TypeB`. If that fails, it tries `TypeC`.
2. **Greedy Matching Bug**: If `TypeA` has loose typing (e.g., all fields optional or permissive coercions), incoming `TypeB` data may partially match `TypeA`, yielding the **wrong type** without an error!

### Bad Practice
```python
class EmailNotification(BaseModel):
    channel: str = "email"
    email_address: str

class SMSNotification(BaseModel):
    channel: str = "sms"
    phone_number: str

# BUG: O(N) trial-and-error parsing, prone to greedy mismatches!
NotificationPayload = Union[EmailNotification, SMSNotification]
```

### Remediated Practice
```python
from typing import Annotated, Literal, Union
from pydantic import Field

class EmailNotification(BaseModel):
    channel: Literal["email"]
    email_address: str

class SMSNotification(BaseModel):
    channel: Literal["sms"]
    phone_number: str

# Discriminated Union: O(1) instantaneous Rust routing via 'channel' tag
NotificationPayload = Annotated[
    Union[EmailNotification, SMSNotification],
    Field(discriminator="channel"),
]
```

---

## 5. Instantiating `TypeAdapter` Inside Request Handlers or Loops

### Failure Mechanism
Creating a `TypeAdapter` triggers compiled `SchemaValidator` compilation in the Rust backend. 

If you instantiate `TypeAdapter(List[MyModel])` inside an API endpoint or a loop processing 10,000 records, you force Pydantic to re-compile the Rust AST on **every single request**, turning a 1-millisecond endpoint into a 50-millisecond CPU hog.

### Bad Practice
```python
@app.post("/batch-import")
async def batch_import(items: list[dict]):
    # CATASTROPHIC OVERHEAD: Compiles SchemaValidator on every HTTP request!
    adapter = TypeAdapter(List[InventoryItem])
    return adapter.validate_python(items)
```

### Remediated Practice
```python
# Instantiate once at MODULE SCOPE as a singleton!
INVENTORY_LIST_ADAPTER = TypeAdapter(List[InventoryItem])

@app.post("/batch-import")
async def batch_import(items: list[dict]):
    # Reuses pre-compiled Rust validator in sub-millisecond time
    return INVENTORY_LIST_ADAPTER.validate_python(items)
```

---

## 6. Expecting `@property` to Serialize Without `@computed_field`

### Failure Mechanism
In standard Python classes, `@property` creates a getter. In Pydantic V1, properties were completely ignored by `.dict()`. Developers in V2 often assume `@property` methods will be included in `model_dump()`.

Without `@computed_field`, properties are **invisible** to `model.model_dump()`, `model.model_dump_json()`, and FastAPI's OpenAPI JSON Schema!

### Bad Practice
```python
class Rectangle(BaseModel):
    width: float
    height: float

    # INVISIBLE: Omitted from model_dump() and OpenAPI docs!
    @property
    def area(self) -> float:
        return self.width * self.height

r = Rectangle(width=10.0, height=5.0)
print(r.model_dump())  # {'width': 10.0, 'height': 5.0}  <-- 'area' IS MISSING!
```

### Remediated Practice
```python
from pydantic import computed_field

class Rectangle(BaseModel):
    width: float
    height: float

    @computed_field
    @property
    def area(self) -> float:
        return self.width * self.height

r = Rectangle(width=10.0, height=5.0)
print(r.model_dump())  # {'width': 10.0, 'height': 5.0, 'area': 50.0} <-- Included!
```

---

## 7. Relying on Lax Coercion for String Identifiers and Numeric Codes

### Failure Mechanism
In default Lax mode, Pydantic coerces integers to strings and strings to integers. 

If an entity has an account code, zip code, or social security number defined as `account_code: int`:
- Input `"00123"` is coerced to integer `123`, **permanently losing the leading zeros**!
- If defined as `code: str`, an integer `12345` is silently coerced to `"12345"`, masking client bugs.

### Bad Practice
```python
class BankAccount(BaseModel):
    # DANGEROUS: "0045" coerces to integer 45!
    routing_number: int
```

### Remediated Practice
```python
from pydantic import Field
from typing import Annotated

RoutingNumber = Annotated[str, Field(pattern=r"^\d{9}$", strict=True)]

class BankAccount(BaseModel):
    # Strict string preserves exact formatting and leading zeros
    routing_number: RoutingNumber
```

---
*Following these remediations ensures maximum throughput, strict type safety, and zero unexpected data mutations in Pydantic V2.*
