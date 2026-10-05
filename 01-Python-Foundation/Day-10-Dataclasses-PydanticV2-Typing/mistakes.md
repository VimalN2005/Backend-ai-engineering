# Day 10: Common Dataclass & Pydantic V2 Antipatterns

Avoid these 7 production bugs when modeling domain entities, building FastAPI endpoints, or validating LLM tool calls.

---

### 1. Mutable Default Arguments in Dataclasses
- **Root Cause**: Defining `tags: list = []` directly in a dataclass definition.
- **Consequence**: CPython creates one list instance at class definition time. Mutating the list on one instance mutates it across all instances, causing catastrophic cross-tenant data leaks.

```python
# ❌ FATAL ANTIPATTERN: Shared mutable default across instances
from dataclasses import dataclass

@dataclass
class DocumentMetadata:
    tags: list[str] = []  # Python raises ValueError: mutable default is not allowed

# ✅ PRODUCTION PATTERN: Use field(default_factory=list)
from dataclasses import dataclass, field

@dataclass
class DocumentMetadata:
    tags: list[str] = field(default_factory=list)
```

---

### 2. Using Deprecated Pydantic V1 Syntax in V2 Projects
- **Root Cause**: Using V1 methods (`.dict()`, `.json()`, `.parse_raw()`, `@validator`, `@root_validator`).
- **Consequence**: In Pydantic V2, V1 methods are either deprecated with runtime performance penalties or raise attribute errors. They bypass the Rust-powered `pydantic-core` engine.

| Deprecated Pydantic V1 Method | Modern Pydantic V2 Replacement |
| :--- | :--- |
| `model.dict()` | `model.model_dump()` |
| `model.json()` | `model.model_dump_json()` |
| `Model.parse_obj(data)` | `Model.model_validate(data)` |
| `Model.parse_raw(json_str)` | `Model.model_validate_json(json_bytes)` |
| `@validator("field")` | `@field_validator("field", mode="after")` |
| `@root_validator` | `@model_validator(mode="after")` |

---

### 3. Relying on Lax Mode at Strict Security/Financial Boundaries
- **Root Cause**: Assuming Pydantic will reject mismatched types by default. In default lax mode, Pydantic silently coerces string `"100"` to integer `100`, `"true"` to boolean `True`, and float `5.0` to integer `5`.
- **Consequence**: Malicious or malformed inputs slip past API boundaries without proper client contract enforcement.

```python
# ❌ DANGEROUS: Silently coerces string "100" to int 100
class TransferFunds(BaseModel):
    account_id: int
    amount_cents: int

# ✅ PRODUCTION PATTERN: Enforce strict=True at boundary models
class TransferFunds(BaseModel):
    model_config = ConfigDict(strict=True)

    account_id: int
    amount_cents: int
```

---

### 4. Direct Mutation of Frozen Instances Instead of `model_copy`
- **Root Cause**: Attempting to modify attributes on a frozen model (`model_config = ConfigDict(frozen=True)`).
- **Consequence**: Raises `ValidationError: Instance is frozen`.

```python
# ❌ CRASHES: Attempting direct mutation on frozen model
user = StrictUser(id=1, email="old@corp.internal")
user.email = "new@corp.internal"  # Raises ValidationError

# ✅ PRODUCTION PATTERN: Return a new modified instance using model_copy(update=...)
updated_user = user.model_copy(update={"email": "new@corp.internal"})
```

---

### 5. Forgetting `mode="before"` When Sanitizing Raw Inputs
- **Root Cause**: Writing a `@field_validator` that expects raw unstructured data (e.g., stripping strings, splitting commas), but using the default `mode="after"`.
- **Consequence**: If the raw data does not match the target type (e.g. passing a string `"tag1,tag2"` for a field typed as `list[str]`), Pydantic fails with a type error *before* the validator ever executes.

```python
# ❌ FAILS: Type validation fails before validator executes
class Article(BaseModel):
    tags: list[str]

    @field_validator("tags")  # Defaults to mode="after"
    @classmethod
    def parse_csv_tags(cls, v):
        if isinstance(v, str):
            return v.split(",")  # Never reached if raw input is a string!
        return v

# ✅ PRODUCTION PATTERN: Explicitly use mode="before" for raw data normalization
class Article(BaseModel):
    tags: list[str]

    @field_validator("tags", mode="before")
    @classmethod
    def parse_csv_tags(cls, v):
        if isinstance(v, str):
            return [tag.strip() for tag in v.split(",")]
        return v
```

---

### 6. Instantiating `TypeAdapter` Inside Hot Loops
- **Root Cause**: Calling `TypeAdapter(list[int])` inside a request loop or message consumer handler.
- **Consequence**: Instantiating a `TypeAdapter` inspects Python type annotations and compiles a Rust validation schema on every invocation, drastically destroying throughput.

```python
# ❌ MASSIVE CPU BOTTLENECK: Compiles Rust schema on every iteration
def process_batches(batches: list[list[int]]):
    for batch in batches:
        adapter = TypeAdapter(list[int])  # High CPU compilation cost!
        adapter.validate_python(batch)

# ✅ HIGH-PERFORMANCE PATTERN: Instantiate TypeAdapter once at module scope
INT_LIST_ADAPTER = TypeAdapter(list[int])

def process_batches(batches: list[list[int]]):
    for batch in batches:
        INT_LIST_ADAPTER.validate_python(batch)  # Reuses compiled schema
```

---

### 7. Mutating Frozen Dataclasses in `__post_init__` Without `object.__setattr__`
- **Root Cause**: Setting attributes directly (`self.derived_field = ...`) inside `__post_init__` on a frozen dataclass.
- **Consequence**: Raises `dataclasses.FrozenInstanceError: cannot assign to field 'derived_field'`.

```python
# ❌ FAILS ON FROZEN DATACLASS
@dataclass(frozen=True)
class NormalizedVector:
    values: list[float]
    norm: float = 0.0

    def __post_init__(self):
        self.norm = sum(x**2 for x in self.values) ** 0.5  # Raises FrozenInstanceError!

# ✅ PRODUCTION PATTERN: Use object.__setattr__ to bypass frozen check during init
@dataclass(frozen=True)
class NormalizedVector:
    values: list[float]
    norm: float = 0.0

    def __post_init__(self):
        computed_norm = sum(x**2 for x in self.values) ** 0.5
        object.__setattr__(self, "norm", computed_norm)
```
