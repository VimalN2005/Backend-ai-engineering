"""
Day 59: Production Celery Application Pipeline
================================================================================
Target: Enterprise Python Backend & AI Systems Engineer Portfolio
Description: Demonstrates production-grade Celery 5.x architecture:
  - Robust Celery application configuration with JSON security hardening.
  - Custom IdempotentTask base class utilizing Redis mutex locks to guarantee
    strictly-once semantics despite at-least-once message delivery.
  - Exponential backoff retry handler with randomized jitter and Dead-Letter Queue
    (DLQ) fallbacks for transient third-party API dependencies.
  - Celery Canvas workflow pipelines:
      * Sequential pipeline (chain): Order Processing -> Invoice -> Notification
      * Parallel fan-out (group): Concurrent image/thumbnail processing
      * Reduction barrier (chord): Multi-account ledger reconciliation
  - Transaction-safe task dispatching boundary (post-commit simulation).
  - Built-in automated integration test harness.
================================================================================
"""

import hashlib
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional
from celery import Celery, Task, chain, chord, group
from celery.exceptions import MaxRetriesExceededError
import fakeredis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("celery_production")

# -----------------------------------------------------------------------------
# 1. Celery Application Configuration (Production Baseline)
# -----------------------------------------------------------------------------
app = Celery("enterprise_tasks", broker="memory://", backend="cache+memory://")

app.conf.update(
    # Strict Serialization Hardening: Prevent arbitrary code execution (RCE) via pickle
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,

    # Execution & Acknowledgment Durability
    task_acks_late=True,                   # Acknowledge only after task completes
    task_reject_on_worker_lost=True,       # Re-queue task if worker process dies
    worker_prefetch_multiplier=1,          # Prevent prefetch queue starvation
    task_track_started=True,               # Emit STARTED event for real-time monitoring
    result_expires=3600,                   # Expire results after 1 hour

    # Hard & Soft Time Limits
    task_time_limit=300,                   # Hard SIGKILL after 5 minutes
    task_soft_time_limit=240,              # Raise SoftTimeLimitExceeded after 4 mins

    # Task Routing Definition
    task_routes={
        "enterprise_tasks.high_priority.*": {"queue": "high_priority"},
        "enterprise_tasks.default.*": {"queue": "default"},
        "enterprise_tasks.bulk_reports.*": {"queue": "bulk_reports"},
    },

    # Self-Contained Eager Execution for In-Process Testing & Diagnostics
    task_always_eager=True,
    task_eager_propagates=False,
)

# Shared Mock Redis instance for Distributed Locks, Deduplication & DLQ
redis_client = fakeredis.FakeRedis(decode_responses=True)

# -----------------------------------------------------------------------------
# 2. Custom Idempotent Task Base Class
# -----------------------------------------------------------------------------
class IdempotentTask(Task):
    """
    Abstract Task base class enforcing exactly-once execution semantics across
    distributed worker nodes. Uses Redis atomic keys to prevent duplicate
    processing under Celery's at-least-once message delivery model.
    """
    abstract = True

    def generate_idempotency_key(self, args: tuple, kwargs: dict) -> str:
        """Derive a deterministic SHA-256 digest from task name and business parameters."""
        raw_payload = json.dumps({"name": self.name, "args": args, "kwargs": kwargs}, sort_keys=True)
        digest = hashlib.sha256(raw_payload.encode()).hexdigest()
        return f"celery:idempotency:{self.name}:{digest}"

    def __call__(self, *args, **kwargs) -> Any:
        # Check if caller opted out of deduplication
        bypass = kwargs.pop("_bypass_idempotency", False)
        if bypass:
            return super().__call__(*args, **kwargs)

        key = self.generate_idempotency_key(args, kwargs)
        
        # Step 1: Attempt atomic lock acquisition with 30-minute lock TTL
        lock_acquired = redis_client.set(key, "PROCESSING", nx=True, ex=1800)
        
        if not lock_acquired:
            current_status = redis_client.get(key)
            if current_status == "COMPLETED":
                logger.warning(f"[IDEMPOTENCY] Duplicate task invocation suppressed for key: {key}")
                cached_result = redis_client.get(f"{key}:result")
                return {
                    "status": "DUPLICATE_SKIPPED",
                    "key": key,
                    "result": json.loads(cached_result) if cached_result else None
                }
            elif current_status == "PROCESSING":
                logger.warning(f"[IDEMPOTENCY] Concurrent execution rejected for key: {key}")
                raise RuntimeError(f"Concurrent task execution detected for key {key}")

        # Step 2: Execute task logic
        try:
            result = super().__call__(*args, **kwargs)
            # Step 3: Record completion state and result cache with 24-hour retention
            redis_client.set(key, "COMPLETED", ex=86400)
            redis_client.set(f"{key}:result", json.dumps(result), ex=86400)
            return result
        except Exception as exc:
            # On task failure, clear the lock so subsequent retries are permitted
            redis_client.delete(key)
            logger.error(f"[IDEMPOTENCY] Task {self.name} failed with {exc}; unlocked key {key}")
            raise

# -----------------------------------------------------------------------------
# 3. Tasks: Idempotent Payment & Exponential Backoff Delivery
# -----------------------------------------------------------------------------
@app.task(
    base=IdempotentTask,
    bind=True,
    name="enterprise_tasks.default.process_payment"
)
def process_payment(self, transaction_id: str, amount_cents: int, currency: str = "USD", **kwargs) -> Dict[str, Any]:
    """
    Idempotent payment processing task. Multiple dispatches with identical
    transaction_id and amount will be executed exactly once.
    """
    logger.info(f"[PAYMENT] Processing transaction {transaction_id} for {amount_cents / 100:.2f} {currency}")
    return {
        "status": "SETTLED",
        "transaction_id": transaction_id,
        "amount_cents": amount_cents,
        "currency": currency,
        "processed_at": time.time()
    }


@app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=1,
    name="enterprise_tasks.high_priority.deliver_webhook"
)
def deliver_webhook(self, webhook_url: str, event_type: str, payload: dict, fail_count: int = 0) -> Dict[str, Any]:
    """
    Simulates external webhook delivery with exponential backoff and randomized jitter.
    If failures exceed max_retries, routes to the Dead Letter Queue (DLQ).
    """
    attempt = self.request.retries
    logger.info(f"[WEBHOOK] Attempt {attempt + 1}/{self.max_retries + 1} sending '{event_type}' to {webhook_url}")

    if attempt < fail_count:
        if attempt >= self.max_retries:
            # Route poison pill message to Dead Letter Queue (DLQ)
            dlq_record = {
                "task_id": self.request.id,
                "webhook_url": webhook_url,
                "event_type": event_type,
                "payload": payload,
                "failed_at": time.time(),
                "error": "Max retries exceeded"
            }
            redis_client.lpush("celery:dlq:webhooks", json.dumps(dlq_record))
            logger.critical(f"[DLQ] Webhook to {webhook_url} moved to DLQ 'celery:dlq:webhooks'")
            return {"status": "DLQ_ROUTED", "webhook_url": webhook_url, "attempt": attempt + 1}

        # Calculate exponential backoff with jitter: delay = base * 2^attempt + jitter
        base_delay = 0.05 * (2 ** attempt)
        jitter = random.uniform(0.01, 0.05)
        calculated_delay = base_delay + jitter
        
        logger.warning(
            f"[WEBHOOK] Delivery failed (HTTP 503 Gateway Timeout). Retrying in {calculated_delay:.2f}s "
            f"(attempt {attempt + 1})"
        )
        raise self.retry(
            exc=ConnectionError("Downstream Webhook Gateway 503"),
            countdown=calculated_delay
        )

    logger.info(f"[WEBHOOK] Successfully delivered '{event_type}' to {webhook_url}")
    return {"status": "DELIVERED", "webhook_url": webhook_url, "attempts": attempt + 1}

# -----------------------------------------------------------------------------
# 4. Canvas Workflow Tasks: Chain, Group, and Chord
# -----------------------------------------------------------------------------
# Chain Pipeline Tasks:
@app.task(name="enterprise_tasks.default.extract_order")
def extract_order(order_id: str) -> Dict[str, Any]:
    """Step 1 in chain: Read order record from database."""
    logger.info(f"[CHAIN-1] Extracting order {order_id}")
    return {"order_id": order_id, "customer_id": "cust_42", "subtotal": 150.00, "tax_rate": 0.08}


@app.task(name="enterprise_tasks.default.apply_tax_and_discounts")
def apply_tax_and_discounts(order_data: Dict[str, Any]) -> Dict[str, Any]:
    """Step 2 in chain: Computes total with tax and applies loyalty discount."""
    subtotal = order_data["subtotal"]
    tax = subtotal * order_data["tax_rate"]
    total = round(subtotal + tax - 10.00, 2)  # $10 loyalty coupon
    logger.info(f"[CHAIN-2] Applied tax: {tax:.2f}, Final Total: {total:.2f}")
    order_data["total"] = total
    order_data["invoice_num"] = f"INV-{order_data['order_id']}-2026"
    return order_data


@app.task(name="enterprise_tasks.default.dispatch_invoice_notification")
def dispatch_invoice_notification(final_order: Dict[str, Any]) -> Dict[str, Any]:
    """Step 3 in chain: Dispatches invoice notification to customer."""
    invoice_num = final_order["invoice_num"]
    total = final_order["total"]
    logger.info(f"[CHAIN-3] Dispatched invoice {invoice_num} for ${total:.2f} to {final_order['customer_id']}")
    return {"status": "INVOICED", "invoice": invoice_num, "amount_billed": total}


# Group Parallel Tasks:
@app.task(name="enterprise_tasks.default.process_image_variant")
def process_image_variant(image_id: str, resolution: int) -> Dict[str, Any]:
    """Parallel worker: Resizes an image into specified resolution."""
    logger.info(f"[GROUP] Generating thumbnail {image_id} @ {resolution}x{resolution}px")
    return {
        "image_id": image_id,
        "resolution": resolution,
        "s3_path": f"s3://media/thumbnails/{image_id}_{resolution}px.webp"
    }


# Chord Aggregator Tasks:
@app.task(name="enterprise_tasks.bulk_reports.audit_ledger")
def audit_ledger(account_id: str) -> Dict[str, Any]:
    """Parallel worker in chord: Computes ledger balance for an account."""
    mock_balances = {"ACC-001": 1500.50, "ACC-002": 3200.00, "ACC-003": -450.25, "ACC-004": 8900.10}
    balance = mock_balances.get(account_id, 0.0)
    logger.info(f"[CHORD-WORKER] Account {account_id} balance audited: ${balance:.2f}")
    return {"account_id": account_id, "balance": balance}


@app.task(name="enterprise_tasks.bulk_reports.reconcile_balances")
def reconcile_balances(account_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Chord Callback/Reducer: Aggregates ledger results and generates balance sheet."""
    total_balance = sum(entry["balance"] for entry in account_results)
    audited_count = len(account_results)
    logger.info(f"[CHORD-REDUCER] Reconciled {audited_count} accounts. Net Ledger: ${total_balance:.2f}")
    return {
        "status": "RECONCILED",
        "accounts_audited": audited_count,
        "net_balance": round(total_balance, 2),
        "details": account_results
    }

# -----------------------------------------------------------------------------
# 5. Transaction-Safe Task Dispatcher (Post-Commit Pattern)
# -----------------------------------------------------------------------------
class TransactionalDispatcher:
    """
    Simulates database transaction commit boundaries. Prevents the common
    post-commit race condition where tasks execute before database records exist.
    """
    def __init__(self):
        self._post_commit_queue: List[Any] = []

    def register_on_commit(self, task_func, *args, **kwargs):
        """Register a task to be dispatched strictly after transaction commit."""
        self._post_commit_queue.append((task_func, args, kwargs))

    def commit(self) -> List[Any]:
        """Simulate DB COMMIT. Executes all registered task dispatchers post-commit."""
        logger.info("[DB-TRANSACTION] Database transaction committed successfully.")
        results = []
        while self._post_commit_queue:
            task_func, args, kwargs = self._post_commit_queue.pop(0)
            logger.info(f"[DB-TRANSACTION] Dispatching post-commit task: {task_func.__name__}")
            async_res = task_func.delay(*args, **kwargs)
            results.append(async_res)
        return results

# -----------------------------------------------------------------------------
# 6. Automated Pipeline Verification & Self-Testing Harness
# -----------------------------------------------------------------------------
def run_verification_suite():
    """Executes automated verification suite covering all Day 59 capabilities."""
    logger.info("================================================================================")
    logger.info(" STARTING CELERY PRODUCTION PIPELINE VERIFICATION SUITE")
    logger.info("================================================================================")

    # Clean redis state
    redis_client.flushall()

    # --- Test 1: Idempotent Payment Processing ---
    logger.info("\n--- TEST 1: Idempotency & Deduplication ---")
    tx_id = "TXN-998877"
    amount = 45000  # $450.00

    # First dispatch: Must execute cleanly
    res1 = process_payment.delay(tx_id, amount, currency="USD").get()
    assert res1["status"] == "SETTLED", f"Expected SETTLED, got {res1}"
    assert res1["transaction_id"] == tx_id
    logger.info(f"First dispatch result: {res1}")

    # Second dispatch with identical arguments: Must be intercepted and skipped
    res2 = process_payment.delay(tx_id, amount, currency="USD").get()
    assert res2["status"] == "DUPLICATE_SKIPPED", f"Expected DUPLICATE_SKIPPED, got {res2}"
    assert res2["result"]["transaction_id"] == tx_id
    logger.info(f"Second duplicate dispatch safely intercepted: {res2}")
    logger.info("Test 1 PASSED: Idempotency token correctly shielded duplicate execution.")

    # --- Test 2: Exponential Backoff & DLQ Fallback ---
    logger.info("\n--- TEST 2: Exponential Backoff & DLQ Routing ---")
    # Case 2A: Transient failure recovering on attempt 2
    res_retry = deliver_webhook.delay(
        webhook_url="https://api.partner.com/hooks",
        event_type="order.created",
        payload={"order_id": "ORD-123"},
        fail_count=2
    ).get()
    assert res_retry["status"] == "DELIVERED"
    assert res_retry["attempts"] == 3
    logger.info(f"Webhook with transient failure recovered on attempt {res_retry['attempts']}")

    # Case 2B: Permanent failure exceeding max_retries -> DLQ
    res_dlq = deliver_webhook.delay(
        webhook_url="https://api.broken-partner.com/hooks",
        event_type="payment.failed",
        payload={"order_id": "ORD-456"},
        fail_count=5  # Exceeds max_retries=3
    ).get()
    assert res_dlq["status"] == "DLQ_ROUTED"
    # Verify entry in DLQ
    dlq_entry = redis_client.rpop("celery:dlq:webhooks")
    assert dlq_entry is not None, "Expected entry in celery:dlq:webhooks"
    dlq_data = json.loads(dlq_entry)
    assert dlq_data["webhook_url"] == "https://api.broken-partner.com/hooks"
    logger.info(f"Poison pill verified in DLQ: {dlq_data['webhook_url']} -> {dlq_data['error']}")
    logger.info("Test 2 PASSED: Retries with backoff and DLQ routing verified.")

    # --- Test 3: Canvas Chain Workflow ---
    logger.info("\n--- TEST 3: Canvas Chain (Sequential Pipeline) ---")
    # Pipeline: extract_order -> apply_tax_and_discounts -> dispatch_invoice_notification
    order_pipeline = chain(
        extract_order.s("ORD-8812"),
        apply_tax_and_discounts.s(),
        dispatch_invoice_notification.s()
    )
    chain_result = order_pipeline().get()
    assert chain_result["status"] == "INVOICED"
    assert chain_result["invoice"] == "INV-ORD-8812-2026"
    assert chain_result["amount_billed"] == 152.00  # 150 + (150*0.08) - 10 = 152.00
    logger.info(f"Chain pipeline output: {chain_result}")
    logger.info("Test 3 PASSED: Linear chain pipeline passed data seamlessly.")

    # --- Test 4: Canvas Group Workflow ---
    logger.info("\n--- TEST 4: Canvas Group (Parallel Fan-Out) ---")
    resolutions = [128, 256, 512, 1024]
    thumb_group = group(process_image_variant.s("profile_avatar_user1", res) for res in resolutions)
    group_results = thumb_group().get()
    assert len(group_results) == 4
    for res_dict, expected_res in zip(group_results, resolutions):
        assert res_dict["resolution"] == expected_res
    logger.info(f"Group parallel execution generated {len(group_results)} image variants.")
    logger.info("Test 4 PASSED: Parallel group task execution verified.")

    # --- Test 5: Canvas Chord Workflow ---
    logger.info("\n--- TEST 5: Canvas Chord (Fan-Out + Barrier Reducer) ---")
    accounts = ["ACC-001", "ACC-002", "ACC-003", "ACC-004"]
    # Chord: Run audit_ledger for each account in parallel, aggregate in reconcile_balances
    reconciliation_chord = chord(
        group(audit_ledger.s(acc) for acc in accounts)
    )(reconcile_balances.s())
    chord_result = reconciliation_chord.get()
    assert chord_result["status"] == "RECONCILED"
    assert chord_result["accounts_audited"] == 4
    # 1500.50 + 3200.00 - 450.25 + 8900.10 = 13150.35
    assert chord_result["net_balance"] == 13150.35
    logger.info(f"Chord reduction balance sheet: {chord_result['net_balance']} USD across 4 accounts.")
    logger.info("Test 5 PASSED: Chord barrier synchronization and reduction verified.")

    # --- Test 6: Transaction-Safe Post-Commit Dispatch ---
    logger.info("\n--- TEST 6: Transaction-Safe Dispatch Boundary ---")
    tx_manager = TransactionalDispatcher()
    tx_manager.register_on_commit(process_payment, "TXN-COMMIT-101", 12000, _bypass_idempotency=True)
    tx_manager.register_on_commit(process_payment, "TXN-COMMIT-102", 24000, _bypass_idempotency=True)
    # Tasks must not execute before commit
    assert len(tx_manager._post_commit_queue) == 2
    # Trigger commit
    dispatched_results = tx_manager.commit()
    assert len(dispatched_results) == 2
    assert dispatched_results[0].get()["transaction_id"] == "TXN-COMMIT-101"
    assert dispatched_results[1].get()["transaction_id"] == "TXN-COMMIT-102"
    logger.info("Test 6 PASSED: Post-commit dispatch boundary validated.")

    logger.info("================================================================================")
    logger.info(" ALL 6 PRODUCTION CELERY PIPELINE TESTS PASSED CLEANLY!")
    logger.info("================================================================================")


if __name__ == "__main__":
    run_verification_suite()
