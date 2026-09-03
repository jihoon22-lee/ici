"""Python security hygiene using bounded, redaction-safe AST rules."""

from __future__ import annotations

import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import _iter_project_files
from ici.engines._python_security import analyze_python_security
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources
from ici.engines.base import BaseEngine


class SecurityEngine(BaseEngine):
    """Detect Python secret, crypto, deserialization, and command risks."""

    CACHE_IMPLEMENTATION_MODULES = (
        "ici.engines._python_resource_scopes",
        "ici.engines._python_security",
        "ici.engines._source_inputs",
        "ici.engines.security",
    )

    def run(self) -> EngineResult:
        started = time.time()
        cfg = self.get_config("security")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))
        allowlist = frozenset(name.casefold() for name in cfg.get("secret_name_allowlist", []))
        selected = self.project_python_sources()
        if cfg.get("scan_tests", False):
            selected.extend(self._test_python_files())
        if not selected:
            return self.create_result(
                name="security",
                status=EngineStatus.SKIP,
                summary="Security analysis skipped: no Python source files",
                duration=time.time() - started,
                targets=[
                    InspectionTarget(
                        file_path=".",
                        start_line=1,
                        target_name="Security:NotApplicable",
                        status=EngineStatus.SKIP,
                        message="No applicable Python source files were selected",
                    )
                ],
                required=required,
                evidence=EvidenceState.NOT_APPLICABLE,
            )

        targets: list[InspectionTarget] = []
        errors: list[str] = []
        files_checked = 0
        calls_checked = 0
        secret_literals_checked = 0
        excluded_counts: dict[str, int] = {}
        try:
            inventory = read_analysis_sources(self.project_root, selected)
            excluded_counts = inventory.exclusion_counts
            for source in inventory.sources:
                if source.language != "python":
                    continue
                files_checked += 1
                try:
                    analysis = analyze_python_security(
                        source.file_path,
                        source.text,
                        secret_name_allowlist=allowlist,
                    )
                except SyntaxError as error:
                    line = max(1, error.lineno or 1)
                    targets.append(
                        InspectionTarget(
                            file_path=source.file_path,
                            start_line=line,
                            start_column=error.offset,
                            target_name="Security:SyntaxUnavailable",
                            status=EngineStatus.ERROR,
                            message="Python syntax is invalid; AST security analysis was not run",
                        )
                    )
                    errors.append(f"{source.file_path}:{line}: syntax prevents security analysis")
                    continue
                calls_checked += analysis.checked_calls
                secret_literals_checked += analysis.secret_literals_checked
                targets.extend(analysis.findings)
                targets.append(
                    InspectionTarget(
                        file_path=source.file_path,
                        start_line=1,
                        target_name="Security:ASTScan",
                        status=EngineStatus.PASS,
                        message="Bounded Python AST security rules completed",
                    )
                )
        except (AnalysisSourceError, OSError, RuntimeError, ValueError) as error:
            file_path = error.file_path if isinstance(error, AnalysisSourceError) else "."
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=1,
                    target_name="Security:SourceInput",
                    status=EngineStatus.ERROR,
                    message=str(error),
                )
            )
            errors.append(str(error))

        finding_count = sum(target.status == EngineStatus.WARN for target in targets)
        if errors:
            status = EngineStatus.ERROR
            summary = f"Security analysis incomplete: {errors[0]}"
        else:
            status = self.evaluate_status(False, finding_count > 0, mode)
            summary = (
                f"Security hygiene: {finding_count} finding(s)"
                if finding_count
                else f"Security hygiene clean across {files_checked} file(s)"
            )
        return self.create_result(
            name="security",
            status=status,
            summary=summary,
            duration=time.time() - started,
            targets=targets,
            extra={
                "analysis_mode": "python-ast-rules-v1",
                "files_checked": files_checked,
                "calls_checked": calls_checked,
                "secret_literals_checked": secret_literals_checked,
                "secret_name_allowlist_count": len(allowlist),
                "excluded_source_counts": excluded_counts,
                "limitations": [
                    "Rules identify risky syntax and import aliases, not runtime taint flow",
                    "Exact secret names can be allowlisted; secret values are never retained",
                ],
            },
            required=required,
            evidence=EvidenceState.NOT_RUN if errors else EvidenceState.MEASURED,
        )

    def _test_python_files(self) -> list[Path]:
        tests_dir = self.project_root / "tests"
        if not tests_dir.is_dir():
            return []
        return list(_iter_project_files(tests_dir, self.project_root, (".py",)))
