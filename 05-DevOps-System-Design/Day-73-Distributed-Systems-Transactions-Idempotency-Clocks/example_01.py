"""
Day 73: Distributed Clocks Simulator: Lamport Timestamps, Vector Clocks & HLC
=============================================================================
Architecture & Principles Demonstrated:
1. Lamport Timestamps:
   - Scalar logical clocks enforcing partial causal ordering.
   - Message merge rule: max(local, incoming) + 1.
2. Vector Clocks:
   - Full causal ordering across N nodes.
   - Exact mathematical conflict detection: detects concurrent conflicting writes (V_A || V_B).
3. Hybrid Logical Clocks (HLC):
   - Merges physical POSIX time with logical counter.
   - Guarantees strict monotonicity even during physical clock drift/backward NTP adjustments.
"""

from __future__ import annotations
import copy
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# =====================================================================
# 1. Lamport Timestamps (Scalar Logical Clocks)
# =====================================================================

class LamportClock:
    """Scalar Lamport clock representing partial causal ordering."""
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.value: int = 0

    def tick(self) -> int:
        """Internal local event increment."""
        self.value += 1
        return self.value

    def send_event(self) -> int:
        """Message send event: increments and returns timestamp to attach."""
        self.value += 1
        return self.value

    def receive_event(self, incoming_timestamp: int) -> int:
        """Message receive event: max(local, incoming) + 1."""
        self.value = max(self.value, incoming_timestamp) + 1
        return self.value


# =====================================================================
# 2. Vector Clocks (Full Causal Ordering & Conflict Detection)
# =====================================================================

class CausalRelation(str, Enum):
    PRECEDES = "PRECEDES"      # V1 < V2 (V1 causally happened before V2)
    SUCCEEDS = "SUCCEEDS"      # V1 > V2 (V2 causally happened before V1)
    EQUAL = "EQUAL"            # V1 == V2 (Same logical state)
    CONCURRENT = "CONCURRENT"  # V1 || V2 (Conflict! Neither dominates)


class VectorClock:
    """Vector clock tracking knowledge of events across all nodes."""
    def __init__(self, node_id: str, all_nodes: List[str]):
        self.node_id = node_id
        self.clock: Dict[str, int] = {nid: 0 for nid in all_nodes}

    def tick(self) -> Dict[str, int]:
        """Local event: increments own index."""
        self.clock[self.node_id] += 1
        return copy.deepcopy(self.clock)

    def send_event(self) -> Dict[str, int]:
        """Send event: increments own index and returns snapshot."""
        self.clock[self.node_id] += 1
        return copy.deepcopy(self.clock)

    def receive_event(self, incoming_vector: Dict[str, int]) -> Dict[str, int]:
        """
        Receive event: pairwise maximum across all nodes,
        then increments own component.
        """
        for nid, val in incoming_vector.items():
            self.clock[nid] = max(self.clock.get(nid, 0), val)
        self.clock[self.node_id] += 1
        return copy.deepcopy(self.clock)

    @staticmethod
    def compare(v1: Dict[str, int], v2: Dict[str, int]) -> CausalRelation:
        """
        Evaluates causal relationship between two vector clocks:
        - v1 < v2: all components v1 <= v2 and at least one strictly <
        - v1 > v2: all components v1 >= v2 and at least one strictly >
        - v1 == v2: all components equal
        - v1 || v2: otherwise (Concurrent Conflict)
        """
        all_keys = set(v1.keys()).union(set(v2.keys()))
        v1_has_smaller = False
        v2_has_smaller = False

        for k in all_keys:
            val1 = v1.get(k, 0)
            val2 = v2.get(k, 0)
            if val1 < val2:
                v1_has_smaller = True
            elif val1 > val2:
                v2_has_smaller = True

        if v1_has_smaller and not v2_has_smaller:
            return CausalRelation.PRECEDES
        elif v2_has_smaller and not v1_has_smaller:
            return CausalRelation.SUCCEEDS
        elif not v1_has_smaller and not v2_has_smaller:
            return CausalRelation.EQUAL
        else:
            return CausalRelation.CONCURRENT


# =====================================================================
# 3. Hybrid Logical Clocks (HLC)
# =====================================================================

@dataclass(frozen=True)
class HLCTimestamp:
    physical_ms: int
    logical: int

    def __lt__(self, other: HLCTimestamp) -> bool:
        if self.physical_ms != other.physical_ms:
            return self.physical_ms < other.physical_ms
        return self.logical < other.logical

    def __repr__(self) -> str:
        return f"HLC({self.physical_ms}ms, c={self.logical})"


class HybridLogicalClock:
    """
    Hybrid Logical Clock (HLC) as defined by Kulkarni et al. (CockroachDB/MongoDB model).
    Combines physical wall-clock (pt) with logical counter (c), ensuring:
    1. e -> e' implies HLC(e) < HLC(e')
    2. HLC physical component stays bounded close to physical time.
    3. Monotonicity is preserved even when physical clock steps backward.
    """
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.l: int = 0  # Highest physical time observed so far
        self.c: int = 0  # Logical counter

    def now(self, physical_time_ms: Optional[int] = None) -> HLCTimestamp:
        """Generates HLC timestamp for local event."""
        pt = physical_time_ms if physical_time_ms is not None else int(time.time() * 1000)
        
        if pt > self.l:
            self.l = pt
            self.c = 0
        else:
            # Physical clock is behind or equal to our highest observed; increment counter
            self.c += 1

        return HLCTimestamp(self.l, self.c)

    def update(self, incoming: HLCTimestamp, physical_time_ms: Optional[int] = None) -> HLCTimestamp:
        """Merges incoming message HLC timestamp."""
        pt = physical_time_ms if physical_time_ms is not None else int(time.time() * 1000)

        prev_l = self.l
        self.l = max(prev_l, incoming.physical_ms, pt)

        if self.l == prev_l and self.l == incoming.physical_ms:
            self.c = max(self.c, incoming.logical) + 1
        elif self.l == prev_l:
            self.c += 1
        elif self.l == incoming.physical_ms:
            self.c = incoming.logical + 1
        else:
            self.c = 0

        return HLCTimestamp(self.l, self.c)


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_lamport_timestamps():
    print("\n--- 1. LAMPORT TIMESTAMPS (SCALAR CLOCKS) DEMO ---")
    node_a = LamportClock("NodeA")
    node_b = LamportClock("NodeB")

    # Node A executes local event
    node_a.tick()  # C=1
    assert node_a.value == 1

    # Node A sends message to Node B
    msg_ts = node_a.send_event()  # C=2
    assert msg_ts == 2

    # Node B (currently C=0) receives message
    node_b.receive_event(msg_ts)  # max(0, 2) + 1 = 3
    assert node_b.value == 3
    print(f"Lamport Causality: NodeA sent at C={msg_ts} -> NodeB received and advanced to C={node_b.value}")


def demo_vector_clocks_and_concurrency():
    print("\n--- 2. VECTOR CLOCKS & CAUSAL CONFLICT DETECTION DEMO ---")
    nodes = ["NodeA", "NodeB", "NodeC"]
    v_a = VectorClock("NodeA", nodes)
    v_b = VectorClock("NodeB", nodes)
    v_c = VectorClock("NodeC", nodes)

    # 1. Node A does local event
    v_a.tick()  # A: [1, 0, 0]
    # 2. Node A sends to Node B
    msg_a = v_a.send_event()  # A: [2, 0, 0]
    v_b.receive_event(msg_a)  # B: [2, 1, 0]

    # Verify causal precedence: v_a snapshot precedes v_b
    relation_1 = VectorClock.compare(msg_a, v_b.clock)
    assert relation_1 == CausalRelation.PRECEDES
    print(f"Causal Precedence Verified: NodeA {msg_a} PRECEDES NodeB {v_b.clock}")

    # 3. CONCURRENT CONFLICT SIMULATION (Branching writes)
    # Node B writes locally: B: [2, 2, 0]
    v_b.tick()
    # Concurrently, Node C writes locally without knowing Node B's state: C: [0, 0, 1]
    v_c.tick()

    # Compare Node B and Node C clocks
    conflict_relation = VectorClock.compare(v_b.clock, v_c.clock)
    print(f"Concurrent Writes Evaluated: NodeB {v_b.clock} vs NodeC {v_c.clock} -> Result: {conflict_relation}")
    assert conflict_relation == CausalRelation.CONCURRENT, "Uncoordinated writes must be detected as CONCURRENT"
    print("Vector Clock accurately detected concurrent branch conflict (V_B || V_C)!")


def demo_hybrid_logical_clocks():
    print("\n--- 3. HYBRID LOGICAL CLOCK (HLC) MONOTONICITY DEMO ---")
    hlc = HybridLogicalClock("Server1")

    # 1. Normal physical time progression
    t1 = hlc.now(physical_time_ms=1000)
    t2 = hlc.now(physical_time_ms=1001)
    print(f"Normal Progression: {t1} -> {t2}")
    assert t1 < t2
    assert t2.logical == 0

    # 2. Simulate Physical Clock Drift BACKWARDS (e.g. NTP stepping back from 1001 to 950ms)
    print("Simulating NTP skew stepping clock backwards by 51ms (1001ms -> 950ms)...")
    t3 = hlc.now(physical_time_ms=950)
    print(f"Post-Drift Timestamp: {t3}")

    # Physical time jumped back, but HLC MUST remain strictly monotonic!
    assert t2 < t3
    assert t3.physical_ms == 1001  # Held to highest observed physical time
    assert t3.logical == 1          # Incremented counter to preserve ordering
    print(f"Monotonicity preserved: t2 < t3 ({t2} < {t3}) despite backward clock drift!")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING DISTRIBUTED CLOCKS SIMULATOR (LAMPORT, VECTOR & HLC)")
    print("=" * 70)
    demo_lamport_timestamps()
    demo_vector_clocks_and_concurrency()
    demo_hybrid_logical_clocks()
    print("\n" + "=" * 70)
    print("ALL DISTRIBUTED CLOCK TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
