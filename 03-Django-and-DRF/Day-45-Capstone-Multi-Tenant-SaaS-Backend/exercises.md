# Day 45: Advanced Capstone Exercises — Multi-Tenant SaaS Architecture

This workbook provides 3 practical architectural challenges testing core competencies in multi-tenant SaaS engineering: automated cross-tenant security auditing, tenant-tier sliding-window rate limiting, and transactional outbox event relay with dead-letter queue (DLQ) recovery. Complete verified reference solutions with test harnesses are included.

---

## Challenge 1: Automated Cross-Tenant IDOR Vulnerability Scanner

### Context & Requirements
Before deploying a multi-tenant API to production, security teams execute automated penetration tests to verify that no endpoint allows one tenant to inspect, modify, or delete another tenant's records.
Implement an automated `MultiTenantSecurityAuditor` that:
1. Inspects a target multi-tenant model or service.
2. Generates authenticated requests for `Tenant_Alpha` (User Alice).
3. Intentionally crafts requests querying resource IDs belonging to `Tenant_Beta` (User Bob).
4. Verifies that all cross-tenant requests strictly return `404 Not Found` (never `200 OK` or `403 Forbidden`).
5. Raises a `SecurityVulnerabilityAlert` if any cross-tenant data leak is detected.

---

### Reference Solution

```python
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class SecurityVulnerabilityAlert(Exception):
    """Raised when an IDOR data leak is detected during automated penetration auditing."""
    pass


@dataclass
class AuditResult:
    total_probes: int
    passed_probes: int
    vulnerabilities_detected: int
    audit_passed: bool


class MockDocumentRepository:
    """Simulates a multi-tenant document repository."""
    def __init__(self) -> None:
        self.documents = {
            1: {"id": 1, "tenant_id": "tenant_alpha", "title": "Alpha Strategy"},
            2: {"id": 2, "tenant_id": "tenant_alpha", "title": "Alpha Financials"},
            3: {"id": 3, "tenant_id": "tenant_beta", "title": "Beta Secret Roadmap"},
            4: {"id": 4, "tenant_id": "tenant_beta", "title": "Beta Customer PII"},
        }

    def get_document(self, requesting_tenant: str, doc_id: int) -> Optional[Dict[str, Any]]:
        doc = self.documents.get(doc_id)
        # Strict Multi-Tenant Invariant: Return None if row does not belong to requesting tenant!
        if doc and doc["tenant_id"] == requesting_tenant:
            return dict(doc)
        return None


class MultiTenantSecurityAuditor:
    def __init__(self, repo: MockDocumentRepository) -> None:
        self.repo = repo

    def audit_idor_boundaries(
        self,
        probing_tenant: str,
        victim_tenant: str,
        probed_ids: List[int]
    ) -> AuditResult:
        total = len(probed_ids)
        passed = 0
        leaks = 0

        for doc_id in probed_ids:
            # Check victim ownership
            raw_doc = self.repo.documents.get(doc_id)
            if raw_doc and raw_doc["tenant_id"] == victim_tenant:
                # Probing tenant attempts to access victim's document
                response = self.repo.get_document(requesting_tenant=probing_tenant, doc_id=doc_id)
                if response is not None:
                    leaks += 1
                else:
                    passed += 1

        if leaks > 0:
            raise SecurityVulnerabilityAlert(
                f"IDOR DETECTED: Tenant '{probing_tenant}' successfully accessed {leaks} records belonging to '{victim_tenant}'!"
            )

        return AuditResult(
            total_probes=total,
            passed_probes=passed,
            vulnerabilities_detected=leaks,
            audit_passed=(leaks == 0)
        )


# --- Verification Harness ---
if __name__ == "__main__":
    repo = MockDocumentRepository()
    auditor = MultiTenantSecurityAuditor(repo)

    # Audit: Tenant Alpha probing Tenant Beta's documents (IDs 3 and 4)
    result = auditor.audit_idor_boundaries(
        probing_tenant="tenant_alpha",
        victim_tenant="tenant_beta",
        probed_ids=[3, 4]
    )

    assert result.audit_passed is True
    assert result.vulnerabilities_detected == 0
    assert result.passed_probes == 2
    print(f"  -> Total Probes: {result.total_probes}, Leaks Detected: {result.vulnerabilities_detected}")
    print("[CHALLENGE 1 PASSED] Automated Cross-Tenant IDOR Scanner verified.")
```

---

## Challenge 2: Multi-Tenant Sliding-Window Rate Limiter with Tier Quotas

### Context & Requirements
In B2B SaaS, tenants operate under different subscription tiers (Free, Pro, Enterprise). A Free-tier tenant must not be allowed to exhaust system capacity, while an Enterprise tenant requires high burst limits.
Implement a `MultiTenantSlidingWindowLimiter` that:
1. Enforces tenant-specific rate limits based on subscription tier:
   - `FREE`: 10 requests / 60 seconds.
   - `PRO`: 100 requests / 60 seconds.
   - `ENTERPRISE`: 1,000 requests / 60 seconds.
2. Uses an in-memory Redis-style sliding window log algorithm (`now - window_seconds`).
3. Returns a tuple `(is_allowed: bool, remaining_quota: int, reset_after_seconds: float)`.
4. Tests concurrent tenant traffic to verify that one tenant exceeding quota does not affect another tenant.

---

### Reference Solution

```python
import threading
import time
from typing import Dict, List, Tuple


TIER_LIMITS: Dict[str, int] = {
    "FREE": 10,
    "PRO": 100,
    "ENTERPRISE": 1000,
}


class MultiTenantSlidingWindowLimiter:
    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = window_seconds
        # tenant_id -> list of timestamp floats
        self._request_logs: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow_request(self, tenant_id: str, tier: str) -> Tuple[bool, int, float]:
        max_requests = TIER_LIMITS.get(tier, 10)
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            timestamps = self._request_logs.setdefault(tenant_id, [])

            # Prune timestamps outside the current sliding window
            self._request_logs[tenant_id] = [t for t in timestamps if t > window_start]
            current_count = len(self._request_logs[tenant_id])

            if current_count < max_requests:
                self._request_logs[tenant_id].append(now)
                remaining = max_requests - (current_count + 1)
                return True, remaining, self.window_seconds
            else:
                oldest_timestamp = self._request_logs[tenant_id][0]
                reset_after = max(0.0, oldest_timestamp + self.window_seconds - now)
                return False, 0, reset_after


# --- Verification Harness ---
if __name__ == "__main__":
    limiter = MultiTenantSlidingWindowLimiter(window_seconds=1.0)

    # 1. Free tier tenant: allowed 10 requests, 11th rejected
    for _ in range(10):
        allowed, remaining, _ = limiter.allow_request("tenant_free", "FREE")
        assert allowed is True

    blocked, remaining, reset_in = limiter.allow_request("tenant_free", "FREE")
    assert blocked is False
    assert remaining == 0
    print(f"  -> Free tenant correctly throttled. Reset in {reset_in:.3f}s.")

    # 2. Enterprise tenant: completely unaffected by Free tenant throttling (Noisy Neighbor Defense)
    allowed_ent, remaining_ent, _ = limiter.allow_request("tenant_corp", "ENTERPRISE")
    assert allowed_ent is True
    assert remaining_ent == 999
    print("[CHALLENGE 2 PASSED] Multi-Tenant Sliding Window Rate Limiter verified.")
```

---

## Challenge 3: Transactional Outbox Relay with Dead-Letter Queue (DLQ)

### Context & Requirements
When relaying domain events from the `Outbox` table to external brokers (Kafka/RabbitMQ), transient network failures must be retried with exponential backoff. If an event repeatedly fails, it must be moved to a **Dead-Letter Queue (DLQ)** to prevent poison-pill events from halting the relay pipeline.
Implement a `TransactionalOutboxRelay` that:
1. Polls `PENDING` events from an outbox store.
2. Simulates message broker publishing with intermittent network failures.
3. Implements an exponential backoff retry counter (`max_retries = 3`).
4. Moves permanently failed events to a `DEAD_LETTER` state with the failure reason logged.
5. Verifies at-least-once delivery and DLQ isolation.

---

### Reference Solution

```python
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class OutboxMessage:
    id: str
    tenant_id: str
    event_type: str
    payload: Dict[str, Any]
    status: str = "PENDING"  # PENDING, PUBLISHED, DEAD_LETTER
    retry_count: int = 0
    last_error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class MockKafkaBroker:
    def __init__(self, poison_event_id: str) -> None:
        self.published: List[Dict[str, Any]] = []
        self.poison_event_id = poison_event_id

    def publish(self, topic: str, key: str, data: Dict[str, Any], event_id: str) -> None:
        if event_id == self.poison_event_id:
            raise ConnectionError("Kafka broker rejected malformed schema payload.")
        self.published.append({"topic": topic, "key": key, "data": data})


class TransactionalOutboxRelay:
    def __init__(self, broker: MockKafkaBroker, max_retries: int = 3) -> None:
        self.broker = broker
        self.max_retries = max_retries
        self.outbox_store: Dict[str, OutboxMessage] = {}

    def stage_event(self, tenant_id: str, event_type: str, payload: Dict[str, Any]) -> str:
        msg_id = str(uuid.uuid4())
        self.outbox_store[msg_id] = OutboxMessage(
            id=msg_id,
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload
        )
        return msg_id

    def process_outbox_batch(self) -> Tuple[int, int, int]:
        """Returns: (published_count, retried_count, dlq_count)"""
        published = 0
        retried = 0
        dlq = 0

        pending = [m for m in self.outbox_store.values() if m.status == "PENDING"]

        for msg in pending:
            try:
                topic = f"events.tenant.{msg.tenant_id}"
                self.broker.publish(
                    topic=topic,
                    key=msg.tenant_id,
                    data=msg.payload,
                    event_id=msg.id
                )
                msg.status = "PUBLISHED"
                published += 1
            except Exception as exc:
                msg.retry_count += 1
                msg.last_error = str(exc)
                if msg.retry_count >= self.max_retries:
                    msg.status = "DEAD_LETTER"
                    dlq += 1
                else:
                    retried += 1

        return published, retried, dlq


# --- Verification Harness ---
if __name__ == "__main__":
    poison_id = "msg_poison_101"
    broker = MockKafkaBroker(poison_event_id=poison_id)
    relay = TransactionalOutboxRelay(broker, max_retries=3)

    # 1. Stage healthy event and poison event
    ok_id = relay.stage_event("tenant_acme", "ORDER_CREATED", {"order_id": 42})
    # Manually inject known poison ID
    relay.outbox_store[poison_id] = OutboxMessage(
        id=poison_id,
        tenant_id="tenant_beta",
        event_type="CORRUPT_PAYLOAD",
        payload={"corrupt": True}
    )

    # Batch run 1
    pub1, ret1, dlq1 = relay.process_outbox_batch()
    assert pub1 == 1  # ok_id published
    assert ret1 == 1  # poison_id retried (count=1)
    assert dlq1 == 0

    # Batch run 2 & 3: exhaust retries on poison message
    relay.process_outbox_batch()  # retry 2
    pub3, ret3, dlq3 = relay.process_outbox_batch()  # retry 3 -> moved to DLQ

    poison_msg = relay.outbox_store[poison_id]
    assert poison_msg.status == "DEAD_LETTER"
    assert poison_msg.retry_count == 3
    assert "rejected malformed" in poison_msg.last_error
    print(f"  -> Healthy event published: {len(broker.published)}")
    print(f"  -> Poison event successfully isolated to DLQ with status '{poison_msg.status}'.")
    print("[CHALLENGE 3 PASSED] Transactional Outbox Relay with DLQ verified.")
```
