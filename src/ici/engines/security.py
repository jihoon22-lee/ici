"""Security hygiene — hardcoded secrets and weak crypto, offline regex."""

import re
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import _iter_project_files, get_all_python_sources
from ici.engines.base import BaseEngine

# Patterns: (name, regex, message, sensitive)
# ``sensitive`` patterns capture an actual secret value in group 1 and must be
# redacted before the matched line is ever placed into a report (message/snippet).
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?P<key>password|passwd|secret|api_key|aws_access_key|aws_secret)"
    r'(?P<op>\s*[=:]\s*)(?P<quote>["\'])(?P<value>[^"\']{6,})(?P=quote)'
)

_PATTERNS: list[tuple[str, re.Pattern[str], str, bool]] = [
    ("HardcodedSecret", _SECRET_VALUE_RE, "Hardcoded secret-like assignment", True),
    ("PrivateKey", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "Private key block", True),
    ("WeakCryptoMD5", re.compile(r"hashlib\.md5\s*\("), "Weak crypto: hashlib.md5", False),
    ("WeakCryptoSHA1", re.compile(r"hashlib\.sha1\s*\("), "Weak crypto: hashlib.sha1", False),
    (
        "WeakRandom",
        re.compile(r"\brandom\.(random|randint|choice|randrange)\s*\("),
        "Weak random: use secrets module",
        False,
    ),
    ("EvalExec", re.compile(r"\b(eval|exec)\s*\("), "Dangerous eval/exec", False),
    ("PickleLoad", re.compile(r"pickle\.loads?\s*\("), "Pickle deserialization", False),
    (
        "ShellTrue",
        re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
        "subprocess with shell=True",
        False,
    ),
]

_REDACTED = "***REDACTED***"


def _redact_line(name: str, pattern: re.Pattern[str], line: str, stripped: str) -> str:
    """Return a display-safe copy of ``line`` with any captured secret masked.

    Only patterns flagged ``sensitive`` capture a real secret value; all other
    findings (weak crypto, eval/exec, ...) never contain confidential material
    so the original stripped line is safe to echo back unmodified.
    """
    if name == "HardcodedSecret":
        match = pattern.search(line)
        if match:
            return (
                f"{match.group('key')}{match.group('op')}{match.group('quote')}"
                f"{_REDACTED}{match.group('quote')}"
            )
    if name == "PrivateKey":
        return "-----BEGIN [REDACTED] PRIVATE KEY-----"
    return stripped[:200]


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
        sources = list(get_all_python_sources(self.project_root, self.config))
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
                for name, pattern, msg, _sensitive in _PATTERNS:
                    if pattern.search(line):
                        display = _redact_line(name, pattern, line, stripped)
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
