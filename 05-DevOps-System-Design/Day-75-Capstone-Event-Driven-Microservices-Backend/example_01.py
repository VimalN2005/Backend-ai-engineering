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
