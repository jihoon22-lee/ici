"""Cognitive complexity engine — SonarQube S3776 style, nesting-weighted."""

import ast
import time

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.runner import run_process
from ici.engines._cpp_cognitive import analyze_cpp_cognitive
from ici.engines._python_metrics import iter_metric_children
from ici.engines.base import BaseEngine


def _cognitive_for_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
    """Return (cognitive_complexity, max_nesting) for one function.

    Rules (Sonar-inspired, pure-Python):
    - +1 for if/elif/else, for, while, except, with, assert, comprehension
    - +1 per boolean operator chain (and/or), plus nesting
    - +nesting_level for each nesting increment (if/for/while/except/with)
    - break/continue inside a loop count as +1
    """
    cognitive = 0
    max_nesting = 0

    def walk(n, nesting: int, in_loop: bool = False):
        nonlocal cognitive, max_nesting
        max_nesting = max(max_nesting, nesting)
        for child in iter_metric_children(n, root=node):
            if isinstance(
                child,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.ExceptHandler,
                    ast.With,
                    ast.AsyncWith,
                ),
            ):
                # Each branch/loop/handler/with is +1, weighted by its nesting
                # depth. elif is modeled as a nested If in orelse, so it is
                # counted (and weighted) the same way as any other If.
                cognitive += 1 + nesting
                walk(
                    child,
                    nesting + 1,
                    in_loop or isinstance(child, (ast.For, ast.AsyncFor, ast.While)),
                )
            elif isinstance(child, ast.BoolOp):
                if isinstance(child.op, (ast.And, ast.Or)):
                    cognitive += 1 + nesting
                walk(child, nesting, in_loop)
            elif isinstance(child, ast.comprehension):
                cognitive += 1 + nesting
                walk(child, nesting, in_loop)
            elif isinstance(child, (ast.Break, ast.Continue)):
                if in_loop:
                    cognitive += 1
            elif isinstance(child, ast.Assert):
                cognitive += 1 + nesting
                walk(child, nesting, in_loop)
            else:
                walk(child, nesting, in_loop)

    walk(node, 0)
    return cognitive, max_nesting


class CognitiveEngine(BaseEngine):
    """Calculates cognitive complexity per function (nesting-weighted)."""

    CACHE_IMPLEMENTATION_MODULES = (
        "ici.core._compile_db_paths",
        "ici.core._cpp_replay_policy",
        "ici.core.cpp_replay",
        "ici.engines._cpp_cognitive",
        "ici.engines._cpp_function_boundaries",
        "ici.engines._cpp_tooling",
        "ici.engines.cpp_text",
    )

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("cognitive")
        # Matches DEFAULT_CONFIG's shipped policy (warn=30, fail=60); kept in
        # sync so a standalone/partial config behaves the same as the real
        # default policy instead of a stricter, undocumented fallback.
        warn = int(cfg.get("warn", 30))
        fail = int(cfg.get("fail", 60))
        warn_nesting = int(cfg.get("warn_nesting", 4))
        mode = cfg.get("mode", "pass_warn_fail")

        has_warn = False
        has_fail = False
        has_error = False
        targets: list[InspectionTarget] = []
        max_cog = 0
        python_functions = 0

        for py_file in self.project_python_sources():
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
            except (OSError, SyntaxError, UnicodeError) as err:
                try:
                    relative = py_file.relative_to(self.project_root).as_posix()
                except ValueError:
                    relative = py_file.name
                line = err.lineno if isinstance(err, SyntaxError) else 1
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=line or 1,
                        target_name="PythonCognitiveAnalysisError",
                        status=EngineStatus.ERROR,
                        message=f"Python cognitive analysis failed: {type(err).__name__}",
                    )
                )
                has_error = True
                continue
            rel = str(py_file.relative_to(self.project_root))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                python_functions += 1
                cog, nesting = _cognitive_for_function(node)
                max_cog = max(max_cog, cog)

                status = EngineStatus.PASS
                if cog >= fail:
                    has_fail = True
                    status = EngineStatus.FAIL
                elif cog >= warn or nesting >= warn_nesting:
                    has_warn = True
                    status = EngineStatus.WARN

                snippet = ast.get_source_segment(content, node) or ""
                snippet_trim = "\n".join(snippet.splitlines()[:20])
                targets.append(
                    InspectionTarget(
                        file_path=rel,
                        start_line=getattr(node, "lineno", 1),
                        end_line=getattr(node, "end_lineno", None),
                        start_column=getattr(node, "col_offset", 0) + 1,
                        end_column=getattr(node, "end_col_offset", None),
                        target_name=node.name,
                        status=status,
                        message=f"Cognitive {cog} (nesting {nesting})",
                        snippet=snippet_trim,
                        metrics={
                            "cognitive": cog,
                            "nesting": nesting,
                            "metric_source": "python-ast",
                            "metric_confidence": "high",
                        },
                    )
                )

        cpp_sources = self.project_cpp_sources()
        cpp = analyze_cpp_cognitive(
            self.project_root,
            cpp_sources,
            self.project_compilable_cpp_sources(),
            self.analysis_context,
            warn=warn,
            fail=fail,
            warn_nesting=warn_nesting,
            boundary_policy=str(cfg.get("cpp_boundaries", "auto")),
            runner=run_process,
        )
        targets.extend(cpp.targets)
        max_cog = max(max_cog, cpp.max_cognitive)
        has_error = has_error or bool(cpp.errors)
        for target in cpp.targets:
            if target.status == EngineStatus.FAIL:
                has_fail = True
            elif target.status == EngineStatus.WARN:
                has_warn = True
        if cpp.errors:
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="CppCognitiveAnalysisError",
                    status=EngineStatus.ERROR,
                    message="; ".join(cpp.errors[:10]),
                    metrics={"boundary_source": "compiler-tool-error"},
                )
            )

        status = EngineStatus.ERROR if has_error else self.evaluate_status(has_fail, has_warn, mode)
        function_count = python_functions + len(cpp.targets)
        summary = f"Max cognitive complexity: {max_cog} across {function_count} functions"
        if has_error:
            summary = "Cognitive complexity analysis did not complete"
        elif has_fail:
            summary += f"; fail threshold {fail} exceeded"
        elif has_warn:
            summary += f"; warn threshold {warn} or nesting threshold {warn_nesting} reached"

        return self.create_result(
            name="cognitive",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=targets,
            extra={
                "max_cognitive": max_cog,
                "total_functions": function_count,
                "python_functions": python_functions,
                "cpp_functions": len(cpp.targets),
                "cpp_boundary_mode": cpp.boundary_mode,
                "cpp_exact_boundaries": cpp.exact_boundaries,
                "cpp_estimated_boundaries": cpp.estimated_boundaries,
                "cpp_boundary_configurations_checked": cpp.configurations_checked,
                "cpp_boundary_sources_checked": cpp.sources_checked,
                "cpp_boundary_warnings": cpp.warnings,
                "cpp_boundary_errors": cpp.errors,
                "cpp_scope_exclusions": {
                    "lambda": cpp.lambdas_excluded,
                    "macro_generated_function": cpp.macro_functions_excluded,
                },
            },
            required=bool(cfg.get("required", False)),
            evidence=(
                EvidenceState.NOT_RUN
                if has_error
                else (EvidenceState.ESTIMATED if cpp.targets else EvidenceState.MEASURED)
            ),
            tool_evidence=cpp.evidence,
        )
