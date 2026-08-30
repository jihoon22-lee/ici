"""7. Dead code and unused symbol detection engine."""

import ast
import time
from pathlib import Path
from typing import Any

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.engines.base import BaseEngine


class DeadCodeEngine(BaseEngine):
    """Detect unreachable statements and unreferenced private module functions."""

    def __init__(
        self, project_root: Path | None = None, config: dict[str, Any] | None = None
    ) -> None:
        super().__init__(project_root, config)
        self._analysis_errors: list[str] = []

    def run(self) -> EngineResult:
        t0 = time.time()
        self._analysis_errors = []
        sources = self.project_python_sources()
        targets: list[InspectionTarget] = []
        proj_type = self.project_type()
        has_python_scope = bool(sources) or proj_type in ("python", "hybrid")
        if has_python_scope and sources:
            targets.extend(self._detect_python_dead_code())
        elif has_python_scope:
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="DeadCode",
                    status=EngineStatus.SKIP,
                    message="No applicable Python source files were selected; dead-code analysis was not run",
                )
            )
        else:
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="DeadCode",
                    status=EngineStatus.SKIP,
                    message="No applicable Python source files were selected; dead-code analysis was not run",
                )
            )

        issue_count = sum(
            1 for target in targets if target.status in (EngineStatus.WARN, EngineStatus.FAIL)
        )
        cfg = self.get_config("dead")
        duration = time.time() - t0
        if self._analysis_errors:
            status = EngineStatus.ERROR
            evidence = EvidenceState.NOT_RUN
            summary = "; ".join(self._analysis_errors[:3])
        elif not sources:
            status = EngineStatus.SKIP
            # Nothing was estimated: this engine only reads Python and the
            # project has none. NOT_APPLICABLE keeps it out of the gate.
            evidence = EvidenceState.NOT_APPLICABLE
            summary = "Dead-code analysis skipped: no Python source files"
        else:
            has_fail = any(target.status == EngineStatus.FAIL for target in targets)
            has_warn = any(target.status == EngineStatus.WARN for target in targets)
            status = self.evaluate_status(has_fail, has_warn, cfg.get("mode", "pass_warn"))
            evidence = EvidenceState.MEASURED
            summary = (
                "No Dead Code Detected"
                if status == EngineStatus.PASS
                else f"{issue_count} Unused Symbols / Unreachable Blocks Detected"
            )
        return self.create_result(
            name="dead",
            status=status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "dead_symbols_count": issue_count,
                "metrics_summary": f"{issue_count} dead symbols",
            },
            required=bool(cfg.get("required", True)),
            evidence=evidence,
        )

    def _detect_python_dead_code(self) -> list[InspectionTarget]:
        targets: list[InspectionTarget] = []
        modules: list[dict] = []
        source_dirs = self.project_source_dirs()
        module_paths: dict[str, str] = {}
        for py_file in self._ordered_python_sources(source_dirs):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError as err:
                self._append_analysis_error(
                    targets,
                    py_file,
                    "SyntaxError",
                    f"SyntaxError: {err.msg}",
                    err.lineno or 1,
                )
                continue
            except (OSError, UnicodeDecodeError) as err:
                self._append_analysis_error(
                    targets, py_file, "ReadError", f"Could not read Python source: {err}", 1
                )
                continue

            module_name = self._module_name(py_file, source_dirs)
            module_id = str(py_file.relative_to(self.project_root))
            module_paths.setdefault(module_name, module_id)
            modules.append(
                {
                    "path": py_file,
                    "content": content,
                    "tree": tree,
                    "module": module_name,
                    "module_id": module_id,
                    "defs": self._private_module_defs(tree),
                    "refs": self._load_names(tree),
                    "qualified_refs": self._qualified_refs(tree, module_name),
                    "imports": self._imports(
                        tree, module_name, is_package=py_file.name == "__init__.py"
                    ),
                    "exports": self._exports(tree),
                }
            )

        if self._analysis_errors:
            return targets
        referenced_keys: set[tuple[str, str]] = set()
        for module in modules:
            referenced_keys.update(self._resolve_imported_refs(module, module_paths))
        for module in modules:
            before = len(targets)
            self._append_module_targets(module, referenced_keys, targets)
            self._append_unreachable_targets(
                module["tree"], str(module["path"].relative_to(self.project_root)), targets
            )
            if len(targets) == before:
                targets.append(
                    InspectionTarget(
                        file_path=str(module["path"].relative_to(self.project_root)),
                        start_line=1,
                        target_name="DeadCode",
                        status=EngineStatus.PASS,
                        message="Python source was parsed and no dead-code findings were identified",
                    )
                )
        return targets

    def _ordered_python_sources(self, source_dirs: list[Path]) -> list[Path]:
        """Return source files in configured root precedence order."""

        sources = self.project_python_sources()
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
        for py_file in sources:
            if py_file not in seen:
                ordered.append(py_file)
        return ordered

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
            status = EngineStatus.PASS if used else EngineStatus.WARN
            message = (
                f"Private function '{name}' is referenced"
                if used
                else f"Private module-level function '{name}' is never referenced"
            )
            targets.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    target_name=f"{name}()",
                    status=status,
                    message=message,
                    snippet=ast.get_source_segment(module["content"], node) or "",
                )
            )

    @staticmethod
    def _private_module_defs(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
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
    def _qualified_refs(tree: ast.AST, module_name: str) -> set[str]:
        del module_name
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

    @staticmethod
    def _imports(
        tree: ast.Module, module_name: str, *, is_package: bool = False
    ) -> dict[str, list[tuple[str, str]]]:
        imports: dict[str, list[tuple[str, str]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = DeadCodeEngine._resolve_import_module(
                    module_name, node.module, node.level, is_package=is_package
                )
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imports.setdefault(alias.asname or alias.name, []).append((base, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    imports.setdefault(local, []).append((alias.name, ""))
        return imports

    @staticmethod
    def _resolve_import_module(
        current: str, imported: str | None, level: int, *, is_package: bool = False
    ) -> str:
        if level == 0:
            return imported or ""
        package = current.split(".") if is_package else current.split(".")[:-1]
        if level > 1:
            package = package[: -(level - 1)]
        return ".".join([*package, *(imported.split(".") if imported else [])])

    @staticmethod
    def _module_name(path: Path, source_dirs: list[Path]) -> str:
        for source_dir in source_dirs:
            try:
                relative = path.relative_to(source_dir)
            except ValueError:
                continue
            parts = list(relative.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts) or path.stem
        return path.stem

    def _resolve_imported_refs(
        self, module: dict, module_paths: dict[str, str]
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
                        for reference in module["qualified_refs"]:
                            parts = reference.split(".")
                            if parts[0] == alias and len(parts) > 1:
                                resolved.add((target_id, parts[1]))
                        if not any(
                            reference.split(".", 1)[0] == alias
                            for reference in module["qualified_refs"]
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

    def _append_unreachable_targets(
        self, tree: ast.Module, rel_path: str, targets: list[InspectionTarget]
    ) -> None:
        seen_lists: set[int] = set()
        for node in ast.walk(tree):
            for _field, value in ast.iter_fields(node):
                if not isinstance(value, list) or id(value) in seen_lists:
                    continue
                if not value or not all(isinstance(item, ast.stmt) for item in value):
                    continue
                seen_lists.add(id(value))
                self._check_unreachable(value, rel_path, targets)

    def _append_analysis_error(
        self, targets: list[InspectionTarget], path: Path, name: str, message: str, line: int
    ) -> None:
        self._analysis_errors.append(message)
        targets.append(
            InspectionTarget(
                file_path=str(path.relative_to(self.project_root)),
                start_line=line,
                target_name=name,
                status=EngineStatus.ERROR,
                message=message,
            )
        )

    def _check_unreachable(
        self, stmts: list[ast.stmt], rel_p: str, targets: list[InspectionTarget]
    ) -> None:
        has_terminator = False
        for stmt in stmts:
            if has_terminator:
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=stmt.lineno,
                        target_name="UnreachableCode",
                        status=EngineStatus.WARN,
                        message="Unreachable code statement detected after terminal return/raise",
                    )
                )
                break
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                has_terminator = True
