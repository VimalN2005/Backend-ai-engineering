# Day 80: MLflow: Experiment Tracking, Model Registry & Version Control

## 1. Enterprise MLOps Problem Statement: The Experimentation Black Box

In mature backend and AI engineering organizations, machine learning is not a one-off modeling script; it is a continuous, iterative engineering lifecycle. Data scientists and ML engineers train dozens of candidate models daily across varying feature sets, preprocessing pipelines, hyperparameters, and dataset versions.

Without a standardized, production-grade tracking and governance framework, teams suffer from four fatal operational failures:

1. **The "Lost Champion" Disaster (Irreproducible Models)**:
   - A model trained three weeks ago achieves an outstanding ROC-AUC of $0.94$ and is earmarked for production deployment.
   - However, nobody recorded the exact Git commit SHA, feature engineering configuration, seed, or training data snapshot. Re-running the training script yields an inferior ROC-AUC of $0.81$. The champion model is irreproducible.
2. **Silent Model Regressions in Production**:
   - A new model version is deployed to production based on offline accuracy, but nobody conducted a formal Champion vs Challenger evaluation gate.
   - In production, the new model underperforms on specific customer segments, triggering high customer churn before engineers can identify which commit caused the regression.
3. **Storage Decoupling Bottlenecks**:
   - Naive teams attempt to store heavy model binaries (multi-hundred-megabyte weights) directly in relational databases (PostgreSQL/MySQL), causing database connection pool exhaustion and crashing operational databases.
4. **Lack of Auditable Provenance & Regulatory Non-Compliance**:
   - In regulated industries (fintech underwriting, medical diagnostics, fraud detection), compliance mandates (e.g. EU AI Act, Fair Lending Act) require organizations to produce the exact dataset version, hyperparameter configuration, and validation metrics for every live model decision.

The industry-standard solution is **MLflow**: decoupling metadata tracking (relational database) from artifact storage (object store/S3), providing immutable model versioning, lineage traceability, and automated Champion/Challenger promotion gates.

---

## 2. Production Architecture Blueprint: MLflow Decoupled Storage Topology

A production MLflow deployment separates high-frequency structured metadata transactions from heavy binary artifact storage:

```
+-------------------------------------------------------------------------------------------------------+
|                         ENTERPRISE MLFLOW ARCHITECTURE TOPOLOGY                                       |
+-------------------------------------------------------------------------------------------------------+
|                                                                                                       |
|  [ML Training Jobs / CI/CD Pipelines]              [Hyperparameter Search Workers (AutoML)]           |
|  Airflow / Ray Train / GitHub Actions               Parallel Trials (Worker 1 ... Worker K)           |
|            |                                                         |                                |
|            +----------------------------+----------------------------+                                |
|                                         |                                                             |
|                                         v                                                             |
|                      +--------------------------------------+                                         |
|                      |   MLflow Tracking Client / Server    |                                         |
|                      |   - REST API / Python SDK            |                                         |
|                      |   - Runs, Params, Metrics, Tags      |                                         |
|                      +--------------------------------------+                                         |
|                                         |                                                             |
|                    +--------------------+--------------------+                                        |
|                    |                                         |                                        |
|                    v                                         v                                        |
|  +------------------------------------+    +------------------------------------+                     |
|  |       Backend Relational Store     |    |       Binary Artifact Store        |                     |
|  |     (PostgreSQL / SQLite / RDS)    |    |      (AWS S3 / GCS / MinIO)        |                     |
|  |------------------------------------|    |------------------------------------|                     |
|  | - experiments (id, name, uri)      |    | /artifacts/<run_id>/               |                     |
|  | - runs (id, status, git_commit)    |    |   ├── MLmodel (YAML specification) |                     |
|  | - params (key, val, run_id)        |    |   ├── model.joblib / model.onnx    |                     |
|  | - metrics (key, val, step, time)   |    |   ├── preprocessor.joblib          |                     |
|  | - tags (key, val, run_id)          |    |   ├── confusion_matrix.png         |                     |
|  | - registered_models (name, tags)   |    |   └── feature_importance.json      |                     |
|  | - model_versions (v1, v2, stage)   |    |                                    |                     |
|  +------------------------------------+    +------------------------------------+                     |
|                    |                                                                                  |
|                    v                                                                                  |
|  +-------------------------------------------------------------------------------------------------+  |
|  |                               ENTERPRISE MODEL REGISTRY & GOVERNANCE                            |  |
|  |                                                                                                 |  |
|  |  Model: "FraudDetectionService"                                                                |  |
|  |  ├── Version 1 (v1) -> Stage: ARCHIVED                                                         |  |
|  |  ├── Version 2 (v2) -> Stage: PRODUCTION (Champion) [ROC-AUC: 0.884]                            |  |
|  |  └── Version 3 (v3) -> Stage: STAGING    (Challenger) [ROC-AUC: 0.912]                           |  |
|  |                                                                                                 |  |
|  |  [Automated Promotion Gate: If Challenger AUC >= Champion + 0.015 -> Promote to Champion]       |  |
|  +-------------------------------------------------------------------------------------------------+  |
|                                                  |                                                    |
|                                                  v                                                    |
|                     +---------------------------------------------------------+                       |
|                     | Production Inference Microservice (FastAPI / Triton)    |                       |
|                     | Ingests models:/FraudDetectionService@champion          |                       |
|                     +---------------------------------------------------------+                       |
|                                                                                                       |
+-------------------------------------------------------------------------------------------------------+
```

---

## 3. End-to-End Model Lifecycle: From Experiment Run to Production Champion

1. **Experiment Creation**: Groups related modeling attempts under a shared business domain (e.g., `fraud_detection_experiment`).
2. **Run Execution & Structured Logging**:
   - `log_params()`: Hyperparameters (learning rate, L2 regularization, max depth).
   - `log_metric(key, val, step)`: Training and validation loss curves across training epochs.
   - `set_tags()`: Git commit SHA, training dataset checksum, author, model architecture.
   - `log_model_artifact()`: Saves pipeline binary and `MLmodel` schema manifest.
3. **Run Evaluation & Leaderboard Querying**: Sort candidate runs by validation metric (e.g. `ORDER BY val_roc_auc DESC`).
4. **Model Registration**: Promotes the top run to the Model Registry as an immutable version ($v1, v2, \dots$).
5. **Challenger vs Champion Evaluation Gate**: Automated CI/CD script evaluates the candidate against the live champion model. If performance passes statistical significance thresholds, the challenger is promoted to production (`@champion`).
