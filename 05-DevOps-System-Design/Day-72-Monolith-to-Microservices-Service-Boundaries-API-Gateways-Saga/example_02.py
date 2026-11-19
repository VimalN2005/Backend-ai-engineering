"""
Day 72: Saga Pattern Simulator: Orchestration vs. Choreography with Compensating Transactions
=============================================================================================
Architecture & Principles Demonstrated:
1. Distributed Transaction Decomposition:
   - 4 Independent microservices with isolated state: Order, Payment, Inventory, Shipping.
2. Orchestration-based Saga:
   - Centralized Saga Execution Coordinator (SEC) managing a persistent state machine.
   - Forward execution across compensable steps and pivot transactions.
   - Automatic backward compensation cascade on downstream failure.
3. Choreography-based Saga:
   - Decentralized event-driven architecture using pub/sub domain events.
   - Each service listens to domain events and emits next step or compensation triggers.
"""

from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# =====================================================================
# 1. Microservices Domain State & Isolated Datastores
# =====================================================================

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class OrderService:
    """Manages Order aggregate root and status lifecycle."""
    def __init__(self):
        self.orders: Dict[str, Dict[str, Any]] = {}

    def create_order(self, order_id: str, customer_id: str, amount: float) -> bool:
        self.orders[order_id] = {
            "order_id": order_id,
            "customer_id": customer_id,
            "amount": amount,
            "status": OrderStatus.PENDING,
            "created_at": time.time()
        }
        return True

    def confirm_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id]["status"] = OrderStatus.CONFIRMED
            return True
        return False

    def cancel_order_compensation(self, order_id: str) -> bool:
        """Compensating transaction: undoes order creation."""
        if order_id in self.orders:
            self.orders[order_id]["status"] = OrderStatus.CANCELLED
            return True
        return False


class PaymentService:
    """Manages customer billing and refund compensations."""
    def __init__(self):
        self.transactions: Dict[str, Dict[str, Any]] = {}  # order_id -> tx info

    def charge_payment(self, order_id: str, customer_id: str, amount: float) -> bool:
        tx_id = f"tx_{uuid.uuid4().hex[:6]}"
        self.transactions[order_id] = {
            "tx_id": tx_id,
            "amount": amount,
            "status": "CAPTURED",
            "refunded": False
        }
        return True

    def refund_payment_compensation(self, order_id: str) -> bool:
        """Compensating transaction: refunds previously captured funds (Idempotent)."""
        tx = self.transactions.get(order_id)
        if tx:
            tx["status"] = "REFUNDED"
            tx["refunded"] = True
            return True
        return False


class InventoryService:
    """Manages product catalog stock and reservation compensations."""
    def __init__(self, initial_stock: int = 10):
        self.available_stock: int = initial_stock
        self.reservations: Dict[str, int] = {}  # order_id -> qty reserved

    def reserve_stock(self, order_id: str, qty: int) -> bool:
        if self.available_stock >= qty:
            self.available_stock -= qty
            self.reservations[order_id] = qty
            return True
        raise ValueError(f"Insufficient stock: requested {qty}, available {self.available_stock}")

    def release_stock_compensation(self, order_id: str) -> bool:
        """Compensating transaction: returns reserved stock to available inventory."""
        qty = self.reservations.pop(order_id, 0)
        self.available_stock += qty
        return True


class ShippingService:
    """Manages dispatch and carrier bookings."""
    def __init__(self):
        self.shipments: Dict[str, Dict[str, Any]] = {}

    def schedule_delivery(self, order_id: str, address: str) -> bool:
        # Simulate business failure condition (restricted address)
        if "RESTRICTED" in address.upper():
            raise ValueError(f"Carrier delivery impossible to restricted address: '{address}'")
        self.shipments[order_id] = {"address": address, "status": "SCHEDULED"}
        return True

    def cancel_shipping_compensation(self, order_id: str) -> bool:
        """Compensating transaction: cancels courier dispatch."""
        if order_id in self.shipments:
            self.shipments[order_id]["status"] = "CANCELLED"
            return True
        return False


# =====================================================================
# 2. Orchestration-based Saga: Centralized State Machine
# =====================================================================

class SagaStepStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


@dataclass
class SagaStep:
    name: str
    action: Callable[[], bool]
    compensation: Callable[[], bool]
    status: SagaStepStatus = SagaStepStatus.PENDING


class OrderSagaOrchestrator:
    """
    Saga Execution Coordinator (SEC):
    Executes sequential workflow steps, logging forward progress and
    initiating backward compensating transactions upon any step failure.
    """
    def __init__(
        self,
        order_svc: OrderService,
        payment_svc: PaymentService,
        inventory_svc: InventoryService,
        shipping_svc: ShippingService
    ):
        self.order_svc = order_svc
        self.payment_svc = payment_svc
        self.inventory_svc = inventory_svc
        self.shipping_svc = shipping_svc
        self.saga_log: List[str] = []

    def execute_order_saga(
        self,
        order_id: str,
        customer_id: str,
        amount: float,
        qty: int,
        address: str
    ) -> Tuple[bool, str]:
        self.saga_log.clear()
        self.saga_log.append(f"START_SAGA: {order_id}")

        steps: List[SagaStep] = [
            SagaStep(
                name="CreateOrder",
                action=lambda: self.order_svc.create_order(order_id, customer_id, amount),
                compensation=lambda: self.order_svc.cancel_order_compensation(order_id)
            ),
            SagaStep(
                name="AuthorizePayment",
                action=lambda: self.payment_svc.charge_payment(order_id, customer_id, amount),
                compensation=lambda: self.payment_svc.refund_payment_compensation(order_id)
            ),
            SagaStep(
                name="ReserveInventory",
                action=lambda: self.inventory_svc.reserve_stock(order_id, qty),
                compensation=lambda: self.inventory_svc.release_stock_compensation(order_id)
            ),
            SagaStep(
                name="ScheduleShipping",
                action=lambda: self.shipping_svc.schedule_delivery(order_id, address),
                compensation=lambda: self.shipping_svc.cancel_shipping_compensation(order_id)
            )
        ]

        completed_steps: List[SagaStep] = []

        # Forward Execution Phase
        for step in steps:
            self.saga_log.append(f"EXEC_FORWARD: {step.name}")
            try:
                success = step.action()
                if success:
                    step.status = SagaStepStatus.SUCCEEDED
                    completed_steps.append(step)
                    self.saga_log.append(f"SUCCESS: {step.name}")
                else:
                    raise RuntimeError(f"Step {step.name} returned false")
            except Exception as exc:
                self.saga_log.append(f"FAILED: {step.name} ({exc})")
                step.status = SagaStepStatus.FAILED
                
                # Initiate Backward Compensation Phase
                self._execute_compensations(completed_steps)
                self.saga_log.append(f"SAGA_ABORTED_COMPENSATED: {order_id}")
                return False, f"Saga failed at step '{step.name}': {exc}"

        # If all steps succeeded, confirm the order
        self.order_svc.confirm_order(order_id)
        self.saga_log.append(f"SAGA_COMPLETED_SUCCESSFULLY: {order_id}")
        return True, "Order confirmed successfully"

    def _execute_compensations(self, completed_steps: List[SagaStep]) -> None:
        """Executes compensating transactions in REVERSE chronological order."""
        for step in reversed(completed_steps):
            self.saga_log.append(f"COMPENSATING: {step.name}")
            try:
                step.compensation()
                step.status = SagaStepStatus.COMPENSATED
                self.saga_log.append(f"COMPENSATED_SUCCESS: {step.name}")
            except Exception as c_exc:
                self.saga_log.append(f"FATAL_COMPENSATION_FAILURE: {step.name} ({c_exc})")


# =====================================================================
# 3. Choreography-based Saga: Decentralized Event Bus
# =====================================================================

class EventBus:
    """Simulated Pub/Sub Broker for Choreography."""
    def __init__(self):
        self.subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)

    def publish(self, event_type: str, payload: Dict[str, Any]):
        for handler in self.subscribers.get(event_type, []):
            handler(payload)


class ChoreographedOrderCoordinator:
    """Demonstrates event-driven choreography without a central orchestrator."""
    def __init__(
        self,
        bus: EventBus,
        order_svc: OrderService,
        payment_svc: PaymentService,
        inventory_svc: InventoryService,
        shipping_svc: ShippingService
    ):
        self.bus = bus
        self.order_svc = order_svc
        self.payment_svc = payment_svc
        self.inventory_svc = inventory_svc
        self.shipping_svc = shipping_svc
        self.event_trace: List[str] = []
        self._wire_event_handlers()

    def _wire_event_handlers(self):
        # 1. OrderCreated -> PaymentService charges
        def on_order_created(event):
            self.event_trace.append("OrderCreated")
            try:
                self.payment_svc.charge_payment(event["order_id"], event["customer_id"], event["amount"])
                self.bus.publish("PaymentCaptured", event)
            except Exception:
                self.bus.publish("PaymentFailed", event)
        self.bus.subscribe("OrderCreated", on_order_created)

        # 2. PaymentCaptured -> InventoryService reserves
        def on_payment_captured(event):
            self.event_trace.append("PaymentCaptured")
            try:
                self.inventory_svc.reserve_stock(event["order_id"], event["qty"])
                self.bus.publish("InventoryReserved", event)
            except Exception:
                self.bus.publish("InventoryFailed", event)
        self.bus.subscribe("PaymentCaptured", on_payment_captured)

        # 3. InventoryReserved -> ShippingService schedules
        def on_inventory_reserved(event):
            self.event_trace.append("InventoryReserved")
            try:
                self.shipping_svc.schedule_delivery(event["order_id"], event["address"])
                self.bus.publish("ShippingScheduled", event)
            except Exception:
                self.bus.publish("ShippingFailed", event)
        self.bus.subscribe("InventoryReserved", on_inventory_reserved)

        # 4. Compensations on ShippingFailed
        def on_shipping_failed(event):
            self.event_trace.append("ShippingFailed -> Compensation Triggered")
            self.inventory_svc.release_stock_compensation(event["order_id"])
            self.payment_svc.refund_payment_compensation(event["order_id"])
            self.order_svc.cancel_order_compensation(event["order_id"])
        self.bus.subscribe("ShippingFailed", on_shipping_failed)


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_orchestration_happy_path():
    print("\n--- 1. ORCHESTRATION SAGA: HAPPY PATH DEMO ---")
    order_svc = OrderService()
    payment_svc = PaymentService()
    inventory_svc = InventoryService(initial_stock=10)
    shipping_svc = ShippingService()

    orchestrator = OrderSagaOrchestrator(order_svc, payment_svc, inventory_svc, shipping_svc)
    success, msg = orchestrator.execute_order_saga(
        order_id="ord_100",
        customer_id="cust_1",
        amount=250.0,
        qty=2,
        address="100 Silicon Way, Sunnyvale, CA"
    )

    print(f"Result: {success} - {msg}")
    print("Saga Execution Log:")
    for entry in orchestrator.saga_log:
        print(f"  [LOG] {entry}")

    assert success is True
    assert order_svc.orders["ord_100"]["status"] == OrderStatus.CONFIRMED
    assert payment_svc.transactions["ord_100"]["status"] == "CAPTURED"
    assert inventory_svc.available_stock == 8
    assert shipping_svc.shipments["ord_100"]["status"] == "SCHEDULED"
    print("Orchestration Happy Path fully verified!")


def demo_orchestration_compensating_failure():
    print("\n--- 2. ORCHESTRATION SAGA: FAILURE & COMPENSATING TRANSACTIONS DEMO ---")
    order_svc = OrderService()
    payment_svc = PaymentService()
    inventory_svc = InventoryService(initial_stock=10)
    shipping_svc = ShippingService()

    orchestrator = OrderSagaOrchestrator(order_svc, payment_svc, inventory_svc, shipping_svc)
    # Provide RESTRICTED address to trigger failure in Step 4 (Shipping)
    success, msg = orchestrator.execute_order_saga(
        order_id="ord_200",
        customer_id="cust_2",
        amount=150.0,
        qty=3,
        address="RESTRICTED Military Base, Zone 9"
    )

    print(f"Result: {success} - {msg}")
    print("Saga Execution Log (Showing Backward Compensations):")
    for entry in orchestrator.saga_log:
        print(f"  [LOG] {entry}")

    # Assertions on distributed state consistency
    assert success is False
    # 1. Order status must be CANCELLED (not CONFIRMED or PENDING)
    assert order_svc.orders["ord_200"]["status"] == OrderStatus.CANCELLED
    # 2. Payment must be REFUNDED
    assert payment_svc.transactions["ord_200"]["status"] == "REFUNDED"
    # 3. Inventory must be RESTORED back to 10 (3 reserved then released)
    assert inventory_svc.available_stock == 10
    # 4. Shipping must NOT be scheduled
    assert "ord_200" not in shipping_svc.shipments
    print("Orchestration Backward Compensations perfectly restored system consistency!")


def demo_choreography_failure():
    print("\n--- 3. CHOREOGRAPHY SAGA: DECENTRALIZED EVENT COMPENSATIONS DEMO ---")
    bus = EventBus()
    order_svc = OrderService()
    payment_svc = PaymentService()
    inventory_svc = InventoryService(initial_stock=5)
    shipping_svc = ShippingService()

    coordinator = ChoreographedOrderCoordinator(bus, order_svc, payment_svc, inventory_svc, shipping_svc)

    order_payload = {
        "order_id": "ord_300",
        "customer_id": "cust_3",
        "amount": 90.0,
        "qty": 2,
        "address": "RESTRICTED Outpost 4"
    }

    # OrderService initiates workflow
    order_svc.create_order(order_payload["order_id"], order_payload["customer_id"], order_payload["amount"])
    bus.publish("OrderCreated", order_payload)

    print("Choreography Event Flow:")
    for ev in coordinator.event_trace:
        print(f"  -> {ev}")

    # State assertions
    assert order_svc.orders["ord_300"]["status"] == OrderStatus.CANCELLED
    assert payment_svc.transactions["ord_300"]["status"] == "REFUNDED"
    assert inventory_svc.available_stock == 5  # Restored
    print("Choreography event-driven compensations verified successfully!")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING SAGA PATTERN SIMULATOR (ORCHESTRATION & CHOREOGRAPHY)")
    print("=" * 70)
    demo_orchestration_happy_path()
    demo_orchestration_compensating_failure()
    demo_choreography_failure()
    print("\n" + "=" * 70)
    print("ALL SAGA PATTERN TESTS AND COMPENSATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
