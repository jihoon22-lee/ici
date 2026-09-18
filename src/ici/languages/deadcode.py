"""Dead-code detection as ici's own observation (#218).

``python.dead`` runs the same cross-file heuristic the stable ``dead``
engine calls — ``analyze_python_dead_code`` — over the component's owned
files. Definitions and uses are correlated across the whole snapshot, so
this is one scope-level analysis, not a per-file rule.

The C++ unused-function replay and linker GC-section probes stay with the
stable engine for now: they execute the user's compiler and linker, which
belongs to the tool-backed provider work, not this in-process check.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.engines._python_dead_code import analyze_python_dead_code
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources

__all__ = ["PROVIDER_NAME", "DeadRequest", "measure_dead"]

PROVIDER_NAME = "ici.deadcode"

_SEVERITY = {"FAIL": "high", "WARN": "medium", "ERROR": "high"}


@dataclass(frozen=True)
class DeadRequest:
    """The scope one dead-code analysis covers."""

    project_root: Path
    source_dirs: tuple[Path, ...]
    files: tuple[Path, ...]
    task_id: str
    component_id: str = ""


def measure_dead(request: DeadRequest) -> Observation:
    """Correlate definitions and uses across the scope's Python sources."""

    try:
        inventory = read_analysis_sources(request.project_root, request.files)
    except AnalysisSourceError as error:
        return Observation(
            task_id=request.task_id,
            provider=PROVIDER_NAME,
            state=TaskState.FAILED,
            limitations=(f"{error.code}: {error.message}",),
        )

    sources = tuple(source for source in inventory.sources if source.language == "python")
    limitations = [f"excluded {item.file_path}: {item.reason}" for item in inventory.excluded]
    targets, errors = analyze_python_dead_code(
        request.project_root, list(request.source_dirs), sources
    )
    limitations.extend(errors)

    findings = tuple(
        _finding(request, target) for target in targets if target.status.value in _SEVERITY
    )
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=findings,
        measurements=(
            Measurement(name="files_checked", value=len(sources), unit="files"),
            Measurement(name="issues_found", value=len(findings), unit="issues"),
        ),
        limitations=tuple(limitations),
    )


def _finding(request: DeadRequest, target) -> Finding:
    """The heuristic marks its own confidence — keep it estimated, not exact."""

    rule = target.target_name or "dead-code"
    digest = hashlib.sha1(
        f"{target.file_path}:{target.start_line}:{rule}:{target.message}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return Finding(
        fingerprint=f"dead-{digest}",
        rule_id=f"dead.{rule.lower().replace('_', '-')}",
        native_rule_id=rule,
        message=target.message or rule,
        severity=_SEVERITY[target.status.value],
        confidence="medium",
        category="maintainability",
        primary_location=SourceSpan(
            path=target.file_path,
            start_line=target.start_line,
            end_line=target.end_line,
            start_column=target.start_column,
            end_column=target.end_column,
        ),
        provider=PROVIDER_NAME,
        component_id=request.component_id or None,
        task_id=request.task_id,
        evidence=EvidenceLevel.ESTIMATED,
    )
