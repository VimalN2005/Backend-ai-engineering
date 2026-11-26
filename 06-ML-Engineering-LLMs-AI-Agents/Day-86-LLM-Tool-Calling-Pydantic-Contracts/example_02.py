"""
Day 86: LLM Tool Calling & Function Execution with Strict Pydantic Contracts
Part 2: Multi-Turn ReAct Tool-Calling Loop with Self-Healing Argument Repair

This module provides:
1. ReActAgentOrchestrator executing the complete Tool-Calling lifecycle.
2. Self-Healing Mechanism: Captures Pydantic ValidationError and generates
   precise structured feedback instructing the model to repair schema violations.
3. Multi-turn conversation history management (system, user, assistant, tool roles).
4. Runaway execution protection with maximum iteration bounds.
5. End-to-end deterministic demonstration of an automated argument repair cycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Re-use registry and tools from example_01
from example_01 import (
    CurrencyConvertParams,
    DatabaseQueryParams,
    NotificationParams,
    ToolRegistry,
    convert_currency,
    query_database,
    send_notification,
)


# =====================================================================
# 1. Conversation Message Data Model
# =====================================================================

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class Message:
    role: str  # "system", "user", "assistant", "tool"
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


# =====================================================================
# 2. Simulated Foundation LLM with Flawed-First Behavior
# =====================================================================

class SimulatedLLM:
    """
    Simulates a foundation model.
    In Turn 1: Emits an invalid tool call (hallucinating string for integer / bad table name).
    In Turn 2: Reads the Pydantic validation error feedback and repairs the call.
    In Turn 3: Reads the valid tool result and generates the final response.
    """
    def __init__(self):
        self.step = 0

    def generate(self, messages: List[Message], tool_definitions: List[Dict[str, Any]]) -> Message:
        self.step += 1

        # Check last message to determine LLM response state
        last_msg = messages[-1]

        if last_msg.role == "user":
            # Turn 1: Propose tool call with malformed arguments (schema violation)
            return Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_901_db",
                        name="query_database",
                        # Flawed: table 'client_accounts' is not in allowed Literal enum; limit '9999' exceeds maximum 50
                        arguments=json.dumps({"table": "client_accounts", "limit": 9999}),
                    )
                ],
            )

        elif last_msg.role == "tool" and "Pydantic validation failed" in (last_msg.content or ""):
            # Turn 2: Self-healing repair: Model inspects validation feedback and fixes arguments
            return Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_902_db_repaired",
                        name="query_database",
                        # Repaired: valid table 'users' and valid limit 3
                        arguments=json.dumps({"table": "users", "limit": 3}),
                    )
                ],
            )

        elif last_msg.role == "tool" and "success" in (last_msg.content or ""):
            # Turn 3: Successful tool execution: Synthesize final user answer
            tool_data = json.loads(last_msg.content or "{}")
            rows = tool_data.get("rows", [])
            user_names = ", ".join(r["name"] for r in rows)
            return Message(
                role="assistant",
                content=f"Successfully retrieved {len(rows)} users from the analytics database: {user_names}.",
            )

        return Message(role="assistant", content="I cannot assist with this request.")


# =====================================================================
# 3. Autonomous ReAct Agent Orchestrator with Self-Healing
# =====================================================================

class ReActAgentOrchestrator:
    """
    Orchestrates the multi-turn Tool-Calling loop:
    Prompt -> Model Call -> Pydantic Validation -> Tool Execution -> Loop / Response
    """
    def __init__(self, registry: ToolRegistry, llm: SimulatedLLM, max_iterations: int = 5):
        self.registry = registry
        self.llm = llm
        self.max_iterations = max_iterations
        self.history: List[Message] = []

    def run(self, user_prompt: str) -> str:
        # Initialize conversation history
        self.history = [
            Message(
                role="system",
                content="You are an enterprise AI assistant with access to strictly validated internal database tools.",
            ),
            Message(role="user", content=user_prompt),
        ]

        tool_schemas = self.registry.get_openai_tool_definitions()

        for iteration in range(1, self.max_iterations + 1):
            print(f"\n--- [Iteration {iteration}] Invoking Foundation Model ---")
            assistant_msg = self.llm.generate(self.history, tool_schemas)
            self.history.append(assistant_msg)

            # If model produced a final text response and no tool calls, return it
            if not assistant_msg.tool_calls:
                print(f"[Agent Finished] Final Response: {assistant_msg.content}")
                return assistant_msg.content or ""

            # Process tool calls
            for tool_call in assistant_msg.tool_calls:
                print(f"  [Tool Request] Name: '{tool_call.name}'")
                print(f"  [Raw Arguments]: {tool_call.arguments}")

                # Dispatch through strict Pydantic validation boundary
                result = self.registry.dispatch(tool_call.name, tool_call.arguments)

                if result.success:
                    print(f"  [Tool Status]: SUCCESS -> Result received.")
                    tool_content = json.dumps(result.output)
                else:
                    print(f"  [Tool Status]: FAILED -> Validation rejected arguments.")
                    # Construct self-healing feedback prompt
                    feedback = {
                        "error": result.error,
                        "validation_diagnostics": result.validation_errors,
                        "instruction": "Your previous tool call violated the parameter schema. Please inspect the diagnostics above and call the tool again with valid arguments.",
                    }
                    tool_content = json.dumps(feedback)
                    print(f"  [Self-Healing Prompt Formatted]:\n    {tool_content}")

                # Inject tool response message back into conversation
                self.history.append(
                    Message(
                        role="tool",
                        content=tool_content,
                        tool_call_id=tool_call.id,
                    )
                )

        raise RuntimeError(f"ReAct agent exceeded maximum permitted iterations ({self.max_iterations}).")


# =====================================================================
# 4. Demonstration and Self-Validating Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 86: REACT AGENT TOOL CALLING & SELF-HEALING LOOP")
    print("=" * 80)

    # Setup Registry
    registry = ToolRegistry()
    registry.register("query_database", DatabaseQueryParams, query_database)
    registry.register("convert_currency", CurrencyConvertParams, convert_currency)
    registry.register("send_notification", NotificationParams, send_notification)

    # Initialize simulated LLM and Orchestrator
    simulated_llm = SimulatedLLM()
    orchestrator = ReActAgentOrchestrator(registry=registry, llm=simulated_llm, max_iterations=4)

    prompt = "Please retrieve the list of active users from our database."
    print(f"User Prompt: '{prompt}'")

    final_response = orchestrator.run(prompt)

    # Verify conversation history trace
    print("\n" + "=" * 80)
    print("CONVERSATION EXECUTION AUDIT LOG")
    print("=" * 80)
    for idx, msg in enumerate(orchestrator.history, 1):
        content_preview = (msg.content[:80] + "...") if msg.content and len(msg.content) > 80 else msg.content
        tc_preview = [f"{tc.name}({tc.arguments})" for tc in msg.tool_calls] if msg.tool_calls else None
        print(f"[{idx}] Role: {msg.role:<10} | Content: {content_preview} | ToolCalls: {tc_preview}")

    # Assertions
    assert "Alice Johnson" in final_response
    assert len(orchestrator.history) == 7, "Expected 7 messages in the complete repair cycle"
    assert orchestrator.history[3].role == "tool", "Message 4 must be the corrective validation failure feedback"
    assert "Pydantic validation failed" in orchestrator.history[3].content
    assert orchestrator.history[5].role == "tool", "Message 6 must be the successful tool result"
    assert orchestrator.history[6].role == "assistant", "Message 7 must be the final response"

    print("\n[SUCCESS] Multi-Turn ReAct Agent with Self-Healing Argument Repair verified.")
