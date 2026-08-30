"""Cognitive complexity engine — SonarQube S3776 style, nesting-weighted."""

import ast
import time

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
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
        for child in ast.iter_child_nodes(n):
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
        targets: list[InspectionTarget] = []
        max_cog = 0

        for py_file in self.project_python_sources():
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content, filename=str(py_file))
            except (OSError, SyntaxError):
                continue
            rel = str(py_file.relative_to(self.project_root))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("__") and node.name.endswith("__"):
                    continue
                cog, nesting = _cognitive_for_function(node)
                max_cog = max(max_cog, cog)

                status = EngineStatus.PASS
                if cog >= fail:
                    has_fail = True
                    status = EngineStatus.FAIL
                elif cog >= warn or nesting >= warn_nesting:
                    has_warn = True
                    status = EngineStatus.WARN

                # Only report WARN/FAIL targets — passing functions would
                # just add noise without any actionable content.
                if status != EngineStatus.PASS:
                    snippet = ast.get_source_segment(content, node) or ""
                    snippet_trim = "\n".join(snippet.splitlines()[:20])
                    targets.append(
                        InspectionTarget(
                            file_path=rel,
                            start_line=getattr(node, "lineno", 1),
                            end_line=getattr(node, "end_lineno", None),
                            target_name=node.name,
                            status=status,
                            message=f"Cognitive {cog} (nesting {nesting})",
                            snippet=snippet_trim,
                            metrics={"cognitive": cog, "nesting": nesting},
                        )
                    )

        status = self.evaluate_status(has_fail, has_warn, mode)
        summary = (
            f"Max cognitive complexity: {max_cog}" if max_cog else "No cognitive complexity issues"
        )
        if has_fail:
            summary = f"Cognitive complexity {max_cog} exceeds fail threshold {fail}"
        elif has_warn:
            summary = f"Cognitive complexity {max_cog} exceeds warn threshold {warn}"

        return self.create_result(
            name="cognitive",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=targets,
            required=bool(cfg.get("required", False)),
            evidence=EvidenceState.MEASURED,
        )
