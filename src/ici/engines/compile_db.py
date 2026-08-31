"""Compilation-database coverage and policy validation engine."""

from __future__ import annotations

import time
from collections import defaultdict

from ici.core.context import CompilationDiagnostic, CompilationUnit
from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.engines.base import BaseEngine

_FAIL_DIAGNOSTICS = frozenset(
    {
        "database-changed",
        "database-malformed",
        "database-missing",
        "database-not-array",
        "database-not-file",
        "database-outside-project",
        "database-too-large",
        "database-too-many-entries",
        "database-unreadable",
        "directory-outside-project",
        "foreign-path-syntax",
        "invalid-arguments",
        "invalid-command",
        "invalid-database-setting",
        "invalid-entry",
        "invalid-entry-value",
        "invalid-file",
        "invalid-path",
        "invalid-source-path",
        "missing-command",
        "missing-directory",
        "missing-file",
        "missing-flag-value",
        "missing-include-dir",
        "response-file-changed",
        "response-file-cycle",
        "response-file-depth",
        "response-file-invalid",
        "response-file-malformed",
        "response-file-missing",
        "response-file-not-file",
        "response-file-outside-project",
        "response-file-too-large",
        "response-file-unreadable",
        "source-mismatch",
        "source-outside-project",
        "stale-source",
    }
)


def _diagnostic_status(diagnostic: CompilationDiagnostic) -> EngineStatus:
    if diagnostic.level == "error" or diagnostic.code in _FAIL_DIAGNOSTICS:
        return EngineStatus.FAIL
    if diagnostic.level == "warning":
        return EngineStatus.WARN
    return EngineStatus.PASS


def _target(
    path: str,
    rule: str,
    status: EngineStatus,
    message: str,
    *,
    metrics: dict[str, int | float] | None = None,
) -> InspectionTarget:
    return InspectionTarget(
        file_path=path,
        start_line=1,
        end_line=1,
        target_name=f"ici.compile-db.{rule}",
        status=status,
        message=message,
        metrics=metrics or {},
    )


def _diagnostic_target(
    diagnostic: CompilationDiagnostic,
    *,
    fallback_path: str,
) -> InspectionTarget:
    metrics: dict[str, int | float] = (
        {"entry_index": diagnostic.entry_index} if diagnostic.entry_index is not None else {}
    )
    return _target(
        diagnostic.source or fallback_path,
        diagnostic.code,
        _diagnostic_status(diagnostic),
        diagnostic.message,
        metrics=metrics,
    )


def _configuration_targets(
    unit: CompilationUnit,
    required_flags: tuple[str, ...],
    forbidden_flags: tuple[str, ...],
) -> list[InspectionTarget]:
    targets = [
        _target(
            unit.source,
            "configuration",
            EngineStatus.PASS,
            "Compilation configuration was inspected.",
        )
    ]
    for diagnostic in unit.diagnostics:
        targets.append(_diagnostic_target(diagnostic, fallback_path=unit.source))
    argv = frozenset(unit.argv)
    targets.extend(
        _target(
            unit.source,
            "required-flag",
            EngineStatus.FAIL,
            "A required compiler flag is absent from this configuration.",
        )
        for flag in required_flags
        if flag not in argv
    )
    targets.extend(
        _target(
            unit.source,
            "forbidden-flag",
            EngineStatus.FAIL,
            "A forbidden compiler flag is present in this configuration.",
        )
        for flag in forbidden_flags
        if flag in argv
    )
    return targets


class CompileDatabaseEngine(BaseEngine):
    """Validate exact translation-unit coverage and compiler policy evidence."""

    def run(self) -> EngineResult:
        started = time.time()
        cfg = self.get_config("compile_db")
        required = bool(cfg.get("required", True))
        mode = str(cfg.get("mode", "pass_warn_fail"))
        database_required = bool(cfg.get("database_required", False))
        required_flags = tuple(cfg.get("required_flags", ()))
        forbidden_flags = tuple(cfg.get("forbidden_flags", ()))

        if self.analysis_context is None:
            return self.create_result(
                name="compile_db",
                status=EngineStatus.ERROR,
                summary="Shared analysis context is unavailable.",
                duration=time.time() - started,
                required=required,
                evidence=EvidenceState.NOT_RUN,
            )

        production = tuple(sorted(self.analysis_context.project.compilable_cpp_sources))
        if not production:
            return self.create_result(
                name="compile_db",
                status=EngineStatus.SKIP,
                summary="No production C/C++ translation units are in scope.",
                duration=time.time() - started,
                required=required,
                evidence=EvidenceState.NOT_APPLICABLE,
            )

        compilation = self.analysis_context.compilation
        targets: list[InspectionTarget] = []
        if compilation.database_path is None:
            missing_status = EngineStatus.FAIL if database_required else EngineStatus.WARN
            targets.extend(
                _target(
                    source,
                    "coverage",
                    missing_status,
                    "No compilation database is available for this production translation unit.",
                )
                for source in production
            )
            return self._result(
                targets,
                production_count=len(production),
                covered_count=0,
                configuration_count=0,
                database_path=None,
                required=required,
                mode=mode,
                evidence=EvidenceState.ESTIMATED,
                started=started,
            )

        by_source: dict[str, list[CompilationUnit]] = defaultdict(list)
        for unit in compilation.units:
            by_source[unit.source].append(unit)

        for source in production:
            covered = bool(by_source.get(source))
            targets.append(
                _target(
                    source,
                    "coverage",
                    EngineStatus.PASS if covered else EngineStatus.FAIL,
                    (
                        "Production translation unit is covered by the compilation database."
                        if covered
                        else "Production translation unit is missing from the compilation database."
                    ),
                    metrics={"configurations": len(by_source.get(source, ()))},
                )
            )

        fallback = compilation.database_path or production[0]
        targets.extend(
            _diagnostic_target(diagnostic, fallback_path=fallback)
            for diagnostic in compilation.diagnostics
        )
        for unit in compilation.units:
            targets.extend(_configuration_targets(unit, required_flags, forbidden_flags))

        return self._result(
            targets,
            production_count=len(production),
            covered_count=sum(source in by_source for source in production),
            configuration_count=len(compilation.units),
            database_path=compilation.database_path,
            required=required,
            mode=mode,
            evidence=EvidenceState.MEASURED,
            started=started,
        )

    def _result(
        self,
        targets: list[InspectionTarget],
        *,
        production_count: int,
        covered_count: int,
        configuration_count: int,
        database_path: str | None,
        required: bool,
        mode: str,
        evidence: EvidenceState,
        started: float,
    ) -> EngineResult:
        failures = sum(target.status is EngineStatus.FAIL for target in targets)
        warnings = sum(target.status is EngineStatus.WARN for target in targets)
        status = self.evaluate_status(bool(failures), bool(warnings), mode)
        coverage = 100.0 * covered_count / production_count if production_count else 100.0
        summary = (
            f"Compilation DB: {covered_count}/{production_count} production units covered, "
            f"{configuration_count} configurations, {failures} failures, {warnings} warnings."
        )
        return self.create_result(
            name="compile_db",
            status=status,
            summary=summary,
            score=coverage,
            max_score=100.0,
            duration=time.time() - started,
            targets=targets,
            extra={
                "database_path": database_path,
                "production_units": production_count,
                "covered_units": covered_count,
                "configurations": configuration_count,
                "issues_count": failures + warnings,
                "coverage_percent": coverage,
            },
            required=required,
            evidence=evidence,
        )
