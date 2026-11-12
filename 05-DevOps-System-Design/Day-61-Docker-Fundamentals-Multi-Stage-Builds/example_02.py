"""
Day 61: Docker Fundamentals, Dockerfile Best Practices & Multi-Stage Builds
=============================================================================
Production-Grade Pattern: Container Runtime Simulation, PID 1 Supervisor,
Cgroups Resource Throttling & Graceful Shutdown Watchdog

This module simulates the Linux kernel primitives and runtime mechanics of an
OCI container running an asynchronous Python backend service:
1. PID Namespace & Isolated Process Tree.
2. PID 1 Zombie Reaping vs. Defunct Process Accumulation.
3. Signal Forwarding (SIGTERM vs SIGKILL) & Graceful HTTP Drain.
4. Cgroups v2 Memory Controller & Kernel OOM-Killer (Exit Code 137).
5. Container Healthcheck State Machine (STARTING -> HEALTHY -> UNHEALTHY).
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import time
from typing import Dict, List, Optional, Set


# ==============================================================================
# 1. PROCESS DEFINITIONS & PID NAMESPACE
# ==============================================================================

class ProcessState(enum.Enum):
    RUNNING = "RUNNING"
    SLEEPING = "SLEEPING"
    ZOMBIE = "ZOMBIE (<defunct>)"
    TERMINATED = "TERMINATED"


@dataclasses.dataclass
class ContainerProcess:
    """Represents a process inside an isolated container PID namespace."""
    pid: int
    parent_pid: int
    command: str
    state: ProcessState = ProcessState.RUNNING
    allocated_memory_mb: float = 10.0
    exit_code: Optional[int] = None
    registered_signal_handlers: Set[str] = dataclasses.field(default_factory=set)


# ==============================================================================
# 2. PID 1 INIT SUPERVISOR & ZOMBIE REAPER (TINI / DUMB-INIT EMULATION)
# ==============================================================================

class ContainerPIDNamespace:
    """
    Simulates a Linux PID namespace. Evaluates whether PID 1 is a naive application
    (which drops signals and ignores zombie children) or an init supervisor like
    tini/dumb-init (which reaps zombies and forwards signals).
    """

    def __init__(self, use_init_supervisor: bool = True):
        self.use_init_supervisor = use_init_supervisor
        self.processes: Dict[int, ContainerProcess] = {}
        self.next_pid = 1
        self.zombie_count = 0
        self.reaped_count = 0

        # Initialize PID 1
        if self.use_init_supervisor:
            self._spawn_process(
                parent_pid=0,
                command="/usr/bin/tini --",
                memory_mb=1.5,
                handlers={"SIGTERM", "SIGINT", "SIGCHLD"},
            )
        else:
            self._spawn_process(
                parent_pid=0,
                command="python main.py",
                memory_mb=45.0,
                handlers=set(),  # Raw Python often lacks explicit SIGTERM forwarding
            )

    def _spawn_process(
        self,
        parent_pid: int,
        command: str,
        memory_mb: float = 20.0,
        handlers: Optional[Set[str]] = None,
    ) -> ContainerProcess:
        pid = self.next_pid
        self.next_pid += 1
        proc = ContainerProcess(
            pid=pid,
            parent_pid=parent_pid,
            command=command,
            allocated_memory_mb=memory_mb,
            registered_signal_handlers=handlers or set(),
        )
        self.processes[pid] = proc
        return proc

    def spawn_worker(self, command: str, memory_mb: float = 35.0) -> ContainerProcess:
        """Spawns an application child process (e.g. uvicorn worker)."""
        pid_1 = self.processes[1]
        parent = 1 if self.use_init_supervisor else 0
        return self._spawn_process(parent_pid=parent, command=command, memory_mb=memory_mb)

    def terminate_child(self, pid: int, exit_code: int = 0) -> None:
        """
        Simulates a child process exiting.
        If parent does not issue waitpid(), child enters ZOMBIE state.
        If init supervisor is present, it receives SIGCHLD and reaps the zombie!
        """
        if pid not in self.processes:
            return

        proc = self.processes[pid]
        proc.exit_code = exit_code

        if self.use_init_supervisor:
            # Init supervisor immediately reaps the process
            proc.state = ProcessState.TERMINATED
            self.reaped_count += 1
        else:
            # Naive container: Child becomes zombie because PID 1 ignores SIGCHLD
            proc.state = ProcessState.ZOMBIE
            self.zombie_count += 1

    def send_signal(self, sig: str) -> Dict[str, Any]:
        """
        Simulates 'docker stop' which issues SIGTERM to PID 1.
        Evaluates whether signal reaches worker processes.
        """
        pid_1 = self.processes[1]
        results = {
            "signal": sig,
            "pid_1_handled": False,
            "forwarded_to_children": False,
            "graceful_shutdown": False,
        }

        if sig not in pid_1.registered_signal_handlers and not self.use_init_supervisor:
            # Linux Kernel Rule: Unhandled signals to PID 1 are discarded!
            results["pid_1_handled"] = False
            results["reason"] = "Signal discarded by kernel: PID 1 lacks registered signal handler"
            return results

        results["pid_1_handled"] = True

        if self.use_init_supervisor:
            # Tini / Dumb-init forwards SIGTERM to entire process group
            results["forwarded_to_children"] = True
            for pid, proc in list(self.processes.items()):
                if pid > 1 and proc.state == ProcessState.RUNNING:
                    proc.state = ProcessState.TERMINATED
                    proc.exit_code = 0
            pid_1.state = ProcessState.TERMINATED
            results["graceful_shutdown"] = True
            results["reason"] = "Init supervisor forwarded SIGTERM to process group and reaped children"
        else:
            results["reason"] = "PID 1 terminated without forwarding signal to background child processes"

        return results


# ==============================================================================
# 3. CGROUPS V2 RESOURCE CONTROLLER & OOM KILLER
# ==============================================================================

class CgroupMemoryController:
    """
    Simulates Linux Control Groups v2 memory management (memory.max, memory.current).
    Enforces kernel Out-Of-Memory (OOM) Killer when memory consumption exceeds limit.
    """

    def __init__(self, memory_limit_mb: float):
        self.memory_limit_mb = memory_limit_mb
        self.current_usage_mb = 0.0
        self.oom_kill_events = 0

    def allocate(self, amount_mb: float) -> Tuple[bool, str]:
        """
        Simulates memory allocation request.
        Returns: (Success: bool, Reason: str)
        """
        projected = self.current_usage_mb + amount_mb
        if projected > self.memory_limit_mb:
            self.oom_kill_events += 1
            return False, f"OOM KILLER INVOKED: Requested {amount_mb:.1f} MB (Total {projected:.1f} MB > Limit {self.memory_limit_mb:.1f} MB). Exit Code 137 (SIGKILL)."

        self.current_usage_mb = projected
        return True, f"Allocated {amount_mb:.1f} MB. Current usage: {self.current_usage_mb:.1f}/{self.memory_limit_mb:.1f} MB"

    def release(self, amount_mb: float) -> None:
        self.current_usage_mb = max(0.0, self.current_usage_mb - amount_mb)


# ==============================================================================
# 4. CONTAINER HEALTHCHECK STATE MACHINE
# ==============================================================================

class HealthStatus(enum.Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class ContainerHealthcheckWatcher:
    """
    Simulates Docker's internal HEALTHCHECK daemon.
    Manages state transitions based on interval, timeout, start_period, and retries.
    """

    def __init__(self, start_period_sec: float = 2.0, max_retries: int = 3):
        self.start_period_sec = start_period_sec
        self.max_retries = max_retries
        self.consecutive_failures = 0
        self.start_time = time.perf_counter()
        self.status = HealthStatus.STARTING

    def record_probe(self, is_success: bool) -> HealthStatus:
        """
        Evaluates a single health probe execution (e.g. curl http://localhost:8000/health).
        """
        elapsed = time.perf_counter() - self.start_time

        if is_success:
            self.consecutive_failures = 0
            self.status = HealthStatus.HEALTHY
            return self.status

        # Probe failed
        if elapsed < self.start_period_sec:
            # During start-period, failures are tolerated
            return self.status

        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_retries:
            self.status = HealthStatus.UNHEALTHY

        return self.status


# ==============================================================================
# 5. INTEGRATION DIAGNOSTICS & BENCHMARK HARNESS
# ==============================================================================

def run_container_runtime_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 61 CONTAINER RUNTIME DIAGNOSTICS: PID 1, CGROUPS & SIGNALS")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 1: THE PID 1 ZOMBIE ACCUMULATION TEST
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 1] Testing PID 1 Zombie Reaping vs Defunct Accumulation...")

    # Case A: Naive container (No tini/dumb-init)
    naive_ns = ContainerPIDNamespace(use_init_supervisor=False)
    for i in range(5):
        w = naive_ns.spawn_worker(f"worker_{i}", memory_mb=15.0)
        naive_ns.terminate_child(w.pid, exit_code=0)

    print(f"  Naive Container (Direct Python PID 1):")
    print(f"    Total Zombie Processes (<defunct>) : {naive_ns.zombie_count}")
    print(f"    Total Reaped Processes             : {naive_ns.reaped_count}")
    assert naive_ns.zombie_count == 5, "Expected 5 zombie processes in naive container"

    # Case B: Hardened container (With tini init supervisor)
    hardened_ns = ContainerPIDNamespace(use_init_supervisor=True)
    for i in range(5):
        w = hardened_ns.spawn_worker(f"worker_{i}", memory_mb=15.0)
        hardened_ns.terminate_child(w.pid, exit_code=0)

    print(f"\n  Hardened Container (tini Supervisor PID 1):")
    print(f"    Total Zombie Processes (<defunct>) : {hardened_ns.zombie_count}")
    print(f"    Total Reaped Processes             : {hardened_ns.reaped_count}")
    assert hardened_ns.zombie_count == 0, "No zombies should exist with tini init"
    assert hardened_ns.reaped_count == 5, "All 5 children must be cleanly reaped"
    print("  ✓ Zombie Reaping Verified: tini prevents kernel process table exhaustion!")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 2: SIGNAL FORWARDING (SIGTERM ON DOCKER STOP)
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 2] Testing 'docker stop' SIGTERM Forwarding...")

    # Naive container receives SIGTERM
    naive_res = naive_ns.send_signal("SIGTERM")
    print(f"  Naive Container Signal Result:")
    print(f"    PID 1 Handled    : {naive_res['pid_1_handled']}")
    print(f"    Forwarded to App : {naive_res['forwarded_to_children']}")
    print(f"    Outcome          : {naive_res['reason']}")
    assert naive_res["graceful_shutdown"] is False

    # Hardened container receives SIGTERM
    hardened_res = hardened_ns.send_signal("SIGTERM")
    print(f"\n  Hardened Container Signal Result:")
    print(f"    PID 1 Handled    : {hardened_res['pid_1_handled']}")
    print(f"    Forwarded to App : {hardened_res['forwarded_to_children']}")
    print(f"    Outcome          : {hardened_res['reason']}")
    assert hardened_res["graceful_shutdown"] is True
    print("  ✓ Graceful Signal Propagation Verified: Avoids 10-second timeout SIGKILL!")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 3: CGROUPS V2 MEMORY LIMIT & OOM-KILLER
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 3] Testing Cgroups v2 Memory Limit & OOM-Killer...")
    cgroup = CgroupMemoryController(memory_limit_mb=256.0)

    ok1, msg1 = cgroup.allocate(100.0)
    print(f"  Alloc 1 (100 MB): {msg1}")
    assert ok1 is True

    ok2, msg2 = cgroup.allocate(120.0)
    print(f"  Alloc 2 (120 MB): {msg2}")
    assert ok2 is True

    # Exceed 256MB ceiling (220 + 50 = 270 MB > 256 MB)
    ok3, msg3 = cgroup.allocate(50.0)
    print(f"  Alloc 3 ( 50 MB): {msg3}")
    assert ok3 is False
    assert cgroup.oom_kill_events == 1
    print("  ✓ Cgroup OOM Killer Verified: Process killed with Exit Code 137.")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 4: HEALTHCHECK STATE MACHINE TRANSITIONS
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 4] Testing Container Healthcheck State Transitions...")
    watcher = ContainerHealthcheckWatcher(start_period_sec=0.2, max_retries=3)

    assert watcher.status == HealthStatus.STARTING
    print(f"  T+0.0s: State = {watcher.status.value}")

    # Initial success marks container healthy
    state = watcher.record_probe(is_success=True)
    assert state == HealthStatus.HEALTHY
    print(f"  Probe 1 (Success): State = {state.value}")

    # Simulate 3 consecutive health probe failures (e.g. database down)
    time.sleep(0.25)  # Advance beyond start-period
    _ = watcher.record_probe(is_success=False)
    print(f"  Probe 2 (Failure 1/3): State = {watcher.status.value}")

    _ = watcher.record_probe(is_success=False)
    print(f"  Probe 3 (Failure 2/3): State = {watcher.status.value}")

    final_state = watcher.record_probe(is_success=False)
    print(f"  Probe 4 (Failure 3/3): State = {final_state.value}")
    assert final_state == HealthStatus.UNHEALTHY
    print("  ✓ Healthcheck State Machine Verified: Transitions to UNHEALTHY on threshold breach!")

    print("\n" + "=" * 80)
    print(" ALL DAY 61 RUNTIME DIAGNOSTIC SUITES COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_container_runtime_diagnostics()
