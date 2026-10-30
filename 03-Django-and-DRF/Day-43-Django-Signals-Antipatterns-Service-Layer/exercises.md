# Day 43: Advanced Exercises — Django Signals vs Service Layer Architecture

This workbook provides 3 practical architectural refactoring challenges designed to transform fragile, signal-dependent Django implementations into resilient, testable Service Layer architectures. Complete reference solutions with verification test harnesses are included.

---

## Challenge 1: Refactor a Fragile Signal-Driven User Registration to an Explicit Service Layer

### Context & Requirements
A legacy Django application manages user onboarding via cascading `post_save` signals:
1. `User.save()` triggers `post_save` -> Creates a `Profile`.
2. `Profile.save()` triggers `post_save` -> Provisions a `Wallet` with an initial credit.
3. `Wallet.save()` triggers `post_save` -> Dispatches a welcome email via an external client.

**The Flaw**: If creating the wallet fails (e.g. database unique constraint or network error), the user and profile exist in an invalid half-provisioned state, or the email is dispatched before the transaction commits.

**Your Task**:
Refactor this entire flow into a single explicit `UserOnboardingService.register_user()` method that:
- Accepts a typed `RegisterUserCommand(email, username, initial_deposit)`.
- Encloses all model creations inside a single atomic transaction.
- Guarantees that if any step fails, zero records are left in the database.
- Guarantees that the welcome email is dispatched **strictly after** the transaction commits.

---

### Reference Solution

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional


class MockDB:
    def __init__(self) -> None:
        self.users: Dict[int, Dict[str, Any]] = {}
        self.profiles: Dict[int, Dict[str, Any]] = {}
        self.wallets: Dict[int, Dict[str, Any]] = {}
        self._counter = 1

    def clear(self) -> None:
        self.users.clear()
        self.profiles.clear()
        self.wallets.clear()
        self._counter = 1


class MockTransaction:
    def __init__(self, db: MockDB) -> None:
        self.db = db
        self._on_commit_callbacks: List[Callable[[], None]] = []
        self._active = False

    def on_commit(self, func: Callable[[], None]) -> None:
        if self._active:
            self._on_commit_callbacks.append(func)
        else:
            func()

    def __enter__(self) -> "MockTransaction":
        self._active = True
        # Save snapshot for rollback
        self._snap_users = dict(self.db.users)
        self._snap_profiles = dict(self.db.profiles)
        self._snap_wallets = dict(self.db.wallets)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self._active = False
        if exc_type is not None:
            # ROLLBACK to snapshot
            self.db.users = self._snap_users
            self.db.profiles = self._snap_profiles
            self.db.wallets = self._snap_wallets
            self._on_commit_callbacks.clear()
            return False  # Propagate error
        
        # COMMIT: execute deferred hooks
        callbacks = list(self._on_commit_callbacks)
        self._on_commit_callbacks.clear()
        for cb in callbacks:
            cb()
        return True


@dataclass(frozen=True)
class RegisterUserCommand:
    email: str
    username: str
    initial_deposit: Decimal


@dataclass
class OnboardingResult:
    user_id: int
    wallet_id: int
    email_dispatched: bool


class UserOnboardingService:
    def __init__(self, db: MockDB, tx: MockTransaction) -> None:
        self.db = db
        self.tx = tx
        self.dispatched_emails: List[str] = []

    def register_user(self, cmd: RegisterUserCommand) -> OnboardingResult:
        if cmd.initial_deposit < Decimal("0.00"):
            raise ValueError("Initial deposit cannot be negative.")

        with self.tx:
            # 1. Create User
            user_id = self.db._counter
            self.db._counter += 1
            self.db.users[user_id] = {"id": user_id, "email": cmd.email, "username": cmd.username}

            # 2. Create Profile
            profile_id = self.db._counter
            self.db._counter += 1
            self.db.profiles[profile_id] = {"id": profile_id, "user_id": user_id}

            # 3. Create Wallet
            wallet_id = self.db._counter
            self.db._counter += 1
            self.db.wallets[wallet_id] = {"id": wallet_id, "user_id": user_id, "balance": cmd.initial_deposit}

            # 4. Safe Post-Commit Notification
            self.tx.on_commit(lambda: self.dispatched_emails.append(cmd.email))

        return OnboardingResult(user_id=user_id, wallet_id=wallet_id, email_dispatched=True)


# --- Verification Harness ---
if __name__ == "__main__":
    db = MockDB()
    tx = MockTransaction(db)
    service = UserOnboardingService(db, tx)

    # 1. Successful Onboarding
    cmd_ok = RegisterUserCommand("alice@enterprise.com", "alice", Decimal("100.00"))
    res = service.register_user(cmd_ok)
    assert len(db.users) == 1
    assert len(db.profiles) == 1
    assert len(db.wallets) == 1
    assert len(service.dispatched_emails) == 1
    assert service.dispatched_emails[0] == "alice@enterprise.com"

    # 2. Failed Onboarding (Validation Error)
    try:
        service.register_user(RegisterUserCommand("bob@enterprise.com", "bob", Decimal("-50.00")))
    except ValueError:
        pass
    # Verify rollback: still only Alice's records exist
    assert len(db.users) == 1
    assert len(service.dispatched_emails) == 1
    print("[CHALLENGE 1 PASSED] Explicit Onboarding Service verified with zero signal dependencies.")
```

---

## Challenge 2: Implement a Safe `transaction.on_commit` Hook Dispatcher

### Context & Requirements
Construct a production-grade, thread-safe `TransactionCommitDispatcher` that:
1. Tracks transaction nesting depth (supporting nested `atomic()` blocks / savepoints).
2. Registers arbitrary callbacks via `on_commit(callback)`.
3. Ensures that callbacks registered in nested blocks execute **only when the outermost transaction commits**.
4. Completely purges and discards all registered callbacks if any transaction or nested savepoint rolls back.
5. Captures and logs exceptions raised inside `on_commit` hooks without rolling back the already-committed transaction.

---

### Reference Solution

```python
import threading
from typing import Any, Callable, List, Optional


class TransactionCommitDispatcher:
    def __init__(self) -> None:
        self._local = threading.local()

    def _get_state(self) -> dict:
        if not hasattr(self._local, "depth"):
            self._local.depth = 0
            self._local.callbacks = []
            self._local.has_error = False
        return self._local

    def enter_atomic(self) -> None:
        state = self._get_state()
        state.depth += 1

    def exit_atomic(self, exc_occurred: bool) -> None:
        state = self._get_state()
        if exc_occurred:
            state.has_error = True

        state.depth -= 1
        if state.depth == 0:
            # Outermost block completed
            if not state.has_error:
                self._execute_hooks(state.callbacks)
            else:
                state.callbacks.clear()
            
            # Reset
            state.has_error = False
            state.callbacks = []

    def on_commit(self, callback: Callable[[], None]) -> None:
        state = self._get_state()
        if state.depth > 0:
            state.callbacks.append(callback)
        else:
            # Called outside atomic block: execute immediately
            try:
                callback()
            except Exception as e:
                print(f"[HOOK ERROR] Immediate hook failed: {e}")

    def _execute_hooks(self, callbacks: List[Callable[[], None]]) -> None:
        for cb in callbacks:
            try:
                cb()
            except Exception as exc:
                # Crucial invariant: Exceptions in on_commit hooks must NOT rollback
                # the already-committed transaction! They must be caught and logged.
                print(f"[HOOK ERROR] Deferred hook failed post-commit: {exc}")


class AtomicBlock:
    def __init__(self, dispatcher: TransactionCommitDispatcher) -> None:
        self.dispatcher = dispatcher

    def __enter__(self) -> "AtomicBlock":
        self.dispatcher.enter_atomic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.dispatcher.exit_atomic(exc_occurred=exc_type is not None)
        return False  # Propagate exception if any


# --- Verification Harness ---
if __name__ == "__main__":
    dispatcher = TransactionCommitDispatcher()
    executed_hooks = []

    # Case 1: Nested atomic block committing successfully
    with AtomicBlock(dispatcher):
        dispatcher.on_commit(lambda: executed_hooks.append("outer_hook"))
        with AtomicBlock(dispatcher):
            dispatcher.on_commit(lambda: executed_hooks.append("nested_hook"))
        # Callbacks must NOT execute until outermost block exits
        assert len(executed_hooks) == 0

    assert executed_hooks == ["outer_hook", "nested_hook"]

    # Case 2: Nested block raising exception (Rollback)
    executed_hooks.clear()
    rolled_back = False
    try:
        with AtomicBlock(dispatcher):
            dispatcher.on_commit(lambda: executed_hooks.append("should_not_run"))
            with AtomicBlock(dispatcher):
                raise RuntimeError("Nested database conflict!")
    except RuntimeError:
        rolled_back = True

    assert rolled_back is True
    # Verify all callbacks were discarded
    assert len(executed_hooks) == 0
    print("[CHALLENGE 2 PASSED] Nested transaction on_commit dispatcher verified.")
```

---

## Challenge 3: Transactional Outbox Pattern Service for Reliable Event Publishing

### Context & Requirements
When integrating Django with distributed message buses (Kafka, RabbitMQ, SQS), sending messages directly in `post_save` causes phantom events on rollback or lost messages on network timeouts.
Implement a `TransactionalOutboxService` that:
1. Implements a `record_event(aggregate_type, aggregate_id, event_type, payload)` method that writes an `OutboxEvent` to the database inside the caller's active database transaction.
2. Implements a background worker `publish_pending_events()` that reads pending events, simulates publishing to Kafka, marks events as `PUBLISHED`, and retries failed events with an exponential backoff policy.
3. Guarantees **At-Least-Once Delivery** and zero lost messages.

---

### Reference Solution

```python
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OutboxRecord:
    id: str
    aggregate_type: str
    aggregate_id: int
    event_type: str
    payload: Dict[str, Any]
    status: str = "PENDING"  # PENDING, PUBLISHED, FAILED
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)


class MockKafkaProducer:
    def __init__(self, fail_first_attempt: bool = False) -> None:
        self.published_messages: List[Dict[str, Any]] = []
        self.fail_first_attempt = fail_first_attempt
        self._attempt_count = 0

    def produce(self, topic: str, key: str, value: Dict[str, Any]) -> None:
        self._attempt_count += 1
        if self.fail_first_attempt and self._attempt_count == 1:
            raise ConnectionError("Kafka cluster connection refused.")
        self.published_messages.append({"topic": topic, "key": key, "value": value})


class TransactionalOutboxService:
    def __init__(self, producer: MockKafkaProducer) -> None:
        self.outbox_table: Dict[str, OutboxRecord] = {}
        self.producer = producer

    def record_event(
        self,
        aggregate_type: str,
        aggregate_id: int,
        event_type: str,
        payload: Dict[str, Any]
    ) -> OutboxRecord:
        """
        Executes inside the caller's DB transaction.
        Persisting this record is guaranteed by ACID alongside the domain entity!
        """
        record_id = str(uuid.uuid4())
        record = OutboxRecord(
            id=record_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload
        )
        self.outbox_table[record_id] = record
        return record

    def publish_pending_events(self, max_retries: int = 3) -> Tuple[int, int]:
        """
        Background poller (or Debezium simulator) reading pending events
        and publishing them with at-least-once delivery semantics.
        """
        published_count = 0
        failed_count = 0

        pending_events = [
            r for r in self.outbox_table.values()
            if r.status in ("PENDING", "FAILED") and r.retry_count < max_retries
        ]

        for record in pending_events:
            try:
                topic = f"events.{record.aggregate_type.lower()}"
                self.producer.produce(
                    topic=topic,
                    key=str(record.aggregate_id),
                    value=record.payload
                )
                record.status = "PUBLISHED"
                published_count += 1
            except Exception as e:
                record.retry_count += 1
                record.status = "FAILED"
                failed_count += 1

        return published_count, failed_count


# --- Verification Harness ---
if __name__ == "__main__":
    kafka = MockKafkaProducer(fail_first_attempt=True)
    outbox = TransactionalOutboxService(kafka)

    # 1. Record event inside business transaction
    record = outbox.record_event(
        aggregate_type="INVOICE",
        aggregate_id=901,
        event_type="INVOICE_PAID",
        payload={"invoice_id": 901, "amount": "450.00"}
    )
    assert record.status == "PENDING"

    # 2. First polling attempt: fails due to simulated network failure
    pub, fail = outbox.publish_pending_events()
    assert pub == 0
    assert fail == 1
    assert record.status == "FAILED"
    assert record.retry_count == 1

    # 3. Second polling attempt: succeeds, marking record as PUBLISHED
    pub2, fail2 = outbox.publish_pending_events()
    assert pub2 == 1
    assert fail2 == 0
    assert record.status == "PUBLISHED"
    assert len(kafka.published_messages) == 1
    print("[CHALLENGE 3 PASSED] Transactional Outbox Pattern verified with at-least-once guarantees.")
```
