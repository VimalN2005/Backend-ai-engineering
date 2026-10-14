"""
Day 19: Example 02 - Production Backend Architecture: Middleware Pipeline & Bounded Worker Queue

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
Modern backend web frameworks (Django, FastAPI, Starlette, Express) process HTTP
traffic through a layered "Onion Architecture". Incoming requests traverse security,
authentication, and telemetry layers in forward order, while outgoing HTTP responses
unwind through those exact layers in reverse LIFO order via an execution Stack.

Furthermore, asynchronous background workers (e.g. Celery / Redis queue workers)
must process jobs in FIFO order while enforcing bounded queue capacities. Unbounded
queues invite Out-Of-Memory (OOM) outages; bounded queues provide deterministic
backpressure and Route-To-Dead-Letter-Queue (DLQ) capabilities for failed jobs.

This module implements:
1. `MiddlewarePipeline`: Production HTTP middleware onion architecture with LIFO
   response stack unwinding, header injection, and exception safety.
2. `BoundedTaskQueueEngine`: Production worker queue with FIFO scheduling, backpressure
   rejection, retry policies, and Dead-Letter Queue (DLQ) routing.
3. Automated verification suite and execution benchmarks.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================================
# 1. MIDDLEWARE ONION PIPELINE (LIFO STACK UNWINDING)
# ============================================================================

@dataclass
class HTTPRequest:
    path: str
    method: str
    headers: Dict[str, str] = field(default_factory=dict)
    user_id: Optional[str] = None


@dataclass
class HTTPResponse:
    status_code: int
    body: str
    headers: Dict[str, str] = field(default_factory=dict)


class BaseMiddleware:
    """Base class for HTTP middleware components."""
    def process_request(self, request: HTTPRequest) -> Optional[HTTPResponse]:
        """Runs on incoming request in forward order. Returning HTTPResponse short-circuits."""
        return None

    def process_response(self, request: HTTPRequest, response: HTTPResponse) -> HTTPResponse:
        """Runs on outgoing response in reverse LIFO stack order."""
        return response


class SecurityHeadersMiddleware(BaseMiddleware):
    """Injects essential security headers on the outbound response pass."""
    def process_response(self, request: HTTPRequest, response: HTTPResponse) -> HTTPResponse:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response


class ExecutionTimerMiddleware(BaseMiddleware):
    """Calculates request duration by starting a timer on request and logging on response."""
    def __init__(self) -> None:
        self._start_times: Dict[str, float] = {}

    def process_request(self, request: HTTPRequest) -> Optional[HTTPResponse]:
        request.headers["X-Internal-Start-Time"] = str(time.perf_counter())
        return None

    def process_response(self, request: HTTPRequest, response: HTTPResponse) -> HTTPResponse:
        start_str = request.headers.get("X-Internal-Start-Time")
        if start_str:
            duration_ms = (time.perf_counter() - float(start_str)) * 1000.0
            response.headers["X-Response-Time-MS"] = f"{duration_ms:.3f}"
        return response


class AuthenticationMiddleware(BaseMiddleware):
    """Enforces authentication; rejects unauthorized requests before reaching handler."""
    def process_request(self, request: HTTPRequest) -> Optional[HTTPResponse]:
        auth_token = request.headers.get("Authorization")
        if not auth_token or not auth_token.startswith("Bearer valid_token"):
            return HTTPResponse(status_code=401, body="Unauthorized: Invalid or missing token")
        request.user_id = "user_42"
        return None


class MiddlewarePipeline:
    """
    Manages the execution stack for incoming requests and outgoing responses.
    
    WHY STACK UNWINDING:
    As each middleware's process_request passes, its corresponding process_response
    is pushed onto an execution Stack. When the route handler completes, the stack is
    popped, guaranteeing that cleanup and outbound transformations occur in the
    exact reverse order of the inbound pass:
        In:   Security -> Timer -> Auth -> Route Handler
        Out:  Route Handler -> Auth' -> Timer' -> Security'
    """

    def __init__(self) -> None:
        self._middleware_list: List[BaseMiddleware] = []

    def add_middleware(self, middleware: BaseMiddleware) -> None:
        self._middleware_list.append(middleware)

    def execute(self, request: HTTPRequest, handler: Callable[[HTTPRequest], HTTPResponse]) -> HTTPResponse:
        response_stack: List[BaseMiddleware] = []

        # 1. FORWARD PASS: Process request through middleware layers
        for mw in self._middleware_list:
            short_circuit_resp = mw.process_request(request)
            # Push to response stack so unwinding still happens even if short-circuited!
            response_stack.append(mw)

            if short_circuit_resp is not None:
                # Middleware rejected or answered early (e.g. 401 Unauthorized)
                return self._unwind_response_stack(request, short_circuit_resp, response_stack)

        # 2. ENDPOINT EXECUTION: Call business logic handler
        try:
            response = handler(request)
        except Exception as exc:
            # Catch unhandled exceptions and convert to clean 500 response
            response = HTTPResponse(status_code=500, body=f"Internal Server Error: {str(exc)}")

        # 3. REVERSE LIFO PASS: Unwind response stack
        return self._unwind_response_stack(request, response, response_stack)

    def _unwind_response_stack(
        self,
        request: HTTPRequest,
        response: HTTPResponse,
        stack: List[BaseMiddleware],
    ) -> HTTPResponse:
        """Pops and executes response middleware in strict LIFO order."""
        curr_response = response
        while stack:
            mw = stack.pop()
            curr_response = mw.process_response(request, curr_response)
        return curr_response


# ============================================================================
# 2. BOUNDED WORKER TASK QUEUE WITH BACKPRESSURE & DLQ
# ============================================================================

@dataclass
class JobTask:
    task_id: str
    payload: str
    retry_count: int = 0
    max_retries: int = 2


class BoundedTaskQueueEngine:
    """
    Production worker queue utilizing a bounded FIFO deque, backpressure, and DLQ.
    
    WHY BOUNDED DEQUE:
    In-memory queues that allow infinite growth cause silent Out-Of-Memory (OOM)
    process termination under high load. Bounding capacity to max_size rejects or
    throttles excess ingress, preserving service stability.
    """

    def __init__(self, max_capacity: int) -> None:
        self.max_capacity = max_capacity
        self._queue: deque[JobTask] = deque()
        self._dead_letter_queue: deque[JobTask] = deque()

    def enqueue(self, task: JobTask) -> bool:
        """
        Enqueues task to the back of the queue in O(1) time.
        Returns False (Backpressure Rejection) if queue is at capacity.
        """
        if len(self._queue) >= self.max_capacity:
            return False  # Queue saturated: Signal backpressure to producer
        self._queue.append(task)
        return True

    def process_next_task(self, worker_action: Callable[[JobTask], bool]) -> Optional[str]:
        """
        Pulls task from front of queue in O(1) time.
        If task fails, retries up to max_retries; otherwise routes to DLQ.
        """
        if not self._queue:
            return None

        task = self._queue.popleft()
        success = False

        try:
            success = worker_action(task)
        except Exception:
            success = False

        if success:
            return f"SUCCESS: Task {task.task_id} completed."
        else:
            task.retry_count += 1
            if task.retry_count <= task.max_retries:
                # Re-queue for retry at the end of the line
                self._queue.append(task)
                return f"RETRY: Task {task.task_id} failed; re-queued (Attempt {task.retry_count}/{task.max_retries})."
            else:
                # Retries exhausted -> Move to Dead-Letter Queue (DLQ)
                self._dead_letter_queue.append(task)
                return f"DLQ_ROUTED: Task {task.task_id} exhausted retries; moved to DLQ."

    def get_stats(self) -> Tuple[int, int]:
        """Returns (active_queue_len, dlq_len)."""
        return len(self._queue), len(self._dead_letter_queue)


# ============================================================================
# 3. DEMONSTRATION & VERIFICATION SUITE
# ============================================================================

def run_production_middleware_and_queue_demo() -> None:
    print("=" * 75)
    print("DAY 19: PRODUCTION MIDDLEWARE PIPELINE & BOUNDED WORKER QUEUE")
    print("=" * 75)

    # 1. Test Middleware Pipeline Execution
    print("\n[PART 1] Testing HTTP Middleware Pipeline with LIFO Stack Unwinding:")
    pipeline = MiddlewarePipeline()
    pipeline.add_middleware(SecurityHeadersMiddleware())
    pipeline.add_middleware(ExecutionTimerMiddleware())
    pipeline.add_middleware(AuthenticationMiddleware())

    def mock_user_profile_handler(req: HTTPRequest) -> HTTPResponse:
        return HTTPResponse(status_code=200, body=f"Profile Data for user: {req.user_id}")

    # Case A: Authorized Request
    req_auth = HTTPRequest(
        path="/api/v1/profile",
        method="GET",
        headers={"Authorization": "Bearer valid_token_secret"},
    )
    resp_auth = pipeline.execute(req_auth, mock_user_profile_handler)

    print(f"  - Authorized Request Status : {resp_auth.status_code} [{resp_auth.body}]")
    print(f"  - Injected Security Header  : {resp_auth.headers.get('X-Content-Type-Options')}")
    print(f"  - Injected Timing Header    : {resp_auth.headers.get('X-Response-Time-MS')} ms")
    assert resp_auth.status_code == 200
    assert resp_auth.headers.get("X-Content-Type-Options") == "nosniff"
    assert "X-Response-Time-MS" in resp_auth.headers
    print("  - Status: AUTHORIZED STACK UNWINDING VERIFIED [PASSED]")

    # Case B: Unauthorized Request (Short-circuiting at Auth Layer)
    req_unauth = HTTPRequest(path="/api/v1/profile", method="GET", headers={})
    resp_unauth = pipeline.execute(req_unauth, mock_user_profile_handler)

    print(f"  - Unauthorized Status Code  : {resp_unauth.status_code}")
    print(f"  - Security Header Present   : {resp_unauth.headers.get('X-Frame-Options')} (Unwound via LIFO Stack)")
    assert resp_unauth.status_code == 401
    assert resp_unauth.headers.get("X-Frame-Options") == "DENY"
    print("  - Status: SHORT-CIRCUIT STACK UNWINDING VERIFIED [PASSED]")

    # 2. Test Bounded Task Queue with Backpressure & DLQ
    print("\n[PART 2] Testing Bounded Task Queue with Backpressure & DLQ:")
    queue_engine = BoundedTaskQueueEngine(max_capacity=3)

    # Enqueue tasks up to capacity
    assert queue_engine.enqueue(JobTask("task-1", "Send Welcome Email")) is True
    assert queue_engine.enqueue(JobTask("task-2", "Process Stripe Invoice")) is True
    assert queue_engine.enqueue(JobTask("task-3", "Generate AI Summary")) is True

    # 4th task should be REJECTED due to backpressure saturation (capacity = 3)
    task_rejected = queue_engine.enqueue(JobTask("task-4", "Excess Traffic"))
    print(f"  - Task 4 Enqueue Result: {task_rejected} (Expected False: Backpressure Rejection)")
    assert task_rejected is False
    print("  - Status: QUEUE BACKPRESSURE ENFORCEMENT VERIFIED [PASSED]")

    # Worker processing with simulated failure and DLQ routing
    def simulated_worker(task: JobTask) -> bool:
        # Task 1 succeeds; Task 2 consistently fails
        return task.task_id != "task-2"

    print("\n  Worker Execution Sequence:")
    r1 = queue_engine.process_next_task(simulated_worker)
    print(f"  - Step 1: {r1}") # task-1 succeeds
    assert "SUCCESS" in r1

    r2 = queue_engine.process_next_task(simulated_worker)
    print(f"  - Step 2: {r2}") # task-2 fails (retry 1/2)
    assert "RETRY" in r2

    r3 = queue_engine.process_next_task(simulated_worker)
    print(f"  - Step 3: {r3}") # task-3 succeeds
    assert "SUCCESS" in r3

    r4 = queue_engine.process_next_task(simulated_worker)
    print(f"  - Step 4: {r4}") # task-2 fails again (retry 2/2)
    assert "RETRY" in r4

    r5 = queue_engine.process_next_task(simulated_worker)
    print(f"  - Step 5: {r5}") # task-2 exhausted -> DLQ!
    assert "DLQ_ROUTED" in r5

    active_len, dlq_len = queue_engine.get_stats()
    print(f"\n  Final Queue Status: Active Tasks = {active_len} | DLQ Tasks = {dlq_len}")
    assert active_len == 0
    assert dlq_len == 1
    print("  - Status: DLQ ROUTING & RETRY POLICY VERIFIED [PASSED]")

    # 3. High-Throughput Queue Benchmark
    print("\n[PART 3] High-Throughput Deque Job Benchmark (50,000 tasks):")
    bench_engine = BoundedTaskQueueEngine(max_capacity=100000)
    start_time = time.perf_counter()
    for i in range(50000):
        bench_engine.enqueue(JobTask(f"b-{i}", "payload"))
    for _ in range(50000):
        bench_engine.process_next_task(lambda t: True)
    bench_ms = (time.perf_counter() - start_time) * 1000.0

    print(f"  Enqueued & Dequeued 50,000 background jobs: {bench_ms:.2f} ms")
    throughput = (100000 / (bench_ms / 1000.0))
    print(f"  Job Pipeline Throughput: ~{throughput:,.0f} ops/sec")

    print("\n" + "=" * 75)
    print("PRODUCTION STACK & QUEUE ARCHITECTURES VERIFIED & VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_production_middleware_and_queue_demo()
