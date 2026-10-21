"""
Day 30: Distributed Hash Rings & Rate Limiter Algorithms.

Covers core distributed systems and traffic shaping algorithms:
1. ConsistentHashRing:
   - Implements Karger's Consistent Hashing with Virtual Nodes (Vnodes).
   - Deterministic MD5 integer hashing for cross-process parity.
   - O(log(N * V)) key routing using binary search (bisect_right).
   - Multi-node replica set routing (Dynamo-style successor replication).
2. TokenBucketRateLimiter:
   - In-memory lazy timestamp delta refill (Zero background timer threads).
   - Enforces sustained rate limits while accommodating legitimate traffic bursts.
3. LeakyBucketRateLimiter:
   - Queue-based constant-rate outflow traffic smoothing.
4. Comprehensive Unit Test Suite & Node Addition Key Migration Verification.
5. High-Volume Performance Benchmark: 100,000 key routings & rate limit evaluations.
"""

from bisect import bisect_right
from collections import defaultdict
import hashlib
import time
from typing import Dict, List, Optional, Set


# =====================================================================
# 1. CONSISTENT HASH RING WITH VIRTUAL NODES
# =====================================================================

class ConsistentHashRing:
    """
    Distributed Hash Ring supporting dynamic node membership and replication.
    
    WHY VIRTUAL NODES?
    In a raw hash ring with only physical nodes, hash values cluster non-uniformly,
    causing severe traffic hotspots where one node handles 70%+ of keys.
    Adding V virtual nodes per physical server scatters tokens uniformly across the
    ring, reducing load variance to < 5%.
    
    WHY DETERMINISTIC MD5 INSTEAD OF PYTHON HASH()?
    Python's built-in `hash()` is randomized on each process startup (PYTHONHASHSEED)
    to prevent HashDoS attacks. In distributed systems, hashing MUST be deterministic
    and identical across all client and server processes.
    """

    def __init__(self, vnodes_per_node: int = 150):
        if vnodes_per_node <= 0:
            raise ValueError("Virtual nodes count must be positive.")
        self.vnodes_per_node = vnodes_per_node
        # Sorted array of 32-bit integer token positions on the ring
        self._ring: List[int] = []
        # Mapping from token position -> physical node name
        self._token_to_node: Dict[int, str] = {}
        # Set of active physical node names
        self._physical_nodes: Set[str] = set()

    def _hash(self, key_str: str) -> int:
        """Computes deterministic 32-bit unsigned integer hash using MD5."""
        digest = hashlib.md5(key_str.encode("utf-8")).hexdigest()
        # Truncate to 32 bits (0 to 2^32 - 1)
        return int(digest[:8], 16)

    def add_node(self, node: str) -> None:
        """Adds a physical node and its virtual tokens to the ring."""
        if node in self._physical_nodes:
            return
        self._physical_nodes.add(node)

        for i in range(self.vnodes_per_node):
            vnode_token = self._hash(f"{node}#vnode_{i}")
            self._token_to_node[vnode_token] = node
            # Insert into sorted ring array
            idx = bisect_right(self._ring, vnode_token)
            self._ring.insert(idx, vnode_token)

    def remove_node(self, node: str) -> None:
        """Removes a physical node and purges all its virtual tokens."""
        if node not in self._physical_nodes:
            return
        self._physical_nodes.remove(node)

        for i in range(self.vnodes_per_node):
            vnode_token = self._hash(f"{node}#vnode_{i}")
            if vnode_token in self._token_to_node:
                del self._token_to_node[vnode_token]
                # Remove from sorted ring array
                idx = bisect_right(self._ring, vnode_token) - 1
                if 0 <= idx < len(self._ring) and self._ring[idx] == vnode_token:
                    self._ring.pop(idx)

    def get_node(self, key: str) -> Optional[str]:
        """
        Routes key to the nearest physical node clockwise along the ring.
        Complexity: O(log(N * V)) via binary search.
        """
        if not self._ring:
            return None

        key_hash = self._hash(key)
        # Binary search for the first token >= key_hash
        idx = bisect_right(self._ring, key_hash)

        # If key_hash is greater than all tokens on ring, wrap around to token 0
        if idx == len(self._ring):
            idx = 0

        target_token = self._ring[idx]
        return self._token_to_node[target_token]

    def get_replicas(self, key: str, count: int) -> List[str]:
        """
        Dynamo-style Replication: Returns the first `count` distinct physical
        nodes clockwise along the ring to ensure data durability.
        """
        if not self._ring or count <= 0:
            return []

        distinct_nodes: List[str] = []
        key_hash = self._hash(key)
        start_idx = bisect_right(self._ring, key_hash)

        n = len(self._ring)
        for i in range(n):
            curr_idx = (start_idx + i) % n
            curr_token = self._ring[curr_idx]
            physical = self._token_to_node[curr_token]

            if physical not in distinct_nodes:
                distinct_nodes.append(physical)
                if len(distinct_nodes) == count or len(distinct_nodes) == len(self._physical_nodes):
                    break

        return distinct_nodes


# =====================================================================
# 2. TOKEN BUCKET RATE LIMITER
# =====================================================================

class TokenBucketRateLimiter:
    """
    Production Token Bucket Rate Limiter with Lazy On-Demand Refill.
    
    WHY ON-DEMAND REFILL?
    Running background threads ticking every second to refill millions of buckets
    wastes CPU cycles and causes lock contention.
    Instead, tokens are dynamically added on arrival based on the elapsed time delta:
    tokens = min(capacity, current_tokens + (now - last_refill) * rate).
    """

    def __init__(self, capacity: int, refill_rate_per_sec: float):
        if capacity <= 0 or refill_rate_per_sec <= 0:
            raise ValueError("Capacity and refill rate must be positive.")
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        # client_id -> (current_tokens, last_refill_timestamp)
        self._buckets: Dict[str, List[float]] = {}

    def allow_request(self, client_id: str, tokens: int = 1) -> bool:
        """
        Evaluates whether client_id has sufficient tokens to execute request.
        Returns True if allowed (tokens deducted), False if throttled (HTTP 429).
        """
        now = time.time()

        if client_id not in self._buckets:
            # New client starts with a full bucket
            self._buckets[client_id] = [float(self.capacity), now]

        bucket = self._buckets[client_id]
        curr_tokens, last_refill = bucket[0], bucket[1]

        # Calculate lazy token refill
        elapsed = now - last_refill
        new_tokens = min(float(self.capacity), curr_tokens + elapsed * self.refill_rate)

        # Check if enough tokens available
        if new_tokens >= tokens:
            bucket[0] = new_tokens - tokens
            bucket[1] = now
            return True

        # Denied: Update tokens and timestamp without deduction
        bucket[0] = new_tokens
        bucket[1] = now
        return False


# =====================================================================
# 3. LEAKY BUCKET RATE LIMITER
# =====================================================================

class LeakyBucketRateLimiter:
    """
    Leaky Bucket Traffic Smoother.
    
    Enforces a smooth, constant outflow rate. Excess burst requests fill the bucket.
    If bucket capacity is exceeded, incoming requests overflow and are dropped.
    """

    def __init__(self, capacity: int, leak_rate_per_sec: float):
        self.capacity = capacity
        self.leak_rate = leak_rate_per_sec
        # client_id -> (current_water_level, last_leak_timestamp)
        self._buckets: Dict[str, List[float]] = {}

    def allow_request(self, client_id: str) -> bool:
        now = time.time()

        if client_id not in self._buckets:
            self._buckets[client_id] = [0.0, now]

        water_level, last_leak = self._buckets[client_id]

        # Drain water based on elapsed time
        elapsed = now - last_leak
        leaked_amount = elapsed * self.leak_rate
        water_level = max(0.0, water_level - leaked_amount)

        # Check if bucket can accept 1 unit of request
        if water_level + 1.0 <= self.capacity:
            self._buckets[client_id][0] = water_level + 1.0
            self._buckets[client_id][1] = now
            return True

        # Bucket overflow -> Throttled
        self._buckets[client_id][0] = water_level
        self._buckets[client_id][1] = now
        return False


# =====================================================================
# 4. UNIT TESTS & VERIFICATION
# =====================================================================

def run_unit_tests():
    print("=" * 70)
    print("RUNNING UNIT TESTS FOR HASH RING & RATE LIMITERS")
    print("=" * 70)

    # 1. Test Consistent Hash Ring Distribution
    ring = ConsistentHashRing(vnodes_per_node=100)
    nodes = ["node-us-east-1", "node-us-west-1", "node-eu-central-1"]
    for n in nodes:
        ring.add_node(n)

    # Verify key routing
    k1_node = ring.get_node("user_session_abc123")
    assert k1_node in nodes
    # Deterministic check: Same key always routes to same node
    assert ring.get_node("user_session_abc123") == k1_node
    print("[PASS] Consistent Hash Ring deterministic routing verified.")

    # Test Minimal Remapping on Node Addition
    # Generate 10,000 keys and record initial routing
    keys = [f"cache_key_{i}" for i in range(10_000)]
    initial_mapping = {k: ring.get_node(k) for k in keys}

    # Add a 4th node
    ring.add_node("node-ap-south-1")
    remapped_count = sum(1 for k in keys if ring.get_node(k) != initial_mapping[k])
    remapping_percentage = (remapped_count / len(keys)) * 100

    # With 4 nodes, theoretical optimal remapping is 1/4 = 25%.
    # Naive modulo hashing would remap 75%!
    assert 18.0 <= remapping_percentage <= 32.0, f"Remapping was {remapping_percentage:.1f}%"
    print(f"[PASS] Minimal Key Migration verified: {remapping_percentage:.1f}% keys remapped (Expected ~25%).")

    # Test Replication (Dynamo-Style)
    replicas = ring.get_replicas("payment_tx_999", count=3)
    assert len(replicas) == 3
    assert len(set(replicas)) == 3  # All distinct physical servers
    print("[PASS] Multi-node replica set routing verified.")

    # 2. Test Token Bucket Rate Limiter
    # Capacity 3, Refill 1 token/sec
    limiter = TokenBucketRateLimiter(capacity=3, refill_rate_per_sec=1.0)
    # Burst 3 requests immediately -> All should succeed
    assert limiter.allow_request("client_1") is True
    assert limiter.allow_request("client_1") is True
    assert limiter.allow_request("client_1") is True
    # 4th request must be rejected (bucket empty)
    assert limiter.allow_request("client_1") is False

    # Sleep 1.05s to refill ~1 token
    time.sleep(1.05)
    assert limiter.allow_request("client_1") is True  # 1 token refilled
    assert limiter.allow_request("client_1") is False # Empty again
    print("[PASS] Token Bucket burst capacity and lazy refill verified.")

    # 3. Test Leaky Bucket Rate Limiter
    leaky = LeakyBucketRateLimiter(capacity=2, leak_rate_per_sec=1.0)
    assert leaky.allow_request("c1") is True
    assert leaky.allow_request("c1") is True
    assert leaky.allow_request("c1") is False  # Overflow!
    print("[PASS] Leaky Bucket overflow throttling verified.")
    print("All unit tests passed successfully!\n")


# =====================================================================
# 5. HIGH-VOLUME PERFORMANCE BENCHMARK
# =====================================================================

def run_performance_benchmark():
    print("=" * 70)
    print("HIGH-VOLUME BENCHMARK: 100,000 OPERATIONS")
    print("=" * 70)

    import random
    random.seed(42)

    total_ops = 100_000

    # 1. Benchmark Consistent Hash Ring
    # 20 physical nodes, 150 vnodes each = 3,000 points on ring
    ring = ConsistentHashRing(vnodes_per_node=150)
    for i in range(20):
        ring.add_node(f"node_cluster_{i}")

    keys = [f"user_req_{random.randint(1, 50_000)}" for _ in range(total_ops)]

    start_time = time.perf_counter()
    for k in keys:
        ring.get_node(k)
    ring_ms = (time.perf_counter() - start_time) * 1000

    # 2. Benchmark Token Bucket Rate Limiter
    limiter = TokenBucketRateLimiter(capacity=100, refill_rate_per_sec=10.0)
    clients = [f"client_{random.randint(1, 500)}" for _ in range(total_ops)]

    start_time = time.perf_counter()
    for c in clients:
        limiter.allow_request(c)
    tb_ms = (time.perf_counter() - start_time) * 1000

    print(f"Total Operations Executed:    {total_ops:,}")
    print(f"Consistent Hash Ring Routing: {ring_ms:.2f} ms ({(total_ops / (ring_ms / 1000)):,.0f} lookups/sec)")
    print(f"Token Bucket Rate Limiting:   {tb_ms:.2f} ms ({(total_ops / (tb_ms / 1000)):,.0f} checks/sec)")
    print(f"Hash Ring Latency per Key:    {(ring_ms / total_ops) * 1000:.2f} microseconds")
    print(f"Rate Limiter Latency per Req: {(tb_ms / total_ops) * 1000:.2f} microseconds")
    print("=" * 70)


if __name__ == "__main__":
    run_unit_tests()
    run_performance_benchmark()
