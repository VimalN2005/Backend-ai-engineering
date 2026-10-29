# Practical ViewSet, Router & Custom Action Challenges

This document contains 3 enterprise-grade engineering challenges covering dynamic ViewSet configuration, multi-tenant IDOR protection, and state-machine transitions using `@action` endpoints. Each challenge includes a real-world scenario, architectural constraints, and an executable reference solution.

---

## Challenge 1: Action-Specific Dynamic Serializer & Permission Router

### Scenario
An enterprise API manages sensitive corporate assets. Different endpoints require radically different security and serialization profiles:
- **`list`**: Accessible to any authenticated user; returns a compact summary omitting internal financial metrics.
- **`retrieve`**: Requires compliance manager role; returns full asset telemetry and cost models.
- **`create`**: Requires asset manager role; strictly validates creation schemas.
- **`destroy`**: Restricted exclusively to System Administrators.

You must build a ViewSet controller that dynamically evaluates `get_serializer_class()` and `get_permissions()` based on `self.action`, verifying that requests receive the correct schema and are rejected if the user lacks the action-specific permission.

### Requirements & Constraints
- Pure Python standard library only.
- Implement dynamic dispatch matching `self.action`.
- Enforce that `get_permissions()` returns instantiated permission objects.

### Executable Solution & Verification

```python
from typing import Any, Dict, List, Optional


class User:
    def __init__(self, username: str, roles: List[str]) -> None:
        self.username = username
        self.roles = set(roles)


# --- Permissions ---
class IsAuthenticated:
    def has_permission(self, user: User) -> bool:
        return user is not None


class IsComplianceManager:
    def has_permission(self, user: User) -> bool:
        return "compliance" in user.roles or "admin" in user.roles


class IsAssetManager:
    def has_permission(self, user: User) -> bool:
        return "asset_mgr" in user.roles or "admin" in user.roles


class IsSystemAdmin:
    def has_permission(self, user: User) -> bool:
        return "admin" in user.roles


# --- Serializers ---
class AssetSummarySerializer:
    @staticmethod
    def serialize(obj: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": obj["id"], "name": obj["name"], "status": obj["status"]}


class AssetDetailSerializer:
    @staticmethod
    def serialize(obj: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": obj["id"],
            "name": obj["name"],
            "status": obj["status"],
            "valuation_usd": obj["valuation_usd"],
            "audit_code": obj["audit_code"],
        }


class AssetCreateSerializer:
    @staticmethod
    def validate(data: Dict[str, Any]) -> Dict[str, Any]:
        if "name" not in data or not data["name"]:
            raise ValueError("Asset name is required.")
        val = data.get("valuation_usd", 0)
        if float(val) <= 0:
            raise ValueError("Valuation must be positive.")
        return {"name": data["name"], "valuation_usd": float(val), "status": "ACTIVE", "audit_code": "AUD-NEW"}


# --- ViewSet Controller ---
class DynamicAssetViewSet:
    _db: Dict[int, Dict[str, Any]] = {
        101: {"id": 101, "name": "Server Cluster Alpha", "status": "ACTIVE", "valuation_usd": 250000.0, "audit_code": "SEC-01"},
    }

    def __init__(self, action: str, user: User) -> None:
        self.action = action
        self.user = user

    def get_serializer_class(self) -> type:
        if self.action == "list":
            return AssetSummarySerializer
        elif self.action == "retrieve":
            return AssetDetailSerializer
        elif self.action == "create":
            return AssetCreateSerializer
        return AssetDetailSerializer

    def get_permissions(self) -> List[Any]:
        if self.action == "list":
            perm_classes = [IsAuthenticated]
        elif self.action == "retrieve":
            perm_classes = [IsComplianceManager]
        elif self.action == "create":
            perm_classes = [IsAssetManager]
        elif self.action == "destroy":
            perm_classes = [IsSystemAdmin]
        else:
            perm_classes = [IsSystemAdmin]

        return [cls() for cls in perm_classes]

    def check_permissions(self) -> None:
        for perm in self.get_permissions():
            if not perm.has_permission(self.user):
                raise PermissionError(f"User '{self.user.username}' denied access to action '{self.action}'.")

    def list(self) -> List[Dict[str, Any]]:
        self.check_permissions()
        serializer = self.get_serializer_class()
        return [serializer.serialize(item) for item in self._db.values()]

    def retrieve(self, pk: int) -> Dict[str, Any]:
        self.check_permissions()
        if pk not in self._db:
            raise KeyError(f"Asset #{pk} not found.")
        serializer = self.get_serializer_class()
        return serializer.serialize(self._db[pk])

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        self.check_permissions()
        serializer = self.get_serializer_class()
        validated = serializer.validate(data)
        new_id = max(self._db.keys(), default=0) + 1
        record = {"id": new_id, **validated}
        self._db[new_id] = record
        return AssetDetailSerializer.serialize(record)

    def destroy(self, pk: int) -> None:
        self.check_permissions()
        if pk in self._db:
            del self._db[pk]


# --- Verification Harness ---
if __name__ == "__main__":
    user_viewer = User("alice", roles=["viewer"])
    user_compliance = User("bob", roles=["compliance"])
    user_admin = User("root", roles=["admin"])

    # 1. Test List action with Viewer (allowed, receives summary without valuation)
    v_list = DynamicAssetViewSet(action="list", user=user_viewer)
    res_list = v_list.list()
    assert len(res_list) == 1
    assert "valuation_usd" not in res_list[0]
    assert res_list[0]["name"] == "Server Cluster Alpha"

    # 2. Test Retrieve action with Viewer (denied: lacks compliance role)
    v_ret_denied = DynamicAssetViewSet(action="retrieve", user=user_viewer)
    try:
        v_ret_denied.retrieve(101)
        assert False, "Viewer should not be able to retrieve detail valuation!"
    except PermissionError:
        pass

    # 3. Test Retrieve action with Compliance Manager (allowed, receives full detail)
    v_ret_allowed = DynamicAssetViewSet(action="retrieve", user=user_compliance)
    res_detail = v_ret_allowed.retrieve(101)
    assert res_detail["valuation_usd"] == 250000.0
    assert res_detail["audit_code"] == "SEC-01"

    # 4. Test Destroy action with Compliance Manager (denied: requires admin)
    v_destroy_denied = DynamicAssetViewSet(action="destroy", user=user_compliance)
    try:
        v_destroy_denied.destroy(101)
        assert False, "Compliance manager should not have destroy permission"
    except PermissionError:
        pass

    # 5. Test Destroy action with Admin (allowed)
    v_destroy_ok = DynamicAssetViewSet(action="destroy", user=user_admin)
    v_destroy_ok.destroy(101)
    assert 101 not in DynamicAssetViewSet._db

    print("[SUCCESS] Challenge 1: Dynamic Serializer & Permission Router verified.")
```

---

## Challenge 2: Multi-Tenant Scoping & IDOR Protection Guard

### Scenario
A multi-tenant healthcare SaaS stores patient clinical contracts. An Insecure Direct Object Reference (IDOR) bug in any endpoint could expose Protected Health Information (PHI) to unauthorized clinic organizations.

You must design an architectural pattern where:
1. `get_queryset()` strictly filters records by `request.user.tenant_id`.
2. Both standard actions (`retrieve`, `destroy`) and custom `@action` methods (e.g. `/sign/`) call `get_object()`, guaranteeing that cross-tenant access attempts return HTTP 404 Not Found without leaking whether the ID even exists.

### Requirements & Constraints
- Pure Python standard library only.
- Strict multi-tenant barrier.
- Verify that attempting to access a different tenant's record raises `KeyError` (404 Not Found).

### Executable Solution & Verification

```python
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Contract:
    id: int
    tenant_id: str
    patient_name: str
    status: str  # "DRAFT", "SIGNED"


class HealthcareUser:
    def __init__(self, username: str, tenant_id: str) -> None:
        self.username = username
        self.tenant_id = tenant_id


class ContractViewSet:
    """
    ViewSet demonstrating strict multi-tenant IDOR protection.
    """
    _table: Dict[int, Contract] = {
        1: Contract(id=1, tenant_id="clinic_alpha", patient_name="John Doe", status="DRAFT"),
        2: Contract(id=2, tenant_id="clinic_beta", patient_name="Jane Smith", status="DRAFT"),
    }

    def __init__(self, user: HealthcareUser) -> None:
        self.user = user

    def get_queryset(self) -> List[Contract]:
        """
        Multi-Tenant Boundary: Scopes records strictly to the caller's clinic.
        """
        return [c for c in self._table.values() if c.tenant_id == self.user.tenant_id]

    def get_object(self, pk: int) -> Contract:
        """
        Retrieves instance strictly from the scoped queryset.
        Raises KeyError (404) if record is missing OR belongs to another tenant.
        """
        for contract in self.get_queryset():
            if contract.id == pk:
                return contract
        raise KeyError(f"Contract #{pk} not found.")

    def retrieve(self, pk: int) -> Dict[str, Any]:
        contract = self.get_object(pk)
        return {"id": contract.id, "patient": contract.patient_name, "status": contract.status}

    def sign(self, pk: int) -> Dict[str, Any]:
        """Custom instance action (detail=True)."""
        contract = self.get_object(pk)  # Protected by multi-tenant get_object()!
        contract.status = "SIGNED"
        return {"id": contract.id, "status": "SIGNED"}


# --- Verification Harness ---
if __name__ == "__main__":
    dr_alice = HealthcareUser("dr_alice", tenant_id="clinic_alpha")
    dr_bob = HealthcareUser("dr_bob", tenant_id="clinic_beta")

    viewset_alice = ContractViewSet(dr_alice)
    viewset_bob = ContractViewSet(dr_bob)

    # 1. Alice can view her own patient's contract (ID=1)
    res_alice = viewset_alice.retrieve(1)
    assert res_alice["patient"] == "John Doe"

    # 2. Alice attempts to view Bob's patient's contract (ID=2) -> IDOR Attempt
    try:
        viewset_alice.retrieve(2)
        assert False, "Alice should be blocked from accessing clinic_beta's records!"
    except KeyError:
        pass

    # 3. Alice attempts to execute custom action on Bob's contract -> IDOR Attempt
    try:
        viewset_alice.sign(2)
        assert False, "Alice should be blocked from signing clinic_beta's records!"
    except KeyError:
        pass

    # 4. Bob signs his own contract
    res_sign = viewset_bob.sign(2)
    assert res_sign["status"] == "SIGNED"
    assert ContractViewSet._table[2].status == "SIGNED"

    print("[SUCCESS] Challenge 2: Multi-Tenant Scoping & IDOR Protection Guard verified.")
```

---

## Challenge 3: Composite RPC State Transition Engine via `@action(detail=True)`

### Scenario
An enterprise supply chain ordering system requires orders to traverse a deterministic finite state machine (FSM):
$$\text{DRAFT} \xrightarrow{\text{submit}} \text{SUBMITTED} \xrightarrow{\text{fulfill}} \text{FULFILLED}$$
$$\text{DRAFT} \text{ or } \text{SUBMITTED} \xrightarrow{\text{cancel}} \text{CANCELLED}$$

You must implement custom `@action(detail=True)` endpoints on a ViewSet to model these operations. The endpoints must validate valid state transitions, accept domain parameters (e.g. `cancellation_reason`), and return structured errors when attempting illegal transitions (e.g. canceling an already fulfilled order).

### Requirements & Constraints
- Pure Python standard library only.
- Transition rules:
  - `submit`: Only allowed if status is `DRAFT`.
  - `fulfill`: Only allowed if status is `SUBMITTED`.
  - `cancel`: Only allowed if status is `DRAFT` or `SUBMITTED`; requires non-empty `reason`.
- Any illegal transition must raise `ValueError`.

### Executable Solution & Verification

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SupplyOrder:
    id: int
    sku: str
    status: str  # "DRAFT", "SUBMITTED", "FULFILLED", "CANCELLED"
    cancellation_reason: Optional[str] = None


class SupplyOrderViewSet:
    _orders: Dict[int, SupplyOrder] = {
        1: SupplyOrder(id=1, sku="SKU-CHIP-01", status="DRAFT"),
        2: SupplyOrder(id=2, sku="SKU-CHIP-02", status="SUBMITTED"),
        3: SupplyOrder(id=3, sku="SKU-CHIP-03", status="FULFILLED"),
    }

    def get_object(self, pk: int) -> SupplyOrder:
        if pk not in self._orders:
            raise KeyError("Order not found.")
        return self._orders[pk]

    def submit(self, pk: int) -> Dict[str, Any]:
        """Custom action: Transition DRAFT -> SUBMITTED."""
        order = self.get_object(pk)
        if order.status != "DRAFT":
            raise ValueError(f"Cannot submit order in '{order.status}' status. Must be DRAFT.")
        order.status = "SUBMITTED"
        return {"id": order.id, "status": order.status}

    def fulfill(self, pk: int) -> Dict[str, Any]:
        """Custom action: Transition SUBMITTED -> FULFILLED."""
        order = self.get_object(pk)
        if order.status != "SUBMITTED":
            raise ValueError(f"Cannot fulfill order in '{order.status}' status. Must be SUBMITTED.")
        order.status = "FULFILLED"
        return {"id": order.id, "status": order.status}

    def cancel(self, pk: int, reason: str) -> Dict[str, Any]:
        """Custom action: Transition DRAFT/SUBMITTED -> CANCELLED."""
        order = self.get_object(pk)
        if order.status not in ("DRAFT", "SUBMITTED"):
            raise ValueError(f"Cannot cancel order in '{order.status}' status.")
        if not reason or not reason.strip():
            raise ValueError("A valid cancellation reason is required.")

        order.status = "CANCELLED"
        order.cancellation_reason = reason.strip()
        return {"id": order.id, "status": order.status, "reason": order.cancellation_reason}


# --- Verification Harness ---
if __name__ == "__main__":
    controller = SupplyOrderViewSet()

    # 1. Order 1: DRAFT -> SUBMITTED
    res1 = controller.submit(1)
    assert res1["status"] == "SUBMITTED"

    # 2. Order 1: SUBMITTED -> FULFILLED
    res2 = controller.fulfill(1)
    assert res2["status"] == "FULFILLED"

    # 3. Order 1: Attempt to Cancel FULFILLED order -> Must Fail!
    try:
        controller.cancel(1, reason="Customer changed mind")
        assert False, "Should not be able to cancel fulfilled order!"
    except ValueError as exc:
        assert "Cannot cancel order in 'FULFILLED' status" in str(exc)

    # 4. Order 2: SUBMITTED -> CANCELLED (with valid reason)
    res_cancel = controller.cancel(2, reason="Stock damaged in warehouse")
    assert res_cancel["status"] == "CANCELLED"
    assert res_cancel["reason"] == "Stock damaged in warehouse"

    # 5. Order 2: Missing cancellation reason -> Must Fail!
    try:
        controller.cancel(2, reason="   ")
        assert False, "Empty reason should be rejected"
    except ValueError as exc:
        assert "cancellation reason is required" in str(exc)

    print("[SUCCESS] Challenge 3: Composite RPC State Transition Engine verified.")
```
