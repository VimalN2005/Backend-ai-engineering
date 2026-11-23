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

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_database_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

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
