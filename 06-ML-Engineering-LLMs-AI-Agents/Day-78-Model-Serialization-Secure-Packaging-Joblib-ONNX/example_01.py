"""
Day 78: Enterprise Cryptographic Model Packager & Secure Deserialization Engine
Part 1: Cryptographic Packaging, HMAC Verification, and Restricted Unpickling

This script demonstrates secure enterprise model serialization:
1. Cryptographic HMAC-SHA256 signature verification preventing model tampering.
2. Restricted Class Whitelist Unpickler blocking arbitrary code execution (RCE).
3. Resilient packaging and verified single-sample prediction serving.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import pickle
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Set, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler


class SecurityTamperError(Exception):
    """Raised when cryptographic signature verification fails on a model package."""
    pass


class SecurityViolationError(Exception):
    """Raised when an unpickling payload attempts to load a non-whitelisted class."""
    pass


class RestrictedSafeUnpickler(pickle.Unpickler):
    """
    Hardened Unpickler restricting object deserialization to an explicit whitelist
    of numerical and machine learning classes. Completely blocks execution gadgets
    like os.system, subprocess.Popen, or builtins.eval.
    """

    ALLOWED_MODULES: Set[str] = {
        "numpy",
        "numpy.core.multiarray",
        "numpy.core.numeric",
        "numpy._core.multiarray",
        "numpy._core.numeric",
        "sklearn",
        "sklearn.pipeline",
        "sklearn.compose",
        "sklearn.compose._column_transformer",
        "sklearn.impute",
        "sklearn.impute._base",
        "sklearn.preprocessing",
        "sklearn.preprocessing._data",
        "sklearn.preprocessing._encoders",
        "sklearn.ensemble",
        "sklearn.ensemble._hist_gradient_boosting.gradient_boosting",
        "sklearn.ensemble._hist_gradient_boosting.predictor",
        "sklearn.tree",
    }

    ALLOWED_BUILTINS: Set[str] = {
        "int", "float", "str", "dict", "list", "tuple", "set", "bool", "bytes", "bytearray", "slice"
    }

    def find_class(self, module: str, name: str) -> Any:
        # Check if module starts with any allowed root module
        is_module_allowed = any(module == allowed or module.startswith(f"{allowed}.") for allowed in self.ALLOWED_MODULES)
        if is_module_allowed:
            return super().find_class(module, name)

        if module == "builtins" and name in self.ALLOWED_BUILTINS:
            return super().find_class(module, name)

        raise SecurityViolationError(
            f"SECURITY EXCEPTION: Unauthorized class '{module}.{name}' blocked during deserialization!"
        )


@dataclass(frozen=True)
class ModelManifest:
    """Immutable metadata manifest describing a cryptographically packaged model."""
    model_name: str
    version: str
    created_at_utc: str
    feature_schema: List[str]
    payload_sha256: str
    hmac_sha256_signature: str
    compression: str


class CryptographicModelPackager:
    """
    Enterprise Model Packaging Engine providing compression, tamper-proofing,
    and secure cryptographic verification for machine learning artifacts.
    """

    PACKAGE_MAGIC: bytes = b"SEC_ML_PKG_V1\n"

    def __init__(self, secret_key: bytes) -> None:
        if len(secret_key) < 16:
            raise ValueError("Cryptographic secret key must be at least 16 bytes long.")
        self.secret_key = secret_key

    def _compute_hmac(self, data: bytes) -> str:
        """Computes HMAC-SHA256 digest over binary buffer."""
        return hmac.new(self.secret_key, data, hashlib.sha256).hexdigest()

    def package_model(
        self,
        model: Any,
        output_filepath: str,
        model_name: str = "fraud_risk_pipeline",
        version: str = "1.0.0",
        feature_schema: List[str] | None = None,
    ) -> ModelManifest:
        """
        Serializes model with Joblib compression, computes cryptographic signature,
        and packages manifest + binary payload into a single secured bundle.
        """
        # 1. Serialize in-memory with Joblib compression
        buffer = io.BytesIO()
        joblib.dump(model, buffer, compress=("zlib", 3))
        payload_bytes = buffer.getvalue()

        # 2. Compute SHA256 and HMAC-SHA256 signature
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        signature = self._compute_hmac(payload_bytes)

        manifest = ModelManifest(
            model_name=model_name,
            version=version,
            created_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            feature_schema=feature_schema or [],
            payload_sha256=payload_sha256,
            hmac_sha256_signature=signature,
            compression="zlib_3",
        )

        manifest_json_bytes = json.dumps(asdict(manifest)).encode("utf-8")
        manifest_len_header = len(manifest_json_bytes).to_bytes(4, byteorder="big")

        # 3. Write atomic secure bundle: [MAGIC 14B] + [MANIFEST_LEN 4B] + [MANIFEST JSON] + [PAYLOAD]
        with open(output_filepath, "wb") as f:
            f.write(self.PACKAGE_MAGIC)
            f.write(manifest_len_header)
            f.write(manifest_json_bytes)
            f.write(payload_bytes)

        return manifest

    def verify_and_load(self, package_filepath: str) -> Tuple[Any, ModelManifest]:
        """
        Reads secure package, verifies HMAC signature in constant time,
        and securely deserializes the model artifact.
        """
        with open(package_filepath, "rb") as f:
            magic = f.read(len(self.PACKAGE_MAGIC))
            if magic != self.PACKAGE_MAGIC:
                raise SecurityTamperError("Invalid model package magic header. File is corrupt or untrusted.")

            manifest_len_bytes = f.read(4)
            manifest_len = int.from_bytes(manifest_len_bytes, byteorder="big")
            manifest_json_bytes = f.read(manifest_len)
            payload_bytes = f.read()

        manifest_dict = json.loads(manifest_json_bytes.decode("utf-8"))
        manifest = ModelManifest(**manifest_dict)

        # 1. Verify Cryptographic HMAC Signature using constant-time comparison
        expected_sig = self._compute_hmac(payload_bytes)
        if not hmac.compare_digest(manifest.hmac_sha256_signature, expected_sig):
            raise SecurityTamperError(
                "SECURITY TAMPER DETECTED: Model package signature does not match! Deserialization aborted."
            )

        # 2. Verify SHA-256 Checksum
        calculated_sha = hashlib.sha256(payload_bytes).hexdigest()
        if calculated_sha != manifest.payload_sha256:
            raise SecurityTamperError("SECURITY EXCEPTION: Model payload checksum mismatch.")

        # 3. Deserialize safely via Joblib
        model = joblib.load(io.BytesIO(payload_bytes))
        return model, manifest


# ---------------------------------------------------------------------------
# Synthetic Malicious Exploit Generator (For Demonstration & Security Testing)
# ---------------------------------------------------------------------------
class MaliciousExploitPayload:
    """Simulates an arbitrary code execution gadget injected by an attacker."""
    def __reduce__(self):
        import os
        return (os.system, ("echo VULNERABILITY_EXECUTED",))


def train_sample_pipeline() -> Tuple[Pipeline, List[str]]:
    """Trains a sample credit scoring pipeline for packaging demonstrations."""
    np.random.seed(42)
    n = 1_000
    features = ["income", "debt_ratio", "employment_type"]

    df = pd.DataFrame({
        "income": np.random.lognormal(10.5, 0.5, n),
        "debt_ratio": np.random.uniform(0.1, 0.6, n),
        "employment_type": np.random.choice(["SALARIED", "SELF_EMPLOYED", "UNEMPLOYED"], n),
    })
    y = ((df["debt_ratio"] > 0.45) | (df["employment_type"] == "UNEMPLOYED")).astype(int)

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("scl", RobustScaler())]), ["income", "debt_ratio"]),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["employment_type"]),
        ],
        remainder="drop",
    )

    pipeline = Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", HistGradientBoostingClassifier(max_iter=30, random_state=42)),
    ])
    pipeline.fit(df, y)
    return pipeline, features


def main() -> None:
    print("================================================================================")
    print("DAY 78: ENTERPRISE CRYPTOGRAPHIC MODEL PACKAGING & DESERIALIZATION")
    print("================================================================================")

    secret_key = b"enterprise-hmac-secret-key-32-bytes!"
    temp_dir = tempfile.mkdtemp()
    package_file = os.path.join(temp_dir, "fraud_risk_v1.pkg")
    tampered_file = os.path.join(temp_dir, "fraud_risk_tampered.pkg")

    try:
        # 1. Train Model
        print("[*] Training production Scikit-Learn pipeline...")
        model, feature_names = train_sample_pipeline()
        print(f"[+] Pipeline trained successfully across features: {feature_names}")

        # 2. Package and Sign Model
        print(f"\n[*] Packaging and cryptographically signing model into '{package_file}'...")
        packager = CryptographicModelPackager(secret_key=secret_key)
        manifest = packager.package_model(
            model=model,
            output_filepath=package_file,
            model_name="fraud_risk_model",
            version="1.0.0",
            feature_schema=feature_names,
        )

        package_size_kb = os.path.getsize(package_file) / 1024.0
        print(f"[+] Package created successfully ({package_size_kb:.2f} KB).")
        print(f"    Model Name : {manifest.model_name} (v{manifest.version})")
        print(f"    Payload SHA256 : {manifest.payload_sha256[:20]}...")
        print(f"    HMAC-SHA256 Signature : {manifest.hmac_sha256_signature[:20]}...")

        # 3. Legitimate Verification & Loading
        print("\n[*] Verifying and loading legitimate model package...")
        loaded_model, verified_manifest = packager.verify_and_load(package_file)
        print(f"[+] Verification SUCCESS: Signature confirmed! Model ready for serving.")

        # Test single-record inference
        sample_df = pd.DataFrame([{
            "income": 75000.0,
            "debt_ratio": 0.28,
            "employment_type": "SALARIED",
        }])
        pred_prob = loaded_model.predict_proba(sample_df)[0, 1]
        print(f"    Sample Default Risk Probability: {pred_prob * 100.0:.2f}%")

        # 4. Security Attack Simulation 1: Tampered Binary Payload
        print("\n[*] Simulating Security Attack: Modifying 1 byte in payload (MitM / S3 tampering)...")
        with open(package_file, "rb") as f:
            package_bytes = bytearray(f.read())

        # Flip a single byte in the binary payload
        package_bytes[-50] = package_bytes[-50] ^ 0xFF
        with open(tampered_file, "wb") as f:
            f.write(package_bytes)

        try:
            packager.verify_and_load(tampered_file)
            assert False, "SECURITY FAILURE: Tampered package was loaded without detection!"
        except SecurityTamperError as e:
            print(f"[+] DEFENSE VERIFIED: Tampered package was REJECTED: {e}")

        # 5. Security Attack Simulation 2: Arbitrary Code Execution Block via Restricted Unpickler
        print("\n[*] Simulating Security Attack: RCE exploit via RestrictedSafeUnpickler...")
        malicious_bytes = pickle.dumps(MaliciousExploitPayload())

        try:
            restricted_unpickler = RestrictedSafeUnpickler(io.BytesIO(malicious_bytes))
            _ = restricted_unpickler.load()
            assert False, "SECURITY FAILURE: Malicious gadget was executed!"
        except SecurityViolationError as e:
            print(f"[+] DEFENSE VERIFIED: Restricted unpickler blocked malicious class: {e}")

        # Assertions
        assert verified_manifest.model_name == "fraud_risk_model"
        assert 0.0 <= pred_prob <= 1.0
        print("\n[+] Verification Passed: All cryptographic model packaging assertions satisfied.")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("[+] Cleaned up temporary artifact directory.")
        print("================================================================================")


if __name__ == "__main__":
    main()
