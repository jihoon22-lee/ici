"""Bounded Python resource-lifetime and mutable-default analysis."""

from __future__ import annotations

import time
from collections import Counter

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
)
from ici.engines._python_resources import (
    PythonResourceAnalysis,
    ResourceAnalysisLimit,
    ResourceIssue,
    analyze_python_resources,
)
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources
from ici.engines.base import BaseEngine


def _native_findings(issues: list[ResourceIssue]) -> list[Finding]:
    counts = Counter((item.target.file_path, item.target.target_name.strip()) for item in issues)
    findings: list[Finding] = []
    for issue in issues:
        target = issue.target
        key = (target.file_path, target.target_name.strip())
        findings.append(
            Finding(
                rule_id="ici.legacy.resource.target",
                category=issue.category,
                severity=FindingSeverity.MEDIUM,
                confidence=issue.confidence,
                fingerprint="",
                primary_location=SourceLocation(
                    path=target.file_path,
                    start_line=target.start_line,
                    end_line=target.end_line,
                    start_column=target.start_column,
                    end_column=target.end_column,
                    label=target.target_name if counts[key] == 1 else "",
                ),
                message=target.message,
                explanation=issue.explanation,
                remediation=issue.remediation,
                tool_rule_id=target.target_name,
                tool_name="ici Python AST",
                snippet=target.snippet,
            )
        )
    return findings


class ResourceEngine(BaseEngine):
    """Distinguish managed, closed, transferred, and possibly leaked resources."""

    CACHE_IMPLEMENTATION_MODULES = (
        "ici.engines._python_resource_scopes",
        "ici.engines._python_resources",
        "ici.engines._source_inputs",
        "ici.engines.resource",
    )

    def run(self) -> EngineResult:
        started = time.time()
        cfg = self.get_config("resource")
        selected = self.project_python_sources()
        if not selected:
            return self.create_result(
                name="resource",
                status=EngineStatus.SKIP,
                summary="Resource analysis skipped: no Python source files",
                duration=time.time() - started,
                targets=[
                    InspectionTarget(
                        file_path=".",
                        start_line=1,
                        target_name="Resource:NotApplicable",
                        status=EngineStatus.SKIP,
                        message="No applicable Python source files were selected",
                    )
                ],
                required=bool(cfg.get("required", False)),
                evidence=EvidenceState.NOT_APPLICABLE,
            )

        targets: list[InspectionTarget] = []
        issues: list[ResourceIssue] = []
        errors: list[str] = []
        files_checked = 0
        acquisitions_checked = 0
        mutable_defaults_checked = 0
        ast_nodes = 0
        excluded_counts: dict[str, int] = {}
        try:
            inventory = read_analysis_sources(self.project_root, selected)
            excluded_counts = inventory.exclusion_counts
            for source in inventory.sources:
                if source.language != "python":
                    continue
                files_checked += 1
                analysis = self._analyze_source(source.file_path, source.text, targets, errors)
                if analysis is None:
                    continue
                issues.extend(analysis.issues)
                acquisitions_checked += analysis.acquisitions_checked
                mutable_defaults_checked += analysis.mutable_defaults_checked
                ast_nodes += analysis.ast_nodes
                targets.append(
                    InspectionTarget(
                        file_path=source.file_path,
                        start_line=1,
                        target_name="Resource:ASTFlow",
                        status=EngineStatus.PASS,
                        message="Bounded intraprocedural resource-flow analysis completed",
                    )
                )
        except (AnalysisSourceError, OSError, RuntimeError, ValueError) as error:
            file_path = error.file_path if isinstance(error, AnalysisSourceError) else "."
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=1,
                    target_name="Resource:SourceInput",
                    status=EngineStatus.ERROR,
                    message=str(error),
                )
            )
            errors.append(str(error))
        targets.extend(issue.target for issue in issues)

        if errors:
            status = EngineStatus.ERROR
            summary = f"Resource analysis incomplete: {errors[0]}"
            evidence = EvidenceState.NOT_RUN
        else:
            status = self.evaluate_status(False, bool(issues), cfg.get("mode", "pass_warn"))
            summary = (
                f"Resource/correctness analysis: {len(issues)} finding(s)"
                if issues
                else f"Resource/correctness analysis clean across {files_checked} file(s)"
            )
            evidence = EvidenceState.MEASURED
        result = self.create_result(
            name="resource",
            status=status,
            summary=summary,
            duration=time.time() - started,
            targets=targets,
            extra={
                "analysis_mode": "python-intraprocedural-resource-flow-v1",
                "files_checked": files_checked,
                "acquisitions_checked": acquisitions_checked,
                "mutable_defaults_checked": mutable_defaults_checked,
                "ast_nodes": ast_nodes,
                "excluded_source_counts": excluded_counts,
                "limitations": [
                    "Flow is intraprocedural and does not infer arbitrary user-defined factories",
                    "A direct close is lexical evidence, not proof that close itself cannot raise",
                    "Returned resources and assignments to attributes or subscripts transfer ownership",
                ],
            },
            required=bool(cfg.get("required", False)),
            evidence=evidence,
        )
        result.findings = _native_findings(issues)
        return result

    @staticmethod
    def _analyze_source(
        file_path: str,
        text: str,
        targets: list[InspectionTarget],
        errors: list[str],
    ) -> PythonResourceAnalysis | None:
        try:
            return analyze_python_resources(file_path, text)
        except SyntaxError as error:
            line = max(1, error.lineno or 1)
            message = "Python syntax is invalid; resource-flow analysis was not run"
            column = error.offset if isinstance(error.offset, int) and error.offset > 0 else None
        except ResourceAnalysisLimit as error:
            line = 1
            column = None
            message = str(error)
        targets.append(
            InspectionTarget(
                file_path=file_path,
                start_line=line,
                start_column=column,
                target_name="Resource:AnalysisUnavailable",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        errors.append(f"{file_path}:{line}: {message}")
        return None
