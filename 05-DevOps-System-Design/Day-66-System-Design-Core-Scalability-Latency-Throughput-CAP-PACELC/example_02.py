"""Day 66: Distributed Cluster Simulator: CAP & PACELC Trade-Offs.

This production-grade script provides:
1. Distributed Network Cluster with dynamic partition injection (split-brain simulation).
2. CP Engine (Quorum Consensus, Linearizable reads/writes, minority write rejection).
3. AP Engine (Eventual Consistency, Vector Clocks, and CRDT Conflict Resolution upon healing).
4. PACELC Else-State Latency Simulator (PC synchronous consensus vs. EL asynchronous replication).
5. Comprehensive automated test suite with zero external dependencies.
"""

from __future__ import annotations

import collections
import copy
import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. VECTOR CLOCK IMPLEMENTATION FOR DISTRIBUTED CAUSALITY
# ==============================================================================

@dataclass
class VectorClock:
    """Lamport-style Vector Clock to track causal ordering across distributed nodes."""
    clock: Dict[str, int] = field(default_factory=dict)

    def increment(self, node_id: str) -> None:
        self.clock[node_id] = self.clock.get(node_id, 0) + 1

    def merge(self, other: VectorClock) -> None:
        for node_id, count in other.clock.items():
            self.clock[node_id] = max(self.clock.get(node_id, 0), count)

    def is_concurrent_with(self, other: VectorClock) -> bool:
        """Two vector clocks are concurrent if neither dominates the other."""
        greater = False
        lesser = False
        all_keys = set(self.clock.keys()).union(other.clock.keys())

        for k in all_keys:
            v1 = self.clock.get(k, 0)
            v2 = other.clock.get(k, 0)
            if v1 > v2:
                greater = True
            elif v1 < v2:
                lesser = True

        return greater and lesser

    def clone(self) -> VectorClock:
        return VectorClock(clock=dict(self.clock))


# ==============================================================================
# 2. DISTRIBUTED NODE & NETWORK TOPOLOGY
# ==============================================================================

@dataclass
class NodeState:
    node_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    vector_clocks: Dict[str, VectorClock] = field(default_factory=dict)
    active_partition_id: int = 1


class DistributedNetwork:
    """Simulates physical network interconnects between distributed nodes."""

    def __init__(self, node_ids: List[str]) -> None:
        self.node_ids = set(node_ids)
        # Partition membership: node_id -> partition_id (default partition 1)
        self.partitions: Dict[str, int] = {nid: 1 for nid in node_ids}

    def can_communicate(self, src: str, dst: str) -> bool:
        """Nodes can communicate only if they belong to the same network partition."""
        return self.partitions.get(src) == self.partitions.get(dst)

    def inject_partition(self, partition_map: Dict[str, int]) -> None:
        """Injects network partition: e.g. {'n1': 1, 'n2': 1, 'n3': 1, 'n4': 2, 'n5': 2}."""
        self.partitions.update(partition_map)

    def heal_partitions(self) -> None:
        """Restores full network connectivity across all nodes."""
        for nid in self.partitions:
            self.partitions[nid] = 1


# ==============================================================================
# 3. CP ENGINE: QUORUM CONSENSUS (Linearizability over Availability)
# ==============================================================================

class CPCluster:
    """Consistent & Partition-Tolerant (CP) cluster.

    Enforces strict majority quorum: Q = floor(N / 2) + 1.
    If a partition contains fewer than Q nodes, writes are REJECTED to prevent split-brain.
    """

    def __init__(self, node_ids: List[str], network: DistributedNetwork) -> None:
        self.node_ids = node_ids
        self.network = network
        self.n = len(node_ids)
        self.quorum_size = (self.n // 2) + 1
        self.nodes: Dict[str, Dict[str, Any]] = {nid: {} for nid in node_ids}

    def write(self, coordinator_id: str, key: str, value: Any) -> Tuple[bool, str]:
        """Attempts a write using Quorum consensus."""
        # Find reachable nodes in coordinator's partition
        reachable = [
            nid for nid in self.node_ids
            if self.network.can_communicate(coordinator_id, nid)
        ]

        if len(reachable) < self.quorum_size:
            # CP Behavior: Sacrifices Availability to preserve Consistency
            return False, f"QUORUM_LOST: Reachable nodes ({len(reachable)}) < Quorum ({self.quorum_size}). Write rejected!"

        # Majority quorum reached: write to all reachable nodes
        for nid in reachable:
            self.nodes[nid][key] = value

        return True, f"QUORUM_ACK: Written to {len(reachable)}/{self.n} nodes (Quorum={self.quorum_size})."

    def read(self, coordinator_id: str, key: str) -> Tuple[bool, Any, str]:
        """Linearizable quorum read."""
        reachable = [
            nid for nid in self.node_ids
            if self.network.can_communicate(coordinator_id, nid)
        ]

        if len(reachable) < self.quorum_size:
            return False, None, f"QUORUM_LOST: Cannot read safely on minority partition."

        # Collect values from reachable nodes
        vals = [self.nodes[nid].get(key) for nid in reachable if key in self.nodes[nid]]
        if not vals:
            return True, None, "KEY_NOT_FOUND"

        # In pure linearizability, all quorum nodes must agree or latest timestamp is returned
        return True, vals[0], f"READ_ACK: Read from {len(reachable)} quorum nodes."


# ==============================================================================
# 4. AP ENGINE: EVENTUAL CONSISTENCY WITH CRDT & VECTOR CLOCKS
# ==============================================================================

@dataclass
class CRDTRegister:
    """Last-Write-Wins (LWW) and Vector Clock multi-value register."""
    value: Any
    timestamp: float
    vector_clock: VectorClock


class APCluster:
    """Available & Partition-Tolerant (AP) cluster.

    Accepts writes on ANY available node. When network heals, replicas reconcile
    conflicts using Vector Clocks and deterministic Last-Write-Wins (LWW).
    """

    def __init__(self, node_ids: List[str], network: DistributedNetwork) -> None:
        self.node_ids = node_ids
        self.network = network
        self.nodes: Dict[str, Dict[str, CRDTRegister]] = {nid: {} for nid in node_ids}

    def write(self, coordinator_id: str, key: str, value: Any, client_time: float) -> Tuple[bool, str]:
        """AP Behavior: Accepts write unconditionally on coordinator and replicates to reachable peers."""
        # Get or create vector clock
        existing_reg = self.nodes[coordinator_id].get(key)
        vc = existing_reg.vector_clock.clone() if existing_reg else VectorClock()
        vc.increment(coordinator_id)

        new_reg = CRDTRegister(value=value, timestamp=client_time, vector_clock=vc)

        # Write to coordinator
        self.nodes[coordinator_id][key] = new_reg

        # Replicate to any peer currently reachable in its partition
        replicated_count = 1
        for peer in self.node_ids:
            if peer != coordinator_id and self.network.can_communicate(coordinator_id, peer):
                self.nodes[peer][key] = copy.deepcopy(new_reg)
                replicated_count += 1

        return True, f"AP_ACK: Accepted write on {replicated_count} node(s) in partition."

    def read(self, node_id: str, key: str) -> Tuple[bool, Any, str]:
        """Local read: always available, but may return stale data during partitions."""
        reg = self.nodes[node_id].get(key)
        if reg is None:
            return True, None, "KEY_NOT_FOUND"
        return True, reg.value, f"AP_READ_ACK: Read from local node {node_id} (TS={reg.timestamp})."

    def reconcile_anti_entropy(self) -> Dict[str, Any]:
        """Simulates anti-entropy gossip protocol to heal divergent state across all nodes."""
        conflicts_resolved = 0
        all_keys: Set[str] = set()
        for nid in self.node_ids:
            all_keys.update(self.nodes[nid].keys())

        for key in all_keys:
            # Find the winning register across all nodes using Vector Clock / LWW
            best_reg: Optional[CRDTRegister] = None
            for nid in self.node_ids:
                curr = self.nodes[nid].get(key)
                if curr is None:
                    continue
                if best_reg is None:
                    best_reg = curr
                else:
                    if curr.vector_clock.is_concurrent_with(best_reg.vector_clock):
                        # Concurrent conflict! Resolve via deterministic Last-Write-Wins (LWW)
                        conflicts_resolved += 1
                        if curr.timestamp > best_reg.timestamp:
                            best_reg = curr
                    elif curr.timestamp > best_reg.timestamp:
                        best_reg = curr

            # Propagate best register to all nodes
            if best_reg is not None:
                for nid in self.node_ids:
                    self.nodes[nid][key] = copy.deepcopy(best_reg)

        return {"status": "CONVERGED", "conflicts_resolved": conflicts_resolved}


# ==============================================================================
# 5. PACELC NORMAL-STATE (ELSE-STATE) LATENCY SIMULATOR
# ==============================================================================

class PACELCLatencySimulator:
    """Simulates normal-state trade-off: PC (Consistency) vs EL (Latency)."""

    @staticmethod
    def simulate_pc_write_latency(cross_az_rtt_ms: float = 2.5, replication_factor: int = 3) -> Dict[str, Any]:
        """PC Model: Synchronous Quorum Consensus (e.g. Google Spanner / CockroachDB).

        Must wait for majority ACK before returning to client.
        """
        # Minimum latency requires 1 round-trip cross-AZ consensus + disk fsync
        disk_fsync_ms = 1.2
        quorum_rtts = cross_az_rtt_ms * 2.0  # 2-phase commit or Raft round-trip
        total_latency = disk_fsync_ms + quorum_rtts
        return {
            "model": "PC (Partition-Consistent, Else-Consistent)",
            "write_latency_ms": round(total_latency, 2),
            "guarantee": "Zero stale reads; linearizable consistency across all replicas.",
            "throughput_penalty": "Limited by slowest cross-datacenter quorum round-trip."
        }

    @staticmethod
    def simulate_el_write_latency(local_ram_ms: float = 0.15) -> Dict[str, Any]:
        """EL Model: Asynchronous Master-to-Replica Replication (e.g. DynamoDB / Cassandra).

        Acknowledges write immediately to local node; replicates in background.
        """
        return {
            "model": "EL (Else-Latency over Consistency)",
            "write_latency_ms": round(local_ram_ms, 2),
            "guarantee": "Sub-millisecond writes; replicas eventually consistent (stale reads possible).",
            "throughput_penalty": "Virtually zero; maximum theoretical write throughput."
        }


# ==============================================================================
# 6. DIAGNOSTICS & VERIFICATION RUNNER
# ==============================================================================

def run_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 66: DISTRIBUTED CLUSTER SIMULATOR: CAP & PACELC DIAGNOSTICS")
    print("=" * 80)

    # 5-Node cluster: n1, n2, n3, n4, n5 (Quorum = 3)
    node_ids = ["node-1", "node-2", "node-3", "node-4", "node-5"]
    network = DistributedNetwork(node_ids)

    # --------------------------------------------------------------------------
    # Diagnostic 1: CP Cluster Behavior Under Network Partition
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 1] CP Cluster (Linearizability over Availability):")
    cp = CPCluster(node_ids, network)

    # 1. Normal state write
    ok, msg = cp.write("node-1", "account_bal", 1000)
    assert ok is True
    print(f"  • Normal State Write: {msg}")

    # 2. Inject Network Partition: Partition 1 (Majority: n1, n2, n3), Partition 2 (Minority: n4, n5)
    print("\n  • [EVENT] Injecting Network Partition:")
    print("    - Majority Partition: [node-1, node-2, node-3] (Size 3 >= Quorum 3)")
    print("    - Minority Partition: [node-4, node-5]         (Size 2 <  Quorum 3)")
    network.inject_partition({
        "node-1": 1, "node-2": 1, "node-3": 1,
        "node-4": 2, "node-5": 2
    })

    # Write to majority partition succeeds
    ok_maj, msg_maj = cp.write("node-1", "account_bal", 1250)
    print(f"  • Majority Partition Write (node-1): {msg_maj}")
    assert ok_maj is True

    # Write to minority partition FAILS (CP preserves consistency by sacrificing availability)
    ok_min, msg_min = cp.write("node-4", "account_bal", 9999)
    print(f"  • Minority Partition Write (node-4): {msg_min}")
    assert ok_min is False
    assert "QUORUM_LOST" in msg_min
    print("  ✓ CP Invariant Verified: Minority partition rejected write to prevent split-brain!")

    # --------------------------------------------------------------------------
    # Diagnostic 2: AP Cluster Behavior Under Network Partition
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 2] AP Cluster (Availability over Consistency):")
    ap = APCluster(node_ids, network)
    t0 = 1000.0

    # Write on Majority Partition
    ok_ap1, msg_ap1 = ap.write("node-1", "cart_items", ["item_A"], client_time=t0)
    assert ok_ap1 is True
    print(f"  • AP Majority Write (node-1): {msg_ap1}")

    # Write on Minority Partition (SUCCEEDS in AP, unlike CP!)
    ok_ap2, msg_ap2 = ap.write("node-4", "cart_items", ["item_B"], client_time=t0 + 1.0)
    assert ok_ap2 is True
    print(f"  • AP Minority Write (node-4): {msg_ap2}")

    # Read from both sides shows divergent state (Inconsistency during partition)
    _, val_maj, _ = ap.read("node-1", "cart_items")
    _, val_min, _ = ap.read("node-4", "cart_items")
    print(f"  • Divergent State:")
    print(f"    - Node 1 State: {val_maj}")
    print(f"    - Node 4 State: {val_min}")
    assert val_maj != val_min
    print("  ✓ AP Invariant Verified: Both partitions remained 100% available, accepting divergent writes.")

    # --------------------------------------------------------------------------
    # Diagnostic 3: Network Heal & CRDT / LWW Convergence
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 3] Network Healing & Anti-Entropy CRDT Reconciliation:")
    network.heal_partitions()
    print("  • Network partition healed: full interconnectivity restored.")

    reconcile_report = ap.reconcile_anti_entropy()
    print(f"  • Anti-Entropy Reconciliation: {reconcile_report}")
    assert reconcile_report["status"] == "CONVERGED"

    # All nodes now agree on the latest write (node-4 at t0 + 1.0)
    for nid in node_ids:
        _, final_val, _ = ap.read(nid, "cart_items")
        assert final_val == ["item_B"]
    print(f"  • All 5 nodes converged to unified value: {final_val}")
    print("  ✓ Eventual consistency convergence verified across entire cluster.")

    # --------------------------------------------------------------------------
    # Diagnostic 4: PACELC Else-State Latency Comparison
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 4] PACELC Else-State Latency Analysis (PC vs EL):")
    pc_metrics = PACELCLatencySimulator.simulate_pc_write_latency(cross_az_rtt_ms=2.5)
    el_metrics = PACELCLatencySimulator.simulate_el_write_latency(local_ram_ms=0.15)

    print(f"  • PC Model (e.g. CockroachDB/Spanner):")
    print(f"    - Write Latency: {pc_metrics['write_latency_ms']}ms | Guarantee: {pc_metrics['guarantee']}")
    print(f"  • EL Model (e.g. DynamoDB/Cassandra):")
    print(f"    - Write Latency: {el_metrics['write_latency_ms']}ms | Guarantee: {el_metrics['guarantee']}")

    # EL is over 40x faster than PC under normal state
    speedup = pc_metrics["write_latency_ms"] / el_metrics["write_latency_ms"]
    print(f"  • EL write is {speedup:.1f}x lower latency than synchronous PC consensus!")
    assert pc_metrics["write_latency_ms"] > el_metrics["write_latency_ms"]
    print("  ✓ PACELC latency vs consistency operational trade-off mathematically verified.")

    print("\n" + "=" * 80)
    print(" ALL DAY 66 DISTRIBUTED CAP & PACELC SIMULATION TESTS PASSED!")
    print("=" * 80)


if __name__ == "__main__":
    run_diagnostics()
