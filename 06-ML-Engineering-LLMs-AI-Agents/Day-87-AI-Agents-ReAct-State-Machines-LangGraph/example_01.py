"""
Day 87: AI Agents — ReAct Architecture, State Machines & LangGraph Workflows
Part 1: Production ReAct Agent Loop with Explicit State Machine Tracking

This module implements:
1. AgentState data model tracking conversation messages, reasoning scratchpads, and iteration counters.
2. ReActAgentEngine executing the canonical Thought -> Action -> Observation loop.
3. Domain tools:
   - search_knowledge_base (internal docs lookup)
   - calculate_expression (safe math engine)
   - query_cluster_metrics (infra health diagnostics)
4. Finite state transitions (IDLE -> REASONING -> EXECUTING_TOOL -> OBSERVING -> TERMINATED).
5. Comprehensive execution tracing with self-validating assertions.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# =====================================================================
# 1. State Machine Definitions & Agent State
# =====================================================================

class AgentLifecycleState(str, Enum):
    IDLE = "IDLE"
    REASONING = "REASONING"
    EXECUTING_ACTION = "EXECUTING_ACTION"
    OBSERVING_RESULT = "OBSERVING_RESULT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class AgentMessage:
    role: str  # "system", "user", "assistant", "observation"
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReActTraceStep:
    iteration: int
    thought: str
    action_name: Optional[str]
    action_input: Optional[Dict[str, Any]]
    observation: Optional[str]


@dataclass
class AgentState:
    session_id: str
    messages: List[AgentMessage] = field(default_factory=list)
    trace_steps: List[ReActTraceStep] = field(default_factory=list)
    lifecycle_state: AgentLifecycleState = AgentLifecycleState.IDLE
    iteration_count: int = 0
    max_iterations: int = 5
    final_response: Optional[str] = None


# =====================================================================
# 2. Tool Environment
# =====================================================================

class ToolEnvironment:
    """Mock external environment exposing domain tools."""
    
    @staticmethod
    def search_knowledge_base(query: str) -> str:
        kb = {
            "cluster memory": "Kubernetes cluster 'prod-us-east-1' currently has 128GB total memory with 102GB utilized (79.6% usage).",
            "pod restart threshold": "Alert threshold for pod restarts is set at >= 5 restarts within a 15-minute sliding window.",
            "auth service latency": "Auth service p99 latency is currently 142ms, below the 200ms SLA target.",
        }
        q_lower = query.lower()
        for k, v in kb.items():
            if k in q_lower or any(word in q_lower for word in k.split()):
                return f"[KB Search Result]: {v}"
        return "[KB Search Result]: No relevant documentation found."

    @staticmethod
    def calculate_expression(expression: str) -> str:
        # Safe mathematical evaluation restricted to arithmetic operators
        cleaned = re.sub(r"[^0-9+\-*/().\s]", "", expression)
        try:
            val = eval(cleaned, {"__builtins__": None}, {})
            return f"[Calculation Result]: {val}"
        except Exception as e:
            return f"[Calculation Error]: Invalid mathematical expression ({str(e)})"

    @staticmethod
    def query_cluster_metrics(metric_name: str) -> str:
        metrics = {
            "node_count": "Active worker nodes: 8 out of 8 healthy.",
            "unassigned_pods": "Unassigned pods in pending state: 3 pods.",
            "disk_io": "Average NVMe IOPS: 4,200 IOPS across storage pool.",
        }
        return metrics.get(metric_name.lower(), f"Unknown metric '{metric_name}'. Available: {list(metrics.keys())}")


# =====================================================================
# 3. Deterministic Simulated LLM Reasoner
# =====================================================================

class SimulatedReasoner:
    """
    Simulates foundation model generating structured Thought/Action strings
    adhering to the canonical ReAct prompt template.
    """
    def __init__(self):
        self.step = 0

    def reason(self, state: AgentState) -> Tuple[str, Optional[str], Optional[Dict[str, Any]], Optional[str]]:
        """
        Returns:
            (thought, action_name, action_input, final_answer)
        """
        self.step += 1
        
        # Turn 1: Inspect cluster memory usage via knowledge base
        if self.step == 1:
            thought = "The user wants to know remaining cluster memory and if it exceeds safety buffer. First, I need to fetch current memory usage."
            action_name = "search_knowledge_base"
            action_input = {"query": "cluster memory"}
            return thought, action_name, action_input, None

        # Turn 2: Calculate remaining available memory in GB
        elif self.step == 2:
            thought = "We know total memory is 128GB and 102GB is utilized. Now I must calculate remaining free memory: 128 - 102."
            action_name = "calculate_expression"
            action_input = {"expression": "128 - 102"}
            return thought, action_name, action_input, None

        # Turn 3: Calculate percentage of free memory
        elif self.step == 3:
            thought = "Free memory is 26GB. Now calculate free percentage: (26 / 128) * 100."
            action_name = "calculate_expression"
            action_input = {"expression": "(26 / 128) * 100"}
            return thought, action_name, action_input, None

        # Turn 4: Final synthesis
        else:
            thought = "I have all required data. Total memory: 128GB, used: 102GB (79.6%), remaining: 26GB (20.31%). I can now synthesize the final response."
            final_ans = "The cluster currently has 26 GB of available memory (20.31% free headroom) out of 128 GB total capacity."
            return thought, None, None, final_ans


# =====================================================================
# 4. ReAct Agent Engine with State Machine Transitions
# =====================================================================

class ReActAgentEngine:
    def __init__(self, tools: Dict[str, Callable[[Any], str]], reasoner: SimulatedReasoner):
        self.tools = tools
        self.reasoner = reasoner

    def execute(self, session_id: str, prompt: str, max_iterations: int = 5) -> AgentState:
        state = AgentState(
            session_id=session_id,
            max_iterations=max_iterations,
            lifecycle_state=AgentLifecycleState.IDLE,
        )
        state.messages.append(AgentMessage(role="user", content=prompt))

        while state.iteration_count < state.max_iterations:
            state.iteration_count += 1
            state.lifecycle_state = AgentLifecycleState.REASONING

            # 1. Thought & Action Generation
            thought, action_name, action_input, final_answer = self.reasoner.reason(state)
            
            # 2. Check if model reached final terminal answer
            if final_answer is not None:
                state.final_response = final_answer
                state.lifecycle_state = AgentLifecycleState.COMPLETED
                state.messages.append(AgentMessage(role="assistant", content=final_answer))
                state.trace_steps.append(
                    ReActTraceStep(
                        iteration=state.iteration_count,
                        thought=thought,
                        action_name=None,
                        action_input=None,
                        observation=None,
                    )
                )
                break

            # 3. Action Execution Transition
            state.lifecycle_state = AgentLifecycleState.EXECUTING_ACTION
            if action_name not in self.tools:
                obs = f"Error: Tool '{action_name}' is not recognized."
            else:
                tool_fn = self.tools[action_name]
                try:
                    # Pass first argument value
                    arg_val = next(iter(action_input.values())) if action_input else ""
                    obs = tool_fn(arg_val)
                except Exception as e:
                    obs = f"Error executing tool '{action_name}': {str(e)}"

            # 4. Observation Recording
            state.lifecycle_state = AgentLifecycleState.OBSERVING_RESULT
            state.messages.append(
                AgentMessage(
                    role="observation",
                    content=obs,
                    metadata={"tool": action_name, "args": action_input},
                )
            )
            state.trace_steps.append(
                ReActTraceStep(
                    iteration=state.iteration_count,
                    thought=thought,
                    action_name=action_name,
                    action_input=action_input,
                    observation=obs,
                )
            )

        if state.lifecycle_state != AgentLifecycleState.COMPLETED:
            state.lifecycle_state = AgentLifecycleState.FAILED
            state.final_response = "Execution terminated: Maximum iteration threshold exceeded without reaching consensus."

        return state


# =====================================================================
# 5. Verification Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 87: REACT AGENT LOOP & STATE MACHINE ENGINE")
    print("=" * 80)

    # Initialize tools map
    tools_map = {
        "search_knowledge_base": ToolEnvironment.search_knowledge_base,
        "calculate_expression": ToolEnvironment.calculate_expression,
        "query_cluster_metrics": ToolEnvironment.query_cluster_metrics,
    }

    reasoner = SimulatedReasoner()
    engine = ReActAgentEngine(tools=tools_map, reasoner=reasoner)

    user_query = "What is the remaining available cluster memory and percentage headroom?"
    print(f"User Query: '{user_query}'\n")

    result_state = engine.execute(session_id="sess_agent_101", prompt=user_query, max_iterations=5)

    print("Execution Trace Steps (Thought -> Action -> Observation):")
    print("-" * 80)
    for step in result_state.trace_steps:
        print(f"[Iter {step.iteration}] Thought: {step.thought}")
        if step.action_name:
            print(f"         Action : {step.action_name}({step.action_input})")
            print(f"         Observ : {step.observation}")
        else:
            print("         Action : [Terminal Answer Emitted]")
        print("-" * 80)

    print(f"\nFinal Agent Lifecycle State: {result_state.lifecycle_state.value}")
    print(f"Final Synthesized Response : {result_state.final_response}")

    # Self-validating assertions
    assert result_state.lifecycle_state == AgentLifecycleState.COMPLETED
    assert result_state.iteration_count == 4
    assert len(result_state.trace_steps) == 4
    assert "26 GB" in result_state.final_response
    assert "20.31%" in result_state.final_response

    print("\n[SUCCESS] ReAct Agent State Machine successfully verified with zero errors.")
