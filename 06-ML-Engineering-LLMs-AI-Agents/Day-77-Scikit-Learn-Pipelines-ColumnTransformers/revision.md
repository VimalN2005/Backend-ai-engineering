# Day 77: Rapid 5-Minute Revision & Flashcards — Scikit-Learn Pipelines

---

## 1. High-Yield Architecture Summary (5-Minute Read)

1. **Why Unified Pipelines Are Mandatory**: Decoupled preprocessing and model scripts cause training-serving skew, non-deterministic bugs, and catastrophic data leakage. A Scikit-Learn `Pipeline` encapsulates raw ingress, stateful transformations, and the estimator in one atomic, serializable object.
2. **`Pipeline` vs `ColumnTransformer` vs `FeatureUnion`**:
   - `Pipeline`: Sequential linear chaining ($A \to B \to C$).
   - `ColumnTransformer`: Parallel column partitioning ($X_{\text{num}} \to T_1$, $X_{\text{cat}} \to T_2$, horizontally stacked).
   - `FeatureUnion`: Parallel feature transformation on the same columns ($X \to [T_1(X), T_2(X)]$).
3. **The Custom Transformer Invariants**:
   - Inherit from `BaseEstimator` and `TransformerMixin`.
   - Pure `__init__`: Keyword arguments only, no `*args` or `**kwargs`, no state calculation, no mutation.
   - Stateful `fit(X, y)`: Calculates state strictly from training split, stores in attributes with trailing underscore (`self.median_`), returns `self`.
   - `transform(X)`: Applies transformation without mutating input in place.
   - `get_feature_names_out()`: Generates string column names for explainability.
4. **Handling Production Heterogeneity**:
   - Skewed Numbers: `SimpleImputer(strategy='median')` followed by `RobustScaler()` (median and IQR).
   - Categorical: `OneHotEncoder(handle_unknown='ignore', sparse_output=False)` to prevent 500 crashes on novel live categories.
   - Temporal / Cyclical: Trigonometric projection into $[\sin(2\pi t / T), \cos(2\pi t / T)]$.
5. **Zero-Leakage Cross-Validation**:
   - Hyperparameter optimization (`RandomizedSearchCV` / `GridSearchCV`) must wrap the entire pipeline so transformers are fitted independently inside each cross-validation fold.
6. **Probability Calibration (`CalibratedClassifierCV`)**:
   - Tree and margin classifiers produce uncalibrated probabilities.
   - Use Platt Scaling (`method='sigmoid'`) or Isotonic Regression (`method='isotonic'`) with `cv=3` or `cv=5` to guarantee accurate probabilities for production risk thresholding.

---

## 2. Key Mathematical Formulas & Invariants

- **IQR Outlier Boundaries**:
  $$\text{IQR} = Q_3 - Q_1, \quad \text{Lower} = Q_1 - 1.5 \times \text{IQR}, \quad \text{Upper} = Q_3 + 1.5 \times \text{IQR}$$
- **Trigonometric Cyclical Projection**:
  $$x_{\sin} = \sin\left(\frac{2\pi \cdot t}{T}\right), \quad x_{\cos} = \cos\left(\frac{2\pi \cdot t}{T}\right)$$
- **Expected Calibration Error (ECE)**:
  $$\text{ECE} = \sum_{m=1}^{M} \frac{|B_m|}{N} \left| \text{acc}(B_m) - \text{conf}(B_m) \right|$$
- **Brier Score Loss (Calibration Quality)**:
  $$\text{Brier} = \frac{1}{N} \sum_{i=1}^{N} (f_i - o_i)^2$$

---

## 3. Top 10 Active Recall Flashcards

### Card 1: What is the primary operational cause of training-serving skew in production ML?
**Answer**: Preprocessing transformations being implemented separately in training scripts vs inference services, causing discrepancies in imputation values, categorical encodings, or scaling parameters.

### Card 2: How does wrapping preprocessing inside a `Pipeline` prevent data leakage during cross-validation?
**Answer**: The cross-validation harness fits the pipeline strictly on the $K-1$ training folds during each split, ensuring scalers and imputers calculate statistics solely from training samples and never see the validation fold.

### Card 3: Why should `OneHotEncoder(handle_unknown='ignore')` always be set in production?
**Answer**: Without `handle_unknown='ignore'`, any novel category submitted by an API client that was not present in the historical training dataset triggers an unhandled `ValueError`, crashing the microservice.

### Card 4: What is the purpose of `remainder='drop'` in `ColumnTransformer`?
**Answer**: It acts as a defensive security and schema boundary, automatically dropping any unvalidated, newly added, or leaked upstream columns (like target labels, PII, or UUIDs) rather than passing them to the model.

### Card 5: Why does Scikit-Learn forbid `*args` and `**kwargs` in custom transformer constructors?
**Answer**: `sklearn.base.clone()` inspects constructor signatures via reflection to replicate estimators during cross-validation and grid searches; variable arguments prevent automated parameter cloning.

### Card 6: What naming convention must stateful custom transformers follow for attributes calculated in `fit()`?
**Answer**: All learned attributes must end with a single trailing underscore (e.g. `self.means_`, `self.iqr_bounds_`).

### Card 7: Why is `StandardScaler` risky for features with extreme outliers, and what is the production alternative?
**Answer**: Mean ($\mu$) and standard deviation ($\sigma$) are sensitive to extreme values, compressing typical data points into a narrow range. The alternative is `RobustScaler`, which scales via the median and Interquartile Range (IQR).

### Card 8: How do you project cyclical features like hour-of-day into continuous numerical spaces?
**Answer**: By transforming the linear integer $t \in [0, 23]$ into a 2D trigonometric coordinate pair $[\sin(2\pi t / 24), \cos(2\pi t / 24)]$, preserving the circular distance between hour 23 and hour 0.

### Card 9: What is the difference between Platt Scaling and Isotonic Regression in `CalibratedClassifierCV`?
**Answer**: Platt Scaling (`method='sigmoid'`) fits a logistic regression model (parametric, less prone to overfitting on small data); Isotonic Regression (`method='isotonic'`) fits a non-parametric monotonic step function (more expressive, but overfits on small datasets).

### Card 10: Why must probability calibration never be fitted on the base estimator's training data?
**Answer**: Base tree models often overfit on training data with near-100% confidence. Calibrating on these overconfident predictions produces severely overconfident calibrated probabilities; calibration must be performed using cross-validation (`cv=3` or `cv=5`).
