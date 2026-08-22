"""Static C++/Python hygiene checks — headers, include cycles, dangerous patterns."""

import re
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.engines.base import BaseEngine

_HEADER_SUFFIXES = frozenset({".h", ".hpp", ".hh"})
_CPP_SOURCE_SUFFIXES = frozenset({".cpp", ".cc", ".cxx", ".c"})
_INCLUDE_RE = re.compile(r'#include\s*["<]([^">]+)[">]')
_GUARD_PRAGMA_RE = re.compile(r"#pragma\s+once")
_GUARD_IFNDEF_RE = re.compile(r"#ifndef\s+\w+")
_DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Eval", re.compile(r"\beval\s*\(")),
    ("Exec", re.compile(r"\bexec\s*\(")),
    ("PickleLoad", re.compile(r"pickle\.loads?\s*\(")),
    ("ShellTrue", re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True")),
    (
        "HardcodedSecret",
        re.compile(r"(?i)(password|passwd|secret|api_key)\s*[=:]\s*[\"'][^\"']{6,}[\"']"),
    ),
    ("PrivateKey", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


class _Finding:
    """Mutable collector shared by all static-hygiene checks."""

    def __init__(self) -> None:
        self.targets: list[InspectionTarget] = []

    def warn(self, rel_path: str, name: str, message: str, line_no: int = 1) -> None:
        self.targets.append(
            InspectionTarget(
                file_path=rel_path,
                start_line=line_no,
                target_name=f"Static:{name}",
                status=EngineStatus.WARN,
                message=message,
            )
        )


def _iter_sources(project_root: Path, suffixes: frozenset[str]) -> list[Path]:
    import os

    files: list[Path] = []
    skip = {".venv", "venv", "build", ".git"}
    for current, dir_names, file_names in os.walk(project_root):
        dir_names[:] = [d for d in dir_names if d not in skip]
        current_path = Path(current)
        for name in sorted(file_names):
            path = current_path / name
            if not path.is_symlink() and path.suffix in suffixes:
                files.append(path)
    return files


def check_headers(finding, project_root: Path) -> None:
    """Every header needs #pragma once or an ifndef guard."""
    for header in _iter_sources(project_root, frozenset(_HEADER_SUFFIXES)):
        try:
            text = header.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not _GUARD_PRAGMA_RE.search(text) and not _GUARD_IFNDEF_RE.search(text):
            rel = str(header.relative_to(project_root))
            finding.warn(rel, "HeaderGuard", f"{rel} lacks #pragma once or include guard")


def build_include_graph(project_root: Path) -> dict[Path, set[Path]]:
    """Map each C++ source/header to the local files it includes."""
    graph: dict[Path, set[Path]] = {}
    all_files = {
        p.resolve(): p
        for p in _iter_sources(project_root, frozenset(_HEADER_SUFFIXES | _CPP_SOURCE_SUFFIXES))
    }
    for path in all_files.values():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        deps: set[Path] = set()
        for match in _INCLUDE_RE.finditer(text):
            target = (path.parent / match.group(1)).resolve()
            if target in all_files:
                deps.add(target)
        graph[path.resolve()] = deps
    return graph


def find_include_cycles(graph: dict[Path, set[Path]]) -> list[list[Path]]:
    """Return one canonical cycle path per strongly-connected component >1."""
    index_counter = [0]
    stack: list[Path] = []
    on_stack: set[Path] = set()
    indices: dict[Path, int] = {}
    lowlink: dict[Path, int] = {}
    components: list[list[Path]] = []

    def strongconnect(node: Path) -> None:
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
            component: list[Path] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph.get(node, set()):
                components.append(sorted(component))

    for node in graph:
        if node not in indices:
            strongconnect(node)
    return components


def check_security(finding, project_root: Path, include_tests: bool = False) -> None:
    """Scan Python sources for offline-detectable dangerous patterns."""
    for source in _iter_sources(project_root, frozenset({".py"})):
        if not include_tests and "tests" in source.relative_to(project_root).parts:
            continue
        try:
            lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel = str(source.relative_to(project_root))
        for line_no, line in enumerate(lines, 1):
            for name, pattern in _DANGEROUS_PATTERNS:
                if pattern.search(line):
                    finding.warn(
                        rel,
                        f"Security:{name}",
                        f"[{name}] {line.strip()[:160]}",
                        line_no=line_no,
                    )


class StaticHygieneEngine(BaseEngine):
    """Header guards, include cycles, and dangerous-pattern scan in one engine."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("static_hygiene")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))

        finding = _Finding()
        if cfg.get("check_header_guards", True):
            check_headers(finding, self.project_root)
        cycles: dict[str, list[str]] = {}
        if cfg.get("check_include_cycles", True):
            graph = build_include_graph(self.project_root)
            for number, component in enumerate(find_include_cycles(graph), 1):
                names = " <-> ".join(p.name for p in component[:5])
                first = str(component[0].relative_to(self.project_root))
                finding.warn(first, "IncludeCycle", f"Cycle #{number}: {names}")
                cycles[f"cycle_{number}"] = [str(p) for p in component]
        if cfg.get("check_security_patterns", True):
            check_security(finding, self.project_root, bool(cfg.get("security_scan_tests", False)))

        issue_count = sum(1 for target in finding.targets if target.status != EngineStatus.PASS)
        status = self.evaluate_status(False, issue_count > 0, mode)
        summary = (
            f"Static hygiene: {issue_count} issue(s)" if issue_count else "Static hygiene clean"
        )
        return self.create_result(
            name="static_hygiene",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=finding.targets,
            extra={"cycles": cycles},
            required=required,
            evidence=EvidenceState.MEASURED,
        )
