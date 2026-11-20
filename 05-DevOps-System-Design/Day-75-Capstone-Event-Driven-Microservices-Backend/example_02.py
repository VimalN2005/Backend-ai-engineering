"""
Day 75: Capstone Resilience, Chaos Engineering & Stress Benchmark
=================================================================
Architecture & Principles Demonstrated:
1. Chaos Engineering Harness:
   - Random transient service brownouts (simulated downstream 503s).
   - Network latency jitter injection.
2. Production Defense Integration:
   - Circuit Breaker fast-fail.
   - Semaphore Bulkhead concurrency cap.
   - Full Jitter retry backoff.
"""

from __future__ import annotations
import random
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


class ChaosFailureException(Exception):
    pass


class ChaosInjectionProxy:
    """Injects artificial latency and random transient failures to test resilience."""
    def __init__(self, failure_rate_pct: float = 15.0, min_latency_ms: float = 5.0, max_latency_ms: float = 30.0):
        self.failure_rate_pct = failure_rate_pct
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max_latency_ms

    def invoke(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        # Simulate network jitter
        jitter_ms = random.uniform(self.min_latency_ms, self.max_latency_ms)
        time.sleep(jitter_ms / 1000.0)

        # Inject failure
        if random.uniform(0.0, 100.0) < self.failure_rate_pct:
            raise ChaosFailureException("Chaos monkey injected transient 503 Service Unavailable")

        return func(*args, **kwargs)
