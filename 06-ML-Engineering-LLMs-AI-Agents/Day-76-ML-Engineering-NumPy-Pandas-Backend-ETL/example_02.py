"""
Day 76: Out-of-Core Memory-Mapped Arrays and High-Speed ETL Benchmark Suite
Part 1: Out-of-Core Memory-Mapped Array Batch Processor (np.memmap)

This module demonstrates processing multi-gigabyte numerical datasets that exceed
physical RAM capacity using virtual memory OS page paging via NumPy memmap.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BenchmarkResult:
    """Benchmark result for an execution paradigm."""
    paradigm_name: str
    elapsed_time_ms: float
    throughput_records_sec: float
    speedup_multiplier: float
    memory_footprint_kb: float


class OutOfCoreMemoryMapEngine:
    """
    Manages disk-backed virtual memory arrays via np.memmap.
    Enables zero-RAM-footprint random access and batch slicing on large numerical arrays.
    """

    def __init__(self, filename: str, shape: Tuple[int, int], dtype: np.dtype = np.float32) -> None:
        self.filename = filename
        self.shape = shape
        self.dtype = dtype

    def create_and_populate(self, seed: int = 42) -> None:
        """
        Creates a binary file on disk and writes data directly to virtual memory pages.
        Flushes dirty pages to disk without keeping the full array in active heap memory.
        """
        np.random.seed(seed)
        # Open in 'w+' mode (read/write, create or truncate)
        mmap_arr = np.memmap(
            self.filename,
            dtype=self.dtype,
            mode="w+",
            shape=self.shape,
        )

        rows, cols = self.shape
        batch_size = 20_000

        # Stream write in small blocks to avoid RAM spikes
        for start_idx in range(0, rows, batch_size):
            end_idx = min(start_idx + batch_size, rows)
            count = end_idx - start_idx
            # Generate random features: [sensor_reading, baseline, scaling_factor, noise]
            chunk_data = np.random.randn(count, cols).astype(self.dtype)
            mmap_arr[start_idx:end_idx] = chunk_data

        # Explicitly flush changes to disk storage
        mmap_arr.flush()
        del mmap_arr  # Close handle

    def stream_batch_aggregation(self, batch_size: int = 25_000) -> Tuple[np.ndarray, np.ndarray]:
        """
        Calculates column-wise mean and variance across disk-backed array
        in streaming passes with strictly bounded RAM.
        """
        # Open in read-only mode 'r'
        mmap_arr = np.memmap(
            self.filename,
            dtype=self.dtype,
            mode="r",
            shape=self.shape,
        )

        rows, cols = self.shape
        running_sum = np.zeros(cols, dtype=np.float64)
        running_sq_sum = np.zeros(cols, dtype=np.float64)

        for start in range(0, rows, batch_size):
            end = min(start + batch_size, rows)
            # Only loads the requested chunk into RAM (via OS page table)
            slice_chunk = mmap_arr[start:end]
            running_sum += np.sum(slice_chunk, axis=0, dtype=np.float64)
            running_sq_sum += np.sum(slice_chunk**2, axis=0, dtype=np.float64)

        mean = (running_sum / rows).astype(self.dtype)
        variance = ((running_sq_sum / rows) - (mean**2)).astype(self.dtype)

        del mmap_arr
        return mean, variance


import math


class VectorizationBenchmarkSuite:
    """
    Empirical benchmark suite measuring latency and throughput across four data
    transformation execution paradigms in Python backend systems.
    Transformation target: f(x, y, z) = sqrt(x^2 + y^2) / (1.0 + exp(-z))
    """

    def __init__(self, num_records: int = 50_000) -> None:
        self.num_records = num_records
        np.random.seed(42)
        # Generate non-negative x, y and bounded z to avoid numerical overflow
        self.x_arr = np.random.uniform(1.0, 100.0, size=num_records).astype(np.float64)
        self.y_arr = np.random.uniform(1.0, 100.0, size=num_records).astype(np.float64)
        self.z_arr = np.random.uniform(-5.0, 5.0, size=num_records).astype(np.float64)

        # Prepare pure Python collections
        self.x_list = self.x_arr.tolist()
        self.y_list = self.y_arr.tolist()
        self.z_list = self.z_arr.tolist()

        # Prepare Pandas DataFrame
        self.df = pd.DataFrame({"x": self.x_arr, "y": self.y_arr, "z": self.z_arr})

    def run_pure_python_loop(self) -> Tuple[List[float], float]:
        """Paradigm 1: Pure Python scalar loop using standard library math."""
        start = time.perf_counter()
        results: List[float] = [0.0] * self.num_records
        x_l, y_l, z_l = self.x_list, self.y_list, self.z_list
        for i in range(self.num_records):
            hypot = math.sqrt(x_l[i] * x_l[i] + y_l[i] * y_l[i])
            sigmoid_denom = 1.0 + math.exp(-z_l[i])
            results[i] = hypot / sigmoid_denom
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return results, elapsed_ms

    def run_pandas_iterrows(self) -> Tuple[List[float], float]:
        """Paradigm 2: Pandas df.iterrows() (Antipattern creating Series per row)."""
        start = time.perf_counter()
        results: List[float] = []
        for _, row in self.df.iterrows():
            hypot = math.sqrt(row["x"] ** 2 + row["y"] ** 2)
            denom = 1.0 + math.exp(-row["z"])
            results.append(hypot / denom)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return results, elapsed_ms

    def run_pandas_apply(self) -> Tuple[pd.Series, float]:
        """Paradigm 3: Pandas .apply() with Python lambda (Row-wise unboxing)."""
        start = time.perf_counter()
        results = self.df.apply(
            lambda r: math.sqrt(r["x"] ** 2 + r["y"] ** 2) / (1.0 + math.exp(-r["z"])),
            axis=1,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return results, elapsed_ms

    def run_numpy_vectorized_simd(self) -> Tuple[np.ndarray, float]:
        """Paradigm 4: Pure NumPy SIMD Vectorized UFuncs (Hardware optimized)."""
        start = time.perf_counter()
        # Direct C-level SIMD vectorized instructions
        hypot = np.sqrt(self.x_arr**2 + self.y_arr**2)
        denom = 1.0 + np.exp(-self.z_arr)
        results = hypot / denom
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return results, elapsed_ms

    def execute_all(self) -> List[BenchmarkResult]:
        """Runs all 4 benchmarks and computes comparative speedups and throughput."""
        print(f"[*] Running 4-way benchmark on {self.num_records:,} records...")

        # 1. Pure Python Loop
        res_py, time_py = self.run_pure_python_loop()

        # 2. Pandas iterrows
        res_iter, time_iter = self.run_pandas_iterrows()

        # 3. Pandas apply
        res_apply, time_apply = self.run_pandas_apply()

        # 4. NumPy Vectorized SIMD
        res_np, time_np = self.run_numpy_vectorized_simd()

        # Validate mathematical parity across all paradigms
        py_arr = np.array(res_py, dtype=np.float64)
        iter_arr = np.array(res_iter, dtype=np.float64)
        apply_arr = res_apply.to_numpy(dtype=np.float64)

        np.testing.assert_allclose(py_arr, res_np, rtol=1e-5, atol=1e-5)
        np.testing.assert_allclose(iter_arr, res_np, rtol=1e-5, atol=1e-5)
        np.testing.assert_allclose(apply_arr, res_np, rtol=1e-5, atol=1e-5)

        baseline_time = time_iter  # Slowest baseline (iterrows)

        benchmarks = [
            BenchmarkResult("Pandas df.iterrows() [Antipattern]", time_iter, (self.num_records / (time_iter / 1000.0)), 1.0, sys.getsizeof(res_iter) / 1024.0),
            BenchmarkResult("Pandas df.apply() [Lambda]", time_apply, (self.num_records / (time_apply / 1000.0)), baseline_time / time_apply, res_apply.memory_usage() / 1024.0),
            BenchmarkResult("Pure Python for-loop [Scalar]", time_py, (self.num_records / (time_py / 1000.0)), baseline_time / time_py, sys.getsizeof(res_py) / 1024.0),
            BenchmarkResult("NumPy SIMD Vectorized [C UFunc]", time_np, (self.num_records / (time_np / 1000.0)), baseline_time / time_np, res_np.nbytes / 1024.0),
        ]
        return benchmarks


def main() -> None:
    print("================================================================================")
    print("DAY 76: OUT-OF-CORE MEMORY-MAPPED ARRAYS & VECTORIZATION BENCHMARK SUITE")
    print("================================================================================")

    # Part 1: Out-of-Core Memory Map Verification
    temp_dir = tempfile.gettempdir()
    mmap_path = os.path.join(temp_dir, "test_large_matrix.dat")
    shape = (100_000, 4)  # 100,000 rows x 4 float32 columns = 1.6 MB on disk

    print(f"\n[1] Testing Out-of-Core Processing with np.memmap on shape {shape}...")
    mmap_engine = OutOfCoreMemoryMapEngine(filename=mmap_path, shape=shape, dtype=np.float32)
    mmap_engine.create_and_populate(seed=42)

    disk_size_mb = os.path.getsize(mmap_path) / (1024.0 * 1024.0)
    print(f"[+] Successfully wrote disk-backed memmap binary ({disk_size_mb:.2f} MB).")

    # Streaming batch aggregations without loading the dataset into RAM
    means, variances = mmap_engine.stream_batch_aggregation(batch_size=20_000)
    print(f"[+] Out-of-Core Stream Aggregated Means    : {means}")
    print(f"[+] Out-of-Core Stream Aggregated Variance : {variances}")

    # Clean up disk file
    if os.path.exists(mmap_path):
        os.remove(mmap_path)
    print("[+] Cleaned up temporary memmap file.")

    # Part 2: Vectorization Benchmark Suite
    print("\n[2] Executing Comparative Performance Benchmark Suite...")
    num_records = 50_000
    suite = VectorizationBenchmarkSuite(num_records=num_records)
    results = suite.execute_all()

    print("\n+------------------------------------+------------+--------------------+----------------+")
    print("| Execution Paradigm                 | Wall Time  | Throughput         | Speedup vs     |")
    print("|                                    | (ms)       | (records/sec)      | iterrows       |")
    print("+------------------------------------+------------+--------------------+----------------+")
    for res in results:
        print(f"| {res.paradigm_name:<34} | {res.elapsed_time_ms:>8.2f}ms | {res.throughput_records_sec:>16,.0f} | {res.speedup_multiplier:>12.1f}x |")
    print("+------------------------------------+------------+--------------------+----------------+")

    # Extract NumPy and iterrows results for assertion
    numpy_res = next(r for r in results if "NumPy" in r.paradigm_name)
    python_res = next(r for r in results if "Pure Python" in r.paradigm_name)
    iterrows_res = next(r for r in results if "iterrows" in r.paradigm_name)

    print(f"\n[+] NumPy SIMD Speedup vs Pandas iterrows : {numpy_res.speedup_multiplier:.1f}x")
    print(f"[+] NumPy SIMD Speedup vs Pure Python    : {(python_res.elapsed_time_ms / numpy_res.elapsed_time_ms):.1f}x")

    assert numpy_res.speedup_multiplier >= 50.0, f"Expected NumPy speedup >= 50x vs iterrows, got {numpy_res.speedup_multiplier:.1f}x"
    assert (python_res.elapsed_time_ms / numpy_res.elapsed_time_ms) >= 3.0, "Expected NumPy speedup >= 3x vs Pure Python"
    print("[+] All performance assertions satisfied.")
    print("================================================================================")


if __name__ == "__main__":
    main()
