# Day 80: Rapid Revision & Active Recall Cheat Sheet

## 1. High-Yield Architectural Summary (5-Minute Scan)

- **Decoupled Architecture**:
  - **Backend Store (RDBMS / SQLite / PostgreSQL)**: Stores structured metadata (`experiments`, `runs`, `params`, `metrics`, `tags`, `registered_models`, `model_versions`).
  - **Artifact Store (S3 / GCS / Azure Blob / POSIX)**: Stores heavy serialized files (`model.joblib`, `MLmodel`, `requirements.txt`).
- **Relational Schema Core**:
  - `experiments`: `experiment_id (PK)`, `name (UNIQUE)`, `artifact_location`.
  - `runs`: `run_uuid (PK)`, `experiment_id (FK)`, `status`, `start_time`, `end_time`.
  - `params`: `(run_uuid, key) PK`, `value`.
  - `metrics`: `(run_uuid, key, step, timestamp) PK`, `value`.
  - `tags`: `(run_uuid, key) PK`, `value`.
- **Model Registry Evolution**:
  - **Legacy Stages**: `None` $\to$ `Staging` $\to$ `Production` $\to$ `Archived`.
  - **Modern Aliases (MLflow 2.0+)**: Dynamic pointers (`@champion`, `@challenger`, `@shadow`) pointing to immutable version numbers ($v1, v2\dots$).
- **Nested Runs**:
  - Parent run represents the global hyperparameter optimization study or pipeline.
  - Child runs link using the canonical tag `mlflow.parentRunId = <parent_run_uuid>`.
- **`MLmodel` Manifest**:
  - Universal metadata contract defining flavors (`python_function`, `sklearn`), model signatures, and cryptographic artifact checksums (`sha256_digest`).

---

## 2. MLflow Relational Schema Blueprint

```sql
-- Core Experiment Tracking Schema
CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    artifact_location TEXT NOT NULL,
    lifecycle_stage TEXT NOT NULL DEFAULT 'active',
    created_at_epoch_ms INTEGER NOT NULL
);

CREATE TABLE runs (
    run_uuid TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments (experiment_id),
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    start_time INTEGER NOT NULL,
    end_time INTEGER,
    git_commit TEXT NOT NULL,
    dataset_version TEXT NOT NULL
);

CREATE TABLE metrics (
    run_uuid TEXT NOT NULL REFERENCES runs (run_uuid),
    key TEXT NOT NULL,
    value REAL NOT NULL,
    timestamp INTEGER NOT NULL,
    step INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_uuid, key, step, timestamp)
);

CREATE INDEX idx_metrics_run_key ON metrics (run_uuid, key);
```

---

## 3. 10 Active Recall Flashcards

### Card 1
- **Q**: What are the two core persistence stores in MLflow, and what does each store?
- **A**: The **Backend Store** (relational database like PostgreSQL/SQLite) stores metadata, params, step metrics, and tags. The **Artifact Store** (object store like S3/GCS) stores model binaries, weight checkpoints, and figures.

### Card 2
- **Q**: Why is `mlflow.parentRunId` used during hyperparameter optimization?
- **A**: It links individual child trials to a top-level parent study run, preventing flat leaderboard clutter and enabling aggregate study-level metrics.

### Card 3
- **Q**: What makes `mlflow.pyfunc` the universal deployment interface?
- **A**: It wraps any ML framework (PyTorch, Scikit-Learn, ONNX) behind a standardized `.predict(dataframe_or_tensor)` Python API.

### Card 4
- **Q**: What is the primary operational advantage of Model Aliases over Model Stages?
- **A**: Aliases act as mutable named pointers (`@champion`, `@shadow`) to immutable versions, supporting multi-region routing, canary rollouts, and zero-downtime hot reloads.

### Card 5
- **Q**: Why must high-frequency step metrics use a composite index `(run_uuid, key)`?
- **A**: Without composite indexing, analytical queries fetching the latest metric per run result in full table scans across millions of metric rows, crashing dashboard queries.

### Card 6
- **Q**: How does wrapping runs in a Python context manager prevent "Zombie / Orphan Runs"?
- **A**: In `__exit__`, if an unhandled exception occurred, status is set to `FAILED`; otherwise `FINISHED`. This ensures runs are never left stranded in `RUNNING`.

### Card 7
- **Q**: What critical information is contained within an `MLmodel` manifest?
- **A**: Supported model flavors, runtime Python version, model input/output signature, artifact subpath, run UUID, and cryptographic SHA256 checksum.

### Card 8
- **Q**: What is the Champion vs Challenger evaluation rule implemented in production pipelines?
- **A**: The challenger is promoted to `@champion` only if its validation metric exceeds the incumbent champion's metric by at least a predefined threshold ($\Delta \ge 0.015$) without breaking latency SLAs.

### Card 9
- **Q**: How does SQLite handle concurrent writes from parallel Ray workers, and what is the remediation?
- **A**: SQLite locks the database file on writes, raising `OperationalError: database is locked`. Remediation: Configure `busy_timeout` and implement exponential backoff retry.

### Card 10
- **Q**: What three elements constitute complete cryptographic model lineage?
- **A**: 1) Git Commit SHA, 2) Dataset content hash (or DVC/Delta version), and 3) Model artifact SHA256 digest recorded in the manifest.
