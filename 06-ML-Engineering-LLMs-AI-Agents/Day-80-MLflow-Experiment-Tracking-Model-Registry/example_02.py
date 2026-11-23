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
