"""Cyclic dependency detection engine — Python imports and C++ includes."""

import ast
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import (
    _iter_project_files,
    get_all_python_sources,
    get_source_dirs,
)
from ici.engines.base import BaseEngine

_INCLUDE_RE = re.compile(r'#include\s*["]([^"]+)["]')
_MAX_REPORTED_DEFAULT = 20


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


def _build_python_graph(
    project_root: Path,
) -> tuple[dict[str, set[str]], dict[str, Path]]:
    """Build module -> imported-module graph for in-project Python sources."""
    from ici.core.project import get_source_dirs

    source_dirs = get_source_dirs(project_root)
    all_sources = get_all_python_sources(project_root)

    file_to_module: dict[Path, str] = {}
    module_to_file: dict[str, Path] = {}
    for py_file in all_sources:
        mod_name = _python_module_name(py_file, source_dirs)
        if mod_name:
            file_to_module[py_file] = mod_name
            module_to_file[mod_name] = py_file

    graph: dict[str, set[str]] = {mod: set() for mod in module_to_file}
    for py_file in all_sources:
        importer = file_to_module.get(py_file)
        if not importer:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                targets = [node.module]
            for target_mod in targets:
                # Try exact match, then suffix match (e.g., "b" matches "pkg.b").
                # The suffix fallback exists for flat/relative-style imports, but
                # must never claim a stdlib name (e.g. "html", "json") as a match
                # for an in-project module that merely shares its last segment.
                if target_mod in module_to_file:
                    graph[importer].add(target_mod)
                elif target_mod.split(".")[0] not in sys.stdlib_module_names:
                    for known_mod in module_to_file:
                        if known_mod.endswith("." + target_mod) or known_mod == target_mod:
                            graph[importer].add(known_mod)
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
    project_root: Path, config: dict[str, Any] | None = None
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


def _find_cycles_tarjan(graph: dict) -> list[list]:
    """Iterative Tarjan SCC — return components with >1 member (cycles).

    Iterative (stack-based) on purpose: a recursive implementation blows
    Python's default recursion limit on import/include graphs with a few
    hundred nodes in a single chain, crashing the whole verification suite.
    """
    index_counter = 0
    stack: list = []
    on_stack: set = set()
    indices: dict = {}
    lowlink: dict = {}
    cycles: list[list] = []

    for root in graph:
        if root in indices:
            continue

        indices[root] = lowlink[root] = index_counter
        index_counter += 1
        stack.append(root)
        on_stack.add(root)
        work: list[tuple] = [(root, iter(graph.get(root, ())))]

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


def _find_actual_cycle_path(start, component: set, graph: dict) -> list:
    """DFS within an SCC's induced subgraph for one real edge-following cycle.

    Tarjan's SCC output is an unordered set of mutually-reachable nodes, not a
    path — joining it with "->" as-is renders edges that were never actually
    imported/included. This walks real edges (restricted to the component) and
    backtracks until it finds one closed loop back to ``start``.
    """
    path = [start]
    visiting = {start}
    work = [(start, iter(n for n in graph.get(start, ()) if n in component))]

    while work:
        node, neighbors = work[-1]
        advanced = False
        for nxt in neighbors:
            if nxt == start:
                return path
            if nxt not in visiting:
                visiting.add(nxt)
                path.append(nxt)
                work.append((nxt, iter(n for n in graph.get(nxt, ()) if n in component)))
                advanced = True
                break
        if advanced:
            continue
        work.pop()
        visiting.discard(node)
        path.pop()

    # Unreachable for a genuine SCC (strong connectivity guarantees a path
    # back to start exists); kept as a defensive fallback only.
    return sorted(component, key=str)


class CycleEngine(BaseEngine):
    """Detects cyclic dependencies in Python imports and C++ includes."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("cycle")
        mode = cfg.get("mode", "pass_warn_fail")
        max_reported = int(cfg.get("max_reported", _MAX_REPORTED_DEFAULT))

        targets: list[InspectionTarget] = []
        total_cycles = 0

        py_graph, py_modules = _build_python_graph(self.project_root)
        py_cycles = _find_cycles_tarjan(py_graph)
        for component in py_cycles[:max_reported]:
            start = min(component)
            cycle_nodes = _find_actual_cycle_path(start, set(component), py_graph)
            names = " -> ".join([*cycle_nodes, cycle_nodes[0]])
            first_mod = cycle_nodes[0]
            first_file = py_modules.get(first_mod)
            if first_file is None:
                continue
            try:
                rel_path = str(first_file.relative_to(self.project_root))
            except ValueError:
                rel_path = str(first_file)
            targets.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=1,
                    target_name=f"Cycle:{len(cycle_nodes)}",
                    status=EngineStatus.WARN,
                    message=f"Python import cycle ({len(cycle_nodes)} modules): {names}",
                    metrics={"modules": cycle_nodes},
                )
            )
        total_cycles += len(py_cycles)

        cpp_graph, cpp_files_map, cpp_include_diagnostics, resolved_cpp_includes = _build_cpp_graph(
            self.project_root, self.config
        )
        cpp_cycles = _find_cycles_tarjan(cpp_graph)
        for component in cpp_cycles[:max_reported]:
            start = min(component, key=str)
            cycle_files = _find_actual_cycle_path(start, set(component), cpp_graph)
            names = " -> ".join(f.name for f in [*cycle_files, cycle_files[0]])
            first_file = cpp_files_map.get(cycle_files[0])
            if first_file is None:
                first_file = cycle_files[0]
            try:
                first_rel = str(first_file.relative_to(self.project_root))
            except ValueError:
                first_rel = str(first_file)
            targets.append(
                InspectionTarget(
                    file_path=first_rel,
                    start_line=1,
                    target_name=f"CppCycle:{len(cycle_files)}",
                    status=EngineStatus.WARN,
                    message=f"C++ include cycle ({len(cycle_files)} files): {names}",
                    metrics={"files": [str(f) for f in cycle_files]},
                )
            )
        total_cycles += len(cpp_cycles)

        for diagnostic in cpp_include_diagnostics[:max_reported]:
            try:
                source_path = str(diagnostic.source.relative_to(self.project_root))
            except ValueError:
                source_path = str(diagnostic.source)
            candidates: list[str] = []
            for candidate in diagnostic.candidates:
                try:
                    candidates.append(str(candidate.relative_to(self.project_root)))
                except ValueError:
                    candidates.append(str(candidate))
            if diagnostic.kind == "ambiguous":
                target_name = "CppIncludeAmbiguous"
                message = (
                    f'Quoted include "{diagnostic.include}" matches '
                    f"{len(candidates)} project files; no dependency edge was guessed"
                )
            else:
                target_name = "CppIncludeUnresolved"
                message = (
                    f'Quoted include "{diagnostic.include}" has no project-file match; '
                    "it may be generated or supplied by compiler include paths"
                )
            targets.append(
                InspectionTarget(
                    file_path=source_path,
                    start_line=diagnostic.line,
                    target_name=target_name,
                    status=EngineStatus.WARN,
                    message=message,
                    snippet=diagnostic.snippet,
                    metrics={
                        "include": diagnostic.include,
                        "candidates": candidates,
                        "resolution": "unique_project_path_suffix",
                    },
                )
            )

        ambiguous_cpp_includes = sum(
            diagnostic.kind == "ambiguous" for diagnostic in cpp_include_diagnostics
        )
        unresolved_cpp_includes = len(cpp_include_diagnostics) - ambiguous_cpp_includes
        has_warn = total_cycles > 0 or bool(cpp_include_diagnostics)
        status = self.evaluate_status(False, has_warn, mode)
        if total_cycles:
            summary = f"Dependency cycles: {total_cycles} found"
        else:
            summary = "No cyclic dependencies detected"
        if cpp_include_diagnostics:
            summary += (
                "; C++ include graph incomplete: "
                f"{ambiguous_cpp_includes} ambiguous, "
                f"{unresolved_cpp_includes} unresolved"
            )
        duration = time.time() - t0
        return self.create_result(
            name="cycle",
            status=status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "total_cycles": total_cycles,
                "py_cycles": len(py_cycles),
                "cpp_cycles": len(cpp_cycles),
                "cpp_include_resolution": "unique_project_path_suffix",
                "resolved_cpp_includes": resolved_cpp_includes,
                "ambiguous_cpp_includes": ambiguous_cpp_includes,
                "unresolved_cpp_includes": unresolved_cpp_includes,
                "cpp_include_diagnostics_truncated": max(
                    0, len(cpp_include_diagnostics) - max_reported
                ),
            },
            # Advisory heuristic engine (project-path-suffix C++ resolution,
            # best-effort Python module mapping) — like cognitive/security/resource,
            # an ERROR here must not sink the whole suite's required gate by default.
            required=bool(cfg.get("required", False)),
            evidence=EvidenceState.MEASURED,
        )
