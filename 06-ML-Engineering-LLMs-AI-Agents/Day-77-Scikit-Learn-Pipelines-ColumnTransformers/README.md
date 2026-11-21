# Day 77: Scikit-Learn Production Pipelines, Feature Engineering & ColumnTransformers

## 1. Production Problem Statement: Training-Serving Skew & Data Leakage

In production machine learning systems, over 80% of model failures in live deployment originate not from poor machine learning algorithms, but from flawed **data preprocessing infrastructure**. When data transformations (scaling, imputation, encoding, feature selection) are implemented as loose, ad-hoc Python scripts or disjointed database stored procedures, two catastrophic production failure modes inevitably emerge:

1. **Catastrophic Data Leakage During Training**:
   - If an imputer or scaler calculates statistics (e.g., column mean, standard deviation, median, category frequencies) across the *entire* dataset before cross-validation splitting, information from the validation/test folds bleeds into the training set.
   - The model achieves artificially inflated validation metrics during experimentation ($AUC = 0.96$), but degrades severely ($AUC = 0.65$) upon deployment to live customer traffic.
2. **Training-Serving Skew (Pre-inference Divergence)**:
   - In production microservices, an inference request arrives as an isolated JSON dictionary representing a single entity (e.g., an individual transaction or user click).
   - If the preprocessing logic applied in the training notebook differs by even a single rounding convention, imputation default, or missing categorical one-hot column from the production REST API backend, the model produces erratic, corrupted predictions.
3. **Fragile State Management**:
   - Machine learning pipelines are stateful graphs: scalers store $\mu$ and $\sigma$, imputers store medians, encoders store category mappings. Decoupling the trained model weights from its feature transformers turns model versioning and rollbacks into an operational nightmare.

The solution is the **Scikit-Learn Unified Pipeline Architecture**: encapsulating raw feature ingestion, stateful imputation, heterogeneous column routing, custom domain feature transformations, and estimator inference into a single atomic, serializable object.

---

## 2. Production Architecture Blueprint: Heterogeneous ColumnTransformer Pipeline

In an enterprise ML backend service (e.g. FastAPI/gRPC inference service), raw JSON payloads pass directly into a unified `Pipeline` containing a multi-branch `ColumnTransformer`.

```
+-------------------------------------------------------------------------------------------------------+
|                          PRODUCTION SCIKIT-LEARN PIPELINE ARCHITECTURE                                |
+-------------------------------------------------------------------------------------------------------+
|                                                                                                       |
|  Raw Ingress JSON Payload (FastAPI / Kafka Stream)                                                    |
|  {"amount": 124.50, "device_age": null, "country": "US", "timestamp": "2026-11-21T18:45:00Z"}      |
|                                                  |                                                    |
|                                                  v                                                    |
|  +-------------------------------------------------------------------------------------------------+  |
|  |                             UNIFIED SCIKIT-LEARN PIPELINE CONTAINER                             |  |
|  |                                                                                                 |  |
|  |  +-------------------------------------------------------------------------------------------+  |  |
|  |  |                                  ColumnTransformer Branching                              |  |  |
|  |  |                                                                                           |  |  |
|  |  |  [Numeric Features]        [Categorical Features]           [Temporal / Datetime Features] |  |  |
|  |  |    ("amount", "age")             ("country", "device")              ("timestamp")          |  |  |
|  |  |           |                               |                               |               |  |  |
|  |  |           v                               v                               v               |  |  |
|  |  |  +---------------------+       +---------------------+         +---------------------+    |  |  |
|  |  |  | SimpleImputer       |       | SimpleImputer       |         | Custom Cyclical     |    |  |  |
|  |  |  | (strategy='median') |       | (strategy='constant'|         | Transformer         |    |  |  |
|  |  |  +---------------------+       |  fill='MISSING')    |         | (sin/cos encoding)  |    |  |  |
|  |  |           |                    +---------------------+         +---------------------+    |  |  |
|  |  |           v                               v                               |               |  |  |
|  |  |  +---------------------+       +---------------------+                    |               |  |  |
|  |  |  | Custom Outlier      |       | OneHotEncoder       |                    |               |  |  |
|  |  |  | Clipper (IQR-based) |       | (handle_unknown=    |                    |               |  |  |
|  |  |  +---------------------+       |    'ignore')        |                    |               |  |  |
|  |  |           |                    +---------------------+                    |               |  |  |
|  |  |           v                               |                               |               |  |  |
|  |  |  +---------------------+                  |                               |               |  |  |
|  |  |  | RobustScaler        |                  |                               |               |  |  |
|  |  |  | (quantile scaling)  |                  |                               |               |  |  |
|  |  |  +---------------------+                  |                               |               |  |  |
|  |  |           |                               |                               |               |  |  |
|  |  |           +-------------------------------+-------------------------------+               |  |  |
|  |  |                                           |                                               |  |  |
|  |  |                                           v                                               |  |  |
|  |  |                        Horizontal Concatenation (Feature Matrix X)                        |  |  |
|  |  +-------------------------------------------------------------------------------------------+  |  |
|  |                                              |                                                  |  |
|  |                                              v                                                  |  |
|  |  +-------------------------------------------------------------------------------------------+  |  |
|  |  | VarianceThreshold (removes zero-variance / constant features)                             |  |  |
|  |  +-------------------------------------------------------------------------------------------+  |  |
|  |                                              |                                                  |  |
|  |                                              v                                                  |  |
|  |  +-------------------------------------------------------------------------------------------+  |  |
|  |  | Calibrated Classifier (CalibratedClassifierCV wrapping GradientBoosting / Random Forest)   |  |  |
|  |  | - Outputs well-calibrated probabilities P(Y=1|X) for business risk thresholds             |  |  |
|  |  +-------------------------------------------------------------------------------------------+  |  |
|  |                                                                                                 |  |
|  +-------------------------------------------------------------------------------------------------+  |
|                                                  |                                                    |
|                                                  v                                                    |
|  Predictive Output: {"fraud_probability": 0.0412, "decision": "APPROVE", "latency_us": 412}          |
+-------------------------------------------------------------------------------------------------------+
```

---

## 3. The Core Building Blocks: Pipeline vs ColumnTransformer vs FeatureUnion

Understanding when to combine these three Scikit-Learn primitives is essential for robust architecture:

| Component | Ingestion Dimension | Primary Responsibility | Common Production Use Case |
| :--- | :--- | :--- | :--- |
| **`Pipeline`** | Sequential ($X \to X_1 \to X_2 \to \dots \to Y$) | Chains transformer steps sequentially, culminating in an optional final Estimator. Enforces no data leakage during `fit()`. | Wrapping an entire end-to-end model workflow or a single column's multi-step transformation. |
| **`ColumnTransformer`** | Parallel Partitioning ($X_{cols} \to \text{Transform}_i \to X'$) | Applies independent transformation pipelines to distinct subsets of columns (numeric, categorical, text) and horizontally stacks results. | Ingesting heterogeneous DataFrames directly without manual slicing or separate dataframes. |
| **`FeatureUnion`** | Parallel Stacking ($X \to \text{Extractors}_{1\dots k} \to [X_1, X_2]$) | Applies multiple independent transformers to the *same* feature subset and concatenates their outputs side-by-side. | Extracting multiple representations (e.g. TF-IDF + sentiment scores + character n-grams) from a single text field. |

---

## 4. Anatomy of Custom Transformers: Stateless vs Stateful

Real-world enterprise business logic requires custom transformations not available in standard Scikit-Learn packages (e.g., custom outlier bounding, cyclical trigonometric projections, domain-specific financial ratios).

To participate seamlessly in `Pipeline` and `ColumnTransformer` workflows, custom transformers must adhere to Scikit-Learn's formal estimator contract:

### 1. The Scikit-Learn Transformer Contract
- Inherit from `sklearn.base.BaseEstimator` and `sklearn.base.TransformerMixin`.
- Implement `__init__(self, ...)` with explicit keyword arguments and **no `*args` or `**kwargs`**. The constructor must never modify arguments or fit parameters; it only stores configurations.
- Implement `fit(self, X, y=None)`:
  - If **Stateful**: Learns statistics strictly from the training data (e.g. IQR percentiles, quantile thresholds, target statistics) and stores them with a trailing underscore (e.g. `self.lower_bound_`, `self.upper_bound_`). Must return `self`.
  - If **Stateless**: Simply returns `self` without calculating dataset-wide state.
- Implement `transform(self, X)`: Applies the transformation using learned parameters. Must return a 2D NumPy array or Pandas DataFrame without modifying the input $X$ in place.
- Implement `get_feature_names_out(self, input_features=None)`: Required for feature tracking and downstream explainability (SHAP/LIME).

---

## 5. Heterogeneous Preprocessing Best Practices

### Numerical Features
1. **Imputation**: Never use mean imputation when features exhibit heavy skew or extreme outliers; use `SimpleImputer(strategy='median')` or `KNNImputer`.
2. **Scaling**: Standard `StandardScaler` ($\frac{x - \mu}{\sigma}$) is hypersensitive to extreme outliers because $\mu$ and $\sigma$ are unbounded. Use `RobustScaler`, which scales via the median and Interquartile Range ($IQR = Q_3 - Q_1$).
3. **Outlier Bounding**: Clip values at $[Q_1 - 1.5 \times IQR, Q_3 + 1.5 \times IQR]$ using a fitted custom transformer before scaling.

### Categorical Features
1. **Unseen Production Categories**: In live traffic, API clients inevitably submit new, unseen categorical values. Standard encoders throw an unhandled `KeyError` or `ValueError`. Always configure:
   ```python
   OneHotEncoder(handle_unknown='ignore', sparse_output=False)
   ```
2. **Cardinality Management**: For high-cardinality features ($>50$ unique categories), avoid one-hot explosion ($1000$ sparse columns). Use frequency encoding or regularized Target Encoding (`sklearn.preprocessing.TargetEncoder`).

### Cyclical & Temporal Features
Temporal features (hour of day: $0\dots 23$, day of week: $0\dots 6$) possess circular continuity (hour 23 is 1 hour away from hour 0). Treating them as linear integers introduces artificial discontinuities. Project them into 2D trigonometric coordinates:
$$x_{\sin} = \sin\left(\frac{2\pi \times t}{T}\right), \quad x_{\cos} = \cos\left(\frac{2\pi \times t}{T}\right)$$

---

## 6. Model Probability Calibration (`CalibratedClassifierCV`)

In mission-critical backend microservices (credit underwriting, fraud detection, automated trading), decision engines rely on true posterior probabilities:
$$P(Y = 1 \mid X)$$
Many top-performing estimators (Support Vector Machines, Naive Bayes, Random Forests, boosted trees) generate uncalibrated, distorted probability scores:
- **Sigmoidal Platt Scaling** (`method='sigmoid'`): Fits a logistic regression model mapping raw classifier decision scores to well-calibrated probabilities. Optimal when distortion is sigmoidal and training data is moderate.
- **Isotonic Regression** (`method='isotonic'`): Non-parametric monotonic step function. Most flexible, but prone to severe overfitting on small datasets ($<1000$ samples).

Using `CalibratedClassifierCV(estimator, cv=5, method='sigmoid')` ensures that when your API returns a risk score of $0.85$, exactly $85\%$ of those historical transactions were truly fraudulent.

---

## 7. Production Engineering Checklist

- [ ] **Zero Data Leakage**: Are all scalers, imputers, and encoders fitted *strictly* within cross-validation folds via `Pipeline`?
- [ ] **Unknown Category Resiliency**: Does `OneHotEncoder` explicitly declare `handle_unknown='ignore'` to prevent 500 server crashes on novel input?
- [ ] **Safe Remainder Handling**: Is `ColumnTransformer(remainder='drop')` explicitly declared to prevent unvalidated columns from leaking into feature matrices?
- [ ] **No In-Place Mutation**: Do custom transformers make defensive copies of input DataFrames/arrays before modifying values?
- [ ] **Stateless Constructor Invariant**: Does `__init__()` strictly store parameters without calculating dataset statistics or accepting `*args`?
- [ ] **Probability Calibration**: If downstream systems apply business decision thresholds, has the classifier been calibrated using `CalibratedClassifierCV`?
- [ ] **Thread-Safe Serialization**: Can the pipeline be pickled and served concurrently across multi-worker ASGI processes (FastAPI / Gunicorn / Uvicorn)?
