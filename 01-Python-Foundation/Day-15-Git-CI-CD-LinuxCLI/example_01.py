"""
Day 15: Example 01 - Git Internals, DAG Inspection & Automated Pre-Commit Safety Scanner

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In enterprise engineering teams, human discipline alone cannot prevent repository corruption,
leaked credentials, or malformed commit histories. Accidental pushes of OpenAI API keys,
AWS secret credentials, or unformatted code into Git history can trigger catastrophic security
breaches and multi-million dollar AWS bills.

Furthermore, understanding Git as an immutable Directed Acyclic Graph (DAG) allows senior
engineers to programmatically audit repositories, build automated release linters, and
construct custom commit-verification hooks that execute in milliseconds before code ever
touches a remote server.

This module implements:
1. Git DAG and Commit Log Inspector (using Git plumbing & porcelain via subprocess).
2. Enterprise Pre-Commit Scanner:
   - Conventional Commit Message Validator (regex-based specification).
   - High-Entropy & Hardcoded API Key / Secret Detector (OpenAI, AWS, JWT, Private Keys).
   - Branch Naming Convention Policy Enforcer.
3. Clean error reporting and self-contained automated test suite.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Tuple


# ============================================================================
# 1. DOMAIN MODELS & CONFIGURATION
# ============================================================================

class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class SecretPattern:
    """
    Represents a signature for detecting leaked enterprise credentials.
    WHY: Regex pattern matching combined with Shannon entropy calculation
    minimizes false positives while catching dangerous leaked credentials.
    """
    name: str
    pattern: Pattern[str]
    severity: Severity
    description: str


@dataclass
class SecurityFinding:
    """Represents a security violation detected in a file or diff."""
    rule_name: str
    file_path: str
    line_number: int
    severity: Severity
    redacted_match: str
    message: str


@dataclass
class CommitMetadata:
    """Represents parsed Git commit DAG node information."""
    sha: str
    parent_shas: List[str]
    author: str
    author_email: str
    timestamp: str
    message: str
    is_merge_commit: bool = field(init=False)

    def __post_init__(self) -> None:
        self.is_merge_commit = len(self.parent_shas) > 1


# ============================================================================
# 2. SHANNON ENTROPY CALCULATOR
# ============================================================================

def calculate_shannon_entropy(data: str) -> float:
    """
    Calculates the Shannon entropy of a string to detect cryptographically random keys.
    
    WHY: Passwords, API tokens, and private keys have high randomness (entropy > 4.5),
    whereas regular variable names and prose have low entropy (< 3.5).
    Formula: H(X) = - sum(P(x) * log2(P(x)))
    """
    if not data:
        return 0.0

    length = len(data)
    frequency: Dict[str, int] = {}
    for char in data:
        frequency[char] = frequency.get(char, 0) + 1

    entropy = 0.0
    for count in frequency.values():
        probability = count / length
        entropy -= probability * math.log2(probability)

    return entropy


# ============================================================================
# 3. ENTERPRISE PRE-COMMIT SECURITY SCANNER
# ============================================================================

class GitSecurityScanner:
    """
    Scans staged files and diffs for leaked secrets, keys, and credentials.
    WHY: Once a secret is committed to a Git DAG, it remains permanently stored
    in the .git/objects database even if deleted in a later commit, requiring
    dangerous and complex history-rewriting tools (e.g. git-filter-repo) to purge.
    """

    KNOWN_PATTERNS: List[SecretPattern] = [
        SecretPattern(
            name="OpenAI API Key",
            pattern=re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b"),
            severity=Severity.CRITICAL,
            description="Leaked OpenAI developer API key allowing unauthorized LLM usage.",
        ),
        SecretPattern(
            name="AWS Access Key ID",
            pattern=re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
            severity=Severity.CRITICAL,
            description="Leaked AWS IAM access key ID.",
        ),
        SecretPattern(
            name="Generic Private Key",
            pattern=re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|PGP)? PRIVATE KEY-----"),
            severity=Severity.CRITICAL,
            description="Leaked cryptographic private key block.",
        ),
        SecretPattern(
            name="JWT Token",
            pattern=re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
            severity=Severity.WARNING,
            description="Hardcoded JSON Web Token (JWT) in source code.",
        ),
        SecretPattern(
            name="High-Entropy Generic Secret Assignment",
            pattern=re.compile(r"""(?:secret|api_key|password|token|auth)\s*[:=]\s*['"]([A-Za-z0-9+/=_-]{24,})['"]""", re.IGNORECASE),
            severity=Severity.CRITICAL,
            description="Suspicious high-entropy secret assignment detected.",
        ),
    ]

    @classmethod
    def redact(cls, secret: str) -> str:
        """Masks secret to prevent re-leaking inside logs or test output."""
        if len(secret) <= 8:
            return "********"
        return f"{secret[:4]}...{secret[-4:]}"

    def scan_content(self, file_path: str, content: str) -> List[SecurityFinding]:
        """Scans string content line by line against known secret signatures."""
        findings: List[SecurityFinding] = []
        lines = content.splitlines()

        for idx, line in enumerate(lines, start=1):
            # Ignore comments in test/mock files that explicitly define examples
            if "mock-secret-ignore" in line:
                continue

            for pattern in self.KNOWN_PATTERNS:
                match = pattern.pattern.search(line)
                if match:
                    # For generic high-entropy matches, verify entropy threshold
                    matched_value = match.group(1) if match.groups() else match.group(0)
                    if "High-Entropy" in pattern.name:
                        entropy = calculate_shannon_entropy(matched_value)
                        # Only flag if entropy indicates genuine randomness (> 3.5 bits/char)
                        if entropy < 3.5:
                            continue

                    findings.append(
                        SecurityFinding(
                            rule_name=pattern.name,
                            file_path=file_path,
                            line_number=idx,
                            severity=pattern.severity,
                            redacted_match=self.redact(matched_value),
                            message=pattern.description,
                        )
                    )
        return findings


# ============================================================================
# 4. CONVENTIONAL COMMIT & BRANCH POLICY VALIDATOR
# ============================================================================

class GitPolicyValidator:
    """
    Enforces Conventional Commits specification and branch naming standards.
    WHY: Consistent commit messages allow automated semantic versioning (SemVer),
    automated changelog generation, and immediate traceability for bug fixes.
    """

    # Conventional Commits format: type(optional-scope): description
    CONVENTIONAL_COMMIT_REGEX = re.compile(
        r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
        r"(\([a-zA-Z0-9_\-\./]+\))?"
        r"!?"
        r":\s+"
        r"[a-z0-9].{2,80}$"
    )

    # Enterprise branch naming policy: type/ticket-description
    BRANCH_NAMING_REGEX = re.compile(
        r"^(main|master|develop|staging|"
        r"(feature|bugfix|hotfix|release|chore)\/[a-zA-Z0-9_\-]+)$"
    )

    @classmethod
    def validate_commit_message(cls, message: str) -> Tuple[bool, Optional[str]]:
        """
        Validates commit message subject line against Conventional Commits.
        WHY: Rejects vague messages like 'fixed stuff' or 'updates'.
        """
        lines = message.strip().splitlines()
        if not lines:
            return False, "Commit message cannot be empty."

        subject = lines[0].strip()

        if not cls.CONVENTIONAL_COMMIT_REGEX.match(subject):
            return False, (
                f"Invalid commit message format: '{subject}'.\n"
                "Must follow Conventional Commits: '<type>(<optional-scope>): <description>'\n"
                "Allowed types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.\n"
                "Example: 'feat(auth): implement oauth2 refresh token rotation'"
            )

        if len(lines) > 1 and lines[1].strip() != "":
            return False, "Second line of commit message must be empty to separate subject from body."

        return True, None

    @classmethod
    def validate_branch_name(cls, branch_name: str) -> Tuple[bool, Optional[str]]:
        """
        Validates Git branch name against organizational naming schema.
        WHY: Prevents arbitrary branch names that break automated CI deploy triggers.
        """
        clean_name = branch_name.strip()
        if not cls.BRANCH_NAMING_REGEX.match(clean_name):
            return False, (
                f"Invalid branch name '{clean_name}'.\n"
                "Must adhere to: 'feature/<name>', 'bugfix/<name>', 'hotfix/<name>', or 'main'."
            )
        return True, None


# ============================================================================
# 5. PROGRAMMATIC GIT DAG INSPECTOR (VIA SUBPROCESS)
# ============================================================================

class GitRepositoryInspector:
    """
    Inspects local Git repository state, DAG history, and working tree.
    WHY: Automating Git operations via Python subprocess allows CI/CD systems
    and developer tooling to verify repository health without third-party dependencies.
    """

    def __init__(self, repo_path: Optional[Path] = None) -> None:
        self.repo_path = repo_path or Path.cwd()

    def _run_git(self, args: List[str]) -> Tuple[int, str, str]:
        """Executes a Git CLI command safely in repo directory."""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except FileNotFoundError:
            return -1, "", "Git executable not found in system PATH."

    def is_git_repository(self) -> bool:
        """Verifies if the current directory is inside a valid Git work tree."""
        code, out, _ = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return code == 0 and out == "true"

    def get_current_branch(self) -> str:
        """Fetches active branch name or returns HEAD state."""
        code, out, _ = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if code == 0:
            return out
        return "UNKNOWN_BRANCH"

    def get_recent_commits(self, limit: int = 5) -> List[CommitMetadata]:
        """
        Parses commit history into structured CommitMetadata objects.
        Plumbing format: SHA%x1fPARENT_SHAS%x1fAUTHOR%x1fEMAIL%x1fTIMESTAMP%x1fSUBJECT
        """
        delimiter = "%x1f"
        log_format = f"%H{delimiter}%P{delimiter}%an{delimiter}%ae{delimiter}%ci{delimiter}%s"
        code, out, _ = self._run_git(["log", f"-n{limit}", f"--format={log_format}"])

        if code != 0 or not out:
            return []

        commits: List[CommitMetadata] = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) >= 6:
                sha, parents, author, email, timestamp, subject = parts[:6]
                parent_list = parents.split() if parents else []
                commits.append(
                    CommitMetadata(
                        sha=sha,
                        parent_shas=parent_list,
                        author=author,
                        author_email=email,
                        timestamp=timestamp,
                        message=subject,
                    )
                )
        return commits


# ============================================================================
# 6. DEMONSTRATION & SELF-TEST SUITE
# ============================================================================

def run_git_automation_demo() -> None:
    print("=" * 75)
    print("DAY 15: ENTERPRISE GIT INTERNALS & PRE-COMMIT HOOK VALIDATOR")
    print("=" * 75)

    # 1. Demonstrate Conventional Commit Message Validation
    print("\n[STEP 1] Testing Conventional Commit Message Enforcement:")
    test_messages = [
        ("feat(auth): add jwt refresh token rotation", True),
        ("fix(database): resolve connection pool leak under heavy load", True),
        ("docs: update api deployment instructions for kubernetes", True),
        ("fixed some bugs and updated dependencies", False), # Non-conventional
        ("FEAT: capitalized type not allowed", False),        # Upper-case type
        ("wip", False),                                       # Too short / vague
    ]

    for msg, expected in test_messages:
        valid, err = GitPolicyValidator.validate_commit_message(msg)
        status = "PASSED" if valid == expected else "FAILED"
        print(f"  - Message: '{msg}'")
        print(f"    Result: {'VALID' if valid else 'REJECTED'} (Expect: {'VALID' if expected else 'REJECTED'}) -> [{status}]")
        if err:
            first_err_line = err.splitlines()[0]
            print(f"    Diagnostic: {first_err_line}")

    # 2. Demonstrate Branch Naming Validation
    print("\n[STEP 2] Testing Branch Naming Convention Enforcement:")
    test_branches = [
        ("main", True),
        ("feature/user-authentication", True),
        ("bugfix/PROD-1024-deadlock", True),
        ("hotfix/ssl-certificate-expiry", True),
        ("random_branch_name", False),
        ("test", False),
    ]

    for branch, expected in test_branches:
        valid, err = GitPolicyValidator.validate_branch_name(branch)
        status = "PASSED" if valid == expected else "FAILED"
        print(f"  - Branch: '{branch}' -> {'VALID' if valid else 'REJECTED'} -> [{status}]")

    # 3. Demonstrate Secret Scanner and Shannon Entropy
    print("\n[STEP 3] Testing Secret Scanner & Shannon Entropy Detection:")
    scanner = GitSecurityScanner()

    # Construct test secrets dynamically to avoid false-positive push protection alerts
    dummy_openai = "sk-" + "mockTestingOnlyKeyWithSufficientEntropy123456789"
    dummy_aws = "AKIA" + "00000000MOCKTEST"
    dummy_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "mockSignaturePayloadForTesting12345"

    mock_unsafe_file = (
        "# Application Configuration File\n"
        'DATABASE_URL = "postgresql://postgres:user@localhost:5432/app_db"\n'
        f'OPENAI_API_KEY = "{dummy_openai}"\n'
        f'AWS_SECRET = "{dummy_aws}"\n'
        f'JWT_BEARER = "{dummy_jwt}"\n'
        'SAFE_IDENTIFIER = "normal_variable_name_without_entropy"\n'
    )

    findings = scanner.scan_content(file_path="src/config.py", content=mock_unsafe_file)
    print(f"  Total Security Violations Found: {len(findings)}")
    for f in findings:
        print(f"  [{f.severity.value}] Line {f.line_number}: {f.rule_name}")
        print(f"      Matched Token: {f.redacted_match}")
        print(f"      Details: {f.message}")

    assert len(findings) >= 3, "Expected at least 3 leaked credentials to be caught."

    # 4. Demonstrate Shannon Entropy Calculation
    print("\n[STEP 4] Evaluating Shannon Randomness Entropy:")
    sample_strings = [
        ("English prose sentence for documentation", "Low Entropy Prose"),
        ("variable_name_one_two_three", "Structured Code Identifier"),
        ("a8F#9xL@2qP$7mK!9zT4", "Cryptographic Secret Token"),
    ]
    for sample, label in sample_strings:
        entropy = calculate_shannon_entropy(sample)
        print(f"  - [{label}] Entropy: {entropy:.2f} bits/char | Sample: '{sample[:25]}...'")

    # 5. Programmatic Git DAG Inspection (Subprocess)
    print("\n[STEP 5] Inspecting Local Git Repository DAG:")
    inspector = GitRepositoryInspector()
    if inspector.is_git_repository():
        current_branch = inspector.get_current_branch()
        recent_commits = inspector.get_recent_commits(limit=3)
        print(f"  Current Branch : {current_branch}")
        print(f"  Recent Commits in DAG ({len(recent_commits)} retrieved):")
        for commit in recent_commits:
            merge_tag = " [MERGE]" if commit.is_merge_commit else ""
            print(f"    - SHA: {commit.sha[:8]} | Author: {commit.author} | Msg: {commit.message}{merge_tag}")
    else:
        print("  Notice: Current directory is not a Git repo or Git CLI is unavailable. (Bypassed)")

    print("\n" + "=" * 75)
    print("ALL GIT INTERNALS & PRE-COMMIT VALIDATIONS COMPLETED SUCCESSFULLY [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_git_automation_demo()
