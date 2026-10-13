"""
Day 18: Example 02 - Production Backend Architecture: Multi-Tenant Session Store & Relational Hash-Join Engine

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-throughput microservices and API gateways (e.g. FastAPI / Django / Node),
relational databases should never be queried on every single authenticated HTTP request
to validate bearer tokens or perform lightweight cross-service data denormalization.

By maintaining in-memory Hash Table architectures:
1. Dual-Indexed Session Stores provide O(1) token authentication AND O(1) instant
   cascade revocation across all active devices for a compromised user.
2. Relational Hash Joins allow microservices to denormalize and pair records across
   heterogeneous streams in linear O(M + N) time, replicating database query planner
   algorithms without executing slow SQL JOIN queries across remote networks.

This module implements:
1. `MultiTenantSessionStore`: Dual-indexed thread-safe session manager with TTL expiration,
   O(1) token validation, and instant user-wide session invalidation.
2. `InMemRelationalHashJoin`: Production implementation of database Build & Probe
   Hash Join algorithm pairing Orders and Customer profiles in O(M + N) time.
3. Automated verification suite and execution benchmarks.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Set, Tuple


# ============================================================================
# 1. DUAL-INDEXED MULTI-TENANT SESSION STORE
# ============================================================================

@dataclass(frozen=True, slots=True)
class UserSession:
    """Represents an active authenticated user session."""
    session_token: str
    user_id: str
    tenant_id: str
    role: str
    created_at: float
    expires_at: float

    def is_expired(self, current_time: float) -> bool:
        return current_time >= self.expires_at


class MultiTenantSessionStore:
    """
    High-performance in-memory session manager with dual-index cascade invalidation.
    
    WHY DUAL INDEXING:
    - Primary Index (Token -> Session): Resolves incoming Authorization header in O(1).
    - Reverse Index (UserID -> Set[Token]): When a user changes their password or an
      account is compromised, revoking all active sessions on mobile/web/tablets
      takes O(K) where K is active devices (typically <= 5), avoiding an O(N) table scan
      across millions of active sessions.
    """

    def __init__(self) -> None:
        # Primary Index: session_token -> UserSession
        self._sessions_by_token: Dict[str, UserSession] = {}
        # Reverse Index: user_id -> Set of active session_tokens
        self._tokens_by_user: Dict[str, Set[str]] = {}
        self._counter: int = 0
        self._lock = threading.Lock()

    def create_session(
        self,
        user_id: str,
        tenant_id: str,
        role: str,
        ttl_seconds: float = 3600.0,
        current_time: Optional[float] = None,
    ) -> str:
        """Generates and registers a new authenticated session in both indexes."""
        now = current_time if current_time is not None else time.time()

        with self._lock:
            self._counter += 1
            token = f"tok_{tenant_id}_{user_id}_{int(now * 1000)}_{self._counter}"

            session = UserSession(
                session_token=token,
                user_id=user_id,
                tenant_id=tenant_id,
                role=role,
                created_at=now,
                expires_at=now + ttl_seconds,
            )

            # 1. Store in primary index
            self._sessions_by_token[token] = session

            # 2. Add to user reverse index
            if user_id not in self._tokens_by_user:
                self._tokens_by_user[user_id] = set()
            self._tokens_by_user[user_id].add(token)

        return token

    def validate_token(self, token: str, current_time: Optional[float] = None) -> Optional[UserSession]:
        """Validates a bearer token in O(1) time, verifying TTL expiration."""
        now = current_time if current_time is not None else time.time()

        with self._lock:
            session = self._sessions_by_token.get(token)
            if not session:
                return None

            # Lazy expiration eviction
            if session.is_expired(now):
                self._remove_session_internal(session)
                return None

            return session

    def revoke_token(self, token: str) -> bool:
        """Revokes an individual session token in O(1) time."""
        with self._lock:
            session = self._sessions_by_token.get(token)
            if not session:
                return False
            self._remove_session_internal(session)
            return True

    def revoke_all_user_sessions(self, user_id: str) -> int:
        """
        Instantly revokes ALL active sessions for a user across all devices in O(K) time.
        """
        with self._lock:
            tokens_to_purge = self._tokens_by_user.get(user_id, set()).copy()
            for token in tokens_to_purge:
                session = self._sessions_by_token.get(token)
                if session:
                    self._remove_session_internal(session)
            return len(tokens_to_purge)

    def _remove_session_internal(self, session: UserSession) -> None:
        """Internal helper to clean up both primary and reverse index entries."""
        # Evict from primary index
        self._sessions_by_token.pop(session.session_token, None)

        # Evict from reverse index
        user_tokens = self._tokens_by_user.get(session.user_id)
        if user_tokens:
            user_tokens.discard(session.session_token)
            if not user_tokens:
                del self._tokens_by_user[session.user_id]

    def get_active_session_counts(self) -> Tuple[int, int]:
        """Returns (total_active_sessions, total_active_users)."""
        with self._lock:
            return len(self._sessions_by_token), len(self._tokens_by_user)


# ============================================================================
# 2. RELATIONAL IN-MEMORY HASH JOIN ENGINE
# ============================================================================

@dataclass(frozen=True, slots=True)
class CustomerRecord:
    customer_id: str
    full_name: str
    subscription_tier: str


@dataclass(frozen=True, slots=True)
class OrderRecord:
    order_id: str
    customer_id: str
    amount_usd: float
    status: str


@dataclass(frozen=True, slots=True)
class EnrichedOrder:
    order_id: str
    customer_name: str
    customer_tier: str
    amount_usd: float
    status: str


class InMemRelationalHashJoin:
    """
    Implements the classical database Hash Join algorithm in linear O(M + N) time.
    
    WHY HASH JOIN OVER NESTED LOOPS:
    A nested loop join compares every order with every customer, taking O(M * N) time.
    For 50,000 orders and 10,000 customers, nested loops perform 500,000,000 iterations!
    
    Hash Join operates in two distinct phases:
    1. BUILD PHASE: Build a hash table from the smaller relation (Customers) on the
       join key 'customer_id' in O(M) time.
    2. PROBE PHASE: Stream through the larger relation (Orders), probing the hash
       table in O(1) average time per row, totaling O(N) time.
    Overall complexity: O(M + N) linear execution with zero cartesian product waste.
    """

    @classmethod
    def inner_join(
        cls,
        customers: List[CustomerRecord],
        orders: List[OrderRecord],
    ) -> Generator[EnrichedOrder, None, None]:
        # PHASE 1: BUILD PHASE (Hash table on smaller table)
        customer_lookup: Dict[str, CustomerRecord] = {
            cust.customer_id: cust for cust in customers
        }

        # PHASE 2: PROBE PHASE (Stream larger table and probe hash map in O(1))
        for order in orders:
            customer = customer_lookup.get(order.customer_id)
            if customer is not None:
                yield EnrichedOrder(
                    order_id=order.order_id,
                    customer_name=customer.full_name,
                    customer_tier=customer.subscription_tier,
                    amount_usd=order.amount_usd,
                    status=order.status,
                )


# ============================================================================
# 3. DEMONSTRATION & VERIFICATION SUITE
# ============================================================================

def run_production_hashmap_demo() -> None:
    print("=" * 75)
    print("DAY 18: PRODUCTION MULTI-TENANT SESSION STORE & HASH JOIN ENGINE")
    print("=" * 75)

    # 1. Test MultiTenantSessionStore
    print("\n[PART 1] Testing Dual-Indexed Multi-Tenant Session Store:")
    store = MultiTenantSessionStore()
    base_t = 1000.0

    # User Alice logs in from Web, Mobile, and iPad (3 concurrent sessions)
    tok_web = store.create_session("usr_alice", "org_acme", "admin", ttl_seconds=60.0, current_time=base_t)
    tok_mobile = store.create_session("usr_alice", "org_acme", "admin", ttl_seconds=60.0, current_time=base_t)
    tok_ipad = store.create_session("usr_alice", "org_acme", "admin", ttl_seconds=60.0, current_time=base_t)

    # User Bob logs in from Web (1 session)
    tok_bob = store.create_session("usr_bob", "org_acme", "viewer", ttl_seconds=60.0, current_time=base_t)

    total_sessions, total_users = store.get_active_session_counts()
    print(f"  - Sessions Created: {total_sessions} active sessions across {total_users} users.")
    assert total_sessions == 4
    assert total_users == 2

    # Validate Alice's token
    session = store.validate_token(tok_web, current_time=base_t + 10.0)
    assert session is not None and session.user_id == "usr_alice"
    print(f"  - Validated Token '{tok_web[:22]}...': User={session.user_id}, Role={session.role} [PASSED]")

    # Single token revocation (Logout from Web only)
    store.revoke_token(tok_web)
    assert store.validate_token(tok_web, current_time=base_t + 15.0) is None
    assert store.validate_token(tok_mobile, current_time=base_t + 15.0) is not None
    print("  - Single Token Revocation Verified: Web session terminated; Mobile session persists [PASSED]")

    # Security Event: Alice resets password -> Invalidate ALL devices instantly
    revoked_count = store.revoke_all_user_sessions("usr_alice")
    print(f"  - Password Reset Triggered: Cascade invalidated {revoked_count} remaining sessions for Alice.")
    assert revoked_count == 2
    assert store.validate_token(tok_mobile, current_time=base_t + 20.0) is None
    assert store.validate_token(tok_ipad, current_time=base_t + 20.0) is None

    # Bob's session must remain completely intact
    assert store.validate_token(tok_bob, current_time=base_t + 20.0) is not None
    print("  - Status: DUAL-INDEX SESSION ISOLATION & CASCADE PURGE VERIFIED [PASSED]")

    # 2. Test In-Memory Relational Hash Join
    print("\n[PART 2] Testing In-Memory Relational Hash Join (Build & Probe):")
    mock_customers = [
        CustomerRecord("c_001", "Acme Corp", "ENTERPRISE"),
        CustomerRecord("c_002", "Nexus AI", "PRO"),
        CustomerRecord("c_003", "Solo Labs", "STARTER"),
    ]

    mock_orders = [
        OrderRecord("ord_101", "c_001", 1250.00, "COMPLETED"),
        OrderRecord("ord_102", "c_002", 450.00, "PROCESSING"),
        OrderRecord("ord_103", "c_001", 3400.00, "COMPLETED"),
        OrderRecord("ord_104", "c_999", 99.00, "ORPHANED_CUSTOMER"), # Should be dropped by INNER JOIN
    ]

    joined_stream = list(InMemRelationalHashJoin.inner_join(mock_customers, mock_orders))

    print(f"  - Customers (Build Relation) : {len(mock_customers)} rows")
    print(f"  - Orders (Probe Relation)    : {len(mock_orders)} rows")
    print(f"  - Joined Output              : {len(joined_stream)} enriched records\n")

    for rec in joined_stream:
        print(f"  - Order {rec.order_id}: Customer='{rec.customer_name}' ({rec.customer_tier}) | ${rec.amount_usd:.2f} [{rec.status}]")

    assert len(joined_stream) == 3
    assert joined_stream[0].customer_name == "Acme Corp"
    assert joined_stream[1].customer_name == "Nexus AI"
    assert joined_stream[2].customer_name == "Acme Corp"
    print("  - Status: RELATIONAL HASH JOIN PROBE INTEGRITY VERIFIED [PASSED]")

    # 3. High-Volume Hash Join Benchmark vs Nested Loops Join
    print("\n[PART 3] Performance Benchmark: Hash Join O(M + N) vs Nested Loops O(M * N):")
    cust_count = 2000
    ord_count = 10000

    bench_customers = [CustomerRecord(f"c_{i}", f"Cust_{i}", "TIER") for i in range(cust_count)]
    bench_orders = [OrderRecord(f"o_{j}", f"c_{j % cust_count}", float(j), "PAID") for j in range(ord_count)]

    # Benchmark Hash Join O(M + N)
    start_time = time.perf_counter()
    hash_joined = list(InMemRelationalHashJoin.inner_join(bench_customers, bench_orders))
    hash_ms = (time.perf_counter() - start_time) * 1000.0

    print(f"  Joining {ord_count:,} Orders with {cust_count:,} Customers:")
    print(f"  - Hash Join O(M + N) Duration  : {hash_ms:.2f} ms")
    print(f"  - Nested Loops would require   : {cust_count * ord_count:,} iterations!")
    print(f"  - Enriched Record Throughput   : ~{(len(hash_joined) / (hash_ms / 1000.0)):,.0f} records/sec")

    assert len(hash_joined) == ord_count

    print("\n" + "=" * 75)
    print("PRODUCTION HASH TABLE ARCHITECTURES VERIFIED & VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_production_hashmap_demo()
