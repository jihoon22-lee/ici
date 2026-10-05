"""Cycle detection primitives — Python import and C++ include graphs.

Extracted from the stable ``ici.engines.cycle`` shell: Tarjan SCC, include
resolution heuristics, and the exact/heuristic C++ analysis normalizer. The
next path's ``ici.languages.cycles`` measures through these; the remaining
stable ``CycleEngine`` imports them back.
"""

import ast
import re
import sys
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from ici.core.project import (
    _iter_project_files,
    get_all_python_sources,
    get_source_dirs,
)

_INCLUDE_RE = re.compile(r'#include\s*["]([^"]+)["]')
_Node = TypeVar("_Node", bound=Hashable)


def _python_module_name(py_file: Path, source_dirs: list[Path]) -> str | None:
    """Map a .py file to its dotted module name relative to its source dir."""
    for src_dir in source_dirs:
        try:
            rel = py_file.relative_to(src_dir)
        except ValueError:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        # Use only the last N parts as the module name (relative to src_dir root)
        return ".".join(parts) if parts else None
    return None


def _module_index(
    all_sources: list[Path],
    source_dirs: list[Path],
) -> tuple[dict[Path, str], dict[str, Path]]:
    """Map each in-project source to its module name, and back."""

    file_to_module: dict[Path, str] = {}
    module_to_file: dict[str, Path] = {}
    for py_file in all_sources:
        mod_name = _python_module_name(py_file, source_dirs)
        if mod_name:
            file_to_module[py_file] = mod_name
            module_to_file[mod_name] = py_file
    return file_to_module, module_to_file


def _imported_module_names(tree: ast.AST) -> list[str]:
    """Return every module name this tree imports, in source order."""

    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
    return targets


def _resolved_import_targets(target_mod: str, module_to_file: dict[str, Path]) -> list[str]:
    """Resolve one imported name to in-project modules.

    Try an exact match, then a suffix match so flat or relative-style imports
    still connect ("b" matches "pkg.b"). The suffix fallback must never claim a
    stdlib name — "html" or "json" — as an in-project module that merely shares
    its last segment.
    """

    if target_mod in module_to_file:
        return [target_mod]
    if target_mod.split(".")[0] in sys.stdlib_module_names:
        return []
    return [
        known_mod
        for known_mod in module_to_file
        if known_mod.endswith("." + target_mod) or known_mod == target_mod
    ]


def _build_python_graph(
    project_root: Path,
    source_dirs: list[Path] | None = None,
    all_sources: list[Path] | None = None,
) -> tuple[dict[str, set[str]], dict[str, Path]]:
    """Build module -> imported-module graph for in-project Python sources."""
    if source_dirs is None:
        source_dirs = get_source_dirs(project_root)
    if all_sources is None:
        all_sources = get_all_python_sources(project_root)

    file_to_module, module_to_file = _module_index(all_sources, source_dirs)

    graph: dict[str, set[str]] = {mod: set() for mod in module_to_file}
    for py_file in all_sources:
        importer = file_to_module.get(py_file)
        if not importer:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for target_mod in _imported_module_names(tree):
            graph[importer].update(_resolved_import_targets(target_mod, module_to_file))
    return graph, module_to_file


_CPP_AND_HEADER_SUFFIXES = (".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh")


@dataclass(frozen=True)
class _IncludeDiagnostic:
    """A quoted include the project-only heuristic could not resolve."""

    source: Path
    line: int
    include: str
    candidates: tuple[Path, ...]
    snippet: str

    @property
    def kind(self) -> str:
        return "ambiguous" if self.candidates else "unresolved"


def _iter_cpp_and_headers(project_root: Path, config: dict[str, Any] | None = None) -> list[Path]:
    """Collect C++ sources and headers from the project's declared scope.

    This used to walk the entire repository, which made cycle the only C++
    engine that ignored ``project.source_dirs`` — lint, dup, complexity and
    exception all go through ``get_all_cpp_sources``. The inconsistency was
    invisible until something deliberate lived outside the source directories:
    a C++ fixture under ``examples/`` was reported as a real finding in this
    project's own verification run, because no other engine could see it and
    this one could.

    Headers are the reason this cannot simply call ``get_all_cpp_sources``:
    that returns implementation files only, and an include cycle is mostly a
    property of headers. So the scan covers each source directory plus a
    top-level ``include/``, matching where ``get_all_cpp_includes`` already
    looks for public headers.
    """
    roots = list(get_source_dirs(project_root, config))
    include_dir = project_root / "include"
    if include_dir.is_dir():
        roots.append(include_dir)

    results: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for path in _iter_project_files(root, project_root, _CPP_AND_HEADER_SUFFIXES):
            if path not in seen:
                seen.add(path)
                results.append(path)
    return sorted(results)


def _include_parts(inc_name: str) -> tuple[str, ...]:
    """Return portable, safe components from a compiler-style include path."""

    normalized = inc_name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not parts or normalized.startswith("/") or ".." in parts:
        return ()
    return tuple(part for part in parts if part not in ("", "."))


def _include_matches(candidate: Path, wanted: tuple[str, ...]) -> bool:
    """Whether ``candidate`` ends with every component named by an include."""

    parts = candidate.parts
    return bool(wanted) and len(parts) >= len(wanted) and parts[-len(wanted) :] == wanted


def _matching_includes(inc_name: str, files: list[Path]) -> list[Path]:
    wanted = _include_parts(inc_name)
    if not wanted:
        return []
    return [candidate for candidate in files if _include_matches(candidate, wanted)]


def _resolve_include(inc_name: str, files: list[Path]) -> Path | None:
    """Resolve a quoted include only when its full path suffix is unique.

    Directory components are useful evidence: ``core/format.hpp`` can name one
    project file even when several files are called ``format.hpp``. Conversely,
    a bare basename with multiple matches remains unresolved rather than being
    guessed. This is deliberately a project-file heuristic, not compiler-exact
    ``-I`` resolution; compilation context supersedes it in the I3 roadmap.
    """

    matches = _matching_includes(inc_name, files)
    return matches[0] if len(matches) == 1 else None


def _build_cpp_graph(
    project_root: Path,
    config: dict[str, Any] | None = None,
    all_files: list[Path] | None = None,
) -> tuple[
    dict[Path, set[Path]],
    dict[Path, Path],
    list[_IncludeDiagnostic],
    int,
]:
    """Build file -> included-file graph including headers.

    ``#include "..."`` is resolved by matching the complete path suffix named
    by the source, rather than throwing away directories and comparing only the
    basename. A non-unique or missing project-file match is preserved as a
    location-bearing diagnostic. This remains a heuristic because it does not
    yet model compiler include search order or generated headers.
    """
    if all_files is None:
        all_files = _iter_cpp_and_headers(project_root, config)

    graph: dict[Path, set[Path]] = {}
    known: dict[Path, Path] = {}
    diagnostics: list[_IncludeDiagnostic] = []
    resolved_count = 0
    for f in all_files:
        resolved_f = f.resolve()
        known[resolved_f] = f
        graph[resolved_f] = set()
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _INCLUDE_RE.finditer(content):
            inc_name = match.group(1)
            target = _resolve_include(inc_name, all_files)
            candidates = [target] if target is not None else _matching_includes(inc_name, all_files)
            if target:
                resolved_count += 1
                if target.resolve() != resolved_f:
                    graph[resolved_f].add(target.resolve())
            elif target is None:
                line = content.count("\n", 0, match.start()) + 1
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end < 0:
                    line_end = len(content)
                diagnostics.append(
                    _IncludeDiagnostic(
                        source=f,
                        line=line,
                        include=inc_name,
                        candidates=tuple(candidates),
                        snippet=content[line_start:line_end].strip(),
                    )
                )
    return graph, known, diagnostics, resolved_count


def _find_cycles_tarjan(graph: dict[_Node, set[_Node]]) -> list[list[_Node]]:
    """Iterative Tarjan SCC — return components with >1 member (cycles).

    Iterative (stack-based) on purpose: a recursive implementation blows
    Python's default recursion limit on import/include graphs with a few
    hundred nodes in a single chain, crashing the whole verification suite.
    """
    index_counter = 0
    stack: list[_Node] = []
    on_stack: set[_Node] = set()
    indices: dict[_Node, int] = {}
    lowlink: dict[_Node, int] = {}
    cycles: list[list[_Node]] = []

    for root in graph:
        if root in indices:
            continue

        indices[root] = lowlink[root] = index_counter
        index_counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(graph.get(root, set())))]

        while work:
            node, neighbors = work[-1]
            descended = False
            for dep in neighbors:
                if dep not in indices:
                    indices[dep] = lowlink[dep] = index_counter
                    index_counter += 1
                    stack.append(dep)
                    on_stack.add(dep)
                    work.append((dep, iter(graph.get(dep, ()))))
                    descended = True
                    break
                elif dep in on_stack:
                    lowlink[node] = min(lowlink[node], indices[dep])
            if descended:
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

            if lowlink[node] == indices[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in graph.get(node, set()):
                    cycles.append(component)

    return cycles
