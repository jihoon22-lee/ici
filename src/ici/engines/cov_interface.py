"""Coverity Static Defect Analysis Pluggable Interface & Local Scanner."""

import os
import re
import time

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    get_all_cpp_sources,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine

UNSAFE_C_FUNCS = [
    (r"\bgets\b", "UNSAFE_GETS", "Critical buffer overflow liability with 'gets'"),
    (r"\bstrcpy\s*\(", "UNSAFE_STRCPY", "Unbounded string copy 'strcpy'"),
    (r"\bsprintf\s*\(", "UNSAFE_SPRINTF", "Unsafe formatting without buffer bounds 'sprintf'"),
    (r"\bstrcat\s*\(", "UNSAFE_STRCAT", "Unbounded string concatenation 'strcat'"),
]


class CoverityInterface(BaseEngine):
    """Pluggable interface for enterprise Coverity analyzer with lightweight local fallback."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cov_cmd = os.environ.get("COVERITY_CMD") or self.config.get("coverity", {}).get("command")
        targets: list[InspectionTarget] = []

        # 1. External Coverity Tool Execution if Configured
        if cov_cmd:
            code, out, err, dur = run_process([cov_cmd], cwd=self.project_root)
            st = EngineStatus.PASS if code == 0 else EngineStatus.FAIL
            return self.create_result(
                name="cov",
                status=st,
                summary=f"Coverity External Tool executed ({dur:.2f}s)",
                duration=dur,
                raw_output=out + "\n" + err,
            )

        # 2. Local Static Defect Pattern Scanner (Default Mode)
        for cpp_file in get_all_cpp_sources(self.project_root):
            try:
                rel_p = str(cpp_file.relative_to(self.project_root))
                with open(cpp_file, encoding="utf-8", errors="ignore") as f:
                    for line_idx, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped.startswith(("//", "/*", "*")):
                            continue
                        for pat, rule_id, desc in UNSAFE_C_FUNCS:
                            if re.search(pat, line):
                                targets.append(
                                    InspectionTarget(
                                        file_path=rel_p,
                                        start_line=line_idx,
                                        target_name=rule_id,
                                        status=EngineStatus.WARN,
                                        message=desc,
                                        snippet=stripped[:80],
                                    )
                                )
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        duration = time.time() - t0
        warn_count = len(targets)
        overall_status = EngineStatus.WARN if warn_count > 0 else EngineStatus.PASS
        summary = (
            "Coverity Analysis Clean"
            if overall_status == EngineStatus.PASS
            else f"{warn_count} Potential Static Defects Identified (Local Rule Mode)"
        )

        return self.create_result(
            name="cov",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={"defects_count": warn_count, "metrics_summary": f"{warn_count} defects"},
        )
