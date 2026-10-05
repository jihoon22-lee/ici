"""Cyclic dependency detection engine — Python imports and C++ includes."""

import time

from ici.analysis.cycles import (
    _MAX_REPORTED_DEFAULT,
    _analyze_cpp_includes,
    _append_cpp_cycle_targets,
    _append_include_diagnostic_targets,
    _append_python_cycle_targets,
    _build_python_graph,
    _find_cycles_tarjan,
)
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.engines.base import BaseEngine


class CycleEngine(BaseEngine):
    """Detects cyclic dependencies in Python imports and C++ includes."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("cycle")
        mode = cfg.get("mode", "pass_warn_fail")
        max_reported = int(cfg.get("max_reported", _MAX_REPORTED_DEFAULT))

        targets: list[InspectionTarget] = []
        tool_evidence: list[ToolEvidence] = []
        cpp_errors: list[str] = []
        total_cycles = 0

        py_graph, py_modules = _build_python_graph(
            self.project_root,
            self.project_source_dirs(),
            self.project_python_sources(),
        )
        py_cycles = _find_cycles_tarjan(py_graph)
        _append_python_cycle_targets(
            self.project_root,
            py_graph,
            py_modules,
            py_cycles,
            targets,
            max_reported,
        )
        total_cycles += len(py_cycles)

        cpp_headers = self.project_cpp_headers()
        cpp_files = [*self.project_cpp_sources(), *cpp_headers] if cpp_headers is not None else None
        cpp_sources = self.project_cpp_sources()
        cpp_analysis = _analyze_cpp_includes(
            self.project_root,
            self.config,
            cpp_sources,
            cpp_files,
            self.analysis_context,
            max_reported,
        )
        targets.extend(cpp_analysis.targets)
        tool_evidence.extend(cpp_analysis.evidence)
        cpp_errors.extend(cpp_analysis.errors)
        _append_cpp_cycle_targets(
            self.project_root,
            cpp_analysis,
            targets,
            max_reported,
        )
        cpp_cycle_count = len(cpp_analysis.cycle_entries)
        total_cycles += cpp_cycle_count
        _append_include_diagnostic_targets(
            self.project_root,
            cpp_analysis.diagnostics,
            targets,
            max_reported,
        )

        has_warn = total_cycles > 0 or bool(cpp_analysis.diagnostics)
        has_warn = has_warn or cpp_analysis.unresolved > 0
        has_warn = has_warn or any(
            target.status == EngineStatus.WARN for target in cpp_analysis.targets
        )
        status = EngineStatus.ERROR if cpp_errors else self.evaluate_status(False, has_warn, mode)
        if cpp_errors:
            summary = "; ".join(cpp_errors[:3])
        elif total_cycles:
            summary = f"Dependency cycles: {total_cycles} found"
        else:
            summary = "No cyclic dependencies detected"
        if cpp_analysis.ambiguous or cpp_analysis.unresolved:
            summary += (
                "; C++ include graph incomplete: "
                f"{cpp_analysis.ambiguous} ambiguous, "
                f"{cpp_analysis.unresolved} unresolved"
            )
        duration = time.time() - t0
        return self.create_result(
            name="cycle",
            status=status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "total_cycles": total_cycles,
                "py_cycles": len(py_cycles),
                "cpp_cycles": cpp_cycle_count,
                "cpp_include_resolution": cpp_analysis.resolution,
                "resolved_cpp_includes": cpp_analysis.resolved,
                "ambiguous_cpp_includes": cpp_analysis.ambiguous,
                "unresolved_cpp_includes": cpp_analysis.unresolved,
                "cpp_include_scope_counts": cpp_analysis.scope_counts,
                "cpp_configurations_checked": cpp_analysis.configurations_checked,
                "cpp_include_diagnostics_truncated": cpp_analysis.diagnostics_truncated,
            },
            # Cycle policy is advisory by default. Exact compiler failures remain
            # visible as ERROR/NOT_RUN without silently becoming a required suite gate.
            required=bool(cfg.get("required", False)),
            evidence=(
                EvidenceState.NOT_RUN
                if cpp_errors
                else EvidenceState.MEASURED
                if cpp_analysis.exact or not cpp_sources
                else EvidenceState.ESTIMATED
            ),
            tool_evidence=tool_evidence,
        )
