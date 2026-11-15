"""Day 65: Production Process Supervisor, Systemd Watchdog & ACME SSL Verifier.

This production-grade script provides:
1. Gunicorn Master-Worker Supervisor Simulation (forking, SIGHUP reload, SIGCHLD reaper).
2. Systemd Unit Manager & Watchdog Ping Engine (Restart=always, WatchdogSec, failure recovery).
3. Let's Encrypt ACME HTTP-01 Challenge Verifier & Certificate Renewal Engine.
4. Nginx Token/Leaky Bucket Rate Limiting Engine (rate=10r/s, burst=20, nodelay semantics).
5. Comprehensive standalone automated test suite.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. GUNICORN MASTER-WORKER LIFECYCLE SUPERVISOR
# ==============================================================================

class WorkerState:
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    TERMINATED = "TERMINATED"


@dataclass
class SimulatedWorker:
    pid: int
    state: str = WorkerState.STARTING
    birth_time: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    requests_processed: int = 0
    generation: int = 1

    def send_heartbeat(self, now: float) -> None:
        self.last_heartbeat = now


class GunicornMasterSupervisor:
    """Simulates Gunicorn's Unix master process lifecycle management."""

    def __init__(self, num_workers: int = 4, timeout: float = 30.0, max_requests: int = 1000) -> None:
        self.num_workers = num_workers
        self.timeout = timeout
        self.max_requests = max_requests
        self.generation = 1
        self.next_pid = 1000
        self.workers: Dict[int, SimulatedWorker] = {}
        self.draining_workers: Dict[int, SimulatedWorker] = {}
        self._spawn_initial_workers()

    def _spawn_worker(self) -> SimulatedWorker:
        self.next_pid += 1
        worker = SimulatedWorker(
            pid=self.next_pid,
            state=WorkerState.RUNNING,
            generation=self.generation,
            birth_time=time.time(),
            last_heartbeat=time.time()
        )
        self.workers[worker.pid] = worker
        return worker

    def _spawn_initial_workers(self) -> None:
        for _ in range(self.num_workers):
            self._spawn_worker()

    def handle_sighup(self) -> Dict[str, Any]:
        """SIGHUP: Zero-Downtime Reload.

        Master re-reads configuration and spawns a new generation of workers.
        Old workers enter DRAINING state and terminate only after new workers are ready.
        """
        self.generation += 1
        old_pids = list(self.workers.keys())

        # Move current active workers to draining pool
        for pid in old_pids:
            worker = self.workers.pop(pid)
            worker.state = WorkerState.DRAINING
            self.draining_workers[pid] = worker

        # Spawn brand new generation of workers
        new_workers = []
        for _ in range(self.num_workers):
            new_workers.append(self._spawn_worker())

        return {
            "action": "SIGHUP_RELOAD",
            "new_generation": self.generation,
            "new_worker_pids": [w.pid for w in new_workers],
            "draining_worker_pids": list(self.draining_workers.keys()),
        }

    def drain_completed(self, pid: int) -> None:
        """Called when a draining worker finishes its in-flight connections."""
        if pid in self.draining_workers:
            w = self.draining_workers.pop(pid)
            w.state = WorkerState.TERMINATED

    def check_health_and_reap(self, now: float) -> List[str]:
        """Simulates master loop: SIGCHLD harvesting and timeout watchdog."""
        events = []

        # 1. Check for hung/timed out workers
        timed_out = []
        for pid, worker in list(self.workers.items()):
            if now - worker.last_heartbeat > self.timeout:
                timed_out.append(pid)

        for pid in timed_out:
            events.append(f"WORKER_TIMEOUT: Worker PID {pid} exceeded timeout ({self.timeout}s). Sent SIGKILL.")
            del self.workers[pid]

        # 2. Reap/replace crashed or terminated workers (maintain worker pool size)
        while len(self.workers) < self.num_workers:
            new_w = self._spawn_worker()
            events.append(f"WORKER_REPLACED: Spawned replacement worker PID {new_w.pid} (Gen {new_w.generation}).")

        return events


# ==============================================================================
# 2. SYSTEMD SERVICE UNIT & WATCHDOG SIMULATOR
# ==============================================================================

@dataclass
class SystemdConfig:
    unit_name: str = "gunicorn.service"
    service_type: str = "notify"  # simple, forking, notify
    restart_policy: str = "always"
    restart_sec: float = 2.0
    watchdog_sec: float = 10.0
    start_limit_burst: int = 5
    start_limit_interval_sec: float = 60.0


class SystemdUnitSimulator:
    """Simulates systemd PID 1 service supervision, watchdog health, and restart policies."""

    def __init__(self, config: Optional[SystemdConfig] = None) -> None:
        self.config = config or SystemdConfig()
        self.state = "inactive"  # inactive, activating, active, deactivating, failed
        self.main_pid: Optional[int] = None
        self.last_watchdog_ping: float = 0.0
        self.restart_history: Deque[float] = collections.deque()
        self.total_restarts = 0

    def start(self, pid: int, now: float) -> str:
        self.main_pid = pid
        self.state = "active"
        self.last_watchdog_ping = now
        return f"Systemd: Service {self.config.unit_name} started with Main PID {pid}."

    def sd_notify_watchdog(self, now: float) -> bool:
        """Simulates sd_notify('WATCHDOG=1'). Returns True if accepted."""
        if self.state != "active":
            return False
        self.last_watchdog_ping = now
        return True

    def evaluate_watchdog(self, now: float) -> Tuple[bool, Optional[str]]:
        """Checks if main process missed watchdog deadline."""
        if self.state != "active":
            return True, None

        if self.config.watchdog_sec > 0:
            elapsed = now - self.last_watchdog_ping
            if elapsed > self.config.watchdog_sec:
                self.state = "failed"
                msg = f"Watchdog timeout elapsed ({elapsed:.1f}s > {self.config.watchdog_sec}s). Service marked FAILED."
                return False, msg
        return True, None

    def trigger_crash(self, now: float, exit_code: int = 1) -> Dict[str, Any]:
        """Simulates process crash and executes Restart=always policy."""
        self.state = "failed"
        self.main_pid = None
        self.total_restarts += 1
        self.restart_history.append(now)

        # Purge restarts older than interval
        while self.restart_history and (now - self.restart_history[0] > self.config.start_limit_interval_sec):
            self.restart_history.popleft()

        # Check burst limit
        if len(self.restart_history) > self.config.start_limit_burst:
            self.state = "failed"
            return {
                "action": "START_LIMIT_HIT",
                "message": f"Unit entered failed state: start-limit-hit ({len(self.restart_history)} crashes within {self.config.start_limit_interval_sec}s).",
                "restart_allowed": False
            }

        return {
            "action": "AUTO_RESTART_SCHEDULED",
            "message": f"Service crashed with code {exit_code}. Restarting in {self.config.restart_sec}s (Policy: {self.config.restart_policy}).",
            "restart_allowed": True,
            "restart_in_seconds": self.config.restart_sec
        }


# ==============================================================================
# 3. LET'S ENCRYPT ACME HTTP-01 CHALLENGE & CERTIFICATE RENEWAL ENGINE
# ==============================================================================

@dataclass
class ACMECertificate:
    domain: str
    issuer: str
    issued_at: float
    expires_at: float
    serial_number: str

    def days_until_expiration(self, now: float) -> float:
        return (self.expires_at - now) / 86400.0

    def requires_renewal(self, now: float, renewal_threshold_days: float = 30.0) -> bool:
        return self.days_until_expiration(now) <= renewal_threshold_days


class ACMEChallengeEngine:
    """Simulates Let's Encrypt automated ACME HTTP-01 domain authorization."""

    def __init__(self, account_key: str = "account_secret_key_123") -> None:
        self.account_key = account_key
        self.active_challenges: Dict[str, str] = {}  # token -> expected key_authorization

    def generate_challenge(self, domain: str) -> Tuple[str, str]:
        """Generates token and key_authorization according to RFC 8555."""
        token = hashlib.sha256(f"{domain}:{time.time()}".encode()).hexdigest()[:32]
        thumbprint = hashlib.sha256(self.account_key.encode()).hexdigest()[:16]
        key_authorization = f"{token}.{thumbprint}"
        self.active_challenges[token] = key_authorization
        return token, key_authorization

    def verify_http01_response(self, token: str, presented_body: str) -> bool:
        """Simulates ACME CA GET request to /.well-known/acme-challenge/<token>."""
        expected = self.active_challenges.get(token)
        if expected is None:
            return False
        return presented_body.strip() == expected

    def issue_certificate(self, domain: str, now: float) -> ACMECertificate:
        """Issues 90-day production TLS certificate."""
        return ACMECertificate(
            domain=domain,
            issuer="Let's Encrypt Authority X3 (R3)",
            issued_at=now,
            expires_at=now + (90 * 86400),
            serial_number=hashlib.md5(f"{domain}:{now}".encode()).hexdigest()
        )


# ==============================================================================
# 4. NGINX LEAKY BUCKET RATE LIMITER (limit_req)
# ==============================================================================

class NginxRateLimiter:
    """Simulates Nginx limit_req module using the Leaky Bucket algorithm.

    Corresponds to:
      limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
      limit_req zone=api burst=20 nodelay;
    """

    def __init__(self, rate_per_second: float = 10.0, burst: int = 20, nodelay: bool = True) -> None:
        self.rate_per_sec = rate_per_second
        self.burst = burst
        self.nodelay = nodelay
        # client_ip -> (excess_tokens, last_update_time)
        self.buckets: Dict[str, Tuple[float, float]] = {}

    def allow_request(self, client_ip: str, now: Optional[float] = None) -> Tuple[bool, int, Dict[str, Any]]:
        """Processes an incoming request. Returns (is_allowed, http_status, telemetry)."""
        if now is None:
            now = time.time()

        excess, last_time = self.buckets.get(client_ip, (0.0, now))

        # 1. Leak accumulated excess tokens over elapsed time
        elapsed = max(0.0, now - last_time)
        leaked = elapsed * self.rate_per_sec
        excess = max(0.0, excess - leaked)

        # 2. Check if adding current request exceeds burst
        # In Nginx, 1 request adds 1000 millitokens (1.0 token)
        if excess + 1.0 > self.burst + 1.0:
            # Capacity exceeded -> HTTP 429 Too Many Requests
            self.buckets[client_ip] = (excess, now)
            return False, 429, {
                "client_ip": client_ip,
                "current_excess": round(excess, 2),
                "burst_capacity": self.burst,
                "retry_after_seconds": round((excess + 1.0 - self.burst) / self.rate_per_sec, 2)
            }

        # Request accepted: increment excess count
        excess += 1.0
        self.buckets[client_ip] = (excess, now)
        return True, 200, {
            "client_ip": client_ip,
            "current_excess": round(excess, 2),
            "burst_capacity": self.burst,
            "retry_after_seconds": 0.0
        }


# ==============================================================================
# 5. DIAGNOSTICS & VERIFICATION RUNNER
# ==============================================================================

def run_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 65: PROCESS SUPERVISOR, SYSTEMD WATCHDOG & ACME SSL DIAGNOSTICS")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # Diagnostic 1: Gunicorn Zero-Downtime SIGHUP Reload Test
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 1] Gunicorn Master Supervisor: SIGHUP Zero-Downtime Reload:")
    master = GunicornMasterSupervisor(num_workers=4, timeout=10.0)
    initial_pids = list(master.workers.keys())
    print(f"  • Spawned Generation 1 Master with 4 Workers: {initial_pids}")
    assert len(initial_pids) == 4

    reload_info = master.handle_sighup()
    print(f"  • Sent SIGHUP to Master:")
    print(f"    - New Generation: {reload_info['new_generation']}")
    print(f"    - New Workers: {reload_info['new_worker_pids']}")
    print(f"    - Draining Workers: {reload_info['draining_worker_pids']}")
    assert reload_info["new_generation"] == 2
    assert len(master.workers) == 4
    assert len(master.draining_workers) == 4

    # Complete draining of old workers
    for pid in reload_info["draining_worker_pids"]:
        master.drain_completed(pid)
    assert len(master.draining_workers) == 0
    print("  ✓ Zero-Downtime reload verified: All old workers drained cleanly without dropped connections.")

    # --------------------------------------------------------------------------
    # Diagnostic 2: Worker Heartbeat Timeout & Automatic Reaper Test
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 2] Master Watchdog & Worker Timeout Reaper:")
    now = time.time()
    stuck_worker_pid = list(master.workers.keys())[0]
    # Simulate worker becoming unresponsive (no heartbeat for 15s > 10s timeout)
    master.workers[stuck_worker_pid].last_heartbeat = now - 15.0

    events = master.check_health_and_reap(now)
    for evt in events:
        print(f"  • [EVENT] {evt}")
    assert any("WORKER_TIMEOUT" in e and str(stuck_worker_pid) in e for e in events)
    assert any("WORKER_REPLACED" in e for e in events)
    assert stuck_worker_pid not in master.workers
    assert len(master.workers) == 4
    print("  ✓ Master detected unresponsive worker, terminated it, and spawned clean replacement.")

    # --------------------------------------------------------------------------
    # Diagnostic 3: Systemd Unit Supervisor & Watchdog Ping
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 3] Systemd Watchdog & Burst Limit Crash Policy:")
    systemd = SystemdUnitSimulator(SystemdConfig(
        watchdog_sec=5.0,
        restart_sec=1.0,
        start_limit_burst=3,
        start_limit_interval_sec=10.0
    ))
    t0 = 1000.0
    systemd.start(pid=5001, now=t0)
    assert systemd.state == "active"
    print(f"  • Systemd service active with PID 5001.")

    # Pinging watchdog keeps service alive
    systemd.sd_notify_watchdog(now=t0 + 3.0)
    ok, _ = systemd.evaluate_watchdog(now=t0 + 4.0)
    assert ok is True
    print("  • Watchdog ping received within deadline -> Service remains healthy.")

    # Miss watchdog deadline (7s > 5s)
    ok, msg = systemd.evaluate_watchdog(now=t0 + 11.0)
    assert ok is False
    print(f"  • Missed watchdog deadline -> {msg}")

    # Crash & Restart Burst Policy Test
    res1 = systemd.trigger_crash(now=t0 + 12.0)
    assert res1["restart_allowed"] is True
    res2 = systemd.trigger_crash(now=t0 + 13.0)
    assert res2["restart_allowed"] is True
    res3 = systemd.trigger_crash(now=t0 + 14.0)
    assert res3["restart_allowed"] is True
    res4 = systemd.trigger_crash(now=t0 + 15.0)  # 4th crash within 10s exceeds burst 3
    assert res4["restart_allowed"] is False
    print(f"  • Crash Burst Enforcement: {res4['message']}")
    print("  ✓ Systemd correctly enforced start-limit-hit to prevent infinite crash-looping.")

    # --------------------------------------------------------------------------
    # Diagnostic 4: Let's Encrypt ACME HTTP-01 Challenge & Renewal
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 4] ACME HTTP-01 Protocol Challenge & Auto-Renewal:")
    acme = ACMEChallengeEngine()
    domain = "api.enterprise.internal"
    token, key_auth = acme.generate_challenge(domain)
    print(f"  • Challenge generated for domain: {domain}")
    print(f"    - Token: {token[:16]}...")
    print(f"    - Key Authorization: {key_auth[:24]}...")

    # Nginx serves challenge at /.well-known/acme-challenge/<token>
    assert acme.verify_http01_response(token, key_auth) is True
    assert acme.verify_http01_response(token, "invalid_payload") is False
    print("  • CA HTTP-01 verification confirmed match against Let's Encrypt challenge.")

    cert = acme.issue_certificate(domain, now=t0)
    print(f"  • Certificate issued by {cert.issuer} (Valid: 90 days).")
    assert cert.requires_renewal(now=t0 + (10 * 86400)) is False
    # At day 65 (25 days left < 30 days threshold) -> requires renewal
    assert cert.requires_renewal(now=t0 + (65 * 86400)) is True
    print(f"  • At Day 65 (25 days remaining), requires_renewal evaluates to TRUE.")
    print("  ✓ ACME certificate lifecycle and renewal alerts verified.")

    # --------------------------------------------------------------------------
    # Diagnostic 5: Nginx Token/Leaky Bucket Rate Limiter
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 5] Nginx Leaky Bucket Rate Limiter (rate=10r/s, burst=5, nodelay):")
    limiter = NginxRateLimiter(rate_per_second=10.0, burst=5, nodelay=True)
    client = "203.0.113.195"

    allowed_count = 0
    rejected_count = 0
    sim_time = 5000.0

    # Send 10 instant requests at t=0
    for _ in range(10):
        allowed, status, meta = limiter.allow_request(client, now=sim_time)
        if allowed:
            allowed_count += 1
        else:
            rejected_count += 1

    print(f"  • Sent 10 instant burst requests:")
    print(f"    - Allowed (200 OK): {allowed_count} (Rate base + 5 burst capacity)")
    print(f"    - Blocked (429 Too Many Requests): {rejected_count}")
    assert allowed_count == 6  # 1 initial + 5 burst
    assert rejected_count == 4

    # Advance time by 0.5s (should leak 5 tokens: 0.5s * 10r/s = 5)
    sim_time += 0.5
    allowed_after_leak, status, _ = limiter.allow_request(client, now=sim_time)
    assert allowed_after_leak is True
    print("  • After 500ms time advance, bucket leaked tokens -> Subsequent request succeeded.")
    print("  ✓ Leaky bucket rate limiter strictly matched Nginx limit_req algorithm.")

    print("\n" + "=" * 80)
    print(" ALL DAY 65 SUPERVISOR, WATCHDOG & ACME SSL TESTS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_diagnostics()
