# Day 77: Principal ML Systems Interview Questions & Answers

---

### Question 1: What is the architectural difference between `Pipeline`, `ColumnTransformer`, and `FeatureUnion` in Scikit-Learn? When should each be chosen in an enterprise ML system?

#### Candidate Answer
In production machine learning systems, modularity and data flow topology determine whether preprocessing is clean and reproducible or fragile and leak-prone.

1. **`Pipeline` (Sequential Topology)**:
   - Chains transformers sequentially in a single linear execution graph:
     $$X \to T_1(X) \to T_2(T_1(X)) \to \dots \to \text{Estimator}$$
   - Every intermediate step must implement `fit()` and `transform()`; the final step implements `fit()` and either `predict()` or `transform()`.
   - **When to Use**: To assemble an entire end-to-end model workflow or to encapsulate multi-step transformations on a single homogenous feature subset (e.g., Impute $\to$ Scale).

2. **`ColumnTransformer` (Parallel Partitioning Topology)**:
   - Routes disjoint subsets of input columns to dedicated, independent transformation pipelines:
     $$\text{ColumnTransformer} = \begin{cases} X_{\text{numeric}} \to \text{Pipeline}_{\text{num}} \\ X_{\text{categorical}} \to \text{Pipeline}_{\text{cat}} \\ X_{\text{text}} \to \text{Pipeline}_{\text{text}} \end{cases} \implies \text{hstack}(X_{\text{num}}', X_{\text{cat}}', X_{\text{text}}')$$
   - Horizontally concatenates transformed outputs into a unified feature matrix.
   - **When to Use**: Mandatory for heterogeneous production datasets containing mixed types (floats, integers, categorical strings, timestamps) without manual DataFrame slicing.

3. **`FeatureUnion` (Parallel Stacking on Same Features)**:
   - Takes the *exact same* input feature subset, passes it simultaneously through multiple independent transformers, and concatenates the resulting representations side-by-side.
   - **When to Use**: Multi-modal feature extraction on a single column (e.g., passing a `product_description` text column through both a Word-level TF-IDF transformer and a Character-level N-gram transformer simultaneously).

---

### Question 2: Explain the phenomenon of Data Leakage in machine learning preprocessing pipelines. How does nested cross-validation with atomic pipelines mathematically prevent leakage?

#### Candidate Answer
Data leakage occurs when information from the target variable or evaluation/test dataset is inadvertently introduced into the training process, producing overly optimistic offline evaluation metrics that collapse under live production deployment.

#### The Mathematical Leakage Problem
Consider standard Z-score feature scaling:
$$x' = \frac{x - \mu}{\sigma}$$
If an engineer fits a scaler across the entire dataset $D = D_{\text{train}} \cup D_{\text{val}}$ before splitting:
$$\mu = \frac{1}{N_{\text{total}}} \sum_{i=1}^{N_{\text{total}}} x_i$$
The calculated mean $\mu$ and variance $\sigma^2$ incorporate values from $D_{\text{val}}$. When the estimator is trained on $D_{\text{train}}$, it has implicitly "seen" the center and spread of the validation distribution. In high-dimensional spaces or feature selection routines, fitting selectors globally can falsely yield $AUC > 0.95$ on pure random noise!

#### The Pipeline Solution
When a preprocessing workflow is encapsulated inside a Scikit-Learn `Pipeline` and passed to `cross_val_score` or `GridSearchCV`:
1. The cross-validation engine first partitions $D$ into $K$ folds.
2. For fold $k$, the pipeline's `fit()` method is invoked *strictly* on $D_{\text{train}, k}$.
3. Transformers compute $\mu_k$ and $\sigma_k$ solely from training fold $k$.
4. The learned parameters are applied via `transform()` to the unseen validation fold $D_{\text{val}, k}$.
This guarantees zero data leakage by construction.

---

### Question 3: Walk me through the implementation of a custom Scikit-Learn transformer. What is the contract for `__init__`, `fit()`, `transform()`, and `get_feature_names_out()`? Why does Scikit-Learn forbid `*args` and `**kwargs`?

#### Candidate Answer
Custom transformers integrate natively with Scikit-Learn's ecosystem (pipelines, cloning, grid searches, feature tracking) by strictly adhering to the Estimator Contract:

1. **Inheritance**:
   - Inherit from `sklearn.base.BaseEstimator` (provides automated `get_params()` and `set_params()` for cloning) and `sklearn.base.TransformerMixin` (provides `fit_transform()`).

2. **Constructor (`__init__`)**:
   - Must only accept explicit keyword arguments with default values.
   - **No `*args` or `**kwargs`**: Scikit-Learn's cloning mechanism (`sklearn.base.clone`) inspects constructor signature parameters using Python reflection (`inspect.signature`). If variable arguments exist, `clone()` cannot determine how to re-instantiate identical replicas during `GridSearchCV`.
   - Never mutate parameters or calculate state in `__init__`.

3. **`fit(self, X, y=None)`**:
   - Computes state from training data (e.g. quantiles, medians, class means).
   - Any learned attributes must be stored with a **trailing underscore** (e.g. `self.bounds_`, `self.mean_`).
   - Must return `self`.

4. **`transform(self, X)`**:
   - Applies transformation using learned attributes.
   - Must return a 2D NumPy array or DataFrame without modifying the input $X$ in place.

5. **`get_feature_names_out(self, input_features=None)`**:
   - Generates string names of output columns. Crucial for downstream explainability tools (SHAP, feature importances).

---

### Question 4: How does probability calibration work in classification pipelines? Compare Platt Scaling vs Isotonic Regression. Why is uncalibrated probability risky in production business thresholding?

#### Candidate Answer
Many high-performing classifiers (Random Forests, Gradient Boosted Trees, Support Vector Machines) are optimized for ranking (AUC-ROC) or decision boundary margin rather than probability log-loss.
- **The Risk**: A model might output a score of $0.80$ for an event that only occurs $40\%$ of the time. If an underwriting API automatically approves loans with predicted probability $>0.75$, the business incurs severe financial losses due to miscalibrated risk thresholds.

#### Calibration Methods in `CalibratedClassifierCV`
1. **Platt Scaling (`method='sigmoid'`)**:
   - Fits a parametric univariate logistic regression model on top of the classifier's outputs:
     $$P(Y = 1 \mid f(X)) = \frac{1}{1 + \exp(A \cdot f(X) + B)}$$
   - Learns scalar weights $A$ and $B$.
   - **Pros/Cons**: Resilient against overfitting on smaller calibration datasets ($N < 1000$); assumes sigmoidal calibration curve distortion.
2. **Isotonic Regression (`method='isotonic'`)**:
   - Fits a non-parametric, piece-wise constant monotonic step function:
     $$\min \sum (y_i - m(f(x_i)))^2 \quad \text{subject to } m(a) \ge m(b) \text{ whenever } a \ge b$$
   - **Pros/Cons**: Most expressive, corrects arbitrary monotonic distortions; highly prone to severe overfitting on small datasets.

#### Production Rule
Always calibrate using cross-validation (`cv=3` or `cv=5`) to prevent overfitting on uncalibrated training margins.

---

### Question 5: How do you architect an online ML inference service in Python (e.g. FastAPI / gRPC) to serve a Scikit-Learn pipeline with sub-10ms P99 latency while guaranteeing thread safety and handling schema evolution?

#### Candidate Answer
In an enterprise online inference microservice:

1. **Serialization & Lifecycle**:
   - Serialize the entire fitted `Pipeline` using `joblib` or ONNX.
   - In FastAPI, load the pipeline into memory once during the application startup lifespan event (`@asynccontextmanager`) and store it in application state (`app.state.pipeline`), eliminating per-request deserialization overhead.

2. **Thread Safety & GIL Avoidance**:
   - Scikit-Learn `Pipeline.predict()` and `predict_proba()` operations are read-only and thread-safe across concurrent requests.
   - However, constructing a single-row Pandas DataFrame per request incurs significant Python overhead (~1–3ms).
   - **Optimization**: Convert incoming Pydantic JSON payloads directly into contiguous NumPy arrays or pass pre-allocated dictionary buffers to bypass DataFrame instantiation latency.

3. **Schema Evolution & Defense**:
   - Enforce `ColumnTransformer(remainder='drop')` so that newly added upstream payload attributes do not cause shape mismatches or memory corruption.
   - Configure `OneHotEncoder(handle_unknown='ignore')` to ensure unseen categorical inputs produce valid zero-vectors without throwing 500 errors.
   - Perform defensive input validation using Pydantic models before passing payloads into the pipeline.
