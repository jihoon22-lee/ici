"""9. Exception handling safety and anti-pattern detection engine."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.engines._exception_rules import (
    analyze_cpp_exceptions,
    analyze_python_exceptions,
)
from ici.engines.base import BaseEngine

if TYPE_CHECKING:
    from ici.core.context import AnalysisContext


class ExceptionSafetyEngine(BaseEngine):
    """Detect swallowed errors, lost Python tracebacks, and unsafe C++ throws."""

    def __init__(
        self,
        project_root: Path | None = None,
        config: dict[str, Any] | None = None,
        analysis_context: AnalysisContext | None = None,
    ) -> None:
        super().__init__(project_root, config, analysis_context)
        self._analysis_errors: list[str] = []

    def run(self) -> EngineResult:
        t0 = time.time()
        self._analysis_errors = []
        targets: list[InspectionTarget] = []
        py_sources = self.project_python_sources()
        cpp_sources = self.project_cpp_sources()
        proj_type = self.project_type()
        has_python_scope = bool(py_sources) or proj_type in ("python", "hybrid")
        has_cpp_scope = bool(cpp_sources) or proj_type in ("cpp", "hybrid")
        has_error = False
        has_warning = False
        if has_python_scope and py_sources:
            py_error, py_warning = self._check_python_exceptions(targets)
            has_error = has_error or py_error
            has_warning = has_warning or py_warning
        if has_cpp_scope and cpp_sources:
            cpp_error = self._check_cpp_exceptions(targets)
            has_error = has_error or cpp_error
        if not py_sources and not cpp_sources:
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="ExceptionSafety",
                    status=EngineStatus.SKIP,
                    message="No applicable Python or C++ source files were selected; analysis was not run",
                )
            )

        cfg = self.get_config("exception")
        duration = time.time() - t0
        fail_count = sum(1 for target in targets if target.status == EngineStatus.FAIL)
        warn_count = sum(1 for target in targets if target.status == EngineStatus.WARN)
        if self._analysis_errors:
            overall_status = EngineStatus.ERROR
            evidence = EvidenceState.NOT_RUN
            summary = "; ".join(self._analysis_errors[:3])
        elif not py_sources and not cpp_sources:
            overall_status = EngineStatus.SKIP
            evidence = EvidenceState.NOT_APPLICABLE
            summary = "Exception safety analysis skipped: no applicable source files"
        else:
            overall_status = self.evaluate_status(
                has_error, has_warning, cfg.get("mode", "pass_fail")
            )
            evidence = EvidenceState.MEASURED
            summary = (
                "Exception Handling Safety Clean"
                if overall_status == EngineStatus.PASS
                else f"{fail_count} Critical Exception Violations, {warn_count} Warnings"
            )
        return self.create_result(
            name="exception",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "violations_count": fail_count + warn_count,
                "metrics_summary": f"{fail_count} exc errors",
            },
            required=bool(cfg.get("required", True)),
            evidence=evidence,
        )

    def _check_python_exceptions(self, targets: list[InspectionTarget]) -> tuple[bool, bool]:
        has_error = False
        has_warning = False
        for py_file in self.project_python_sources():
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as err:
                self._append_analysis_error(
                    targets, py_file, "ReadError", f"Could not read Python source: {err}", 1
                )
                continue
            rel_path = str(py_file.relative_to(self.project_root))
            analysis = analyze_python_exceptions(rel_path, content)
            if analysis.error_message:
                self._append_analysis_error(
                    targets,
                    py_file,
                    analysis.error_name or "AnalysisError",
                    analysis.error_message,
                    analysis.error_line,
                )
                continue
            targets.extend(analysis.targets)
            has_error = has_error or analysis.has_error
            has_warning = has_warning or analysis.has_warning
        return has_error, has_warning

    def _check_cpp_exceptions(self, targets: list[InspectionTarget]) -> bool:
        has_error = False
        for cpp_file in self.project_cpp_sources():
            try:
                content = cpp_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as err:
                self._append_analysis_error(
                    targets, cpp_file, "ReadError", f"Could not read C++ source: {err}", 1
                )
                continue
            rel_path = str(cpp_file.relative_to(self.project_root))
            analysis = analyze_cpp_exceptions(rel_path, content)
            targets.extend(analysis.targets)
            has_error = has_error or analysis.has_error
        return has_error

    def _append_analysis_error(
        self,
        targets: list[InspectionTarget],
        path,
        name: str,
        message: str,
        line: int,
    ) -> None:
        self._analysis_errors.append(message)
        targets.append(
            InspectionTarget(
                file_path=str(path.relative_to(self.project_root)),
                start_line=line,
                target_name=name,
                status=EngineStatus.ERROR,
                message=message,
            )
        )
