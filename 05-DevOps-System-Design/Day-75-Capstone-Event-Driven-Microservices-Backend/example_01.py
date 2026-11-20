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
