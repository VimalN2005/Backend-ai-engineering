"""
Day 40: DRF ViewSets, ModelViewSets, Routers & Custom Action Endpoints
Example 02: Enterprise ModelViewSet Architecture: Multi-Tenant Scoping & Custom Actions

Architecture Overview:
----------------------
This module implements a production-grade enterprise ModelViewSet controller demonstrating:
  1. Strict Multi-Tenant QuerySet Scoping (`get_queryset()`):
     - Prevents Insecure Direct Object Reference (IDOR) attacks across organization boundaries.
  2. Dynamic Serializer Switching (`get_serializer_class()`):
     - Action-specific serializers (ListSummarySerializer vs DetailedSerializer vs CreateSerializer).
  3. Action-Specific Permission Enforcement (`get_permissions()`):
     - Fine-grained permission classes mapped to specific action verbs.
  4. Enterprise Soft-Delete Lifecycle:
     - `perform_destroy()` marks records as deleted with audit metadata.
     - Custom `@action(detail=True)` provides `/restore/` endpoint.
  5. Custom Business Action Endpoints:
     - `@action(detail=True)` for `/approve/` financial state transitions.
     - `@action(detail=False)` for `/bulk-export/` reporting.
  6. High-Throughput Production Benchmark:
     - Benchmarks 10,000 multi-tenant API requests through the ViewSet dispatch pipeline.

WHY THIS ARCHITECTURE?
----------------------
In enterprise SaaS platforms, a single ViewSet governs an entire domain aggregate.
Coupling multi-tenant data isolation in `get_queryset()` with action-specific serializers
and soft-delete auditing guarantees compliance, eliminates data leakage, and provides
clean, maintainable REST controllers.
"""

from __future__ import annotations

import copy
import datetime
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. Domain Entities & Storage
# ============================================================================

@dataclass
class Invoice:
    id: int
    tenant_id: str
    client_name: str
    amount: float
    status: str  # "DRAFT", "PENDING", "APPROVED", "PAID"
    created_by: str
    is_deleted: bool = False
    deleted_at: Optional[float] = None
    deleted_by: Optional[str] = None


class UserContext:
    def __init__(self, username: str, tenant_id: str, role: str = "member", is_superuser: bool = False) -> None:
        self.username = username
        self.tenant_id = tenant_id
        self.role = role  # "member", "admin"
        self.is_superuser = is_superuser
        self.is_authenticated = True


# ============================================================================
# 2. Serializers for Dynamic Action Switching
# ============================================================================

class InvoiceListSerializer:
    """Lightweight serializer for collection listing (omits heavy audit fields)."""
    @staticmethod
    def to_representation(obj: Invoice) -> Dict[str, Any]:
        return {
            "id": obj.id,
            "client_name": obj.client_name,
            "amount": obj.amount,
            "status": obj.status,
        }


class InvoiceDetailSerializer:
    """Full detail serializer with audit metadata."""
    @staticmethod
    def to_representation(obj: Invoice) -> Dict[str, Any]:
        return {
            "id": obj.id,
            "tenant_id": obj.tenant_id,
            "client_name": obj.client_name,
            "amount": obj.amount,
            "status": obj.status,
            "created_by": obj.created_by,
            "is_deleted": obj.is_deleted,
            "deleted_at": obj.deleted_at,
        }


class InvoiceCreateSerializer:
    """Write serializer validating input schemas."""
    @staticmethod
    def validate(data: Dict[str, Any]) -> Dict[str, Any]:
        client = data.get("client_name")
        amount = data.get("amount")

        if not client or not str(client).strip():
            raise ValueError("Client name is required.")
        if amount is None or float(amount) <= 0.0:
            raise ValueError("Amount must be greater than zero.")

        return {
            "client_name": str(client).strip(),
            "amount": float(amount),
            "status": "DRAFT",
        }


# ============================================================================
# 3. Permissions Engine
# ============================================================================

class AllowAny:
    @staticmethod
    def has_permission(user: UserContext) -> bool:
        return True


class IsAuthenticated:
    @staticmethod
    def has_permission(user: UserContext) -> bool:
        return user.is_authenticated


class IsTenantAdmin:
    @staticmethod
    def has_permission(user: UserContext) -> bool:
        return user.is_authenticated and (user.role == "admin" or user.is_superuser)


# ============================================================================
# 4. Enterprise ModelViewSet Controller
# ============================================================================

class EnterpriseInvoiceViewSet:
    """
    Production-grade ModelViewSet simulating DRF's GenericViewSet + ModelMixin pipeline.
    """

    # Simulated shared persistent database table
    _db: Dict[int, Invoice] = {}
    _next_id: int = 100

    def __init__(self, action: str, user: UserContext) -> None:
        self.action = action
        self.user = user

    # ------------------------------------------------------------------------
    # Core GenericAPIView Lifecycle Hooks
    # ------------------------------------------------------------------------

    def get_queryset(self, include_deleted: bool = False) -> List[Invoice]:
        """
        WHY STRICT QUERYSET SCOPING?
        Filtering by request.user.tenant_id guarantees that all downstream actions
        (list, retrieve, update, destroy, and custom actions) are physically incapable
        of accessing records belonging to another tenant, eliminating IDOR vulnerabilities.
        """
        # Superuser bypass
        if self.user.is_superuser:
            qs = list(self._db.values())
        else:
            qs = [inv for inv in self._db.values() if inv.tenant_id == self.user.tenant_id]

        if not include_deleted:
            # Filter out soft-deleted records by default
            qs = [inv for inv in qs if not inv.is_deleted]

        return qs

    def get_serializer_class(self) -> type:
        """
        WHY DYNAMIC SERIALIZER SWITCHING?
        Conserves bandwidth on high-frequency list endpoints while retaining rich
        audit trails on detail endpoints.
        """
        if self.action == "list":
            return InvoiceListSerializer
        elif self.action == "create":
            return InvoiceCreateSerializer
        return InvoiceDetailSerializer

    def get_permissions(self) -> List[type]:
        """
        Enforces action-level security boundaries.
        Destruction and restoration require TenantAdmin; reading requires Member.
        """
        if self.action in ("destroy", "restore"):
            return [IsTenantAdmin]
        elif self.action in ("create", "approve", "bulk_export"):
            return [IsAuthenticated]
        return [IsAuthenticated]

    def check_permissions(self) -> None:
        """Evaluates permission classes against the current user context."""
        for perm_cls in self.get_permissions():
            if not perm_cls.has_permission(self.user):
                raise PermissionError(f"Permission denied for action '{self.action}'.")

    def get_object(self, pk: int, include_deleted: bool = False) -> Invoice:
        """
        Retrieves a single object scoped strictly to the current tenant.
        Raises KeyError (404) if the object does not exist OR belongs to another tenant!
        """
        scoped_qs = self.get_queryset(include_deleted=include_deleted)
        for obj in scoped_qs:
            if obj.id == pk:
                return obj
        raise KeyError(f"Invoice #{pk} not found in current tenant scope.")

    # ------------------------------------------------------------------------
    # Persistence Hooks
    # ------------------------------------------------------------------------

    def perform_create(self, validated_data: Dict[str, Any]) -> Invoice:
        """Injects request-scoped tenant_id and created_by metadata."""
        inv_id = EnterpriseInvoiceViewSet._next_id
        EnterpriseInvoiceViewSet._next_id += 1

        new_invoice = Invoice(
            id=inv_id,
            tenant_id=self.user.tenant_id,
            client_name=validated_data["client_name"],
            amount=validated_data["amount"],
            status="DRAFT",
            created_by=self.user.username,
        )
        self._db[inv_id] = new_invoice
        return new_invoice

    def perform_destroy(self, instance: Invoice) -> None:
        """Soft-deletes the instance rather than executing a hard SQL DELETE."""
        instance.is_deleted = True
        instance.deleted_at = time.time()
        instance.deleted_by = self.user.username

    # ------------------------------------------------------------------------
    # Standard Action Handlers
    # ------------------------------------------------------------------------

    def list(self) -> Dict[str, Any]:
        self.check_permissions()
        qs = self.get_queryset()
        serializer_cls = self.get_serializer_class()
        return {
            "count": len(qs),
            "results": [serializer_cls.to_representation(inv) for inv in qs],
        }

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.check_permissions()
        serializer_cls = self.get_serializer_class()
        validated = serializer_cls.validate(payload)
        instance = self.perform_create(validated)
        return InvoiceDetailSerializer.to_representation(instance)

    def retrieve(self, pk: int) -> Dict[str, Any]:
        self.check_permissions()
        instance = self.get_object(pk)
        return InvoiceDetailSerializer.to_representation(instance)

    def destroy(self, pk: int) -> Dict[str, Any]:
        self.check_permissions()
        instance = self.get_object(pk)
        self.perform_destroy(instance)
        return {"status": "soft_deleted", "id": pk}

    # ------------------------------------------------------------------------
    # Custom Action Endpoints (@action)
    # ------------------------------------------------------------------------

    def approve(self, pk: int) -> Dict[str, Any]:
        """
        POST /invoices/{pk}/approve/ (detail=True)
        Domain business rule: transitions status from DRAFT/PENDING to APPROVED.
        """
        self.check_permissions()
        instance = self.get_object(pk)

        if instance.status == "APPROVED":
            raise ValueError("Invoice is already approved.")
        if instance.status == "PAID":
            raise ValueError("Cannot approve an already paid invoice.")

        instance.status = "APPROVED"
        return {"status": "approved", "invoice_id": instance.id}

    def restore(self, pk: int) -> Dict[str, Any]:
        """
        POST /invoices/{pk}/restore/ (detail=True)
        Recovers a soft-deleted invoice back to active status.
        """
        self.check_permissions()
        # Look up across tenant records INCLUDING soft-deleted
        instance = self.get_object(pk, include_deleted=True)

        if not instance.is_deleted:
            return {"status": "already_active", "invoice_id": instance.id}

        instance.is_deleted = False
        instance.deleted_at = None
        instance.deleted_by = None
        return {"status": "restored", "invoice_id": instance.id}

    def bulk_export(self) -> Dict[str, Any]:
        """
        POST /invoices/bulk-export/ (detail=False)
        Collection operation: generates summary aggregate data for export.
        """
        self.check_permissions()
        qs = self.get_queryset()
        total_value = sum(inv.amount for inv in qs)
        return {
            "tenant_id": self.user.tenant_id,
            "exported_count": len(qs),
            "total_value": round(total_value, 2),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# ============================================================================
# 5. Verification & Performance Benchmark
# ============================================================================

def run_enterprise_verification() -> None:
    print("[STAGE 1] Enterprise ModelViewSet & Action Verification")
    print("-" * 65)

    # Setup Tenants & Users
    t_acme = "tenant_acme"
    t_beta = "tenant_beta"

    user_alice = UserContext(username="alice", tenant_id=t_acme, role="member")
    admin_alice = UserContext(username="alice_admin", tenant_id=t_acme, role="admin")
    user_bob = UserContext(username="bob", tenant_id=t_beta, role="member")

    # Clear table
    EnterpriseInvoiceViewSet._db.clear()

    # 1. Test Creation & Injection Hooks
    s_create = EnterpriseInvoiceViewSet(action="create", user=user_alice)
    inv1 = s_create.create({"client_name": "MegaCorp Logistics", "amount": 15000.0})
    inv1_id = inv1["id"]
    assert inv1["tenant_id"] == t_acme
    assert inv1["created_by"] == "alice"
    assert inv1["status"] == "DRAFT"
    print(f"  [PASS] Creation Hook: Invoice #{inv1_id} created with tenant and user injection.")

    # 2. Test Dynamic Serializer on List (omits tenant_id and is_deleted)
    s_list = EnterpriseInvoiceViewSet(action="list", user=user_alice)
    list_res = s_list.list()
    assert list_res["count"] == 1
    assert "tenant_id" not in list_res["results"][0]  # ListSummarySerializer verified!
    assert "amount" in list_res["results"][0]
    print("  [PASS] Dynamic Serializer: List endpoint used lightweight representation.")

    # 3. Test IDOR Defense (Tenant Isolation)
    # User Bob (Tenant Beta) attempts to retrieve Alice's invoice (Tenant Acme)
    s_bob_retrieve = EnterpriseInvoiceViewSet(action="retrieve", user=user_bob)
    try:
        s_bob_retrieve.retrieve(inv1_id)
        assert False, "Bob should not be able to view Alice's invoice!"
    except KeyError:
        print(f"  [PASS] IDOR Defense: Cross-tenant access to Invoice #{inv1_id} strictly rejected (404).")

    # 4. Test Custom Action: Approve (@action detail=True)
    s_approve = EnterpriseInvoiceViewSet(action="approve", user=user_alice)
    approve_res = s_approve.approve(inv1_id)
    assert approve_res["status"] == "approved"
    assert EnterpriseInvoiceViewSet._db[inv1_id].status == "APPROVED"
    print(f"  [PASS] Custom Action (detail=True): Invoice #{inv1_id} approved successfully.")

    # 5. Test Action-Level Permissions: Destroy requires Admin
    s_member_destroy = EnterpriseInvoiceViewSet(action="destroy", user=user_alice)
    try:
        s_member_destroy.destroy(inv1_id)
        assert False, "Member should not have destroy permission!"
    except PermissionError:
        print("  [PASS] Action Permission: Member correctly denied destroy operation (403).")

    # 6. Test Soft-Delete via Admin
    s_admin_destroy = EnterpriseInvoiceViewSet(action="destroy", user=admin_alice)
    del_res = s_admin_destroy.destroy(inv1_id)
    assert del_res["status"] == "soft_deleted"
    assert EnterpriseInvoiceViewSet._db[inv1_id].is_deleted is True

    # Verify soft-deleted invoice disappears from standard list
    assert len(s_list.list()["results"]) == 0
    print(f"  [PASS] Soft-Delete: Invoice #{inv1_id} marked deleted and filtered from standard views.")

    # 7. Test Restore Action (@action detail=True)
    s_restore = EnterpriseInvoiceViewSet(action="restore", user=admin_alice)
    restore_res = s_restore.restore(inv1_id)
    assert restore_res["status"] == "restored"
    assert EnterpriseInvoiceViewSet._db[inv1_id].is_deleted is False
    assert len(s_list.list()["results"]) == 1
    print(f"  [PASS] Custom Action (Restore): Invoice #{inv1_id} successfully restored.")

    # 8. Test Collection Action: Bulk Export (@action detail=False)
    s_export = EnterpriseInvoiceViewSet(action="bulk_export", user=user_alice)
    exp_res = s_export.bulk_export()
    assert exp_res["exported_count"] == 1
    assert exp_res["total_value"] == 15000.0
    print("  [PASS] Custom Action (detail=False): Collection bulk export verified.\n")


def run_viewset_benchmark(iterations: int = 10_000) -> None:
    print(f"[STAGE 2] High-Throughput ViewSet Benchmark ({iterations:,} iterations)")
    print("-" * 65)

    # Pre-populate database with 500 invoices across 10 tenants
    EnterpriseInvoiceViewSet._db.clear()
    for i in range(1, 501):
        t_id = f"tenant_{i % 10}"
        EnterpriseInvoiceViewSet._db[i] = Invoice(
            id=i,
            tenant_id=t_id,
            client_name=f"Client-{i}",
            amount=float(i * 100),
            status="PENDING",
            created_by="bench_worker",
        )

    user = UserContext(username="bench_user", tenant_id="tenant_3", role="member")
    viewset = EnterpriseInvoiceViewSet(action="list", user=user)

    latencies_us: List[float] = []
    start_total = time.perf_counter()

    for i in range(iterations):
        t0 = time.perf_counter()
        _ = viewset.list()
        t1 = time.perf_counter()
        latencies_us.append((t1 - t0) * 1_000_000.0)

    total_time_sec = time.perf_counter() - start_total
    throughput = iterations / total_time_sec

    latencies_us.sort()
    p50 = latencies_us[int(iterations * 0.50)]
    p95 = latencies_us[int(iterations * 0.95)]
    p99 = latencies_us[int(iterations * 0.99)]

    print(f"Total Requests      : {iterations:,} ViewSet list executions")
    print(f"Wall Clock Time     : {total_time_sec:.4f} seconds")
    print(f"Throughput          : {throughput:,.1f} requests/sec")
    print(f"Latency p50 (median): {p50:.2f} microseconds (us)")
    print(f"Latency p95         : {p95:.2f} microseconds (us)")
    print(f"Latency p99         : {p99:.2f} microseconds (us)")
    print("=" * 65)


if __name__ == "__main__":
    print("======================================================================")
    print(" Day 40: Enterprise ModelViewSet & Action Dispatch Benchmark")
    print("======================================================================\n")
    run_enterprise_verification()
    run_viewset_benchmark(10_000)
