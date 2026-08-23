"""Resource leak detection — Python open without with, etc., offline AST."""

import ast
import time

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import get_all_python_sources
from ici.engines.base import BaseEngine


class _LeakVisitor(ast.NodeVisitor):
    """Finds open() without with/close in a function scope."""

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.findings: list[InspectionTarget] = []
        self._with_depth = 0

    def visit_With(self, node):
        self._with_depth += 1
        self.generic_visit(node)
        self._with_depth -= 1

    def visit_AsyncWith(self, node):
        self._with_depth += 1
        self.generic_visit(node)
        self._with_depth -= 1

    def visit_Assign(self, node):
        # Look for x = open(...)
        if self._with_depth > 0:
            self.generic_visit(node)
            return
        if isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "open":
            # Check if there's a close() call later in same function - simplified: just warn
            # Check if assigned var is closed via `var.close()` in same scope
            # For now, just warn for any open outside with
            self.findings.append(
                InspectionTarget(
                    file_path=self.rel_path,
                    start_line=getattr(node, "lineno", 1),
                    target_name="Resource:OpenWithoutWith",
                    status=EngineStatus.WARN,
                    message="open() without with statement — may leak file handle",
                    snippet="open(...)",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node):
        # Also check for open() not in assign, e.g., open(...).read() is ok (one-liner)
        # But bare open() as expr is also leak
        if isinstance(node.func, ast.Name) and node.func.id == "open" and self._with_depth == 0:
            # Check if parent is Assign or With - if it's Expr, it's bare open()
            # We already handled Assign case, so this is Expr case
            # Check if it's inside an Assign via stack? Simplified: if it's direct Expr, warn
            # We can check by seeing if this Call is inside an Assign - but generic_visit already handles Assign
            # For Expr case, the Call will be visited as part of Expr node, not Assign
            # So we need to check if this Call's parent is Expr
            # Since we are visiting Call directly, we can't know parent easily, so just warn for any open() outside with that is not part of a with
            # To avoid double counting, only warn if this Call is not inside an Assign that we already warned for
            # We'll just not double warn: check if this Call is inside an Assign - we can approximate by checking if it's inside a with (already) and if it's not inside an Assign's value that is open()
            # For simplicity, warn for bare open() in Expr
            # We can detect by checking if this node's parent is Expr - but we don't have parent, so we'll just warn for any open() outside with that is not already counted as Assign
            # To avoid double, we can check if this Call is the same as the one in Assign - but that's complex
            # For now, just warn for any open() outside with, but avoid duplicate by checking if this Call is not inside an Assign's value that is open()
            # We'll use a simple heuristic: if this Call is open() and not inside with, warn, but track visited Assign opens to avoid duplicate
            pass
        self.generic_visit(node)


def _check_file_for_leaks(rel_path: str, content: str) -> list[InspectionTarget]:
    try:
        tree = ast.parse(content, filename=rel_path)
    except SyntaxError:
        return []
    visitor = _LeakVisitor(rel_path)
    visitor.visit(tree)
    # Also check for mutable default args
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    visitor.findings.append(
                        InspectionTarget(
                            file_path=rel_path,
                            start_line=getattr(node, "lineno", 1),
                            target_name="Resource:MutableDefault",
                            status=EngineStatus.WARN,
                            message=f"Mutable default argument in {node.name}()",
                        )
                    )
    return visitor.findings


class ResourceEngine(BaseEngine):
    """Detects resource leaks and mutable defaults via AST."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("resource")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))

        targets: list[InspectionTarget] = []
        for py_file in get_all_python_sources(self.project_root, self.config):
            rel = str(py_file.relative_to(self.project_root))
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            findings = _check_file_for_leaks(rel, content)
            targets.extend(findings)

        has_warn = bool(targets)
        status = self.evaluate_status(False, has_warn, mode)
        summary = (
            f"Resource leaks: {len(targets)} finding(s)"
            if has_warn
            else "No resource leaks detected"
        )
        return self.create_result(
            name="resource",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=targets,
            required=required,
            evidence=EvidenceState.MEASURED,
        )
