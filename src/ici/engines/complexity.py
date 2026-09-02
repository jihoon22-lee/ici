"""5. Code Complexity & Nesting Depth Analysis Engine with Policy Thresholds."""

import ast
import re
import time
from dataclasses import dataclass, field

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.runner import run_process
from ici.engines._cpp_function_boundaries import (
    CppFunctionBoundary,
    CppFunctionBoundaryOutcome,
    read_cpp_source_text,
    run_cpp_function_boundaries,
)
from ici.engines._python_metrics import iter_metric_children, walk_metric_scope
from ici.engines.base import BaseEngine
from ici.engines.cpp_text import (
    cpp_definition_name,
    cpp_function_like_macro_names,
    cpp_has_conditional_directive,
    cpp_is_operator_name,
    cpp_requires_expression_before_brace,
    mask_cpp_lambda_bodies,
    mask_cpp_literals,
    mask_cpp_preprocessor_directives,
)

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

_CPP_DECISION_TOKENS = ("&&", "||", "?")

_CPP_DECISION_HEAD_RE = re.compile(
    r"\b(?:for|while|catch)\s*\(|\bif\s+(?:constexpr\s*\(|!?consteval\b)|\bif\s*\("
)
_CPP_CASE_RE = re.compile(r"\bcase\b[^:\n]*:")
_CPP_LAMBDA_INITIALIZER_RE = re.compile(r"(?<![=!<>])=\s*(?:\+\s*)?\[")
_MAX_CPP_COMPLEXITY_SOURCES = 2_048
_MAX_CPP_COMPLEXITY_SOURCE_BYTES = 64 * 1024 * 1024


class _CppFunctionSpan:
    """One C++ function definition and its measured complexity."""

    __slots__ = (
        "body_start_column",
        "body_start_line",
        "complexity",
        "end_column",
        "end_line",
        "excluded_lambdas",
        "function_kind",
        "is_template",
        "max_nesting",
        "name",
        "preprocessor_conditional",
        "start_column",
        "start_line",
    )

    def __init__(
        self,
        name: str,
        start_line: int,
        start_column: int,
        body_start_line: int,
        body_start_column: int,
        *,
        function_kind: str,
        is_template: bool,
    ) -> None:
        self.name = name
        self.start_line = start_line
        self.start_column = start_column
        self.body_start_line = body_start_line
        self.body_start_column = body_start_column
        self.end_line = start_line
        self.end_column: int | None = None
        self.complexity = 1
        self.excluded_lambdas = 0
        self.function_kind = function_kind
        self.is_template = is_template
        self.preprocessor_conditional = False
        # Counted the way the Python side counts it: how many blocks deep the
        # code goes *inside* the function, so a body with no branches is 0.
        self.max_nesting = 0


def _cpp_decision_count(text: str) -> int:
    """Count decision points on one line, the usual cyclomatic contributors."""
    total = sum(text.count(token) for token in _CPP_DECISION_TOKENS)
    return total + len(_CPP_DECISION_HEAD_RE.findall(text)) + len(_CPP_CASE_RE.findall(text))


def _cpp_function_spans(lines: list[str]) -> list[_CppFunctionSpan]:
    """Scan a translation unit and measure every function definition in it."""
    spans, _metric_lines = _cpp_function_inventory(lines)
    return spans


def _cpp_function_inventory(
    lines: list[str],
) -> tuple[list[_CppFunctionSpan], list[str]]:
    # Mask the complete file in one pass so block comments, raw strings, and
    # line-spliced comments cannot leak fake braces across line boundaries.
    masked_source = mask_cpp_literals("\n".join(lines)).replace("<%", "{ ").replace("%>", "} ")
    scanner = _CppScanner(cpp_function_like_macro_names(masked_source))
    metric_lines = masked_source.splitlines()
    scanner_lines = mask_cpp_preprocessor_directives(masked_source).splitlines()
    for number, text in enumerate(scanner_lines, 1):
        scanner.feed(number, text)
    scanner.finish(len(lines))
    for span in scanner.spans:
        if span.end_column is None:
            continue
        details = _cpp_metric_details_from_lines(
            metric_lines,
            span.body_start_line,
            span.body_start_column,
            span.end_line,
            span.end_column,
        )
        span.complexity = details[0]
        span.max_nesting = details[1]
        span.excluded_lambdas = details[2]
        span.preprocessor_conditional = details[3]
    return scanner.spans, metric_lines


def _cpp_metric_details(
    text: str,
    body_start_line: int,
    body_start_column: int,
    end_line: int,
    end_column: int,
) -> tuple[int, int, int, bool]:
    lines = mask_cpp_literals(text).replace("<%", "{ ").replace("%>", "} ").splitlines()
    return _cpp_metric_details_from_lines(
        lines,
        body_start_line,
        body_start_column,
        end_line,
        end_column,
    )


def _cpp_metric_details_from_lines(
    lines: list[str],
    body_start_line: int,
    body_start_column: int,
    end_line: int,
    end_column: int,
) -> tuple[int, int, int, bool]:
    selected = lines[body_start_line - 1 : end_line]
    if not selected:
        return 1, 0, 0, False
    if body_start_line == end_line:
        selected[0] = selected[0][body_start_column - 1 : end_column]
    else:
        selected[0] = selected[0][body_start_column - 1 :]
        selected[-1] = selected[-1][:end_column]
    body = "\n".join(selected)
    metric_body, lambda_ranges = mask_cpp_lambda_bodies(body)
    complexity = 1 + sum(_cpp_decision_count(line) for line in metric_body.splitlines())
    depth = 0
    max_nesting = 0
    for char in metric_body:
        if char == "{":
            depth += 1
            max_nesting = max(max_nesting, max(0, depth - 1))
        elif char == "}":
            depth = max(0, depth - 1)
    return complexity, max_nesting, len(lambda_ranges), cpp_has_conditional_directive(metric_body)


@dataclass
class _CppComplexityAnalysis:
    max_complexity: int = 0
    targets: list[InspectionTarget] = field(default_factory=list)
    tool_evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    boundary_mode: str = "not_applicable"
    exact_boundaries: int = 0
    estimated_boundaries: int = 0
    configurations_checked: int = 0
    sources_checked: int = 0
    lambdas_excluded: int = 0
    macro_functions_excluded: int = 0


_CppSourceRows = dict[str, tuple[str, list[_CppFunctionSpan], list[str]]]


class _CppScanner:
    """Brace-depth state machine over one file."""

    def __init__(self, macro_names: frozenset[str] = frozenset()) -> None:
        self.spans: list[_CppFunctionSpan] = []
        self._depth = 0
        self._current: _CppFunctionSpan | None = None
        self._base_depth = 0
        self._pending = ""
        self._pending_line = 0
        self._pending_column = 0
        self._expression_depth = 0
        self._function_try = False
        self._awaiting_catch = False
        self._catch_header = False
        self._macro_names = macro_names

    def feed(self, number: int, text: str) -> None:
        remaining = text
        column = 1
        while remaining:
            if self._current is not None:
                consumed = self._feed_body(number, remaining, column)
            elif self._expression_depth:
                consumed = self._feed_expression(remaining)
            else:
                consumed = self._feed_outside(number, remaining, column)
            if consumed <= 0:
                if self._current is None:
                    continue
                return
            remaining = remaining[consumed:]
            column += consumed

    def finish(self, last_line: int) -> None:
        """Close an unterminated function so its findings are still reported."""
        if self._current is None:
            return
        self._current.end_line = last_line
        self.spans.append(self._current)
        self._current = None

    def _feed_body(self, number: int, text: str, column: int) -> int:
        current = self._current
        if current is None:
            return len(text)
        if self._awaiting_catch:
            return self._feed_catch(number, text, column)
        close_at: int | None = None
        for index, char in enumerate(text):
            if char == "{":
                self._depth += 1
                # The function's own brace is not nesting; only blocks inside
                # it contribute to the shared Python/C++ nesting policy.
                current.max_nesting = max(
                    current.max_nesting,
                    self._depth - self._base_depth - 1,
                )
            elif char == "}":
                self._depth -= 1
                if self._depth <= self._base_depth:
                    close_at = index
                    break
        body = text if close_at is None else text[: close_at + 1]
        current.complexity += _cpp_decision_count(body)
        if close_at is None:
            return len(text)
        current.end_line = number
        current.end_column = column + close_at
        self._depth = max(self._depth, self._base_depth)
        if self._function_try:
            self._awaiting_catch = True
            return close_at + 1
        self.spans.append(current)
        self._current = None
        return close_at + 1

    def _feed_catch(self, number: int, text: str, column: int) -> int:
        current = self._current
        if current is None:
            return 0
        stripped = text.lstrip()
        leading = len(text) - len(stripped)
        if not stripped:
            return len(text)
        if not self._catch_header:
            match = re.match(r"catch\b", stripped)
            if match is None:
                self.spans.append(current)
                self._current = None
                self._function_try = False
                self._awaiting_catch = False
                return 0
            current.complexity += 1
            self._catch_header = True
        opening = text.find("{")
        if opening < 0:
            return len(text)
        self._catch_header = False
        self._awaiting_catch = False
        self._depth = self._base_depth + 1
        current.max_nesting = max(current.max_nesting, 0)
        return max(opening + 1, leading + 1)

    def _append_pending(self, number: int, column: int, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if not self._pending:
            self._pending_line = number
            self._pending_column = column + len(text) - len(text.lstrip())
        self._pending = f"{self._pending} {stripped}".strip()

    def _clear_pending(self) -> None:
        self._pending = ""
        self._pending_line = 0
        self._pending_column = 0

    def _feed_expression(self, text: str) -> int:
        for index, char in enumerate(text):
            if char == "{":
                self._expression_depth += 1
            elif char == "}":
                self._expression_depth -= 1
                if self._expression_depth == 0:
                    self._pending = f"{self._pending} {{}}".strip()
                    return index + 1
        return len(text)

    def _feed_outside(self, number: int, text: str, column: int) -> int:
        if text.lstrip().startswith("#"):
            self._clear_pending()
            return len(text)
        if not self._pending and self._standalone_macro_invocation(text):
            self._clear_pending()
            return len(text)
        delimiters = [(index, char) for index, char in enumerate(text) if char in "{};"]
        if not delimiters:
            self._append_pending(number, column, text)
            return len(text)
        index, delimiter = delimiters[0]
        self._append_pending(number, column, text[:index])
        if delimiter == ";":
            self._clear_pending()
            return index + 1
        if delimiter == "}":
            self._clear_pending()
            self._depth = max(0, self._depth - 1)
            return index + 1

        signature = self._pending
        parentheses, brackets = _cpp_delimiter_depth(signature)
        if (
            parentheses
            or brackets
            or _CPP_LAMBDA_INITIALIZER_RE.search(signature) is not None
            or cpp_requires_expression_before_brace(signature)
            or _cpp_constructor_initializer_candidate(signature)
        ):
            self._expression_depth = 1
            return index + 1
        name = cpp_definition_name(signature)
        if name is None:
            self._clear_pending()
            self._depth += 1
            return index + 1
        span = _CppFunctionSpan(
            name,
            self._pending_line,
            self._pending_column,
            number,
            column + index,
            function_kind="operator" if cpp_is_operator_name(name) else "function",
            is_template=bool(re.search(r"\btemplate\s*<", signature)),
        )
        self._clear_pending()
        self._base_depth = self._depth
        self._depth += 1
        self._current = span
        self._function_try = bool(re.search(r"\btry\b", signature))
        span.complexity += _cpp_decision_count(signature)
        return index + 1

    def _standalone_macro_invocation(self, text: str) -> bool:
        match = re.fullmatch(
            r"[ \t]*(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\(.*\)[ \t]*",
            text,
        )
        if match is None:
            return False
        name = match.group("name")
        return name in self._macro_names or (
            name == name.upper() and any(char.isalpha() for char in name)
        )


def _cpp_delimiter_depth(text: str) -> tuple[int, int]:
    parentheses = 0
    brackets = 0
    for char in text:
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
    return parentheses, brackets


def _cpp_constructor_initializer_candidate(text: str) -> bool:
    parentheses = 0
    brackets = 0
    has_colon = False
    for index, char in enumerate(text):
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif (
            char == ":"
            and parentheses == 0
            and brackets == 0
            and (index == 0 or text[index - 1] != ":")
            and (index + 1 == len(text) or text[index + 1] != ":")
        ):
            has_colon = True
    return has_colon and bool(re.search(r"(?:[A-Za-z_][A-Za-z0-9_]*|[>\]])$", text.rstrip()))


class ComplexityEngine(BaseEngine):
    """Calculates Cyclomatic Complexity and Max Nesting Depth for functions."""

    CACHE_IMPLEMENTATION_MODULES = (
        "ici.core._compile_db_paths",
        "ici.core._cpp_replay_policy",
        "ici.core.cpp_replay",
        "ici.engines._cpp_function_boundaries",
        "ici.engines._cpp_tooling",
        "ici.engines.cpp_text",
    )

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
        """Calculates Cyclomatic Complexity: 1 + number of branching points."""
        complexity = 1
        for child in walk_metric_scope(node):
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
            for child in iter_metric_children(curr, root=node):
                max_d = max(max_d, _get_depth(child, new_depth))
            return max_d

        return _get_depth(node, 0)

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
            analysis.errors.append(
                "compiler-backed C++ function boundaries were required, but "
                f"{analysis.estimated_boundaries} function(s) still needed source scanning"
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
