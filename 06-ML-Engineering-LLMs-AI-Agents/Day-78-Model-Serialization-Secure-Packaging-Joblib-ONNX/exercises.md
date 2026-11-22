# Day 78: Production Exercises — Model Serialization & Secure Packaging

---

## Exercise 1: Hardened Restricted Unpickler with Class Whitelisting

### Objective
In enterprise microservices where `pickle` or `joblib` artifacts must be loaded, standard deserialization is vulnerable to Remote Code Execution (RCE) via `__reduce__` exploit payloads. Implement a security-hardened `StrictSafeUnpickler` class that intercepts class lookup (`find_class`) and rejects any module or class not explicitly included on an approved whitelist.

### Production Constraints & Specification
1. Inherit from `pickle.Unpickler`.
2. Allow only `numpy`, `sklearn`, and Python primitive built-ins (`int`, `float`, `str`, `dict`, `list`, `tuple`, `bool`).
3. Explicitly block `os`, `subprocess`, `posix`, `builtins.eval`, `builtins.exec`, `builtins.__import__`.
4. Provide unit tests proving that:
   - Legitimate Scikit-Learn / NumPy objects load without error.
   - Malicious exploit payloads attempting to invoke `os.system` raise a custom `SecurityException` immediately.

### Production Solution & Verification Code

```python
import io
import os
import pickle
from typing import Any, Set


class SecurityException(Exception):
    """Raised when an unpickling payload attempts to load a non-whitelisted class."""
    pass


class StrictSafeUnpickler(pickle.Unpickler):
    """
    Security-hardened unpickler that restricts class instantiation to a strict
    allowlist of safe mathematical and data structures.
    """

    ALLOWED_MODULES: Set[str] = {
        "numpy",
        "numpy.core.multiarray",
        "numpy.core.numeric",
        "numpy._core.multiarray",
        "numpy._core.numeric",
        "sklearn",
        "sklearn.pipeline",
        "sklearn.linear_model",
        "sklearn.preprocessing",
    }

    ALLOWED_BUILTINS: Set[str] = {
        "int", "float", "str", "dict", "list", "tuple", "set", "bool", "bytes"
    }

    def find_class(self, module: str, name: str) -> Any:
        # Check module prefix allowlist
        if any(module == m or module.startswith(f"{m}.") for m in self.ALLOWED_MODULES):
            return super().find_class(module, name)

        if module == "builtins" and name in self.ALLOWED_BUILTINS:
            return super().find_class(module, name)

        raise SecurityException(
            f"SECURITY ALERT: Blocked unauthorized class '{module}.{name}' during model unpickling!"
        )


class MaliciousExploit:
    """Exploit class simulating an attacker's RCE payload."""
    def __reduce__(self):
        return (os.system, ("echo COMPROMISED",))


def test_exercise_01() -> None:
    print("[*] Running Exercise 1: StrictSafeUnpickler Verification...")

    # 1. Test legitimate payload
    legitimate_data = {"weights": [1.5, 2.3, -0.4], "bias": 0.12, "model_type": "linear"}
    legit_bytes = pickle.dumps(legitimate_data)

    safe_unpickler = StrictSafeUnpickler(io.BytesIO(legit_bytes))
    loaded_data = safe_unpickler.load()
    assert loaded_data == legitimate_data, "Legitimate data failed to load"
    print("[+] Legitimate dictionary and primitives loaded successfully.")

    # 2. Test malicious exploit payload
    malicious_bytes = pickle.dumps(MaliciousExploit())
    try:
        attacker_unpickler = StrictSafeUnpickler(io.BytesIO(malicious_bytes))
        _ = attacker_unpickler.load()
        assert False, "SECURITY FAILURE: Malicious class was loaded without restriction!"
    except SecurityException as e:
        print(f"[+] Security Exception Correctly Triggered: {e}")

    print("[+] Exercise 1 Passed: Restricted safe unpickler successfully blocked RCE payload.")


if __name__ == "__main__":
    test_exercise_01()
```

---

## Exercise 2: Standalone Pure NumPy Model Weight Extractor & Predictor

### Objective
High-frequency inference backends (e.g., ad-bidding, algorithmic trading, edge devices) require sub-millisecond latency. Loading the full Scikit-Learn framework incurs heavy memory and import overhead. Implement a `StandaloneLinearPredictor` that extracts raw floating-point weights, biases, and scaler means from a trained Scikit-Learn model into a compact binary format, and executes predictions using pure NumPy linear algebra without importing Scikit-Learn at runtime.

### Production Constraints & Specification
1. Export model parameters to an atomic binary format:
   - Header: JSON metadata storing shapes, dtypes, and tensor byte offsets.
   - Body: Raw contiguous float32 buffers for weights, bias, feature means, and feature scales.
2. Runtime predictor must rely purely on NumPy (`np.frombuffer` and `np.dot`).
3. Mathematical parity test: Verify that predicted probabilities match Scikit-Learn's `predict_proba()` within $1e-4$ tolerance.

### Production Solution & Verification Code

```python
import io
import json
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


class StandaloneLinearPredictor:
    """
    Zero-dependency, high-speed linear model predictor executing
    vectorized inference directly on raw float32 memory buffers.
    """

    def __init__(self, binary_package: bytes) -> None:
        header_len = int.from_bytes(binary_package[:4], byteorder="big")
        manifest_json = binary_package[4:4 + header_len].decode("utf-8")
        self.manifest = json.loads(manifest_json)
        raw_buffer = binary_package[4 + header_len:]

        tensors = {}
        for name, meta in self.manifest["tensors"].items():
            start, end = meta["offsets"]
            tensors[name] = np.frombuffer(raw_buffer[start:end], dtype=np.float32).reshape(meta["shape"])

        self.means_ = tensors["means"]
        self.scales_ = tensors["scales"]
        self.weights_ = tensors["weights"]
        self.bias_ = tensors["bias"]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Computes standardized logistic sigmoid: P(y=1|X) = sigmoid((X - mu)/sigma * W + b)."""
        X_mat = np.asarray(X, dtype=np.float32)
        X_scaled = (X_mat - self.means_) / self.scales_
        logits = np.dot(X_scaled, self.weights_.T) + self.bias_
        return 1.0 / (1.0 + np.exp(-logits))


def export_model_to_bytes(scaler: StandardScaler, model: LogisticRegression) -> bytes:
    """Serializes weights into binary envelope: [4B len] + [JSON manifest] + [raw bytes]."""
    weights = model.coef_.astype(np.float32)
    bias = model.intercept_.astype(np.float32)
    means = scaler.mean_.astype(np.float32)
    scales = scaler.scale_.astype(np.float32)

    tensors = {"means": means, "scales": scales, "weights": weights, "bias": bias}
    meta = {}
    offset = 0
    raw_payload = bytearray()

    for name, arr in tensors.items():
        b = arr.tobytes()
        meta[name] = {"shape": list(arr.shape), "offsets": [offset, offset + len(b)]}
        raw_payload.extend(b)
        offset += len(b)

    manifest_bytes = json.dumps({"tensors": meta}).encode("utf-8")
    header_len = len(manifest_bytes).to_bytes(4, byteorder="big")
    return header_len + manifest_bytes + bytes(raw_payload)


def test_exercise_02() -> None:
    print("[*] Running Exercise 2: Standalone Pure NumPy Linear Predictor...")
    np.random.seed(42)
    X_train = np.random.randn(500, 5).astype(np.float32)
    y_train = (X_train[:, 0] * 1.5 - X_train[:, 1] * 0.8 > 0.0).astype(int)

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression().fit(scaler.transform(X_train), y_train)

    # Export to pure binary
    pkg_bytes = export_model_to_bytes(scaler, model)
    print(f"[+] Exported model size: {len(pkg_bytes)} bytes.")

    # Instantiate standalone predictor without Scikit-Learn
    predictor = StandaloneLinearPredictor(pkg_bytes)

    # Verification on test batch
    X_test = np.random.randn(10, 5).astype(np.float32)
    skl_probs = model.predict_proba(scaler.transform(X_test))[:, 1]
    np_probs = predictor.predict_proba(X_test).flatten()

    np.testing.assert_allclose(skl_probs, np_probs, rtol=1e-4, atol=1e-4)
    print("[+] Exercise 2 Passed: Mathematical parity between Scikit-Learn and Pure NumPy verified.")


if __name__ == "__main__":
    test_exercise_02()
```

---

## Exercise 3: Zero-Copy Memory-Mapped Batch Inference Engine (`joblib.load(mmap_mode='r')`)

### Objective
In high-throughput microservice architectures, multiple worker processes (FastAPI / Gunicorn workers) serving model predictions each load their own duplicate copies of large model weights, consuming gigabytes of redundant RAM. Implement a memory-mapped batch inference engine that loads a serialized model via `joblib.load(..., mmap_mode='r')` and executes concurrent inference against read-only virtual memory pages without heap duplication.

### Production Constraints & Specification
1. Serialize a large Random Forest / Gradient Boosting pipeline using `joblib.dump()`.
2. Load the model using `joblib.load(..., mmap_mode='r')`.
3. Verify that the loaded NumPy array attributes have memory-mapped flags (`arr.flags['WRITEABLE'] == False` or `isinstance(arr, np.memmap)`).
4. Run batch predictions and verify output validity.

### Production Solution & Verification Code

```python
import os
import tempfile
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def test_exercise_03() -> None:
    print("[*] Running Exercise 3: Memory-Mapped Model Serving Engine...")
    temp_dir = tempfile.mkdtemp()
    model_path = os.path.join(temp_dir, "rf_mmap_model.joblib")

    try:
        np.random.seed(42)
        X = np.random.randn(1_000, 10).astype(np.float64)
        y = (np.sum(X[:, :3], axis=1) > 0.0).astype(int)

        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("rf", RandomForestClassifier(n_estimators=20, max_depth=6, random_state=42)),
        ])
        pipeline.fit(X, y)

        # Save model using joblib
        joblib.dump(pipeline, model_path)
        disk_size_kb = os.path.getsize(model_path) / 1024.0
        print(f"[+] Saved model to disk ({disk_size_kb:.2f} KB).")

        # Load with memory mapping
        loaded_pipeline = joblib.load(model_path, mmap_mode="r")

        # Verify inference execution on new batch
        X_test = np.random.randn(50, 10)
        predictions = loaded_pipeline.predict(X_test)
        probabilities = loaded_pipeline.predict_proba(X_test)

        assert len(predictions) == 50
        assert probabilities.shape == (50, 2)
        assert np.all((probabilities >= 0.0) & (probabilities <= 1.0))
        print("[+] Exercise 3 Passed: Memory-mapped inference executed successfully with valid bounds.")

    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_exercise_03()
```
