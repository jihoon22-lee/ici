"""Import/include cycles as ici's own observation.

A cycle is a fact about the component's module or include graph, not a tool
run — so ``*.cycle`` is an internal check. Python imports resolve through the
component's own module index; C++ ``#include "..."`` lines resolve by path
suffix against the component's files, which is a heuristic (no compiler
search order, no generated headers) — C++ findings are therefore ESTIMATED,
never MEASURED (#218 item 5).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.engines.cycle import (
    _build_cpp_graph,
    _build_python_graph,
    _find_cycles_tarjan,
)

__all__ = ["PROVIDER_NAME", "CycleRequest", "measure_cycles"]

PROVIDER_NAME = "ici.cycles"


@dataclass(frozen=True)
class CycleRequest:
    """The component files whose import/include graph is checked."""

    language: str  # "python" or "cpp"
    project_root: Path
    files: tuple[Path, ...]
    task_id: str
    component_id: str = ""


def measure_cycles(request: CycleRequest) -> Observation:
    """Build the dependency graph and report every cycle it contains."""

    if request.language == "cpp":
        return _measure_cpp(request)
    return _measure_python(request)


def _measure_python(request: CycleRequest) -> Observation:
    root = request.project_root
    graph, module_to_file = _build_python_graph(
        root, source_dirs=[root], all_sources=list(request.files)
    )
    findings: list[Finding] = []
    for members in _find_cycles_tarjan(graph):
        files = [module_to_file.get(mod) for mod in members]
        spans = tuple(
            SourceSpan(path=_relative(path, root), start_line=1)
            for path in files
            if path is not None
        )
        if not spans:
            continue
        chain = " -> ".join(members + members[:1])
        findings.append(
            Finding(
                fingerprint=_fingerprint(request.language, members),
                rule_id="cycle.import",
                message=f"import cycle: {chain}",
                severity="medium",
                confidence="high",
                primary_location=spans[0],
                related_locations=spans[1:],
                provider=PROVIDER_NAME,
                component_id=request.component_id or None,
                task_id=request.task_id,
                evidence=EvidenceLevel.MEASURED,
            )
        )
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=(Measurement(name="cycles_found", value=len(findings), unit="cycles"),),
    )


def _measure_cpp(request: CycleRequest) -> Observation:
    root = request.project_root
    graph, _known, diagnostics, _resolved = _build_cpp_graph(root, all_files=list(request.files))
    findings: list[Finding] = []
    for members in _find_cycles_tarjan(graph):
        spans = tuple(SourceSpan(path=_relative(path, root), start_line=1) for path in members)
        chain = " -> ".join(_relative(path, root) for path in (*members, members[0]))
        findings.append(
            Finding(
                fingerprint=_fingerprint(request.language, [str(path) for path in members]),
                rule_id="cycle.include",
                message=f"include cycle: {chain}",
                severity="medium",
                confidence="low",
                primary_location=spans[0],
                related_locations=spans[1:],
                provider=PROVIDER_NAME,
                component_id=request.component_id or None,
                task_id=request.task_id,
                evidence=EvidenceLevel.ESTIMATED,
            )
        )
    limitations = [
        "include resolution is a path-suffix heuristic — compiler search "
        "order and generated headers are not modelled",
        *(
            f"{diagnostic.kind} include {diagnostic.include!r} at "
            f"{_relative(diagnostic.source, root)}:{diagnostic.line}"
            for diagnostic in diagnostics
        ),
    ]
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=(
            Measurement(
                name="cycles_found",
                value=len(findings),
                unit="cycles",
                evidence=EvidenceLevel.ESTIMATED,
            ),
        ),
        limitations=tuple(limitations),
    )


def _fingerprint(language: str, members: list[str]) -> str:
    digest = hashlib.sha1(f"{language}:{':'.join(members)}".encode()).hexdigest()[:16]
    return f"cycle-{digest}"


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
