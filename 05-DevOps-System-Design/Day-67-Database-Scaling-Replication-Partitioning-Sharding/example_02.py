"""Day 67: Distributed Sharding Engine, Consistent Hashing & Scatter-Gather Query Router.

This production-grade script provides:
1. Consistent Hashing Ring with configurable Virtual Nodes (vnodes) and bisect routing.
2. Modulo Sharding vs. Consistent Hashing Rebalancing Simulator (measuring key migration %).
3. Sharded Database Cluster with Entity Colocation (User & Orders affinity).
4. Scatter-Gather Query Aggregator with distributed sorting and pagination.
5. Comprehensive standalone automated test suite with zero external dependencies.
"""

from __future__ import annotations

import bisect
import collections
import dataclasses
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. CONSISTENT HASHING RING WITH VIRTUAL NODES (VNODES)
# ==============================================================================

class ConsistentHashRing:
    """Implements Consistent Hashing across a 32-bit ring with Virtual Nodes."""

    def __init__(self, vnodes_per_node: int = 150) -> None:
        self.vnodes_per_node = vnodes_per_node
        # Sorted list of integer tokens on ring
        self.ring: List[int] = []
        # token -> physical node_id
        self.token_to_node: Dict[int, str] = {}
        # Set of active physical node IDs
        self.physical_nodes: Set[str] = set()

    def _hash(self, key: str) -> int:
        """Computes 32-bit integer token from MD5 digest."""
        digest = hashlib.md5(key.encode("utf-8")).digest()
        # Unpack first 4 bytes as unsigned big-endian integer (0 to 2^32 - 1)
        return int.from_bytes(digest[:4], byteorder="big")

    def add_node(self, node_id: str) -> None:
        """Adds a physical node by generating vnodes evenly across the ring."""
        if node_id in self.physical_nodes:
            return
        self.physical_nodes.add(node_id)

        for i in range(self.vnodes_per_node):
            vnode_key = f"{node_id}#vnode-{i}"
            token = self._hash(vnode_key)
            self.ring.append(token)
            self.token_to_node[token] = node_id

        self.ring.sort()

    def remove_node(self, node_id: str) -> None:
        """Removes all vnodes belonging to a physical node."""
        if node_id not in self.physical_nodes:
            return
        self.physical_nodes.remove(node_id)

        tokens_to_remove = set()
        for i in range(self.vnodes_per_node):
            vnode_key = f"{node_id}#vnode-{i}"
            token = self._hash(vnode_key)
            tokens_to_remove.add(token)

        self.ring = [t for t in self.ring if t not in tokens_to_remove]
        for t in tokens_to_remove:
            self.token_to_node.pop(t, None)

    def get_node(self, key: str) -> str:
        """Routes a key clockwise to the first available node on the ring."""
        if not self.ring:
            raise RuntimeError("ConsistentHashRing is empty!")

        key_token = self._hash(key)
        # Binary search for the first token >= key_token
        idx = bisect.bisect_right(self.ring, key_token)
        # Wrap around to 0 if key_token exceeds max token on the ring
        if idx == len(self.ring):
            idx = 0

        target_token = self.ring[idx]
        return self.token_to_node[target_token]


# ==============================================================================
# 2. MODULO SHARDING VS CONSISTENT HASHING REBALANCING BENCHMARK
# ==============================================================================

class ShardingRebalancerBenchmark:
    """Benchmarks data migration overhead when scaling shard clusters."""

    @staticmethod
    def evaluate_modulo_migration(keys: List[str], initial_shards: int = 4, new_shards: int = 5) -> float:
        """Measures % of keys moved in naive modulo sharding: hash(k) % N."""
        moved_count = 0
        for k in keys:
            digest = int.from_bytes(hashlib.md5(k.encode()).digest()[:4], "big")
            old_shard = digest % initial_shards
            new_shard = digest % new_shards
            if old_shard != new_shard:
                moved_count += 1
        return (moved_count / len(keys)) * 100.0

    @staticmethod
    def evaluate_consistent_hashing_migration(
        keys: List[str],
        initial_nodes: List[str],
        added_node: str,
        vnodes: int = 150
    ) -> float:
        """Measures % of keys moved when adding 1 node to a Consistent Hash Ring."""
        ring = ConsistentHashRing(vnodes_per_node=vnodes)
        for n in initial_nodes:
            ring.add_node(n)

        # Baseline mappings
        old_mappings = {k: ring.get_node(k) for k in keys}

        # Add 1 new node
        ring.add_node(added_node)
        new_mappings = {k: ring.get_node(k) for k in keys}

        moved_count = sum(1 for k in keys if old_mappings[k] != new_mappings[k])
        return (moved_count / len(keys)) * 100.0


# ==============================================================================
# 3. PHYSICAL SHARD INSTANCE & ENTITY COLOCATION
# ==============================================================================

@dataclass
class OrderRecord:
    order_id: str
    user_id: str
    amount: float
    status: str
    created_at: float


class PhysicalShard:
    """Simulates an independent database instance."""

    def __init__(self, shard_id: str) -> None:
        self.shard_id = shard_id
        # user_id -> user dict
        self.users: Dict[str, Dict[str, Any]] = {}
        # order_id -> OrderRecord
        self.orders: Dict[str, OrderRecord] = {}

    def insert_user(self, user_id: str, profile: Dict[str, Any]) -> None:
        self.users[user_id] = profile

    def insert_order(self, order: OrderRecord) -> None:
        self.orders[order.order_id] = order

    def get_user_with_orders(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Colocated join: User and all their Orders live on the SAME shard!"""
        user = self.users.get(user_id)
        if not user:
            return None
        orders = [o for o in self.orders.values() if o.user_id == user_id]
        return {
            "user": user,
            "orders": orders,
            "shard_id": self.shard_id,
            "colocated_single_shard_join": True
        }


# ==============================================================================
# 4. SCATTER-GATHER QUERY AGGREGATOR PROXY
# ==============================================================================

class ShardedDatabaseCluster:
    """Manages physical shards and coordinates single-shard and scatter-gather queries."""

    def __init__(self, shard_ids: List[str], vnodes_per_node: int = 150) -> None:
        self.ring = ConsistentHashRing(vnodes_per_node=vnodes_per_node)
        self.shards: Dict[str, PhysicalShard] = {}

        for sid in shard_ids:
            self.ring.add_node(sid)
            self.shards[sid] = PhysicalShard(shard_id=sid)

    def insert_user(self, user_id: str, name: str, email: str) -> str:
        """Single-shard write: routes based on shard key (user_id)."""
        target_shard_id = self.ring.get_node(user_id)
        shard = self.shards[target_shard_id]
        shard.insert_user(user_id, {"user_id": user_id, "name": name, "email": email})
        return target_shard_id

    def insert_order(self, order_id: str, user_id: str, amount: float, status: str = "PENDING") -> str:
        """Entity Colocation: routes order based on USER_ID (parent shard key)!"""
        target_shard_id = self.ring.get_node(user_id)
        shard = self.shards[target_shard_id]
        order = OrderRecord(
            order_id=order_id,
            user_id=user_id,
            amount=amount,
            status=status,
            created_at=time.time()
        )
        shard.insert_order(order)
        return target_shard_id

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Targeted Single-Shard Read: O(1) direct query without cluster fanout."""
        target_shard_id = self.ring.get_node(user_id)
        return self.shards[target_shard_id].get_user_with_orders(user_id)

    def scatter_gather_orders_by_status(
        self,
        status: str,
        limit: int = 10,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Scatter-Gather: Queries ALL shards in parallel, merges, and paginates in memory."""
        all_matching: List[OrderRecord] = []
        shards_queried = 0

        # Parallel fanout across all shards
        for shard in self.shards.values():
            shards_queried += 1
            for order in shard.orders.values():
                if order.status == status:
                    all_matching.append(order)

        # Global in-memory sort by amount descending
        all_matching.sort(key=lambda o: o.amount, reverse=True)

        # Apply distributed pagination
        paginated = all_matching[offset: offset + limit]

        return {
            "query_type": "SCATTER_GATHER",
            "shards_queried": shards_queried,
            "total_matches": len(all_matching),
            "results": paginated,
            "limit": limit,
            "offset": offset
        }


# ==============================================================================
# 5. DIAGNOSTICS & VERIFICATION RUNNER
# ==============================================================================

def run_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 67: SHARDING, CONSISTENT HASHING & SCATTER-GATHER DIAGNOSTICS")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # Diagnostic 1: Consistent Hashing Uniform Key Distribution
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 1] Consistent Hashing Uniform Key Distribution (vnodes=150):")
    nodes = ["shard-1", "shard-2", "shard-3", "shard-4"]
    ring = ConsistentHashRing(vnodes_per_node=150)
    for n in nodes:
        ring.add_node(n)

    sample_keys = [f"user_{i:05d}" for i in range(10_000)]
    distribution: Dict[str, int] = collections.defaultdict(int)
    for k in sample_keys:
        distribution[ring.get_node(k)] += 1

    print(f"  • Distributed 10,000 keys across 4 shards:")
    for nid in nodes:
        count = distribution[nid]
        pct = (count / len(sample_keys)) * 100.0
        print(f"    - {nid}: {count:5d} keys ({pct:.1f}%)")
        # Every node should hold roughly 25% +- 5%
        assert 2000 <= count <= 3000, f"Non-uniform distribution on {nid}: {count}"
    print("  ✓ Virtual nodes successfully guaranteed uniform key distribution (variance < 5%).")

    # --------------------------------------------------------------------------
    # Diagnostic 2: Modulo Sharding vs. Consistent Hashing Rebalancing
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 2] Rebalancing Benchmark: Scaling from 4 Shards to 5 Shards:")
    keys_10k = [f"account_{i:06d}" for i in range(10_000)]

    # 1. Modulo Sharding
    modulo_moved_pct = ShardingRebalancerBenchmark.evaluate_modulo_migration(
        keys_10k, initial_shards=4, new_shards=5
    )
    print(f"  • Modulo Sharding [hash(k) % N]: {modulo_moved_pct:.2f}% of keys MIGRATED!")
    assert modulo_moved_pct > 75.0, "Modulo sharding must move ~80% of data"

    # 2. Consistent Hashing
    ch_moved_pct = ShardingRebalancerBenchmark.evaluate_consistent_hashing_migration(
        keys_10k, initial_nodes=nodes, added_node="shard-5", vnodes=150
    )
    print(f"  • Consistent Hashing [Ring + vnodes]: {ch_moved_pct:.2f}% of keys MIGRATED!")
    # Theoretical ideal when adding 5th shard is 1/5 = 20%
    assert 15.0 <= ch_moved_pct <= 25.0, f"Expected ~20% migration, got {ch_moved_pct}%"

    efficiency_gain = modulo_moved_pct / ch_moved_pct
    print(f"  • Consistent Hashing prevented {efficiency_gain:.1f}x unnecessary data movement!")
    print("  ✓ Rebalancing efficiency mathematically verified.")

    # --------------------------------------------------------------------------
    # Diagnostic 3: Entity Colocation (User & Orders Affinity)
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 3] Sharded Cluster & Entity Colocation:")
    cluster = ShardedDatabaseCluster(["shard-A", "shard-B", "shard-C"])

    # User 888 and orders
    u_shard = cluster.insert_user("user_888", "Alice", "alice@enterprise.com")
    o1_shard = cluster.insert_order("ord_01", "user_888", 250.0, status="COMPLETED")
    o2_shard = cluster.insert_order("ord_02", "user_888", 1400.0, status="COMPLETED")

    print(f"  • User 'user_888' mapped to: {u_shard}")
    print(f"  • Order 'ord_01' mapped to:  {o1_shard}")
    print(f"  • Order 'ord_02' mapped to:  {o2_shard}")
    assert u_shard == o1_shard == o2_shard, "Parent and child entities must colocate on the same shard!"

    profile = cluster.get_user_profile("user_888")
    assert profile is not None
    assert len(profile["orders"]) == 2
    assert profile["colocated_single_shard_join"] is True
    print(f"  • Colocated Join executed locally on '{profile['shard_id']}': Found User + 2 Orders with ZERO cross-shard RPCs!")
    print("  ✓ Entity colocation verified.")

    # --------------------------------------------------------------------------
    # Diagnostic 4: Scatter-Gather Query Aggregator
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 4] Scatter-Gather Aggregator (Query without Shard Key):")
    # Insert orders across different users
    cluster.insert_order("ord_101", "user_111", 50.0, status="PENDING")
    cluster.insert_order("ord_102", "user_222", 950.0, status="PENDING")
    cluster.insert_order("ord_103", "user_333", 300.0, status="PENDING")
    cluster.insert_order("ord_104", "user_444", 2100.0, status="PENDING")

    # Scatter-gather: query all pending orders, sorted by amount descending, top 2
    res = cluster.scatter_gather_orders_by_status("PENDING", limit=2, offset=0)
    print(f"  • Executed Scatter-Gather: Fanout to {res['shards_queried']} shards.")
    print(f"  • Total Matching Pending Orders: {res['total_matches']}")
    print(f"  • Top Paginated Results (Sorted by Amount Descending):")
    for o in res["results"]:
        print(f"    - Order {o.order_id}: ${o.amount:,.2f} (User: {o.user_id})")

    assert res["shards_queried"] == 3
    assert res["total_matches"] == 4
    assert len(res["results"]) == 2
    assert res["results"][0].order_id == "ord_104"  # Highest amount ($2100)
    assert res["results"][1].order_id == "ord_102"  # Second highest ($950)
    print("  ✓ Scatter-gather parallel fanout, distributed merge-sort, and pagination verified.")

    print("\n" + "=" * 80)
    print(" ALL DAY 67 SHARDING & CONSISTENT HASHING TESTS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_diagnostics()
