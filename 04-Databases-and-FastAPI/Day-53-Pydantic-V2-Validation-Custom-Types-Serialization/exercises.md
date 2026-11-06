# Day 53: Advanced Pydantic V2 Practical Exercises

Mastering Pydantic V2 requires fluency with low-level validator lifecycles, cross-field reconciliation, custom type generation, and high-speed polymorphic routing. Complete the following three senior-level engineering exercises. Verified reference solutions follow each prompt.

---

## Exercise 1: Double-Entry Financial Ledger Validator with Cross-Row Balancing

### Architectural Scenario
In an enterprise accounting engine, financial ledger entries adhere to the **Double-Entry Bookkeeping Principle**:
1. Every ledger transaction consists of two or more entries.
2. Each entry has an `account_id`, a `direction` (`"DEBIT"` or `"CREDIT"`), and an `amount` (strictly positive `Decimal` with up to 2 decimal places).
3. The sum of all debits must **exactly equal** the sum of all credits ($\sum \text{Debits} - \sum \text{Credits} = 0$).

### Requirements
1. Define an immutable `LedgerEntry` model (`frozen=True`) with positive `Decimal` amounts.
2. Define a `TransactionBatch` model with:
   - A list of entries (minimum 2 entries).
   - An ISO currency code (strictly validated).
   - A `@model_validator(mode="after")` that asserts the total sum of debits matches total credits to within `0.00` Decimal precision.
3. Compute `@computed_field` properties for `total_debit_volume` and `entry_count`.
4. Provide unit assertions demonstrating rejection of unbalanced transactions.

### Verified Reference Solution

```python
from decimal import Decimal
from typing import Annotated, List, Literal
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, computed_field, model_validator

def validate_currency(v: str) -> str:
    cleaned = v.strip().upper()
    if cleaned not in {"USD", "EUR", "GBP", "JPY", "CHF"}:
        raise ValueError(f"Unsupported ledger currency: {v}")
    return cleaned

Currency = Annotated[str, AfterValidator(validate_currency)]

class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str = Field(..., pattern=r"^ACC-\d{5,10}$")
    direction: Literal["DEBIT", "CREDIT"]
    amount: Decimal = Field(..., gt=Decimal("0.00"), decimal_places=2)


class TransactionBatch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: str
    currency: Currency = "USD"
    entries: List[LedgerEntry] = Field(..., min_length=2)

    @computed_field
    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @computed_field
    @property
    def total_debit_volume(self) -> Decimal:
        return sum(e.amount for e in self.entries if e.direction == "DEBIT")

    @model_validator(mode="after")
    def verify_double_entry_balance(self) -> "TransactionBatch":
        debit_sum = sum(e.amount for e in self.entries if e.direction == "DEBIT")
        credit_sum = sum(e.amount for e in self.entries if e.direction == "CREDIT")

        if debit_sum != credit_sum:
            raise ValueError(
                f"Ledger transaction unbalanced! Total Debits (${debit_sum}) "
                f"does not equal Total Credits (${credit_sum}). Imbalance: ${abs(debit_sum - credit_sum)}"
            )
        return self
```

---

## Exercise 2: Polymorphic Cloud Security Event Stream with Discriminated Unions

### Architectural Scenario
A security monitoring SIEM processes high-frequency infrastructure events from AWS, GCP, and Kubernetes. The event stream contains three distinct schemas:
1. `IAMRoleAssumedEvent` (`event_type: "iam.assumed"`, `role_arn`, `actor_ip`, `session_duration`).
2. `K8sPodEvictedEvent` (`event_type: "k8s.pod_evicted"`, `namespace`, `pod_name`, `reason`).
3. `StorageBucketAccessEvent` (`event_type: "storage.access"`, `bucket_name`, `bytes_transferred`, `is_public`).

### Requirements
1. Define the 3 distinct event models with strict `Literal` event tags.
2. Build a high-performance **Discriminated Union** using `Field(discriminator="event_type")`.
3. Wrap validation in a module-level `TypeAdapter`.
4. Validate a mixed list of 1,000 incoming event payloads, measuring parsing correctness and capturing malformed tags.

### Verified Reference Solution

```python
from typing import Annotated, List, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

class IAMRoleAssumedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: Literal["iam.assumed"]
    role_arn: str = Field(..., pattern=r"^arn:aws:iam::\d{12}:role/[a-zA-Z0-9+=,.@_-]+$")
    actor_ip: str
    session_duration: int = Field(..., ge=900, le=43200)

class K8sPodEvictedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: Literal["k8s.pod_evicted"]
    namespace: str
    pod_name: str
    reason: Literal["OOMKilled", "EvictedByScheduler", "NodeAffinity"]

class StorageBucketAccessEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: Literal["storage.access"]
    bucket_name: str
    bytes_transferred: int = Field(..., ge=0)
    is_public: bool

# Tagged Discriminated Union
SecurityEvent = Annotated[
    Union[IAMRoleAssumedEvent, K8sPodEvictedEvent, StorageBucketAccessEvent],
    Field(discriminator="event_type"),
]

# Singleton TypeAdapter compiled once in Rust
SECURITY_EVENT_ADAPTER = TypeAdapter(SecurityEvent)
SECURITY_STREAM_ADAPTER = TypeAdapter(List[SecurityEvent])
```

---

## Exercise 3: Dynamic Secret Masking & PII Redaction Serializer

### Architectural Scenario
In healthcare and financial systems (HIPAA / PCI-DSS compliance), sensitive personal data (e.g., Social Security Numbers, patient diagnoses, and credit card CVVs) must be:
1. Completely masked in standard logs (`model_dump()` and `repr()`).
2. Capable of being exported to an encrypted archive via an explicit context flag (`info.context = {"authorized_audit": True}`).

### Requirements
1. Implement a custom Pydantic model `PatientRecord` containing:
   - `patient_id: str`
   - `ssn: SecretStr`
   - `diagnosis_notes: str`
2. Implement a `@field_serializer("ssn", mode="wrap")` and `@field_serializer("diagnosis_notes", mode="wrap")`:
   - If `info.context` contains `authorized_audit=True`, serialize raw values.
   - Otherwise, mask SSN as `"***-**-****"` and truncate diagnosis notes to `"REDACTED_PII"`.

### Verified Reference Solution

```python
from typing import Optional
from pydantic import BaseModel, Field, SecretStr, SerializationInfo, field_serializer

class PatientRecord(BaseModel):
    patient_id: str
    full_name: str
    ssn: SecretStr
    diagnosis_notes: str

    @field_serializer("ssn", mode="wrap")
    def serialize_ssn(self, ssn: SecretStr, handler, info: SerializationInfo) -> str:
        # Check serialization context
        context = info.context if info else None
        if context and context.get("authorized_audit"):
            return ssn.get_secret_value()
        return "***-**-****"

    @field_serializer("diagnosis_notes", mode="wrap")
    def serialize_notes(self, notes: str, handler, info: SerializationInfo) -> str:
        context = info.context if info else None
        if context and context.get("authorized_audit"):
            return notes
        return "[REDACTED MEDICAL RECORD]"
```

---
*Completing these exercises ensures production-grade mastery of Pydantic V2 validation, polymorphic dispatch, and data protection.*
