# Day 86: Exercises — LLM Tool Calling & Pydantic Contracts

Three production-grade engineering exercises covering schema contracts, self-healing validation, and parallel tool dispatch.

---

## Exercise 1: Dynamic Tool Registry with Pydantic V2

### Objective
Create a lightweight decorator `@register_tool` that registers a Python function along with its Pydantic V2 input contract, generating an OpenAI-compatible JSON Schema definition.

### Requirements
1. Implement class `LightweightRegistry`.
2. Support `@registry.tool(name, schema_cls)` decorator.
3. Provide `get_schemas()` returning OpenAI-format JSON schema objects with `name`, `description`, and `parameters`.

### Solution & Test Harness

```python
from typing import Any, Callable, Dict, List, Type
from pydantic import BaseModel, Field

class LightweightRegistry:
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}

    def tool(self, name: str, schema_cls: Type[BaseModel]):
        def decorator(func: Callable):
            self.tools[name] = {
                "schema": schema_cls,
                "handler": func,
                "description": (schema_cls.__doc__ or "").strip(),
            }
            return func
        return decorator

    def get_schemas(self) -> List[Dict[str, Any]]:
        schemas = []
        for name, data in self.tools.items():
            param_schema = data["schema"].model_json_schema()
            param_schema.pop("title", None)
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": data["description"],
                    "parameters": param_schema,
                }
            })
        return schemas

# Verification
if __name__ == "__main__":
    registry = LightweightRegistry()

    class MathAdd(BaseModel):
        """Adds two integer numbers together."""
        a: int = Field(..., description="First operand")
        b: int = Field(..., description="Second operand")

    @registry.tool("add_numbers", MathAdd)
    def add_numbers(params: MathAdd) -> int:
        return params.a + params.b

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "add_numbers"
    assert "properties" in schemas[0]["function"]["parameters"]
    assert schemas[0]["function"]["parameters"]["properties"]["a"]["type"] == "integer"
    print("[Exercise 1] PASSED: Lightweight dynamic tool registry verified.")
```

---

## Exercise 2: Self-Healing Tool Argument Validator

### Objective
Implement an argument validator that catches `pydantic.ValidationError` and converts it into a structured, LLM-digestible corrective message specifying field names and validation requirements.

### Requirements
1. Define a Pydantic model `PaymentContract` with `account_id` (format `ACC-XXXX`), `amount` ($>0.0$ and $\le 10000.0$), and `currency` (one of `["USD", "EUR", "GBP"]`).
2. Write function `validate_and_repair_feedback(schema_cls, raw_dict) -> Tuple[bool, Optional[PaymentContract], Optional[str]]`.
3. If valid, return `(True, instance, None)`. If invalid, return `(False, None, feedback_string)`.

### Solution & Test Harness

```python
from typing import Literal, Optional, Tuple
from pydantic import BaseModel, Field, ValidationError

class PaymentContract(BaseModel):
    account_id: str = Field(..., pattern=r"^ACC-\d{4}$", description="Account identifier (ACC-XXXX)")
    amount: float = Field(..., gt=0.0, le=10000.0, description="Payment amount up to 10,000")
    currency: Literal["USD", "EUR", "GBP"] = Field(..., description="Permitted currency")

def validate_and_repair_feedback(
    schema_cls: type[BaseModel], raw_dict: dict
) -> Tuple[bool, Optional[BaseModel], Optional[str]]:
    try:
        model = schema_cls.model_validate(raw_dict)
        return True, model, None
    except ValidationError as e:
        diagnostics = []
        for err in e.errors():
            field_name = ".".join(str(k) for k in err["loc"])
            diagnostics.append(f"- Field '{field_name}': {err['msg']}")
        feedback = "Validation failed:\n" + "\n".join(diagnostics)
        return False, None, feedback

# Verification
if __name__ == "__main__":
    bad_payload = {"account_id": "INVALID_ID", "amount": -50.0, "currency": "BITCOIN"}
    valid, instance, feedback = validate_and_repair_feedback(PaymentContract, bad_payload)
    assert valid is False
    assert instance is None
    assert "account_id" in feedback
    assert "amount" in feedback
    assert "currency" in feedback

    good_payload = {"account_id": "ACC-1234", "amount": 250.0, "currency": "USD"}
    valid2, instance2, feedback2 = validate_and_repair_feedback(PaymentContract, good_payload)
    assert valid2 is True
    assert instance2.account_id == "ACC-1234"
    assert feedback2 is None
    print("[Exercise 2] PASSED: Self-healing validator verified.")
```

---

## Exercise 3: Parallel Tool Execution Dispatcher with Error Containment

### Objective
Build a parallel tool execution dispatcher that processes multiple tool calls in a batch without allowing one failing tool to crash the entire batch.

### Requirements
1. Implement `dispatch_parallel(tool_calls, handlers, schemas) -> List[Dict[str, Any]]`.
2. Each result must contain `tool_call_id`, `status` ("success" or "error"), and `result` (or `error_details`).
3. Ensure unhandled exceptions in one handler are caught and isolated.

### Solution & Test Harness

```python
from typing import Any, Callable, Dict, List
from pydantic import BaseModel, Field

class PingParams(BaseModel):
    host: str = Field(..., min_length=1)

def ping_handler(p: PingParams) -> str:
    if p.host == "fail.local":
        raise ConnectionResetError("Connection refused by host")
    return f"Pong from {p.host}"

def dispatch_parallel(
    calls: List[Dict[str, Any]], handlers: Dict[str, Callable], schemas: Dict[str, type[BaseModel]]
) -> List[Dict[str, Any]]:
    results = []
    for call in calls:
        cid = call["id"]
        name = call["name"]
        args = call["args"]
        
        if name not in handlers:
            results.append({"tool_call_id": cid, "status": "error", "error": f"Unknown tool: {name}"})
            continue
            
        schema = schemas[name]
        try:
            validated = schema.model_validate(args)
            output = handlers[name](validated)
            results.append({"tool_call_id": cid, "status": "success", "result": output})
        except Exception as e:
            results.append({"tool_call_id": cid, "status": "error", "error": str(e)})
            
    return results

# Verification
if __name__ == "__main__":
    calls = [
        {"id": "call_1", "name": "ping", "args": {"host": "api.internal"}},
        {"id": "call_2", "name": "ping", "args": {"host": "fail.local"}},
        {"id": "call_3", "name": "ping", "args": {"host": "db.internal"}},
    ]
    schemas = {"ping": PingParams}
    handlers = {"ping": ping_handler}

    batch_results = dispatch_parallel(calls, handlers, schemas)
    assert len(batch_results) == 3
    assert batch_results[0]["status"] == "success"
    assert batch_results[1]["status"] == "error"
    assert "Connection refused" in batch_results[1]["error"]
    assert batch_results[2]["status"] == "success"
    print("[Exercise 3] PASSED: Parallel tool dispatcher verified.")
```
