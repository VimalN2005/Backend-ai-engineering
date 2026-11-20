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
