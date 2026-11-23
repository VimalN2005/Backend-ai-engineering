# Day 80: Critical MLflow Antipatterns & Production Remediations

Enterprise experiment tracking and model registries form the audit backbone of production machine learning systems. Flaws in tracking schemas, artifact storage tiers, or promotion workflows create unrecoverable model lineage debt, regulatory compliance violations, and severe production outages.

Below are 7 critical production antipatterns, their root causes, and production-tested remediations.

---

## 1. Storing Model Artifacts Directly in the Tracking RDBMS (Blob Storage Exhaustion)

### The Mistake
Saving serialized model weights (`.bin`, `.onnx`, `.pt`, `.joblib`) directly into relational backend database tables (`VARCHAR` / `BYTEA` / `BLOB` columns) rather than utilizing dedicated S3/GCS/POSIX object storage.

```python
# ANTIPATTERN: Attempting to store binary model directly into Postgres/SQLite
with engine.connect() as conn:
    conn.execute(
        text("INSERT INTO runs (run_uuid, model_blob) VALUES (:uuid, :blob)"),
        {"uuid": run_id, "blob": pickle.dumps(model_weights)}  # Bloats DB backups & freezes connections
    )
```

### Why It Fails
- RDBMS performance rapidly collapses due to table bloat, memory cache pollution, and lock escalation during parallel model saves.
- Automated database backups (WAL replication, pg_dump) balloon from megabytes to hundreds of gigabytes, causing replication lag and timeout errors.

### Production Remediation
Strictly decouple the **metadata store** from the **artifact store**:
```python
# PRODUCTION PATTERN: Store metadata in RDBMS, stream binary artifact to Object Store
client.log_model_artifact(
    run_uuid=run_id,
    model_obj=model,
    artifact_subpath="models/fraud_detector",
    flavor="sklearn"
)
# Metadata row only records: 's3://prod-mlflow-artifacts/experiments/4/runs/run_id/artifacts/model'
```

---

## 2. Unversioned Model Overwriting Without Deployment Aliases

### The Mistake
Overwriting model files in-place on shared network storage (e.g. `s3://models/latest_model.pkl`) or hardcoding model versions in production serving microservices without an immutable Model Registry.

```python
# ANTIPATTERN: In-place overwrite breaks rollbacks and auditing
joblib.dump(model, "s3://prod-bucket/fraud_model_latest.pkl")
```

### Why It Fails
- If a corrupted or overfitted model is pushed, existing instances load differing model weights unpredictably.
- Zero auditability: impossible to determine which git commit, training data split, or validation metric generated the serving model.

### Production Remediation
Use immutable semantic versioning ($v1, v2\dots$) coupled with dynamic deployment aliases (`@champion`, `@challenger`):
```python
# PRODUCTION PATTERN: Assign aliases to immutable versions
registry.create_model_version("FraudClassifier", run_id=run.uuid, artifact_source=art_uri)
registry.set_model_version_alias("FraudClassifier", alias="champion", version=3)

# Serving microservice resolves the pointer dynamically:
active_model = registry.get_model_version_by_alias("FraudClassifier", alias="champion")
```

---

## 3. In-Sample Metric Logging Disguised as Generalization Metrics

### The Mistake
Logging evaluation metrics computed on training or resubstituted data without isolating holdout test splits, creating false confidence in model performance.

```python
# ANTIPATTERN: Logging training score as validation metric
y_train_pred = model.predict(X_train)
run.log_metric("roc_auc", roc_auc_score(y_train, y_train_pred))  # Overfitting concealed!
```

### Why It Fails
- Complex non-linear models (e.g., Random Forests, XGBoost) achieve near 1.0 train ROC-AUC while severely degrading on out-of-distribution production traffic.
- Automated promotion gates trigger erroneously on overfitted candidates.

### Production Remediation
Enforce standardized metric namespaces and strict data splits:
```python
# PRODUCTION PATTERN: Explicit namespace separation
run.log_metric("train_loss", train_loss, step=epoch)
run.log_metric("val_roc_auc", roc_auc_score(y_val, y_val_pred), step=epoch)
run.log_metric("test_holdout_roc_auc", roc_auc_score(y_test, y_test_pred))
```

---

## 4. Missing Run Lineage: Ephemeral Datasets and Untracked Git SHAs

### The Mistake
Logging hyperparameters and metrics without recording the immutable Git Commit SHA, dataset content hash, and Python environment dependencies.

```python
# ANTIPATTERN: Run is completely un-reproducible
run.log_param("learning_rate", 0.05)
# No git commit, no dataset version, no environment hash logged!
```

### Why It Fails
- Six months later, regulatory auditors or on-call engineers cannot reproduce the model weights from source code.
- "Works on my machine" syndrome during serving runtime containerization.

### Production Remediation
Mandate cryptographic provenance tracking on every run:
```python
run.set_tag("git_commit_sha", os.popen("git rev-parse HEAD").read().strip())
run.set_tag("dataset_sha256", compute_dataset_hash("data/training_split.parquet"))
run.set_tag("python_version", sys.version)
```

---

## 5. Unindexed High-Frequency Metric Logging Freezing Tracking Databases

### The Mistake
Logging per-batch training loss (e.g., 100 times per second across 64 GPU workers) directly into an unindexed or under-provisioned tracking database.

```python
# ANTIPATTERN: Logging millions of metrics synchronously per epoch
for batch_idx, loss in enumerate(batches):
    client.log_metric("batch_loss", loss, step=batch_idx)  # Locks SQLite / exhausts Postgres connections
```

### Why It Fails
- Relational databases exhaust connection pools and disk I/O under millions of small INSERT statements.
- Leaderboard dashboard queries time out.

### Production Remediation
1. Buffer and log metrics epoch-wise or at sampled intervals (e.g., every 50 or 100 steps).
2. Utilize composite indices: `CREATE INDEX idx_metrics_run_key ON metrics (run_uuid, key);`.
3. Use asynchronous background metric loggers with batched `INSERT INTO metrics ... VALUES (...)` bulk operations.

---

## 6. Direct Promotion Without Automated Challenger Validation Gates

### The Mistake
Manually clicking "Promote to Production" in the UI without programmatic regression checks against the incumbent production model.

### Why It Fails
- A new model might boast higher overall accuracy but catastrophic false-positive spikes on high-value demographic cohorts or edge cases.
- Human error in selecting the wrong version under high operational stress.

### Production Remediation
Implement automated gatekeeper scripts that execute statistical hypothesis testing and threshold deltas:
```python
# PRODUCTION PATTERN: Programmatic Champion vs Challenger Promotion Gate
if challenger_auc >= champion_auc + 0.015:
    registry.transition_model_version_stage("RiskModel", challenger_v, "Production", archive_existing=True)
    registry.set_model_version_alias("RiskModel", "champion", challenger_v)
else:
    registry.transition_model_version_stage("RiskModel", challenger_v, "Archived")
```

---

## 7. Zombie / Orphan Runs Left in 'RUNNING' State on Process Crashes

### The Mistake
Relying on explicit manual termination calls (`client.end_run()`) without wrapping execution in exception-safe context managers.

```python
# ANTIPATTERN: If training throws OOM or unhandled exception, run stays 'RUNNING' forever
client.start_run()
train_massive_model()  # Throws CUDA OutOfMemoryError
client.end_run()       # Never reached!
```

### Why It Fails
- Leaderboards and AutoML sweeps show active runs that died hours ago.
- Automated cleanup and alerting scripts trigger false alarms.

### Production Remediation
Always utilize Python context managers (`__enter__` and `__exit__`) that guarantee status transition to `FAILED` or `FINISHED`:
```python
class ActiveRunContext:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        status = "FAILED" if exc_type is not None else "FINISHED"
        self.client.set_terminated(self.run_uuid, status=status)
        return False
```
