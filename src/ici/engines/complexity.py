"""5. Code Complexity & Nesting Depth Analysis Engine."""

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
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        max_cc = 0
        has_error = False
        has_warn = False

        # 1. Python Complexity (AST analysis)
        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            p_max, p_targets = self._analyze_python_complexity()
            max_cc = max(max_cc, p_max)
            targets.extend(p_targets)

        # 2. C++ Complexity (Brace/Nesting parser)
        if proj_type in ("cpp", "hybrid") or any(self.project_root.rglob("*.cpp")):
            c_max, c_targets = self._analyze_cpp_complexity()
            max_cc = max(max_cc, c_max)
            targets.extend(c_targets)

        for t in targets:
            if t.status == EngineStatus.FAIL:
                has_error = True
            elif t.status == EngineStatus.WARN:
                has_warn = True

        duration = time.time() - t0
        overall_status = (
            EngineStatus.FAIL
            if has_error
            else (EngineStatus.WARN if has_warn else EngineStatus.PASS)
        )
        summary = f"Max Cyclomatic Complexity: {max_cc} (limit 15) across {len(targets)} functions"

        return self.create_result(
            name="complexity",
            status=overall_status,
            summary=summary,
            score=float(max_cc),
            duration=duration,
            targets=targets,
            extra={"max_complexity": max_cc, "metrics_summary": f"Max CC: {max_cc}"},
        )

    def _analyze_python_complexity(self) -> tuple[int, list[InspectionTarget]]:
        targets: list[InspectionTarget] = []
        max_cc = 0

        for py_file in get_all_python_sources(self.project_root):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                rel_p = str(py_file.relative_to(self.project_root))

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cc = self._calc_ast_cc(node)
                        nesting = self._calc_ast_nesting(node)
                        max_cc = max(max_cc, cc)

                        if cc > 25:
                            st = EngineStatus.FAIL
                            msg = f"Critical complexity: {cc} > 25 (Immediate refactoring required, Nesting: {nesting})"
                        elif cc > 15 or nesting >= 4:
                            st = EngineStatus.WARN
                            msg = f"High complexity: {cc} (limit 15), Max Nesting: {nesting} (limit 4)"
                        else:
                            st = EngineStatus.PASS
                            msg = f"Complexity: {cc}, Nesting: {nesting}"

                        end_line = getattr(node, "end_lineno", node.lineno + 10)
                        targets.append(
                            InspectionTarget(
                                file_path=rel_p,
                                start_line=node.lineno,
                                end_line=end_line,
                                target_name=f"{node.name}()",
                                status=st,
                                message=msg,
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
            elif isinstance(child, (ast.IfExp, ast.Match)):
                complexity += 1
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

    def _analyze_cpp_complexity(self) -> tuple[int, list[InspectionTarget]]:
        targets: list[InspectionTarget] = []
        max_cc = 0

        for cpp_file in get_all_cpp_sources(self.project_root):
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
                                )
                            )
                            max_cc = max(max_cc, branch_count)
                            curr_func = None

                if curr_func:
                    targets.append(
                        self._make_cpp_target(
                            rel_p, func_start, len(lines), curr_func, branch_count, max_func_nesting
                        )
                    )
                    max_cc = max(max_cc, branch_count)
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        return max_cc, targets

    def _make_cpp_target(
        self, rel_p: str, start: int, end: int, name: str, cc: int, nesting: int
    ) -> InspectionTarget:
        if cc > 25:
            st = EngineStatus.FAIL
            msg = f"Critical complexity: {cc} > 25 (Immediate refactoring required)"
        elif cc > 15 or nesting >= 4:
            st = EngineStatus.WARN
            msg = f"High complexity: {cc} (limit 15), Max Nesting: {nesting}"
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
