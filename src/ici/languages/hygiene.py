"""Per-file AST hygiene rules as ici's own observation.

``python.security`` and ``python.resource`` are internal checks — the rules
are ici's AST analyses, the same functions the stable ``security`` and
``resource`` engines call per file. The ownership policy is shared too:
generated and vendor files are excluded by ``read_analysis_sources`` and the
exclusion is reported, not skipped in silence (#218).

A file that cannot be parsed is a limitation — nobody checked it — not a
clean result.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.engines._exception_rules import (
    analyze_cpp_exceptions,
    analyze_python_exceptions,
)
from ici.engines._python_resources import (
    ResourceAnalysisLimit,
    analyze_python_resources,
)
from ici.engines._python_security import analyze_python_security
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources

__all__ = ["PROVIDER_NAME", "HygieneRequest", "measure_hygiene"]

PROVIDER_NAME = "ici.hygiene"


@dataclass(frozen=True)
class HygieneRequest:
    """The Python files one hygiene check reads."""

    kind: str  # "security" or "resource"
    project_root: Path
    files: tuple[Path, ...]
    task_id: str
    component_id: str = ""


def measure_hygiene(request: HygieneRequest) -> Observation:
    """Run the check's AST rules over every owned file in the scope."""

    try:
        inventory = read_analysis_sources(request.project_root, request.files)
    except AnalysisSourceError as error:
        return Observation(
            task_id=request.task_id,
            provider=PROVIDER_NAME,
            state=TaskState.FAILED,
            limitations=(f"{error.code}: {error.message}",),
        )

    limitations = [f"excluded {item.file_path}: {item.reason}" for item in inventory.excluded]
    findings: list[Finding] = []
    files_checked = 0
    for source in inventory.sources:
        try:
            if request.kind == "security":
                analysis = analyze_python_security(source.file_path, source.text)
                for target in analysis.findings:
                    findings.append(_finding(request, source.file_path, target, severity="high"))
            elif request.kind == "resource":
                analysis = analyze_python_resources(source.file_path, source.text)
                for issue in analysis.issues:
                    findings.append(
                        _finding(
                            request,
                            source.file_path,
                            issue.target,
                            category=issue.category.value,
                            confidence=issue.confidence.value,
                        )
                    )
            elif source.file_path.endswith(".py"):
                analysis = analyze_python_exceptions(source.file_path, source.text)
                if analysis.error_message:
                    limitations.append(
                        f"{source.file_path}:{analysis.error_line}: "
                        f"{analysis.error_message} — exception analysis was not run"
                    )
                    continue
                findings.extend(
                    _finding(
                        request,
                        source.file_path,
                        target,
                        severity="high" if target.status.value == "FAIL" else "medium",
                        category="correctness",
                    )
                    for target in analysis.targets
                    if target.status.value in {"FAIL", "WARN"}
                )
            else:
                analysis = analyze_cpp_exceptions(source.file_path, source.text)
                findings.extend(
                    _finding(
                        request,
                        source.file_path,
                        target,
                        severity="high",
                        category="correctness",
                        confidence="medium",
                    )
                    for target in analysis.targets
                    if target.status.value == "FAIL"
                )
        except SyntaxError as error:
            limitations.append(
                f"{source.file_path}:{error.lineno or 1}: syntax invalid — "
                f"{request.kind} analysis was not run"
            )
            continue
        except ResourceAnalysisLimit as error:
            limitations.append(f"{source.file_path}: {error}")
            continue
        files_checked += 1

    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=(
            Measurement(name="files_checked", value=files_checked, unit="files"),
            Measurement(name="issues_found", value=len(findings), unit="issues"),
        ),
        limitations=tuple(limitations),
    )


def _finding(
    request: HygieneRequest,
    path: str,
    target,
    *,
    severity: str = "medium",
    category: str = "",
    confidence: str = "high",
) -> Finding:
    """One InspectionTarget from the shared rule modules, normalized."""

    rule = target.target_name.split(":", 1)[-1] or target.target_name
    digest = hashlib.sha1(
        f"{path}:{target.start_line}:{rule}:{target.message}".encode()
    ).hexdigest()[:16]
    return Finding(
        fingerprint=f"hygiene-{digest}",
        rule_id=f"{request.kind}.{rule.lower().replace('_', '-')}",
        native_rule_id=target.target_name,
        message=target.message or rule,
        severity=severity,
        confidence=confidence,
        category=category,
        primary_location=SourceSpan(
            path=path,
            start_line=target.start_line,
            end_line=target.end_line,
            start_column=target.start_column,
            end_column=target.end_column,
        ),
        provider=PROVIDER_NAME,
        component_id=request.component_id or None,
        task_id=request.task_id,
        evidence=EvidenceLevel.MEASURED,
    )
