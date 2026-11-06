# Day 53: Pydantic V2: Data Validation, Custom Field Types & Serialization

Welcome to Day 53 of the Enterprise Backend & AI Engineering curriculum. Today, we step into the core architecture of **Pydantic V2**, the foundational data validation, parsing, and serialization engine powering modern Python backend frameworks (FastAPI, Litestar), AI orchestration frameworks (LangChain, LlamaIndex, Instructor), and high-throughput data pipelines.

Pydantic V2 represents a monumental generational rewrite: the core validation logic was moved from pure Python into **`pydantic-core`**, a compiled Rust library operating directly on Python's C-API. This rewrite delivered a 5x to 50x throughput improvement while overhauling validation lifecycles, serialization controls, and type adaptability.

This guide provides an exhaustive 15-point architectural breakdown of Pydantic V2 internals, custom validation pipelines, modern PEP 593 field typing, and high-performance serialization patterns.

---

## 1. The Pydantic V2 Revolution: From Pure Python to Rust `pydantic-core`

In Pydantic V1, models were validated through dynamic Python metaclasses, runtime `getattr`/`setattr` inspections, and recursive Python loops. Under high-throughput API workloads, Python's bytecode evaluation and dictionary allocation created a severe performance bottleneck.

Pydantic V2 completely separates the **user-facing Python API** from the **underlying validation execution engine**:

```
+---------------------------------------------------------------------------------+
|                                Python Front-End                                 |
|  - BaseModel Declarations                                                       |
|  - @field_validator, @model_validator, @field_serializer                        |
|  - Annotated[T, Field(...), AfterValidator(...)]                                |
+---------------------------------------------------------------------------------+
                                       |
                   Builds CoreSchema AST during module import
                                       v
+---------------------------------------------------------------------------------+
|                       pydantic-core (Compiled Rust Engine)                      |
|                                                                                 |
|  +---------------------------+             +---------------------------------+  |
|  |     SchemaValidator       |             |        SchemaSerializer         |  |
|  | - Direct JSON/Dict Parser |             | - Direct JSON/Dict Dumper       |  |
|  | - Recursive Type Matching |             | - High-speed Rust Stringifier   |  |
|  | - Zero Intermediate Dicts |             | - Exclude/Include Filter Tree   |  |
|  +---------------------------+             +---------------------------------+  |
|                                      |                                          |
|                              Direct C-API Calls                                 |
|                                      v                                          |
|                          [ Python C-API / Memory ]                              |
+---------------------------------------------------------------------------------+
```

### The Architectural Shift
1. **Compilation Step**: When a `BaseModel` class is created in Python, Pydantic traverses annotations and validators to compile an internal dictionary tree called a **`CoreSchema`**.
2. **SchemaValidator Instantiation**: The `CoreSchema` is passed down to Rust via PyO3, which compiles it into a native Rust struct hierarchy (`SchemaValidator`).
3. **Zero Python Bytecode Overhead**: When validating incoming data (especially raw JSON bytes), `pydantic-core` parses JSON directly in Rust memory without allocating intermediate Python dictionaries, achieving speeds competitive with Go and Java parsers.

---

## 2. The Two-Tier Architecture: Python Front-End vs Rust Back-End

Understanding the boundary between Python and Rust is vital for optimizing enterprise models:

| Component | Responsibility | Written In | Execution Timing |
| :--- | :--- | :--- | :--- |
| **`pydantic`** | Syntax definition, PEP 484/593 type inspection, plugin hooks, error formatting | Python | Module load time (once) |
| **`CoreSchema`** | Intermediate Representation (IR) describing validation rules and serialization graphs | Python Dict | Model definition time |
| **`SchemaValidator`** | Type coercion, string parsing, regex checking, bounds verification | Rust (`pydantic-core`) | Per-request runtime |
| **`SchemaSerializer`** | Filtering (`exclude_none`, `exclude_unset`), formatting dates/UUIDs, emitting JSON | Rust (`pydantic-core`) | Per-request runtime |

Because validation execution happens in compiled Rust, custom Python validators (`@field_validator`) actually involve context switches across the C-API boundary back into the Python interpreter. Therefore, native Pydantic types (`int`, `str`, `EmailStr`, `conint`) are significantly faster than custom Python validator functions.

---

## 3. Pydantic V1 vs V2 Breaking Changes Migration Matrix

Pydantic V2 introduced several intentional breaking changes to clean up syntax, improve type safety, and eliminate ambiguity:

| Feature | Pydantic V1 (Deprecated / Legacy) | Pydantic V2 (Modern Standard) |
| :--- | :--- | :--- |
| **Model Configuration** | Inner `class Config:` | `model_config = ConfigDict(...)` |
| **Field Validation** | `@validator("field")` | `@field_validator("field", mode="after")` |
| **Root/Model Validation** | `@root_validator(pre=True)` | `@model_validator(mode="before")` |
| **Export to Dict** | `model.dict()` | `model.model_dump()` |
| **Export to JSON** | `model.json()` | `model.model_dump_json()` |
| **Parse Object/Dict** | `Model.parse_obj(data)` | `Model.model_validate(data)` |
| **Parse Raw JSON** | `Model.parse_raw(json_str)` | `Model.model_validate_json(json_bytes_or_str)` |
| **ORM / Attribute Mode** | `orm_mode = True` | `ConfigDict(from_attributes=True)` |
| **Arbitrary Types** | `arbitrary_types_allowed = True` | `ConfigDict(arbitrary_types_allowed=True)` |
| **Custom Data Types** | Subclassing with `__get_validators__` | `Annotated[T, AfterValidator(...)]` or `__get_pydantic_core_schema__` |

---

## 4. Validation Modes: Strict vs Lax Mode

In Pydantic V1, type coercion was aggressive and sometimes unexpected (e.g. the string `"false"` or the integer `0` coerced into booleans, `"123"` coerced to `123`).

Pydantic V2 introduces a formal dual-mode validation contract:

### A. Lax Mode (Default)
Permits safe, predictable type coercions necessary for web protocols where all URL parameters and form fields arrive as strings:
- String `"42"` $\implies$ Integer `42`
- Float `42.0` $\implies$ Integer `42`
- String `"true"` / `"1"` $\implies$ Boolean `True`

### B. Strict Mode (`strict=True`)
Completely disallows type coercion. The input data type must exactly match the declared Python type:
- String `"42"` $\implies$ **Fails** with `ValidationError` when expecting `int`.
- Float `42.0` $\implies$ **Fails** when expecting `int`.

### Configuring Strict Mode
```python
from pydantic import BaseModel, ConfigDict, Field

# 1. Model-wide strictness
class SecureAuditLog(BaseModel):
    model_config = ConfigDict(strict=True)
    user_id: int
    is_admin: bool

# 2. Field-level strictness
class MixedModel(BaseModel):
    lax_count: int                        # Permits "10"
    strict_count: int = Field(strict=True) # Rejects "10", accepts only 10

# 3. Invocation-level strictness
data = {"user_id": "99", "is_admin": True}
# Force strict validation on a normally lax model:
# model = SecureAuditLog.model_validate(data, strict=True)
```

---

## 5. Field Validation Lifecycle (`@field_validator`)

Field validators inspect and mutate individual field values. Pydantic V2 provides three distinct execution modes:

```
[ Raw Input Data ]
        |
        v
+-------------------------------+
| mode='before' Validator       |  Runs on raw unconverted input (e.g., raw str before int parsing)
+-------------------------------+
        |
        v
+-------------------------------+
| pydantic-core Parsing         |  Rust core coerces types, enforces regex, bounds (min/max)
+-------------------------------+
        |
        v
+-------------------------------+
| mode='after' Validator        |  Runs on validated, typed Python objects (Default)
+-------------------------------+
        |
        v
[ Final Model Field Value ]
```

### Validator Implementation
```python
from pydantic import BaseModel, field_validator, ValidationInfo

class Transaction(BaseModel):
    currency: str
    amount: float

    # mode='before': Clean raw input before type parsing
    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency_code(cls, value: any) -> str:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    # mode='after': Enforce domain business rules on validated types
    @field_validator("amount", mode="after")
    @classmethod
    def validate_positive_amount(cls, value: float, info: ValidationInfo) -> float:
        if value <= 0.0:
            raise ValueError(f"Transaction amount must be strictly positive. Got: {value}")
        return round(value, 2)
```

### What is `ValidationInfo`?
`info: ValidationInfo` provides access to:
- `info.data`: Dictionary of previously validated sibling fields.
- `info.context`: Arbitrary runtime context passed to `model_validate(data, context={"tenant": "alpha"})`.
- `info.mode`: `'python'` or `'json'`.

---

## 6. Model-Level Validation (`@model_validator`)

When a validation rule spans multiple interdependent fields (cross-field validation), use `@model_validator`.

### Mode: `'before'` (Pre-Parsing Transformation)
Operates on the raw input dictionary before any field has been validated or coerced:

```python
from pydantic import BaseModel, model_validator
from typing import Any, Dict

class AuthenticationRequest(BaseModel):
    username: str
    password: str

    @model_validator(mode="before")
    @classmethod
    def preprocess_legacy_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Backward-compatibility: map legacy 'user' key to 'username'
            if "user" in data and "username" not in data:
                data["username"] = data.pop("user")
        return data
```

### Mode: `'after'` (Post-Validation Invariants)
Operates on the initialized instance (`self`), where all individual fields have already passed their individual type checks:

```python
from datetime import date
from pydantic import BaseModel, model_validator

class DateRange(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def verify_date_ordering(self) -> "DateRange":
        if self.end_date < self.start_date:
            raise ValueError(
                f"end_date ({self.end_date}) cannot be earlier than start_date ({self.start_date})"
            )
        return self
```

---

## 7. Custom Types via `typing.Annotated` & Functional Validators

In Pydantic V1, creating a reusable validated type required subclassing a primitive and implementing the obscure `__get_validators__` generator protocol.

Pydantic V2 standardizes on **PEP 593 `typing.Annotated`** combined with functional validator markers:
- `BeforeValidator(func)`: Pre-processing.
- `AfterValidator(func)`: Post-validation assertion.
- `PlainValidator(func)`: Replaces core validation entirely.
- `WrapValidator(func)`: Surrounds core validation with custom error interception.

```python
from typing import Annotated
from pydantic import BaseModel, AfterValidator, BeforeValidator

def strip_whitespace(v: str) -> str:
    return v.strip() if isinstance(v, str) else v

def validate_sku_format(v: str) -> str:
    if not v.startswith("SKU-"):
        raise ValueError("Product SKU must begin with prefix 'SKU-'")
    return v

# Composable, reusable domain type aliases
CleanString = Annotated[str, BeforeValidator(strip_whitespace)]
ProductSKU = Annotated[CleanString, AfterValidator(validate_sku_format)]

class InventoryItem(BaseModel):
    sku: ProductSKU  # Automatically strips whitespace THEN validates prefix!
    name: CleanString
```

---

## 8. Dynamic Computed Fields (`@computed_field`)

In API microservices, domain models often calculate synthetic metrics (e.g., `full_name`, `tax_amount`, `profit_margin`) derived from stored fields.

In Pydantic V1, standard Python `@property` methods were invisible to `.dict()` and OpenAPI schemas. Pydantic V2 introduces **`@computed_field`**:

```python
from pydantic import BaseModel, computed_field

class InvoiceLine(BaseModel):
    unit_price: float
    quantity: int
    tax_rate: float = 0.10

    @computed_field
    @property
    def subtotal(self) -> float:
        return round(self.unit_price * self.quantity, 2)

    @computed_field
    @property
    def total_with_tax(self) -> float:
        return round(self.subtotal * (1.0 + self.tax_rate), 2)

line = InvoiceLine(unit_price=49.99, quantity=3)
# Subtotal and total_with_tax are automatically calculated and serialized!
print(line.model_dump())
# {'unit_price': 49.99, 'quantity': 3, 'tax_rate': 0.1, 'subtotal': 149.97, 'total_with_tax': 164.97}
```

---

## 9. Advanced Serialization: `@field_serializer` & `@model_serializer`

Serialization controls how Python model instances are converted into JSON primitives or wire strings.

### `@field_serializer`
Customizes the outbound representation of specific attributes:

```python
from datetime import datetime, timezone
from pydantic import BaseModel, field_serializer

class AuditRecord(BaseModel):
    event: str
    timestamp: datetime

    # Format datetime as epoch timestamp exclusively when dumping to JSON
    @field_serializer("timestamp", when_used="json")
    def serialize_timestamp_to_epoch(self, ts: datetime) -> int:
        return int(ts.replace(tzinfo=timezone.utc).timestamp())
```

### Options for `when_used`:
- `'always'`: Runs during both `model_dump()` and `model_dump_json()`.
- `'json'`: Runs only during JSON serialization (`model_dump_json()` or `model_dump(mode='json')`).
- `'unless-none'`: Bypasses serialization if the field value is `None`.

### `@model_serializer`
Completely overrides the outbound structure of the model (e.g. flattening or wrapping):
```python
from pydantic import BaseModel, model_serializer

class KeyValueSecret(BaseModel):
    key: str
    value: str

    @model_serializer
    def serialize_as_single_dict(self) -> dict:
        return {self.key: self.value}
```

---

## 10. The Power of `TypeAdapter`

`TypeAdapter` enables Pydantic V2 validation and serialization on **arbitrary Python types** without requiring an encapsulating `BaseModel`:

```python
from typing import List, Dict
from pydantic import TypeAdapter

# Validate a raw top-level list of integers
int_list_adapter = TypeAdapter(List[int])
validated_ints = int_list_adapter.validate_python(["1", 2, "3", 4.0])
# [1, 2, 3, 4]

# Serialize complex structures directly to JSON bytes
json_bytes = int_list_adapter.dump_json(validated_ints)
# b'[1,2,3,4]'
```

### Production Best Practice: Caching
Creating a `TypeAdapter` instance triggers schema compilation in Rust. **Always instantiate `TypeAdapter` once at module scope**, never inside request loops!

---

## 11. Discriminated (Tagged) Unions for Polymorphic Payloads

In event streams, message queues, and REST webhooks, an endpoint often receives payloads of varying structures identified by a discriminator tag (e.g., `"event_type"`):

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field

class CardPayment(BaseModel):
    payment_type: Literal["credit_card"]
    card_number: str
    cvv: str

class BankTransfer(BaseModel):
    payment_type: Literal["bank_wire"]
    iban: str
    bic: str

class CryptoPayment(BaseModel):
    payment_type: Literal["crypto"]
    wallet_address: str
    blockchain: str

# Discriminated Union using payment_type as discriminator
PaymentPayload = Annotated[
    Union[CardPayment, BankTransfer, CryptoPayment],
    Field(discriminator="payment_type")
]

class CheckoutSession(BaseModel):
    session_id: str
    payment: PaymentPayload
```

### Why Discriminated Unions Win on Performance
Standard unions (`Union[A, B, C]`) validate by trial-and-error: Pydantic attempts parsing against `A`, then `B`, then `C`, collecting errors on failures. 

A **Discriminated Union** inspects `payment_type` in $O(1)$ time and branches directly into the correct Rust validator, speeding up polymorphic validation by **orders of magnitude**.

---

## 12. Immutability & Domain Hashability (`frozen=True`)

In Domain-Driven Design (DDD), **Value Objects** must be immutable and comparable by value:

```python
from pydantic import BaseModel, ConfigDict

class Money(BaseModel):
    model_config = ConfigDict(frozen=True)  # Makes instance immutable and hashable!

    amount: float
    currency: str = "USD"

m1 = Money(amount=100.0, currency="USD")
m2 = Money(amount=100.0, currency="USD")

# Hashable: Can be used in Python sets or as dictionary keys!
wallets = {m1: "Primary Account"}

# Immutable: Mutating raises an error!
# m1.amount = 200.0  # Raises ValidationError: Instance is frozen
```

---

## 13. Data Masking & Secret Types (`SecretStr`, `SecretBytes`)

To prevent sensitive credentials (passwords, tokens, API keys) from accidentally being logged, serialized into Sentry traces, or printed to stdout:

```python
from pydantic import BaseModel, SecretStr

class DatabaseCredentials(BaseModel):
    username: str
    password: SecretStr

creds = DatabaseCredentials(username="app_user", password="SuperSecretPassword123!")

# 1. Printing masks value
print(creds)
# DatabaseCredentials(username='app_user', password=SecretStr('**********'))

# 2. model_dump() masks value
print(creds.model_dump())
# {'username': 'app_user', 'password': SecretStr('**********')}

# 3. Explicit retrieval when connecting to DB:
raw_password = creds.password.get_secret_value()
# "SuperSecretPassword123!"
```

---

## 14. Custom Core Schemas (`__get_pydantic_core_schema__`)

For enterprise libraries creating custom primitives (e.g., custom IPv6 wrappers, cryptographic hashes, or database IDs), you can hook directly into the Rust schema generator:

```python
from typing import Any
from pydantic_core import core_schema
from pydantic import GetCoreSchemaHandler

class HexColor:
    def __init__(self, value: str):
        self.value = value

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.chain_schema([
            core_schema.str_schema(),
            core_schema.no_info_plain_validator_function(cls._validate),
        ])

    @classmethod
    def _validate(cls, value: str) -> "HexColor":
        if not value.startswith("#") or len(value) not in (4, 7):
            raise ValueError(f"Invalid Hex Color string: {value}")
        return cls(value)

    def __repr__(self) -> str:
        return f"HexColor('{self.value}')"
```

---

## 15. Performance Best Practices & Production Checklist

1. **Direct JSON Parsing**: Always use `Model.model_validate_json(raw_bytes)` instead of `Model.model_validate(json.loads(raw_str))`. `model_validate_json` executes entirely inside Rust and avoids creating intermediary Python dictionaries.
2. **Reuse `TypeAdapter`**: Never instantiate `TypeAdapter(...)` inside a loop or request handler. Define it once as a module singleton.
3. **Use Discriminated Unions**: Never declare bare unions (`Union[A, B, C]`) if the data model has a type tag; always use `Field(discriminator="...")`.
4. **Selective Dumps**: Use `model.model_dump(include={...}, exclude={...})` or `exclude_none=True` to minimize memory allocations when converting models into dictionaries.
5. **Enforce `strict=True` for Ingestion Pipelines**: For internal microservice-to-microservice RPCs where types are guaranteed, use `strict=True` to bypass coercion branches in Rust.

---
*Next Step: Explore `example_01.py` for a fully functional enterprise Pydantic V2 pipeline, and `example_02.py` for comprehensive throughput and serialization benchmarks.*
