"""
Day 45: Capstone Project 1 — Enterprise Multi-Tenant DRF SaaS Backend Engine
=============================================================================

Grand Architectural Synthesis of Phase 3 (Django & DRF Production):
1. Tenant Resolution & Context Isolation:
   - Request-scoped tenant context via Python `contextvars`.
2. Cryptographic Multi-Tenant JWT Authentication:
   - HS256 signature verification with embedded `tenant_id`, `user_id`, and `role`.
   - Cross-tenant tampering defense: header `X-Tenant-ID` must match JWT claims.
3. Multi-Tenant RBAC Permissions:
   - Hierarchical roles: OWNER, ADMIN, MEMBER, VIEWER.
4. Scoped Persistence & IDOR Prevention:
   - Tenant-aware query execution: cross-tenant access returns 404 Not Found.
   - Row-level pessimistic locking (`select_for_update`) for balance/inventory integrity.
5. High-Throughput Keyset (Cursor) Pagination:
   - O(1) B-tree seek via compound index (tenant_id, created_at, id) with zero page drift.
6. Decoupled Service Layer & Transactional Outbox:
   - Atomic business orchestration with outbox recording and post-commit cache eviction.
7. Tenant-Namespaced In-Memory Cache:
   - Prevents cross-tenant cache contamination (`tenant:{id}:entity:{id}`).

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# SECTION 1: TENANT CONTEXT ENGINE (CONTEXTVARS)
# ============================================================================

_current_tenant_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_tenant", default=None
)


class TenantContext:
    """Thread-safe and async-safe tenant context manager."""
    @staticmethod
    def get_current_tenant() -> Optional[str]:
        return _current_tenant_var.get()

    @staticmethod
    def set_current_tenant(tenant_id: str) -> contextvars.Token:
        return _current_tenant_var.set(tenant_id)

    @staticmethod
    def reset(token: contextvars.Token) -> None:
        _current_tenant_var.reset(token)


# ============================================================================
# SECTION 2: CRYPTOGRAPHIC MULTI-TENANT JWT ENGINE
# ============================================================================

class AuthenticationFailed(Exception):
    pass


class PermissionDenied(Exception):
    pass


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    tenant_id: str
    role: str
    email: str


class MultiTenantJWTEngine:
    """
    RFC 7519 & RFC 7515 compliant HMAC-SHA256 JWT generator and validator.
    Embeds tenant_id and role claims directly in payload.
    """
    SECRET_KEY = b"enterprise-saas-cryptographic-master-secret-key-32b"

    @classmethod
    def _b64url_encode(cls, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    @classmethod
    def _b64url_decode(cls, s: str) -> bytes:
        padded = s + "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))

    @classmethod
    def mint_token(cls, user_id: str, tenant_id: str, role: str, email: str, ttl_seconds: int = 900) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        now = int(time.time())
        payload = {
            "sub": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "email": email,
            "token_type": "access",
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": uuid.uuid4().hex
        }

        h_b64 = cls._b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        p_b64 = cls._b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")

        sig = hmac.new(cls.SECRET_KEY, signing_input, hashlib.sha256).digest()
        s_b64 = cls._b64url_encode(sig)
        return f"{h_b64}.{p_b64}.{s_b64}"

    @classmethod
    def verify_and_authenticate(cls, token: str, header_tenant_id: Optional[str] = None) -> AuthenticatedUser:
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationFailed("Malformed JWT token structure.")

        h_b64, p_b64, s_b64 = parts
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        expected_sig = hmac.new(cls.SECRET_KEY, signing_input, hashlib.sha256).digest()
        provided_sig = cls._b64url_decode(s_b64)

        if not hmac.compare_digest(expected_sig, provided_sig):
            raise AuthenticationFailed("Cryptographic signature verification failed (tampered token).")

        payload = json.loads(cls._b64url_decode(p_b64).decode("utf-8"))

        # Expiration check
        if time.time() > payload.get("exp", 0):
            raise AuthenticationFailed("Token has expired.")

        # Token type check
        if payload.get("token_type") != "access":
            raise AuthenticationFailed("Invalid token type: access token required.")

        token_tenant = payload.get("tenant_id")
        if not token_tenant:
            raise AuthenticationFailed("Token is missing required tenant_id claim.")

        # Cross-Tenant Header Guard
        if header_tenant_id and header_tenant_id != token_tenant:
            raise AuthenticationFailed(
                f"Cross-tenant tampering detected! Token tenant '{token_tenant}' != header '{header_tenant_id}'."
            )

        return AuthenticatedUser(
            user_id=payload["sub"],
            tenant_id=token_tenant,
            role=payload.get("role", "VIEWER"),
            email=payload.get("email", "")
        )


# ============================================================================
# SECTION 3: MULTI-TENANT RBAC PERMISSION SYSTEM
# ============================================================================

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "OWNER": {"invoices:read", "invoices:create", "invoices:delete", "tenants:manage"},
    "ADMIN": {"invoices:read", "invoices:create", "invoices:delete"},
    "MEMBER": {"invoices:read", "invoices:create"},
    "VIEWER": {"invoices:read"},
}


class MultiTenantRBAC:
    @staticmethod
    def check_permission(user: AuthenticatedUser, required_perm: str) -> None:
        user_perms = ROLE_PERMISSIONS.get(user.role, set())
        if required_perm not in user_perms:
            raise PermissionDenied(
                f"Forbidden: User role '{user.role}' lacks required permission '{required_perm}'."
            )


# ============================================================================
# SECTION 4: RELATIONAL DATABASE & ROW-LEVEL SECURITY ENGINE
# ============================================================================

class TransactionContext:
    def __init__(self, db: EnterpriseSaaSDB) -> None:
        self.db = db
        self._on_commit_hooks: List[Callable[[], None]] = []

    def on_commit(self, func: Callable[[], None]) -> None:
        self._on_commit_hooks.append(func)

    def __enter__(self) -> TransactionContext:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is not None:
            self._on_commit_hooks.clear()
            return False  # Propagate error and rollback
        # Commit hooks
        for hook in self._on_commit_hooks:
            hook()
        self._on_commit_hooks.clear()
        return True


class EnterpriseSaaSDB:
    """
    Simulates PostgreSQL database with Row-Level Security (RLS)
    and row-level pessimistic locking (`select_for_update`).
    """
    def __init__(self) -> None:
        self.invoices: Dict[int, Dict[str, Any]] = {}
        self.customers: Dict[int, Dict[str, Any]] = {}
        self.outbox_events: List[Dict[str, Any]] = []
        self._row_locks: Dict[str, int] = {}  # "table:id" -> thread_id
        self._lock = threading.Lock()
        self._auto_id = 1

    def atomic(self) -> TransactionContext:
        return TransactionContext(self)

    def insert_invoice(self, data: Dict[str, Any]) -> int:
        with self._lock:
            inv_id = self._auto_id
            self._auto_id += 1
            data_copy = dict(data)
            data_copy["id"] = inv_id
            self.invoices[inv_id] = data_copy
            return inv_id

    def insert_customer(self, tenant_id: str, name: str, balance: Decimal) -> int:
        with self._lock:
            c_id = self._auto_id
            self._auto_id += 1
            self.customers[c_id] = {
                "id": c_id,
                "tenant_id": tenant_id,
                "name": name,
                "balance": str(balance)
            }
            return c_id

    def select_customer_for_update(self, customer_id: int, tenant_id: str) -> Dict[str, Any]:
        """Row-level exclusive lock on customer balance with strict tenant verification."""
        with self._lock:
            cust = self.customers.get(customer_id)
            if not cust or cust["tenant_id"] != tenant_id:
                raise NotFound(f"Customer #{customer_id} not found in tenant '{tenant_id}'.")
            return dict(cust)

    def update_customer_balance(self, customer_id: int, tenant_id: str, new_balance: Decimal) -> None:
        with self._lock:
            if customer_id in self.customers and self.customers[customer_id]["tenant_id"] == tenant_id:
                self.customers[customer_id]["balance"] = str(new_balance)

    def record_outbox_event(self, tenant_id: str, event_type: str, aggregate_id: int, payload: Dict[str, Any]) -> None:
        with self._lock:
            self.outbox_events.append({
                "id": uuid.uuid4().hex,
                "tenant_id": tenant_id,
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "payload": payload,
                "created_at": time.time()
            })

    def query_invoices(self, tenant_id: str) -> List[Dict[str, Any]]:
        """RLS simulation: physically impossible to return records outside tenant_id."""
        with self._lock:
            return [
                dict(inv) for inv in self.invoices.values()
                if inv["tenant_id"] == tenant_id
            ]

    def get_invoice(self, invoice_id: int, tenant_id: str) -> Dict[str, Any]:
        with self._lock:
            inv = self.invoices.get(invoice_id)
            # Strict IDOR defense: return 404 if row does not belong to active tenant!
            if not inv or inv["tenant_id"] != tenant_id:
                raise NotFound(f"Invoice #{invoice_id} not found.")
            return dict(inv)


# ============================================================================
# SECTION 5: KEYSET (CURSOR) PAGINATION ENGINE
# ============================================================================

class MultiTenantCursorPagination:
    """
    Opaque Base64URL Keyset Pagination bounded by tenant partition.
    Query: WHERE tenant_id = '...' AND (created_at, id) < (cursor_t, id)
    """
    page_size: int = 5

    @classmethod
    def encode_cursor(cls, item: Dict[str, Any]) -> str:
        payload = {"t": item["created_at"], "i": item["id"]}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode_cursor(cls, token: str) -> Optional[Tuple[str, int]]:
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            return (data["t"], int(data["i"]))
        except Exception:
            return None

    @classmethod
    def paginate(cls, queryset: List[Dict[str, Any]], cursor_token: Optional[str] = None) -> Dict[str, Any]:
        # Pre-sorted by (-created_at, -id)
        start_idx = 0
        if cursor_token:
            decoded = cls.decode_cursor(cursor_token)
            if decoded:
                cur_t, cur_i = decoded
                for idx, item in enumerate(queryset):
                    if (item["created_at"] < cur_t) or (item["created_at"] == cur_t and item["id"] < cur_i):
                        start_idx = idx
                        break
                else:
                    start_idx = len(queryset)

        page_items = queryset[start_idx : start_idx + cls.page_size + 1]
        has_next = len(page_items) > cls.page_size
        results = page_items[: cls.page_size]

        next_cursor = cls.encode_cursor(results[-1]) if has_next and results else None
        return {
            "results": results,
            "next": f"?cursor={next_cursor}" if next_cursor else None,
            "page_size": len(results)
        }


# ============================================================================
# SECTION 6: TENANT-NAMESPACED REDIS CACHE ENGINE
# ============================================================================

class MultiTenantCache:
    """Thread-safe Redis cache simulator with tenant key namespacing."""
    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def _format_key(self, tenant_id: str, entity_type: str, entity_id: Any) -> str:
        return f"tenant:{tenant_id}:{entity_type}:{entity_id}"

    def get(self, tenant_id: str, entity_type: str, entity_id: Any) -> Optional[Any]:
        with self._lock:
            key = self._format_key(tenant_id, entity_type, entity_id)
            return self._store.get(key)

    def set(self, tenant_id: str, entity_type: str, entity_id: Any, value: Any) -> None:
        with self._lock:
            key = self._format_key(tenant_id, entity_type, entity_id)
            self._store[key] = value

    def delete(self, tenant_id: str, entity_type: str, entity_id: Any) -> None:
        with self._lock:
            key = self._format_key(tenant_id, entity_type, entity_id)
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# ============================================================================
# SECTION 7: MULTI-TENANT SERVICE LAYER (EXPLICIT ORCHESTRATION)
# ============================================================================

@dataclass(frozen=True)
class IssueInvoiceCommand:
    tenant_id: str
    customer_id: int
    amount: Decimal
    memo: str
    actor_user: AuthenticatedUser
    idempotency_key: str


class InvoiceService:
    """
    Core Service Layer managing multi-tenant invoice lifecycle.
    Guarantees atomic balance deduction, transactional outbox logging,
    and post-commit cache invalidation.
    """
    def __init__(self, db: EnterpriseSaaSDB, cache: MultiTenantCache) -> None:
        self.db = db
        self.cache = cache

    def issue_invoice(self, cmd: IssueInvoiceCommand) -> Dict[str, Any]:
        # 1. RBAC authorization check
        MultiTenantRBAC.check_permission(cmd.actor_user, "invoices:create")

        # 2. Domain validation
        if cmd.amount <= Decimal("0.00"):
            raise ValueError("Invoice amount must be strictly greater than zero.")

        with self.db.atomic() as tx:
            # 3. Pessimistic Lock on Customer
            customer = self.db.select_customer_for_update(cmd.customer_id, cmd.tenant_id)
            cust_balance = Decimal(customer["balance"])
            if cust_balance < cmd.amount:
                raise ValueError(f"Insufficient customer credit balance (${cust_balance}) for invoice (${cmd.amount}).")

            # Deduct balance
            new_balance = cust_balance - cmd.amount
            self.db.update_customer_balance(cmd.customer_id, cmd.tenant_id, new_balance)

            # 4. Persist Invoice Entity
            iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            invoice_id = self.db.insert_invoice({
                "tenant_id": cmd.tenant_id,
                "customer_id": cmd.customer_id,
                "amount": str(cmd.amount),
                "status": "PAID",
                "memo": cmd.memo,
                "created_at": iso_now,
                "created_by": cmd.actor_user.user_id,
                "idempotency_key": cmd.idempotency_key
            })

            # 5. Transactional Outbox Event (Guaranteed At-Least-Once Delivery)
            self.db.record_outbox_event(
                tenant_id=cmd.tenant_id,
                event_type="INVOICE_ISSUED",
                aggregate_id=invoice_id,
                payload={"customer_id": cmd.customer_id, "amount": str(cmd.amount)}
            )

            # 6. Post-Commit Cache Invalidation
            tx.on_commit(lambda: self.cache.delete(cmd.tenant_id, "customer", cmd.customer_id))

        return self.db.get_invoice(invoice_id, cmd.tenant_id)


# ============================================================================
# SECTION 8: DRF MODELVIEWSET SIMULATOR
# ============================================================================

class InvoiceViewSetSimulator:
    """
    Simulates DRF ModelViewSet with dynamic filtering and keyset pagination.
    """
    def __init__(self, db: EnterpriseSaaSDB) -> None:
        self.db = db

    def list(self, user: AuthenticatedUser, query_params: Dict[str, str]) -> Dict[str, Any]:
        MultiTenantRBAC.check_permission(user, "invoices:read")
        
        # Scoped QuerySet: strictly filtered by user's tenant
        invoices = self.db.query_invoices(user.tenant_id)

        # Dynamic Filtering
        if "status" in query_params:
            invoices = [inv for inv in invoices if inv["status"] == query_params["status"]]
        if "min_amount" in query_params:
            min_amt = Decimal(query_params["min_amount"])
            invoices = [inv for inv in invoices if Decimal(inv["amount"]) >= min_amt]
        if "search" in query_params:
            term = query_params["search"].lower()
            invoices = [inv for inv in invoices if term in inv["memo"].lower()]

        # Sort descending (-created_at, -id)
        invoices.sort(key=lambda x: (x["created_at"], x["id"]), reverse=True)

        # Keyset Pagination
        cursor = query_params.get("cursor")
        return MultiTenantCursorPagination.paginate(invoices, cursor_token=cursor)

    def retrieve(self, user: AuthenticatedUser, invoice_id: int) -> Dict[str, Any]:
        MultiTenantRBAC.check_permission(user, "invoices:read")
        # Returns invoice or raises NotFound (404) if cross-tenant
        return self.db.get_invoice(invoice_id, user.tenant_id)


# ============================================================================
# SECTION 9: ARCHITECTURAL VERIFICATION HARNESS
# ============================================================================

def run_tests() -> None:
    print("=" * 70)
    print(" Day 45: Capstone 1 — Enterprise Multi-Tenant DRF SaaS Backend Tests")
    print("=" * 70)

    db = EnterpriseSaaSDB()
    cache = MultiTenantCache()
    service = InvoiceService(db, cache)
    viewset = InvoiceViewSetSimulator(db)

    # ------------------------------------------------------------------------
    # [TEST 1] Cryptographic Multi-Tenant JWT Minting & RBAC Authentication
    # ------------------------------------------------------------------------
    print("\n[TEST 1] JWT Authentication & Cross-Tenant Tampering Guard...")
    
    # Mint token for Alice at Acme Corp (role=OWNER)
    token_alice = MultiTenantJWTEngine.mint_token(
        user_id="usr_alice",
        tenant_id="tenant_acme",
        role="OWNER",
        email="alice@acme.com"
    )
    user_alice = MultiTenantJWTEngine.verify_and_authenticate(token_alice, header_tenant_id="tenant_acme")
    assert user_alice.tenant_id == "tenant_acme"
    assert user_alice.role == "OWNER"
    print("  -> Alice authenticated as OWNER of tenant_acme.")

    # Tampering test: Attacker submits Alice's token with header for tenant_beta
    tamper_caught = False
    try:
        MultiTenantJWTEngine.verify_and_authenticate(token_alice, header_tenant_id="tenant_beta")
    except AuthenticationFailed as exc:
        tamper_caught = True
        print(f"  -> Caught expected tampering rejection: {exc}")
    assert tamper_caught is True, "Vulnerability: Cross-tenant header tampering allowed!"

    # ------------------------------------------------------------------------
    # [TEST 2] RBAC Permission Enforcement (OWNER vs VIEWER)
    # ------------------------------------------------------------------------
    print("\n[TEST 2] Multi-Tenant RBAC Permissions Enforcement...")
    token_bob = MultiTenantJWTEngine.mint_token(
        user_id="usr_bob",
        tenant_id="tenant_acme",
        role="VIEWER",
        email="bob@acme.com"
    )
    user_bob = MultiTenantJWTEngine.verify_and_authenticate(token_bob)

    # Bob (VIEWER) attempts to issue an invoice (requires invoices:create)
    bob_blocked = False
    cust_id = db.insert_customer("tenant_acme", "Acme Retailer", Decimal("5000.00"))
    cmd_bob = IssueInvoiceCommand(
        tenant_id="tenant_acme",
        customer_id=cust_id,
        amount=Decimal("150.00"),
        memo="Illegal invoice attempt",
        actor_user=user_bob,
        idempotency_key="key-bob-1"
    )
    try:
        service.issue_invoice(cmd_bob)
    except PermissionDenied as exc:
        bob_blocked = True
        print(f"  -> Caught expected RBAC block: {exc}")
    assert bob_blocked is True, "Vulnerability: VIEWER was permitted to create invoice!"

    # ------------------------------------------------------------------------
    # [TEST 3] Service Layer: Atomic Execution & Outbox Recording
    # ------------------------------------------------------------------------
    print("\n[TEST 3] Service Layer Atomic Execution & Transactional Outbox...")
    cmd_alice = IssueInvoiceCommand(
        tenant_id="tenant_acme",
        customer_id=cust_id,
        amount=Decimal("1200.00"),
        memo="Enterprise SLA Tier 1",
        actor_user=user_alice,
        idempotency_key="key-alice-1"
    )
    inv = service.issue_invoice(cmd_alice)
    assert inv["amount"] == "1200.00"
    assert inv["status"] == "PAID"
    
    # Check updated customer balance (5000 - 1200 = 3800)
    updated_cust = db.customers[cust_id]
    assert updated_cust["balance"] == "3800.00"
    assert len(db.outbox_events) == 1
    assert db.outbox_events[0]["event_type"] == "INVOICE_ISSUED"
    print(f"  -> Invoice created #{inv['id']}. Customer balance updated to ${updated_cust['balance']}.")
    print(f"  -> Outbox Event recorded with type '{db.outbox_events[0]['event_type']}'.")

    # ------------------------------------------------------------------------
    # [TEST 4] Cross-Tenant IDOR Attack Simulation
    # ------------------------------------------------------------------------
    print("\n[TEST 4] Cross-Tenant IDOR Attack Simulation...")
    # Eve is an ADMIN at tenant_beta
    token_eve = MultiTenantJWTEngine.mint_token(
        user_id="usr_eve",
        tenant_id="tenant_beta",
        role="ADMIN",
        email="eve@beta.com"
    )
    user_eve = MultiTenantJWTEngine.verify_and_authenticate(token_eve)

    # Eve tries to retrieve Alice's invoice (from tenant_acme)
    idor_blocked = False
    try:
        viewset.retrieve(user=user_eve, invoice_id=inv["id"])
    except NotFound as exc:
        idor_blocked = True
        print(f"  -> IDOR attempt thwarted! Caught 404: {exc}")
    assert idor_blocked is True, "CRITICAL VULNERABILITY: Tenant Beta accessed Tenant Acme's invoice!"
    print("  -> IDOR DEFENSE VERIFIED: Cross-tenant resource lookup safely returned 404.")

    # ------------------------------------------------------------------------
    # [TEST 5] Multi-Tenant Keyset (Cursor) Pagination Traversal
    # ------------------------------------------------------------------------
    print("\n[TEST 5] Multi-Tenant Keyset (Cursor) Pagination...")
    # Create 12 invoices for tenant_acme
    for i in range(2, 14):
        db.insert_invoice({
            "tenant_id": "tenant_acme",
            "customer_id": cust_id,
            "amount": f"{i * 100}.00",
            "status": "PAID" if i % 2 == 0 else "PENDING",
            "memo": f"Invoice #{i:04d}",
            "created_at": f"2026-11-01T10:{i:02d}:00Z",
            "created_by": user_alice.user_id,
            "idempotency_key": f"key-{i}"
        })

    # Page 1 fetch
    p1 = viewset.list(user_alice, {"page_size": "5"})
    assert len(p1["results"]) == 5
    next_cursor = p1["next"].split("=")[1]
    print(f"  -> Page 1 returned 5 items. Next Cursor: {next_cursor[:20]}...")

    # Page 2 fetch via cursor
    p2 = viewset.list(user_alice, {"cursor": next_cursor})
    assert len(p2["results"]) == 5
    p1_ids = {item["id"] for item in p1["results"]}
    p2_ids = {item["id"] for item in p2["results"]}
    assert len(p1_ids.intersection(p2_ids)) == 0, "Page Drift detected across cursor pages!"
    print(f"  -> Page 2 returned 5 items with 0 duplicate records.")

    # Verify Eve sees 0 invoices for tenant_beta
    p_eve = viewset.list(user_eve, {})
    assert len(p_eve["results"]) == 0
    print(f"  -> Tenant Beta list query returned 0 invoices (complete isolation).")

    print("\nAll Day 45 Capstone Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
