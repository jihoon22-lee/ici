"""Static Python compatibility — the syntax/stdlib floor scan, in-process.

``python.compat`` is ici's own measurement: each owned source is parsed and
checked against the floor the project's ``requires-python`` declares, using
the same ``analyze_static_compatibility`` the stable ``python_compat``
engine calls per file (#220 item 5).

The scan answers exactly what it can: whether the *text* fits the declared
floor. What it cannot answer is kept separate — a file the AST cannot parse
is a limitation, not a pass, and a runtime the interpreter itself would
reject is the sibling check ``python.compat-runtime``'s evidence, not this
one's.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.engines._python_compatibility import (
    MAX_COMPAT_TOTAL_AST_NODES,
    PythonMetadataError,
    PythonProjectMetadata,
    analyze_static_compatibility,
    inferred_target_version,
    load_python_metadata,
)
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources

__all__ = ["PROVIDER_NAME", "CompatRequest", "declared_python_floor", "measure_python_compat"]

PROVIDER_NAME = "ici.compat"

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass(frozen=True)
class CompatRequest:
    """The Python files the static floor scan reads.

    ``project_root`` is the component root — it anchors the project-relative
    paths findings carry, the same convention the other in-process checks
    use. ``workspace_root`` is where a component without its own
    ``pyproject.toml`` still inherits the workspace's declared floor.
    """

    project_root: Path
    files: tuple[Path, ...]
    task_id: str
    component_id: str = ""
    workspace_root: Path | None = None


def declared_python_floor(component_root: Path, workspace_root: Path) -> str:
    """The ``requires-python`` specifier this component answers to.

    The component's own ``pyproject.toml`` wins; a component without one
    inherits the workspace's. An absent floor returns ``""`` — the caller
    records that the floor was never asserted — while an unreadable or
    invalid one raises :class:`PythonMetadataError`, because a declaration
    that cannot be trusted is not the same fact as no declaration.
    """

    metadata = load_python_metadata(component_root, [])
    if not metadata.pyproject_present and component_root != workspace_root:
        metadata = load_python_metadata(workspace_root, [])
    return metadata.requires_python


def _metadata(request: CompatRequest, source_paths: list[Path]) -> PythonProjectMetadata:
    metadata = load_python_metadata(request.project_root, source_paths)
    workspace = request.workspace_root
    if (
        not metadata.pyproject_present
        and workspace is not None
        and workspace != request.project_root
    ):
        metadata = load_python_metadata(workspace, source_paths)
    return metadata


def measure_python_compat(request: CompatRequest) -> Observation:
    """Scan every owned Python file against the declared syntax/API floor."""

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
    ast_nodes = 0
    source_paths = [request.project_root / source.file_path for source in inventory.sources]
    try:
        metadata = _metadata(request, source_paths)
        target = inferred_target_version(metadata.requires_python)
    except PythonMetadataError as error:
        return Observation(
            task_id=request.task_id,
            provider=PROVIDER_NAME,
            state=TaskState.FAILED,
            limitations=(*limitations, f"python metadata unreadable: {error}"),
        )
    if target is None:
        limitations.append(
            "no requires-python floor could be inferred — the syntax and "
            "standard-library floors were not asserted"
        )
    else:
        limitations.append(
            f"floor asserted: Python {target[0]}.{target[1]} "
            f"(from requires-python {metadata.requires_python!r})"
        )
    limitations.append(
        "static API rules cover a documented standard-library compatibility "
        "inventory — absence of a finding is not proof of compatibility"
    )
    for source in inventory.sources:
        try:
            analysis = analyze_static_compatibility(source.file_path, source.text, target)
        except SyntaxError as error:
            limitations.append(
                f"{source.file_path}:{error.lineno or 1}: the running interpreter "
                "cannot parse this source — it was not checked"
            )
            continue
        except PythonMetadataError as error:
            limitations.append(f"{source.file_path}: {error}")
            continue
        if ast_nodes + analysis.ast_nodes > MAX_COMPAT_TOTAL_AST_NODES:
            limitations.append(
                "the aggregate AST bound was reached — remaining files were not checked"
            )
            break
        ast_nodes += analysis.ast_nodes
        files_checked += 1
        findings.extend(_finding(request, source.file_path, item) for item in analysis.targets)

    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=(Measurement(name="files_checked", value=files_checked, unit="files"),),
        limitations=tuple(limitations),
    )


def _finding(request: CompatRequest, path: str, target) -> Finding:
    """One ``Compatibility:*`` target from the shared scan, normalized."""

    rule = target.target_name.split(":", 1)[-1] or target.target_name
    slug = _CAMEL_RE.sub("-", rule).lower().replace("_", "-")
    digest = hashlib.sha1(
        f"{path}:{target.start_line}:{rule}:{target.message}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return Finding(
        fingerprint=f"compat-{digest}",
        rule_id=f"python.compat.{slug}",
        native_rule_id=target.target_name,
        message=target.message or rule,
        severity="medium",
        confidence="high",
        category="compatibility",
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
