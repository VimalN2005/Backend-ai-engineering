# Celery Production Engineering Exercises

Three real-world enterprise engineering challenges with complete, runnable, verified reference solutions.

---

## Exercise 1: Distributed Idempotent Webhook Consumer with Redis Mutex

### Problem Statement
Your e-commerce backend receives high volumes of webhook events from third-party payment gateways (e.g., Stripe `invoice.payment_succeeded`). Due to network instability and aggressive gateway retries, the webhook endpoint frequently receives identical webhook events within milliseconds. 

Build a production-grade Celery task that:
1. Derives an atomic idempotency key from the webhook event ID and customer ID.
2. Uses Redis atomic primitives (`SET NX EX`) to guarantee that only **one worker** can process the event.
3. Safely returns cached results for duplicate deliveries without re-executing business side effects.
4. Unlocks the key upon unexpected exceptions so subsequent legitimate retries are not blocked.

### Verified Reference Solution

```python
import hashlib
import json
import logging
import time
from typing import Any, Dict
from celery import Celery, Task
import fakeredis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_worker")

app = Celery("webhook_app", broker="memory://", backend="cache+memory://")
app.conf.update(
    task_always_eager=True,
    task_eager_propagates=False,
    task_serializer="json",
    result_serializer="json"
)

redis_db = fakeredis.FakeRedis(decode_responses=True)


class IdempotentWebhookTask(Task):
    """Custom Task Base providing distributed mutex and idempotency verification."""
    abstract = True

    def get_event_lock_key(self, event_id: str, event_type: str) -> str:
        return f"celery:webhook:lock:{event_type}:{event_id}"

    def __call__(self, event_id: str, event_type: str, payload: dict, *args, **kwargs) -> Dict[str, Any]:
        lock_key = self.get_event_lock_key(event_id, event_type)
        result_key = f"{lock_key}:result"

        # 1. Acquire mutex: Set PROCESSING with 30-minute expiration
        acquired = redis_db.set(lock_key, "PROCESSING", nx=True, ex=1800)

        if not acquired:
            state = redis_db.get(lock_key)
            if state == "COMPLETED":
                cached_data = redis_db.get(result_key)
                logger.warning(f"[IDEMPOTENCY] Webhook {event_id} already settled. Returning cached result.")
                return {
                    "status": "DUPLICATE_SUPPRESSED",
                    "event_id": event_id,
                    "cached_result": json.loads(cached_data) if cached_data else None
                }
            elif state == "PROCESSING":
                logger.warning(f"[IDEMPOTENCY] Webhook {event_id} currently being processed by another worker.")
                raise RuntimeError(f"Concurrent execution detected for event {event_id}")

        # 2. Execute task logic
        try:
            result = super().__call__(event_id, event_type, payload, *args, **kwargs)
            # 3. Mark COMPLETED and cache result payload with 48-hour TTL
            redis_db.set(lock_key, "COMPLETED", ex=172800)
            redis_db.set(result_key, json.dumps(result), ex=172800)
            return result
        except Exception as exc:
            # Clear lock upon failure so retry mechanism can re-attempt
            redis_db.delete(lock_key)
            logger.error(f"[ERROR] Webhook {event_id} failed with {exc}; unlocked key.")
            raise


@app.task(base=IdempotentWebhookTask, bind=True, name="tasks.process_stripe_webhook")
def process_stripe_webhook(self, event_id: str, event_type: str, payload: dict) -> Dict[str, Any]:
    """Processes settled invoice webhooks."""
    logger.info(f"Processing invoice settlement for Event ID: {event_id}")
    customer_id = payload.get("customer_id")
    amount_paid = payload.get("amount_paid_cents", 0)

    # Simulate database ledger write
    return {
        "status": "INVOICE_SETTLED",
        "event_id": event_id,
        "customer_id": customer_id,
        "amount_usd": amount_paid / 100.0,
        "settled_at": time.time()
    }


# Verification Test
if __name__ == "__main__":
    test_event_id = "evt_3MvY82LkdIwHu7ix042"
    payload = {"customer_id": "cus_9941", "amount_paid_cents": 12500}

    # First dispatch: Must process successfully
    r1 = process_stripe_webhook.delay(test_event_id, "invoice.paid", payload).get()
    assert r1["status"] == "INVOICE_SETTLED"
    assert r1["amount_usd"] == 125.0

    # Immediate duplicate dispatch: Must be safely intercepted
    r2 = process_stripe_webhook.delay(test_event_id, "invoice.paid", payload).get()
    assert r2["status"] == "DUPLICATE_SUPPRESSED"
    assert r2["cached_result"]["status"] == "INVOICE_SETTLED"
    print("Exercise 1 Test PASSED: Idempotent webhook processing successfully verified.")
```

---

## Exercise 2: Dynamic Celery Beat Health Check & Alert Dispatcher

### Problem Statement
Design an automated Celery Beat health monitoring dispatcher that runs periodically (every 60 seconds). The monitor evaluates database ping latency, Redis memory capacity, and third-party API availability. If an individual service check fails 3 consecutive times, the task dispatches an emergency notification to PagerDuty/Slack.

### Verified Reference Solution

```python
import logging
import time
from typing import Any, Dict, List
from celery import Celery
import fakeredis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("beat_health")

beat_app = Celery("health_app", broker="memory://", backend="cache+memory://")
beat_app.conf.update(
    task_always_eager=True,
    task_eager_propagates=False
)

redis_monitor = fakeredis.FakeRedis(decode_responses=True)


@beat_app.task(name="tasks.dispatch_incident_alert")
def dispatch_incident_alert(service_name: str, consecutive_failures: int, details: str) -> Dict[str, Any]:
    """High-priority alert notification dispatcher."""
    logger.critical(
        f"[ALERT DISPATCHED] Service: {service_name} | Consecutive Failures: {consecutive_failures} | Reason: {details}"
    )
    return {
        "alert_status": "TRIGGERED",
        "service": service_name,
        "failures": consecutive_failures,
        "timestamp": time.time()
    }


@beat_app.task(bind=True, name="tasks.check_system_health")
def check_system_health(self) -> Dict[str, Any]:
    """Periodic health-check entrypoint triggered by Celery Beat."""
    services = [
        {"name": "PostgreSQL-Primary", "mock_healthy": True, "latency_ms": 1.4},
        {"name": "Redis-Cluster", "mock_healthy": True, "latency_ms": 0.3},
        {"name": "Stripe-Payment-API", "mock_healthy": False, "latency_ms": 5000.0},  # Down
    ]

    report: Dict[str, Any] = {"healthy": [], "degraded": []}

    for svc in services:
        name = svc["name"]
        counter_key = f"health:consecutive_fails:{name}"

        if svc["mock_healthy"]:
            # Reset consecutive failure counter on healthy heartbeat
            redis_monitor.set(counter_key, 0)
            report["healthy"].append(name)
            logger.info(f"[HEALTH CHECK] {name}: OK ({svc['latency_ms']} ms)")
        else:
            # Increment failure counter
            current_fails = redis_monitor.incr(counter_key)
            report["degraded"].append({"name": name, "consecutive_fails": current_fails})
            logger.warning(f"[HEALTH CHECK] {name}: FAILED (Failure count: {current_fails})")

            # Alert threshold reached (>= 3 consecutive failures)
            if current_fails >= 3:
                dispatch_incident_alert.delay(
                    service_name=name,
                    consecutive_failures=current_fails,
                    details=f"Gateway timeout exceeding {svc['latency_ms']}ms"
                )

    return report


# Verification Test
if __name__ == "__main__":
    redis_monitor.flushall()

    # Simulate 3 consecutive Beat executions
    print("\n--- BEAT TICK 1 ---")
    r1 = check_system_health.delay().get()
    assert len(r1["degraded"]) == 1

    print("\n--- BEAT TICK 2 ---")
    r2 = check_system_health.delay().get()

    print("\n--- BEAT TICK 3 (Threshold Reached) ---")
    r3 = check_system_health.delay().get()
    fails = int(redis_monitor.get("health:consecutive_fails:Stripe-Payment-API"))
    assert fails == 3, f"Expected 3 failures, got {fails}"
    print("Exercise 2 Test PASSED: Health monitoring and threshold alerting verified.")
```

---

## Exercise 3: Resilient Video Transcoding Canvas Workflow (`chord`)

### Problem Statement
In a media pipeline, users upload high-resolution master video files. The system must transcode the video into 4 target resolutions: `[1080p, 720p, 480p, 360p]` in parallel using Celery's `chord` primitive. Once all parallel workers finish, an aggregator task compiles a master HLS `.m3u8` playlist.

**Resilience Requirement**: If an optional ultra-high-definition variant (e.g. `1080p`) fails due to a temporary resource limit, the aggregator task must not fail completely. Instead, it must gracefully exclude the failed variant and publish an adaptive stream with the remaining healthy resolutions.

### Verified Reference Solution

```python
import logging
from typing import Any, Dict, List
from celery import Celery, chord, group

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("media_pipeline")

media_app = Celery("media_app", broker="memory://", backend="cache+memory://")
media_app.conf.update(
    task_always_eager=True,
    task_eager_propagates=False,
    task_serializer="json",
    result_serializer="json"
)


@media_app.task(name="media.transcode_variant")
def transcode_variant(video_id: str, resolution: str, fail_variant: bool = False) -> Dict[str, Any]:
    """Parallel worker: Transcodes master file into a specific resolution."""
    if fail_variant and resolution == "1080p":
        logger.error(f"[TRANSCODE ERROR] Variant {resolution} failed due to memory limit.")
        return {"status": "FAILED", "resolution": resolution, "error": "OOM limit"}

    logger.info(f"[TRANSCODE] Completed {video_id} @ {resolution}")
    return {
        "status": "SUCCESS",
        "video_id": video_id,
        "resolution": resolution,
        "stream_url": f"https://cdn.media.com/streams/{video_id}/{resolution}/index.m3u8"
    }


@media_app.task(name="media.compile_master_playlist")
def compile_master_playlist(variant_results: List[Dict[str, Any]], video_id: str) -> Dict[str, Any]:
    """Chord Reducer: Aggregates successful variant streams into master HLS manifest."""
    successful_variants = [r for r in variant_results if r.get("status") == "SUCCESS"]
    failed_variants = [r for r in variant_results if r.get("status") == "FAILED"]

    if not successful_variants:
        raise RuntimeError(f"All transcoding variants failed for video {video_id}")

    master_manifest = {
        "video_id": video_id,
        "hls_master_url": f"https://cdn.media.com/streams/{video_id}/master.m3u8",
        "available_resolutions": [v["resolution"] for v in successful_variants],
        "dropped_resolutions": [v["resolution"] for v in failed_variants],
        "is_partial_degradation": len(failed_variants) > 0
    }

    logger.info(
        f"[PLAYLIST COMPILED] Master manifest generated for {video_id} with "
        f"{len(successful_variants)} resolutions. Dropped: {master_manifest['dropped_resolutions']}"
    )
    return master_manifest


# Verification Test
if __name__ == "__main__":
    vid = "vid_enterprise_8899"
    targets = ["1080p", "720p", "480p", "360p"]

    # Execute chord: group of 4 variants -> reducer callback with partial failure simulation
    transcode_pipeline = chord(
        group(transcode_variant.s(vid, res, fail_variant=(res == "1080p")) for res in targets)
    )(compile_master_playlist.s(video_id=vid))

    manifest = transcode_pipeline.get()
    assert manifest["video_id"] == vid
    assert manifest["is_partial_degradation"] is True
    assert "1080p" in manifest["dropped_resolutions"]
    assert "720p" in manifest["available_resolutions"]
    assert "360p" in manifest["available_resolutions"]
    print("Exercise 3 Test PASSED: Resilient chord transcode pipeline verified.")
```
