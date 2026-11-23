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


---

## 4. Relational Database Schema Internals

Under the hood, MLflow Tracking Server uses an industrial SQL relational schema to maintain transactional integrity across experiments and registered models:

### Core Tables
1. **`experiments`**:
   - `experiment_id` (PRIMARY KEY, INTEGER / VARCHAR)
   - `name` (VARCHAR, UNIQUE, NOT NULL)
   - `artifact_location` (VARCHAR)
   - `lifecycle_stage` (`'active'` | `'deleted'`)
2. **`runs`**:
   - `run_uuid` (PRIMARY KEY, VARCHAR(32))
   - `name` (VARCHAR)
   - `experiment_id` (FOREIGN KEY -> `experiments.experiment_id`)
   - `status` (`'SCHEDULED'` | `'RUNNING'` | `'FINISHED'` | `'FAILED'`)
   - `start_time` (BIGINT, epoch ms)
   - `end_time` (BIGINT, epoch ms)
   - `source_type` (`'NOTEBOOK'` | `'JOB'` | `'LOCAL'`)
   - `source_name` (Git repository URL / script name)
   - `entry_point_name` (Entrypoint command)
   - `lifecycle_stage` (`'active'` | `'deleted'`)
3. **`params`**:
   - `key` (VARCHAR(250), PRIMARY KEY component)
   - `value` (VARCHAR(500), NOT NULL)
   - `run_uuid` (PRIMARY KEY component, FOREIGN KEY -> `runs.run_uuid`)
4. **`metrics`**:
   - `key` (VARCHAR(250), PRIMARY KEY component)
   - `value` (FLOAT, NOT NULL)
   - `timestamp` (BIGINT, PRIMARY KEY component, epoch ms)
   - `step` (BIGINT, PRIMARY KEY component, default 0)
   - `run_uuid` (PRIMARY KEY component, FOREIGN KEY -> `runs.run_uuid`)
5. **`tags`**:
   - `key` (VARCHAR(250), PRIMARY KEY component)
   - `value` (VARCHAR(5000))
   - `run_uuid` (PRIMARY KEY component, FOREIGN KEY -> `runs.run_uuid`)
6. **`registered_models`**:
   - `name` (PRIMARY KEY, VARCHAR(256))
   - `creation_time` (BIGINT)
   - `last_updated_time` (BIGINT)
   - `description` (VARCHAR(1000))
7. **`model_versions`**:
   - `name` (PRIMARY KEY component, FOREIGN KEY -> `registered_models.name`)
   - `version` (PRIMARY KEY component, INTEGER)
   - `creation_time` (BIGINT)
   - `current_stage` (`'None'` | `'Staging'` | `'Production'` | `'Archived'`)
   - `source` (Artifact URI pointing to model files in S3/MinIO)
   - `run_id` (FOREIGN KEY -> `runs.run_uuid`)
   - `status` (`'READY'` | `'FAILED_REGISTRATION'`)

---

## 5. The `MLmodel` Artifact Specification & Model Flavors

When MLflow logs a model (e.g. `mlflow.sklearn.log_model`), it does not merely save a raw `model.joblib` pickle file. It packages the artifact as a self-describing directory containing the `MLmodel` configuration file:

```yaml
artifact_path: model
flavors:
  python_function:
    env:
      conda: conda.yaml
      virtualenv: python_env.yaml
    loader_module: mlflow.sklearn
    model_path: model.joblib
    predict_fn: predict_proba
    python_version: 3.14.0
  sklearn:
    code: null
    pickled_model: model.joblib
    serialization_format: cloudpickle
    sklearn_version: 1.9.0
model_size_bytes: 14285
model_uuid: 8f4a13d8e3b04c81a56f2e82103f6711
run_id: run_7b19dc6e8201
signature:
  inputs: '[{"name": "income", "type": "double"}, {"name": "debt_ratio", "type": "double"}]'
  outputs: '[{"type": "tensor", "tensor-spec": {"dtype": "float32", "shape": [-1, 2]}}]'
utc_time_created: '2026-11-23 18:30:00.000000'
```

### Key Components of `MLmodel`
1. **Model Signature**: Defines the formal schema contract (names, types, dimensions) of inputs and outputs. During serving, inference requests are validated against this signature before hitting model code.
2. **Multiple Flavors**:
   - The native flavor (`sklearn`, `pytorch`, `onnx`) allows loading with the original library for fine-tuning or inspection.
   - The generic flavor (`python_function` / `pyfunc`) standardizes inference across all models with a unified `pyfunc.predict(df)` interface, allowing generic inference servers (FastAPI, Triton, Ray) to host any model without framework-specific code.

---

## 6. Model Registry Governance: Champion vs Challenger Evaluation

In an enterprise CI/CD pipeline, human engineers do not manually click "Promote to Production". Instead, an automated evaluation gate executes:

1. **Candidate Registration**: A new training run meeting baseline validation metrics is registered as a Challenger version (e.g., `v3` with stage `'Staging'`).
2. **Holdout Evaluation Benchmark**: The automated CI/CD runner loads:
   - Champion Model: `models:/FraudDetectionModel@champion` (or `current_stage='Production'`).
   - Challenger Model: `models:/FraudDetectionModel/3` (or `current_stage='Staging'`).
3. **Statistical Promotion Rule**:
   $$\text{Promote Condition} = \left(\text{AUC}_{\text{challenger}} \ge \text{AUC}_{\text{champion}} + \delta\right) \land \left(\text{P99 Latency}_{\text{challenger}} \le \text{SLA}\right)$$
   Where $\delta$ is the minimum required margin of improvement (e.g. $+0.015$ ROC-AUC) to justify deployment risk.
4. **Automated Atomic Transition**:
   - Champion `v2` stage is transitioned to `'Archived'`.
   - Challenger `v3` stage is transitioned to `'Production'` (and tagged `@champion`).

---

## 7. Production MLOps Checklist for MLflow

- [ ] **Decoupled Stores**: Is metadata stored in an indexed relational database (PostgreSQL/SQLite) and heavy artifacts stored in object storage (S3/MinIO/GCS)?
- [ ] **Git Provenance Logged**: Is every training run tagged with `mlflow.source.git.commit` to ensure 100% reproducibility?
- [ ] **Data Version Logged**: Is the exact dataset hash or DVC/LakeFS tag recorded in run tags?
- [ ] **Model Signature Enforced**: Does the saved model artifact include an explicit input/output schema signature?
- [ ] **No Raw In-Sample Overfitting**: Are reported evaluation metrics computed strictly on holdout test datasets, never training data?
- [ ] **Automated Promotion Gating**: Is model promotion governed by automated Champion-vs-Challenger validation scripts rather than manual human overrides?
- [ ] **Graceful Exception Handling**: Are runs enclosed in context managers (`with mlflow.start_run():`) to ensure failed runs are marked `'FAILED'` rather than left hanging as `'RUNNING'`?
