"""Security hygiene — hardcoded secrets and weak crypto, offline regex."""

import re
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import get_all_python_sources
from ici.engines.base import BaseEngine

# Patterns: (name, regex, message)
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "HardcodedSecret",
        re.compile(
            r'(?i)(password|passwd|secret|api_key|aws_access_key|aws_secret)\s*[=:]\s*["\'][^"\']{6,}["\']'
        ),
        "Hardcoded secret-like assignment",
    ),
    ("PrivateKey", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "Private key block"),
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

        for py_file in get_all_python_sources(self.project_root, self.config):
            rel = str(py_file.relative_to(self.project_root))
            if not scan_tests and "tests" in Path(rel).parts:
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lines = content.splitlines()
            for idx, line in enumerate(lines, 1):
                # Skip lines with # nosec
                if "nosec" in line:
                    continue
                for name, pattern, msg in _PATTERNS:
                    if pattern.search(line):
                        targets.append(
                            InspectionTarget(
                                file_path=rel,
                                start_line=idx,
                                target_name=f"Security:{name}",
                                status=EngineStatus.WARN,
                                message=f"{msg}: {line.strip()[:120]}",
                                snippet=line.strip()[:200],
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
