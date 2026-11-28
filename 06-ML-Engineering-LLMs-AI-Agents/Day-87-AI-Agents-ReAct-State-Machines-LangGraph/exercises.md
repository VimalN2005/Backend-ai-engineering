# Day 87: Exercises — AI Agents, State Machines & LangGraph Workflows

Three production-grade engineering exercises covering cycle detection, state reducers, and dynamic conditional routing.

---

## Exercise 1: ReAct Cycle Detector & Circuit Breaker

### Objective
Implement a circuit breaker class `AgentCircuitBreaker` that tracks tool invocations in an agent scratchpad and detects infinite loops (e.g., calling the identical tool with the exact same arguments consecutively or exceeding max iterations).

### Requirements
1. Implement `record_action(tool_name: str, args: dict) -> bool`.
2. If the same `(tool_name, args)` signature is invoked 2 times consecutively, trigger a `LOOP_DETECTED` failure and return `False`.
3. If total actions exceed `max_turns`, trigger `MAX_TURNS_EXCEEDED` and return `False`.
4. Otherwise, return `True` (safe to proceed).

### Solution & Test Harness

```python
import json
from typing import Any, Dict, List, Optional

class AgentCircuitBreaker:
    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.history: List[str] = []
        self.tripped: bool = False
        self.trip_reason: Optional[str] = None

    def record_action(self, tool_name: str, args: Dict[str, Any]) -> bool:
        if self.tripped:
            return False

        # Serialize signature canonically
        sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"

        if len(self.history) >= self.max_turns:
            self.tripped = True
            self.trip_reason = "MAX_TURNS_EXCEEDED"
            return False

        # Consecutive duplicate detection
        if self.history and self.history[-1] == sig:
            self.tripped = True
            self.trip_reason = "LOOP_DETECTED"
            return False

        self.history.append(sig)
        return True

# Verification
if __name__ == "__main__":
    cb = AgentCircuitBreaker(max_turns=4)
    # Turn 1: Search
    assert cb.record_action("search", {"q": "memory"}) is True
    # Turn 2: Calc
    assert cb.record_action("calc", {"expr": "128-102"}) is True
    # Turn 3: Duplicate calc -> Should trip circuit breaker!
    assert cb.record_action("calc", {"expr": "128-102"}) is False
    assert cb.trip_reason == "LOOP_DETECTED"
    print("[Exercise 1] PASSED: ReAct cycle detector verified.")
```

---

## Exercise 2: Append-Only State Reducer Engine

### Objective
Implement a functional state reducer that aggregates messages and merges metadata from parallel agent workers without race conditions or overwriting history.

### Requirements
1. Implement `reduce_state(current_state: dict, update: dict) -> dict`.
2. Messages must be appended without mutating the input object.
3. Metadata dictionaries must be deeply merged.
4. Numerical counters (like `token_count`) must be summed.

### Solution & Test Harness

```python
import copy
from typing import Any, Dict, List

def reduce_state(current: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    new_state = copy.deepcopy(current)

    # 1. Append-only messages reducer
    if "messages" in delta:
        existing_msgs = new_state.get("messages", [])
        new_state["messages"] = existing_msgs + delta["messages"]

    # 2. Cumulative counter reducer
    if "token_count" in delta:
        new_state["token_count"] = new_state.get("token_count", 0) + delta["token_count"]

    # 3. Dict merge reducer
    if "metadata" in delta:
        meta = new_state.get("metadata", {})
        meta.update(delta["metadata"])
        new_state["metadata"] = meta

    return new_state

# Verification
if __name__ == "__main__":
    s0 = {"messages": [{"role": "user", "content": "Hi"}], "token_count": 10, "metadata": {"env": "prod"}}
    
    delta1 = {"messages": [{"role": "assistant", "content": "Checking DB"}], "token_count": 45}
    s1 = reduce_state(s0, delta1)
    
    assert len(s1["messages"]) == 2
    assert s1["token_count"] == 55
    assert s1["metadata"]["env"] == "prod"
    # Ensure immutability of s0
    assert len(s0["messages"]) == 1
    assert s0["token_count"] == 10
    print("[Exercise 2] PASSED: Functional state reducer verified.")
```

---

## Exercise 3: Dynamic Conditional Routing State Machine

### Objective
Build a dynamic routing engine that inspects state flags and directs execution between nodes (`planner`, `retriever`, `safety_guard`, `synthesizer`, `END`).

### Requirements
1. Router function `route_next(state: dict) -> str`.
2. If `state["error"]` is present -> route to `"error_handler"`.
3. If `state["needs_context"]` is True -> route to `"retriever"`.
4. If `state["has_tools"]` is True:
   - If `state["is_safe"]` is False -> route to `"safety_guard"`.
   - Else -> route to `"tool_executor"`.
5. If no tools needed -> route to `"synthesizer"`.

### Solution & Test Harness

```python
from typing import Dict, Any

def route_next(state: Dict[str, Any]) -> str:
    if state.get("error"):
        return "error_handler"
    if state.get("needs_context"):
        return "retriever"
    if state.get("has_tools"):
        if not state.get("is_safe", True):
            return "safety_guard"
        return "tool_executor"
    return "synthesizer"

# Verification
if __name__ == "__main__":
    assert route_next({"error": "DB Timeout"}) == "error_handler"
    assert route_next({"needs_context": True}) == "retriever"
    assert route_next({"has_tools": True, "is_safe": False}) == "safety_guard"
    assert route_next({"has_tools": True, "is_safe": True}) == "tool_executor"
    assert route_next({"has_tools": False}) == "synthesizer"
    print("[Exercise 3] PASSED: Dynamic conditional router verified.")
```
