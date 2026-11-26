"""
Day 86: LLM Tool Calling & Function Execution with Strict Pydantic Contracts
Part 1: Strict Pydantic Tool Registry with OpenAPI Schema Generation & Dispatch

This module implements:
1. ToolRegistry with automatic OpenAPI/JSON Schema extraction from Pydantic V2 models.
2. Type-safe validation boundary catching malformed LLM arguments before execution.
3. Production tool implementations:
   - DatabaseQueryTool (read-only table inspection with bounds)
   - CurrencyConverterTool (ISO 4217 validation, positive floats)
   - SendNotificationTool (multi-channel dispatch with length guards)
4. Structured execution results with detailed diagnostic error reporting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, Field, ValidationError


# =====================================================================
# 1. Pydantic Tool Parameter Contracts
# =====================================================================

class DatabaseQueryParams(BaseModel):
    """Parameters for executing a read-only query against the analytics database replica."""
    table: Literal["users", "orders", "audit_logs"] = Field(
        ..., description="Target database table to inspect (must be in allowed list)"
    )
    limit: int = Field(
        default=5, ge=1, le=50, description="Maximum number of rows to return (1 to 50)"
    )
    filter_field: Optional[str] = Field(
        default=None, description="Optional column name to filter by"
    )
    filter_value: Optional[str] = Field(
        default=None, description="Value for the filter_field column"
    )


class CurrencyConvertParams(BaseModel):
    """Parameters for real-time currency conversion."""
    source_currency: str = Field(
        ..., pattern=r"^[A-Z]{3}$", description="3-letter ISO 4217 source currency code (e.g., USD, EUR)"
    )
    target_currency: str = Field(
        ..., pattern=r"^[A-Z]{3}$", description="3-letter ISO 4217 target currency code (e.g., JPY, GBP)"
    )
    amount: float = Field(
        ..., gt=0.0, description="Positive decimal currency amount to convert"
    )


class NotificationParams(BaseModel):
    """Parameters for dispatching system notifications across enterprise channels."""
    channel: Literal["email", "slack", "pagerduty"] = Field(
        ..., description="Delivery destination channel"
    )
    recipient: str = Field(
        ..., min_length=3, description="Target recipient address or user ID"
    )
    message: str = Field(
        ..., min_length=5, max_length=200, description="Notification body text (5 to 200 characters)"
    )


# =====================================================================
# 2. Tool Implementation Handlers
# =====================================================================

def query_database(params: DatabaseQueryParams) -> Dict[str, Any]:
    """Mock implementation of database query tool."""
    mock_db = {
        "users": [
            {"id": "usr_01", "name": "Alice Johnson", "role": "admin", "status": "active"},
            {"id": "usr_02", "name": "Bob Smith", "role": "developer", "status": "active"},
            {"id": "usr_03", "name": "Charlie Lee", "role": "analyst", "status": "inactive"},
        ],
        "orders": [
            {"id": "ord_101", "amount": 250.00, "currency": "USD", "status": "fulfilled"},
            {"id": "ord_102", "amount": 1490.50, "currency": "EUR", "status": "pending"},
        ],
        "audit_logs": [
            {"id": "log_01", "action": "LOGIN", "timestamp": "2026-11-26T08:00:00Z"},
        ],
    }

    rows = mock_db.get(params.table, [])
    if params.filter_field and params.filter_value:
        rows = [r for r in rows if str(r.get(params.filter_field, "")).lower() == params.filter_value.lower()]

    limited_rows = rows[: params.limit]
    return {
        "status": "success",
        "table": params.table,
        "row_count": len(limited_rows),
        "rows": limited_rows,
    }


def convert_currency(params: CurrencyConvertParams) -> Dict[str, Any]:
    """Mock implementation of currency converter tool."""
    rates_to_usd = {"USD": 1.0, "EUR": 1.08, "GBP": 1.28, "JPY": 0.0065, "INR": 0.012}

    src_rate = rates_to_usd.get(params.source_currency, 1.0)
    tgt_rate = rates_to_usd.get(params.target_currency, 1.0)

    amount_usd = params.amount * src_rate
    converted_amount = amount_usd / tgt_rate
    effective_rate = src_rate / tgt_rate

    return {
        "status": "success",
        "source": params.source_currency,
        "target": params.target_currency,
        "original_amount": params.amount,
        "converted_amount": round(converted_amount, 2),
        "exchange_rate": round(effective_rate, 4),
    }


def send_notification(params: NotificationParams) -> Dict[str, Any]:
    """Mock implementation of notification dispatch tool."""
    return {
        "status": "delivered",
        "dispatch_id": f"DISP-{hash(params.message) % 100000:05d}",
        "channel": params.channel,
        "recipient": params.recipient,
        "char_count": len(params.message),
    }


# =====================================================================
# 3. Strict Pydantic Tool Registry & Execution Dispatcher
# =====================================================================

@dataclass
class ToolExecutionResult:
    success: bool
    tool_name: str
    output: Optional[Dict[str, Any]]
    error: Optional[str]
    validation_errors: Optional[List[Dict[str, Any]]] = None


class ToolRegistry:
    """
    Central registry managing tool registration, JSON schema generation,
    and safe execution dispatching.
    """
    def __init__(self):
        self._tools: Dict[str, Tuple[Type[BaseModel], Callable[[Any], Dict[str, Any]], str]] = {}

    def register(
        self,
        name: str,
        schema_cls: Type[BaseModel],
        handler: Callable[[Any], Dict[str, Any]],
        description: Optional[str] = None,
    ) -> None:
        doc = description or (schema_cls.__doc__ or "No description provided.")
        self._tools[name] = (schema_cls, handler, doc.strip())

    def get_openai_tool_definitions(self) -> List[Dict[str, Any]]:
        """Generates OpenAI-compatible function calling schemas."""
        definitions = []
        for name, (schema_cls, _, doc) in self._tools.items():
            schema = schema_cls.model_json_schema()
            # Clean up Pydantic title metadata for cleaner prompt injection
            schema.pop("title", None)
            definitions.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": doc,
                    "parameters": schema,
                },
            })
        return definitions

    def dispatch(self, tool_name: str, raw_arguments_json: str) -> ToolExecutionResult:
        """
        Safely deserializes, validates against Pydantic schema, and invokes the tool handler.
        Guarantees that unvalidated or malformed arguments never reach backend execution.
        """
        if tool_name not in self._tools:
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                output=None,
                error=f"Tool '{tool_name}' is not registered in the registry.",
            )

        schema_cls, handler, _ = self._tools[tool_name]

        # 1. Parse JSON payload
        try:
            if isinstance(raw_arguments_json, dict):
                args_dict = raw_arguments_json
            else:
                args_dict = json.loads(raw_arguments_json)
        except json.JSONDecodeError as e:
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                output=None,
                error=f"Invalid JSON syntax in tool arguments: {str(e)}",
            )

        # 2. Validate using Pydantic V2 contract
        try:
            validated_params = schema_cls.model_validate(args_dict)
        except ValidationError as e:
            error_details = []
            for err in e.errors():
                loc = " -> ".join(str(item) for item in err["loc"])
                error_details.append({
                    "field": loc,
                    "issue": err["msg"],
                    "type": err["type"],
                })
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                output=None,
                error=f"Pydantic validation failed for tool '{tool_name}' arguments.",
                validation_errors=error_details,
            )

        # 3. Execute handler securely
        try:
            result = handler(validated_params)
            return ToolExecutionResult(
                success=True,
                tool_name=tool_name,
                output=result,
                error=None,
            )
        except Exception as e:
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                output=None,
                error=f"Internal tool execution error: {str(e)}",
            )


# =====================================================================
# 4. Verification Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 86: PYDANTIC TOOL REGISTRY & DISPATCH ENGINE")
    print("=" * 80)

    registry = ToolRegistry()
    registry.register("query_database", DatabaseQueryParams, query_database)
    registry.register("convert_currency", CurrencyConvertParams, convert_currency)
    registry.register("send_notification", NotificationParams, send_notification)

    # 1. Export JSON Schemas
    schemas = registry.get_openai_tool_definitions()
    print(f"Registered {len(schemas)} tools. Example Schema for 'query_database':")
    print(json.dumps(schemas[0], indent=2))

    # 2. Test Successful Dispatch
    print("\n" + "-" * 80)
    print("[Test 1] Successful Tool Execution:")
    call_payload = json.dumps({"table": "users", "limit": 2, "filter_field": "role", "filter_value": "admin"})
    res1 = registry.dispatch("query_database", call_payload)
    print(f"  Success: {res1.success} | Tool: {res1.tool_name}")
    print(f"  Output: {json.dumps(res1.output, indent=4)}")
    assert res1.success is True
    assert res1.output["row_count"] == 1
    assert res1.output["rows"][0]["name"] == "Alice Johnson"

    # 3. Test Validation Error Handling (Invalid table enum and limit out-of-bounds)
    print("\n" + "-" * 80)
    print("[Test 2] Invalid Arguments Rejected by Pydantic Boundary:")
    invalid_payload = json.dumps({"table": "passwords_table", "limit": 999})
    res2 = registry.dispatch("query_database", invalid_payload)
    print(f"  Success: {res2.success}")
    print(f"  Error: {res2.error}")
    print("  Validation Diagnostics:")
    for err in res2.validation_errors:
        print(f"    - Field: '{err['field']}' -> {err['issue']}")
    assert res2.success is False
    assert len(res2.validation_errors) >= 2

    # 4. Test Currency Regex & Bounds Validation
    print("\n" + "-" * 80)
    print("[Test 3] Currency Tool Parameter Validation:")
    valid_curr = json.dumps({"source_currency": "USD", "target_currency": "EUR", "amount": 100.0})
    res3 = registry.dispatch("convert_currency", valid_curr)
    print(f"  Success: {res3.success} | Converted: {res3.output['converted_amount']} EUR")
    assert res3.success is True

    print("\n[SUCCESS] Strict Pydantic Tool Registry passed all functional and validation checks.")
