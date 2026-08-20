"""5. Code Complexity & Nesting Depth Analysis Engine with Policy Thresholds."""

import ast
import time

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    detect_project_type,
    get_all_cpp_sources,
    get_all_python_sources,
)
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

        proj_type = detect_project_type(self.project_root)
        all_targets: list[InspectionTarget] = []
        max_cc = 0
        has_error = False
        has_warn = False

        # 1. Python Complexity (AST analysis)
        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            p_max, p_targets = self._analyze_python_complexity(warn_cc, fail_cc, warn_nesting)
            max_cc = max(max_cc, p_max)
            all_targets.extend(p_targets)

        # 2. C++ Complexity (Brace/Nesting parser)
        if proj_type in ("cpp", "hybrid") or any(self.project_root.rglob("*.cpp")):
            c_max, c_targets = self._analyze_cpp_complexity(warn_cc, fail_cc, warn_nesting)
            max_cc = max(max_cc, c_max)
            all_targets.extend(c_targets)

        issue_targets = [
            t for t in all_targets if t.status in (EngineStatus.WARN, EngineStatus.FAIL)
        ]
        for t in issue_targets:
            if t.status == EngineStatus.FAIL:
                has_error = True
            elif t.status == EngineStatus.WARN:
                has_warn = True

        duration = time.time() - t0
        overall_status = self.evaluate_status(has_error, has_warn, mode)
        summary = (
            f"Max Cyclomatic Complexity: {max_cc} (limit {warn_cc}) across {len(all_targets)} functions "
            f"({len(issue_targets)} issues)"
        )

        # Sort all targets by complexity descending
        sorted_targets = sorted(
            all_targets, key=lambda x: x.metrics.get("complexity", 0), reverse=True
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
                "total_functions": len(all_targets),
                "issues_count": len(issue_targets),
                "top_complex_funcs": top_funcs_data,
                "metrics_summary": f"Max CC: {max_cc} ({len(issue_targets)} issues / {len(all_targets)} funcs)",
            },
            required=bool(cfg.get("required", True)),
        )

    def _analyze_python_complexity(
        self, warn_cc: int, fail_cc: int, warn_nesting: int
    ) -> tuple[int, list[InspectionTarget]]:
        targets: list[InspectionTarget] = []
        max_cc = 0

        for py_file in get_all_python_sources(self.project_root, self.config):
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
        """Calculates Cyclomatic Complexity: 1 + number of branching points."""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(
                child,
                (
                    ast.If,
                    ast.While,
                    ast.For,
                    ast.AsyncFor,
                    ast.ExceptHandler,
                    ast.With,
                    ast.AsyncWith,
                ),
            ):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
            elif isinstance(child, ast.IfExp):
                complexity += 1
            elif isinstance(child, ast.comprehension):
                complexity += len(child.ifs)
            elif isinstance(child, ast.Match):
                complexity += 1 + sum(1 for case in child.cases if case.guard is not None)
        return complexity

    def _calc_ast_nesting(self, node: ast.AST) -> int:
        """Calculates maximum block nesting depth inside function."""

        def _get_depth(curr: ast.AST, depth: int) -> int:
            max_d = depth
            is_block = isinstance(
                curr, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.Try, ast.With)
            )
            new_depth = depth + (1 if is_block else 0)
            max_d = max(max_d, new_depth)
            for child in ast.iter_child_nodes(curr):
                max_d = max(max_d, _get_depth(child, new_depth))
            return max_d

        return _get_depth(node, 0)

    def _analyze_cpp_complexity(
        self, warn_cc: int, fail_cc: int, warn_nesting: int
    ) -> tuple[int, list[InspectionTarget]]:
        targets: list[InspectionTarget] = []
        max_cc = 0

        for cpp_file in get_all_cpp_sources(self.project_root, self.config):
            try:
                rel_p = str(cpp_file.relative_to(self.project_root))
                with open(cpp_file, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()

                # Basic heuristic for C++ functions
                curr_func = None
                func_start = 1
                branch_count = 1
                nesting = 0
                max_func_nesting = 0

                for idx, line in enumerate(lines, 1):
                    stripped = line.strip()
                    if (
                        "(" in stripped
                        and ")" in stripped
                        and "{" in stripped
                        and not stripped.startswith(("//", "#", "/*"))
                    ) and (
                        "int " in stripped
                        or "void " in stripped
                        or "bool " in stripped
                        or "auto " in stripped
                        or "double " in stripped
                    ):
                        if curr_func:
                            targets.append(
                                self._make_cpp_target(
                                    rel_p,
                                    func_start,
                                    idx - 1,
                                    curr_func,
                                    branch_count,
                                    max_func_nesting,
                                    warn_cc,
                                    fail_cc,
                                    warn_nesting,
                                )
                            )
                            max_cc = max(max_cc, branch_count)
                        curr_func = stripped.split("(")[0].split()[-1]
                        func_start = idx
                        branch_count = 1
                        nesting = 1
                        max_func_nesting = 1
                        continue

                    if curr_func:
                        if "{" in stripped:
                            nesting += stripped.count("{")
                            max_func_nesting = max(max_func_nesting, nesting)
                        if "}" in stripped:
                            nesting -= stripped.count("}")
                        if any(
                            kw in stripped
                            for kw in (
                                "if (",
                                "if(",
                                "while (",
                                "while(",
                                "for (",
                                "for(",
                                "catch (",
                                "case ",
                                "&&",
                                "||",
                            )
                        ):
                            branch_count += 1

                        if nesting <= 0:
                            targets.append(
                                self._make_cpp_target(
                                    rel_p,
                                    func_start,
                                    idx,
                                    curr_func,
                                    branch_count,
                                    max_func_nesting,
                                    warn_cc,
                                    fail_cc,
                                    warn_nesting,
                                )
                            )
                            max_cc = max(max_cc, branch_count)
                            curr_func = None

                if curr_func:
                    targets.append(
                        self._make_cpp_target(
                            rel_p,
                            func_start,
                            len(lines),
                            curr_func,
                            branch_count,
                            max_func_nesting,
                            warn_cc,
                            fail_cc,
                            warn_nesting,
                        )
                    )
                    max_cc = max(max_cc, branch_count)
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        return max_cc, targets

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

        return InspectionTarget(
            file_path=rel_p,
            start_line=start,
            end_line=end,
            target_name=f"{name}()",
            status=st,
            message=msg,
            metrics={"complexity": cc, "nesting": nesting},
        )
