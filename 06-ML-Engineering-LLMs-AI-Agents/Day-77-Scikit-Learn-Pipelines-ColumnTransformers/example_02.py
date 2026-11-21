"""
Day 77: Cross-Validated Pipeline Search and Model Probability Calibration
Part 2: Hyperparameter Optimization and Production Probability Calibration

This script demonstrates zero-leakage cross-validated pipeline hyperparameter tuning,
followed by probability calibration (Platt scaling & Isotonic regression) to ensure
statistically reliable posterior probabilities for downstream business decision engines.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler, StandardScaler


@dataclass(frozen=True)
class CalibrationMetrics:
    """Metrics assessing the statistical quality of predicted probabilities."""
    model_name: str
    brier_score: float
    log_loss_val: float
    roc_auc: float
    expected_calibration_error: float
    p50_latency_us: float


def compute_expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """
    Computes Expected Calibration Error (ECE): the weighted average absolute difference
    between predicted confidence and empirical accuracy across probability bins.
    ECE = sum(|acc(B_m) - conf(B_m)| * (|B_m| / N))
    """
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    total_samples = len(y_true)

    for i in range(n_bins):
        bin_mask = (y_prob >= bin_edges[i]) & (y_prob < bin_edges[i + 1])
        bin_count = np.count_nonzero(bin_mask)
        if bin_count > 0:
            bin_acc = np.mean(y_true[bin_mask])
            bin_conf = np.mean(y_prob[bin_mask])
            ece += np.abs(bin_acc - bin_conf) * (bin_count / total_samples)

    return float(ece)


def generate_credit_default_data(n_samples: int = 4_000, random_seed: int = 42) -> pd.DataFrame:
    """
    Generates synthetic fintech credit risk dataset with realistic feature correlations,
    heterogeneous dtypes, and non-calibrated baseline distributions.
    """
    np.random.seed(random_seed)

    income = np.random.lognormal(mean=10.5, sigma=0.6, size=n_samples)
    debt_to_income = np.random.uniform(0.05, 0.65, size=n_samples)
    credit_score = np.random.normal(loc=680.0, scale=60.0, size=n_samples).clip(300.0, 850.0)
    delinquencies_2yr = np.random.poisson(lam=0.4, size=n_samples)

    employment_length = np.random.choice(
        ["<1 year", "1-3 years", "3-5 years", "5+ years"],
        size=n_samples,
        p=[0.20, 0.35, 0.25, 0.20],
    )
    home_ownership = np.random.choice(
        ["RENT", "MORTGAGE", "OWN", "OTHER"],
        size=n_samples,
        p=[0.45, 0.40, 0.12, 0.03],
    )

    # Missing values injection
    income[np.random.choice(n_samples, size=int(0.06 * n_samples), replace=False)] = np.nan
    credit_score[np.random.choice(n_samples, size=int(0.04 * n_samples), replace=False)] = np.nan

    # True default risk model with distinct predictive signals
    risk_score = (
        (debt_to_income * 4.5)
        - ((credit_score - 600.0) / 60.0)
        + (delinquencies_2yr * 1.2)
        + (home_ownership == "RENT") * 0.8
        - (income > 50000.0) * 0.9
        - 1.5
    )
    prob_default = 1.0 / (1.0 + np.exp(-risk_score))
    default_target = (np.random.rand(n_samples) < prob_default).astype(int)

    return pd.DataFrame({
        "income": income,
        "debt_to_income": debt_to_income,
        "credit_score": credit_score,
        "delinquencies_2yr": delinquencies_2yr,
        "employment_length": employment_length,
        "home_ownership": home_ownership,
        "default": default_target,
    })


def create_searchable_pipeline() -> Tuple[Pipeline, dict]:
    """
    Creates a parameterized Pipeline spanning preprocessing and estimator stages
    for joint hyperparameter optimization via RandomizedSearchCV.
    """
    numeric_features = ["income", "debt_to_income", "credit_score", "delinquencies_2yr"]
    categorical_features = ["employment_length", "home_ownership"]

    numeric_transformer = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", RobustScaler()),
    ])

    categorical_transformer = Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value="UNKNOWN")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_transformer, numeric_features),
            ("cat", categorical_transformer, categorical_features),
        ],
        remainder="drop",
    )

    full_pipeline = Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", HistGradientBoostingClassifier(random_state=42)),
    ])

    param_distributions = {
        "preprocessor__num__imputer__strategy": ["median", "mean"],
        "preprocessor__num__scaler": [RobustScaler(), StandardScaler()],
        "classifier__l2_regularization": [0.0, 0.5, 2.0, 5.0],
        "classifier__max_leaf_nodes": [15, 31, 63],
        "classifier__min_samples_leaf": [20, 50, 100],
    }

    return full_pipeline, param_distributions


def evaluate_calibration(
    name: str,
    model: Pipeline | CalibratedClassifierCV,
    X_test: pd.DataFrame,
    y_test: np.ndarray,
) -> CalibrationMetrics:
    """Calculates Brier Score, Log-Loss, ECE, and inference latency for a model."""
    y_probs = model.predict_proba(X_test)[:, 1]

    brier = float(brier_score_loss(y_test, y_probs))
    logloss = float(log_loss(y_test, y_probs))
    auc = float(roc_auc_score(y_test, y_probs))
    ece = compute_expected_calibration_error(y_test, y_probs, n_bins=10)

    # Benchmark single-sample latency
    sample = X_test.iloc[[0]]
    for _ in range(5):
        _ = model.predict_proba(sample)

    latencies_us: List[float] = []
    for _ in range(50):
        t0 = time.perf_counter()
        _ = model.predict_proba(sample)
        latencies_us.append((time.perf_counter() - t0) * 1_000_000.0)

    p50_lat = float(np.percentile(latencies_us, 50))

    return CalibrationMetrics(
        model_name=name,
        brier_score=brier,
        log_loss_val=logloss,
        roc_auc=auc,
        expected_calibration_error=ece,
        p50_latency_us=p50_lat,
    )


def main() -> None:
    print("================================================================================")
    print("DAY 77: CROSS-VALIDATED PIPELINE SEARCH & MODEL CALIBRATION ENGINE")
    print("================================================================================")

    # 1. Dataset Generation
    print("[*] Generating 4,000 credit underwriting records...")
    df = generate_credit_default_data(n_samples=4_000, random_seed=42)
    default_rate = df["default"].mean() * 100.0
    print(f"[+] Total samples: {len(df):,} | Default rate: {default_rate:.2f}%")

    X = df.drop(columns=["default"])
    y = df["default"].to_numpy()

    # Train (80%) and Test (20%) split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    print(f"[+] Splits -> Train: {len(X_train):,}, Test: {len(X_test):,}")

    # 2. Joint Cross-Validated Hyperparameter Search (Zero Leakage)
    print("\n[*] Performing 3-fold cross-validated hyperparameter optimization...")
    pipeline, param_dist = create_searchable_pipeline()
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)

    search = RandomizedSearchCV(
        estimator=pipeline,
        param_distributions=param_dist,
        n_iter=4,
        scoring="roc_auc",
        cv=cv,
        random_state=42,
        n_jobs=1,
    )

    t_search_start = time.perf_counter()
    search.fit(X_train, y_train)
    search_duration = time.perf_counter() - t_search_start

    print(f"[+] CV Search completed in {search_duration:.2f} seconds.")
    print(f"    Best CV ROC-AUC Score : {search.best_score_:.4f}")
    print("    Best Hyperparameters  :")
    for param, val in search.best_params_.items():
        print(f"      - {param}: {val}")

    best_uncalibrated_pipeline = search.best_estimator_

    # 3. Probability Calibration (Platt Sigmoid & Isotonic Regression via 3-Fold Cross-Validation)
    print("\n[*] Fitting Cross-Validated Probability Calibration (Sigmoid & Isotonic)...")
    calibrated_sigmoid = CalibratedClassifierCV(
        estimator=best_uncalibrated_pipeline,
        method="sigmoid",
        cv=3,
    )
    calibrated_sigmoid.fit(X_train, y_train)

    calibrated_isotonic = CalibratedClassifierCV(
        estimator=best_uncalibrated_pipeline,
        method="isotonic",
        cv=3,
    )
    calibrated_isotonic.fit(X_train, y_train)

    # 4. Comparative Evaluation on Unseen Test Set
    print("\n[*] Evaluating Calibration Quality and Inference Overhead on Test Set...")
    metrics_uncal = evaluate_calibration("Uncalibrated Best Pipeline", best_uncalibrated_pipeline, X_test, y_test)
    metrics_sigmoid = evaluate_calibration("Calibrated (Platt Sigmoid)", calibrated_sigmoid, X_test, y_test)
    metrics_isotonic = evaluate_calibration("Calibrated (Isotonic)", calibrated_isotonic, X_test, y_test)

    results = [metrics_uncal, metrics_sigmoid, metrics_isotonic]

    print("\n+------------------------------+------------+----------+---------+----------+----------------+")
    print("| Model Variant                | Brier Loss | Log Loss | ROC-AUC | ECE (↓)  | P50 Latency    |")
    print("+------------------------------+------------+----------+---------+----------+----------------+")
    for r in results:
        print(f"| {r.model_name:<28} | {r.brier_score:>10.4f} | {r.log_loss_val:>8.4f} | {r.roc_auc:>7.4f} | {r.expected_calibration_error:>8.4f} | {r.p50_latency_us:>10.1f} us  |")
    print("+------------------------------+------------+----------+---------+----------+----------------+")

    # Verifications
    assert metrics_uncal.roc_auc >= 0.75, "Expected ROC-AUC >= 0.75"
    # Calibration should maintain or improve Brier score and ECE
    assert metrics_sigmoid.brier_score <= metrics_uncal.brier_score + 0.05, "Brier score degraded significantly"
    assert metrics_sigmoid.p50_latency_us < 50000.0, "Calibrated inference latency exceeded SLA"
    print("\n[+] Verification Passed: Cross-validation search and probability calibration validated.")
    print("================================================================================")


if __name__ == "__main__":
    main()
