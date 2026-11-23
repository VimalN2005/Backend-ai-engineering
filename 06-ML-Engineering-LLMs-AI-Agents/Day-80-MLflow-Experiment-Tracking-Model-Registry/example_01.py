"""
Day 80: Production MLflow Tracking Server, Experiment Tracking & Model Registry
Part 1: Relational Tracking Backend and Artifact Store Engine

This module scaffolds the production SQLite relational tracking store and filesystem
artifact store implementing the MLflow 2.0+ tracking schema specifications.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ExperimentRecord:
    """Represents an MLflow experiment entity."""
    experiment_id: str
    name: str
    artifact_location: str
    lifecycle_stage: str
    created_at_epoch_ms: int


@dataclass(frozen=True)
class RunRecord:
    """Represents an individual MLflow experiment execution run."""
    run_uuid: str
    experiment_id: str
    name: str
    status: str
    start_time: int
    end_time: Optional[int]
    git_commit: str
    dataset_version: str


@dataclass(frozen=True)
class ModelVersionRecord:
    """Represents an immutable model version in the Model Registry."""
    model_name: str
    version: int
    run_id: str
    current_stage: str  # 'None' | 'Staging' | 'Production' | 'Archived'
    artifact_source: str
    creation_time: int
    aliases: List[str]


class RelationalTrackingBackend:
    """
    Production SQLite relational backend store implementing MLflow's core tracking tables.
    Provides ACID transaction guarantees and indexed query performance.
    """

    def __init__(self, db_path: str, artifact_root: Optional[str] = None) -> None:
        self.db_path = db_path
        self.artifact_root = artifact_root or os.path.join(os.path.dirname(db_path), "artifacts")
        os.makedirs(self.artifact_root, exist_ok=True)
        self._init_database_schema()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        return self.get_connection()

    def _init_database_schema(self) -> None:
        """Initializes tables matching MLflow relational specifications."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Experiments
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    artifact_location TEXT NOT NULL,
                    lifecycle_stage TEXT NOT NULL DEFAULT 'active',
                    created_at_epoch_ms INTEGER NOT NULL
                );
            """)

            # 2. Runs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_uuid TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    name TEXT,
                    status TEXT NOT NULL DEFAULT 'RUNNING',
                    start_time INTEGER NOT NULL,
                    end_time INTEGER,
                    git_commit TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES experiments (experiment_id) ON DELETE CASCADE
                );
            """)

            # 3. Parameters
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS params (
                    run_uuid TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (run_uuid, key),
                    FOREIGN KEY (run_uuid) REFERENCES runs (run_uuid) ON DELETE CASCADE
                );
            """)

            # 4. Metrics (with step and timestamp tracking)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    run_uuid TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value REAL NOT NULL,
                    timestamp INTEGER NOT NULL,
                    step INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_uuid, key, step, timestamp),
                    FOREIGN KEY (run_uuid) REFERENCES runs (run_uuid) ON DELETE CASCADE
                );
            """)

            # 5. Tags
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    run_uuid TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (run_uuid, key),
                    FOREIGN KEY (run_uuid) REFERENCES runs (run_uuid) ON DELETE CASCADE
                );
            """)

            # 6. Registered Models
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS registered_models (
                    name TEXT PRIMARY KEY,
                    creation_time INTEGER NOT NULL,
                    last_updated_time INTEGER NOT NULL,
                    description TEXT
                );
            """)

            # 7. Model Versions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS model_versions (
                    model_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    current_stage TEXT NOT NULL DEFAULT 'None',
                    artifact_source TEXT NOT NULL,
                    creation_time INTEGER NOT NULL,
                    aliases TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (model_name, version),
                    FOREIGN KEY (model_name) REFERENCES registered_models (name) ON DELETE CASCADE,
                    FOREIGN KEY (run_id) REFERENCES runs (run_uuid) ON DELETE CASCADE
                );
            """)

            # Indices for low-latency leaderboard querying
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_run_key ON metrics (run_uuid, key);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_params_run ON params (run_uuid);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_runs_experiment ON runs (experiment_id);")
            conn.commit()


SQLiteTrackingStore = RelationalTrackingBackend


# =====================================================================
# Part 2: Enterprise Tracking Client & Artifact Serialization Engine
# =====================================================================

import hashlib
import pickle
import shutil


class ActiveRunContext:
    """Context manager for an active MLflow run, guaranteeing lifecycle termination."""
    def __init__(self, client: MLflowClient, run: RunRecord):
        self.client = client
        self.run = run

    def __enter__(self) -> ActiveRunContext:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        status = "FAILED" if exc_type is not None else "FINISHED"
        self.client.set_terminated(self.run.run_uuid, status=status)
        return False  # Propagate exception if any

    def log_param(self, key: str, value: Any) -> None:
        self.client.log_param(self.run.run_uuid, key, value)

    def log_params(self, params_dict: Dict[str, Any]) -> None:
        self.client.log_params(self.run.run_uuid, params_dict)

    def log_metric(self, key: str, value: float, step: int = 0) -> None:
        self.client.log_metric(self.run.run_uuid, key, value, step=step)

    def set_tag(self, key: str, value: str) -> None:
        self.client.set_tag(self.run.run_uuid, key, value)

    def log_model_artifact(
        self,
        model_obj: Any,
        artifact_subpath: str = "model",
        flavor: str = "sklearn",
        signature: Optional[Dict[str, Any]] = None,
    ) -> str:
        return self.client.log_model_artifact(
            self.run.run_uuid,
            model_obj,
            artifact_subpath=artifact_subpath,
            flavor=flavor,
            signature=signature,
        )


class MLflowClient:
    """
    Production-grade MLflow tracking client managing runs, parameters, step metrics,
    tags, and MLmodel artifact packaging.
    """
    def __init__(self, backend_store: SQLiteTrackingStore):
        self.store = backend_store

    def create_experiment(self, name: str, artifact_location: Optional[str] = None) -> str:
        if artifact_location is None:
            artifact_location = os.path.join(self.store.artifact_root, name.replace(" ", "_").lower())
        os.makedirs(artifact_location, exist_ok=True)
        exp_id = str(uuid.uuid4())[:8]
        now_ms = int(time.time() * 1000)

        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO experiments (experiment_id, name, artifact_location, lifecycle_stage, created_at_epoch_ms) "
                "VALUES (?, ?, ?, 'active', ?)",
                (exp_id, name, artifact_location, now_ms)
            )
            conn.commit()
        return exp_id

    def get_experiment_by_name(self, name: str) -> Optional[ExperimentRecord]:
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT experiment_id, name, artifact_location, lifecycle_stage, created_at_epoch_ms "
                "FROM experiments WHERE name = ?", (name,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return ExperimentRecord(*row)

    def start_run(
        self,
        experiment_id: str,
        run_name: str,
        git_commit: str = "HEAD",
        dataset_version: str = "v1.0.0",
    ) -> ActiveRunContext:
        run_uuid = uuid.uuid4().hex
        now_ms = int(time.time() * 1000)

        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO runs (run_uuid, experiment_id, name, status, start_time, end_time, git_commit, dataset_version) "
                "VALUES (?, ?, ?, 'RUNNING', ?, NULL, ?, ?)",
                (run_uuid, experiment_id, run_name, now_ms, git_commit, dataset_version)
            )
            conn.commit()

        run = RunRecord(
            run_uuid=run_uuid,
            experiment_id=experiment_id,
            name=run_name,
            status="RUNNING",
            start_time=now_ms,
            end_time=None,
            git_commit=git_commit,
            dataset_version=dataset_version,
        )
        return ActiveRunContext(self, run)

    def set_terminated(self, run_uuid: str, status: str = "FINISHED") -> None:
        now_ms = int(time.time() * 1000)
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE runs SET status = ?, end_time = ? WHERE run_uuid = ?",
                (status, now_ms, run_uuid)
            )
            conn.commit()

    def log_param(self, run_uuid: str, key: str, value: Any) -> None:
        val_str = str(value)
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO params (run_uuid, key, value) VALUES (?, ?, ?)",
                (run_uuid, key, val_str)
            )
            conn.commit()

    def log_params(self, run_uuid: str, params_dict: Dict[str, Any]) -> None:
        records = [(run_uuid, k, str(v)) for k, v in params_dict.items()]
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "INSERT OR REPLACE INTO params (run_uuid, key, value) VALUES (?, ?, ?)",
                records
            )
            conn.commit()

    def log_metric(self, run_uuid: str, key: str, value: float, step: int = 0) -> None:
        now_ms = int(time.time() * 1000)
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO metrics (run_uuid, key, value, timestamp, step) VALUES (?, ?, ?, ?, ?)",
                (run_uuid, key, float(value), now_ms, step)
            )
            conn.commit()

    def set_tag(self, run_uuid: str, key: str, value: str) -> None:
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO tags (run_uuid, key, value) VALUES (?, ?, ?)",
                (run_uuid, key, str(value))
            )
            conn.commit()

    def log_model_artifact(
        self,
        run_uuid: str,
        model_obj: Any,
        artifact_subpath: str = "model",
        flavor: str = "sklearn",
        signature: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Serializes model object and generates production MLmodel metadata bundle.
        """
        run_artifact_dir = os.path.join(self.store.artifact_root, "runs", run_uuid, artifact_subpath)
        os.makedirs(run_artifact_dir, exist_ok=True)

        model_filename = "model.joblib"
        model_path = os.path.join(run_artifact_dir, model_filename)
        try:
            import joblib
            joblib.dump(model_obj, model_path, compress=3)
        except Exception:
            with open(model_path, "wb") as f:
                pickle.dump(model_obj, f)

        hasher = hashlib.sha256()
        with open(model_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        artifact_digest = hasher.hexdigest()

        mlmodel_manifest = {
            "artifact_path": artifact_subpath,
            "flavors": {
                "python_function": {
                    "model_path": model_filename,
                    "loader_module": "mlflow.sklearn",
                    "python_version": "3.14.0",
                },
                flavor: {
                    "pickled_model": model_filename,
                    "sklearn_version": getattr(model_obj, "_sklearn_version", "1.9.0"),
                    "serialization_format": "joblib",
                }
            },
            "run_id": run_uuid,
            "utc_time_created": time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime()),
            "signature": signature or {
                "inputs": [{"name": f"feature_{i}", "type": "double"} for i in range(10)],
                "outputs": [{"type": "tensor", "tensor-spec": {"dtype": "int64", "shape": [-1]}}]
            },
            "sha256_digest": artifact_digest,
        }

        manifest_path = os.path.join(run_artifact_dir, "MLmodel")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(mlmodel_manifest, f, indent=2)

        return run_artifact_dir


# =====================================================================
# Part 3: Model Registry & Automated Champion-Challenger Evaluation Gate
# =====================================================================

class ModelRegistryClient:
    """
    Enterprise Model Registry client managing model lifecycle stages,
    version increments, deployment aliases, and automated validation gates.
    """
    def __init__(self, backend_store: SQLiteTrackingStore):
        self.store = backend_store

    def create_registered_model(self, name: str, description: str = "") -> None:
        now_ms = int(time.time() * 1000)
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO registered_models (name, creation_time, last_updated_time, description) "
                "VALUES (?, ?, ?, ?)",
                (name, now_ms, now_ms, description)
            )
            conn.commit()

    def create_model_version(
        self,
        name: str,
        run_id: str,
        artifact_source: str,
    ) -> ModelVersionRecord:
        now_ms = int(time.time() * 1000)
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(version) FROM model_versions WHERE model_name = ?", (name,))
            max_v = cursor.fetchone()[0]
            next_v = 1 if max_v is None else max_v + 1

            cursor.execute(
                "INSERT INTO model_versions (model_name, version, run_id, current_stage, artifact_source, creation_time, aliases) "
                "VALUES (?, ?, ?, 'None', ?, ?, '[]')",
                (name, next_v, run_id, artifact_source, now_ms)
            )
            cursor.execute("UPDATE registered_models SET last_updated_time = ? WHERE name = ?", (now_ms, name))
            conn.commit()

        return ModelVersionRecord(
            model_name=name,
            version=next_v,
            run_id=run_id,
            current_stage="None",
            artifact_source=artifact_source,
            creation_time=now_ms,
            aliases=[],
        )

    def transition_model_version_stage(
        self,
        name: str,
        version: int,
        stage: str,
        archive_existing_versions: bool = False,
    ) -> None:
        valid_stages = {"None", "Staging", "Production", "Archived"}
        if stage not in valid_stages:
            raise ValueError(f"Invalid stage '{stage}'. Allowed: {valid_stages}")

        now_ms = int(time.time() * 1000)
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            if archive_existing_versions and stage in {"Staging", "Production"}:
                cursor.execute(
                    "UPDATE model_versions SET current_stage = 'Archived' "
                    "WHERE model_name = ? AND current_stage = ? AND version != ?",
                    (name, stage, version)
                )

            cursor.execute(
                "UPDATE model_versions SET current_stage = ? WHERE model_name = ? AND version = ?",
                (stage, name, version)
            )
            cursor.execute("UPDATE registered_models SET last_updated_time = ? WHERE name = ?", (now_ms, name))
            conn.commit()

    def set_model_version_alias(self, name: str, alias: str, version: int) -> None:
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT version, aliases FROM model_versions WHERE model_name = ?", (name,))
            rows = cursor.fetchall()
            for v_num, aliases_json in rows:
                aliases_list: List[str] = json.loads(aliases_json)
                if alias in aliases_list and v_num != version:
                    aliases_list.remove(alias)
                    cursor.execute(
                        "UPDATE model_versions SET aliases = ? WHERE model_name = ? AND version = ?",
                        (json.dumps(aliases_list), name, v_num)
                    )
                elif v_num == version and alias not in aliases_list:
                    aliases_list.append(alias)
                    cursor.execute(
                        "UPDATE model_versions SET aliases = ? WHERE model_name = ? AND version = ?",
                        (json.dumps(aliases_list), name, v_num)
                    )
            conn.commit()

    def get_model_version_by_alias(self, name: str, alias: str) -> Optional[ModelVersionRecord]:
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT model_name, version, run_id, current_stage, artifact_source, creation_time, aliases FROM model_versions WHERE model_name = ?", (name,))
            rows = cursor.fetchall()
            for row in rows:
                aliases_list = json.loads(row[6])
                if alias in aliases_list:
                    return ModelVersionRecord(
                        model_name=row[0],
                        version=row[1],
                        run_id=row[2],
                        current_stage=row[3],
                        artifact_source=row[4],
                        creation_time=row[5],
                        aliases=aliases_list,
                    )
            return None

    def evaluate_champion_challenger(
        self,
        model_name: str,
        challenger_version: int,
        metric_key: str = "val_roc_auc",
        margin_threshold: float = 0.015,
    ) -> bool:
        """
        Automated Champion vs Challenger Promotion Guardrail:
        Promotes challenger to Production and assigns @champion alias ONLY if
        challenger metric exceeds current champion metric by margin_threshold.
        """
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT run_id FROM model_versions WHERE model_name = ? AND version = ?",
                (model_name, challenger_version)
            )
            challenger_row = cursor.fetchone()
            if not challenger_row:
                raise ValueError(f"Challenger version {challenger_version} not found")
            challenger_run_id = challenger_row[0]

            cursor.execute(
                "SELECT value FROM metrics WHERE run_uuid = ? AND key = ? ORDER BY step DESC, timestamp DESC LIMIT 1",
                (challenger_run_id, metric_key)
            )
            challenger_metric_row = cursor.fetchone()
            if not challenger_metric_row:
                raise ValueError(f"Metric '{metric_key}' missing for challenger run {challenger_run_id}")
            challenger_metric = challenger_metric_row[0]

            champion_record = self.get_model_version_by_alias(model_name, "champion")
            if champion_record is None:
                print(f"[Promotion Gate] No existing champion found. Version {challenger_version} promoted to @champion.")
                self.transition_model_version_stage(model_name, challenger_version, "Production", archive_existing_versions=True)
                self.set_model_version_alias(model_name, "champion", challenger_version)
                return True

            cursor.execute(
                "SELECT value FROM metrics WHERE run_uuid = ? AND key = ? ORDER BY step DESC, timestamp DESC LIMIT 1",
                (champion_record.run_id, metric_key)
            )
            champion_metric = cursor.fetchone()[0]

            diff = challenger_metric - champion_metric
            print(f"[Promotion Gate] Evaluating {model_name}: Challenger v{challenger_version} ({metric_key}={challenger_metric:.4f}) vs Champion v{champion_record.version} ({metric_key}={champion_metric:.4f}). Delta = {diff:+.4f} (Required: +{margin_threshold:.4f})")

            if diff >= margin_threshold:
                print(f"[Promotion Gate] SUCCESS: Challenger exceeds threshold by {diff:.4f}. Promoting v{challenger_version} -> @champion.")
                self.transition_model_version_stage(model_name, challenger_version, "Production", archive_existing_versions=True)
                self.set_model_version_alias(model_name, "champion", challenger_version)
                return True
            else:
                print(f"[Promotion Gate] REJECTED: Challenger delta {diff:+.4f} < margin threshold +{margin_threshold:.4f}. Maintaining Champion v{champion_record.version}.")
                self.transition_model_version_stage(model_name, challenger_version, "Archived")
                return False


def run_pipeline_demo() -> None:
    """Executes end-to-end experiment tracking and model registry demonstration."""
    import tempfile
    from sklearn.datasets import make_classification
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    base_dir = os.path.join(tempfile.gettempdir(), f"mlflow_demo_{int(time.time())}")
    db_path = os.path.join(base_dir, "mlflow_tracking.db")
    artifact_root = os.path.join(base_dir, "artifacts")

    store = SQLiteTrackingStore(db_path=db_path, artifact_root=artifact_root)
    client = MLflowClient(backend_store=store)
    registry = ModelRegistryClient(backend_store=store)

    exp_id = client.create_experiment("fraud_detection_platform")
    print(f"\n[MLflow Tracking] Created Experiment: fraud_detection_platform (ID: {exp_id})")

    X, y = make_classification(n_samples=1000, n_features=10, n_informative=8, random_state=42)
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

    with client.start_run(exp_id, run_name="baseline_logistic_regression", git_commit="c0a1b2", dataset_version="v1.0") as run_1:
        run_1.set_tag("model_family", "linear")
        run_1.set_tag("environment", "staging")
        run_1.log_params({"solver": "lbfgs", "C": 1.0, "max_iter": 200})

        model_1 = LogisticRegression(C=1.0, max_iter=200, random_state=42)
        model_1.fit(X_train, y_train)

        y_prob = model_1.predict_proba(X_val)[:, 1]
        auc_1 = roc_auc_score(y_val, y_prob)

        run_1.log_metric("train_loss", 0.452, step=1)
        run_1.log_metric("val_roc_auc", float(auc_1), step=1)

        model_art_dir = run_1.log_model_artifact(model_1, artifact_subpath="model", flavor="sklearn")
        print(f"[Run 1 Complete] Run ID: {run_1.run.run_uuid[:8]} | Val ROC-AUC: {auc_1:.4f}")

    registry.create_registered_model("FraudPredictor", description="Enterprise fraud risk scoring model")
    v1_record = registry.create_model_version("FraudPredictor", run_1.run.run_uuid, artifact_source=model_art_dir)
    print(f"[Model Registry] Registered Version 1 for FraudPredictor. Evaluating initial promotion...")
    registry.evaluate_champion_challenger("FraudPredictor", challenger_version=v1_record.version, margin_threshold=0.015)

    with client.start_run(exp_id, run_name="challenger_gradient_boosting", git_commit="d3e4f5", dataset_version="v1.1") as run_2:
        run_2.set_tag("model_family", "tree_ensemble")
        run_2.set_tag("environment", "staging")
        run_2.log_params({"n_estimators": 100, "learning_rate": 0.1, "max_depth": 3})

        model_2 = GradientBoostingClassifier(n_estimators=100, learning_rate=0.1, max_depth=3, random_state=42)
        model_2.fit(X_train, y_train)

        y_prob_2 = model_2.predict_proba(X_val)[:, 1]
        auc_2 = roc_auc_score(y_val, y_prob_2)

        run_2.log_metric("train_loss", 0.284, step=1)
        run_2.log_metric("val_roc_auc", float(auc_2), step=1)

        model_art_dir_2 = run_2.log_model_artifact(model_2, artifact_subpath="model", flavor="sklearn")
        print(f"[Run 2 Complete] Run ID: {run_2.run.run_uuid[:8]} | Val ROC-AUC: {auc_2:.4f}")

    v2_record = registry.create_model_version("FraudPredictor", run_2.run.run_uuid, artifact_source=model_art_dir_2)
    print(f"[Model Registry] Registered Version 2 for FraudPredictor. Evaluating automated champion gate...")
    promoted = registry.evaluate_champion_challenger("FraudPredictor", challenger_version=v2_record.version, margin_threshold=0.015)

    current_champ = registry.get_model_version_by_alias("FraudPredictor", "champion")
    assert current_champ is not None, "Champion alias must be assigned!"
    print(f"\n[Verification] Current Active Champion: Version {current_champ.version} (Stage: {current_champ.current_stage})")
    print(f"[Verification] Artifact Source: {current_champ.artifact_source}")

    shutil.rmtree(base_dir, ignore_errors=True)
    print("[Clean Up] Pipeline execution and verification complete.\n")


if __name__ == "__main__":
    run_pipeline_demo()
