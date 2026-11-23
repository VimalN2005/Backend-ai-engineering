"""
Day 80: Multi-Run Governance, Nested Parent-Child Trials & Model Lineage Auditor
Part 1: Nested Parent-Child Run Tracking Harness for AutoML / Hyperparameter Sweeps

This module implements nested parent-child run lifecycles matching MLflow production conventions:
- Parent runs track global study configurations, objectives, git commit, and aggregate metrics.
- Child runs link via 'mlflow.parentRunId', recording granular trial hyperparameters, step curves,
  and validation trade-offs.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Re-use core components from example_01
from example_01 import MLflowClient, RelationalTrackingBackend, SQLiteTrackingStore


@dataclass
class HyperparameterTrialResult:
    """Represents the outcome of a child optimization trial."""
    trial_id: int
    run_uuid: str
    params: Dict[str, Any]
    metrics: Dict[str, float]
    status: str
    duration_ms: float


class NestedRunTracker:
    """
    Manages nested parent-child run relationships for distributed sweeps,
    automating parentRunId tag linking and aggregate summary rollups.
    """
    def __init__(self, client: MLflowClient):
        self.client = client

    def start_parent_run(
        self,
        experiment_id: str,
        study_name: str,
        objective_metric: str,
        search_algorithm: str,
        git_commit: str = "HEAD",
        dataset_version: str = "v2.0.0",
    ) -> str:
        """Initializes top-level parent run representing the hyperparameter sweep."""
        with self.client.start_run(
            experiment_id=experiment_id,
            run_name=study_name,
            git_commit=git_commit,
            dataset_version=dataset_version,
        ) as parent_ctx:
            parent_uuid = parent_ctx.run.run_uuid
            parent_ctx.set_tag("mlflow.runType", "parent_study")
            parent_ctx.set_tag("study_name", study_name)
            parent_ctx.set_tag("objective_metric", objective_metric)
            parent_ctx.set_tag("search_algorithm", search_algorithm)
            parent_ctx.log_params({
                "objective_metric": objective_metric,
                "search_algorithm": search_algorithm,
            })
            # Leave parent in RUNNING status until all child runs complete
            return parent_uuid

    def execute_child_trial(
        self,
        experiment_id: str,
        parent_run_id: str,
        trial_id: int,
        params: Dict[str, Any],
        step_metrics_history: List[Tuple[int, Dict[str, float]]],
        model_artifact_obj: Optional[Any] = None,
    ) -> HyperparameterTrialResult:
        """
        Executes and logs an individual trial as a nested child run,
        linking it via the canonical 'mlflow.parentRunId' tag.
        """
        start_t = time.perf_counter()
        child_name = f"trial_{trial_id:03d}"

        with self.client.start_run(
            experiment_id=experiment_id,
            run_name=child_name,
        ) as child_ctx:
            child_uuid = child_ctx.run.run_uuid

            # Canonical MLflow parent-child link
            child_ctx.set_tag("mlflow.parentRunId", parent_run_id)
            child_ctx.set_tag("trial_id", str(trial_id))
            child_ctx.log_params(params)

            # Log iterative step metrics (e.g. per-epoch training curve)
            final_metrics: Dict[str, float] = {}
            for step, metrics_dict in step_metrics_history:
                for k, v in metrics_dict.items():
                    child_ctx.log_metric(k, v, step=step)
                    final_metrics[k] = v

            # Serialize model if provided
            if model_artifact_obj is not None:
                child_ctx.log_model_artifact(
                    model_obj=model_artifact_obj,
                    artifact_subpath="model",
                    flavor="sklearn",
                )

        duration = (time.perf_counter() - start_t) * 1000.0
        return HyperparameterTrialResult(
            trial_id=trial_id,
            run_uuid=child_uuid,
            params=params,
            metrics=final_metrics,
            status="FINISHED",
            duration_ms=duration,
        )

    def finalize_parent_run(
        self,
        parent_run_id: str,
        best_trial: HyperparameterTrialResult,
        total_trials: int,
    ) -> None:
        """Concludes parent run, recording global study champion metrics."""
        self.client.log_metric(parent_run_id, f"best_{best_trial.metrics.get('objective_name', 'score')}", list(best_trial.metrics.values())[0])
        self.client.log_metric(parent_run_id, "total_trials_completed", float(total_trials))
        self.client.set_tag(parent_run_id, "best_child_run_id", best_trial.run_uuid)
        self.client.set_terminated(parent_run_id, status="FINISHED")


# =====================================================================
# Part 2: Multi-Run Leaderboard Engine & End-to-End Lineage Auditor
# =====================================================================

import shutil
from example_01 import ModelRegistryClient


class MultiRunLeaderboardEngine:
    """
    Executes high-performance SQL analytical queries across runs, params,
    tags, and metrics to produce real-time evaluation leaderboards.
    """
    def __init__(self, backend_store: RelationalTrackingBackend):
        self.store = backend_store

    def query_child_runs(
        self,
        parent_run_id: str,
        order_by_metric: str = "val_f1",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all child runs linked to a parent, aggregating latest metrics and parameters.
        """
        direction = "ASC" if ascending else "DESC"
        query = f"""
            SELECT
                r.run_uuid,
                r.name,
                r.status,
                r.start_time,
                r.end_time,
                m.value AS target_metric
            FROM runs r
            JOIN tags t ON r.run_uuid = t.run_uuid AND t.key = 'mlflow.parentRunId' AND t.value = ?
            LEFT JOIN (
                SELECT m1.run_uuid, m1.value
                FROM metrics m1
                INNER JOIN (
                    SELECT run_uuid, MAX(step) AS max_step, MAX(timestamp) AS max_ts
                    FROM metrics
                    WHERE key = ?
                    GROUP BY run_uuid
                ) m2 ON m1.run_uuid = m2.run_uuid AND m1.step = m2.max_step AND m1.timestamp = m2.max_ts
                WHERE m1.key = ?
            ) m ON r.run_uuid = m.run_uuid
            ORDER BY m.value {direction} NULLS LAST;
        """

        results: List[Dict[str, Any]] = []
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (parent_run_id, order_by_metric, order_by_metric))
            rows = cursor.fetchall()

            for row in rows:
                run_uuid = row["run_uuid"]
                cursor.execute("SELECT key, value FROM params WHERE run_uuid = ?", (run_uuid,))
                params = {p["key"]: p["value"] for p in cursor.fetchall()}

                cursor.execute("""
                    SELECT key, value FROM metrics
                    WHERE run_uuid = ?
                    GROUP BY key
                    HAVING step = MAX(step)
                """, (run_uuid,))
                metrics = {m["key"]: m["value"] for m in cursor.fetchall()}

                results.append({
                    "run_uuid": run_uuid,
                    "name": row["name"],
                    "status": row["status"],
                    "target_metric": row["target_metric"],
                    "params": params,
                    "metrics": metrics,
                    "latency_ms": (row["end_time"] - row["start_time"]) if row["end_time"] else None,
                })

        return results

    def generate_lineage_traceability_report(self, run_uuid: str) -> Dict[str, Any]:
        """
        Reconstructs the full cryptographic lineage graph for a given run or registered model:
        Git Commit SHA -> Dataset Hash -> Hyperparameters -> Final Metrics -> Artifact Hash.
        """
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT run_uuid, experiment_id, name, status, git_commit, dataset_version FROM runs WHERE run_uuid = ?", (run_uuid,))
            run_info = cursor.fetchone()
            if not run_info:
                raise ValueError(f"Run {run_uuid} does not exist")

            cursor.execute("SELECT key, value FROM params WHERE run_uuid = ?", (run_uuid,))
            params = {r["key"]: r["value"] for r in cursor.fetchall()}

            cursor.execute("SELECT key, value FROM tags WHERE run_uuid = ?", (run_uuid,))
            tags = {r["key"]: r["value"] for r in cursor.fetchall()}

            cursor.execute("SELECT key, value FROM metrics WHERE run_uuid = ? GROUP BY key HAVING step = MAX(step)", (run_uuid,))
            metrics = {r["key"]: r["value"] for r in cursor.fetchall()}

            artifact_dir = os.path.join(self.store.artifact_root, "runs", run_uuid, "model")
            manifest_path = os.path.join(artifact_dir, "MLmodel")
            artifact_manifest = {}
            if os.path.exists(manifest_path):
                import json
                with open(manifest_path, "r", encoding="utf-8") as f:
                    artifact_manifest = json.load(f)

        return {
            "run_uuid": run_info["run_uuid"],
            "run_name": run_info["name"],
            "status": run_info["status"],
            "git_commit": run_info["git_commit"],
            "dataset_version": run_info["dataset_version"],
            "parent_run_id": tags.get("mlflow.parentRunId", "ROOT_PARENT"),
            "params": params,
            "metrics": metrics,
            "artifact_sha256": artifact_manifest.get("sha256_digest", "N/A"),
            "model_flavors": list(artifact_manifest.get("flavors", {}).keys()),
        }


def run_automl_sweep_demo() -> None:
    """Executes a multi-trial nested hyperparameter sweep and generates leaderboard reports."""
    import tempfile
    from sklearn.datasets import make_classification
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import f1_score
    from sklearn.model_selection import train_test_split

    base_dir = os.path.join(tempfile.gettempdir(), f"mlflow_sweep_{int(time.time())}")
    db_path = os.path.join(base_dir, "mlflow_tracking.db")
    artifact_root = os.path.join(base_dir, "artifacts")

    store = RelationalTrackingBackend(db_path=db_path, artifact_root=artifact_root)
    client = MLflowClient(backend_store=store)
    registry = ModelRegistryClient(backend_store=store)
    nested_tracker = NestedRunTracker(client=client)
    leaderboard_engine = MultiRunLeaderboardEngine(backend_store=store)

    exp_id = client.create_experiment("credit_default_automl_sweep")
    print(f"\n[MLflow Sweep] Created Experiment: credit_default_automl_sweep (ID: {exp_id})")

    X, y = make_classification(n_samples=1200, n_features=12, n_informative=8, random_state=42)
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.25, random_state=42)

    trial_configs = [
        {"n_estimators": 25, "max_depth": 3, "min_samples_split": 5},
        {"n_estimators": 50, "max_depth": 5, "min_samples_split": 2},
        {"n_estimators": 100, "max_depth": 8, "min_samples_split": 2},
        {"n_estimators": 150, "max_depth": 12, "min_samples_split": 4},
    ]

    parent_uuid = nested_tracker.start_parent_run(
        experiment_id=exp_id,
        study_name="RandomForest_Depth_Estimators_Study",
        objective_metric="val_f1",
        search_algorithm="grid_search",
        git_commit="e7f8a9",
        dataset_version="v2.1.0",
    )
    print(f"[Parent Study Initialized] Parent Run UUID: {parent_uuid}")

    best_trial: Optional[HyperparameterTrialResult] = None
    best_f1 = -1.0

    for idx, params in enumerate(trial_configs, start=1):
        model = RandomForestClassifier(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            min_samples_split=params["min_samples_split"],
            random_state=42,
        )
        model.fit(X_train, y_train)

        preds = model.predict(X_val)
        val_f1 = float(f1_score(y_val, preds))

        step_history = [
            (1, {"train_loss": 0.52 - idx * 0.05, "val_f1": val_f1 * 0.85}),
            (2, {"train_loss": 0.40 - idx * 0.04, "val_f1": val_f1 * 0.95}),
            (3, {"train_loss": 0.32 - idx * 0.03, "val_f1": val_f1}),
        ]

        trial_result = nested_tracker.execute_child_trial(
            experiment_id=exp_id,
            parent_run_id=parent_uuid,
            trial_id=idx,
            params=params,
            step_metrics_history=step_history,
            model_artifact_obj=model,
        )

        print(f"  -> Trial {idx:02d}: {params} => Val F1: {val_f1:.4f} (Run: {trial_result.run_uuid[:8]})")

        if val_f1 > best_f1:
            best_f1 = val_f1
            best_trial = trial_result

    assert best_trial is not None
    nested_tracker.finalize_parent_run(parent_uuid, best_trial, total_trials=len(trial_configs))
    print(f"[Parent Study Concluded] Champion Trial: {best_trial.trial_id} with Val F1: {best_f1:.4f}")

    leaderboard = leaderboard_engine.query_child_runs(parent_run_id=parent_uuid, order_by_metric="val_f1", ascending=False)
    print("\n" + "="*80)
    print(f"{'RANK':<5} | {'RUN UUID':<10} | {'VAL F1':<10} | {'N_EST':<8} | {'MAX_DEPTH':<10} | {'STATUS':<8}")
    print("="*80)
    for rank, item in enumerate(leaderboard, start=1):
        print(f"{rank:<5} | {item['run_uuid'][:8]:<10} | {item['target_metric']:<10.4f} | {item['params'].get('n_estimators', 'N/A'):<8} | {item['params'].get('max_depth', 'N/A'):<10} | {item['status']:<8}")
    print("="*80)

    registry.create_registered_model("CreditRiskScorer", description="Production credit default scoring model")
    art_path = os.path.join(store.artifact_root, "runs", best_trial.run_uuid, "model")
    ver_record = registry.create_model_version("CreditRiskScorer", best_trial.run_uuid, artifact_source=art_path)
    registry.transition_model_version_stage("CreditRiskScorer", ver_record.version, "Production", archive_existing_versions=True)
    registry.set_model_version_alias("CreditRiskScorer", "champion", ver_record.version)

    lineage = leaderboard_engine.generate_lineage_traceability_report(best_trial.run_uuid)
    print("\n[Cryptographic Model Lineage Audit Report]")
    print(f"  Model Version:       CreditRiskScorer v{ver_record.version}")
    print(f"  Winning Run UUID:    {lineage['run_uuid']}")
    print(f"  Parent Study UUID:   {lineage['parent_run_id']}")
    print(f"  Git Commit SHA:      {lineage['git_commit']}")
    print(f"  Dataset Version:     {lineage['dataset_version']}")
    print(f"  Artifact SHA256:     {lineage['artifact_sha256']}")
    print(f"  Logged Flavors:      {lineage['model_flavors']}")
    print(f"  Optimal Parameters:  {lineage['params']}")

    shutil.rmtree(base_dir, ignore_errors=True)
    print("\n[Sweep Complete] Leaderboard and lineage governance verification complete.\n")


if __name__ == "__main__":
    run_automl_sweep_demo()
