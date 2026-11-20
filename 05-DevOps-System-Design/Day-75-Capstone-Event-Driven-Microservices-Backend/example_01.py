"""
Day 75: Capstone Project 3: Enterprise Event-Driven Microservices Backend
========================================================================
Architecture & Principles Demonstrated:
1. Domain-Driven Design (DDD) & Isolated Database-per-Service:
   - OrderService (orders_db, outbox_events)
   - PaymentService (payments_db, outbox_events)
   - InventoryService (inventory_db, outbox_events)
   - NotificationService (notifications_db)
"""

from __future__ import annotations
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


@dataclass
class OrderRecord:
    order_id: str
    customer_id: str
    amount: float
    items: List[Dict[str, Any]]
    status: OrderStatus
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class OutboxEvent:
    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: Dict[str, Any]
    status: str = "PENDING"  # PENDING, PUBLISHED
    created_at: float = field(default_factory=time.time)
    published_at: Optional[float] = None


# =====================================================================
# 2. Transactional Outbox Pattern & CDC Engine
# =====================================================================

class KafkaEventBus:
    """Simulated Apache Kafka partitioned broker."""
    def __init__(self):
        self.topics: Dict[str, List[Dict[str, Any]]] = {}

    def publish(self, topic: str, key: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> int:
        if topic not in self.topics:
            self.topics[topic] = []
        record = {
            "key": key,
            "payload": payload,
            "headers": headers or {},
            "offset": len(self.topics[topic]),
            "timestamp": time.time()
        }
        self.topics[topic].append(record)
        return record["offset"]


class OrderDatabase:
    """Simulates PostgreSQL orders_db with ACID outbox transaction."""
    def __init__(self):
        self.orders: Dict[str, OrderRecord] = {}
        self.outbox: List[OutboxEvent] = []

    def save_order_with_outbox(self, order: OrderRecord, event_type: str, payload: Dict[str, Any]) -> OutboxEvent:
        """Atomic Local ACID Transaction: saves order entity and outbox event."""
        self.orders[order.order_id] = order
        event = OutboxEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            aggregate_type="Order",
            aggregate_id=order.order_id,
            event_type=event_type,
            payload=payload
        )
        self.outbox.append(event)
        return event


class DebeziumCDCRelay:
    """Change Data Capture (CDC) worker polling outbox tables and publishing to Kafka."""
    def __init__(self, db: OrderDatabase, bus: KafkaEventBus):
        self.db = db
        self.bus = bus

    def poll_and_relay(self, batch_size: int = 10) -> int:
        pending = [e for e in self.db.outbox if e.status == "PENDING"][:batch_size]
        relayed = 0
        for event in pending:
            self.bus.publish(
                topic="orders.events",
                key=event.aggregate_id,
                payload=event.payload,
                headers={"event_id": event.event_id, "event_type": event.event_type}
            )
            event.status = "PUBLISHED"
            event.published_at = time.time()
            relayed += 1
        return relayed


# =====================================================================
# 3. Saga Orchestrator & Microservices Integration
# =====================================================================

class PaymentService:
    """Manages payment authorization and refunds."""
    def __init__(self):
        self.transactions: Dict[str, Dict[str, Any]] = {}

    def authorize_and_capture(self, order_id: str, amount: float, fail_simulation: bool = False) -> bool:
        if fail_simulation:
            raise ValueError(f"Credit card declined for amount: ${amount:.2f}")
        self.transactions[order_id] = {
            "order_id": order_id,
            "amount": amount,
            "status": PaymentStatus.CAPTURED,
            "captured_at": time.time()
        }
        return True

    def refund_compensation(self, order_id: str) -> bool:
        tx = self.transactions.get(order_id)
        if tx and tx["status"] == PaymentStatus.CAPTURED:
            tx["status"] = PaymentStatus.REFUNDED
            tx["refunded_at"] = time.time()
            return True
        return False


class InventoryService:
    """Manages warehouse catalog stock reservations and releases."""
    def __init__(self, initial_stock: Dict[str, int]):
        self.stock: Dict[str, int] = dict(initial_stock)
        self.reservations: Dict[str, Dict[str, int]] = {}  # order_id -> {sku: qty}

    def reserve_items(self, order_id: str, items: List[Dict[str, Any]]) -> bool:
        # Check all items have sufficient stock
        for item in items:
            sku = item["sku"]
            qty = item["qty"]
            if self.stock.get(sku, 0) < qty:
                raise ValueError(f"Insufficient stock for SKU {sku}: available {self.stock.get(sku, 0)}, needed {qty}")

        # Deduct available and record reservation
        self.reservations[order_id] = {}
        for item in items:
            sku = item["sku"]
            qty = item["qty"]
            self.stock[sku] -= qty
            self.reservations[order_id][sku] = qty
        return True

    def release_compensation(self, order_id: str) -> bool:
        res = self.reservations.pop(order_id, None)
        if res:
            for sku, qty in res.items():
                self.stock[sku] += qty
            return True
        return False


class SagaExecutionCoordinator:
    """Centralized Saga Orchestrator executing forward steps and reverse compensations."""
    def __init__(
        self,
        order_db: OrderDatabase,
        payment_svc: PaymentService,
        inventory_svc: InventoryService,
        bus: KafkaEventBus
    ):
        self.order_db = order_db
        self.payment_svc = payment_svc
        self.inventory_svc = inventory_svc
        self.bus = bus
        self.saga_log: List[str] = []

    def execute_checkout_saga(
        self,
        order_id: str,
        customer_id: str,
        amount: float,
        items: List[Dict[str, Any]],
        simulate_payment_fail: bool = False,
        simulate_inventory_fail: bool = False
    ) -> Tuple[bool, str]:
        self.saga_log.clear()
        self.saga_log.append(f"START_SAGA: {order_id}")

        # Step 1: Create Order in PENDING status (with Outbox Event)
        order = OrderRecord(
            order_id=order_id,
            customer_id=customer_id,
            amount=amount,
            items=items,
            status=OrderStatus.PENDING
        )
        self.order_db.save_order_with_outbox(order, "OrderCreated", {"order_id": order_id, "amount": amount})
        self.saga_log.append("STEP_1_ORDER_PENDING")

        # Step 2: Authorize & Capture Payment (Compensable)
        try:
            self.payment_svc.authorize_and_capture(order_id, amount, fail_simulation=simulate_payment_fail)
            self.saga_log.append("STEP_2_PAYMENT_CAPTURED")
        except Exception as exc:
            self.saga_log.append(f"STEP_2_FAILED: {exc}")
            # Cancel order
            order.status = OrderStatus.CANCELLED
            self.saga_log.append("COMPENSATION_ORDER_CANCELLED")
            return False, f"Checkout failed at Payment: {exc}"

        # Step 3: Reserve Inventory (Compensable)
        try:
            if simulate_inventory_fail:
                raise ValueError("Simulated warehouse outage on stock reservation")
            self.inventory_svc.reserve_items(order_id, items)
            self.saga_log.append("STEP_3_INVENTORY_RESERVED")
        except Exception as exc:
            self.saga_log.append(f"STEP_3_FAILED: {exc}")
            # Initiate Backward Compensations in reverse order
            self.payment_svc.refund_compensation(order_id)
            self.saga_log.append("COMPENSATION_PAYMENT_REFUNDED")
            order.status = OrderStatus.CANCELLED
            self.saga_log.append("COMPENSATION_ORDER_CANCELLED")
            return False, f"Checkout failed at Inventory: {exc}"

        # Step 4: Final Confirmation (Pivot commit)
        order.status = OrderStatus.CONFIRMED
        self.saga_log.append("STEP_4_ORDER_CONFIRMED")
        self.bus.publish("orders.events", key=order_id, payload={"order_id": order_id, "status": "CONFIRMED"})
        return True, "Order confirmed successfully"
