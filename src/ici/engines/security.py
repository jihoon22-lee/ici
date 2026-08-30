"""Security hygiene — hardcoded secrets and weak crypto, offline regex."""

import re
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import _iter_project_files
from ici.engines.base import BaseEngine

# Patterns whose match text contains actual confidential material. These drive
# redaction and are applied to EVERY reported line, independently of which
# pattern produced the finding -- one line can match a secret pattern and a
# non-secret one at once (e.g. `password = "..." ; eval(x)`), and the
# non-secret finding must not echo the secret back into the report.
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?P<key>password|passwd|secret|api_key|aws_access_key|aws_secret)"
    r'(?P<op>\s*[=:]\s*)(?P<quote>["\'])(?P<value>[^"\']{6,})(?P=quote)'
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*$")

# Patterns: (name, regex, message)
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("HardcodedSecret", _SECRET_VALUE_RE, "Hardcoded secret-like assignment"),
    ("PrivateKey", _PRIVATE_KEY_RE, "Private key block"),
    ("WeakCryptoMD5", re.compile(r"hashlib\.md5\s*\("), "Weak crypto: hashlib.md5"),
    ("WeakCryptoSHA1", re.compile(r"hashlib\.sha1\s*\("), "Weak crypto: hashlib.sha1"),
    (
        "WeakRandom",
        re.compile(r"\brandom\.(random|randint|choice|randrange)\s*\("),
        "Weak random: use secrets module",
    ),
    ("EvalExec", re.compile(r"\b(eval|exec)\s*\("), "Dangerous eval/exec"),
    ("PickleLoad", re.compile(r"pickle\.loads?\s*\("), "Pickle deserialization"),
    (
        "ShellTrue",
        re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
        "subprocess with shell=True",
    ),
]

_REDACTED = "***REDACTED***"


def _mask_secret_assignment(match: re.Match[str]) -> str:
    return (
        f"{match.group('key')}{match.group('op')}{match.group('quote')}"
        f"{_REDACTED}{match.group('quote')}"
    )


def redact_secrets(line: str) -> str:
    """Return a display-safe copy of ``line`` with every secret span masked.

    Applied once per source line and reused for all findings on that line, so
    a non-secret pattern (weak crypto, eval/exec, ...) can never smuggle a
    co-located secret into the report. The reports this feeds -- HTML, JSON,
    and the gh-pages copy published by ``--publish`` -- are routinely shared
    more widely than the source itself.
    """
    masked = _SECRET_VALUE_RE.sub(_mask_secret_assignment, line)
    masked = _PRIVATE_KEY_RE.sub("-----BEGIN [REDACTED] PRIVATE KEY-----", masked)
    return masked.strip()


class SecurityEngine(BaseEngine):
    """Detects hardcoded secrets and weak crypto via offline regex."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("security")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))

        # Allow disabling via config, and allow scanning tests if explicitly enabled
        scan_tests = bool(cfg.get("scan_tests", False))
        targets: list[InspectionTarget] = []

        seen: set[Path] = set()
        sources = self.project_python_sources()
        if scan_tests:
            sources.extend(self._test_python_files())

        for py_file in sources:
            if py_file in seen:
                continue
            seen.add(py_file)
            rel = str(py_file.relative_to(self.project_root))
            if not scan_tests and "tests" in Path(rel).parts:
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lines = content.splitlines()
            for idx, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip lines with # nosec, and skip comment-only lines to cut noise
                # from documentation/examples that merely mention these patterns.
                if "nosec" in line or stripped.startswith("#"):
                    continue
                matched = [(name, msg) for name, pattern, msg in _PATTERNS if pattern.search(line)]
                if not matched:
                    continue
                # Redact once per line, then reuse for every finding on it — a
                # non-secret pattern must not echo a co-located secret.
                display = redact_secrets(line)
                for name, msg in matched:
                    targets.append(
                        InspectionTarget(
                            file_path=rel,
                            start_line=idx,
                            target_name=f"Security:{name}",
                            status=EngineStatus.WARN,
                            message=f"{msg}: {display[:120]}",
                            snippet=display[:200],
                        )
                    )

        has_warn = bool(targets)
        status = self.evaluate_status(False, has_warn, mode)
        summary = (
            f"Security hygiene: {len(targets)} finding(s)" if has_warn else "Security hygiene clean"
        )
        return self.create_result(
            name="security",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=targets,
            required=required,
            evidence=EvidenceState.MEASURED,
        )

    def _test_python_files(self) -> list[Path]:
        """Yields Python files under the conventional top-level ``tests/`` dir.

        ``get_all_python_sources`` only walks ``project.source_dirs``, which
        never includes ``tests/`` — so ``scan_tests`` needs its own explicit
        walk to actually have an effect.
        """
        tests_dir = self.project_root / "tests"
        if not tests_dir.is_dir():
            return []
        return list(_iter_project_files(tests_dir, self.project_root, (".py",)))
