"""Cyclic dependency detection engine — Python imports and C++ includes."""

import ast
import re
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
                # Try exact match, then suffix match (e.g., "b" matches "pkg.b")
                if target_mod in module_to_file:
                    graph[importer].add(target_mod)
                else:
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
    """Build file -> included-file graph including headers."""
    all_files = _iter_cpp_and_headers(project_root)
    by_name: dict[str, Path] = {}
    for f in all_files:
        by_name.setdefault(f.name, f)

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
            target = by_name.get(Path(inc_name).name)
            if target and target.resolve() != resolved_f:
                graph[resolved_f].add(target.resolve())
    return graph, known


def _find_cycles_tarjan(graph: dict) -> list[list]:
    """Tarjan SCC — return components with >1 member (cycles)."""
    index_counter = [0]
    stack: list = []
    on_stack: set = set()
    indices: dict = {}
    lowlink: dict = {}
    cycles: list[list] = []

    def strongconnect(node):
        indices[node] = lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for dep in graph.get(node, ()):
            if dep not in indices:
                strongconnect(dep)
                lowlink[node] = min(lowlink[node], lowlink[dep])
            elif dep in on_stack:
                lowlink[node] = min(lowlink[node], indices[dep])
        if lowlink[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph.get(node, set()):
                cycles.append(sorted(component))

    for node in graph:
        if node not in indices:
            strongconnect(node)
    return cycles


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
        for cycle_nodes in py_cycles[:max_reported]:
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
        for cycle_files in cpp_cycles[:max_reported]:
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
            required=bool(cfg.get("required", True)),
            evidence=EvidenceState.MEASURED,
        )
