"""5. Code Complexity & Nesting Depth Analysis Engine with Policy Thresholds."""

import ast
import re
import time

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    detect_project_type,
    get_all_cpp_sources,
    get_all_python_sources,
)
from ici.engines.base import BaseEngine
from ici.engines.cpp_text import mask_cpp_literals

# --- C++ function scanning -------------------------------------------------
#
# The previous implementation guessed at function boundaries by looking for a
# line that contained "(", ")", "{" and one of a handful of return-type
# keywords. That misread C++ in three separate ways, each of which put the
# reported complexity on the wrong function:
#
#   * A definition written on one line — `void Stats::add(const T& r) { v_.push_back(r); }`
#     — never closed, because the scanner skipped the signature line when
#     counting braces. It kept accumulating and absorbed the functions after it.
#   * `for (int i = 0; i < n; ++i) {` matched the heuristic (it has parentheses,
#     a brace, and "int "), so a loop was reported as a function named "for" and
#     the real function was truncated at that line.
#   * A signature split across lines was never detected at all, so its body was
#     attributed to whatever came before it.
#
# Tracking brace depth and accumulating the signature until its opening brace
# removes all three. Names are rejected when the token before "(" is a control
# keyword, which is what keeps loops and conditionals out of the results.

_CPP_CONTROL_HEADS = frozenset(
    {"if", "for", "while", "switch", "catch", "do", "else", "return", "sizeof"}
)

_CPP_DECISION_TOKENS = ("&&", "||", "?")

_CPP_DECISION_KEYWORDS = ("if", "for", "while", "case", "catch")


class _CppFunctionSpan:
    """One C++ function definition and its measured complexity."""

    __slots__ = ("complexity", "end_line", "max_nesting", "name", "start_line")

    def __init__(self, name: str, start_line: int) -> None:
        self.name = name
        self.start_line = start_line
        self.end_line = start_line
        self.complexity = 1
        self.max_nesting = 1


def _strip_cpp_noise(line: str) -> str:
    """Drop line comments and string/char literals so they cannot fake tokens."""
    text = line.split("//", 1)[0]
    return mask_cpp_literals(text)


def _cpp_decision_count(text: str) -> int:
    """Count decision points on one line, the usual cyclomatic contributors."""
    total = sum(text.count(token) for token in _CPP_DECISION_TOKENS)
    for keyword in _CPP_DECISION_KEYWORDS:
        total += len(re.findall(r"\b" + keyword + r"\b\s*[({:]", text))
    return total


def _cpp_definition_name(signature: str) -> str | None:
    """Return the function name for a definition header, or None if it is not one."""
    if "(" not in signature:
        return None
    head = signature.split("(", 1)[0].strip()
    if not head:
        return None
    token = head.split()[-1].lstrip("*&")
    if not token or token in _CPP_CONTROL_HEADS:
        return None
    if not (token[0].isalpha() or token[0] == "_"):
        return None
    return token


def _cpp_function_spans(lines: list[str]) -> list[_CppFunctionSpan]:
    """Scan a translation unit and measure every function definition in it."""
    scanner = _CppScanner()
    for number, raw in enumerate(lines, 1):
        scanner.feed(number, _strip_cpp_noise(raw))
    scanner.finish(len(lines))
    return scanner.spans


class _CppScanner:
    """Brace-depth state machine over one file."""

    def __init__(self) -> None:
        self.spans: list[_CppFunctionSpan] = []
        self._depth = 0
        self._current: _CppFunctionSpan | None = None
        self._base_depth = 0
        self._pending = ""
        self._pending_line = 0

    def feed(self, number: int, text: str) -> None:
        if self._current is not None:
            self._feed_body(number, text)
            return
        self._feed_outside(number, text)

    def finish(self, last_line: int) -> None:
        """Close an unterminated function so its findings are still reported."""
        if self._current is None:
            return
        self._current.end_line = last_line
        self.spans.append(self._current)
        self._current = None

    def _feed_body(self, number: int, text: str) -> None:
        current = self._current
        if current is None:
            return
        current.complexity += _cpp_decision_count(text)
        opened = text.count("{")
        closed = text.count("}")
        if opened:
            self._depth += opened
            current.max_nesting = max(current.max_nesting, self._depth - self._base_depth)
        self._depth -= closed
        if self._depth <= self._base_depth:
            current.end_line = number
            self.spans.append(current)
            self._current = None
            self._depth = max(self._depth, self._base_depth)

    def _feed_outside(self, number: int, text: str) -> None:
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            self._pending = ""
            return
        if not self._pending:
            self._pending_line = number
        self._pending = f"{self._pending} {stripped}".strip()
        if "{" not in stripped:
            # A ';' at this level ends a declaration or statement, not a body.
            if ";" in stripped:
                self._pending = ""
            return
        self._open_or_discard(number, stripped)

    def _open_or_discard(self, number: int, stripped: str) -> None:
        signature = self._pending
        self._pending = ""
        name = _cpp_definition_name(signature)
        if name is None:
            # Not a function: keep the braces balanced for anything that follows
            # (a struct body, a namespace, an initialiser list).
            self._depth += stripped.count("{") - stripped.count("}")
            self._depth = max(self._depth, 0)
            return
        span = _CppFunctionSpan(name, self._pending_line)
        self._base_depth = self._depth
        self._depth += 1
        self._current = span
        span.complexity += _cpp_decision_count(signature)
        # The rest of the line can close the body outright: `void f() { g(); }`.
        after = stripped.split("{", 1)[1]
        self._current = span
        self._feed_body(number, after)


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
                lines = cpp_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            for span in _cpp_function_spans(lines):
                targets.append(
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
                    )
                )
                max_cc = max(max_cc, span.complexity)

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
