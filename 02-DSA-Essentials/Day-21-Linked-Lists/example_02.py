"""
Day 21: Example 02 - Production Architecture: High-Throughput LRU Semantic Cache with Doubly Linked List

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-throughput microservices and AI inference gateways (e.g., LLM semantic caching,
vLLM KV-cache block managers, Redis buffer pools), caching frequent queries is essential
to prevent expensive re-computation and downstream database exhaustion.

A standard array-based cache fails at scale: promoting an accessed item to the "most recent"
position requires an O(N) array shift. A pure hash map supports O(1) lookups but cannot
track recency order in O(1) time.

The industry-standard solution combines a Hash Map with a Doubly Linked List:
1. Hash Map provides O(1) key-to-node pointer resolution.
2. Doubly Linked List provides O(1) unlinking and re-insertion at the head,
   plus O(1) eviction of the least recently used node from the tail.

This module implements:
1. `DoublyLinkedNode`: Compact bidirectional node with `__slots__`.
2. `ProductionLRUCache`: Enterprise LRU cache featuring sentinel boundaries, TTL expirations,
   hit/miss telemetry, and O(1) eviction guarantees.
3. Realistic AI embedding / API query cache simulation and benchmark.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Generic, List, Optional, Tuple, TypeVar

K = TypeVar("K")
V = TypeVar("V")


# ============================================================================
# 1. COMPACT DOUBLY LINKED NODE
# ============================================================================

class DoublyLinkedNode(Generic[K, V]):
    """
    Bidirectional node optimized with __slots__ for minimal memory footprint.
    
    WHY DOUBLY LINKED:
    To remove a node from the middle of a list in O(1) time, we MUST have direct access
    to its predecessor (`prev`) so we can link `prev.next = next`. In a singly linked list,
    finding the predecessor requires an O(N) linear scan from the head!
    """
    __slots__ = ("key", "val", "expires_at", "prev", "next")

    def __init__(
        self,
        key: K,
        val: V,
        expires_at: Optional[float] = None,
        prev: Optional[DoublyLinkedNode[K, V]] = None,
        next: Optional[DoublyLinkedNode[K, V]] = None,
    ) -> None:
        self.key = key
        self.val = val
        self.expires_at = expires_at
        self.prev = prev
        self.next = next


# ============================================================================
# 2. PRODUCTION LRU CACHE WITH TTL & TELEMETRY
# ============================================================================

class ProductionLRUCache(Generic[K, V]):
    """
    High-performance LRU Cache implementing the Hash Map + Doubly Linked List pattern.
    
    INVARIANTS:
    - `head.next` is always the MOST RECENTLY USED (MRU) node.
    - `tail.prev` is always the LEAST RECENTLY USED (LRU) node.
    - Both `head` and `tail` are permanent sentinel dummy nodes to eliminate null checks.
    """

    def __init__(self, capacity: int, default_ttl_seconds: Optional[float] = None) -> None:
        if capacity <= 0:
            raise ValueError("LRU Cache capacity must be greater than zero.")
        
        self.capacity = capacity
        self.default_ttl = default_ttl_seconds
        
        # Hash map: Key -> DoublyLinkedNode pointer (O(1) direct address resolution)
        self._map: Dict[K, DoublyLinkedNode[K, V]] = {}

        # Sentinel dummy nodes eliminate edge cases during insert/remove
        self._head: DoublyLinkedNode[K, V] = DoublyLinkedNode(None, None)  # type: ignore
        self._tail: DoublyLinkedNode[K, V] = DoublyLinkedNode(None, None)  # type: ignore
        self._head.next = self._tail
        self._tail.prev = self._head

        # Telemetry metrics
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    # ------------------------------------------------------------------------
    # Internal Pointer Manipulation Primitives (Strict O(1))
    # ------------------------------------------------------------------------

    def _remove_node(self, node: DoublyLinkedNode[K, V]) -> None:
        """Unlinks a node from its current position in O(1) time."""
        prev_node = node.prev
        next_node = node.next
        if prev_node:
            prev_node.next = next_node
        if next_node:
            next_node.prev = prev_node

    def _add_to_head(self, node: DoublyLinkedNode[K, V]) -> None:
        """Splices a node immediately after the sentinel head (mark as MRU) in O(1) time."""
        node.prev = self._head
        node.next = self._head.next
        
        # Connect existing first node back to new node
        if self._head.next:
            self._head.next.prev = node
        self._head.next = node

    def _move_to_head(self, node: DoublyLinkedNode[K, V]) -> None:
        """Promotes an existing node to the MRU position."""
        self._remove_node(node)
        self._add_to_head(node)

    def _pop_tail(self) -> DoublyLinkedNode[K, V]:
        """Evicts the LRU node right before sentinel tail in O(1) time."""
        lru_node = self._tail.prev
        assert lru_node is not None and lru_node is not self._head, "Attempted to pop from empty cache"
        self._remove_node(lru_node)
        return lru_node

    # ------------------------------------------------------------------------
    # Public API (Strict O(1) Time Complexity)
    # ------------------------------------------------------------------------

    def get(self, key: K) -> Optional[V]:
        """
        Retrieves a cached item in O(1) time. Promotes the item to MRU on hit.
        Returns None if key is absent or expired.
        """
        if key not in self._map:
            self._misses += 1
            return None

        node = self._map[key]

        # Check TTL expiration
        now = time.time()
        if node.expires_at is not None and now > node.expires_at:
            self._remove_node(node)
            del self._map[key]
            self._expirations += 1
            self._misses += 1
            return None

        # Cache Hit: Promote node to MRU head
        self._move_to_head(node)
        self._hits += 1
        return node.val

    def put(self, key: K, val: V, ttl_seconds: Optional[float] = None) -> None:
        """
        Inserts or updates a cached item in O(1) time.
        If cache exceeds capacity, evicts the least recently used entry.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        expires_at = time.time() + ttl if ttl is not None else None

        if key in self._map:
            # Key exists: Update value, update TTL, and promote to MRU
            node = self._map[key]
            node.val = val
            node.expires_at = expires_at
            self._move_to_head(node)
        else:
            # New Key: Create node, link to MRU head, and register in hash map
            new_node = DoublyLinkedNode(key=key, val=val, expires_at=expires_at)
            self._map[key] = new_node
            self._add_to_head(new_node)

            # Evict LRU tail if capacity is exceeded
            if len(self._map) > self.capacity:
                lru_node = self._pop_tail()
                del self._map[lru_node.key]
                self._evictions += 1

    def delete(self, key: K) -> bool:
        """Removes a key explicitly from cache in O(1) time."""
        if key not in self._map:
            return False
        node = self._map[key]
        self._remove_node(node)
        del self._map[key]
        return True

    def clear(self) -> None:
        """Resets the cache in O(1) pointer updates."""
        self._map.clear()
        self._head.next = self._tail
        self._tail.prev = self._head

    @property
    def size(self) -> int:
        return len(self._map)

    @property
    def hit_ratio(self) -> float:
        total = self._hits + self._misses
        return (self._hits / total) if total > 0 else 0.0

    @property
    def telemetry(self) -> Dict[str, Any]:
        return {
            "size": len(self._map),
            "capacity": self.capacity,
            "hits": self._hits,
            "misses": self._misses,
            "hit_ratio": f"{self.hit_ratio * 100:.2f}%",
            "evictions": self._evictions,
            "expirations": self._expirations,
        }


# ============================================================================
# 3. VERIFICATION SUITE & PRODUCTION SIMULATION
# ============================================================================

def run_tests() -> None:
    """Verifies LRU eviction invariants and TTL expirations."""
    print("======================================================================")
    print("RUNNING LRU CACHE VERIFICATION SUITE")
    print("======================================================================")

    # 1. Capacity and Eviction Invariant
    cache: ProductionLRUCache[str, int] = ProductionLRUCache(capacity=2)
    cache.put("k1", 100)
    cache.put("k2", 200)
    assert cache.get("k1") == 100, "k1 should be present"  # k1 becomes MRU, k2 is LRU
    
    # Adding k3 should evict k2 (LRU)
    cache.put("k3", 300)
    assert cache.get("k2") is None, "k2 should have been evicted!"
    assert cache.get("k3") == 300, "k3 should be present"
    assert cache.get("k1") == 100, "k1 should still be present"
    print("[PASS] LRU capacity eviction verified.")

    # 2. Update existing key does not increase size
    cache.put("k1", 150)
    assert cache.size == 2
    assert cache.get("k1") == 150
    print("[PASS] Value update & promotion verified.")

    # 3. Explicit deletion
    assert cache.delete("k3") is True
    assert cache.get("k3") is None
    assert cache.size == 1
    print("[PASS] Explicit deletion verified.")

    # 4. TTL Expiration
    ttl_cache: ProductionLRUCache[str, str] = ProductionLRUCache(capacity=5, default_ttl_seconds=0.05)
    ttl_cache.put("temp_token", "jwt_secret_xyz")
    assert ttl_cache.get("temp_token") == "jwt_secret_xyz", "Should hit before TTL expires"
    
    time.sleep(0.06)  # Sleep past TTL
    assert ttl_cache.get("temp_token") is None, "Should expire after TTL"
    assert ttl_cache.telemetry["expirations"] == 1
    print("[PASS] TTL time-to-live expiration verified.")

    print("All unit tests passed successfully!\n")


def run_production_simulation() -> None:
    """Simulates high-throughput LLM Semantic Cache / API gateway caching."""
    print("======================================================================")
    print("SIMULATING HIGH-THROUGHPUT AI SEMANTIC CACHE (100,000 OPERATIONS)")
    print("======================================================================")

    cache_capacity = 1_000
    cache: ProductionLRUCache[str, str] = ProductionLRUCache(capacity=cache_capacity)

    # 80/20 Zipfian-like distribution: 200 hot prompts account for 80% of traffic
    hot_prompts = [f"prompt_summary_user_{i}" for i in range(200)]
    cold_prompts = [f"prompt_unique_query_{i}" for i in range(5_000)]

    total_ops = 100_000
    start_time = time.perf_counter()

    for i in range(total_ops):
        # 80% chance to request a hot prompt
        if i % 10 < 8:
            key = hot_prompts[i % len(hot_prompts)]
        else:
            key = cold_prompts[i % len(cold_prompts)]

        # Check cache
        val = cache.get(key)
        if val is None:
            # Simulate embedding / inference lookup and store result
            cache.put(key, f"simulated_llm_response_token_{i}")

    elapsed = time.perf_counter() - start_time
    throughput = total_ops / elapsed if elapsed > 0 else 0.0

    print(f"Total In-Memory Operations: {total_ops:,}")
    print(f"Elapsed Processing Time:    {elapsed * 1000:8.2f} ms")
    print(f"Cache Throughput:           {throughput:10,.0f} ops/sec")
    print(f"Average Latency:            {(elapsed / total_ops) * 1_000_000:8.4f} microseconds/op")

    print("\nProduction Cache Telemetry:")
    for k, v in cache.telemetry.items():
        print(f"  {k:15s}: {v}")

    # Assert high hit ratio on Zipfian distribution (>75%)
    assert cache.hit_ratio > 0.75, f"Expected >75% hit ratio, got {cache.hit_ratio:.2%}"
    print("\n[PASS] Production AI Cache benchmark completed successfully!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_production_simulation()
