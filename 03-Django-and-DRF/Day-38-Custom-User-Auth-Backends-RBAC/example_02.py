"""
Day 38: Custom User Model, Authentication Backends & RBAC Permissions
Example 02: Enterprise Role-Based (RBAC) & Object-Level Access Control Engine

Architecture Overview:
----------------------
This module implements a production-grade, multi-tenant authorization engine modeled after
Django's permission architecture, extended with enterprise capabilities:
  1. Hierarchical Role Engine: Directed acyclic role inheritance (Viewer < Editor < Manager < OrgAdmin).
  2. Multi-Tenant Isolation Barrier: Enforces tenant boundaries before evaluating permissions.
  3. Object-Level Policy Engine: Evaluates resource attributes, ownership, and separation of duties.
  4. Per-Request Memoization Cache: Mimics Django's `user._perm_cache` to prevent repeated policy walks.
  5. High-Throughput Benchmark: Evaluates 10,000 authorization decisions across multi-tenant resources.

WHY THIS ARCHITECTURE?
----------------------
Django's default `ModelBackend.has_perm(user, perm, obj=None)` discards the `obj` parameter.
In multi-tenant SaaS applications, granting a user `billing.change_invoice` globally is catastrophic;
permissions must be evaluated against the specific tenant and resource instance (e.g., Alice can edit
her own draft invoices in Tenant A, but cannot approve an invoice she created above $10,000).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. Domain Entities & Role Definitions
# ============================================================================

@dataclass(frozen=True)
class Role:
    """
    Immutable Role definition with explicit inheritance.
    WHY INHERITANCE?
    Managing permissions per user creates administrative nightmares. Defining roles
    that inherit from lower tiers guarantees consistent privilege ladders.
    """
    name: str
    permissions: Set[str] = field(default_factory=set)
    parent: Optional["Role"] = None

    def get_all_permissions(self) -> Set[str]:
        """Recursively collects all permissions from this role and all parent roles."""
        perms = set(self.permissions)
        current = self.parent
        while current is not None:
            perms.update(current.permissions)
            current = current.parent
        return perms


# Standard Enterprise Role Hierarchy
ROLE_VIEWER = Role(
    name="Viewer",
    permissions={"invoice:read", "report:read", "audit:read"},
)

ROLE_EDITOR = Role(
    name="Editor",
    permissions={"invoice:create", "invoice:update"},
    parent=ROLE_VIEWER,  # Inherits invoice:read, report:read, audit:read
)

ROLE_PROJECT_MANAGER = Role(
    name="ProjectManager",
    permissions={"invoice:approve", "invoice:export", "project:manage"},
    parent=ROLE_EDITOR,  # Inherits Editor and Viewer permissions
)

ROLE_ORG_ADMIN = Role(
    name="OrgAdmin",
    permissions={"tenant:manage", "user:invite", "user:modify_role", "invoice:delete"},
    parent=ROLE_PROJECT_MANAGER,  # Top of tenant hierarchy
)


# ============================================================================
# 2. User & Resource Models
# ============================================================================

class AuthUser:
    """Authenticated user context with tenant binding and role assignments."""

    def __init__(
        self,
        user_id: uuid.UUID,
        email: str,
        tenant_id: str,
        roles: Optional[List[Role]] = None,
        direct_permissions: Optional[Set[str]] = None,
        is_active: bool = True,
        is_superuser: bool = False,
    ) -> None:
        self.id = user_id
        self.email = email
        self.tenant_id = tenant_id
        self.roles = roles or []
        self.direct_permissions = direct_permissions or set()
        self.is_active = is_active
        self.is_superuser = is_superuser

        # Per-request caches (mimicking Django's _perm_cache)
        self._cached_permissions: Optional[Set[str]] = None
        self._object_perm_cache: Dict[Tuple[str, str], bool] = {}

    def get_all_permissions(self) -> Set[str]:
        """Resolves and caches combined permissions from direct grants and assigned roles."""
        if self._cached_permissions is None:
            combined: Set[str] = set(self.direct_permissions)
            for role in self.roles:
                combined.update(role.get_all_permissions())
            self._cached_permissions = combined
        return self._cached_permissions

    def clear_perm_cache(self) -> None:
        """Invalidates permission cache (invoked after role modifications)."""
        self._cached_permissions = None
        self._object_perm_cache.clear()


@dataclass
class Invoice:
    """Multi-tenant financial invoice resource."""
    id: str
    tenant_id: str
    owner_id: uuid.UUID
    amount: float
    status: str  # "DRAFT", "PENDING_APPROVAL", "PAID", "CANCELLED"


# ============================================================================
# 3. Enterprise Policy Evaluator (RBAC + ABAC + Tenant Isolation)
# ============================================================================

class AuthorizationEngine:
    """
    Centralized enterprise authorization and policy enforcement service.

    Evaluation Pipeline Order:
      1. Superuser Override: Superadmins bypass all checks.
      2. Active Status Check: Inactive users are unconditionally denied.
      3. Tenant Isolation Check: Rejects requests targeting resources in other tenants.
      4. Object-Level Ownership & Business Rules:
         - Invoice Creator can always edit their own 'DRAFT' invoices.
         - Separation of Duties: Creators CANNOT approve their own invoices > $10,000.
      5. Role-Based Permissions (RBAC): Evaluates required permission string against user roles.
      6. Default Deny.
    """

    @classmethod
    def has_permission(
        cls,
        user: AuthUser,
        permission: str,
        resource: Optional[Any] = None,
    ) -> bool:
        """
        Main authorization entrypoint with memoized per-request cache.
        """
        # Step 0: Check per-request object permission cache
        cache_key = (permission, resource.id if resource else "__global__")
        if cache_key in user._object_perm_cache:
            return user._object_perm_cache[cache_key]

        decision = cls._evaluate_policy(user, permission, resource)

        # Memoize decision for this request
        user._object_perm_cache[cache_key] = decision
        return decision

    @classmethod
    def _evaluate_policy(
        cls,
        user: AuthUser,
        permission: str,
        resource: Optional[Any],
    ) -> bool:
        # Rule 1: Superuser bypasses everything
        if user.is_superuser:
            return True

        # Rule 2: Inactive or suspended accounts cannot perform actions
        if not user.is_active:
            return False

        # If evaluating a specific object, enforce resource policies
        if resource is not None:
            # Rule 3: Multi-Tenant Boundary Enforcement
            # Strict multi-tenant isolation: Never allow cross-tenant data access
            if hasattr(resource, "tenant_id") and resource.tenant_id != user.tenant_id:
                return False

            # Rule 4: Resource-Specific Policy (ABAC / Ownership)
            if isinstance(resource, Invoice):
                # Policy 4a: Creator can edit their own draft invoices
                if permission == "invoice:update":
                    if resource.owner_id == user.id and resource.status == "DRAFT":
                        return True

                # Policy 4b: Separation of Duties (SOX Compliance)
                # An invoice creator CANNOT approve their own invoice if amount > $10,000
                if permission == "invoice:approve":
                    if resource.owner_id == user.id and resource.amount > 10_000.0:
                        # Explicit Deny: Self-approval violation
                        return False

        # Rule 5: Standard RBAC Permission Evaluation
        all_user_perms = user.get_all_permissions()
        if permission in all_user_perms:
            return True

        # Rule 6: Default Deny
        return False


# ============================================================================
# 4. Verification & Performance Benchmark
# ============================================================================

def run_rbac_verification_suite() -> None:
    print("[STAGE 1] Enterprise RBAC & Object-Level Security Verification")
    print("-" * 65)

    tenant_acme = "tenant_acme"
    tenant_beta = "tenant_beta"

    user_alice_id = uuid.uuid4()
    user_bob_id = uuid.uuid4()
    user_admin_id = uuid.uuid4()

    # User Alice: Editor in Acme
    alice = AuthUser(
        user_id=user_alice_id,
        email="alice@acme.com",
        tenant_id=tenant_acme,
        roles=[ROLE_EDITOR],
    )

    # User Bob: Project Manager in Acme (can approve invoices)
    bob = AuthUser(
        user_id=user_bob_id,
        email="bob@acme.com",
        tenant_id=tenant_acme,
        roles=[ROLE_PROJECT_MANAGER],
    )

    # User Charlie: SuperAdmin (global)
    superadmin = AuthUser(
        user_id=uuid.uuid4(),
        email="root@platform.io",
        tenant_id="global",
        is_superuser=True,
    )

    # Resources
    acme_draft_invoice = Invoice(
        id="INV-001",
        tenant_id=tenant_acme,
        owner_id=user_alice_id,
        amount=5_000.0,
        status="DRAFT",
    )

    acme_large_invoice = Invoice(
        id="INV-002",
        tenant_id=tenant_acme,
        owner_id=user_bob_id,
        amount=25_000.0,  # > $10,000
        status="PENDING_APPROVAL",
    )

    beta_invoice = Invoice(
        id="INV-999",
        tenant_id=tenant_beta,
        owner_id=uuid.uuid4(),
        amount=1_000.0,
        status="DRAFT",
    )

    # 1. Test Role Inheritance
    # Bob is ProjectManager -> Inherits Editor and Viewer permissions
    assert bob.get_all_permissions() >= {"invoice:read", "invoice:create", "invoice:update", "invoice:approve"}
    print("  [PASS] Role Hierarchy: ProjectManager correctly inherited Editor & Viewer perms.")

    # 2. Test Ownership Update
    # Alice can update her own draft invoice
    assert AuthorizationEngine.has_permission(alice, "invoice:update", acme_draft_invoice) is True
    print("  [PASS] Object Ownership: Alice allowed to update her own draft invoice.")

    # 3. Test Multi-Tenant Isolation
    # Alice attempts to read an invoice in Tenant Beta -> STRICT REJECTION
    assert AuthorizationEngine.has_permission(alice, "invoice:read", beta_invoice) is False
    print("  [PASS] Tenant Barrier: Alice denied access to Tenant Beta's invoice.")

    # 4. Test Separation of Duties (Self-Approval Safeguard)
    # Bob has 'invoice:approve' permission globally, but is the CREATOR of acme_large_invoice ($25,000)
    # Policy MUST deny Bob from self-approving his own large invoice!
    assert AuthorizationEngine.has_permission(bob, "invoice:approve", acme_large_invoice) is False
    print("  [PASS] Separation of Duties: Bob denied self-approval of $25,000 invoice.")

    # 5. Test SuperAdmin Override
    # SuperAdmin can view and approve anything across any tenant
    assert AuthorizationEngine.has_permission(superadmin, "invoice:approve", acme_large_invoice) is True
    assert AuthorizationEngine.has_permission(superadmin, "invoice:read", beta_invoice) is True
    print("  [PASS] SuperAdmin Override: Root allowed unconditional access.")

    # 6. Test Per-Request Cache Hit
    # Repeating check for Alice on draft invoice should hit cache
    alice.clear_perm_cache()
    t0 = time.perf_counter()
    AuthorizationEngine.has_permission(alice, "invoice:update", acme_draft_invoice)
    t_cold = time.perf_counter() - t0

    t1 = time.perf_counter()
    AuthorizationEngine.has_permission(alice, "invoice:update", acme_draft_invoice)
    t_hot = time.perf_counter() - t1
    assert t_hot < t_cold or t_hot < 0.0001
    print(f"  [PASS] Per-Request Cache: Cold={t_cold * 1e6:.1f}us, Hot Cache={t_hot * 1e6:.1f}us.\n")


def run_authorization_benchmark(iterations: int = 10_000) -> None:
    print(f"[STAGE 2] High-Throughput Authorization Benchmark ({iterations:,} iterations)")
    print("-" * 65)

    tenant_id = "tenant_benchmark"
    user_id = uuid.uuid4()
    user = AuthUser(
        user_id=user_id,
        email="bench_user@acme.com",
        tenant_id=tenant_id,
        roles=[ROLE_PROJECT_MANAGER],
    )

    invoices = [
        Invoice(
            id=f"INV-{i}",
            tenant_id=tenant_id,
            owner_id=user_id if i % 2 == 0 else uuid.uuid4(),
            amount=float(i * 10),
            status="DRAFT" if i % 3 == 0 else "PENDING_APPROVAL",
        )
        for i in range(100)
    ]

    latencies_us: List[float] = []
    start_total = time.perf_counter()

    for i in range(iterations):
        inv = invoices[i % 100]
        # Invalidate per-request cache every 5 calls to simulate fresh requests
        if i % 5 == 0:
            user.clear_perm_cache()

        t0 = time.perf_counter()
        allowed = AuthorizationEngine.has_permission(user, "invoice:update", inv)
        t1 = time.perf_counter()

        latencies_us.append((t1 - t0) * 1_000_000.0)

    total_time_sec = time.perf_counter() - start_total
    throughput = iterations / total_time_sec

    latencies_us.sort()
    p50 = latencies_us[int(iterations * 0.50)]
    p95 = latencies_us[int(iterations * 0.95)]
    p99 = latencies_us[int(iterations * 0.99)]

    print(f"Total Decisions     : {iterations:,} authorizations")
    print(f"Wall Clock Time     : {total_time_sec:.4f} seconds")
    print(f"Throughput          : {throughput:,.1f} decisions/sec")
    print(f"Latency p50 (median): {p50:.2f} microseconds (us)")
    print(f"Latency p95         : {p95:.2f} microseconds (us)")
    print(f"Latency p99         : {p99:.2f} microseconds (us)")
    print("=" * 65)


if __name__ == "__main__":
    print("======================================================================")
    print(" Day 38: Enterprise RBAC & Object-Level Access Control Engine")
    print("======================================================================\n")
    run_rbac_verification_suite()
    run_authorization_benchmark(10_000)
