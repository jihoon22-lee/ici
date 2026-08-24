"""Cyclic dependency detection engine — Python imports and C++ includes."""

import ast
import re
import sys
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import get_all_python_sources
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


def _iter_cpp_and_headers(project_root: Path) -> list[Path]:
    """Collect all C++ source + header files inside the project boundary."""
    import os

    skip = {".venv", "venv", "build", ".git"}
    suffixes = {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"}
    results = []
    for current, dir_names, file_names in os.walk(project_root):
        dir_names[:] = [d for d in dir_names if d not in skip]
        current_path = Path(current)
        for name in sorted(file_names):
            path = current_path / name
            if not path.is_symlink() and path.suffix in suffixes:
                results.append(path)
    return results


def _build_cpp_graph(
    project_root: Path,
) -> tuple[dict[Path, set[Path]], dict[Path, Path]]:
    """Build file -> included-file graph including headers.

    ``#include "..."`` is resolved by basename only (no ``-I`` search-path
    model here), so a basename shared by more than one file in the project is
    genuinely ambiguous — silently picking the first one found risks wiring a
    false edge to the wrong file. Ambiguous basenames are left unresolved
    instead.
    """
    all_files = _iter_cpp_and_headers(project_root)
    by_name: dict[str, list[Path]] = {}
    for f in all_files:
        by_name.setdefault(f.name, []).append(f)
    unambiguous = {name: paths[0] for name, paths in by_name.items() if len(paths) == 1}

    graph: dict[Path, set[Path]] = {}
    known: dict[Path, Path] = {}
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
            target = unambiguous.get(Path(inc_name).name)
            if target and target.resolve() != resolved_f:
                graph[resolved_f].add(target.resolve())
    return graph, known


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

        cpp_graph, cpp_files_map = _build_cpp_graph(self.project_root)
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

        has_warn = total_cycles > 0
        status = self.evaluate_status(False, has_warn, mode)
        summary = (
            f"Dependency cycles: {total_cycles} found"
            if has_warn
            else "No cyclic dependencies detected"
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
            },
            # Advisory heuristic engine (basename-only C++ resolution, best-effort
            # Python module mapping) — like cognitive/security/resource, an
            # ERROR here must not sink the whole suite's required gate by default.
            required=bool(cfg.get("required", False)),
            evidence=EvidenceState.MEASURED,
        )
