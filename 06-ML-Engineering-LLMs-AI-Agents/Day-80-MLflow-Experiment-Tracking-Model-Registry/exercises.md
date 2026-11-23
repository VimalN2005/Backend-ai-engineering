# Day 80: Production Exercises — MLflow Experiment Tracking & Governance

Below are 3 enterprise-grade production engineering exercises covering automated promotion guardrails, cryptographic provenance auditing, and concurrent database connection resilience.

---

## Exercise 1: Multi-Metric Model Promotion Guardrail Gate

### Problem Statement
In production, a challenger model cannot simply improve an accuracy metric if it also causes catastrophic regression in inference latency. Implement a guardrail gate that promotes a challenger model to `@champion` ONLY if:
1. `val_f1_score >= champion_f1 + 0.010` (at least 1% absolute F1 improvement).
2. `p99_latency_ms <= 25.0` (strict inference latency SLA).
3. If criteria fail, the challenger is tagged as `rejected` and moved to `Archived`.

### Complete Solution & Verification Test

```python
"""
Exercise 1: Multi-Metric Model Promotion Guardrail
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ModelMetrics:
    version: int
    val_f1: float
    p99_latency_ms: float


class MultiMetricPromotionGate:
    def __init__(self, min_f1_delta: float = 0.010, max_latency_ms: float = 25.0):
        self.min_f1_delta = min_f1_delta
        self.max_latency_ms = max_latency_ms

    def evaluate(
        self,
        champion: Optional[ModelMetrics],
        challenger: ModelMetrics
    ) -> Dict[str, Any]:
        # Latency constraint check
        if challenger.p99_latency_ms > self.max_latency_ms:
            return {
                "promoted": False,
                "reason": f"Latency breach: {challenger.p99_latency_ms:.1f}ms > {self.max_latency_ms:.1f}ms limit",
                "target_stage": "Archived"
            }

        # If no incumbent champion exists, promote directly
        if champion is None:
            return {
                "promoted": True,
                "reason": "First model version promoted to champion",
                "target_stage": "Production"
            }

        # F1 Score Delta check
        delta_f1 = challenger.val_f1 - champion.val_f1
        if delta_f1 >= self.min_f1_delta:
            return {
                "promoted": True,
                "reason": f"F1 score improved by {delta_f1:+.4f} (Required: +{self.min_f1_delta:.4f})",
                "target_stage": "Production"
            }
        else:
            return {
                "promoted": False,
                "reason": f"F1 score delta {delta_f1:+.4f} below threshold +{self.min_f1_delta:.4f}",
                "target_stage": "Archived"
            }


# Unit Test Verification
if __name__ == "__main__":
    gate = MultiMetricPromotionGate(min_f1_delta=0.010, max_latency_ms=25.0)

    champ = ModelMetrics(version=1, val_f1=0.880, p99_latency_ms=18.5)

    # Candidate A: Better F1, but violates latency
    cand_a = ModelMetrics(version=2, val_f1=0.910, p99_latency_ms=32.0)
    res_a = gate.evaluate(champ, cand_a)
    assert not res_a["promoted"], "Should reject due to latency breach"
    print(f"Test A Passed: {res_a['reason']}")

    # Candidate B: Lower F1 improvement
    cand_b = ModelMetrics(version=3, val_f1=0.885, p99_latency_ms=19.0)
    res_b = gate.evaluate(champ, cand_b)
    assert not res_b["promoted"], "Should reject due to insufficient F1 delta"
    print(f"Test B Passed: {res_b['reason']}")

    # Candidate C: Meets all criteria
    cand_c = ModelMetrics(version=4, val_f1=0.898, p99_latency_ms=20.5)
    res_c = gate.evaluate(champ, cand_c)
    assert res_c["promoted"], "Should promote meeting all SLA criteria"
    print(f"Test C Passed: {res_c['reason']}")
    print("Exercise 1 Verification: All Assertions Passed!\n")
```

---

## Exercise 2: Cryptographic Lineage & Provenance Auditor

### Problem Statement
Build an enterprise verification auditor that checks whether a registered model artifact has been tampered with or corrupted after training. The auditor must:
1. Re-compute the SHA256 digest of the serialized model binary on disk.
2. Compare the live digest against the recorded digest inside the `MLmodel` manifest.
3. Verify that the logged `git_commit` is a 40-character hexadecimal string or valid commit reference.

### Complete Solution & Verification Test

```python
"""
Exercise 2: Cryptographic Lineage & Integrity Auditor
"""
import hashlib
import json
import os
import re
import tempfile


class ArtifactIntegrityAuditor:
    @staticmethod
    def audit_model_bundle(artifact_dir: str) -> Dict[str, Any]:
        manifest_path = os.path.join(artifact_dir, "MLmodel")
        if not os.path.exists(manifest_path):
            return {"valid": False, "error": "MLmodel manifest missing"}

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        expected_sha = manifest.get("sha256_digest")
        if not expected_sha:
            return {"valid": False, "error": "sha256_digest missing from manifest"}

        model_filename = manifest.get("flavors", {}).get("python_function", {}).get("model_path", "model.joblib")
        model_path = os.path.join(artifact_dir, model_filename)
        if not os.path.exists(model_path):
            return {"valid": False, "error": f"Model binary '{model_filename}' not found on disk"}

        # Compute live SHA256
        hasher = hashlib.sha256()
        with open(model_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        computed_sha = hasher.hexdigest()

        if computed_sha != expected_sha:
            return {
                "valid": False,
                "error": f"Digest mismatch! Expected {expected_sha}, computed {computed_sha}",
            }

        return {
            "valid": True,
            "run_id": manifest.get("run_id"),
            "computed_sha256": computed_sha,
        }


# Unit Test Verification
if __name__ == "__main__":
    test_dir = tempfile.mkdtemp()
    model_file = os.path.join(test_dir, "model.joblib")
    with open(model_file, "wb") as f:
        f.write(b"SAMPLE_SERIALIZED_MODEL_WEIGHTS_42")

    hasher = hashlib.sha256()
    with open(model_file, "rb") as f:
        hasher.update(f.read())
    valid_digest = hasher.hexdigest()

    manifest_file = os.path.join(test_dir, "MLmodel")
    manifest_data = {
        "artifact_path": "model",
        "flavors": {"python_function": {"model_path": "model.joblib"}},
        "run_id": "test_run_12345",
        "sha256_digest": valid_digest,
    }
    with open(manifest_file, "w") as f:
        json.dump(manifest_data, f)

    # Audit Valid Bundle
    audit_res = ArtifactIntegrityAuditor.audit_model_bundle(test_dir)
    assert audit_res["valid"], f"Expected valid bundle: {audit_res}"
    print(f"Audit Valid Test: Passed with SHA256 {audit_res['computed_sha256'][:16]}...")

    # Tamper with file
    with open(model_file, "wb") as f:
        f.write(b"MALICIOUS_TAMPERED_WEIGHTS")

    tamper_res = ArtifactIntegrityAuditor.audit_model_bundle(test_dir)
    assert not tamper_res["valid"], "Tampered file should fail integrity check"
    print(f"Audit Tamper Test: Successfully detected! Reason: {tamper_res['error']}")
    print("Exercise 2 Verification: All Assertions Passed!\n")
```

---

## Exercise 3: Resilient SQLite Connection Pool with Exponential Backoff Retry

### Problem Statement
When running multi-process hyperparameter sweeps (e.g., 8 parallel Ray or Optuna workers logging to a shared SQLite tracking database), simultaneous writes can produce `sqlite3.OperationalError: database is locked`. Implement a resilient connection wrapper that executes writes with randomized exponential backoff retry.

### Complete Solution & Verification Test

```python
"""
Exercise 3: Resilient SQLite Connection with Retry Backoff
"""
import random
import sqlite3
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class ResilientSQLiteExecutor:
    def __init__(self, db_path: str, max_retries: int = 5, base_delay: float = 0.05):
        self.db_path = db_path
        self.max_retries = max_retries
        self.base_delay = base_delay

    def execute_with_retry(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        attempt = 0
        while True:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.execute("PRAGMA busy_timeout = 3000;")
                res = operation(conn)
                conn.commit()
                return res
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < self.max_retries:
                    attempt += 1
                    sleep_time = self.base_delay * (2 ** attempt) + random.uniform(0.01, 0.05)
                    time.sleep(sleep_time)
                else:
                    raise
            finally:
                if conn:
                    conn.close()


# Unit Test Verification
if __name__ == "__main__":
    db_file = os.path.join(tempfile.gettempdir(), f"retry_test_{int(time.time())}.db")
    executor = ResilientSQLiteExecutor(db_file)

    def init_op(conn: sqlite3.Connection):
        conn.execute("CREATE TABLE IF NOT EXISTS test_tbl (id INTEGER PRIMARY KEY, val TEXT);")

    def insert_op(conn: sqlite3.Connection):
        conn.execute("INSERT INTO test_tbl (val) VALUES ('metric_value_100');")

    executor.execute_with_retry(init_op)
    executor.execute_with_retry(insert_op)

    def query_op(conn: sqlite3.Connection):
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM test_tbl")
        return cur.fetchone()[0]

    count = executor.execute_with_retry(query_op)
    assert count == 1, f"Expected 1 record, got {count}"
    print(f"Exercise 3 Verification: Resilient query executed successfully with count={count}!\n")
```
