"""Metadata, syntax, and standard-library API checks for Python runtimes."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.core.models import EngineStatus, InspectionTarget
from ici.engines._python_resource_scopes import (
    collect_import_aliases,
    collect_scope_bindings,
)

MAX_PYPROJECT_BYTES = 2 * 1024 * 1024
MAX_COMPAT_AST_NODES = 100_000
MAX_COMPAT_TOTAL_AST_NODES = 1_000_000
_VERSION_RE = re.compile(r"Python\s+(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)(?![A-Za-z0-9])")
_TARGET_RE = re.compile(r"^(\d+)\.(\d+)$")
_INTRODUCED_APIS = {
    "datetime.UTC": (3, 11),
    "enum.StrEnum": (3, 11),
    "itertools.batched": (3, 12),
    "pathlib.Path.walk": (3, 12),
    "sys.monitoring": (3, 12),
    "tomllib": (3, 11),
    "typing.Never": (3, 11),
    "typing.NotRequired": (3, 11),
    "typing.Required": (3, 11),
    "typing.Self": (3, 11),
    "typing.TypeAliasType": (3, 12),
}


class PythonMetadataError(ValueError):
    """Raised when present project metadata cannot be verified safely."""


@dataclass(frozen=True)
class PythonProjectMetadata:
    requires_python: str
    import_names: tuple[str, ...]
    pyproject_present: bool


@dataclass(frozen=True)
class StaticCompatibilityAnalysis:
    targets: tuple[InspectionTarget, ...]
    ast_nodes: int


def parse_runtime_version(output: str) -> Version | None:
    """Parse the first stable CPython/PyPy-style version from ``-VV`` output."""

    match = _VERSION_RE.search(output)
    if match is None:
        return None
    try:
        return Version(match.group(1))
    except InvalidVersion:
        return None


def parse_target_version(value: str) -> tuple[int, int] | None:
    match = _TARGET_RE.fullmatch(value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def requires_python_allows(specifier: str, version: Version) -> bool:
    try:
        return SpecifierSet(specifier).contains(version, prereleases=True)
    except InvalidSpecifier as error:
        raise PythonMetadataError(f"project.requires-python is invalid: {specifier!r}") from error


def inferred_target_version(specifier: str) -> tuple[int, int] | None:
    """Return the first supported CPython minor in ici's bounded support horizon."""

    if not specifier:
        return None
    minor_candidates = (
        (major, minor) for major in range(3, 5) for minor in range(7 if major == 3 else 0, 21)
    )
    for major, minor in minor_candidates:
        if any(
            requires_python_allows(specifier, Version(f"{major}.{minor}.{patch}"))
            for patch in range(100)
        ):
            return major, minor
    return None


def _module_name(value: str) -> str | None:
    parts = value.split(".")
    return value if parts and all(part.isidentifier() for part in parts) else None


def _declared_import_names(project: dict[str, Any]) -> list[str]:
    raw = project.get("import-names", [])
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.split(";", 1)[0].strip()
        if _module_name(name) is not None:
            names.append(name)
    return names


def _script_modules(project: dict[str, Any]) -> list[str]:
    scripts = project.get("scripts", {})
    if not isinstance(scripts, dict):
        return []
    names: list[str] = []
    for value in scripts.values():
        if not isinstance(value, str):
            continue
        module = value.split(":", 1)[0].strip()
        if _module_name(module) is not None:
            names.append(module)
    return names


def _auto_import_names(root: Path, source_files: list[Path]) -> list[str]:
    names: set[str] = set()
    for path in source_files:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        parts = relative.parts
        if not parts:
            continue
        if parts[0] in {"src", "lib", "python", "packages"} and len(parts) > 1:
            parts = parts[1:]
        first = parts[0]
        if first.endswith(".py"):
            candidate = Path(first).stem
        elif (root / relative.parts[0] / first / "__init__.py").is_file():
            candidate = first
        else:
            candidate = first
        if candidate != "__init__" and _module_name(candidate) is not None:
            names.add(candidate)
    return sorted(names)[:64]


def load_python_metadata(root: Path, source_files: list[Path]) -> PythonProjectMetadata:
    path = root / "pyproject.toml"
    if not path.exists():
        return PythonProjectMetadata("", tuple(_auto_import_names(root, source_files)), False)
    try:
        payload = _read_bounded_regular(path, MAX_PYPROJECT_BYTES, containment_root=root)
        document = tomli.loads(payload.decode("utf-8", errors="strict"))
    except (FileNotFoundError, UnicodeDecodeError, _ReadError, tomli.TOMLDecodeError) as error:
        raise PythonMetadataError("pyproject.toml could not be read and parsed safely") from error
    project = document.get("project", {})
    if not isinstance(project, dict):
        raise PythonMetadataError("pyproject.toml [project] must be a table")
    requires_python = project.get("requires-python", "")
    if not isinstance(requires_python, str):
        raise PythonMetadataError("project.requires-python must be a string")
    if requires_python:
        try:
            SpecifierSet(requires_python)
        except InvalidSpecifier as error:
            raise PythonMetadataError(
                f"project.requires-python is invalid: {requires_python!r}"
            ) from error
    imports = [*_declared_import_names(project), *_script_modules(project)]
    if not imports:
        imports = _auto_import_names(root, source_files)
    return PythonProjectMetadata(requires_python, tuple(sorted(set(imports))[:64]), True)


_LEXICAL_SCOPES = (
    ast.Module,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


@dataclass(frozen=True)
class _ScopeIndex:
    node_scope: dict[ast.AST, ast.AST]
    parents: dict[ast.AST, ast.AST | None]
    aliases: dict[ast.AST, dict[str, str]]
    bindings: dict[ast.AST, frozenset[str]]


def _comprehension_bindings(scope: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        for generator in scope.generators:
            names.update(
                node.id
                for node in ast.walk(generator.target)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
            )
    elif isinstance(scope, ast.Lambda):
        names.update(argument.arg for argument in scope.args.posonlyargs)
        names.update(argument.arg for argument in scope.args.args)
        names.update(argument.arg for argument in scope.args.kwonlyargs)
        if scope.args.vararg:
            names.add(scope.args.vararg.arg)
        if scope.args.kwarg:
            names.add(scope.args.kwarg.arg)
    return names


def _scope_index(tree: ast.AST) -> _ScopeIndex:
    node_scope: dict[ast.AST, ast.AST] = {}
    parents: dict[ast.AST, ast.AST | None] = {tree: None}
    scopes: list[ast.AST] = [tree]

    def visit(node: ast.AST, current: ast.AST) -> None:
        if node is not tree and isinstance(node, _LEXICAL_SCOPES):
            parents[node] = current
            scopes.append(node)
            current = node
        node_scope[node] = current
        for child in ast.iter_child_nodes(node):
            visit(child, current)

    visit(tree, tree)
    aliases = {scope: collect_import_aliases(scope) for scope in scopes}
    bindings = {
        scope: frozenset(set(collect_scope_bindings(scope)) | _comprehension_bindings(scope))
        for scope in scopes
    }
    return _ScopeIndex(node_scope, parents, aliases, bindings)


def _resolve_name(name: str, scope: ast.AST, index: _ScopeIndex) -> str:
    current: ast.AST | None = scope
    while current is not None:
        # A parameter/assignment/function declaration shadows a same-named
        # import.  Refuse the alias rather than report a false compatibility
        # problem when module-level control flow makes the exact point unclear.
        if name in index.bindings[current]:
            return ""
        alias = index.aliases[current].get(name)
        if alias:
            return alias
        parent = index.parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and isinstance(
            parent, ast.ClassDef
        ):
            parent = index.parents[parent]
        current = parent
    return ""


def _qualified_name(node: ast.AST, index: _ScopeIndex) -> str:
    if isinstance(node, ast.Name):
        return _resolve_name(node.id, index.node_scope[node], index)
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value, index)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _imported_apis(node: ast.AST) -> tuple[tuple[str, ast.AST], ...]:
    if isinstance(node, ast.Import):
        return tuple((item.name, node) for item in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return tuple(
            (f"{node.module}.{item.name}", node) for item in node.names if item.name != "*"
        )
    return ()


def _node_target(
    file_path: str,
    node: ast.AST,
    rule: str,
    message: str,
) -> InspectionTarget:
    column = getattr(node, "col_offset", None)
    end_column = getattr(node, "end_col_offset", None)
    return InspectionTarget(
        file_path=file_path,
        start_line=max(1, getattr(node, "lineno", 1)),
        end_line=getattr(node, "end_lineno", None),
        start_column=column + 1 if isinstance(column, int) else None,
        end_column=end_column if isinstance(end_column, int) and end_column > 0 else None,
        target_name=f"Compatibility:{rule}",
        status=EngineStatus.WARN,
        message=message,
    )


def _syntax_floor_target(
    file_path: str,
    text: str,
    target_version: tuple[int, int] | None,
) -> InspectionTarget | None:
    current_version = (sys.version_info.major, sys.version_info.minor)
    if target_version is None or target_version > current_version:
        return None
    try:
        ast.parse(text, filename=file_path, feature_version=target_version)
    except SyntaxError as error:
        return InspectionTarget(
            file_path=file_path,
            start_line=max(1, error.lineno or 1),
            start_column=error.offset,
            target_name="Compatibility:SyntaxFloor",
            status=EngineStatus.WARN,
            message=(
                f"Syntax is not accepted by configured Python "
                f"{target_version[0]}.{target_version[1]} floor"
            ),
        )
    return None


def _api_candidates(
    node: ast.AST,
    scope_index: _ScopeIndex,
) -> tuple[tuple[str, ast.AST], ...]:
    imported = _imported_apis(node)
    if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(
        getattr(node, "ctx", None), ast.Load
    ):
        return (*imported, (_qualified_name(node, scope_index), node))
    return imported


def _api_floor_targets(
    file_path: str,
    tree: ast.AST,
    target_version: tuple[int, int] | None,
) -> list[InspectionTarget]:
    if target_version is None:
        return []
    scope_index = _scope_index(tree)
    blocked_imports = {
        qualified
        for node in ast.walk(tree)
        for qualified, _location in _imported_apis(node)
        if (introduced := _INTRODUCED_APIS.get(qualified)) is not None
        and introduced > target_version
    }
    emitted: set[tuple[int, int, str]] = set()
    targets: list[InspectionTarget] = []
    for node in ast.walk(tree):
        imported = bool(_imported_apis(node))
        for qualified, location_node in _api_candidates(node, scope_index):
            introduced = _INTRODUCED_APIS.get(qualified)
            key = (
                max(1, getattr(location_node, "lineno", 1)),
                getattr(location_node, "col_offset", 0),
                qualified,
            )
            if (
                (not imported and qualified in blocked_imports)
                or introduced is None
                or introduced <= target_version
                or key in emitted
            ):
                continue
            emitted.add(key)
            targets.append(
                _node_target(
                    file_path,
                    location_node,
                    "StandardLibraryFloor",
                    f"{qualified} requires Python {introduced[0]}.{introduced[1]} or newer",
                )
            )
    return targets


def analyze_static_compatibility(
    file_path: str,
    text: str,
    target_version: tuple[int, int] | None,
) -> StaticCompatibilityAnalysis:
    tree = ast.parse(text, filename=file_path)
    ast_nodes = sum(1 for _ in ast.walk(tree))
    if ast_nodes > MAX_COMPAT_AST_NODES:
        raise PythonMetadataError(
            f"Python compatibility AST exceeds the bounded limit ({MAX_COMPAT_AST_NODES} nodes)"
        )
    targets = _api_floor_targets(file_path, tree, target_version)
    syntax_target = _syntax_floor_target(file_path, text, target_version)
    if syntax_target is not None:
        targets.append(syntax_target)
    targets.sort(key=lambda item: (item.start_line, item.start_column or 0, item.target_name))
    return StaticCompatibilityAnalysis(tuple(targets), ast_nodes)
