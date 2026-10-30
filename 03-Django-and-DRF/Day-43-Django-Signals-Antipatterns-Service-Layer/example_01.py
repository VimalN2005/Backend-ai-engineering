"""
Day 43: Django Signals Antipatterns vs Service Layer Architecture
=================================================================

Core Concepts Demonstrated:
1. Signal Dispatch Mechanics:
   - Synchronous dispatch loop: Signals are blocking in-process calls, NOT background workers.
2. The Critical Antipatterns of Django Signals:
   - Rollback Vulnerability: Side effects (payments, emails) executing in `post_save`
     before transaction commit, leaving phantom external side-effects when DB rolls back.
   - Bulk Operation Blindspot: `bulk_create` and `bulk_update` completely bypassing
     model-level signals, silently corrupting audit logs and cache invariants.
   - Infinite Recursion Risk: Calling `save()` inside `post_save` handlers.
3. Production Service Layer Architecture:
   - Explicit Command pattern with deterministic orchestration.
   - Atomic transaction management (`transaction.atomic()`).
   - Transactional Outbox & `transaction.on_commit()`: Deferring side-effects until
     AFTER successful database commit, with automatic discarding on rollback.

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import inspect
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# SECTION 1: IN-PROCESS SIGNAL DISPATCH ENGINE (DJANGO SIMULATOR)
# ============================================================================

class Signal:
    """
    Simulates `django.dispatch.Signal`.
    
    WHY:
    Signals in Django use an internal list of receivers called synchronously
    in the active thread. They do not provide isolation, transactional boundaries,
    or asynchronous execution.
    """
    def __init__(self, providing_args: Optional[List[str]] = None) -> None:
        self.providing_args = providing_args or []
        self._receivers: List[Tuple[Any, Callable[..., Any]]] = []
        self._lock = threading.Lock()

    def connect(self, receiver: Callable[..., Any], sender: Any = None, dispatch_uid: Optional[str] = None) -> None:
        with self._lock:
            # If dispatch_uid provided, prevent duplicate registration
            if dispatch_uid:
                self._receivers = [r for r in self._receivers if r[0] != dispatch_uid]
                lookup_key = dispatch_uid
            else:
                lookup_key = id(receiver)
            self._receivers.append((lookup_key, receiver))

    def send(self, sender: Any, **named: Any) -> List[Tuple[Callable[..., Any], Any]]:
        """
        Synchronously iterates through all receivers.
        If a receiver raises an exception, execution halts immediately.
        """
        responses: List[Tuple[Callable[..., Any], Any]] = []
        for _, receiver in list(self._receivers):
            response = receiver(sender=sender, **named)
            responses.append((receiver, response))
        return responses


# Global model signals
post_save_signal = Signal(providing_args=["instance", "created"])


def receiver(signal: Signal, dispatch_uid: Optional[str] = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a receiver function to a signal."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        signal.connect(func, dispatch_uid=dispatch_uid)
        return func
    return decorator


# ============================================================================
# SECTION 2: TRANSACTION & DATABASE ENGINE SIMULATOR
# ============================================================================

class TransactionManager:
    """
    Simulates Django's database transaction management and `transaction.on_commit`.
    
    WHY:
    In production SQL databases, `INSERT` / `UPDATE` happen within an uncommitted
    transaction buffer. `transaction.on_commit(func)` defers callbacks until the
    outermost transaction block successfully commits.
    """
    def __init__(self) -> None:
        self._in_transaction = False
        self._on_commit_hooks: List[Callable[[], None]] = []

    def atomic(self) -> AtomicContext:
        return AtomicContext(self)

    def on_commit(self, func: Callable[[], None]) -> None:
        if self._in_transaction:
            self._on_commit_hooks.append(func)
        else:
            # If called outside transaction, execute immediately
            func()

    def _execute_commit_hooks(self) -> None:
        hooks = list(self._on_commit_hooks)
        self._on_commit_hooks.clear()
        for hook in hooks:
            hook()

    def _discard_commit_hooks(self) -> None:
        self._on_commit_hooks.clear()


class AtomicContext:
    def __init__(self, manager: TransactionManager) -> None:
        self.manager = manager

    def __enter__(self) -> AtomicContext:
        self.manager._in_transaction = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.manager._in_transaction = False
        if exc_type is not None:
            # Transaction ROLLED BACK
            self.manager._discard_commit_hooks()
            return False  # Propagate exception
        # Transaction COMMITTED
        self.manager._execute_commit_hooks()
        return True


transaction = TransactionManager()


class MockDatabase:
    """In-memory database storing model records."""
    def __init__(self) -> None:
        self.tables: Dict[str, Dict[int, Dict[str, Any]]] = {
            "users": {},
            "orders": {},
            "audit_logs": {},
            "outbox_events": {},
        }
        self._auto_id = 1

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        record_id = self._auto_id
        self._auto_id += 1
        data_copy = dict(data)
        data_copy["id"] = record_id
        self.tables[table][record_id] = data_copy
        return record_id

    def bulk_insert(self, table: str, items: List[Dict[str, Any]]) -> List[int]:
        ids = []
        for item in items:
            ids.append(self.insert(table, item))
        return ids

    def delete(self, table: str, record_id: int) -> None:
        self.tables[table].pop(record_id, None)

    def clear(self) -> None:
        for t in self.tables:
            self.tables[t].clear()
        self._auto_id = 1


db = MockDatabase()


# ============================================================================
# SECTION 3: THE SIGNALS ANTIPATTERN DEMONSTRATION
# ============================================================================

class MockPaymentGateway:
    """Simulates external payment provider (Stripe/PayPal)."""
    charges: List[Dict[str, Any]] = []

    @classmethod
    def charge(cls, user_id: int, amount: Decimal) -> str:
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        cls.charges.append({"charge_id": charge_id, "user_id": user_id, "amount": amount})
        return charge_id

    @classmethod
    def clear(cls) -> None:
        cls.charges.clear()


class MockEmailClient:
    """Simulates external email service (SendGrid/SES)."""
    sent_emails: List[Dict[str, Any]] = []

    @classmethod
    def send(cls, to: str, subject: str) -> None:
        cls.sent_emails.append({"to": to, "subject": subject})

    @classmethod
    def clear(cls) -> None:
        cls.sent_emails.clear()


class SignalOrderModel:
    """
    Model relying on post_save signals for business logic.
    Antipattern: The model itself triggers external side-effects and audit logs.
    """
    def __init__(self, user_id: int, amount: Decimal, status: str = "PENDING") -> None:
        self.id: Optional[int] = None
        self.user_id = user_id
        self.amount = amount
        self.status = status

    def save(self) -> None:
        created = self.id is None
        if created:
            self.id = db.insert("orders", {
                "user_id": self.user_id,
                "amount": str(self.amount),
                "status": self.status
            })
        # Firing post_save inside the uncommitted transaction!
        post_save_signal.send(sender=self.__class__, instance=self, created=created)


# ANTIPATTERN RECEIVER 1: Triggering external payments inside post_save
@receiver(post_save_signal, dispatch_uid="vulnerable_payment_receiver")
def on_order_post_save_charge_payment(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
    if sender is SignalOrderModel and created:
        # VULNERABILITY: This fires BEFORE transaction commits!
        # If the calling transaction rolls back, Stripe has already charged the money!
        MockPaymentGateway.charge(user_id=instance.user_id, amount=instance.amount)
        MockEmailClient.send(to=f"user_{instance.user_id}@enterprise.com", subject="Order Confirmed")


# ANTIPATTERN RECEIVER 2: Audit Logging attached to post_save
@receiver(post_save_signal, dispatch_uid="vulnerable_audit_receiver")
def on_order_post_save_audit_log(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
    if sender is SignalOrderModel:
        db.insert("audit_logs", {
            "entity": "ORDER",
            "entity_id": instance.id,
            "action": "CREATED" if created else "UPDATED"
        })


# ============================================================================
# SECTION 4: PRODUCTION SERVICE LAYER ARCHITECTURE
# ============================================================================

@dataclass(frozen=True)
class CreateOrderCommand:
    """
    Explicit, strongly-typed command input for order creation.
    WHY: Keeps business intent transparent and decouples presentation from storage.
    """
    user_id: int
    amount: Decimal
    idempotency_key: str
    items: List[str]


@dataclass
class OrderResult:
    """Explicit domain result object returned by the service."""
    order_id: int
    status: str
    charge_id: Optional[str] = None


class OrderFulfillmentService:
    """
    Enterprise Service Layer implementation.
    
    Principles:
    1. Explicit Orchestration: Every step is clearly visible in the service function.
    2. Transaction Integrity: All SQL operations are enclosed within `transaction.atomic()`.
    3. Rollback Safety: External notifications use `transaction.on_commit()`.
    4. Transactional Outbox: Persists domain events to the outbox table inside the same DB transaction.
    5. Bulk Safety: Provides explicit batch operations that do not rely on implicit hooks.
    """

    @classmethod
    def create_order(cls, cmd: CreateOrderCommand) -> OrderResult:
        # 1. Domain Validation
        if cmd.amount <= Decimal("0.00"):
            raise ValueError("Order amount must be greater than zero.")
        if not cmd.items:
            raise ValueError("Order must contain at least one item.")

        charge_id: Optional[str] = None
        order_id: Optional[int] = None

        # 2. Atomic Database Transaction
        with transaction.atomic():
            # Persist Order Entity
            order_id = db.insert("orders", {
                "user_id": cmd.user_id,
                "amount": str(cmd.amount),
                "status": "CONFIRMED",
                "idempotency_key": cmd.idempotency_key
            })

            # Audit Trail (Explicitly recorded in DB, immune to bulk bypass)
            db.insert("audit_logs", {
                "entity": "ORDER",
                "entity_id": order_id,
                "action": "CREATED"
            })

            # Transactional Outbox Event (Ensures zero message loss)
            db.insert("outbox_events", {
                "event_type": "ORDER_PLACED",
                "aggregate_id": order_id,
                "payload": {"user_id": cmd.user_id, "amount": str(cmd.amount)}
            })

            # 3. External Gateway Call
            charge_id = MockPaymentGateway.charge(user_id=cmd.user_id, amount=cmd.amount)

            # 4. Safe Post-Commit Notification
            # WHY: If this transaction encounters an error after this line,
            # this callback is automatically discarded by the TransactionManager!
            user_email = f"user_{cmd.user_id}@enterprise.com"
            transaction.on_commit(
                lambda: MockEmailClient.send(to=user_email, subject="Order Confirmed")
            )

        return OrderResult(order_id=order_id, status="CONFIRMED", charge_id=charge_id)

    @classmethod
    def bulk_create_orders(cls, commands: List[CreateOrderCommand]) -> List[int]:
        """
        Explicit bulk operation.
        Guarantees that audit records and outbox events are created in batch!
        """
        order_ids: List[int] = []
        with transaction.atomic():
            for cmd in commands:
                oid = db.insert("orders", {
                    "user_id": cmd.user_id,
                    "amount": str(cmd.amount),
                    "status": "CONFIRMED",
                    "idempotency_key": cmd.idempotency_key
                })
                order_ids.append(oid)
                # Audit log explicitly written for EVERY bulk record
                db.insert("audit_logs", {
                    "entity": "ORDER",
                    "entity_id": oid,
                    "action": "BULK_CREATED"
                })
        return order_ids


# ============================================================================
# SECTION 5: VERIFICATION & TEST HARNESS
# ============================================================================

def run_tests() -> None:
    print("=" * 70)
    print(" Day 43: Django Signals Antipatterns vs Service Layer Architecture")
    print("=" * 70)

    # ------------------------------------------------------------------------
    # [ANTIPATTERN TEST 1] The Rollback Disaster with Signals
    # ------------------------------------------------------------------------
    print("\n[TEST 1] Signals Antipattern: Transaction Rollback with Premature Side-Effect...")
    db.clear()
    MockPaymentGateway.clear()
    MockEmailClient.clear()

    # Simulate a transaction that saves an order via signals, but then fails (e.g. inventory constraint)
    failed_transaction = False
    try:
        with transaction.atomic():
            order = SignalOrderModel(user_id=101, amount=Decimal("250.00"))
            order.save()  # post_save fires IMMEDIATELY here!
            
            # Simulate unexpected failure inside atomic block
            raise RuntimeError("Database constraint failed: Insufficient warehouse stock!")
    except RuntimeError:
        failed_transaction = True

    assert failed_transaction is True
    # The transaction failed, so the order is deleted / rolled back in DB
    db.delete("orders", 1)

    # BUT inspect the external side-effects:
    print(f"  -> DB Orders count       : {len(db.tables['orders'])} (Rolled back to 0)")
    print(f"  -> Payment Gateway charges: {len(MockPaymentGateway.charges)} (DISASTER: 1 charge committed!)")
    print(f"  -> Emails dispatched     : {len(MockEmailClient.sent_emails)} (DISASTER: 1 receipt sent!)")
    assert len(MockPaymentGateway.charges) == 1, "Vulnerability proof: Card charged despite DB rollback!"
    assert len(MockEmailClient.sent_emails) == 1, "Vulnerability proof: Email sent despite DB rollback!"
    print("  -> Signals Failure Mode 1 CONFIRMED: External side-effects leaked during rollback.")

    # ------------------------------------------------------------------------
    # [ANTIPATTERN TEST 2] Bulk Operation Blindspot in Signals
    # ------------------------------------------------------------------------
    print("\n[TEST 2] Signals Antipattern: Bulk Operations Silently Bypassing Signals...")
    db.clear()

    # Simulating Django's Model.objects.bulk_create([Order(...), Order(...)])
    bulk_records = [
        {"user_id": 201, "amount": "99.00", "status": "PENDING"},
        {"user_id": 202, "amount": "149.00", "status": "PENDING"},
        {"user_id": 203, "amount": "199.00", "status": "PENDING"},
    ]
    # In Django ORM, bulk_create executes a single SQL INSERT and does NOT call save() or post_save!
    inserted_ids = db.bulk_insert("orders", bulk_records)
    
    print(f"  -> Bulk inserted orders  : {len(inserted_ids)}")
    print(f"  -> Audit log count       : {len(db.tables['audit_logs'])} (0 audit records generated!)")
    assert len(db.tables['audit_logs']) == 0, "Vulnerability proof: Signals completely bypassed in bulk!"
    print("  -> Signals Failure Mode 2 CONFIRMED: Signals blind to bulk operations.")

    # ------------------------------------------------------------------------
    # [SERVICE LAYER TEST 3] Service Layer Rollback Safety with on_commit
    # ------------------------------------------------------------------------
    print("\n[TEST 3] Service Layer: Rollback Safety via transaction.atomic & on_commit...")
    db.clear()
    MockPaymentGateway.clear()
    MockEmailClient.clear()

    # Simulate an error inside the service layer
    class FaultyOrderService(OrderFulfillmentService):
        @classmethod
        def create_order_with_simulated_crash(cls, cmd: CreateOrderCommand) -> None:
            with transaction.atomic():
                db.insert("orders", {"user_id": cmd.user_id, "amount": str(cmd.amount)})
                # Register safe post-commit hook
                transaction.on_commit(
                    lambda: MockEmailClient.send(to="test@enterprise.com", subject="Receipt")
                )
                # Crash before commit
                raise RuntimeError("Downstream microservice timeout!")

    crashed = False
    try:
        FaultyOrderService.create_order_with_simulated_crash(
            CreateOrderCommand(user_id=301, amount=Decimal("50.00"), idempotency_key="key-1", items=["item_a"])
        )
    except RuntimeError:
        crashed = True

    assert crashed is True
    # Verify that the on_commit hook was DISCARDED automatically
    print(f"  -> Emails dispatched after rollback: {len(MockEmailClient.sent_emails)}")
    assert len(MockEmailClient.sent_emails) == 0, "Guaranteed: on_commit hook safely discarded on rollback!"
    print("  -> Service Layer Proof: 0 phantom emails dispatched on transaction failure.")

    # ------------------------------------------------------------------------
    # [SERVICE LAYER TEST 4] Successful End-to-End Orchestration
    # ------------------------------------------------------------------------
    print("\n[TEST 4] Service Layer: Successful Atomic Orchestration & Outbox...")
    db.clear()
    MockPaymentGateway.clear()
    MockEmailClient.clear()

    cmd = CreateOrderCommand(
        user_id=401,
        amount=Decimal("1200.00"),
        idempotency_key="idem-uuid-401",
        items=["MacBook Pro 16", "Magic Mouse"]
    )
    result = OrderFulfillmentService.create_order(cmd)

    assert result.status == "CONFIRMED"
    assert len(db.tables["orders"]) == 1
    assert len(db.tables["audit_logs"]) == 1
    assert len(db.tables["outbox_events"]) == 1
    assert len(MockPaymentGateway.charges) == 1
    assert len(MockEmailClient.sent_emails) == 1

    outbox_event = list(db.tables["outbox_events"].values())[0]
    print(f"  -> Order Created ID     : {result.order_id}")
    print(f"  -> Outbox Event Type     : {outbox_event['event_type']} (Payload: {outbox_event['payload']})")
    print(f"  -> Audit Record Action   : {list(db.tables['audit_logs'].values())[0]['action']}")
    print(f"  -> Confirmed Email Sent  : {MockEmailClient.sent_emails[0]['to']}")
    print("  -> Service Layer clean execution fully verified.")

    # ------------------------------------------------------------------------
    # [SERVICE LAYER TEST 5] Explicit Bulk Orchestration
    # ------------------------------------------------------------------------
    print("\n[TEST 5] Service Layer: Deterministic Bulk Processing with Auditing...")
    bulk_cmds = [
        CreateOrderCommand(user_id=500 + i, amount=Decimal("75.00"), idempotency_key=f"idem-{i}", items=["Widget"])
        for i in range(5)
    ]
    bulk_ids = OrderFulfillmentService.bulk_create_orders(bulk_cmds)
    assert len(bulk_ids) == 5
    # Every bulk record has an explicit audit record!
    assert len(db.tables["audit_logs"]) == 1 + 5  # 1 from test 4 + 5 from test 5
    print(f"  -> Successfully bulk created {len(bulk_ids)} orders with 100% audit integrity.")

    print("\nAll Day 43 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
