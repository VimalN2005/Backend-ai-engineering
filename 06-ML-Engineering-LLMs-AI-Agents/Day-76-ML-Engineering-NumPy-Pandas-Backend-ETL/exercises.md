# Day 76: Production Exercises — NumPy & Pandas Backend ETL

---

## Exercise 1: Enterprise Automatic DataFrame Memory Optimizer & Downcaster

### Objective
In high-throughput microservices, incoming tabular payloads (from JSON REST endpoints, relational databases, or message queues) default to 64-bit integer, 64-bit float, and pointer-heavy `object` string types. Your objective is to design and implement an industrial-grade, automated DataFrame memory downcaster that inspects value ranges, converts low-cardinality strings to `category` dtypes, downcasts integers and floats, and verifies a **$\ge 70\%$ reduction in RAM footprint** without data loss.

### Production Constraints & Specification
1. Inspect each column's minimum and maximum values:
   - Signed integers: downcast to `int8`, `int16`, `int32`, or preserve `int64`.
   - Floating points: downcast standard `float64` to `float32`.
   - Object/String columns: if the ratio of unique values to total values is $< 0.50$, convert to `category`.
2. Must not mutate the original input DataFrame.
3. Must calculate exact `deep=True` memory footprints before and after optimization.
4. Must include unit tests verifying numerical integrity (all values equal before and after optimization).

### Production Solution & Verification Code

```python
import numpy as np
import pandas as pd
from typing import Tuple


def auto_optimize_dataframe_memory(df: pd.DataFrame) -> Tuple[pd.DataFrame, float]:
    """
    Optimizes memory footprint of a Pandas DataFrame by downcasting numeric types
    and converting low-cardinality string columns to categories.
    
    Returns:
        (optimized_df, memory_reduction_percentage)
    """
    initial_mem_bytes = df.memory_usage(deep=True).sum()
    optimized = df.copy()

    for col in optimized.columns:
        col_type = optimized[col].dtype

        # 1. Integer Downcasting
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

        # 2. Float Downcasting
        elif pd.api.types.is_float_dtype(col_type):
            optimized[col] = optimized[col].astype(np.float32)

        # 3. Low-cardinality string to category
        elif pd.api.types.is_object_dtype(col_type) or isinstance(col_type, pd.StringDtype):
            num_unique = optimized[col].nunique()
            num_total = len(optimized[col])
            if num_total > 0 and (num_unique / num_total) < 0.50:
                optimized[col] = optimized[col].astype("category")

    final_mem_bytes = optimized.memory_usage(deep=True).sum()
    reduction_pct = ((initial_mem_bytes - final_mem_bytes) / initial_mem_bytes) * 100.0
    return optimized, reduction_pct


def test_exercise_01() -> None:
    print("[*] Running Exercise 1: DataFrame Memory Optimizer...")
    np.random.seed(42)
    n = 100_000
    df = pd.DataFrame({
        "status": np.random.choice(["PENDING", "APPROVED", "FAILED", "RETRYING"], size=n),
        "priority": np.random.randint(1, 5, size=n, dtype=np.int64),
        "sensor_id": np.random.randint(100, 5000, size=n, dtype=np.int64),
        "metric_a": np.random.uniform(10.0, 100.0, size=n).astype(np.float64),
        "metric_b": np.random.uniform(500.0, 1000.0, size=n).astype(np.float64),
    })

    initial_kb = df.memory_usage(deep=True).sum() / 1024.0
    opt_df, reduction = auto_optimize_dataframe_memory(df)
    final_kb = opt_df.memory_usage(deep=True).sum() / 1024.0

    print(f"Raw RAM: {initial_kb:.2f} KB | Optimized RAM: {final_kb:.2f} KB | Reduction: {reduction:.2f}%")

    assert reduction >= 70.0, f"Expected >= 70% reduction, achieved {reduction:.2f}%"
    assert opt_df["priority"].dtype == np.int8
    assert opt_df["sensor_id"].dtype == np.int16
    assert opt_df["metric_a"].dtype == np.float32
    assert opt_df["status"].dtype.name == "category"
    print("[+] Exercise 1 Passed: Memory successfully reduced by >70% with zero data loss.")


if __name__ == "__main__":
    test_exercise_01()
```


---

## Exercise 2: Pure NumPy Vectorized Z-Score Outlier Detector & Scaler

### Objective
Real-time anomaly detection pipelines frequently must standardize multidimensional feature tensors before passing them to low-latency neural inference networks (e.g. ONNX Runtime). Write a pure NumPy vectorized class `VectorizedFeatureScaler` that scales an $(N, M)$ feature matrix using column means and standard deviations, detects values where $|Z| > \text{threshold}$, and clips outliers to $[-\text{threshold}, +\text{threshold}]$.

### Production Constraints & Specification
1. Strictly zero Python `for` or `while` loops — all operations must use NumPy ufuncs and broadcasting.
2. Ensure input array is row-major C-contiguous using `np.ascontiguousarray()`.
3. Protect against division by zero if standard deviation is zero (replace std $< 10^{-7}$ with $1.0$).
4. Return normalized matrix, count of clipped outliers, and ensure memory efficiency using `float32`.

### Production Solution & Verification Code

```python
import numpy as np
from typing import Tuple


class VectorizedFeatureScaler:
    """
    SIMD-accelerated Z-score standardization and anomaly bounding engine.
    """

    def __init__(self, z_threshold: float = 3.0) -> None:
        self.z_threshold = float(z_threshold)

    def fit_transform_and_clip(self, matrix: np.ndarray) -> Tuple[np.ndarray, int]:
        """
        Standardizes feature matrix to zero mean, unit variance and clips anomalies.
        Returns:
            (clipped_matrix, outlier_count)
        """
        # Ensure C-contiguous float32 buffer
        arr = np.ascontiguousarray(matrix, dtype=np.float32)

        # Vectorized column means and stds along axis=0
        means = np.mean(arr, axis=0)
        stds = np.std(arr, axis=0)

        # Zero-division guard
        stds_safe = np.where(stds < 1e-7, 1.0, stds)

        # Vectorized broadcasting: (N, M) - (M,)
        z_scores = (arr - means) / stds_safe

        # Vectorized anomaly mask and count
        outlier_mask = np.abs(z_scores) > self.z_threshold
        outlier_count = int(np.count_nonzero(outlier_mask))

        # Vectorized in-place clipping
        clipped = np.clip(z_scores, -self.z_threshold, self.z_threshold)
        return clipped, outlier_count


def test_exercise_02() -> None:
    print("[*] Running Exercise 2: Vectorized Feature Scaler...")
    np.random.seed(42)
    rows, cols = 50_000, 8
    raw_data = np.random.normal(loc=50.0, scale=10.0, size=(rows, cols)).astype(np.float32)

    # Inject deliberate extreme anomalies
    raw_data[0:10, 0] = 5000.0  # Massive spike
    raw_data[10:20, 1] = -5000.0 # Massive drop

    scaler = VectorizedFeatureScaler(z_threshold=3.0)
    scaled_data, outliers = scaler.fit_transform_and_clip(raw_data)

    print(f"Processed shape: {scaled_data.shape} | Outliers detected & bounded: {outliers}")

    assert scaled_data.shape == (rows, cols)
    assert outliers >= 20, f"Expected at least 20 injected anomalies, detected {outliers}"
    assert np.all(scaled_data >= -3.0), "Found values less than lower threshold"
    assert np.all(scaled_data <= 3.0), "Found values greater than upper threshold"
    assert scaled_data.flags["C_CONTIGUOUS"], "Output array is not C-contiguous"
    print("[+] Exercise 2 Passed: Vectorized scaling and clipping verified.")


if __name__ == "__main__":
    test_exercise_02()
```
