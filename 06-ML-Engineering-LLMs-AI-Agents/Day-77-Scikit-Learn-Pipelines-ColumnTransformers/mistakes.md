# Day 77: Production Mistakes & Antipatterns — Scikit-Learn Pipelines & ColumnTransformers

In production machine learning systems, subtle flaws in feature preprocessing pipelines are far more dangerous than algorithmic bugs. Algorithmic bugs typically crash with a stack trace; pipeline flaws silently corrupt data, create massive data leakage, degrade production prediction accuracy, or cause sudden microservice outages. Below are seven critical enterprise antipatterns and their production-grade remedies.

---

### Mistake 1: Fitting Scalers or Imputers Before `train_test_split` (Data Leakage)

#### The Anti-Pattern
Fitting preprocessing transformations on the entire dataset prior to splitting into train and test folds:
```python
# BROKEN: Catastrophic data leakage
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)  # Calculates global mean and std across ALL data!

X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2)
model.fit(X_train, y_train)
```

#### Why It Breaks Production
1. The global mean ($\mu$) and standard deviation ($\sigma$) contain information from the test set.
2. The model learns subtle distributional cues from future test samples that will never exist in live production inference.
3. Offline validation scores appear outstanding ($AUC = 0.94$), but in live production, the model suffers massive degradation ($AUC = 0.68$) because live data does not match the leaked distribution.

#### Production Solution
Always encapsulate preprocessing and model training inside an atomic `Pipeline`, and fit *strictly* on training splits:
```python
# PRODUCTION GRADE: Zero leakage
pipeline = Pipeline([
    ("scaler", StandardScaler()),
    ("model", LogisticRegression()),
])
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
pipeline.fit(X_train, y_train)  # Scaler learns mu and sigma ONLY from X_train
```

---

### Mistake 2: Crashing Microservices with Unseen Production Categories

#### The Anti-Pattern
Using default or naive categorical one-hot encoding without handling unknown categories:
```python
# BROKEN: Default encoder raises ValueError on novel categories
ohe = OneHotEncoder()  # Defaults to handle_unknown='error'
```

#### Why It Breaks Production
1. In live production traffic, client requests inevitably submit new categories (e.g. a new country code `"SG"` or new device `"smart_watch"`) that were not present in the historical training batch.
2. The encoder throws an unhandled `ValueError: Found unknown categories ['SG'] in column 0 during transform`.
3. The inference API returns an HTTP 500 Internal Server Error, dropping customer requests and triggering PagerDuty alerts.

#### Production Solution
Always explicitly configure `handle_unknown='ignore'`:
```python
# PRODUCTION GRADE: Silently encodes unseen categories as all-zeros vector
ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
```

---

### Mistake 3: Permissive Column Passthrough via `remainder='passthrough'`

#### The Anti-Pattern
Allowing unmentioned columns to pass through `ColumnTransformer` automatically:
```python
# BROKEN: Permissive passthrough
preprocessor = ColumnTransformer(
    transformers=[("num", StandardScaler(), ["amount", "age"])],
    remainder="passthrough",
)
```

#### Why It Breaks Production
1. If the database schema evolves or an upstream API client adds new fields (e.g. `raw_user_id`, `ssn`, `timestamp_str`, `session_uuid`), `remainder='passthrough'` appends these raw unvalidated columns to the feature matrix.
2. String or UUID columns cause downstream NumPy/Scipy linear algebra kernels to crash with `TypeError: float() argument must be a string or a real number`.
3. If an upstream service accidentally includes the target label `is_fraud`, the target leaks directly into the model without warning!

#### Production Solution
Always explicitly enforce `remainder='drop'`:
```python
# PRODUCTION GRADE: Defensively drops any column not explicitly registered
preprocessor = ColumnTransformer(
    transformers=[("num", StandardScaler(), ["amount", "age"])],
    remainder="drop",
)
```

---

### Mistake 4: In-Place Mutation of Input Data in Custom Transformers

#### The Anti-Pattern
Mutating the input DataFrame or NumPy array inside the `transform()` method:
```python
# BROKEN: In-place mutation corrupts caller data structures
class BrokenTransformer(BaseEstimator, TransformerMixin):
    def transform(self, X):
        X["new_feature"] = X["a"] / X["b"]  # Mutates original DataFrame!
        return X
```

#### Why It Breaks Production
1. In Python, DataFrames and NumPy arrays are passed by reference. Mutating `X` directly alters the caller's working data structures outside the pipeline.
2. In multi-threaded API servers or cross-validation routines where the same dataset reference is reused across multiple runs, subsequent iterations receive corrupted, already-mutated features.
3. This creates non-deterministic bugs and race conditions that are virtually impossible to reproduce in testing.

#### Production Solution
Always operate on defensive copies or construct new arrays:
```python
# PRODUCTION GRADE: Defensive copy
class RobustTransformer(BaseEstimator, TransformerMixin):
    def transform(self, X):
        X_df = X.copy() if isinstance(X, pd.DataFrame) else pd.DataFrame(X).copy()
        X_df["new_feature"] = X_df["a"] / (X_df["b"] + 1e-7)
        return X_df
```

---

### Mistake 5: Violating the Scikit-Learn Estimator Constructor Contract

#### The Anti-Pattern
Accepting variable arguments (`*args`, `**kwargs`), performing validation, or calculating state inside `__init__`:
```python
# BROKEN: Violates Scikit-Learn estimator contract
class BrokenEstimator(BaseEstimator, TransformerMixin):
    def __init__(self, threshold=0.5, *args, **kwargs):
        self.threshold = threshold
        self.fitted_mean_ = 0.0  # Setting fitted attributes in __init__ is forbidden!
        if threshold < 0:
            raise ValueError()    # Complex validation in __init__ breaks cloning!
```

#### Why It Breaks Production
1. Scikit-Learn relies heavily on `sklearn.base.clone()`, which inspects constructor parameters via reflection. If `__init__` contains `*args` or `**kwargs`, `clone()` fails.
2. `GridSearchCV` and `RandomizedSearchCV` clone the pipeline hundreds of times. If `__init__` mutates arguments or performs heavy logic, grid searches crash or produce corrupted hyperparameter states.

#### Production Solution
Follow the strict constructor contract: only store keyword arguments verbatim:
```python
# PRODUCTION GRADE: Pure constructor
class CompliantEstimator(BaseEstimator, TransformerMixin):
    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold  # Exact same attribute name, no mutation
        self.fitted_mean_: float | None = None
```

---

### Mistake 6: Omitting `get_feature_names_out` in Custom Transformers

#### The Anti-Pattern
Implementing custom transformers without the `get_feature_names_out` method:
```python
# BROKEN: Missing feature name tracking
class FeatureExpander(BaseEstimator, TransformerMixin):
    def fit(self, X, y=None): return self
    def transform(self, X): return np.hstack([X, X**2])
    # No get_feature_names_out() implemented!
```

#### Why It Breaks Production
1. In modern production ML backends, explainability frameworks (SHAP, LIME, Integrated Gradients) and governance tools require inspecting exact feature names.
2. If any step in a `ColumnTransformer` lacks `get_feature_names_out`, calling `preprocessor.get_feature_names_out()` raises an `AttributeError`, breaking automated model audit pipelines.

#### Production Solution
Always implement `get_feature_names_out`:
```python
# PRODUCTION GRADE: Full feature lineage tracking
class FeatureExpander(BaseEstimator, TransformerMixin):
    def get_feature_names_out(self, input_features=None):
        base = input_features if input_features is not None else ["x"]
        names = []
        for f in base:
            names.extend([f"{f}", f"{f}_squared"])
        return np.array(names, dtype=object)
```

---

### Mistake 7: Calibrating Probabilities on the Same Training Data Used to Fit the Base Estimator

#### The Anti-Pattern
Fitting `CalibratedClassifierCV` using the exact same training set without cross-validation:
```python
# BROKEN: Massive overfitting on uncalibrated training probabilities
base_model = RandomForestClassifier()
base_model.fit(X_train, y_train)

# WRONG: Calibrating on base model's overfitted training outputs
calibrator = CalibratedClassifierCV(estimator=base_model, method="sigmoid", cv="prefit")
calibrator.fit(X_train, y_train)
```

#### Why It Breaks Production
1. Complex non-linear models (Random Forests, Gradient Boosted Trees) frequently achieve near-zero training error ($R^2 \approx 1.0$, Train Accuracy $\approx 99\%$).
2. Fitting a sigmoid (Platt scaling) or isotonic regression on these artificially overconfident training predictions calibrates the model to believe its predictions are $100\%$ certain.
3. On live production traffic, the calibrated probabilities remain severely overconfident and distorted, completely defeating the purpose of probability calibration.

#### Production Solution
Always calibrate using internal cross-validation (`cv=3` or `cv=5`) or a dedicated holdout calibration dataset:
```python
# PRODUCTION GRADE: Out-of-fold cross-validated calibration
calibrator = CalibratedClassifierCV(
    estimator=base_pipeline,
    method="sigmoid",
    cv=3,  # Base model is fitted on 2 folds and calibrated on the 3rd fold out-of-sample!
)
calibrator.fit(X_train, y_train)
```
