"""7. Dead code and unused symbol detection engine."""

from __future__ import annotations

import ast
import math
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
)
from ici.core.runner import run_process
from ici.engines._cpp_linker_dead_symbols import (
    CppLinkerDeadOutcome,
    run_cpp_linker_dead_symbols,
)
from ici.engines._cpp_unused_functions import (
    CppUnusedFunctionOutcome,
    run_cpp_unused_functions,
)
from ici.engines._source_inputs import (
    AnalysisSource,
    AnalysisSourceError,
    AnalysisSourceInventory,
    read_analysis_sources,
)
from ici.engines.base import BaseEngine

if TYPE_CHECKING:
    from ici.core.context import AnalysisContext


class DeadCodeEngine(BaseEngine):
    """Detect Python heuristics and exact C/C++ TU-local unused functions."""

    ANALYSIS_CONTEXT_ENGINES = frozenset({"dead"})
    # Exact compiler output depends on external/generated include state that
    # the v3 analysis cache key does not yet model as a complete dependency
    # closure. Never reuse this mixed engine result until that identity exists.
    CACHE_REUSE_SAFE = False
    CACHE_IMPLEMENTATION_MODULES = (
        "ici.core._cpp_replay_policy",
        "ici.core.cpp_replay",
        "ici.engines._cpp_diagnostics",
        "ici.engines._cpp_linker_dead_symbols",
        "ici.engines._cpp_tooling",
        "ici.engines._cpp_unused_functions",
        "ici.engines._source_inputs",
    )

    def __init__(
        self,
        project_root: Path | None = None,
        config: dict[str, Any] | None = None,
        analysis_context: AnalysisContext | None = None,
    ) -> None:
        super().__init__(project_root, config, analysis_context)
        self._analysis_errors: list[str] = []

    @classmethod
    def standalone_analysis_context_engines(
        cls,
        config: dict[str, Any],
    ) -> frozenset[str]:
        """Avoid build-context preparation when the standalone C++ scope is off."""

        dead = config.get("engines", {}).get("dead", {})
        unused_policy = str(dead.get("cpp_unused", "auto"))
        linker_policy = str(dead.get("cpp_linker", "off"))
        return (
            frozenset()
            if unused_policy == "off" and linker_policy == "off"
            else cls.ANALYSIS_CONTEXT_ENGINES
        )

    def _collect_sources(
        self,
        cfg: dict[str, Any],
        cpp_enabled: bool,
        targets: list[InspectionTarget],
    ) -> tuple[AnalysisSourceInventory, list[Path], list[Path], bool]:
        """Discover scopes and read only the source bytes selected by policy."""

        python_candidates = self.project_python_sources()
        cpp_candidates = [
            *self.project_cpp_sources(),
            *(self.project_cpp_headers() or []),
            *self.project_compilable_cpp_sources(),
        ]
        cpp_scope_present = bool(cpp_candidates)
        inventory = AnalysisSourceInventory((), (), 0)
        if not python_candidates and not cpp_candidates:
            return inventory, python_candidates, cpp_candidates, cpp_scope_present
        ordered_python = self._ordered_python_sources(self.project_source_dirs(), python_candidates)
        analysis_candidates = (
            (*ordered_python, *cpp_candidates) if cpp_enabled else tuple(ordered_python)
        )
        try:
            inventory = read_analysis_sources(
                self.project_root,
                analysis_candidates,
                include_generated=cfg.get("include_generated") is True,
                include_vendor=cfg.get("include_vendor") is True,
            )
        except AnalysisSourceError as err:
            self._analysis_errors.append(err.message)
            targets.append(
                InspectionTarget(
                    file_path=err.file_path,
                    start_line=1,
                    target_name="SourceInputError",
                    status=EngineStatus.ERROR,
                    message=f"{err.code}: {err.message}",
                )
            )
        return inventory, python_candidates, cpp_candidates, cpp_scope_present

    @staticmethod
    def _unavailable_cpp_target(
        outcome: CppUnusedFunctionOutcome,
        cpp_policy: str,
        source: str,
    ) -> tuple[InspectionTarget, str]:
        message = (
            outcome.warnings[0]
            if outcome.warnings
            else "Exact C++ unused-function analysis is unavailable"
        )
        required = cpp_policy == "required"
        return (
            InspectionTarget(
                file_path=source,
                start_line=1,
                target_name="C++UnusedFunctionsUnavailable",
                status=EngineStatus.ERROR if required else EngineStatus.SKIP,
                message=message,
            ),
            message if required else "",
        )

    def _analyze_cpp_scope(
        self,
        cpp_policy: str,
        cpp_scope_present: bool,
        cpp_sources: tuple[AnalysisSource, ...],
        targets: list[InspectionTarget],
    ) -> tuple[CppUnusedFunctionOutcome, list[InspectionTarget]]:
        """Evaluate policy and attach one atomic C++ compiler outcome."""

        outcome = CppUnusedFunctionOutcome(mode="not-applicable")
        if cpp_policy == "off":
            if not cpp_scope_present:
                return outcome, []
            outcome = CppUnusedFunctionOutcome(
                targets=[
                    InspectionTarget(
                        file_path=".",
                        start_line=1,
                        target_name="C++UnusedFunctionsDisabled",
                        status=EngineStatus.SKIP,
                        message="Compiler-backed C++ unused-function analysis is disabled",
                    )
                ],
                mode="off",
            )
            cpp_targets = list(outcome.targets)
            targets.extend(cpp_targets)
            return outcome, cpp_targets
        if not cpp_sources or self._analysis_errors:
            return outcome, []
        outcome = self._run_cpp_scope(cpp_policy, cpp_sources)
        if outcome.mode == "error":
            self._analysis_errors.extend(outcome.errors)
        if outcome.mode == "unavailable":
            unavailable_target, required_error = self._unavailable_cpp_target(
                outcome,
                cpp_policy,
                cpp_sources[0].file_path,
            )
            outcome.targets.append(unavailable_target)
            if required_error:
                self._analysis_errors.append(required_error)
        cpp_targets = list(outcome.targets)
        targets.extend(cpp_targets)
        return outcome, cpp_targets

    def _analyze_linker_scope(
        self,
        policy: str,
        cpp_scope_present: bool,
        cpp_sources: tuple[AnalysisSource, ...],
        targets: list[InspectionTarget],
    ) -> tuple[CppLinkerDeadOutcome, list[InspectionTarget]]:
        """Evaluate the independent target-local GNU ELF reachability policy."""

        if policy == "off":
            return CppLinkerDeadOutcome(mode="off"), []
        if not cpp_sources or self._analysis_errors:
            return CppLinkerDeadOutcome(mode="not-applicable"), []
        outcome = run_cpp_linker_dead_symbols(
            self.project_root,
            self.analysis_context,
            source_texts={source.file_path: source.text for source in cpp_sources},
            policy=policy,
            runner=run_process,
        )
        if outcome.mode == "error":
            self._analysis_errors.extend(outcome.errors)
        elif outcome.mode == "unavailable":
            message = (
                outcome.warnings[0]
                if outcome.warnings
                else "Exact GNU ELF target-local reachability is unavailable"
            )
            outcome.targets.append(
                InspectionTarget(
                    file_path=cpp_sources[0].file_path,
                    start_line=1,
                    target_name="C++LinkerReachabilityUnavailable",
                    status=EngineStatus.ERROR if policy == "required" else EngineStatus.SKIP,
                    message=message,
                )
            )
            if policy == "required":
                self._analysis_errors.append(message)
        linker_targets = list(outcome.targets)
        targets.extend(linker_targets)
        return outcome, linker_targets

    def _analyze_python_scope(
        self,
        inventory: AnalysisSourceInventory,
        python_candidates: list[Path],
        targets: list[InspectionTarget],
    ) -> tuple[tuple[AnalysisSource, ...], list[InspectionTarget], EvidenceState]:
        """Run the bounded Python heuristic independently of the C++ scope."""

        sources = tuple(source for source in inventory.sources if source.language == "python")
        python_targets: list[InspectionTarget] = []
        if sources and not self._analysis_errors:
            python_targets = self._detect_python_dead_code(sources)
            targets.extend(python_targets)
        return sources, python_targets, self._python_evidence(sources, python_candidates)

    def _append_empty_scope_target(
        self,
        inventory: AnalysisSourceInventory,
        cpp_outcome: CppUnusedFunctionOutcome,
        linker_outcome: CppLinkerDeadOutcome,
        targets: list[InspectionTarget],
    ) -> None:
        """Explain why no source bytes were analyzed when that is not policy-off."""

        if (
            inventory.sources
            or self._analysis_errors
            or (cpp_outcome.mode == "off" and linker_outcome.mode == "off")
        ):
            return
        message = (
            "All selected sources were excluded by the generated/vendor policy; "
            "dead-code analysis was not run"
            if inventory.excluded
            else "No applicable source files were selected; dead-code analysis was not run"
        )
        targets.append(
            InspectionTarget(
                file_path=".",
                start_line=1,
                target_name="DeadCode",
                status=EngineStatus.SKIP,
                message=message,
            )
        )

    @staticmethod
    def _analysis_provenance(
        python_sources: tuple[AnalysisSource, ...],
        cpp_outcome: CppUnusedFunctionOutcome,
        linker_outcome: CppLinkerDeadOutcome,
    ) -> str:
        names = []
        if python_sources:
            names.append("python-ast-heuristic")
        if cpp_outcome.mode == "exact":
            names.append("cpp-compiler-unused-function")
        if linker_outcome.mode == "exact":
            names.append("cpp-gnu-elf-section-gc")
        return "+".join(names) or "not-run"

    @staticmethod
    def _cpp_details(cpp_outcome: CppUnusedFunctionOutcome) -> list[dict[str, Any]]:
        return [
            {
                "file_path": item.target.file_path,
                "start_line": item.target.start_line,
                "end_line": item.target.end_line,
                "start_column": item.target.start_column,
                "end_column": item.target.end_column,
                "tool_rule_id": "-Wunused-function",
                "configurations": list(item.configurations),
                "tool_names": list(item.tool_names),
                "tool_versions": list(item.tool_versions),
                "diagnostic_message": item.diagnostic_message,
            }
            for item in cpp_outcome.functions
        ]

    @staticmethod
    def _linker_details(linker_outcome: CppLinkerDeadOutcome) -> list[dict[str, Any]]:
        return [
            {
                "file_path": item.target.file_path,
                "start_line": item.target.start_line,
                "link_target": item.link_target,
                "symbol": item.symbol,
                "section": item.section,
                "object_path": item.object_path,
                "tool_name": item.tool_name,
                "tool_version": item.tool_version,
                "link_command_digest": item.link_command_digest,
            }
            for item in linker_outcome.symbols
        ]

    @staticmethod
    def _effective_required(
        cfg: dict[str, Any],
        cpp_policy: str,
        linker_policy: str,
        cpp_scope_present: bool,
        python_sources: tuple[AnalysisSource, ...],
        cpp_outcome: CppUnusedFunctionOutcome,
        linker_outcome: CppLinkerDeadOutcome,
        analysis_failed: bool,
    ) -> bool:
        cpp_only = cpp_scope_present and not python_sources
        cpp_only_disabled = cpp_only and cpp_policy == "off" and linker_policy == "off"
        cpp_only_auto_unavailable = (
            cpp_only
            and cpp_policy != "required"
            and linker_policy != "required"
            and not analysis_failed
            and (cpp_policy == "off" or cpp_outcome.mode in {"not-applicable", "unavailable"})
            and (linker_policy == "off" or linker_outcome.mode in {"not-applicable", "unavailable"})
        )
        return bool(cfg.get("required", True)) and not (
            cpp_only_disabled or cpp_only_auto_unavailable
        )

    def _result_state(
        self,
        *,
        cfg: dict[str, Any],
        cpp_policy: str,
        linker_policy: str,
        cpp_scope_present: bool,
        python_sources: tuple[AnalysisSource, ...],
        cpp_sources: tuple[AnalysisSource, ...],
        cpp_outcome: CppUnusedFunctionOutcome,
        linker_outcome: CppLinkerDeadOutcome,
        targets: list[InspectionTarget],
        issue_count: int,
        python_issue_count: int,
        cpp_issue_count: int,
        linker_issue_count: int,
    ) -> tuple[EngineStatus, EvidenceState, str]:
        """Aggregate language-scoped evidence without upgrading partial work."""

        if self._analysis_errors:
            return EngineStatus.ERROR, EvidenceState.NOT_RUN, "; ".join(self._analysis_errors[:3])
        cpp_exact = cpp_outcome.mode == "exact" or linker_outcome.mode == "exact"
        if not python_sources and not cpp_exact:
            evidence = EvidenceState.NOT_RUN if cpp_scope_present else EvidenceState.NOT_APPLICABLE
            if cpp_policy == "off" and linker_policy == "off" and cpp_scope_present:
                summary = "C++ dead-code analysis disabled by policy"
            elif cpp_sources:
                summary = "C++ dead-code analysis not run: exact native context unavailable"
            else:
                summary = "Dead-code analysis skipped: no owned source files"
            return EngineStatus.SKIP, evidence, summary
        has_fail = any(target.status == EngineStatus.FAIL for target in targets)
        has_warn = any(target.status == EngineStatus.WARN for target in targets)
        cpp_partial_unavailable = not cpp_exact and (
            (cpp_policy != "off" and cpp_outcome.mode == "unavailable")
            or (linker_policy != "off" and linker_outcome.mode == "unavailable")
        )
        if python_sources and cpp_partial_unavailable:
            has_warn = True
        status = self.evaluate_status(has_fail, has_warn, cfg.get("mode", "pass_warn"))
        evidence = EvidenceState.ESTIMATED if python_sources else EvidenceState.MEASURED
        if status == EngineStatus.PASS:
            summary = "No dead-code findings detected in the analyzed scopes"
        elif python_sources and cpp_partial_unavailable:
            summary = "Python dead-code analysis completed, but exact C++ analysis was unavailable"
        else:
            summary = (
                f"{issue_count} dead-code finding(s): "
                f"{python_issue_count} heuristic Python, {cpp_issue_count} compiler-local C/C++, "
                f"{linker_issue_count} target-local GNU ELF"
            )
        return status, evidence, summary

    def run(self) -> EngineResult:
        t0 = time.time()
        self._analysis_errors = []
        targets: list[InspectionTarget] = []
        cfg = self.get_config("dead")
        cpp_policy = str(cfg.get("cpp_unused", "auto"))
        linker_policy = str(cfg.get("cpp_linker", "off"))
        cpp_enabled = cpp_policy != "off" or linker_policy != "off"
        inventory, python_candidates, cpp_candidates, cpp_scope_present = self._collect_sources(
            cfg,
            cpp_enabled,
            targets,
        )
        python_sources, python_targets, python_evidence = self._analyze_python_scope(
            inventory,
            python_candidates,
            targets,
        )
        cpp_sources = tuple(source for source in inventory.sources if source.language == "cpp")

        cpp_outcome, cpp_targets = self._analyze_cpp_scope(
            cpp_policy,
            cpp_scope_present,
            cpp_sources,
            targets,
        )
        linker_outcome, linker_targets = self._analyze_linker_scope(
            linker_policy,
            cpp_scope_present,
            cpp_sources,
            targets,
        )
        self._append_empty_scope_target(inventory, cpp_outcome, linker_outcome, targets)

        python_issue_count = sum(
            1
            for target in python_targets
            if target.status in (EngineStatus.WARN, EngineStatus.FAIL)
        )
        cpp_issue_count = len(cpp_outcome.functions)
        linker_issue_count = len(linker_outcome.symbols)
        issue_count = python_issue_count + cpp_issue_count + linker_issue_count
        duration = time.time() - t0
        compiler_evidence = self._cpp_evidence(
            cpp_sources,
            bool(cpp_candidates),
            cpp_outcome,
            cpp_policy,
            bool(self._analysis_errors),
            cpp_scope_present,
        )
        linker_evidence = self._linker_evidence(
            cpp_sources,
            bool(cpp_candidates),
            linker_outcome,
            linker_policy,
            bool(self._analysis_errors),
            cpp_scope_present,
        )
        cpp_evidence = (
            EvidenceState.MEASURED
            if EvidenceState.MEASURED in {compiler_evidence, linker_evidence}
            else EvidenceState.NOT_RUN
            if cpp_scope_present
            else EvidenceState.NOT_APPLICABLE
        )
        status, evidence, summary = self._result_state(
            cfg=cfg,
            cpp_policy=cpp_policy,
            linker_policy=linker_policy,
            cpp_scope_present=cpp_scope_present,
            python_sources=python_sources,
            cpp_sources=cpp_sources,
            cpp_outcome=cpp_outcome,
            linker_outcome=linker_outcome,
            targets=targets,
            issue_count=issue_count,
            python_issue_count=python_issue_count,
            cpp_issue_count=cpp_issue_count,
            linker_issue_count=linker_issue_count,
        )
        effective_required = self._effective_required(
            cfg,
            cpp_policy,
            linker_policy,
            cpp_scope_present,
            python_sources,
            cpp_outcome,
            linker_outcome,
            bool(self._analysis_errors),
        )
        result = self.create_result(
            name="dead",
            status=status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "dead_symbols_count": issue_count,
                "metrics_summary": f"{issue_count} dead symbols",
                "analysis_provenance": self._analysis_provenance(
                    python_sources,
                    cpp_outcome,
                    linker_outcome,
                ),
                "language_evidence": {
                    "python": python_evidence.value,
                    "cpp": cpp_evidence.value,
                },
                "cpp_unused_policy": cpp_policy,
                "cpp_unused_mode": cpp_outcome.mode,
                "cpp_unused_functions_count": cpp_issue_count,
                "cpp_unused_configurations_checked": cpp_outcome.configurations_checked,
                "cpp_unused_sources_checked": cpp_outcome.sources_checked,
                "cpp_unused_non_tu_diagnostics_excluded": (cpp_outcome.non_tu_diagnostics_excluded),
                "cpp_unused_warnings": cpp_outcome.warnings,
                "cpp_unused_details": self._cpp_details(cpp_outcome),
                "cpp_linker_policy": linker_policy,
                "cpp_linker_mode": linker_outcome.mode,
                "cpp_linker_symbols_count": linker_issue_count,
                "cpp_linker_targets_checked": linker_outcome.link_targets_checked,
                "cpp_linker_sources_checked": linker_outcome.sources_checked,
                "cpp_linker_discarded_sections_observed": (
                    linker_outcome.discarded_sections_observed
                ),
                "cpp_linker_ambiguous_sections_excluded": (
                    linker_outcome.ambiguous_sections_excluded
                ),
                "cpp_linker_warnings": linker_outcome.warnings,
                "cpp_linker_details": self._linker_details(linker_outcome),
                "cpp_scope_evidence": {
                    "compiler_unused": compiler_evidence.value,
                    "gnu_elf_linker": linker_evidence.value,
                },
                "source_files_analyzed": (
                    len(python_sources)
                    + max(cpp_outcome.sources_checked, linker_outcome.sources_checked)
                ),
                "source_files_snapshotted": len(inventory.sources),
                "source_bytes_analyzed": inventory.total_bytes,
                "source_files_excluded": len(inventory.excluded),
                "source_exclusion_counts": inventory.exclusion_counts,
            },
            required=effective_required,
            evidence=evidence,
            tool_evidence=[*cpp_outcome.evidence, *linker_outcome.evidence],
        )
        result.findings = self._scoped_findings(
            targets,
            python_targets,
            cpp_targets,
            linker_targets,
            cpp_outcome,
            linker_outcome,
            python_evidence,
            cpp_evidence,
        )
        return result

    def _run_cpp_scope(
        self,
        policy: str,
        sources: tuple[AnalysisSource, ...],
    ) -> CppUnusedFunctionOutcome:
        if policy == "off":
            return CppUnusedFunctionOutcome(
                targets=[
                    InspectionTarget(
                        file_path=sources[0].file_path,
                        start_line=1,
                        target_name="C++UnusedFunctionsDisabled",
                        status=EngineStatus.SKIP,
                        message="Compiler-backed C++ unused-function analysis is disabled",
                    )
                ],
                mode="off",
            )
        production_sources = {
            path.relative_to(self.project_root).as_posix() for path in self.project_cpp_sources()
        }
        cpp_files = [source.path for source in sources if source.file_path in production_sources]
        if not cpp_files:
            return CppUnusedFunctionOutcome(
                warnings=["No production C/C++ translation unit is available for analysis"],
                mode="unavailable",
            )
        return run_cpp_unused_functions(
            self.project_root,
            cpp_files,
            self.analysis_context,
            source_texts={source.file_path: source.text for source in sources},
            runner=run_process,
        )

    def _python_evidence(
        self,
        sources: tuple[AnalysisSource, ...],
        candidates: list[Path],
    ) -> EvidenceState:
        if self._analysis_errors and (sources or candidates):
            return EvidenceState.NOT_RUN
        if sources:
            return EvidenceState.ESTIMATED
        return EvidenceState.NOT_APPLICABLE

    @staticmethod
    def _cpp_evidence(
        sources: tuple[AnalysisSource, ...],
        had_candidates: bool,
        outcome: CppUnusedFunctionOutcome,
        policy: str,
        analysis_failed: bool,
        scope_present: bool,
    ) -> EvidenceState:
        if policy == "off":
            return EvidenceState.NOT_RUN if scope_present else EvidenceState.NOT_APPLICABLE
        if not sources:
            return (
                EvidenceState.NOT_RUN
                if had_candidates and analysis_failed
                else EvidenceState.NOT_APPLICABLE
            )
        if outcome.mode in {"unavailable", "error"}:
            return EvidenceState.NOT_RUN
        if outcome.mode == "exact":
            return EvidenceState.MEASURED
        return EvidenceState.NOT_RUN

    @staticmethod
    def _linker_evidence(
        sources: tuple[AnalysisSource, ...],
        had_candidates: bool,
        outcome: CppLinkerDeadOutcome,
        policy: str,
        analysis_failed: bool,
        scope_present: bool,
    ) -> EvidenceState:
        if policy == "off":
            return EvidenceState.NOT_RUN if scope_present else EvidenceState.NOT_APPLICABLE
        if not sources:
            return (
                EvidenceState.NOT_RUN
                if had_candidates and analysis_failed
                else EvidenceState.NOT_APPLICABLE
            )
        return EvidenceState.MEASURED if outcome.mode == "exact" else EvidenceState.NOT_RUN

    @staticmethod
    def _cpp_findings(
        targets: list[InspectionTarget],
        outcome: CppUnusedFunctionOutcome,
    ) -> list[Finding]:
        key_counts = Counter((target.file_path, target.target_name.strip()) for target in targets)
        findings: list[Finding] = []
        for item in outcome.functions:
            target = item.target
            key = (target.file_path, target.target_name.strip())
            label = target.target_name if key_counts[key] == 1 else ""
            findings.append(
                Finding(
                    rule_id="ici.legacy.dead.target",
                    category=FindingCategory.MAINTAINABILITY,
                    severity=FindingSeverity.MEDIUM,
                    confidence=FindingConfidence.EXACT,
                    fingerprint="",
                    primary_location=SourceLocation(
                        path=target.file_path,
                        start_line=target.start_line,
                        end_line=target.end_line,
                        start_column=target.start_column,
                        end_column=target.end_column,
                        label=label,
                    ),
                    message=target.message,
                    explanation=(
                        "The selected compiler reported -Wunused-function for this "
                        "internal-linkage definition in every exact configuration of its "
                        "production translation unit. This is not whole-program or linker "
                        "reachability evidence."
                    ),
                    remediation=(
                        "Remove the function or add a real reference in every affected "
                        "configuration; retain it explicitly with [[maybe_unused]] only when "
                        "the inactive use is intentional."
                    ),
                    tool_rule_id="-Wunused-function",
                    tool_name="+".join(item.tool_names),
                    tool_version=", ".join(item.tool_versions),
                    metrics={
                        "configurations_checked": FindingMetric(
                            value=len(item.configurations),
                            unit="configurations",
                        )
                    },
                )
            )
        return findings

    @staticmethod
    def _linker_findings(
        targets: list[InspectionTarget],
        outcome: CppLinkerDeadOutcome,
    ) -> list[Finding]:
        key_counts = Counter((target.file_path, target.target_name.strip()) for target in targets)
        findings: list[Finding] = []
        for item in outcome.symbols:
            target = item.target
            key = (target.file_path, target.target_name.strip())
            findings.append(
                Finding(
                    rule_id="ici.dead.gnu-elf-discarded-function",
                    category=FindingCategory.MAINTAINABILITY,
                    severity=FindingSeverity.MEDIUM,
                    confidence=FindingConfidence.EXACT,
                    fingerprint="",
                    primary_location=SourceLocation(
                        path=target.file_path,
                        start_line=target.start_line,
                        end_line=target.end_line,
                        label=target.target_name if key_counts[key] == 1 else "",
                    ),
                    message=target.message,
                    explanation=(
                        "GNU ld explicitly discarded this uniquely mapped local or hidden "
                        "function section while relinking the named CMake executable in the "
                        "isolated Release reachability shadow. The claim is target-local, not "
                        "project-wide behavioral unreachability."
                    ),
                    remediation=(
                        "Review the function and its target-specific call path. Remove it when "
                        "the target no longer needs it, or retain/reference it explicitly when "
                        "the discarded section is intentional."
                    ),
                    tool_rule_id="--gc-sections/--print-gc-sections",
                    tool_name=item.tool_name,
                    tool_version=item.tool_version,
                    metrics={"link_targets": FindingMetric(value=1, unit="targets")},
                )
            )
        return findings

    @classmethod
    def _scoped_findings(
        cls,
        all_targets: list[InspectionTarget],
        python_targets: list[InspectionTarget],
        cpp_targets: list[InspectionTarget],
        linker_targets: list[InspectionTarget],
        cpp_outcome: CppUnusedFunctionOutcome,
        linker_outcome: CppLinkerDeadOutcome,
        python_evidence: EvidenceState,
        cpp_evidence: EvidenceState,
    ) -> list[Finding]:
        """Keep finding confidence and tool attribution within each language scope."""

        findings = [
            *cls._cpp_findings(all_targets, cpp_outcome),
            *cls._linker_findings(all_targets, linker_outcome),
        ]
        exact_cpp_target_ids = {id(item.target) for item in cpp_outcome.functions}
        exact_linker_target_ids = {id(item.target) for item in linker_outcome.symbols}
        key_counts = Counter(
            (target.file_path, target.target_name.strip()) for target in all_targets
        )
        python_confidence = (
            FindingConfidence.MEDIUM
            if python_evidence == EvidenceState.ESTIMATED
            else FindingConfidence.LOW
        )
        cpp_confidence = (
            FindingConfidence.EXACT
            if cpp_evidence == EvidenceState.MEASURED
            else FindingConfidence.LOW
        )
        cpp_tool_names = "+".join(sorted({item.name for item in cpp_outcome.evidence if item.name}))
        cpp_tool_versions = ", ".join(
            sorted({item.version for item in cpp_outcome.evidence if item.version})
        )
        linker_tool_names = "+".join(
            sorted({item.name for item in linker_outcome.evidence if item.name})
        )
        linker_tool_versions = ", ".join(
            sorted({item.version for item in linker_outcome.evidence if item.version})
        )
        for target in python_targets:
            findings.append(
                cls._scope_finding(
                    target,
                    key_counts,
                    confidence=python_confidence,
                    explanation=(
                        "Python dead-code evidence is derived from bounded AST "
                        "reachability and name-reference analysis."
                    ),
                    remediation=(
                        "Review the reported symbol or unreachable block and either remove it "
                        "or make the intended reference explicit."
                    ),
                )
            )
        for target in cpp_targets:
            if id(target) in exact_cpp_target_ids:
                continue
            findings.append(
                cls._scope_finding(
                    target,
                    key_counts,
                    confidence=cpp_confidence,
                    explanation=(
                        "This target records the bounded TU-local compiler unused-function scope."
                    ),
                    remediation=(
                        "Restore a complete, safe compilation context when exact analysis did "
                        "not run."
                        if cpp_evidence != EvidenceState.MEASURED
                        else ""
                    ),
                    tool_name=cpp_tool_names,
                    tool_version=cpp_tool_versions,
                )
            )
        for target in linker_targets:
            if id(target) in exact_linker_target_ids:
                continue
            findings.append(
                cls._scope_finding(
                    target,
                    key_counts,
                    confidence=(
                        FindingConfidence.EXACT
                        if linker_outcome.mode == "exact"
                        else FindingConfidence.LOW
                    ),
                    explanation=(
                        "This target records the bounded target-local GNU ELF section-GC scope."
                    ),
                    remediation=(
                        "Restore the supported GNU ELF CMake Release context when exact "
                        "target-local analysis did not run."
                        if linker_outcome.mode != "exact"
                        else ""
                    ),
                    tool_name=linker_tool_names,
                    tool_version=linker_tool_versions,
                )
            )
        return findings

    @staticmethod
    def _scope_finding(
        target: InspectionTarget,
        key_counts: Counter[tuple[str, str]],
        *,
        confidence: FindingConfidence,
        explanation: str,
        remediation: str,
        tool_name: str = "",
        tool_version: str = "",
    ) -> Finding:
        key = (target.file_path, target.target_name.strip())
        label = target.target_name if key_counts[key] == 1 else ""
        severities = {
            EngineStatus.PASS: FindingSeverity.INFO,
            EngineStatus.SKIP: FindingSeverity.INFO,
            EngineStatus.WARN: FindingSeverity.MEDIUM,
            EngineStatus.FAIL: FindingSeverity.HIGH,
            EngineStatus.ERROR: FindingSeverity.CRITICAL,
        }
        metrics = {
            str(name): FindingMetric(value=value)
            for name, value in sorted(target.metrics.items())
            if not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        }
        return Finding(
            rule_id="ici.legacy.dead.target",
            category=FindingCategory.MAINTAINABILITY,
            severity=severities[target.status],
            confidence=confidence,
            fingerprint="",
            primary_location=SourceLocation(
                path=target.file_path,
                start_line=target.start_line,
                end_line=target.end_line,
                start_column=target.start_column,
                end_column=target.end_column,
                label=label,
            ),
            message=target.message,
            explanation=explanation,
            remediation=remediation,
            tool_name=tool_name,
            tool_version=tool_version,
            metrics=metrics,
            snippet=target.snippet,
        )

    def _detect_python_dead_code(
        self, sources: tuple[AnalysisSource, ...]
    ) -> list[InspectionTarget]:
        targets: list[InspectionTarget] = []
        modules: list[dict] = []
        source_dirs = self.project_source_dirs()
        module_paths: dict[str, str] = {}
        for source in sources:
            py_file = source.path
            try:
                content = source.text
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError as err:
                self._append_analysis_error(
                    targets,
                    py_file,
                    "SyntaxError",
                    f"SyntaxError: {err.msg}",
                    err.lineno or 1,
                )
                continue
            module_name = self._module_name(py_file, source_dirs)
            module_id = source.file_path
            module_paths.setdefault(module_name, module_id)
            modules.append(
                {
                    "path": py_file,
                    "content": content,
                    "tree": tree,
                    "module": module_name,
                    "module_id": module_id,
                    "defs": self._private_module_defs(tree),
                    "refs": self._load_names(tree),
                    "qualified_refs": self._qualified_refs(tree, module_name),
                    "imports": self._imports(
                        tree, module_name, is_package=py_file.name == "__init__.py"
                    ),
                    "exports": self._exports(tree),
                }
            )

        if self._analysis_errors:
            return targets
        referenced_keys: set[tuple[str, str]] = set()
        for module in modules:
            referenced_keys.update(self._resolve_imported_refs(module, module_paths))
        for module in modules:
            before = len(targets)
            self._append_module_targets(module, referenced_keys, targets)
            self._append_unreachable_targets(
                module["tree"], str(module["path"].relative_to(self.project_root)), targets
            )
            if len(targets) == before:
                targets.append(
                    InspectionTarget(
                        file_path=str(module["path"].relative_to(self.project_root)),
                        start_line=1,
                        target_name="DeadCode",
                        status=EngineStatus.PASS,
                        message="Python source was parsed and no dead-code findings were identified",
                    )
                )
        return targets

    def _ordered_python_sources(self, source_dirs: list[Path], sources: list[Path]) -> list[Path]:
        """Return source files in configured root precedence order."""

        ordered: list[Path] = []
        seen: set[Path] = set()
        for source_dir in source_dirs:
            for py_file in sources:
                if py_file in seen:
                    continue
                try:
                    py_file.relative_to(source_dir)
                except ValueError:
                    continue
                ordered.append(py_file)
                seen.add(py_file)
        for py_file in sources:
            if py_file not in seen:
                ordered.append(py_file)
        return ordered

    def _append_module_targets(
        self,
        module: dict,
        referenced_keys: set[tuple[str, str]],
        targets: list[InspectionTarget],
    ) -> None:
        local_refs: set[str] = module["refs"]
        rel_path = str(module["path"].relative_to(self.project_root))
        for name, node in module["defs"].items():
            if name in module["exports"] or getattr(node, "decorator_list", []):
                continue
            used = name in local_refs or (module["module_id"], name) in referenced_keys
            status = EngineStatus.PASS if used else EngineStatus.WARN
            message = (
                f"Private function '{name}' is referenced"
                if used
                else f"Private module-level function '{name}' is never referenced"
            )
            targets.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    target_name=f"{name}()",
                    status=status,
                    message=message,
                    snippet=ast.get_source_segment(module["content"], node) or "",
                )
            )

    @staticmethod
    def _private_module_defs(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        return {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_")
            and not node.name.startswith("__")
        }

    @staticmethod
    def _load_names(tree: ast.AST) -> set[str]:
        return {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }

    @staticmethod
    def _qualified_refs(tree: ast.AST, module_name: str) -> set[str]:
        del module_name
        refs: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            parts: list[str] = []
            current: ast.AST = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                refs.add(".".join(reversed(parts)))
        return refs

    @staticmethod
    def _exports(tree: ast.Module) -> set[str]:
        exported: set[str] = set()
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            names = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(name, ast.Name) and name.id == "__all__" for name in names):
                continue
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                exported.update(
                    item.value
                    for item in value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
        return exported

    @staticmethod
    def _imports(
        tree: ast.Module, module_name: str, *, is_package: bool = False
    ) -> dict[str, list[tuple[str, str]]]:
        imports: dict[str, list[tuple[str, str]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = DeadCodeEngine._resolve_import_module(
                    module_name, node.module, node.level, is_package=is_package
                )
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imports.setdefault(alias.asname or alias.name, []).append((base, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    imports.setdefault(local, []).append((alias.name, ""))
        return imports

    @staticmethod
    def _resolve_import_module(
        current: str, imported: str | None, level: int, *, is_package: bool = False
    ) -> str:
        if level == 0:
            return imported or ""
        package = current.split(".") if is_package else current.split(".")[:-1]
        if level > 1:
            package = package[: -(level - 1)]
        return ".".join([*package, *(imported.split(".") if imported else [])])

    @staticmethod
    def _module_name(path: Path, source_dirs: list[Path]) -> str:
        for source_dir in source_dirs:
            try:
                relative = path.relative_to(source_dir)
            except ValueError:
                continue
            parts = list(relative.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts) or path.stem
        return path.stem

    def _resolve_imported_refs(
        self, module: dict, module_paths: dict[str, str]
    ) -> set[tuple[str, str]]:
        resolved: set[tuple[str, str]] = set()
        for alias, bindings in module["imports"].items():
            if alias not in module["refs"]:
                continue
            for imported_module, imported_name in bindings:
                if imported_name:
                    candidate = f"{imported_module}.{imported_name}"
                    if candidate in module_paths:
                        target_id = module_paths[candidate]
                        for reference in module["qualified_refs"]:
                            parts = reference.split(".")
                            if parts[0] == alias and len(parts) > 1:
                                resolved.add((target_id, parts[1]))
                        if not any(
                            reference.split(".", 1)[0] == alias
                            for reference in module["qualified_refs"]
                        ):
                            resolved.add((target_id, imported_name))
                    else:
                        target_id = module_paths.get(imported_module, imported_module)
                        resolved.add((target_id, imported_name))
                    continue
                imported_parts = imported_module.split(".")
                prefix = [alias, *imported_parts[1:]] if imported_parts[0] == alias else [alias]
                for reference in module["qualified_refs"]:
                    parts = reference.split(".")
                    if parts[: len(prefix)] == prefix and len(parts) > len(prefix):
                        target_id = module_paths.get(imported_module, imported_module)
                        resolved.add((target_id, parts[len(prefix)]))
                for module_name in module_paths:
                    if module_name == imported_module or module_name.startswith(
                        imported_module + "."
                    ):
                        resolved.add((module_paths[module_name], ""))
        return resolved

    def _append_unreachable_targets(
        self, tree: ast.Module, rel_path: str, targets: list[InspectionTarget]
    ) -> None:
        seen_lists: set[int] = set()
        for node in ast.walk(tree):
            for _field, value in ast.iter_fields(node):
                if not isinstance(value, list) or id(value) in seen_lists:
                    continue
                if not value or not all(isinstance(item, ast.stmt) for item in value):
                    continue
                seen_lists.add(id(value))
                self._check_unreachable(value, rel_path, targets)

    def _append_analysis_error(
        self, targets: list[InspectionTarget], path: Path, name: str, message: str, line: int
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

    def _check_unreachable(
        self, stmts: list[ast.stmt], rel_p: str, targets: list[InspectionTarget]
    ) -> None:
        has_terminator = False
        for stmt in stmts:
            if has_terminator:
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=stmt.lineno,
                        target_name="UnreachableCode",
                        status=EngineStatus.WARN,
                        message="Unreachable code statement detected after terminal return/raise",
                    )
                )
                break
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                has_terminator = True
