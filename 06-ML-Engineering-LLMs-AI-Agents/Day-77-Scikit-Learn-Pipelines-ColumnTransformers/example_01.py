"""
Day 77: Production-Grade Scikit-Learn Inference Pipeline with Heterogeneous ColumnTransformers
Part 1: Production Pipeline Architecture, Custom Transformers, and Low-Latency Serving

This script demonstrates an enterprise-grade ML preprocessing and inference pipeline
handling mixed data types, missing values, extreme outliers, unseen production categories,
and sub-millisecond inference serving.
"""

from __future__ import annotations

import time
from typing import List, Tuple

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler


class RobustOutlierClipper(BaseEstimator, TransformerMixin):
    """
    Stateful custom transformer calculating Interquartile Range (IQR) bounds
    strictly during fit() and clipping extreme outliers during transform().
    Protects downstream linear and tree models from pathological spikes.
    """

    def __init__(self, factor: float = 1.5) -> None:
        self.factor = float(factor)
        self.lower_bounds_: np.ndarray | None = None
        self.upper_bounds_: np.ndarray | None = None
        self.n_features_in_: int = 0
        self.feature_names_in_: np.ndarray | None = None

    def fit(self, X: pd.DataFrame | np.ndarray, y: np.ndarray | None = None) -> RobustOutlierClipper:
        """Computes Q1, Q3, and IQR boundaries per column on training data only."""
        if isinstance(X, pd.DataFrame):
            self.feature_names_in_ = np.array(X.columns)
            X_mat = X.to_numpy(dtype=np.float64)
        else:
            X_mat = np.asarray(X, dtype=np.float64)

        self.n_features_in_ = X_mat.shape[1]
        q25 = np.nanpercentile(X_mat, 25, axis=0)
        q75 = np.nanpercentile(X_mat, 75, axis=0)
        iqr = q75 - q25

        # Protect against stationary columns
        iqr_safe = np.where(iqr <= 1e-7, 1.0, iqr)
        self.lower_bounds_ = q25 - (self.factor * iqr_safe)
        self.upper_bounds_ = q75 + (self.factor * iqr_safe)
        return self

    def transform(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Clips input features to learned lower and upper IQR boundaries."""
        if self.lower_bounds_ is None or self.upper_bounds_ is None:
            raise RuntimeError("RobustOutlierClipper instance is not fitted yet.")

        if isinstance(X, pd.DataFrame):
            X_mat = X.to_numpy(dtype=np.float64)
        else:
            X_mat = np.asarray(X, dtype=np.float64)

        return np.clip(X_mat, self.lower_bounds_, self.upper_bounds_)

    def get_feature_names_out(self, input_features: List[str] | None = None) -> np.ndarray:
        if input_features is not None:
            return np.array(input_features, dtype=object)
        if self.feature_names_in_ is not None:
            return self.feature_names_in_
        return np.array([f"clipped_{i}" for i in range(self.n_features_in_)], dtype=object)


class CyclicalTemporalTransformer(BaseEstimator, TransformerMixin):
    """
    Stateless transformer projecting cyclical temporal features (hour, day of week)
    into 2D trigonometric coordinates (sine & cosine) to preserve circular continuity.
    """

    def __init__(self, period: float = 24.0) -> None:
        self.period = float(period)
        self.n_features_in_: int = 0
        self.feature_names_in_: np.ndarray | None = None

    def fit(self, X: pd.DataFrame | np.ndarray, y: np.ndarray | None = None) -> CyclicalTemporalTransformer:
        if isinstance(X, pd.DataFrame):
            self.feature_names_in_ = np.array(X.columns)
            self.n_features_in_ = X.shape[1]
        else:
            X_mat = np.asarray(X)
            self.n_features_in_ = X_mat.shape[1] if X_mat.ndim > 1 else 1
        return self

    def transform(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Projects temporal columns to [sin(2pi * t / T), cos(2pi * t / T)]."""
        if isinstance(X, pd.DataFrame):
            X_mat = X.to_numpy(dtype=np.float64)
        else:
            X_mat = np.asarray(X, dtype=np.float64)

        radians = (2.0 * np.pi * X_mat) / self.period
        sin_feat = np.sin(radians)
        cos_feat = np.cos(radians)
        return np.hstack([sin_feat, cos_feat])

    def get_feature_names_out(self, input_features: List[str] | None = None) -> np.ndarray:
        base_names = input_features or (
            list(self.feature_names_in_) if self.feature_names_in_ is not None else ["feat"]
        )
        out = []
        for name in base_names:
            out.append(f"{name}_sin")
            out.append(f"{name}_cos")
        return np.array(out, dtype=object)


def generate_synthetic_transactions(n_samples: int = 5_000, random_seed: int = 42) -> pd.DataFrame:
    """
    Generates synthetic e-commerce transaction dataset with heterogeneous feature types,
    pathological outliers, realistic missing values, and subtle fraud patterns.
    """
    np.random.seed(random_seed)

    # 1. Skewed numeric features with heavy tails and outliers
    amount = np.random.exponential(scale=65.0, size=n_samples) + 5.0
    # Inject 1.5% extreme outliers (e.g. money laundering or card testing spikes)
    outlier_idx = np.random.choice(n_samples, size=int(0.015 * n_samples), replace=False)
    amount[outlier_idx] = np.random.uniform(2000.0, 15000.0, size=len(outlier_idx))

    user_age = np.random.normal(loc=38.0, scale=12.0, size=n_samples)
    user_age = np.clip(user_age, 18.0, 85.0)

    session_duration = np.random.exponential(scale=180.0, size=n_samples) + 10.0

    # 2. Inject missing values (MCAR - Missing Completely At Random)
    user_age[np.random.choice(n_samples, size=int(0.08 * n_samples), replace=False)] = np.nan
    session_duration[np.random.choice(n_samples, size=int(0.05 * n_samples), replace=False)] = np.nan

    # 3. Categorical features
    device_types = ["mobile", "desktop", "tablet"]
    device_probs = [0.60, 0.32, 0.08]
    devices = np.random.choice(device_types, size=n_samples, p=device_probs)
    # Inject missing category
    devices[np.random.choice(n_samples, size=int(0.04 * n_samples), replace=False)] = None

    payment_methods = ["credit_card", "paypal", "apple_pay", "crypto"]
    payments = np.random.choice(payment_methods, size=n_samples, p=[0.55, 0.25, 0.15, 0.05])

    countries = ["US", "GB", "DE", "IN", "CA"]
    country_codes = np.random.choice(countries, size=n_samples, p=[0.45, 0.20, 0.15, 0.12, 0.08])

    # 4. Temporal cyclical features
    hours = np.random.randint(0, 24, size=n_samples)

    # 5. Target generation: clear multi-factor fraud signal
    fraud_logits = (
        (amount > 150.0) * 2.2
        + (payments == "crypto") * 2.5
        + ((hours >= 1) & (hours <= 4)) * 1.8
        + (session_duration < 40.0) * 1.5
        + (country_codes == "CA") * 1.2
        - 3.5
    )
    fraud_probs = 1.0 / (1.0 + np.exp(-fraud_logits))
    is_fraud = (np.random.rand(n_samples) < fraud_probs).astype(int)

    df = pd.DataFrame({
        "transaction_amount": amount,
        "user_age": user_age,
        "session_duration": session_duration,
        "device_type": devices,
        "payment_method": payments,
        "country_code": country_codes,
        "hour_of_day": hours,
        "is_fraud": is_fraud,
    })
    return df


def build_production_pipeline() -> Tuple[Pipeline, List[str], List[str], List[str]]:
    """
    Constructs unified Scikit-Learn pipeline with heterogeneous ColumnTransformer
    and Gradient Boosting classifier.
    """
    numeric_features = ["transaction_amount", "user_age", "session_duration"]
    categorical_features = ["device_type", "payment_method", "country_code"]
    temporal_features = ["hour_of_day"]

    # Numeric sub-pipeline: Median Imputation -> IQR Clipping -> Robust Scaling
    numeric_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("outlier_clipper", RobustOutlierClipper(factor=3.0)),
        ("scaler", RobustScaler()),
    ])

    # Categorical sub-pipeline: Constant Imputation -> One-Hot with Unseen Category Tolerance
    categorical_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value="MISSING")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])

    # Temporal sub-pipeline: Trigonometric Sine/Cosine Projection
    temporal_pipeline = Pipeline([
        ("cyclical", CyclicalTemporalTransformer(period=24.0)),
    ])

    # Assemble ColumnTransformer
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_pipeline, numeric_features),
            ("cat", categorical_pipeline, categorical_features),
            ("time", temporal_pipeline, temporal_features),
        ],
        remainder="drop",  # Defensively drop unexpected columns in production
    )

    # Complete End-to-End Pipeline
    full_pipeline = Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", HistGradientBoostingClassifier(max_iter=100, random_state=42)),
    ])

    return full_pipeline, numeric_features, categorical_features, temporal_features


def benchmark_inference_latency(pipeline: Pipeline, sample_payload: pd.DataFrame, iterations: int = 1_000) -> dict:
    """Measures single-record production inference latency across multiple iterations."""
    # Warmup runs to allow JIT / dynamic caching
    for _ in range(50):
        _ = pipeline.predict_proba(sample_payload)

    latencies_us: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = pipeline.predict_proba(sample_payload)
        latencies_us.append((time.perf_counter() - t0) * 1_000_000.0)

    latencies_arr = np.array(latencies_us)
    return {
        "p50_us": float(np.percentile(latencies_arr, 50)),
        "p95_us": float(np.percentile(latencies_arr, 95)),
        "p99_us": float(np.percentile(latencies_arr, 99)),
        "mean_us": float(np.mean(latencies_arr)),
    }


def main() -> None:
    print("================================================================================")
    print("DAY 77: PRODUCTION SCIKIT-LEARN HETEROGENEOUS PIPELINES & CUSTOM TRANSFORMERS")
    print("================================================================================")

    # 1. Generate Dataset
    print("[*] Generating 5,000 heterogeneous transactions with missing values & outliers...")
    dataset = generate_synthetic_transactions(n_samples=5_000, random_seed=42)
    fraud_rate = dataset["is_fraud"].mean() * 100.0
    print(f"[+] Total samples: {len(dataset):,} | Fraud positive rate: {fraud_rate:.2f}%")

    X = dataset.drop(columns=["is_fraud"])
    y = dataset["is_fraud"].to_numpy()

    # 2. Strict Train/Test Split (Preventing Data Leakage)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    print(f"[+] Training split: {len(X_train):,} rows | Test split: {len(X_test):,} rows")

    # 3. Assemble and Train Pipeline
    pipeline, num_cols, cat_cols, time_cols = build_production_pipeline()
    print("\n[*] Fitting unified Pipeline (preprocessors + HistGradientBoosting)...")
    t_train_start = time.perf_counter()
    pipeline.fit(X_train, y_train)
    train_duration_ms = (time.perf_counter() - t_train_start) * 1000.0
    print(f"[+] Pipeline training complete in {train_duration_ms:.2f} ms.")

    # 4. Evaluate Test Set Performance
    y_pred_proba = pipeline.predict_proba(X_test)[:, 1]
    y_pred = (y_pred_proba >= 0.50).astype(int)
    test_roc_auc = roc_auc_score(y_test, y_pred_proba)

    print(f"\n--- Model Test Evaluation ---")
    print(f"ROC-AUC Score: {test_roc_auc:.4f}")
    print(classification_report(y_test, y_pred, target_names=["Legitimate", "Fraud"], digits=4))

    # 5. Production Resiliency Verification: Unseen Category & Null Value Resilience
    print("\n[*] Testing Production Ingress Resiliency (Handling Unseen Categories & Nulls)...")
    # Live customer payload with:
    # - Brand new unseen country ("JP")
    # - Brand new unseen device ("smart_tv")
    # - Missing user_age and missing session_duration
    # - Huge transaction amount ($25,000.00)
    unseen_payload = pd.DataFrame([{
        "transaction_amount": 25000.0,
        "user_age": np.nan,
        "session_duration": np.nan,
        "device_type": "smart_tv",
        "payment_method": "crypto",
        "country_code": "JP",
        "hour_of_day": 3,
    }])

    fraud_probability = pipeline.predict_proba(unseen_payload)[0, 1]
    predicted_class = pipeline.predict(unseen_payload)[0]
    print(f"[+] Unseen payload handled successfully with zero exceptions!")
    print(f"    Fraud Probability : {fraud_probability * 100.0:.2f}%")
    print(f"    Predicted Label   : {'FRAUD' if predicted_class == 1 else 'LEGITIMATE'}")

    # 6. Single-Record Low-Latency Inference Benchmark
    print("\n[*] Benchmarking single-record inference latency across 1,000 iterations...")
    latency_stats = benchmark_inference_latency(pipeline, unseen_payload, iterations=1_000)
    print(f"    P50 Latency : {latency_stats['p50_us']:.1f} microseconds ({latency_stats['p50_us']/1000.0:.3f} ms)")
    print(f"    P95 Latency : {latency_stats['p95_us']:.1f} microseconds ({latency_stats['p95_us']/1000.0:.3f} ms)")
    print(f"    P99 Latency : {latency_stats['p99_us']:.1f} microseconds ({latency_stats['p99_us']/1000.0:.3f} ms)")
    print(f"    Mean Latency: {latency_stats['mean_us']:.1f} microseconds ({latency_stats['mean_us']/1000.0:.3f} ms)")

    # Assertions for enterprise production integrity
    assert test_roc_auc >= 0.75, f"Expected ROC-AUC >= 0.75, obtained {test_roc_auc:.4f}"
    assert 0.0 <= fraud_probability <= 1.0, "Probability outside valid [0, 1] bound"
    assert latency_stats["p50_us"] < 15000.0, "Inference latency exceeded 15ms SLA"
    print("\n[+] Verification Passed: All production pipeline assertions satisfied.")
    print("================================================================================")


if __name__ == "__main__":
    main()
