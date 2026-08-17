"""Security Analysis Module (SAM) Pluggable Interface & Local Scanner."""

import os
import re
import time

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    get_all_cpp_sources,
    get_all_python_sources,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine

SECURITY_PATTERNS = [
    (
        r'(?i)(api[_-]?key|secret[_-]?token|password|passwd)\s*=\s*[\'"][A-Za-z0-9_\-]{8,}[\'"]',
        "HARDCODED_SECRET",
        "HIGH",
        15.0,
    ),
    (r"(?i)\beval\s*\(", "ARBITRARY_CODE_EVAL", "CRITICAL", 25.0),
    (
        r"(?i)subprocess\.(Popen|run|call)\s*\(.*shell\s*=\s*True",
        "SHELL_INJECTION_RISK",
        "HIGH",
        15.0,
    ),
    (r"(?i)\bos\.system\s*\(", "UNSAFE_SYSTEM_EXEC", "HIGH", 15.0),
    (r"(?i)pickle\.loads\s*\(", "INSECURE_DESERIALIZATION", "CRITICAL", 25.0),
]


class SAMInterface(BaseEngine):
    """Pluggable interface for enterprise SAM security scanner with lightweight local fallback."""

    def run(self) -> EngineResult:
        t0 = time.time()
        sam_cmd = os.environ.get("SAM_CMD") or self.config.get("sam", {}).get("command")
        targets: list[InspectionTarget] = []

        # 1. External SAM Tool Execution if Configured
        if sam_cmd:
            code, out, err, dur = run_process([sam_cmd], cwd=self.project_root)
            st = EngineStatus.PASS if code == 0 else EngineStatus.FAIL
            return self.create_result(
                name="sam",
                status=st,
                summary=f"SAM Security External Tool executed ({dur:.2f}s)",
                duration=dur,
                raw_output=out + "\n" + err,
            )

        # 2. Local Security Pattern Scanner & 100-Point Score
        score = 100.0
        all_sources = get_all_python_sources(self.project_root) + get_all_cpp_sources(
            self.project_root
        )

        for src_file in all_sources:
            try:
                rel_p = str(src_file.relative_to(self.project_root))
                with open(src_file, encoding="utf-8", errors="ignore") as f:
                    for line_idx, line in enumerate(f, 1):
                        stripped = line.strip()
                        if (
                            stripped.startswith(("#", "//", "/*", "*"))
                            or "SECURITY_PATTERNS" in line
                        ):
                            continue

                        for pat, rule_id, severity, penalty in SECURITY_PATTERNS:
                            if re.search(pat, line):
                                score -= penalty
                                st = (
                                    EngineStatus.FAIL
                                    if severity == "CRITICAL"
                                    else EngineStatus.WARN
                                )
                                targets.append(
                                    InspectionTarget(
                                        file_path=rel_p,
                                        start_line=line_idx,
                                        target_name=f"SAM:{rule_id}",
                                        status=st,
                                        message=f"[{severity}] Security finding: {rule_id}",
                                        snippet=stripped[:80],
                                    )
                                )
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        score = max(0.0, score)
        duration = time.time() - t0
        overall_status = (
            EngineStatus.PASS
            if score >= 80.0
            else (EngineStatus.WARN if score >= 60.0 else EngineStatus.FAIL)
        )
        summary = f"SAM Security Audit Score: {score:.1f} / 100.0 ({len(targets)} findings)"

        return self.create_result(
            name="sam",
            status=overall_status,
            summary=summary,
            score=score,
            max_score=100.0,
            duration=duration,
            targets=targets,
            extra={"security_score": score, "metrics_summary": f"Score: {score:.1f}/100"},
        )
