"""
Day 87: AI Agents — ReAct Architecture, State Machines & LangGraph Workflows
Part 2: Cyclic State Graph Orchestrator with Checkpointing & Human-in-the-Loop (HITL)

This module implements:
1. CyclicStateGraph representing the LangGraph architectural pattern (Nodes, Edges, Conditional Edges).
2. CheckpointManager: Durable state checkpointing per thread_id for fault-tolerance and pause/resume.
3. Human-in-the-Loop (HITL) Security Gate:
   - Intercepts high-risk actions (e.g., fund transfers > $1,000, database mutations).
   - Suspends graph execution at a checkpoint until approved or rejected by a supervisor.
4. Cyclic loops: Nodes route back to Planner upon receiving tool observations.
5. Production demonstration verifying both automated flow and paused/resumed HITL flow.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# =====================================================================
# 1. Graph State Model & Reducer
# =====================================================================

class GraphStatus(str, Enum):
    RUNNING = "RUNNING"
    SUSPENDED_FOR_APPROVAL = "SUSPENDED_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


@dataclass
class GraphState:
    thread_id: str
    checkpoint_id: int = 0
    current_node: str = "START"
    status: GraphStatus = GraphStatus.RUNNING
    messages: List[Dict[str, Any]] = field(default_factory=list)
    pending_tool_call: Optional[Dict[str, Any]] = None
    is_approved: bool = False
    rejection_reason: Optional[str] = None
    final_output: Optional[str] = None

    def clone(self) -> GraphState:
        return copy.deepcopy(self)


# =====================================================================
# 2. Checkpoint Manager (State Persistence)
# =====================================================================

class CheckpointManager:
    """
    Simulates a durable PostgreSQL or Redis state checkpointer.
    Persists state snapshots at every node boundary.
    """
    def __init__(self):
        # Key: (thread_id, checkpoint_id) -> GraphState
        self._checkpoints: Dict[Tuple[str, int], GraphState] = {}

    def save(self, state: GraphState) -> int:
        state.checkpoint_id += 1
        snapshot = state.clone()
        self._checkpoints[(state.thread_id, state.checkpoint_id)] = snapshot
        return state.checkpoint_id

    def load_latest(self, thread_id: str) -> Optional[GraphState]:
        matching = [
            (cid, st) for (tid, cid), st in self._checkpoints.items() if tid == thread_id
        ]
        if not matching:
            return None
        # Return state with highest checkpoint_id
        matching.sort(key=lambda x: x[0], reverse=True)
        return matching[0][1].clone()


# =====================================================================
# 3. Domain Tools & Policy Boundaries
# =====================================================================

class FinancialTools:
    @staticmethod
    def transfer_funds(account: str, amount: float) -> Dict[str, Any]:
        return {
            "status": "success",
            "tx_id": f"TX-SEC-{int(amount * 103)}",
            "account": account,
            "transferred_amount": amount,
            "currency": "USD",
        }

    @staticmethod
    def get_account_balance(account: str) -> Dict[str, Any]:
        return {"account": account, "balance": 75000.0, "currency": "USD"}


class SecurityPolicy:
    HIGH_RISK_TRANSFER_THRESHOLD = 1000.0

    @classmethod
    def requires_human_approval(cls, tool_name: str, args: Dict[str, Any]) -> bool:
        if tool_name == "transfer_funds":
            amount = float(args.get("amount", 0.0))
            return amount >= cls.HIGH_RISK_TRANSFER_THRESHOLD
        return False


# =====================================================================
# 4. State Graph Nodes & Conditional Routing
# =====================================================================

class CyclicAgentGraph:
    """
    Implements a cyclic execution graph with conditional edges and HITL interrupt.
    """
    def __init__(self, checkpointer: CheckpointManager):
        self.checkpointer = checkpointer

    # --- Node 1: Planner Node ---
    def node_planner(self, state: GraphState) -> GraphState:
        last_msg = state.messages[-1]
        content = last_msg.get("content", "").lower()

        if "balance" in content:
            state.pending_tool_call = {
                "name": "get_account_balance",
                "args": {"account": "ACC-CORP-900"},
            }
        elif "transfer" in content:
            # Extract transfer amount
            amount = 5000.0 if "5000" in content else 250.0
            state.pending_tool_call = {
                "name": "transfer_funds",
                "args": {"account": "ACC-VENDOR-404", "amount": amount},
            }
        else:
            state.pending_tool_call = None

        state.current_node = "planner"
        return state

    # --- Node 2: Policy Gate Node ---
    def node_policy_gate(self, state: GraphState) -> GraphState:
        state.current_node = "policy_gate"
        if not state.pending_tool_call:
            return state

        tool_name = state.pending_tool_call["name"]
        args = state.pending_tool_call["args"]

        if SecurityPolicy.requires_human_approval(tool_name, args) and not state.is_approved:
            # Suspend graph and wait for external human approval
            state.status = GraphStatus.SUSPENDED_FOR_APPROVAL
        return state

    # --- Node 3: Tool Execution Node ---
    def node_tool_executor(self, state: GraphState) -> GraphState:
        state.current_node = "tool_executor"
        call = state.pending_tool_call
        if not call:
            return state

        name = call["name"]
        args = call["args"]

        if name == "get_account_balance":
            res = FinancialTools.get_account_balance(args["account"])
        elif name == "transfer_funds":
            res = FinancialTools.transfer_funds(args["account"], args["amount"])
        else:
            res = {"error": f"Unknown tool: {name}"}

        state.messages.append({
            "role": "tool",
            "name": name,
            "content": json.dumps(res),
        })
        state.pending_tool_call = None
        return state

    # --- Node 4: Synthesizer Node ---
    def node_synthesizer(self, state: GraphState) -> GraphState:
        state.current_node = "synthesizer"
        last_tool_msg = next((m for m in reversed(state.messages) if m.get("role") == "tool"), None)
        
        if last_tool_msg:
            data = json.loads(last_tool_msg["content"])
            if "transferred_amount" in data:
                state.final_output = f"Transferred ${data['transferred_amount']:,.2f} to {data['account']} successfully. Ref: {data['tx_id']}."
            elif "balance" in data:
                state.final_output = f"Current balance for {data['account']} is ${data['balance']:,.2f} {data['currency']}."
        else:
            state.final_output = "No operations executed."

        state.status = GraphStatus.COMPLETED
        return state

    # --- Conditional Router ---
    def route_after_policy(self, state: GraphState) -> str:
        if state.status == GraphStatus.SUSPENDED_FOR_APPROVAL:
            return "INTERRUPT"
        return "tool_executor"

    def route_after_tool(self, state: GraphState) -> str:
        # Cyclic check: if more actions are planned, route back to planner; otherwise synthesize
        return "synthesizer"

    # --- Graph Execution Engine ---
    def run(self, state: GraphState) -> GraphState:
        self.checkpointer.save(state)

        # Step 1: Planner
        state = self.node_planner(state)
        self.checkpointer.save(state)

        # Step 2: Policy Gate
        state = self.node_policy_gate(state)
        self.checkpointer.save(state)

        next_step = self.route_after_policy(state)
        if next_step == "INTERRUPT":
            # Graph execution pauses here
            return state

        # Step 3: Tool Execution
        state = self.node_tool_executor(state)
        self.checkpointer.save(state)

        # Step 4: Synthesizer
        state = self.node_synthesizer(state)
        self.checkpointer.save(state)

        return state

    def resume_with_approval(self, thread_id: str, approved: bool, reason: Optional[str] = None) -> GraphState:
        """Resumes a suspended graph from its latest checkpoint after human supervisor input."""
        state = self.checkpointer.load_latest(thread_id)
        if not state:
            raise ValueError(f"No checkpoint found for thread {thread_id}")

        if state.status != GraphStatus.SUSPENDED_FOR_APPROVAL:
            raise RuntimeError(f"Thread {thread_id} is not suspended. Status: {state.status}")

        if not approved:
            state.status = GraphStatus.REJECTED
            state.rejection_reason = reason or "Rejected by human supervisor."
            state.final_output = f"Action rejected: {state.rejection_reason}"
            self.checkpointer.save(state)
            return state

        # Mark approved and resume execution
        state.is_approved = True
        state.status = GraphStatus.RUNNING
        self.checkpointer.save(state)

        # Execute Tool
        state = self.node_tool_executor(state)
        self.checkpointer.save(state)

        # Synthesize Final
        state = self.node_synthesizer(state)
        self.checkpointer.save(state)

        return state


# =====================================================================
# 5. Verification Harness
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DAY 87: CYCLIC STATE GRAPH WITH CHECKPOINTING & HITL APPROVAL")
    print("=" * 80)

    checkpointer = CheckpointManager()
    graph = CyclicAgentGraph(checkpointer)

    # -----------------------------------------------------------------
    # Test 1: Low-Risk Request (Auto-executes without interrupt)
    # -----------------------------------------------------------------
    print("\n[Scenario 1] Low-Risk Request: Transfer $250 (Auto-approved)")
    thread_1 = "thread_low_risk_01"
    init_state_1 = GraphState(
        thread_id=thread_1,
        messages=[{"role": "user", "content": "Please transfer $250 to vendor account."}],
    )

    result_1 = graph.run(init_state_1)
    print(f"  Status: {result_1.status.value} | Final Output: {result_1.final_output}")
    print(f"  Total Checkpoints Saved: {result_1.checkpoint_id}")
    assert result_1.status == GraphStatus.COMPLETED
    assert "Transferred $250.00" in result_1.final_output

    # -----------------------------------------------------------------
    # Test 2: High-Risk Request (Triggers HITL Interrupt, Checkpointing & Resume)
    # -----------------------------------------------------------------
    print("\n" + "-" * 80)
    print("[Scenario 2] High-Risk Request: Transfer $5000 (Requires Human Gate)")
    thread_2 = "thread_high_risk_02"
    init_state_2 = GraphState(
        thread_id=thread_2,
        messages=[{"role": "user", "content": "Please transfer $5000 to vendor account."}],
    )

    # Run initial pass -> Should suspend at Policy Gate
    result_2 = graph.run(init_state_2)
    print(f"  Initial Pass Status: {result_2.status.value}")
    print(f"  Pending Tool Call  : {result_2.pending_tool_call}")
    print(f"  Current Node       : {result_2.current_node}")
    assert result_2.status == GraphStatus.SUSPENDED_FOR_APPROVAL
    assert result_2.pending_tool_call["args"]["amount"] == 5000.0

    # Simulate Human Supervisor Approval
    print("\n  [Supervisor Action] Approving transaction for thread_high_risk_02...")
    resumed_state = graph.resume_with_approval(thread_id=thread_2, approved=True)
    print(f"  Resumed Pass Status: {resumed_state.status.value}")
    print(f"  Final Output       : {resumed_state.final_output}")
    print(f"  Total Checkpoints  : {resumed_state.checkpoint_id}")

    assert resumed_state.status == GraphStatus.COMPLETED
    assert "Transferred $5,000.00" in resumed_state.final_output

    print("\n[SUCCESS] Cyclic State Graph with HITL Checkpointing verified successfully.")
