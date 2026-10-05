"""Day 10: Dataclasses, Pydantic V2 Models, and Strict Production Typing.

This module demonstrates core production patterns:
1. Standard Library Dataclasses with slots, immutability, and __post_init__.
2. Pydantic V2 BaseModel with Rust-powered validation and constraints.
3. Strict vs Lax Mode type coercion behaviors.
4. Field and Model Validators (mode="before" and mode="after").
5. Computed Fields and Zero-Copy JSON Deserialization.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)


# =====================================================================
# 1. ADVANCED PYTHON DATACLASSES (SLOTS, IMMUTABILITY & POST-INIT)
# =====================================================================
@dataclass(slots=True, frozen=True, kw_only=True)
class KnowledgeChunk:
    """High-performance immutable internal domain value object.

    WHY slots=True and frozen=True?
    - slots=True replaces dynamic __dict__ with a fixed C array, saving ~60% RAM.
    - frozen=True makes instances immutable, preventing accidental side-effects
      and automatically generating an __eq__ and __hash__ for set/dict storage.
    - kw_only=True enforces readable call sites: KnowledgeChunk(chunk_id=..., ...)
    """

    chunk_id: UUID = field(default_factory=uuid4)
    text_content: str
    tokens: int
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate state immediately upon initialization.

        WHY object.__setattr__?
        Because the dataclass is frozen=True, standard 'self.tokens = ...'
        raises FrozenInstanceError. To adjust or validate attributes during
        creation, object.__setattr__ must be used.
        """
        if self.tokens <= 0:
            raise ValueError(f"tokens must be positive, got {self.tokens}")
        if not self.text_content.strip():
            raise ValueError("text_content cannot be empty or whitespace")


# =====================================================================
# 2. PYDANTIC V2 BASEMODEL WITH FIELD CONSTRAINTS & COMPUTED FIELDS
# =====================================================================
class LLMModelPayload(BaseModel):
    """Production Pydantic V2 model for API boundaries and request parsing."""

    model_config = ConfigDict(
        str_strip_whitespace=True,  # Automatically trim leading/trailing whitespace
        extra="forbid",             # Reject unexpected fields from callers
    )

    request_id: UUID = Field(default_factory=uuid4, description="Unique trace request ID")
    model_name: str = Field(min_length=2, max_length=50, description="LLM model identifier")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling randomness")
    prompt: str = Field(min_length=1, max_length=50000, description="Input user prompt")
    stop_sequences: List[str] = Field(default_factory=list, max_length=4)

    # WHY @field_validator with mode="before"?
    # mode="before" executes BEFORE Pydantic verifies types.
    # It allows normalizing messy client payloads (e.g. converting uppercase strings
    # or stripping prefixes) before strict validation occurs.
    @field_validator("model_name", mode="before")
    @classmethod
    def normalize_model_name(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.lower().strip()
        return v

    # WHY @computed_field?
    # Exposes dynamically derived calculations in JSON exports and schemas
    # without storing duplicate state or computing at database query time.
    @computed_field  # type: ignore[misc]
    @property
    def estimated_prompt_tokens(self) -> int:
        return max(1, len(self.prompt) // 4)


# =====================================================================
# 3. STRICT MODE VS LAX MODE DEMONSTRATION
# =====================================================================
class LaxUserModel(BaseModel):
    user_id: int
    is_active: bool


class StrictUserModel(BaseModel):
    model_config = ConfigDict(strict=True)
    user_id: int
    is_active: bool


# =====================================================================
# 4. CROSS-FIELD MODEL VALIDATOR (mode="after")
# =====================================================================
class RateLimitWindowConfig(BaseModel):
    """Enforces cross-field interdependent consistency."""

    min_requests: int = Field(ge=1)
    max_requests: int = Field(ge=1)
    window_seconds: int = Field(ge=1, le=3600)

    # WHY @model_validator(mode="after")?
    # Runs after all individual fields have passed type validation.
    # Essential for verifying relationships between multiple fields.
    @model_validator(mode="after")
    def verify_request_boundaries(self) -> "RateLimitWindowConfig":
        if self.max_requests < self.min_requests:
            raise ValueError(
                f"max_requests ({self.max_requests}) cannot be less than min_requests ({self.min_requests})"
            )
        return self


# =====================================================================
# DEMONSTRATION RUNNER
# =====================================================================
def run_demonstrations() -> None:
    print("=" * 65)
    print("1. ADVANCED PYTHON DATACLASS (SLOTS + IMMUTABILITY)")
    print("=" * 65)
    chunk = KnowledgeChunk(
        text_content="Transformer attention mechanisms allow parallel sequence encoding.",
        tokens=11,
        tags=("ai", "nlp", "transformers"),
    )
    print(f"[*] Created KnowledgeChunk: {chunk.chunk_id}")
    print(f"[*] Tokens: {chunk.tokens} | Tags: {chunk.tags}")

    try:
        # Attempt to mutate frozen instance
        chunk.tokens = 50  # type: ignore[misc]
    except Exception as err:
        print(f"[*] Immutability Verified: Cannot mutate frozen dataclass ({type(err).__name__})")

    print("\n" + "=" * 65)
    print("2. PYDANTIC V2 VALIDATION & COMPUTED FIELDS")
    print("=" * 65)
    payload_data = {
        "model_name": "  GPT-4o  ",  # Raw whitespace and uppercase
        "prompt": "Explain vector indexing with HNSW graphs in database systems.",
        "temperature": 0.5,
    }

    # Deserializes and validates
    validated_payload = LLMModelPayload.model_validate(payload_data)
    print(f"[*] Normalized Model Name: '{validated_payload.model_name}'")
    print(f"[*] Computed Prompt Tokens: {validated_payload.estimated_prompt_tokens}")

    # Export to JSON directly via Rust core
    exported_json = validated_payload.model_dump_json(indent=2)
    print("[*] Serialized JSON (including computed fields):")
    print(exported_json)

    print("\n" + "=" * 65)
    print("3. STRICT MODE VS LAX MODE")
    print("=" * 65)
    # Lax mode silently coerces string "123" to integer 123, and "true" to True
    lax_obj = LaxUserModel.model_validate({"user_id": "999", "is_active": "true"})
    print(f"[*] Lax Mode Coercion Succeeded: user_id={lax_obj.user_id} ({type(lax_obj.user_id).__name__})")

    try:
        # Strict mode rejects type coercion to protect system boundaries
        StrictUserModel.model_validate({"user_id": "999", "is_active": "true"})
    except ValidationError as err:
        print(f"[*] Strict Mode Intercepted Coercion Attempt:")
        for error_entry in err.errors():
            print(f"    - Field: {error_entry['loc'][0]} -> {error_entry['msg']}")

    print("\n" + "=" * 65)
    print("4. CROSS-FIELD MODEL VALIDATOR")
    print("=" * 65)
    try:
        # max_requests (5) is less than min_requests (10)
        RateLimitWindowConfig(min_requests=10, max_requests=5, window_seconds=60)
    except ValidationError as err:
        print(f"[*] Interdependent Validation Error Caught: {err.errors()[0]['msg']}")

    print("\n" + "=" * 65)
    print("5. TYPEADAPTER FOR STANDALONE PRIMITIVES")
    print("=" * 65)
    # Validate a list of UUID strings without wrapping in a full model
    uuid_list_adapter = TypeAdapter(List[UUID])
    parsed_uuids = uuid_list_adapter.validate_python([str(uuid4()), str(uuid4())])
    print(f"[*] Successfully validated {len(parsed_uuids)} standalone UUIDs: {type(parsed_uuids[0])}")


if __name__ == "__main__":
    run_demonstrations()
