"""Function-level metrics as ici's own observation — one parse, two measures.

``python.complexity``/``python.cognitive`` and their ``cpp.*`` twins answer
different questions — cyclomatic decision count versus nesting-weighted
cognitive load — but both read the same function boundaries. The file-level
scanners here return rows carrying *both* numbers, and the caller's shared
cache lets two selected checks consume one scan rather than scanning twice
(#218's shared-primitive requirement). The formulas themselves stay distinct
and are the same ones the stable engines run — the engines now delegate here
rather than keeping a second copy that means almost the same thing.

What differs between the languages is how sure the boundary is. Python
functions come from ``ast.parse`` — exact. C++ functions come from the
brace-depth scanner, which is a heuristic: preprocessed or macro-built
functions can fool it, so C++ rows carry ``heuristic=True`` and the findings
they produce are marked ``ESTIMATED`` rather than ``MEASURED`` — a heuristic
must not quietly promote itself to tool-backed evidence (#218 item 5).
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.engines._cpp_cognitive import cpp_cognitive_metric
from ici.engines._python_metrics import (
    cognitive_complexity,
    cyclomatic_complexity,
    max_nesting,
)
from ici.engines.complexity import _cpp_function_inventory

__all__ = [
    "PROVIDER_NAME",
    "FunctionMetric",
    "MetricRequest",
    "Thresholds",
    "measure",
    "scan_functions",
]

PROVIDER_NAME = "ici.metrics"

#: The policies the stable engines ship — complexity warns at 15, fails at 25,
#: cognitive warns at 30, fails at 60, both flag nesting past 4. The next path
#: has no per-check option channel yet, so the shipped defaults are the
#: contract until the quality-policy work (#219) wires one.
COMPLEXITY_THRESHOLDS = ("complexity", 15, 25, 4)
COGNITIVE_THRESHOLDS = ("cognitive", 30, 60, 4)

Thresholds = tuple[str, int, int, int]


@dataclass(frozen=True)
class FunctionMetric:
    """One function, where it is, and what it scores."""

    name: str
    path: str
    start_line: int
    end_line: int
    cyclomatic: int
    cognitive: int
    nesting: int
    #: True when the boundary came from the heuristic scanner, not a parser.
    heuristic: bool = False


@dataclass
class MetricRequest:
    """What one internal metric check measures."""

    kind: str  # "complexity" or "cognitive"
    language: str  # "python" or "cpp"
    project_root: Path
    files: tuple[Path, ...]
    task_id: str
    component_id: str = ""
    analysis_unit_id: str = ""
    #: Shared by a component's metric checks so selecting both scans once.
    cache: dict[Path, tuple[FunctionMetric, ...]] = field(default_factory=dict)


def scan_functions(path: Path, language: str, root: Path) -> tuple[FunctionMetric, ...]:
    """One scan of one file, returning every function with both measures."""

    if language == "cpp":
        return _scan_cpp(path, root)
    return _scan_python(path, root)


def _scan_python(path: Path, root: Path) -> tuple[FunctionMetric, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relative = _relative(path, root)
    rows: list[FunctionMetric] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        cognitive, cognitive_nesting = cognitive_complexity(node)
        rows.append(
            FunctionMetric(
                name=node.name,
                path=relative,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                cyclomatic=cyclomatic_complexity(node),
                cognitive=cognitive,
                nesting=max(max_nesting(node), cognitive_nesting),
            )
        )
    return tuple(rows)


def _scan_cpp(path: Path, root: Path) -> tuple[FunctionMetric, ...]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    spans, metric_lines = _cpp_function_inventory(lines)
    relative = _relative(path, root)
    rows: list[FunctionMetric] = []
    for span in spans:
        if span.end_column is None:
            continue
        body = _slice_body(
            metric_lines,
            span.body_start_line,
            span.body_start_column,
            span.end_line,
            span.end_column,
        )
        try:
            cognitive = cpp_cognitive_metric(body)
        except ValueError:
            # The scanner's span was real but the cognitive parser cannot read
            # it — report the cyclomatic figure rather than dropping the row.
            cognitive = None
        rows.append(
            FunctionMetric(
                name=span.name,
                path=relative,
                start_line=span.start_line,
                end_line=span.end_line,
                cyclomatic=span.complexity,
                cognitive=cognitive.cognitive if cognitive is not None else 0,
                nesting=max(
                    span.max_nesting, cognitive.max_nesting if cognitive is not None else 0
                ),
                heuristic=True,
            )
        )
    return tuple(rows)


def _slice_body(
    lines: list[str],
    body_start_line: int,
    body_start_column: int,
    end_line: int,
    end_column: int,
) -> str:
    """The column-precise body slice the metric parsers expect."""

    selected = lines[body_start_line - 1 : end_line]
    if body_start_line == end_line:
        selected[0] = selected[0][body_start_column - 1 : end_column]
    else:
        selected[0] = selected[0][body_start_column - 1 :]
        selected[-1] = selected[-1][:end_column]
    return "\n".join(selected)


def measure(request: MetricRequest) -> Observation:
    """Score the request's files and report over-threshold functions.

    Thresholds follow the stable engines' shipped policy; per-check options
    arrive with the quality-policy work. A file that cannot be parsed is a
    limitation, not silence — nobody measured it and the observation says so.
    """

    kind, warn, fail, warn_nesting = _thresholds(request.kind)
    findings: list[Finding] = []
    limitations: list[str] = []
    functions = 0
    peak = 0
    evidence = EvidenceLevel.MEASURED

    for path in request.files:
        rows = request.cache.get(path)
        if rows is None:
            try:
                rows = scan_functions(path, request.language, request.project_root)
            except (OSError, SyntaxError, ValueError) as error:
                limitations.append(
                    f"{_relative(path, request.project_root)}: {type(error).__name__}"
                )
                continue
            request.cache[path] = rows
        for row in rows:
            functions += 1
            if row.heuristic:
                evidence = EvidenceLevel.ESTIMATED
            value = row.cyclomatic if kind == "complexity" else row.cognitive
            peak = max(peak, value)
            finding = _finding(request, row, kind, value, warn, fail, warn_nesting)
            if finding is not None:
                findings.append(finding)

    measurements = (
        Measurement(
            name="functions_measured",
            value=functions,
            unit="functions",
            evidence=evidence,
        ),
        Measurement(name=f"max_{kind}", value=peak, unit=kind, evidence=evidence),
    )
    if request.language == "cpp":
        limitations = [
            "function boundaries from the heuristic scanner — preprocessed or "
            "macro-built functions may be missed or mis-bounded",
            *limitations,
        ]
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=measurements,
        limitations=tuple(limitations),
    )


def _thresholds(kind: str) -> Thresholds:
    if kind == "complexity":
        return COMPLEXITY_THRESHOLDS
    return COGNITIVE_THRESHOLDS


def _finding(
    request: MetricRequest,
    row: FunctionMetric,
    kind: str,
    value: int,
    warn: int,
    fail: int,
    warn_nesting: int,
) -> Finding | None:
    """One over-threshold function as a finding; under-threshold is silent."""

    over_value = value > fail
    over_warn = value > warn or row.nesting > warn_nesting
    if not over_value and not over_warn:
        return None
    evidence = EvidenceLevel.ESTIMATED if row.heuristic else EvidenceLevel.MEASURED
    if over_value:
        message = f"{row.name}: {kind} {value} exceeds fail limit {fail}"
        severity = "high"
    else:
        parts = [f"{kind} {value} exceeds warn limit {warn}"] if value > warn else []
        if row.nesting > warn_nesting:
            parts.append(f"nesting depth {row.nesting} exceeds {warn_nesting}")
        message = f"{row.name}: " + "; ".join(parts)
        severity = "medium"
    return Finding(
        fingerprint=_fingerprint(row, kind),
        rule_id=f"metrics.{kind}",
        message=message,
        severity=severity,
        confidence="low" if row.heuristic else "high",
        primary_location=SourceSpan(
            path=row.path, start_line=row.start_line, end_line=row.end_line
        ),
        provider=PROVIDER_NAME,
        component_id=request.component_id or None,
        analysis_unit_id=request.analysis_unit_id or None,
        task_id=request.task_id,
        evidence=evidence,
    )


def _fingerprint(row: FunctionMetric, kind: str) -> str:
    digest = hashlib.sha1(f"{row.path}:{row.name}:{kind}".encode()).hexdigest()[:16]
    return f"metrics-{digest}"


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
