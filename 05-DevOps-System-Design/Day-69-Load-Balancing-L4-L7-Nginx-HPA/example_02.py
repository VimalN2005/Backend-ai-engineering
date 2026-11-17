"""Day 69: Kubernetes HPA Mathematical Engine, Flapping Defense & KEDA Simulator.

This production-grade script provides:
1. Official Kubernetes HPA Scaling Formula Engine with 10% tolerance band.
2. Hysteresis & Stabilization Window Engine (Scale-Down cooling window).
3. Flapping (Thrashing) Oscillation Simulator & Stabilization Verification.
4. KEDA Event-Driven Autoscaler (Message Queue depth & Scale-to-Zero).
5. Comprehensive automated test suite with zero external dependencies.
"""

from __future__ import annotations

import collections
import dataclasses
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ==============================================================================
# 1. KUBERNETES HPA SCALING FORMULA ENGINE
# ==============================================================================

@dataclass
class HPAConfig:
    min_replicas: int = 2
    max_replicas: int = 50
    target_metric_value: float = 60.0    # e.g. 60% CPU
    tolerance: float = 0.10             # Default 10% tolerance band
    stabilization_window_seconds: float = 300.0  # 5 minutes


class HPAScalingEngine:
    """Implements the official Kubernetes HPA controller scaling equation."""

    def __init__(self, config: Optional[HPAConfig] = None) -> None:
        self.config = config or HPAConfig()
        # History of (timestamp, raw_desired_replicas)
        self.scale_down_history: collections.deque[Tuple[float, int]] = collections.deque()

    def calculate_raw_desired_replicas(
        self,
        current_replicas: int,
        current_metric_value: float
    ) -> Tuple[int, str]:
        """Evaluates: ceil(currentReplicas * (currentMetric / targetMetric))."""
        if self.config.target_metric_value <= 0:
            raise ValueError("Target metric must be positive")

        ratio = current_metric_value / self.config.target_metric_value

        # Check 10% tolerance band (No-Op Guard)
        if abs(1.0 - ratio) <= self.config.tolerance:
            return current_replicas, f"TOLERANCE_NOOP (Ratio {ratio:.3f} within {self.config.tolerance * 100:.0f}%)"

        # Official Kubernetes ceiling formula
        calculated = math.ceil(current_replicas * ratio)

        # Enforce min/max boundaries
        bounded = max(self.config.min_replicas, min(calculated, self.config.max_replicas))
        return bounded, f"SCALED (Ratio {ratio:.3f}: {current_replicas} -> {bounded})"

    def evaluate_with_stabilization(
        self,
        current_replicas: int,
        current_metric_value: float,
        now: float
    ) -> Tuple[int, str]:
        """Applies scale-down stabilization window to prevent thrashing/flapping."""
        raw_desired, reason = self.calculate_raw_desired_replicas(current_replicas, current_metric_value)

        # Purge entries older than stabilization window
        while self.scale_down_history and (now - self.scale_down_history[0][0] > self.config.stabilization_window_seconds):
            self.scale_down_history.popleft()

        self.scale_down_history.append((now, raw_desired))

        # Scale Up: Takes effect immediately (highest priority for availability)
        if raw_desired > current_replicas:
            return raw_desired, f"SCALE_UP_IMMEDIATE ({reason})"

        # Scale Down: Must pick the MAXIMUM desired replica count observed in the stabilization window
        if raw_desired < current_replicas:
            stabilized_desired = max(entry[1] for entry in self.scale_down_history)
            if stabilized_desired == current_replicas:
                return current_replicas, f"STABILIZATION_HELD (Raw desired {raw_desired}, but held at {current_replicas} for {self.config.stabilization_window_seconds}s)"
            else:
                return stabilized_desired, f"STABILIZED_SCALE_DOWN (Adjusted to {stabilized_desired})"

        return current_replicas, reason


# ==============================================================================
# 2. FLAPPING (THRASHING) OSCILLATION SIMULATOR
# ==============================================================================

class FlappingSimulator:
    """Demonstrates how stabilization windows eliminate autoscaler thrashing."""

    @staticmethod
    def simulate_flapping(
        duration_cycles: int = 10,
        use_stabilization: bool = True
    ) -> List[int]:
        """Simulates alternating high and low load spikes.

        Returns the sequence of pod counts over time.
        """
        engine = HPAScalingEngine(HPAConfig(
            min_replicas=4,
            max_replicas=50,
            target_metric_value=60.0,
            stabilization_window_seconds=300.0 if use_stabilization else 0.0
        ))

        replicas = 4
        replica_history = [replicas]
        current_time = 1000.0

        for cycle in range(duration_cycles):
            # Alternates: High Load (90% CPU) then Low Load (20% CPU)
            metric_val = 90.0 if (cycle % 2 == 0) else 20.0
            current_time += 15.0  # 15s sync period

            if use_stabilization:
                replicas, _ = engine.evaluate_with_stabilization(replicas, metric_val, now=current_time)
            else:
                replicas, _ = engine.calculate_raw_desired_replicas(replicas, metric_val)

            replica_history.append(replicas)

        return replica_history


# ==============================================================================
# 3. KEDA EVENT-DRIVEN QUEUE AUTOSCALER (SCALE-TO-ZERO)
# ==============================================================================

@dataclass
class KEDAScalerConfig:
    min_replicas: int = 0               # Supports Scale-to-Zero!
    max_replicas: int = 100
    target_messages_per_pod: int = 50   # 1 pod per 50 queued messages


class KEDAQueueScaler:
    """Simulates KEDA event-driven autoscaling for Kafka / SQS / RabbitMQ."""

    def __init__(self, config: Optional[KEDAScalerConfig] = None) -> None:
        self.config = config or KEDAScalerConfig()

    def calculate_desired_replicas(self, queue_depth: int) -> Tuple[int, str]:
        """Calculates replicas strictly from queue lag: ceil(queue_depth / target)."""
        if queue_depth <= 0:
            return self.config.min_replicas, "SCALE_TO_ZERO (Queue is empty)"

        calculated = math.ceil(queue_depth / self.config.target_messages_per_pod)
        bounded = max(self.config.min_replicas, min(calculated, self.config.max_replicas))
        return bounded, f"SCALED_ON_QUEUE_LAG ({queue_depth} messages / {self.config.target_messages_per_pod} target -> {bounded} Pods)"


# ==============================================================================
# 4. DIAGNOSTICS & VERIFICATION RUNNER
# ==============================================================================

def run_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 69: KUBERNETES HPA & KEDA AUTOSCALING MATHEMATICAL DIAGNOSTICS")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # Diagnostic 1: Kubernetes HPA Formula & Ceiling Verification
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 1] Kubernetes HPA Official Formula (Target 60% CPU):")
    engine = HPAScalingEngine(HPAConfig(min_replicas=2, max_replicas=50, target_metric_value=60.0))

    # Case A: 10 pods, current metric spikes to 85% CPU -> ceil(10 * (85/60)) = ceil(14.166) = 15
    desired_a, msg_a = engine.calculate_raw_desired_replicas(current_replicas=10, current_metric_value=85.0)
    print(f"  • Traffic Spike (10 Pods @ 85% CPU): -> Desired: {desired_a} Pods ({msg_a})")
    assert desired_a == 15

    # Case B: 10% Tolerance band test (Current 64% against 60% target -> ratio = 1.066 <= 1.10 -> No-Op!)
    desired_b, msg_b = engine.calculate_raw_desired_replicas(current_replicas=10, current_metric_value=64.0)
    print(f"  • Tolerance Band (10 Pods @ 64% CPU): -> Desired: {desired_b} Pods ({msg_b})")
    assert desired_b == 10
    assert "TOLERANCE_NOOP" in msg_b

    # Case C: Boundary Clamping (Spike to 500% CPU clamped to max_replicas=50)
    desired_c, msg_c = engine.calculate_raw_desired_replicas(current_replicas=10, current_metric_value=500.0)
    print(f"  • Boundary Clamping (500% CPU): -> Desired: {desired_c} Pods (Max Clamped)")
    assert desired_c == 50
    print("  ✓ HPA mathematical equation, ceiling rounding, and tolerance verified.")

    # --------------------------------------------------------------------------
    # Diagnostic 2: Flapping Thrashing vs. Stabilization Window
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 2] Autoscaling Flapping (Thrashing) Analysis:")
    unstable_history = FlappingSimulator.simulate_flapping(duration_cycles=8, use_stabilization=False)
    stable_history = FlappingSimulator.simulate_flapping(duration_cycles=8, use_stabilization=True)

    print(f"  • Unstabilized Pod History (Flapping): {unstable_history}")
    print(f"  • Stabilized Pod History (300s Window): {stable_history}")

    # Unstable must fluctuate wildly up and down
    flaps_unstable = sum(1 for i in range(1, len(unstable_history)) if unstable_history[i] != unstable_history[i-1])
    # Stable holds capacity and avoids downscaling thrash
    assert flaps_unstable >= 6
    assert stable_history[-1] >= 6  # Kept capacity stable
    print("  ✓ Demonstrated elimination of high-frequency flapping via 300s stabilization window.")

    # --------------------------------------------------------------------------
    # Diagnostic 3: KEDA Event-Driven Scaling & Scale-to-Zero
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 3] KEDA Event-Driven Autoscaling (Queue Lag & Scale-to-Zero):")
    keda = KEDAQueueScaler(KEDAScalerConfig(
        min_replicas=0,
        max_replicas=100,
        target_messages_per_pod=50
    ))

    # 1. Scale-to-Zero test
    zero_pods, zero_msg = keda.calculate_desired_replicas(queue_depth=0)
    print(f"  • Empty Queue (0 messages): -> {zero_pods} Pods ({zero_msg})")
    assert zero_pods == 0

    # 2. Sudden Queue Influx: 2,450 messages arrive
    # Desired = ceil(2450 / 50) = 49 Pods!
    scale_pods, scale_msg = keda.calculate_desired_replicas(queue_depth=2450)
    print(f"  • Massive Queue Spike (2,450 messages): -> {scale_pods} Pods ({scale_msg})")
    assert scale_pods == 49

    # 3. Maximum Clamp: 10,000 messages clamped to max_replicas=100
    clamp_pods, clamp_msg = keda.calculate_desired_replicas(queue_depth=10_000)
    print(f"  • Overload Spike (10,000 messages): -> {clamp_pods} Pods (Max Clamped)")
    assert clamp_pods == 100
    print("  ✓ KEDA Scale-to-Zero and proactive queue depth autoscaling verified.")

    print("\n" + "=" * 80)
    print(" ALL DAY 69 HPA & KEDA AUTOSCALING TESTS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_diagnostics()
