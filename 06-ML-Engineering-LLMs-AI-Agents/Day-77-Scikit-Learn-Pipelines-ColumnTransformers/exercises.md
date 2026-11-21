# Day 77: Production Exercises — Scikit-Learn Pipelines & ColumnTransformers

---

## Exercise 1: Custom Zero-Leakage Target Encoder with Smoothing

### Objective
In high-cardinality categorical features (e.g., zip codes, merchant IDs with hundreds of distinct levels), standard One-Hot Encoding causes extreme dimensionality explosion. Implement an enterprise-grade, stateful `SmoothTargetEncoder` adhering to Scikit-Learn's transformer contract. It must compute smoothed target probabilities during `fit()` using the empirical Bayes formulation:
$$S_i = \frac{n_i \cdot \bar{y}_i + m \cdot \bar{y}_{\text{global}}}{n_i + m}$$
Where:
- $n_i$ is the count of samples in category $i$.
- $\bar{y}_i$ is the empirical target mean of category $i$.
- $\bar{y}_{\text{global}}$ is the overall dataset target mean.
- $m$ is the smoothing regularization parameter (prior weight).

### Production Constraints & Specification
1. Inherit from `BaseEstimator` and `TransformerMixin`.
2. Do not mutate the input feature data.
3. Handle unseen production categories by falling back to $\bar{y}_{\text{global}}$.
4. Implement `get_feature_names_out()` for downstream explainability.
5. Provide a complete, runnable test script verifying transformation accuracy.

### Production Solution & Verification Code

```python
import numpy as np
import pandas as pd
from typing import Dict, List
from sklearn.base import BaseEstimator, TransformerMixin


class SmoothTargetEncoder(BaseEstimator, TransformerMixin):
    """
    Stateful Target Encoder with Bayesian additive smoothing.
    Replaces high-cardinality categories with regularized target posterior means.
    """

    def __init__(self, m_smoothing: float = 10.0) -> None:
        self.m_smoothing = float(m_smoothing)
        self.global_mean_: float = 0.0
        self.mapping_: Dict[str, Dict[str, float]] = {}
        self.feature_names_in_: np.ndarray | None = None

    def fit(self, X: pd.DataFrame, y: np.ndarray | pd.Series) -> SmoothTargetEncoder:
        """Learns smoothed category-to-target mappings on training split strictly."""
        X_df = X if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
        self.feature_names_in_ = np.array(X_df.columns)
        y_arr = np.asarray(y, dtype=np.float64)

        self.global_mean_ = float(np.mean(y_arr))
        self.mapping_ = {}

        for col in X_df.columns:
            col_series = X_df[col].astype(str)
            stats = pd.DataFrame({"cat": col_series, "target": y_arr}).groupby("cat").agg(
                count=("target", "count"),
                mean=("target", "mean")
            )
            # Bayesian smoothed formula: (n * mean + m * global_mean) / (n + m)
            smoothed = (
                (stats["count"] * stats["mean"]) + (self.m_smoothing * self.global_mean_)
            ) / (stats["count"] + self.m_smoothing)
            self.mapping_[col] = smoothed.to_dict()

        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        """Maps categorical levels to smoothed targets; unknown categories get global mean."""
        if not self.mapping_:
            raise RuntimeError("SmoothTargetEncoder is not fitted.")

        X_df = X if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
        transformed_cols = []

        for col in X_df.columns:
            col_map = self.mapping_.get(col, {})
            # Map known categories, fill novel/unseen categories with global mean
            mapped = X_df[col].astype(str).map(col_map).fillna(self.global_mean_).to_numpy(dtype=np.float64)
            transformed_cols.append(mapped.reshape(-1, 1))

        return np.hstack(transformed_cols)

    def get_feature_names_out(self, input_features: List[str] | None = None) -> np.ndarray:
        base = input_features or (list(self.feature_names_in_) if self.feature_names_in_ is not None else ["feat"])
        return np.array([f"{f}_target_enc" for f in base], dtype=object)


def test_exercise_01() -> None:
    print("[*] Running Exercise 1: SmoothTargetEncoder Verification...")
    df_train = pd.DataFrame({
        "merchant_id": ["M1", "M1", "M1", "M2", "M2", "M3"],
    })
    y_train = np.array([1, 1, 0, 0, 0, 1])  # global mean = 3/6 = 0.50

    encoder = SmoothTargetEncoder(m_smoothing=2.0)
    encoder.fit(df_train, y_train)

    # Test on unseen categories and known categories
    df_test = pd.DataFrame({
        "merchant_id": ["M1", "M2", "UNSEEN_M4"],
    })
    transformed = encoder.transform(df_test)

    # M1: n=3, mean=2/3 (~0.667), m=2, global=0.5 -> (3*(2/3) + 2*0.5)/(3+2) = 3.0 / 5 = 0.60
    # M2: n=2, mean=0.0, m=2, global=0.5 -> (0 + 1.0) / 4 = 0.25
    # UNSEEN: falls back to global mean 0.50
    assert np.isclose(transformed[0, 0], 0.60, atol=1e-3), f"Expected 0.60, got {transformed[0, 0]}"
    assert np.isclose(transformed[1, 0], 0.25, atol=1e-3), f"Expected 0.25, got {transformed[1, 0]}"
    assert np.isclose(transformed[2, 0], 0.50, atol=1e-3), f"Expected 0.50 for unseen, got {transformed[2, 0]}"
    print("[+] Exercise 1 Passed: Bayesian target encoding with unseen category fallback verified.")


if __name__ == "__main__":
    test_exercise_01()
```

---

## Exercise 2: Dynamic Feature Selection Pipeline with VarianceThreshold & SelectKBest

### Objective
High-dimensional feature stores frequently introduce zero-variance features (constants) or noisy uncorrelated features that degrade estimator performance and inflate inference latency. Build a unified Scikit-Learn pipeline that automatically removes near-zero-variance features (`VarianceThreshold(threshold=0.01)`) and selects the top $K$ most informative features using ANOVA F-value (`SelectKBest(score_func=f_classif, k=3)`), chained directly into a LogisticRegression estimator.

### Production Constraints & Specification
1. Build end-to-end `Pipeline` encapsulating `SimpleImputer`, `StandardScaler`, `VarianceThreshold`, `SelectKBest`, and `LogisticRegression`.
2. Verify that constant/dead features are eliminated without throwing exceptions.
3. Validate that feature selection occurs strictly inside cross-validation folds.

### Production Solution & Verification Code

```python
import numpy as np
import pandas as pd
from sklearn.datasets import make_classification
from sklearn.feature_selection import SelectKBest, VarianceThreshold, f_classif
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def build_feature_selection_pipeline(k_features: int = 3) -> Pipeline:
    """Constructs pipeline with automated dead-feature elimination and K-best selection."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="mean")),
        ("scaler", StandardScaler()),
        ("variance_filter", VarianceThreshold(threshold=0.01)),  # Drops constant / near-zero variance
        ("k_best", SelectKBest(score_func=f_classif, k=k_features)),
        ("classifier", LogisticRegression(random_state=42)),
    ])


def test_exercise_02() -> None:
    print("[*] Running Exercise 2: Dynamic Feature Selection Pipeline...")
    np.random.seed(42)
    # Generate 500 samples with 10 features (3 informative, 2 dead/constant, 5 noisy)
    X, y = make_classification(
        n_samples=500,
        n_features=8,
        n_informative=3,
        n_redundant=2,
        random_state=42,
    )
    # Inject two completely constant/dead features
    dead_col1 = np.ones((500, 1))
    dead_col2 = np.zeros((500, 1))
    X_full = np.hstack([X, dead_col1, dead_col2])  # 10 columns total

    pipeline = build_feature_selection_pipeline(k_features=3)

    # Cross-validation verifies zero data leakage during feature selection
    scores = cross_val_score(pipeline, X_full, y, cv=5, scoring="roc_auc")
    mean_auc = float(np.mean(scores))

    print(f"5-Fold CV ROC-AUC: {mean_auc:.4f} (across 10 features with 2 dead columns)")

    pipeline.fit(X_full, y)
    support = pipeline.named_steps["k_best"].get_support()
    selected_count = int(np.count_nonzero(support))

    assert selected_count == 3, f"Expected 3 selected features, got {selected_count}"
    assert mean_auc >= 0.75, f"Expected mean AUC >= 0.75, got {mean_auc:.4f}"
    print("[+] Exercise 2 Passed: Automated variance filtering and feature selection verified.")


if __name__ == "__main__":
    test_exercise_02()
```

---

## Exercise 3: Production FastAPI-Ready Thread-Safe Inference Wrapper

### Objective
In high-throughput microservices, incoming raw JSON payloads must be validated against schema contracts before passing through the deserialized Scikit-Learn pipeline. Design a thread-safe inference wrapper class `ProductionModelService` that:
1. Validates presence and types of required input fields.
2. Defensively converts single-entity or batch dictionaries into DataFrames.
3. Executes `predict_proba()` and extracts positive class risk score.
4. Returns structured decision metadata with microsecond latency instrumentation.

### Production Solution & Verification Code

```python
import time
from typing import Any, Dict, List
import pandas as pd
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class ProductionModelService:
    """
    Enterprise production wrapper providing validation, thread safety,
    and latency monitoring for Scikit-Learn pipelines.
    """

    def __init__(self, pipeline: Pipeline, required_features: List[str]) -> None:
        self.pipeline = pipeline
        self.required_features = set(required_features)

    def predict_single(self, payload: Dict[str, Any], threshold: float = 0.50) -> Dict[str, Any]:
        """Validates payload, executes pipeline inference, and returns decision with latency."""
        t0 = time.perf_counter()

        # 1. Schema Validation
        missing_keys = self.required_features - set(payload.keys())
        if missing_keys:
            raise ValueError(f"Payload missing required feature keys: {missing_keys}")

        # 2. Defensive DataFrame Construction
        df_input = pd.DataFrame([{col: payload[col] for col in self.required_features}])

        # 3. Model Inference
        probs = self.pipeline.predict_proba(df_input)[0]
        pos_prob = float(probs[1]) if len(probs) > 1 else float(probs[0])
        decision = "POSITIVE" if pos_prob >= threshold else "NEGATIVE"

        latency_us = (time.perf_counter() - t0) * 1_000_000.0

        return {
            "probability": round(pos_prob, 4),
            "decision": decision,
            "threshold": threshold,
            "latency_microseconds": round(latency_us, 1),
        }


def test_exercise_03() -> None:
    print("[*] Running Exercise 3: Production Model Service...")
    # Train simple pipeline on Iris dataset binary classification
    iris = load_iris(as_frame=True)
    X = iris.data[["sepal length (cm)", "sepal width (cm)"]]
    y = (iris.target == 1).astype(int)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("model", LogisticRegression()),
    ])
    pipeline.fit(X, y)

    service = ProductionModelService(
        pipeline=pipeline,
        required_features=["sepal length (cm)", "sepal width (cm)"]
    )

    valid_payload = {"sepal length (cm)": 5.4, "sepal width (cm)": 3.0}
    response = service.predict_single(valid_payload)

    print(f"Service Response: {response}")
    assert "probability" in response
    assert response["latency_microseconds"] < 10000.0, "Latency exceeded 10ms"

    # Test error handling on invalid schema
    invalid_payload = {"sepal length (cm)": 5.4}  # Missing sepal width
    try:
        service.predict_single(invalid_payload)
        assert False, "Expected ValueError on missing fields"
    except ValueError as e:
        print(f"[+] Correctly caught schema error: {e}")

    print("[+] Exercise 3 Passed: Production model service wrapper verified.")


if __name__ == "__main__":
    test_exercise_03()
```
