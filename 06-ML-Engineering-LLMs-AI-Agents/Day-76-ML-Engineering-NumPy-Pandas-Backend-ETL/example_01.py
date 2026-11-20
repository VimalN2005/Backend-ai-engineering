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
