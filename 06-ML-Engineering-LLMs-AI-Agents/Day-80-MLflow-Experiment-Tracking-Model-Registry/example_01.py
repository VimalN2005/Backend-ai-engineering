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
