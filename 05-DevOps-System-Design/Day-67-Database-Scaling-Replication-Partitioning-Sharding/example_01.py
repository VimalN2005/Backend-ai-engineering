"""Day 67: Primary-Replica Replication, Lag Simulator & Read-Your-Own-Writes Router.

This production-grade script provides:
1. Primary-Replica Cluster with Synchronous, Semi-Synchronous, and Asynchronous modes.
2. Write-Ahead Log (WAL) with monotonic Log Sequence Numbers (LSN).
3. Replication Lag Simulator with stale read anomaly reproduction.
4. Read-Your-Own-Writes Consistency Router (Time-Window Pinning & LSN-Guarded routing).
5. Automated Failover & Split-Brain Fencing Simulator (Quorum Lease Demotion).
6. Comprehensive automated test suite with zero external dependencies.
"""

from __future__ import annotations

import collections
import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. WRITE-AHEAD LOG (WAL) RECORD & REPLICATION ENGINE
# ==============================================================================

@dataclass
class WALRecord:
    lsn: int               # Monotonic Log Sequence Number
    key: str
    value: Any
    timestamp: float
    client_id: str


class DatabaseNode:
    """Represents an individual database instance (Primary or Read Replica)."""

    def __init__(self, node_id: str, is_primary: bool = False) -> None:
        self.node_id = node_id
        self.is_primary = is_primary
        self.storage: Dict[str, Any] = {}
        self.wal: List[WALRecord] = []
        self.applied_lsn = 0
        self.is_alive = True
        self.is_fenced = False  # Set to True if demoted during split-brain

    def apply_record(self, record: WALRecord) -> None:
        """Applies a WAL mutation to the local memory table."""
        self.storage[record.key] = record.value
        self.wal.append(record)
        self.applied_lsn = record.lsn

    def read(self, key: str) -> Optional[Any]:
        if not self.is_alive:
            raise RuntimeError(f"Cannot read from dead node: {self.node_id}")
        return self.storage.get(key)


# ==============================================================================
# 2. PRIMARY-REPLICA CLUSTER WITH REPLICATION MODES
# ==============================================================================

class ReplicationMode:
    ASYNCHRONOUS = "ASYNCHRONOUS"
    SEMI_SYNCHRONOUS = "SEMI_SYNCHRONOUS"
    SYNCHRONOUS = "SYNCHRONOUS"


class PrimaryReplicaCluster:
    """Manages a Primary database node and multiple Read Replicas."""

    def __init__(
        self,
        num_replicas: int = 2,
        mode: str = ReplicationMode.ASYNCHRONOUS,
        simulated_lag_records: int = 2
    ) -> None:
        self.primary = DatabaseNode(node_id="primary-1", is_primary=True)
        self.replicas: List[DatabaseNode] = [
            DatabaseNode(node_id=f"replica-{i+1}", is_primary=False)
            for i in range(num_replicas)
        ]
        self.mode = mode
        self.simulated_lag_records = simulated_lag_records
        self.current_lsn = 0
        # Replicas pending WAL queues to simulate async lag
        self.replica_wal_queues: Dict[str, collections.deque] = {
            r.node_id: collections.deque() for r in self.replicas
        }

    def write(self, key: str, value: Any, client_id: str = "client-default") -> WALRecord:
        """Executes a write transaction on Primary and replicates according to mode."""
        if not self.primary.is_alive or self.primary.is_fenced:
            raise RuntimeError("503 Service Unavailable: Primary database is down or fenced!")

        self.current_lsn += 1
        record = WALRecord(
            lsn=self.current_lsn,
            key=key,
            value=value,
            timestamp=time.time(),
            client_id=client_id
        )

        # 1. Primary commits locally
        self.primary.apply_record(record)

        # 2. Replication dispatch
        if self.mode == ReplicationMode.ASYNCHRONOUS:
            # Stage in queue to simulate network / replay lag
            for r in self.replicas:
                self.replica_wal_queues[r.node_id].append(record)
                # Keep queue depth up to simulated_lag_records
                if len(self.replica_wal_queues[r.node_id]) > self.simulated_lag_records:
                    oldest = self.replica_wal_queues[r.node_id].popleft()
                    r.apply_record(oldest)

        elif self.mode == ReplicationMode.SEMI_SYNCHRONOUS:
            # Must replicate to at least 1 replica synchronously
            if self.replicas:
                ack_replica = self.replicas[0]
                ack_replica.apply_record(record)
            # Remaining replicas queue asynchronously
            for r in self.replicas[1:]:
                self.replica_wal_queues[r.node_id].append(record)

        elif self.mode == ReplicationMode.SYNCHRONOUS:
            # All active replicas must apply before commit returns
            for r in self.replicas:
                if r.is_alive:
                    r.apply_record(record)

        return record

    def flush_replica_lag(self) -> None:
        """Forces all pending replication queues to drain to replicas."""
        for r in self.replicas:
            q = self.replica_wal_queues[r.node_id]
            while q:
                record = q.popleft()
                r.apply_record(record)


# ==============================================================================
# 3. READ-YOUR-OWN-WRITES CONSISTENCY ROUTER
# ==============================================================================

class ReadAfterWriteRouter:
    """Intelligent database proxy that prevents serving stale reads to writers."""

    def __init__(self, cluster: PrimaryReplicaCluster, pin_duration_seconds: float = 3.0) -> None:
        self.cluster = cluster
        self.pin_duration = pin_duration_seconds
        # client_id -> (last_write_timestamp, last_write_lsn)
        self.client_write_state: Dict[str, Tuple[float, int]] = {}

    def record_write(self, client_id: str, record: WALRecord) -> None:
        self.client_write_state[client_id] = (record.timestamp, record.lsn)

    def route_read_time_window(self, client_id: str, key: str, now: Optional[float] = None) -> Tuple[Any, str]:
        """Routes to Primary if client wrote recently within the pin duration window."""
        if now is None:
            now = time.time()

        last_write = self.client_write_state.get(client_id)
        if last_write is not None:
            write_time, _ = last_write
            if (now - write_time) < self.pin_duration:
                # Time window active -> Read from Primary to guarantee fresh data
                val = self.cluster.primary.read(key)
                return val, f"ROUTED_PRIMARY (Active write window: {now - write_time:.2f}s < {self.pin_duration}s)"

        # Safe to read from load-balanced read replica
        replica = self.cluster.replicas[0]
        val = replica.read(key)
        return val, f"ROUTED_REPLICA ({replica.node_id})"

    def route_read_lsn_guarded(self, client_id: str, key: str) -> Tuple[Any, str]:
        """Routes to Replica ONLY if replica applied_lsn >= client_required_lsn."""
        last_write = self.client_write_state.get(client_id)
        required_lsn = last_write[1] if last_write else 0

        # Find any replica that has caught up with required LSN
        eligible_replicas = [
            r for r in self.cluster.replicas
            if r.is_alive and r.applied_lsn >= required_lsn
        ]

        if eligible_replicas:
            chosen = eligible_replicas[0]
            return chosen.read(key), f"ROUTED_REPLICA ({chosen.node_id} LSN {chosen.applied_lsn} >= Req {required_lsn})"

        # All replicas are lagging behind client LSN -> Route to Primary
        return self.cluster.primary.read(key), f"ROUTED_PRIMARY (Replicas lagging behind client LSN {required_lsn})"


# ==============================================================================
# 4. AUTOMATED FAILOVER & SPLIT-BRAIN FENCING SIMULATOR
# ==============================================================================

class HighAvailabilityManager:
    """Simulates automated health detection, standby promotion, and quorum fencing."""

    def __init__(self, cluster: PrimaryReplicaCluster) -> None:
        self.cluster = cluster

    def simulate_primary_crash_and_failover(self) -> DatabaseNode:
        """Simulates primary hardware crash and promotes the replica with highest LSN."""
        # 1. Crash current primary
        old_primary = self.cluster.primary
        old_primary.is_alive = False
        old_primary.is_fenced = True

        # 2. Elect standby replica with the highest applied_lsn (Zero RPO priority)
        alive_replicas = [r for r in self.cluster.replicas if r.is_alive]
        if not alive_replicas:
            raise RuntimeError("CRITICAL: All database nodes are dead!")

        promoted_replica = max(alive_replicas, key=lambda r: r.applied_lsn)
        self.cluster.replicas.remove(promoted_replica)

        # 3. Promote to new primary
        promoted_replica.is_primary = True
        self.cluster.primary = promoted_replica

        return promoted_replica

    def simulate_network_partition_quorum_fencing(
        self,
        majority_has_primary: bool
    ) -> Dict[str, Any]:
        """Simulates Patroni/etcd quorum fencing to prevent split-brain.

        If the primary is isolated on a minority partition, it MUST fence itself
        (demote to read-only) before the majority partition promotes a new primary.
        """
        if not majority_has_primary:
            # Old primary is isolated in minority partition
            self.cluster.primary.is_fenced = True
            return {
                "action": "PRIMARY_FENCED",
                "status": "Isolated Primary demoted to READ-ONLY (Lost Quorum Lease)",
                "split_brain_prevented": True
            }
        return {
            "action": "QUORUM_MAINTAINED",
            "status": "Primary resides in majority partition; continuing writes",
            "split_brain_prevented": True
        }


# ==============================================================================
# 5. DIAGNOSTICS & VERIFICATION RUNNER
# ==============================================================================

def run_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 67: DATABASE REPLICATION, LAG & READ-AFTER-WRITE ROUTER DIAGNOSTICS")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # Diagnostic 1: Replication Lag & Stale Read Anomaly Reproduction
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 1] Replication Lag & Stale Read Anomaly Reproduction:")
    # Async cluster with 2 records of simulated queue lag
    cluster = PrimaryReplicaCluster(num_replicas=2, mode=ReplicationMode.ASYNCHRONOUS, simulated_lag_records=2)

    # Write 1: Update user email
    rec1 = cluster.write("user:101:email", "alice@newdomain.com", client_id="user-101")
    print(f"  • Write 1 committed to Primary (LSN={rec1.lsn}): user:101:email = alice@newdomain.com")

    # Immediate read directly from replica-1 (without smart routing)
    replica_val = cluster.replicas[0].read("user:101:email")
    print(f"  • Immediate Raw Read on Replica-1: {replica_val} (STALE READ ANOMALY!)")
    assert replica_val is None, "Asynchronous replica must show lag before queue flushes!"
    print("  ✓ Successfully reproduced Read-Your-Own-Writes stale read anomaly on raw replica.")

    # --------------------------------------------------------------------------
    # Diagnostic 2: Read-Your-Own-Writes Router (Time-Window Pinning)
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 2] Read-Your-Own-Writes Router (Time-Window Pinning):")
    router = ReadAfterWriteRouter(cluster, pin_duration_seconds=3.0)
    t0 = time.time()

    # User 101 writes
    rec2 = cluster.write("user:101:name", "Alice Smith", client_id="user-101")
    rec2.timestamp = t0
    router.record_write("user-101", rec2)

    # User 101 reads immediately at t0 + 0.5s (within 3s pin duration)
    val_alice, route_info = router.route_read_time_window("user-101", "user:101:name", now=t0 + 0.5)
    print(f"  • User 101 Read at t+0.5s: '{val_alice}' -> {route_info}")
    assert val_alice == "Alice Smith"
    assert "ROUTED_PRIMARY" in route_info

    # Unrelated user 202 reads at t0 + 0.5s -> Routes to replica (scaled reads)
    val_other, route_other = router.route_read_time_window("user-202", "user:101:name", now=t0 + 0.5)
    print(f"  • User 202 Read at t+0.5s: '{val_other}' -> {route_other}")
    assert "ROUTED_REPLICA" in route_other

    # User 101 reads after pin expires (t0 + 5.0s > 3.0s pin duration)
    # First flush lag so replica is ready
    cluster.flush_replica_lag()
    val_expired, route_expired = router.route_read_time_window("user-101", "user:101:name", now=t0 + 5.0)
    print(f"  • User 101 Read at t+5.0s: '{val_expired}' -> {route_expired}")
    assert "ROUTED_REPLICA" in route_expired
    print("  ✓ Time-window session pinning verified: fresh writes hit Primary, aging reads hit Replicas.")

    # --------------------------------------------------------------------------
    # Diagnostic 3: LSN-Guarded Read Routing
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 3] LSN-Guarded Read Routing:")
    # Write new setting
    rec3 = cluster.write("user:101:theme", "dark_mode", client_id="user-101")
    router.record_write("user-101", rec3)

    # Replicas are still lagging (applied_lsn < rec3.lsn)
    val_lsn, info_lsn = router.route_read_lsn_guarded("user-101", "user:101:theme")
    print(f"  • LSN Read before replica sync: '{val_lsn}' -> {info_lsn}")
    assert "ROUTED_PRIMARY" in info_lsn
    assert val_lsn == "dark_mode"

    # Now replica catches up to LSN
    cluster.flush_replica_lag()
    val_lsn2, info_lsn2 = router.route_read_lsn_guarded("user-101", "user:101:theme")
    print(f"  • LSN Read after replica catch-up: '{val_lsn2}' -> {info_lsn2}")
    assert "ROUTED_REPLICA" in info_lsn2
    assert val_lsn2 == "dark_mode"
    print("  ✓ Monotonic LSN-guarded routing strictly verified.")

    # --------------------------------------------------------------------------
    # Diagnostic 4: Semi-Synchronous Replication Zero RPO Test
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 4] Semi-Synchronous Replication (Zero-RPO Guarantee):")
    semi_cluster = PrimaryReplicaCluster(num_replicas=2, mode=ReplicationMode.SEMI_SYNCHRONOUS)
    semi_rec = semi_cluster.write("fintech:tx:999", {"amount": 5000, "status": "APPROVED"})

    # Semi-sync ensures replica-1 applied immediately
    assert semi_cluster.replicas[0].read("fintech:tx:999") is not None
    print(f"  • Semi-Synchronous Write: Replica-1 applied immediately (LSN={semi_cluster.replicas[0].applied_lsn}).")
    print("  ✓ Verified Semi-Synchronous zero data loss guarantee.")

    # --------------------------------------------------------------------------
    # Diagnostic 5: Automated Failover & Split-Brain Quorum Fencing
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 5] Automated Standby Promotion & Quorum Fencing:")
    ha = HighAvailabilityManager(semi_cluster)

    # 1. Simulate Split-Brain fencing
    fence_report = ha.simulate_network_partition_quorum_fencing(majority_has_primary=False)
    print(f"  • Quorum Fencing Event: {fence_report['status']}")
    assert fence_report["split_brain_prevented"] is True
    assert semi_cluster.primary.is_fenced is True

    # Attempting write on fenced primary must fail
    try:
        semi_cluster.write("test:key", "val")
        assert False, "Fenced primary should not accept writes!"
    except RuntimeError as e:
        print(f"  • Fenced Primary correctly rejected write: {e}")

    # 2. Promote standby replica to new Primary
    new_primary = ha.simulate_primary_crash_and_failover()
    print(f"  • Standby Promotion: Node '{new_primary.node_id}' successfully promoted to NEW PRIMARY.")
    assert new_primary.is_primary is True
    assert new_primary.is_alive is True

    # New primary accepts writes
    new_rec = semi_cluster.write("order:5001", "FULFILLED")
    assert new_primary.read("order:5001") == "FULFILLED"
    print(f"  • New Primary accepted write (LSN={new_rec.lsn}).")
    print("  ✓ Automated failover & split-brain prevention verified.")

    print("\n" + "=" * 80)
    print(" ALL DAY 67 REPLICATION & CONSISTENCY TESTS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_diagnostics()
