"""Central output-boundary redaction for reports and returned verification results."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any

from ici.core.models import (
    BaselineComparison,
    EngineResult,
    Finding,
    FindingDelta,
    FindingMetric,
    FindingSuppression,
    SourceLocation,
    SupportMatrix,
    VerificationSuiteResult,
)
from ici.core.redaction_values import REDACTED, redact_data, redact_text

_SECRET_FLAGS = {
    "--api-key",
    "--api_key",
    "--password",
    "--passwd",
    "--secret",
    "--token",
}
_SECRET_DEFINE_RE = re.compile(
    r"(?i)(?:password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)"
)
_COMPILE_PATH_FLAGS = frozenset({"-I", "-isystem", "-iquote", "--sysroot", "-isysroot", "-o"})
_COMPILE_PATH_PREFIXES = ("--sysroot=", "-isystem", "-iquote", "-isysroot", "-I", "-o")


def _redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for value in argv:
        if hide_next:
            redacted.append(REDACTED)
            hide_next = False
            continue
        redacted_value = redact_text(value)
        redacted.append(redacted_value)
        hide_next = value.casefold() in _SECRET_FLAGS
    return redacted


def _redact_compilation_path(value: str, project_root: Path) -> str:
    if not value:
        return value
    path = Path(value)
    if not path.is_absolute() and not PureWindowsPath(value).is_absolute():
        return redact_text(value)
    if PureWindowsPath(value).is_absolute() and not path.is_absolute():
        return "[external]"
    try:
        return path.resolve(strict=False).relative_to(project_root).as_posix() or "."
    except (OSError, RuntimeError, ValueError):
        return "[external]"


def _redact_compilation_argv(argv: tuple[str, ...], project_root: Path) -> tuple[str, ...]:
    redacted: list[str] = []
    hide_next = False
    path_next = False
    for value in argv:
        if hide_next:
            redacted.append(REDACTED)
            hide_next = False
            continue
        if path_next:
            redacted.append(_redact_compilation_path(value, project_root))
            path_next = False
            continue
        safe = redact_text(value)
        if value.casefold() in _SECRET_FLAGS:
            redacted.append(safe)
            hide_next = True
            continue
        if value in _COMPILE_PATH_FLAGS:
            redacted.append(safe)
            path_next = True
            continue
        prefix = next(
            (
                item
                for item in _COMPILE_PATH_PREFIXES
                if value.startswith(item) and len(value) > len(item)
            ),
            None,
        )
        if prefix is not None:
            redacted.append(prefix + _redact_compilation_path(value[len(prefix) :], project_root))
            continue
        redacted.append(_redact_compilation_path(safe, project_root))
    return tuple(redacted)


def _redact_compilation_diagnostic(diagnostic: Any) -> Any:
    return replace(
        diagnostic,
        code=redact_text(diagnostic.code),
        message=redact_text(diagnostic.message),
        source=redact_text(diagnostic.source),
    )


def _redact_compilation_unit(unit: Any, project_root: Path) -> Any:
    definitions = tuple(
        replace(
            item,
            name=redact_text(item.name),
            value=(
                REDACTED
                if item.value is not None and _SECRET_DEFINE_RE.search(item.name)
                else redact_text(item.value)
                if item.value is not None
                else None
            ),
        )
        for item in unit.defines
    )
    include_paths = tuple(
        replace(item, path=_redact_compilation_path(item.path, project_root))
        for item in unit.include_paths
    )
    return replace(
        unit,
        source=_redact_compilation_path(unit.source, project_root),
        directory=_redact_compilation_path(unit.directory, project_root),
        argv=_redact_compilation_argv(unit.argv, project_root),
        output=_redact_compilation_path(unit.output, project_root),
        compiler=redact_text(unit.compiler),
        standard=redact_text(unit.standard),
        defines=definitions,
        include_paths=include_paths,
        sysroot=_redact_compilation_path(unit.sysroot, project_root),
        diagnostics=tuple(_redact_compilation_diagnostic(item) for item in unit.diagnostics),
    )


def _redact_analysis_context(context: Any) -> Any:
    if context is None:
        return None
    project_root = context.project.root
    project = replace(
        context.project,
        cpp_include_flags=_redact_compilation_argv(
            tuple(context.project.cpp_include_flags), project_root
        ),
    )
    compilation = replace(
        context.compilation,
        database_path=(
            _redact_compilation_path(context.compilation.database_path, project_root)
            if context.compilation.database_path is not None
            else None
        ),
        units=tuple(
            _redact_compilation_unit(unit, project_root) for unit in context.compilation.units
        ),
        diagnostics=tuple(
            _redact_compilation_diagnostic(item) for item in context.compilation.diagnostics
        ),
    )
    return replace(context, project=project, compilation=compilation)


def _redact_location(location: SourceLocation) -> SourceLocation:
    return replace(
        location,
        path=redact_text(location.path),
        label=redact_text(location.label),
    )


def _redact_finding_metrics(metrics: dict[str, FindingMetric]) -> dict[str, FindingMetric]:
    redacted: dict[str, FindingMetric] = {}
    for name, metric in metrics.items():
        base_name = redact_text(name)
        safe_name = base_name
        suffix = 2
        while safe_name in redacted:
            safe_name = f"{base_name}#{suffix}"
            suffix += 1
        redacted[safe_name] = replace(metric, unit=redact_text(metric.unit))
    return redacted


def _redact_finding(finding: Finding) -> Finding:
    suppression = FindingSuppression(
        suppressed=finding.suppression.suppressed,
        kind=finding.suppression.kind,
        reason=redact_text(finding.suppression.reason),
    )
    return replace(
        finding,
        primary_location=_redact_location(finding.primary_location),
        related_locations=[_redact_location(item) for item in finding.related_locations],
        message=redact_text(finding.message),
        explanation=redact_text(finding.explanation),
        remediation=redact_text(finding.remediation),
        tool_rule_id=redact_text(finding.tool_rule_id),
        tool_name=redact_text(finding.tool_name),
        tool_version=redact_text(finding.tool_version),
        snippet=redact_text(finding.snippet),
        suppression=suppression,
        metrics=_redact_finding_metrics(finding.metrics),
    )


def _redact_support_matrix(matrix: SupportMatrix | None) -> SupportMatrix | None:
    if matrix is None:
        return None
    entries = [
        replace(
            entry,
            engine_name=redact_text(entry.engine_name),
            frameworks=[redact_text(value) for value in entry.frameworks],
            required_tools=[redact_text(value) for value in entry.required_tools],
            optional_tools=[redact_text(value) for value in entry.optional_tools],
            limitations=[redact_text(value) for value in entry.limitations],
            reason=redact_text(entry.reason),
        )
        for entry in matrix.entries
    ]
    return replace(
        matrix,
        project_frameworks=[redact_text(value) for value in matrix.project_frameworks],
        entries=entries,
    )


def _redact_capability_inventory(inventory: Any) -> Any:
    """Redact an immutable capability snapshot without importing it eagerly.

    Capabilities depend on the shared redaction primitives, so using dataclass
    ``replace`` here avoids a circular module import while preserving the
    inventory's concrete type and immutable mapping contract.
    """

    if inventory is None:
        return None
    capabilities = {}
    requirements = {}
    for original_name, capability in inventory.capabilities.items():
        base_name = redact_text(original_name)
        safe_name = base_name
        suffix = 2
        while safe_name in capabilities:
            safe_name = f"{base_name}#{suffix}"
            suffix += 1
        capabilities[safe_name] = replace(
            capability,
            name=safe_name,
            path=redact_text(capability.path),
            version=redact_text(capability.version),
            error=redact_text(capability.error),
            details=redact_data(dict(capability.details)),
            probe_argv=tuple(_redact_argv(list(capability.probe_argv))),
            evidence=tuple(
                replace(
                    item,
                    purpose=redact_text(item.purpose),
                    argv=tuple(_redact_argv(list(item.argv))),
                )
                for item in capability.evidence
            ),
        )
        requirement = inventory.requirements[original_name]
        requirements[safe_name] = replace(
            requirement,
            name=safe_name,
            required_by=tuple(redact_text(value) for value in requirement.required_by),
            optional_by=tuple(redact_text(value) for value in requirement.optional_by),
        )
    safe = replace(inventory, capabilities=capabilities, requirements=requirements)
    return inventory if safe == inventory else safe


def _redact_delta(delta: FindingDelta) -> FindingDelta:
    return replace(
        delta,
        engine_name=redact_text(delta.engine_name),
        rule_id=redact_text(delta.rule_id),
        message=redact_text(delta.message),
        current_location=(
            _redact_location(delta.current_location) if delta.current_location is not None else None
        ),
        baseline_location=(
            _redact_location(delta.baseline_location)
            if delta.baseline_location is not None
            else None
        ),
    )


def _redact_baseline(
    comparison: BaselineComparison | None,
) -> BaselineComparison | None:
    if comparison is None:
        return None
    return replace(
        comparison,
        source_path=redact_text(comparison.source_path),
        warnings=[redact_text(warning) for warning in comparison.warnings],
        entries=[_redact_delta(entry) for entry in comparison.entries],
        baseline_metadata=(
            replace(
                comparison.baseline_metadata,
                producer_version=redact_text(comparison.baseline_metadata.producer_version),
                fingerprint_version=redact_text(comparison.baseline_metadata.fingerprint_version),
                policy_digest=redact_text(comparison.baseline_metadata.policy_digest),
                tool_policy_digest=redact_text(comparison.baseline_metadata.tool_policy_digest),
            )
            if comparison.baseline_metadata is not None
            else None
        ),
    )


def redact_engine_result(result: EngineResult) -> EngineResult:
    """Return a reporting-safe copy of an engine result."""

    targets = [
        replace(
            target,
            file_path=redact_text(target.file_path),
            target_name=redact_text(target.target_name),
            message=redact_text(target.message),
            snippet=redact_text(target.snippet),
            metrics=redact_data(target.metrics),
        )
        for target in result.targets
    ]
    tools = [
        replace(
            tool,
            name=redact_text(tool.name),
            path=redact_text(tool.path),
            version=redact_text(tool.version),
            argv=_redact_argv(tool.argv),
            error=redact_text(tool.error),
        )
        for tool in result.tool_evidence
    ]
    return replace(
        result,
        summary=redact_text(result.summary),
        targets=targets,
        raw_output=redact_text(result.raw_output),
        extra=redact_data(result.extra),
        tool_evidence=tools,
        findings=[_redact_finding(finding) for finding in result.findings],
        support_matrix=_redact_support_matrix(result.support_matrix),
    )


def redact_suite(suite: VerificationSuiteResult) -> VerificationSuiteResult:
    """Return a reporting-safe suite shared by every output format."""

    return replace(
        suite,
        results=[redact_engine_result(result) for result in suite.results],
        support_matrix=_redact_support_matrix(suite.support_matrix),
        baseline_comparison=_redact_baseline(suite.baseline_comparison),
        capability_inventory=_redact_capability_inventory(suite.capability_inventory),
        analysis_context=_redact_analysis_context(suite.analysis_context),
    )
