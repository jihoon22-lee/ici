"""Bounded Python AST heuristics used by the dead-code engine."""

from __future__ import annotations

import ast
from pathlib import Path

from ici.core.models import EngineStatus, InspectionTarget
from ici.engines._source_inputs import AnalysisSource


def ordered_python_sources(source_dirs: list[Path], sources: list[Path]) -> list[Path]:
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
    ordered.extend(py_file for py_file in sources if py_file not in seen)
    return ordered


class _PythonDeadAnalyzer:
    def __init__(self, project_root: Path, source_dirs: list[Path]) -> None:
        self.project_root = project_root
        self.source_dirs = source_dirs
        self.errors: list[str] = []

    def analyze(self, sources: tuple[AnalysisSource, ...]) -> list[InspectionTarget]:
        targets: list[InspectionTarget] = []
        modules: list[dict] = []
        module_paths: dict[str, str] = {}
        for source in sources:
            py_file = source.path
            try:
                tree = ast.parse(source.text, filename=str(py_file))
            except SyntaxError as err:
                self._append_analysis_error(
                    targets,
                    py_file,
                    "SyntaxError",
                    f"SyntaxError: {err.msg}",
                    err.lineno or 1,
                )
                continue
            module_name = self._module_name(py_file)
            module_paths.setdefault(module_name, source.file_path)
            modules.append(
                {
                    "path": py_file,
                    "content": source.text,
                    "tree": tree,
                    "module": module_name,
                    "module_id": source.file_path,
                    "defs": self._private_module_defs(tree),
                    "refs": self._load_names(tree),
                    "qualified_refs": self._qualified_refs(tree),
                    "imports": self._imports(
                        tree,
                        module_name,
                        is_package=py_file.name == "__init__.py",
                    ),
                    "exports": self._exports(tree),
                }
            )

        if self.errors:
            return targets
        referenced_keys: set[tuple[str, str]] = set()
        for module in modules:
            referenced_keys.update(self._resolve_imported_refs(module, module_paths))
        for module in modules:
            before = len(targets)
            self._append_module_targets(module, referenced_keys, targets)
            self._append_unreachable_targets(
                module["tree"],
                str(module["path"].relative_to(self.project_root)),
                targets,
            )
            if len(targets) == before:
                targets.append(
                    InspectionTarget(
                        file_path=str(module["path"].relative_to(self.project_root)),
                        start_line=1,
                        target_name="DeadCode",
                        status=EngineStatus.PASS,
                        message=(
                            "Python source was parsed and no dead-code findings were identified"
                        ),
                    )
                )
        return targets

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
            targets.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    target_name=f"{name}()",
                    status=EngineStatus.PASS if used else EngineStatus.WARN,
                    message=(
                        f"Private function '{name}' is referenced"
                        if used
                        else f"Private module-level function '{name}' is never referenced"
                    ),
                    snippet=ast.get_source_segment(module["content"], node) or "",
                )
            )

    @staticmethod
    def _private_module_defs(
        tree: ast.Module,
    ) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
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
    def _qualified_refs(tree: ast.AST) -> set[str]:
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

    @classmethod
    def _imports(
        cls,
        tree: ast.Module,
        module_name: str,
        *,
        is_package: bool = False,
    ) -> dict[str, list[tuple[str, str]]]:
        imports: dict[str, list[tuple[str, str]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = cls._resolve_import_module(
                    module_name,
                    node.module,
                    node.level,
                    is_package=is_package,
                )
                for alias in node.names:
                    if alias.name != "*":
                        imports.setdefault(alias.asname or alias.name, []).append(
                            (base, alias.name)
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    imports.setdefault(local, []).append((alias.name, ""))
        return imports

    @staticmethod
    def _resolve_import_module(
        current: str,
        imported: str | None,
        level: int,
        *,
        is_package: bool = False,
    ) -> str:
        if level == 0:
            return imported or ""
        package = current.split(".") if is_package else current.split(".")[:-1]
        if level > 1:
            package = package[: -(level - 1)]
        return ".".join([*package, *(imported.split(".") if imported else [])])

    def _module_name(self, path: Path) -> str:
        for source_dir in self.source_dirs:
            try:
                relative = path.relative_to(source_dir)
            except ValueError:
                continue
            parts = list(relative.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts) or path.stem
        return path.stem

    @staticmethod
    def _resolve_imported_refs(
        module: dict,
        module_paths: dict[str, str],
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
                        qualified_refs = module["qualified_refs"]
                        for reference in qualified_refs:
                            parts = reference.split(".")
                            if parts[0] == alias and len(parts) > 1:
                                resolved.add((target_id, parts[1]))
                        if not any(
                            reference.split(".", 1)[0] == alias for reference in qualified_refs
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

    @staticmethod
    def _append_unreachable_targets(
        tree: ast.Module,
        rel_path: str,
        targets: list[InspectionTarget],
    ) -> None:
        seen_lists: set[int] = set()
        for node in ast.walk(tree):
            for _field, value in ast.iter_fields(node):
                if not isinstance(value, list) or id(value) in seen_lists:
                    continue
                if not value or not all(isinstance(item, ast.stmt) for item in value):
                    continue
                seen_lists.add(id(value))
                _PythonDeadAnalyzer._check_unreachable(value, rel_path, targets)

    def _append_analysis_error(
        self,
        targets: list[InspectionTarget],
        path: Path,
        name: str,
        message: str,
        line: int,
    ) -> None:
        self.errors.append(message)
        targets.append(
            InspectionTarget(
                file_path=str(path.relative_to(self.project_root)),
                start_line=line,
                target_name=name,
                status=EngineStatus.ERROR,
                message=message,
            )
        )

    @staticmethod
    def _check_unreachable(
        stmts: list[ast.stmt],
        rel_path: str,
        targets: list[InspectionTarget],
    ) -> None:
        has_terminator = False
        for stmt in stmts:
            if has_terminator:
                targets.append(
                    InspectionTarget(
                        file_path=rel_path,
                        start_line=stmt.lineno,
                        end_line=getattr(stmt, "end_lineno", stmt.lineno),
                        start_column=getattr(stmt, "col_offset", 0) + 1,
                        end_column=getattr(stmt, "end_col_offset", None),
                        target_name="UnreachableCode",
                        status=EngineStatus.WARN,
                        message=("Unreachable code statement detected after terminal return/raise"),
                    )
                )
                break
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                has_terminator = True


def analyze_python_dead_code(
    project_root: Path,
    source_dirs: list[Path],
    sources: tuple[AnalysisSource, ...],
) -> tuple[list[InspectionTarget], list[str]]:
    """Return Python heuristic targets and parse errors for one immutable snapshot."""

    analyzer = _PythonDeadAnalyzer(project_root, source_dirs)
    return analyzer.analyze(sources), analyzer.errors


__all__ = ["analyze_python_dead_code", "ordered_python_sources"]
