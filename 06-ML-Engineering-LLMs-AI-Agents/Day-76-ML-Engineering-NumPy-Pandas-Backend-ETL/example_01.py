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


    def process_stream(
        self, output_parquet_path: str, total_records: int = 50_000
    ) -> TelemetryBatchStats:
        """
        Executes end-to-end chunked ETL:
        1. Ingests raw batch DataFrame with heavyweight dtypes.
        2. Compresses memory footprint via type downcasting.
        3. Extracts and standardizes numerical features using SIMD NumPy vectorization.
        4. Detects and bounds extreme anomalies.
        5. Persists transformed batches to columnar Snappy-compressed Parquet.
        """
        start_time = time.perf_counter()
        total_raw_mem = 0
        total_opt_mem = 0
        total_outliers = 0
        records_processed = 0

        parquet_writer: pq.ParquetWriter | None = None

        try:
            for raw_chunk in self.generate_synthetic_telemetry_stream(total_records=total_records):
                # 1. Downcast and compress types in Pandas
                optimized_chunk, raw_mem, opt_mem = MemoryOptimizer.optimize_dataframe(raw_chunk)
                total_raw_mem += raw_mem
                total_opt_mem += opt_mem

                # 2. Extract numeric features into contiguous NumPy float32 matrix
                feature_cols = ["voltage", "temperature", "vibration"]
                raw_matrix = optimized_chunk[feature_cols].to_numpy(dtype=np.float32)

                # 3. Vectorized Z-Score normalization and outlier clipping
                normalized_matrix, _, _ = self.feature_extractor.compute_z_score_normalization(raw_matrix)
                clipped_matrix, clipped_count = self.feature_extractor.vectorized_outlier_filter(normalized_matrix)
                total_outliers += clipped_count

                # 4. Vectorized rolling convolution on vibration signal
                rolling_vibe = self.feature_extractor.compute_vectorized_rolling_average(
                    clipped_matrix[:, 2], window_size=5
                )

                # 5. Attach engineered features back to DataFrame
                optimized_chunk["voltage_norm"] = clipped_matrix[:, 0]
                optimized_chunk["temp_norm"] = clipped_matrix[:, 1]
                optimized_chunk["vibe_norm"] = clipped_matrix[:, 2]
                optimized_chunk["vibe_rolling_avg"] = rolling_vibe

                # 6. Stream write to Columnar Apache Parquet via PyArrow
                arrow_table = pa.Table.from_pandas(optimized_chunk)
                if parquet_writer is None:
                    parquet_writer = pq.ParquetWriter(
                        output_parquet_path,
                        schema=arrow_table.schema,
                        compression="snappy",
                    )
                parquet_writer.write_table(arrow_table)
                records_processed += len(raw_chunk)

        finally:
            if parquet_writer is not None:
                parquet_writer.close()

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        reduction_pct = (
            ((total_raw_mem - total_opt_mem) / total_raw_mem) * 100.0
            if total_raw_mem > 0
            else 0.0
        )

        return TelemetryBatchStats(
            total_records=records_processed,
            raw_memory_bytes=total_raw_mem,
            optimized_memory_bytes=total_opt_mem,
            memory_reduction_pct=reduction_pct,
            outliers_clipped=total_outliers,
            processing_time_ms=elapsed_ms,
        )


def main() -> None:
    print("================================================================================")
    print("DAY 76: PRODUCTION HIGH-SPEED NUMPY & PANDAS BACKEND ETL PIPELINE")
    print("================================================================================")

    output_parquet = "telemetry_processed_output.parquet"
    total_records = 50_000

    pipeline = StreamingETLPipeline(chunk_size=10_000)
    print(f"[*] Ingesting and transforming {total_records:,} sensor telemetry events...")

    stats = pipeline.process_stream(output_parquet_path=output_parquet, total_records=total_records)

    # Read back and inspect Parquet metadata and shape
    parquet_file = pq.ParquetFile(output_parquet)
    disk_size_kb = os.path.getsize(output_parquet) / 1024.0
    num_row_groups = parquet_file.num_row_groups
    num_cols = len(parquet_file.schema)
    num_rows = parquet_file.metadata.num_rows
    parquet_file.close()

    print("\n--- Pipeline Execution Metrics ---")
    print(f"Total Records Processed   : {stats.total_records:,}")
    print(f"Raw In-Memory Size        : {stats.raw_memory_bytes / 1024.0:.2f} KB")
    print(f"Optimized In-Memory Size  : {stats.optimized_memory_bytes / 1024.0:.2f} KB")
    print(f"RAM Memory Reduction      : {stats.memory_reduction_pct:.2f}%")
    print(f"Outliers Clipped (Z > 3.0): {stats.outliers_clipped:,}")
    print(f"Snappy Parquet Disk Size  : {disk_size_kb:.2f} KB")
    print(f"Parquet Row Groups        : {num_row_groups}")
    print(f"Parquet Total Schema Cols : {num_cols}")
    print(f"End-to-End Latency        : {stats.processing_time_ms:.2f} ms")
    print(f"Throughput                : {(stats.total_records / (stats.processing_time_ms / 1000.0)):,.0f} records/sec")

    # Production Integrity Verifications
    assert stats.total_records == total_records, f"Record count mismatch: {stats.total_records}"
    assert stats.memory_reduction_pct >= 50.0, f"Expected >=50% RAM reduction, got {stats.memory_reduction_pct}%"
    assert stats.outliers_clipped > 0, "Expected outliers to be clipped by Z-score filter"
    assert num_rows == total_records, "Parquet row count mismatch"
    print("\n[+] Verification Passed: All data engineering assertions satisfied.")

    # Clean up parquet file
    if os.path.exists(output_parquet):
        os.remove(output_parquet)
    print("[+] Cleaned up temporary parquet artifact.")
    print("================================================================================")


if __name__ == "__main__":
    main()
