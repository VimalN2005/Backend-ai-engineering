"""
Day 76: Production High-Speed NumPy & Pandas Backend ETL Pipeline
Part 1: Vectorized Feature Extraction and NumPy Linear Algebra Engine

This module demonstrates low-latency, hardware-optimized numerical transformations
for high-throughput backend services ingesting IoT/sensor telemetry.
"""

from __future__ import annotations

import io
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Generator, List, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class TelemetryBatchStats:
    """Statistical summary of processed telemetry batch."""
    total_records: int
    raw_memory_bytes: int
    optimized_memory_bytes: int
    memory_reduction_pct: float
    outliers_clipped: int
    processing_time_ms: float


class VectorizedFeatureExtractor:
    """
    High-performance feature extraction engine utilizing NumPy C-contiguous memory
    buffers, SIMD vectorization, and zero-allocation broadcasting.
    """

    def __init__(self, z_score_threshold: float = 3.0) -> None:
        self.z_score_threshold = float(z_score_threshold)

    @staticmethod
    def ensure_c_contiguous(arr: np.ndarray) -> np.ndarray:
        """
        Guarantees that the underlying array buffer is row-major C-contiguous.
        Prevents cache-line thrashing and enables SIMD prefetching.
        """
        if not arr.flags["C_CONTIGUOUS"]:
            return np.ascontiguousarray(arr)
        return arr

    def compute_z_score_normalization(
        self, feature_matrix: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Computes vectorized Z-score standardization across features using broadcasting.
        Shape: (N, M) matrix normalized by (M,) mean and std vectors.
        Returns:
            normalized_matrix: np.ndarray (float32)
            means: np.ndarray (float32)
            stds: np.ndarray (float32)
        """
        matrix = self.ensure_c_contiguous(feature_matrix).astype(np.float32, copy=False)
        
        # Calculate column-wise means and standard deviations (axis=0)
        means = np.mean(matrix, axis=0)  # Shape (M,)
        stds = np.std(matrix, axis=0)    # Shape (M,)

        # Protect against division by zero in stationary features
        epsilon = 1e-7
        stds_safe = np.where(stds < epsilon, 1.0, stds)

        # Broadcast subtraction and division: (N, M) - (M,) operates with zero duplicate allocations
        normalized = (matrix - means) / stds_safe
        return normalized, means, stds

    def vectorized_outlier_filter(
        self, normalized_matrix: np.ndarray
    ) -> Tuple[np.ndarray, int]:
        """
        Detects and clips anomalies exceeding z-score threshold using SIMD boolean masking.
        Returns the clipped array and count of clipped outlier values.
        """
        arr = self.ensure_c_contiguous(normalized_matrix)
        outlier_mask = np.abs(arr) > self.z_score_threshold
        clipped_count = int(np.count_nonzero(outlier_mask))

        # Vectorized in-place clipping
        clipped_array = np.clip(arr, -self.z_score_threshold, self.z_score_threshold)
        return clipped_array, clipped_count

    @staticmethod
    def compute_vectorized_rolling_average(
        series: np.ndarray, window_size: int = 5
    ) -> np.ndarray:
        """
        Computes moving average over 1D signal using 1D convolution (SIMD ufunc).
        Eliminates Python sliding window iteration overhead.
        """
        if len(series) < window_size:
            return series.copy()
        weights = np.ones(window_size, dtype=np.float32) / float(window_size)
        # 'same' mode maintains output dimension matching input series
        return np.convolve(series, weights, mode="same")



class MemoryOptimizer:
    """
    Production memory compression engine for Pandas DataFrames.
    Downcasts numeric dtypes and converts low-cardinality strings to categorical representations.
    """

    @classmethod
    def optimize_dataframe(cls, df: pd.DataFrame) -> Tuple[pd.DataFrame, int, int]:
        """
        Compresses DataFrame memory footprint.
        Returns:
            (optimized_df, initial_memory_bytes, optimized_memory_bytes)
        """
        initial_mem = int(df.memory_usage(deep=True).sum())
        optimized = df.copy()

        for col in optimized.columns:
            col_type = optimized[col].dtype

            # Downcast integer columns
            if pd.api.types.is_integer_dtype(col_type):
                c_min = optimized[col].min()
                c_max = optimized[col].max()
                if c_min >= np.iinfo(np.int8).min and c_max <= np.iinfo(np.int8).max:
                    optimized[col] = optimized[col].astype(np.int8)
                elif c_min >= np.iinfo(np.int16).min and c_max <= np.iinfo(np.int16).max:
                    optimized[col] = optimized[col].astype(np.int16)
                elif c_min >= np.iinfo(np.int32).min and c_max <= np.iinfo(np.int32).max:
                    optimized[col] = optimized[col].astype(np.int32)
                else:
                    optimized[col] = optimized[col].astype(np.int64)

            # Downcast floating point columns
            elif pd.api.types.is_float_dtype(col_type):
                # Standardize ML feature floats to float32
                optimized[col] = optimized[col].astype(np.float32)

            # Convert low-cardinality string/object columns to category
            elif pd.api.types.is_object_dtype(col_type) or isinstance(col_type, pd.StringDtype):
                num_unique = optimized[col].nunique()
                num_total = len(optimized[col])
                if num_total > 0 and (num_unique / num_total) < 0.50:
                    optimized[col] = optimized[col].astype("category")

        optimized_mem = int(optimized.memory_usage(deep=True).sum())
        return optimized, initial_mem, optimized_mem


class StreamingETLPipeline:
    """
    Streaming chunked backend ETL processor with bounded memory footprint.
    Simulates ingesting large data streams and flushing to columnar Parquet format.
    """

    def __init__(self, chunk_size: int = 10_000) -> None:
        self.chunk_size = chunk_size
        self.feature_extractor = VectorizedFeatureExtractor(z_score_threshold=3.0)

    @staticmethod
    def generate_synthetic_telemetry_stream(
        total_records: int = 50_000,
    ) -> Generator[pd.DataFrame, None, None]:
        """
        Generates streaming batches of synthetic IoT sensor telemetry.
        Fields: device_id (int), region (str), voltage (float), temperature (float), vibration (float)
        """
        np.random.seed(42)
        chunk_size = 10_000
        regions = ["us-east-1", "us-west-2", "eu-central-1", "ap-southeast-1"]

        for start_idx in range(0, total_records, chunk_size):
            current_batch_size = min(chunk_size, total_records - start_idx)
            
            # Intentionally create default heavyweight dtypes (int64, float64, object)
            batch_data = {
                "device_id": np.random.randint(1000, 9999, size=current_batch_size, dtype=np.int64),
                "region": np.random.choice(regions, size=current_batch_size),
                "voltage": np.random.normal(loc=220.0, scale=15.0, size=current_batch_size).astype(np.float64),
                "temperature": np.random.normal(loc=65.0, scale=12.0, size=current_batch_size).astype(np.float64),
                "vibration": np.random.exponential(scale=2.5, size=current_batch_size).astype(np.float64),
            }
            # Inject synthetic extreme outliers (anomalous spikes)
            if current_batch_size > 10:
                batch_data["temperature"][:5] = [250.0, -90.0, 310.0, -120.0, 400.0]
                batch_data["voltage"][:5] = [500.0, 10.0, 600.0, 5.0, 750.0]

            yield pd.DataFrame(batch_data)
