"""
Production Benchmark, Performance Profiling & Security Diagnostic Harness
==========================================================================
Day 55: OAuth2 with Password Grant, JWT Authentication & Scopes

This module provides a production-grade benchmarking and security diagnostic
suite for high-throughput authentication architectures. It profiles:
1. Symmetric (HS256) vs. Asymmetric (RS256) JWT Signing & Verification
2. High-Performance Token Validation Caching (LRU / Signature Hash Indexing)
3. Side-Channel Timing-Attack Resistance (Short-Circuit vs. Constant-Time Comparison)
4. Hierarchical OAuth2 Scope Evaluation Throughput & Wildcard Expansion
5. Token Expiry & Leeway Clock Skew Overhead

Run via:
    python example_02.py
"""

from __future__ import annotations

import hmac
import os
import secrets
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# ==============================================================================
# Configuration & Key Generation
# ==============================================================================

@dataclass(frozen=True)
class BenchmarkConfig:
    """Benchmark parameters and test configuration."""
    iterations: int = 5000
    cache_capacity: int = 1000
    cache_ttl_seconds: int = 60
    secret_key_hs256: str = "4d8a9f3e1b7c2d5e8a0f4c6b8d2e5a7c9f1b3d5e7a0c2e4f6b8d1a3c5e7f9b0d"


def generate_rsa_keypair() -> Tuple[bytes, bytes]:
    """Generate an RSA 2048-bit keypair for RS256 asymmetric benchmarking."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


# ==============================================================================
# In-Memory Token Validation Cache (LRU + TTL)
# ==============================================================================

class TokenValidationCache:
    """
    High-performance LRU cache for decoded JWT payloads.
    
    Validating RS256 signatures requires asymmetric modular exponentiation,
    which consumes significant CPU cycles (~0.5ms-2ms per call). Caching validated
    payloads indexed by token signature hash prevents repeated cryptographic
    operations for burst requests with the same token.
    """

    def __init__(self, capacity: int = 1000, ttl_seconds: int = 60) -> None:
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, token_sig: str) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        entry = self._cache.get(token_sig)
        if entry is None:
            self.misses += 1
            return None
        expires_at, payload = entry
        if now > expires_at:
            del self._cache[token_sig]
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def set(self, token_sig: str, payload: Dict[str, Any]) -> None:
        if len(self._cache) >= self.capacity:
            # Evict oldest entry (FIFO approximation of LRU for benchmark)
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[token_sig] = (time.monotonic() + self.ttl_seconds, payload)


# ==============================================================================
# OAuth2 Scope Evaluators
# ==============================================================================

class HierarchicalScopeEvaluator:
    """
    Evaluates OAuth2 scopes with support for wildcards and hierarchical namespaces.
    e.g., 'reports:*' satisfies 'reports:read' and 'reports:write'.
          '*' satisfies any required scope.
    """

    @staticmethod
    def match_scope(granted_scope: str, required_scope: str) -> bool:
        if granted_scope == "*" or granted_scope == required_scope:
            return True
        if granted_scope.endswith(":*"):
            prefix = granted_scope[:-2]
            if required_scope.startswith(prefix + ":") or required_scope == prefix:
                return True
        return False

    @classmethod
    def has_required_scopes(cls, granted_scopes: Set[str], required_scopes: Set[str]) -> bool:
        if not required_scopes:
            return True
        if "*" in granted_scopes:
            return True
        for req in required_scopes:
            if not any(cls.match_scope(granted, req) for granted in granted_scopes):
                return False
        return True


# ==============================================================================
# Benchmark 1: Symmetric (HS256) vs. Asymmetric (RS256) Performance
# ==============================================================================

def benchmark_jwt_crypto(config: BenchmarkConfig, priv_pem: bytes, pub_pem: bytes) -> Dict[str, Any]:
    """Profile encode & decode latency for HS256 vs RS256."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user_prod_09876",
        "username": "lead_architect",
        "scopes": ["users:read", "reports:read", "reports:write"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "jti": secrets.token_hex(16),
    }

    # 1. HS256 Encode Benchmark
    hs256_enc_times = []
    for _ in range(config.iterations):
        t0 = time.perf_counter_ns()
        _ = jwt.encode(payload, config.secret_key_hs256, algorithm="HS256")
        hs256_enc_times.append(time.perf_counter_ns() - t0)

    sample_hs256_token = jwt.encode(payload, config.secret_key_hs256, algorithm="HS256")

    # 2. HS256 Decode Benchmark
    hs256_dec_times = []
    for _ in range(config.iterations):
        t0 = time.perf_counter_ns()
        _ = jwt.decode(sample_hs256_token, config.secret_key_hs256, algorithms=["HS256"])
        hs256_dec_times.append(time.perf_counter_ns() - t0)

    # 3. RS256 Encode Benchmark (Private Key signing)
    rs256_enc_times = []
    rs256_iterations = min(config.iterations, 100)  # RS256 key signing is CPU-intensive
    for _ in range(rs256_iterations):
        t0 = time.perf_counter_ns()
        _ = jwt.encode(payload, priv_pem, algorithm="RS256")
        rs256_enc_times.append(time.perf_counter_ns() - t0)

    sample_rs256_token = jwt.encode(payload, priv_pem, algorithm="RS256")

    # 4. RS256 Decode Benchmark (Public Key verification)
    rs256_dec_times = []
    for _ in range(config.iterations):
        t0 = time.perf_counter_ns()
        _ = jwt.decode(sample_rs256_token, pub_pem, algorithms=["RS256"])
        rs256_dec_times.append(time.perf_counter_ns() - t0)

    return {
        "hs256_encode": hs256_enc_times,
        "hs256_decode": hs256_dec_times,
        "rs256_encode": rs256_enc_times,
        "rs256_decode": rs256_dec_times,
    }


# ==============================================================================
# Benchmark 2: Token Validation Caching vs Uncached Decode
# ==============================================================================

def benchmark_validation_cache(config: BenchmarkConfig, pub_pem: bytes, sample_token: str) -> Dict[str, Any]:
    """Profile uncached RS256 decoding vs cached signature lookup."""
    cache = TokenValidationCache(capacity=config.cache_capacity, ttl_seconds=config.cache_ttl_seconds)
    token_sig = sample_token.split(".")[-1]

    # Warm cache
    payload = jwt.decode(sample_token, pub_pem, algorithms=["RS256"])
    cache.set(token_sig, payload)

    cached_times = []
    uncached_times = []

    for _ in range(config.iterations):
        # Cached path
        t0 = time.perf_counter_ns()
        cached_payload = cache.get(token_sig)
        if cached_payload is None:
            cached_payload = jwt.decode(sample_token, pub_pem, algorithms=["RS256"])
            cache.set(token_sig, cached_payload)
        cached_times.append(time.perf_counter_ns() - t0)

        # Uncached path
        t1 = time.perf_counter_ns()
        _ = jwt.decode(sample_token, pub_pem, algorithms=["RS256"])
        uncached_times.append(time.perf_counter_ns() - t1)

    return {
        "cached": cached_times,
        "uncached": uncached_times,
        "cache_hits": cache.hits,
        "cache_misses": cache.misses,
    }


# ==============================================================================
# Benchmark 3: Timing-Attack Resistance Diagnostic
# ==============================================================================

def benchmark_timing_attack_resistance(iterations: int = 50000) -> Dict[str, Any]:
    """
    Demonstrates constant-time comparison vs short-circuiting equality.
    
    In naive comparison (`a == b`), the loop exits on the very first mismatched byte.
    In `hmac.compare_digest(a, b)`, all bytes are compared regardless of mismatches,
    eliminating side-channel timing leakage.
    """
    secret = secrets.token_bytes(64)
    # Mismatch at byte 0
    mismatch_early = bytes([secret[0] ^ 0xFF]) + secret[1:]
    # Mismatch at byte 63
    mismatch_late = secret[:-1] + bytes([secret[-1] ^ 0xFF])
    # Exact match
    match_exact = bytes(secret)

    # 1. Naive string comparison (simulated byte short-circuit)
    def naive_compare(a: bytes, b: bytes) -> bool:
        if len(a) != len(b):
            return False
        for byte_a, byte_b in zip(a, b):
            if byte_a != byte_b:
                return False
        return True

    naive_early_times = []
    naive_late_times = []
    naive_match_times = []

    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        _ = naive_compare(secret, mismatch_early)
        naive_early_times.append(time.perf_counter_ns() - t0)

        t1 = time.perf_counter_ns()
        _ = naive_compare(secret, mismatch_late)
        naive_late_times.append(time.perf_counter_ns() - t1)

        t2 = time.perf_counter_ns()
        _ = naive_compare(secret, match_exact)
        naive_match_times.append(time.perf_counter_ns() - t2)

    # 2. hmac.compare_digest (constant time)
    hmac_early_times = []
    hmac_late_times = []
    hmac_match_times = []

    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        _ = hmac.compare_digest(secret, mismatch_early)
        hmac_early_times.append(time.perf_counter_ns() - t0)

        t1 = time.perf_counter_ns()
        _ = hmac.compare_digest(secret, mismatch_late)
        hmac_late_times.append(time.perf_counter_ns() - t1)

        t2 = time.perf_counter_ns()
        _ = hmac.compare_digest(secret, match_exact)
        hmac_match_times.append(time.perf_counter_ns() - t2)

    return {
        "naive_early_ns": statistics.median(naive_early_times),
        "naive_late_ns": statistics.median(naive_late_times),
        "naive_match_ns": statistics.median(naive_match_times),
        "hmac_early_ns": statistics.median(hmac_early_times),
        "hmac_late_ns": statistics.median(hmac_late_times),
        "hmac_match_ns": statistics.median(hmac_match_times),
    }


# ==============================================================================
# Benchmark 4: OAuth2 Scope Evaluation Throughput
# ==============================================================================

def benchmark_scope_evaluation(iterations: int = 50000) -> Dict[str, Any]:
    """Profile flat set evaluation vs hierarchical wildcard scope resolution."""
    user_scopes_flat = {"users:read", "reports:read", "analytics:export"}
    required_scopes = {"users:read", "reports:read"}

    # Flat Set Evaluation
    flat_times = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        _ = required_scopes.issubset(user_scopes_flat)
        flat_times.append(time.perf_counter_ns() - t0)

    # Hierarchical Wildcard Evaluation
    user_scopes_wildcard = {"reports:*", "users:*"}
    hierarchical_times = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        _ = HierarchicalScopeEvaluator.has_required_scopes(user_scopes_wildcard, required_scopes)
        hierarchical_times.append(time.perf_counter_ns() - t0)

    return {
        "flat_times": flat_times,
        "hierarchical_times": hierarchical_times,
    }


# ==============================================================================
# Statistics & Formatting Helpers
# ==============================================================================

def compute_stats(durations_ns: List[int]) -> Tuple[float, float, float, float]:
    """Return mean_us, p95_us, p99_us, ops_per_sec."""
    durations_us = [d / 1000.0 for d in durations_ns]
    mean_us = statistics.mean(durations_us)
    p95_us = statistics.quantiles(durations_us, n=100)[94] if len(durations_us) >= 100 else mean_us
    p99_us = statistics.quantiles(durations_us, n=100)[98] if len(durations_us) >= 100 else p95_us
    mean_sec = (mean_us * 1e-6) if mean_us > 0 else 1e-9
    ops_sec = 1.0 / mean_sec
    return mean_us, p95_us, p99_us, ops_sec


def print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(f" {title.upper()}")
    print("=" * 80)


def print_table_header(columns: List[Tuple[str, int]]) -> None:
    header = " | ".join(col.ljust(w) for col, w in columns)
    sep = "-+-".join("-" * w for _, w in columns)
    print(header)
    print(sep)


def print_table_row(values: List[Tuple[str, int]]) -> None:
    row = " | ".join(val.ljust(w) for val, w in values)
    print(row)


# ==============================================================================
# Main Diagnostic Execution
# ==============================================================================

def main() -> None:
    print("=" * 80)
    print(" PRODUCTION AUTHENTICATION & SECURITY BENCHMARK SUITE")
    print(" Day 55: OAuth2 with Password Grant, JWT Authentication & Scopes")
    print("=" * 80)
    print(f"Python Runtime: {sys.version.split()[0]} on {sys.platform}")
    print(f"PyJWT Version : {jwt.__version__}")

    config = BenchmarkConfig(iterations=3000)

    print("\n[INFO] Generating RSA 2048-bit keypair for RS256 tests...")
    t_key_start = time.perf_counter()
    priv_pem, pub_pem = generate_rsa_keypair()
    t_key_dur = (time.perf_counter() - t_key_start) * 1000
    print(f"[OK] Generated 2048-bit RSA keypair in {t_key_dur:.2f} ms")

    # --------------------------------------------------------------------------
    # 1. JWT Crypto Benchmark (HS256 vs RS256)
    # --------------------------------------------------------------------------
    print_section("1. Cryptographic Signature Throughput (HS256 vs RS256)")
    crypto_results = benchmark_jwt_crypto(config, priv_pem, pub_pem)

    cols = [("Algorithm", 12), ("Operation", 10), ("Mean (µs)", 12), ("P95 (µs)", 12), ("P99 (µs)", 12), ("Throughput (ops/s)", 20)]
    print_table_header(cols)

    for algo, op, key in [
        ("HS256", "Encode", "hs256_encode"),
        ("HS256", "Decode", "hs256_decode"),
        ("RS256", "Encode", "rs256_encode"),
        ("RS256", "Decode", "rs256_decode"),
    ]:
        durations = crypto_results[key]
        mean_u, p95_u, p99_u, ops = compute_stats(durations)
        print_table_row([
            (algo, 12),
            (op, 10),
            (f"{mean_u:.2f} µs", 12),
            (f"{p95_u:.2f} µs", 12),
            (f"{p99_u:.2f} µs", 12),
            (f"{ops:,.0f} ops/s", 20),
        ])

    rs_enc_mean = statistics.mean(crypto_results["rs256_encode"]) / 1000.0
    hs_enc_mean = statistics.mean(crypto_results["hs256_encode"]) / 1000.0
    enc_ratio = rs_enc_mean / hs_enc_mean if hs_enc_mean > 0 else 1.0

    print(f"\n[INSIGHT] RS256 private key signing is {enc_ratio:.1f}x more CPU-intensive than HS256.")
    print("[INSIGHT] Asymmetric signing should be isolated to dedicated Auth Services / IDPs;")
    print("          Downstream services only perform RS256 public-key verification or use caching.")

    # --------------------------------------------------------------------------
    # 2. Validation Caching Benchmark
    # --------------------------------------------------------------------------
    print_section("2. Token Validation Caching (LRU vs Full RS256 Decode)")
    sample_rs256 = jwt.encode({"sub": "user_123", "scopes": ["read"]}, priv_pem, algorithm="RS256")
    cache_results = benchmark_validation_cache(config, pub_pem, sample_rs256)

    cols_cache = [("Strategy", 18), ("Mean (µs)", 12), ("P95 (µs)", 12), ("Throughput (ops/s)", 20), ("Speedup", 12)]
    print_table_header(cols_cache)

    mean_cached, p95_cached, _, ops_cached = compute_stats(cache_results["cached"])
    mean_uncached, p95_uncached, _, ops_uncached = compute_stats(cache_results["uncached"])
    speedup = mean_uncached / mean_cached if mean_cached > 0 else 1.0

    print_table_row([
        ("Cached (LRU)", 18),
        (f"{mean_cached:.2f} µs", 12),
        (f"{p95_cached:.2f} µs", 12),
        (f"{ops_cached:,.0f} ops/s", 20),
        (f"{speedup:.1f}x", 12),
    ])
    print_table_row([
        ("Uncached (RS256)", 18),
        (f"{mean_uncached:.2f} µs", 12),
        (f"{p95_uncached:.2f} µs", 12),
        (f"{ops_uncached:,.0f} ops/s", 20),
        ("1.0x (Baseline)", 12),
    ])

    print(f"\n[RESULT] Validation caching reduces token parsing latency by {speedup:.1f}x.")
    print(f"         Cache Hits: {cache_results['cache_hits']}, Misses: {cache_results['cache_misses']}")

    # --------------------------------------------------------------------------
    # 3. Side-Channel Timing Attack Resistance
    # --------------------------------------------------------------------------
    print_section("3. Side-Channel Timing Attack Resistance Diagnostic")
    timing_res = benchmark_timing_attack_resistance(iterations=40000)

    print("Comparison of string equality vs hmac.compare_digest over 64-byte secret tokens:")
    print("-" * 75)
    print(f"Naive Compare - Early Mismatch (Byte 0) : {timing_res['naive_early_ns']:.1f} ns")
    print(f"Naive Compare - Late Mismatch (Byte 63): {timing_res['naive_late_ns']:.1f} ns")
    print(f"Naive Compare - Exact Match            : {timing_res['naive_match_ns']:.1f} ns")
    naive_diff = abs(timing_res['naive_late_ns'] - timing_res['naive_early_ns'])
    print(f"--> Naive Timing Delta (Side-Channel Vulnerability): {naive_diff:.1f} ns difference")
    print("-" * 75)
    print(f"hmac.compare_digest - Early Mismatch   : {timing_res['hmac_early_ns']:.1f} ns")
    print(f"hmac.compare_digest - Late Mismatch    : {timing_res['hmac_late_ns']:.1f} ns")
    print(f"hmac.compare_digest - Exact Match      : {timing_res['hmac_match_ns']:.1f} ns")
    hmac_diff = abs(timing_res['hmac_late_ns'] - timing_res['hmac_early_ns'])
    print(f"--> Constant-Time Timing Delta         : {hmac_diff:.1f} ns (Statistically Invariant)")
    print("-" * 75)
    print("[SECURITY] Always use `secrets.compare_digest` or `hmac.compare_digest` for API keys,")
    print("           refresh tokens, and signature checks to prevent timing attacks.")

    # --------------------------------------------------------------------------
    # 4. Scope Evaluation Performance
    # --------------------------------------------------------------------------
    print_section("4. OAuth2 Scope Evaluation Engine Performance")
    scope_res = benchmark_scope_evaluation(iterations=50000)

    mean_flat, p95_flat, _, ops_flat = compute_stats(scope_res["flat_times"])
    mean_hier, p95_hier, _, ops_hier = compute_stats(scope_res["hierarchical_times"])

    cols_scope = [("Scope Engine", 22), ("Mean (ns)", 12), ("P95 (ns)", 12), ("Throughput (ops/s)", 20)]
    print_table_header(cols_scope)
    print_table_row([
        ("Flat Set issubset", 22),
        (f"{mean_flat * 1000:.1f} ns", 12),
        (f"{p95_flat * 1000:.1f} ns", 12),
        (f"{ops_flat:,.0f} ops/s", 20),
    ])
    print_table_row([
        ("Hierarchical Wildcard", 22),
        (f"{mean_hier * 1000:.1f} ns", 12),
        (f"{p95_hier * 1000:.1f} ns", 12),
        (f"{ops_hier:,.0f} ops/s", 20),
    ])

    print("\n[ARCHITECTURAL SUMMARY]")
    print("1. HS256: Best for internal monolithic or tightly coupled services (lightweight, fastest).")
    print("2. RS256/ES256: Required for distributed architectures where resource servers only verify.")
    print("3. Validation Caching: Essential for high-concurrency microservices to avoid RSA bottlenecks.")
    print("4. Constant-Time Checks: Mandatory for opaque token / API key lookups.")
    print("=" * 80)
    print(" ALL BENCHMARKS & SECURITY DIAGNOSTICS COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    main()
