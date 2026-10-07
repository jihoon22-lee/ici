"""5. Code Complexity & Nesting Depth Analysis Engine with Policy Thresholds."""

import ast
import time

from ici.analysis._cpp_function_boundaries import (
    CppFunctionBoundary,
    CppFunctionBoundaryOutcome,
    read_cpp_source_text,
    run_cpp_function_boundaries,
)
from ici.analysis._python_metrics import cyclomatic_complexity, max_nesting
from ici.analysis.cpp_complexity import (
    _MAX_CPP_COMPLEXITY_SOURCE_BYTES,
    _MAX_CPP_COMPLEXITY_SOURCES,
    _cpp_function_inventory,
    _cpp_metric_details,
    _cpp_metric_details_from_lines,
    _CppComplexityAnalysis,
    _CppFunctionSpan,
    _CppSourceRows,
)
from ici.analysis.cpp_text import (
    mask_cpp_lambda_bodies,
    mask_cpp_literals,
)
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine


class ComplexityEngine(BaseEngine):
    """Calculates Cyclomatic Complexity and Max Nesting Depth for functions."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("complexity")
        warn_cc = cfg.get("warn_cc", 15)
        fail_cc = cfg.get("fail_cc", 25)
        warn_nesting = cfg.get("warn_nesting", 4)
        mode = cfg.get("mode", "pass_warn_fail")

        proj_type = self.project_type()
        all_targets: list[InspectionTarget] = []
        max_cc = 0
        has_error = False
        has_warn = False
        cpp_analysis = _CppComplexityAnalysis()

        # 1. Python Complexity (AST analysis)
        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            p_max, p_targets = self._analyze_python_complexity(warn_cc, fail_cc, warn_nesting)
            max_cc = max(max_cc, p_max)
            all_targets.extend(p_targets)

        # 2. C++ Complexity (Brace/Nesting parser)
        if proj_type in ("cpp", "hybrid") or any(self.project_root.rglob("*.cpp")):
            cpp_analysis = self._analyze_cpp_complexity(
                warn_cc,
                fail_cc,
                warn_nesting,
                str(cfg.get("cpp_boundaries", "auto")),
            )
            max_cc = max(max_cc, cpp_analysis.max_complexity)
            all_targets.extend(cpp_analysis.targets)

        function_targets = [target for target in all_targets if "complexity" in target.metrics]
        issue_targets = [
            target
            for target in all_targets
            if target.status in (EngineStatus.WARN, EngineStatus.FAIL, EngineStatus.ERROR)
        ]
        for t in issue_targets:
            if t.status in {EngineStatus.FAIL, EngineStatus.ERROR}:
                has_error = True
            elif t.status == EngineStatus.WARN:
                has_warn = True

        duration = time.time() - t0
        overall_status = (
            EngineStatus.ERROR
            if cpp_analysis.errors
            else self.evaluate_status(has_error, has_warn, mode)
        )
        summary = (
            f"Max Cyclomatic Complexity: {max_cc} (limit {warn_cc}) across "
            f"{len(function_targets)} functions "
            f"({len(issue_targets)} issues)"
        )

        # Sort all targets by complexity descending
        sorted_targets = sorted(
            function_targets, key=lambda x: x.metrics.get("complexity", 0), reverse=True
        )

        top_funcs_data = [
            {
                "file_path": t.file_path,
                "start_line": t.start_line,
                "end_line": t.end_line,
                "target_name": t.target_name,
                "status": t.status.value,
                "message": t.message,
                "snippet": t.snippet,
                "metrics": t.metrics,
            }
            for t in sorted_targets[:15]
        ]

        return self.create_result(
            name="complexity",
            status=overall_status,
            summary=summary,
            score=float(max_cc),
            duration=duration,
            targets=all_targets,  # Full list kept for toggle inspection
            extra={
                "max_complexity": max_cc,
                "total_functions": len(function_targets),
                "issues_count": len(issue_targets),
                "top_complex_funcs": top_funcs_data,
                "metrics_summary": f"Max CC: {max_cc} "
                f"({len(issue_targets)} issues / {len(function_targets)} funcs)",
                "cpp_boundary_mode": cpp_analysis.boundary_mode,
                "cpp_exact_boundaries": cpp_analysis.exact_boundaries,
                "cpp_estimated_boundaries": cpp_analysis.estimated_boundaries,
                "cpp_boundary_configurations_checked": cpp_analysis.configurations_checked,
                "cpp_boundary_sources_checked": cpp_analysis.sources_checked,
                "cpp_boundary_warnings": cpp_analysis.warnings,
                "cpp_boundary_errors": cpp_analysis.errors,
                "cpp_scope_exclusions": {
                    "lambda": cpp_analysis.lambdas_excluded,
                    "macro_generated_function": cpp_analysis.macro_functions_excluded,
                },
            },
            required=bool(cfg.get("required", True)),
            evidence=(
                EvidenceState.NOT_RUN
                if cpp_analysis.errors
                else (
                    EvidenceState.ESTIMATED
                    if cpp_analysis.estimated_boundaries
                    or cpp_analysis.boundary_mode in {"heuristic", "mixed", "partial"}
                    else EvidenceState.MEASURED
                )
            ),
            tool_evidence=cpp_analysis.tool_evidence,
        )

    def _analyze_python_complexity(
        self, warn_cc: int, fail_cc: int, warn_nesting: int
    ) -> tuple[int, list[InspectionTarget]]:
        targets: list[InspectionTarget] = []
        max_cc = 0

        for py_file in self.project_python_sources():
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                rel_p = str(py_file.relative_to(self.project_root))

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cc = self._calc_ast_cc(node)
                        nesting = self._calc_ast_nesting(node)
                        max_cc = max(max_cc, cc)

                        if cc > fail_cc:
                            st = EngineStatus.FAIL
                            msg = f"Critical complexity: {cc} > {fail_cc} (Immediate refactoring required, Nesting: {nesting})"
                        elif cc > warn_cc or nesting >= warn_nesting:
                            st = EngineStatus.WARN
                            msg = f"High complexity: {cc} (limit {warn_cc}), Max Nesting: {nesting} (limit {warn_nesting})"
                        else:
                            st = EngineStatus.PASS
                            msg = f"Complexity: {cc}, Nesting: {nesting}"

                        end_line = getattr(node, "end_lineno", node.lineno + 10)
                        snippet = ast.get_source_segment(content, node) or ""
                        targets.append(
                            InspectionTarget(
                                file_path=rel_p,
                                start_line=node.lineno,
                                end_line=end_line,
                                target_name=f"{node.name}()",
                                status=st,
                                message=msg,
                                snippet=snippet,
                                metrics={"complexity": cc, "nesting": nesting},
                            )
                        )
            except (SyntaxError, OSError, UnicodeDecodeError) as err:
                _ = err

        return max_cc, targets

    def _calc_ast_cc(self, node: ast.AST) -> int:
        """Delegate to the shared formula — see _python_metrics."""
        return cyclomatic_complexity(node)

    def _calc_ast_nesting(self, node: ast.AST) -> int:
        """Delegate to the shared formula — see _python_metrics."""
        return max_nesting(node)

    def _analyze_cpp_complexity(
        self,
        warn_cc: int,
        fail_cc: int,
        warn_nesting: int,
        boundary_policy: str = "auto",
    ) -> _CppComplexityAnalysis:
        analysis = _CppComplexityAnalysis(boundary_mode="heuristic")
        source_rows = self._cpp_source_rows(analysis)
        if analysis.errors:
            analysis.boundary_mode = "error"
            self._finish_cpp_analysis(analysis)
            return analysis
        exact_boundaries = self._compiler_boundary_rows(
            boundary_policy,
            source_rows,
            analysis,
        )
        matched_heuristic = self._append_exact_cpp_targets(
            analysis,
            source_rows,
            exact_boundaries,
            warn_cc,
            fail_cc,
            warn_nesting,
        )
        self._append_estimated_cpp_targets(
            analysis,
            source_rows,
            matched_heuristic,
            warn_cc,
            fail_cc,
            warn_nesting,
        )
        if (
            boundary_policy == "required"
            and not analysis.errors
            and (analysis.estimated_boundaries or analysis.boundary_mode == "partial")
        ):
            shortfalls: list[str] = []
            if analysis.estimated_boundaries:
                shortfalls.append(
                    f"{analysis.estimated_boundaries} function(s) still needed source scanning"
                )
            if analysis.boundary_mode == "partial":
                shortfalls.append(
                    "compiler-backed function metrics or configuration coverage remained "
                    "partial/low-confidence"
                )
            analysis.errors.append(
                "compiler-backed C++ function boundaries were required, but "
                + "; ".join(shortfalls)
            )
            analysis.boundary_mode = "error"
        self._finish_cpp_analysis(analysis)
        return analysis

    def _cpp_source_rows(self, analysis: _CppComplexityAnalysis) -> _CppSourceRows:
        source_rows: _CppSourceRows = {}
        cpp_sources = self.project_cpp_sources()
        if len(cpp_sources) > _MAX_CPP_COMPLEXITY_SOURCES:
            analysis.errors.append("C++ complexity source count exceeds the bounded limit")
            return source_rows
        source_bytes = 0
        for cpp_file in cpp_sources:
            try:
                rel_p = cpp_file.relative_to(self.project_root).as_posix()
                text = read_cpp_source_text(self.project_root, rel_p)
            except (OSError, UnicodeError, ValueError):
                analysis.errors.append(
                    f"C++ complexity source is not a bounded project file: {cpp_file.name}"
                )
                continue
            source_bytes += len(text.encode("utf-8"))
            if source_bytes > _MAX_CPP_COMPLEXITY_SOURCE_BYTES:
                analysis.errors.append("C++ complexity source inventory exceeds the bounded limit")
                return {}
            try:
                spans, metric_lines = _cpp_function_inventory(text.splitlines())
                _lambda_masked, lambda_ranges = mask_cpp_lambda_bodies(mask_cpp_literals(text))
            except ValueError as err:
                analysis.errors.append(f"C++ complexity source scope is invalid: {rel_p}: {err}")
                return {}
            source_rows[rel_p] = (text, spans, metric_lines)
            analysis.lambdas_excluded += len(lambda_ranges)
        return source_rows

    def _compiler_boundary_rows(
        self,
        boundary_policy: str,
        source_rows: _CppSourceRows,
        analysis: _CppComplexityAnalysis,
    ) -> list[CppFunctionBoundary]:
        if boundary_policy == "off":
            return []
        outcome = run_cpp_function_boundaries(
            self.project_root,
            self.project_compilable_cpp_sources(),
            self.analysis_context,
            runner=run_process,
            source_texts={path: text for path, (text, _spans, _lines) in source_rows.items()},
        )
        self._record_boundary_outcome(analysis, outcome)
        if outcome.mode in {"exact", "partial"}:
            return [
                boundary for boundary in outcome.boundaries if boundary.file_path in source_rows
            ]
        if outcome.mode == "error":
            analysis.errors.extend(outcome.errors)
            analysis.boundary_mode = "error"
        elif boundary_policy == "required":
            analysis.errors.append(
                "compiler-backed C++ function boundaries require an exact compilation "
                "database and approved clang-tidy"
            )
            analysis.boundary_mode = "error"
        return []

    @staticmethod
    def _record_boundary_outcome(
        analysis: _CppComplexityAnalysis,
        outcome: CppFunctionBoundaryOutcome,
    ) -> None:
        analysis.tool_evidence.extend(outcome.evidence)
        analysis.configurations_checked = outcome.configurations_checked
        analysis.sources_checked = outcome.sources_checked
        if outcome.mode != "unavailable":
            analysis.lambdas_excluded = outcome.lambdas_excluded
            analysis.macro_functions_excluded = outcome.macro_functions_excluded
        analysis.warnings.extend(outcome.warnings)
        if outcome.mode in {"exact", "partial"}:
            analysis.boundary_mode = outcome.mode

    def _append_exact_cpp_targets(
        self,
        analysis: _CppComplexityAnalysis,
        source_rows: _CppSourceRows,
        exact_boundaries: list[CppFunctionBoundary],
        warn_cc: int,
        fail_cc: int,
        warn_nesting: int,
    ) -> dict[str, set[int]]:
        matched_heuristic: dict[str, set[int]] = {path: set() for path in source_rows}
        for boundary in exact_boundaries:
            _text, spans, metric_lines = source_rows[boundary.file_path]
            cc, nesting, excluded_lambdas, conditional = _cpp_metric_details_from_lines(
                metric_lines,
                boundary.body_start_line,
                boundary.body_start_column,
                boundary.end_line,
                boundary.end_column or 1,
            )
            metric_variant = boundary.metric_variant or conditional
            metrics: dict[str, object] = {
                "boundary_source": "clang-tidy-ast",
                "boundary_confidence": "exact",
                "metric_confidence": "low" if metric_variant else "medium",
                "tool_lines": boundary.lines,
                "tool_statements": boundary.statements,
                "tool_parameters": boundary.parameters,
                "configurations": list(boundary.configurations),
                "configuration_metrics": [
                    {
                        "configuration": item.configuration,
                        "lines": item.lines,
                        "statements": item.statements,
                        "parameters": item.parameters,
                    }
                    for item in boundary.configuration_metrics
                ],
                "function_kind": boundary.function_kind,
                "function_template": boundary.is_template,
                "function_origin": boundary.origin,
                "metric_variant": metric_variant,
                "preprocessor_conditional": conditional,
                "excluded_nested_lambdas": excluded_lambdas,
            }
            analysis.targets.append(
                self._make_cpp_target(
                    boundary.file_path,
                    boundary.start_line,
                    boundary.end_line,
                    boundary.name,
                    cc,
                    nesting,
                    warn_cc,
                    fail_cc,
                    warn_nesting,
                    start_column=boundary.start_column,
                    end_column=boundary.end_column,
                    extra_metrics=metrics,
                )
            )
            analysis.exact_boundaries += 1
            analysis.max_complexity = max(analysis.max_complexity, cc)
            matched = self._matching_heuristic_span(boundary, spans)
            if matched is not None:
                matched_heuristic[boundary.file_path].add(matched)
        return matched_heuristic

    @staticmethod
    def _matching_heuristic_span(
        boundary: CppFunctionBoundary,
        spans: list[_CppFunctionSpan],
    ) -> int | None:
        exact_name = boundary.name.removesuffix("()").rsplit("::", 1)[-1]
        candidates = [
            (index, span)
            for index, span in enumerate(spans)
            if span.body_start_line == boundary.body_start_line
            and span.body_start_column == boundary.body_start_column
            and span.end_line == boundary.end_line
            and span.end_column == boundary.end_column
            and span.name.removesuffix("()").rsplit("::", 1)[-1] == exact_name
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (item[1].end_line - item[1].start_line, item[0]),
        )[0]

    def _append_estimated_cpp_targets(
        self,
        analysis: _CppComplexityAnalysis,
        source_rows: _CppSourceRows,
        matched_heuristic: dict[str, set[int]],
        warn_cc: int,
        fail_cc: int,
        warn_nesting: int,
    ) -> None:
        for rel_p, (_text, spans, _metric_lines) in source_rows.items():
            for index, span in enumerate(spans):
                if index in matched_heuristic[rel_p]:
                    continue
                analysis.targets.append(
                    self._make_cpp_target(
                        rel_p,
                        span.start_line,
                        span.end_line,
                        span.name,
                        span.complexity,
                        span.max_nesting,
                        warn_cc,
                        fail_cc,
                        warn_nesting,
                        start_column=span.start_column,
                        end_column=span.end_column,
                        extra_metrics={
                            "boundary_source": "heuristic",
                            "boundary_confidence": "medium",
                            "metric_confidence": (
                                "low" if span.preprocessor_conditional else "medium"
                            ),
                            "configurations": [],
                            "function_kind": span.function_kind,
                            "function_template": span.is_template,
                            "function_origin": "source-scanner",
                            "metric_variant": span.preprocessor_conditional,
                            "preprocessor_conditional": span.preprocessor_conditional,
                            "excluded_nested_lambdas": span.excluded_lambdas,
                        },
                    )
                )
                analysis.estimated_boundaries += 1
                analysis.max_complexity = max(analysis.max_complexity, span.complexity)

    @staticmethod
    def _finish_cpp_analysis(analysis: _CppComplexityAnalysis) -> None:
        if analysis.errors:
            analysis.targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="CppComplexityAnalysisError",
                    status=EngineStatus.ERROR,
                    message="; ".join(analysis.errors[:10]),
                    metrics={"boundary_source": "compiler-tool-error"},
                )
            )
        elif analysis.boundary_mode == "partial":
            return
        elif analysis.estimated_boundaries and analysis.boundary_mode == "exact":
            analysis.warnings.append(
                "compiler-backed function output omitted source-scanned definitions"
            )
            analysis.boundary_mode = "partial"
        elif analysis.exact_boundaries and analysis.estimated_boundaries:
            analysis.boundary_mode = "mixed"
        elif analysis.exact_boundaries and analysis.boundary_mode != "partial":
            analysis.boundary_mode = "exact"
        elif not analysis.exact_boundaries and analysis.boundary_mode != "exact":
            analysis.boundary_mode = "heuristic"

    @staticmethod
    def _cpp_boundary_metrics(
        text: str,
        boundary: CppFunctionBoundary,
    ) -> tuple[int, int]:
        complexity, nesting, _lambdas, _conditional = ComplexityEngine._cpp_boundary_metric_details(
            text, boundary
        )
        return complexity, nesting

    @staticmethod
    def _cpp_boundary_metric_details(
        text: str,
        boundary: CppFunctionBoundary,
    ) -> tuple[int, int, int, bool]:
        return _cpp_metric_details(
            text,
            boundary.body_start_line,
            boundary.body_start_column,
            boundary.end_line,
            boundary.end_column or 1,
        )

    def _make_cpp_target(
        self,
        rel_p: str,
        start: int,
        end: int,
        name: str,
        cc: int,
        nesting: int,
        warn_cc: int = 15,
        fail_cc: int = 25,
        warn_nesting: int = 4,
        *,
        start_column: int | None = None,
        end_column: int | None = None,
        extra_metrics: dict[str, object] | None = None,
    ) -> InspectionTarget:
        if cc > fail_cc:
            st = EngineStatus.FAIL
            msg = f"Critical complexity: {cc} > {fail_cc} (Immediate refactoring required)"
        elif cc > warn_cc or nesting >= warn_nesting:
            st = EngineStatus.WARN
            msg = f"High complexity: {cc} (limit {warn_cc}), Max Nesting: {nesting} (limit {warn_nesting})"
        else:
            st = EngineStatus.PASS
            msg = f"Complexity: {cc}, Nesting: {nesting}"

        metrics: dict[str, object] = {"complexity": cc, "nesting": nesting}
        metrics.update(extra_metrics or {})
        display_name = name if name.endswith("()") else f"{name}()"
        return InspectionTarget(
            file_path=rel_p,
            start_line=start,
            end_line=end,
            start_column=start_column,
            end_column=end_column,
            target_name=display_name,
            status=st,
            message=msg,
            metrics=metrics,
        )
