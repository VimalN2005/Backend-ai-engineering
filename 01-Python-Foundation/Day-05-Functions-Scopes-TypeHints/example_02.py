"""
Day 05: Functions, Scopes (LEGB) & Production Type Hinting
File: example_02.py - Production AI Agent Tool Registry & Function Calling Schema Generator

ALIGNMENT WITH AI BACKEND ENGINEER ROLE:
Job Description Requirement:
"Integrate LLMs, RAG pipelines, agentic workflows... develop features for tool execution...
and optimize for low latency, token usage, and API cost efficiency."

WHY THIS ARCHITECTURE IS ESSENTIAL:
In autonomous AI Agent systems (e.g. LangGraph, OpenAI Tool Calling, Gemini Function Calling),
the LLM decides which backend Python function to invoke based on a structured JSON Schema.
Hardcoding JSON schemas for dozens of backend tools creates severe maintenance debt:
any signature or type change requires manually updating both the Python code and JSON definitions.

This module implements a production-grade Agent Tool Registry that uses Python type hint
introspection (`typing.get_type_hints()` and `inspect.signature()`) to:
1. Automatically generate OpenAI/Gemini-compliant JSON tool schemas directly from Python functions.
2. Enforce strict parameter validation before executing tool callbacks.
3. Eliminate redundant schema boilerplate across the backend.
"""

import inspect
import json
from typing import get_type_hints, Callable, Dict, Any, List, Literal


class ToolExecutionError(Exception):
    """Raised when an agent tool fails during argument validation or execution."""
    pass


class AIAgentToolRegistry:
    """
    Central registry for agent tools with automated schema generation and execution dispatching.
    """

    PYTHON_TO_JSON_SCHEMA_TYPES = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object"
    }

    def __init__(self) -> None:
        self._tools: Dict[str, Callable] = {}
        self._schemas: Dict[str, Dict[str, Any]] = {}

    def register(self, tool_func: Callable) -> Callable:
        """
        Decorator that registers a Python function as an agent tool and
        auto-generates its JSON tool specification.
        """
        name = tool_func.__name__
        self._tools[name] = tool_func
        self._schemas[name] = self._generate_tool_schema(tool_func)
        return tool_func

    def _generate_tool_schema(self, func: Callable) -> Dict[str, Any]:
        """
        Uses introspection to extract parameter types, docstrings, and defaults.
        """
        sig = inspect.signature(func)
        type_hints = get_type_hints(func)
        docstring = inspect.getdoc(func) or "No description provided."

        properties: Dict[str, Dict[str, Any]] = {}
        required_params: List[str] = []

        for param_name, param in sig.parameters.items():
            if param_name == "return":
                continue

            param_type = type_hints.get(param_name, str)
            json_type = self.PYTHON_TO_JSON_SCHEMA_TYPES.get(param_type, "string")

            properties[param_name] = {
                "type": json_type,
                "description": f"Parameter '{param_name}' of type {param_type.__name__}"
            }

            # If no default value exists, mark parameter as required
            if param.default is inspect.Parameter.empty:
                required_params.append(param_name)

        return {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": docstring,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_params
                }
            }
        }

    def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        """Returns the full list of tool definitions ready to pass to OpenAI/Gemini API."""
        return list(self._schemas.values())

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Executes a registered tool callback with defensive argument validation.
        """
        if tool_name not in self._tools:
            raise ToolExecutionError(f"Tool '{tool_name}' is not registered in the system.")

        target_func = self._tools[tool_name]
        sig = inspect.signature(target_func)

        try:
            # Validate and bind arguments against the Python function signature
            bound_args = sig.bind(**arguments)
            bound_args.apply_defaults()
            return target_func(*bound_args.args, **bound_args.kwargs)
        except TypeError as err:
            raise ToolExecutionError(f"Argument mismatch for tool '{tool_name}': {err}")
        except Exception as err:
            raise ToolExecutionError(f"Runtime failure in tool '{tool_name}': {err}")


# =====================================================================
# SIMULATION: REGISTERING REAL-WORLD AGENT TOOLS
# =====================================================================

registry = AIAgentToolRegistry()


@registry.register
def query_database_records(table_name: str, limit: int = 10, is_active_only: bool = True) -> Dict[str, Any]:
    """Queries enterprise relational database records with filtering."""
    # Simulated database query execution
    return {
        "status": "success",
        "table": table_name,
        "returned_records": min(limit, 5),
        "filter_active": is_active_only
    }


@registry.register
def calculate_token_cost(total_tokens: int, model: str = "gpt-4o") -> float:
    """Calculates API monetary cost based on consumed prompt and completion tokens."""
    rate_per_k = 0.005 if model == "gpt-4o" else 0.0015
    return round((total_tokens / 1000) * rate_per_k, 6)


def run_production_simulation() -> None:
    print("=" * 65)
    print("  PRODUCTION AI AGENT TOOL REGISTRY & SCHEMA DISPATCHER")
    print("=" * 65)

    # 1. Export auto-generated tool specifications for LLM payload
    print("\n--- [1] AUTO-GENERATED OPENAI/GEMINI TOOL SCHEMAS ---")
    schemas = registry.get_all_tool_schemas()
    print(json.dumps(schemas, indent=2))

    # 2. Simulate LLM deciding to invoke 'query_database_records'
    print("\n--- [2] SIMULATING LLM FUNCTION CALL DISPATCH ---")
    llm_tool_choice = "query_database_records"
    llm_extracted_args = {"table_name": "organizations", "limit": 25}

    print(f"Calling tool: '{llm_tool_choice}' with args: {llm_extracted_args}")
    result = registry.execute_tool(llm_tool_choice, llm_extracted_args)
    print(f"Tool Execution Output: {result}")

    # 3. Simulate LLM invoking 'calculate_token_cost'
    print("\n--- [3] SIMULATING TOKEN COST AUDIT DISPATCH ---")
    cost = registry.execute_tool("calculate_token_cost", {"total_tokens": 150_000, "model": "gpt-4o"})
    print(f"Calculated Token Cost: ${cost:.4f} USD")

    # 4. Defensive handling: Catching invalid tool invocation
    print("\n--- [4] DEFENSIVE VALIDATION (Simulating Malformed LLM Arguments) ---")
    try:
        registry.execute_tool("calculate_token_cost", {"invalid_param": 999})
    except ToolExecutionError as err:
        print(f"Caught expected agent execution error: {err}")

    print("=" * 65)


if __name__ == "__main__":
    run_production_simulation()
