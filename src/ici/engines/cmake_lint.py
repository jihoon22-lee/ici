"""CMake definition lint (no execution) — offline parser."""

import re
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.path_utils import resolve_project_path
from ici.engines.base import BaseEngine

_CMAKE_MIN_RE = re.compile(
    r"cmake_minimum_required\s*\(\s*VERSION\s+([0-9]+(?:\.[0-9]+)*(?:\.[0-9]+)?)",
    re.IGNORECASE,
)
_PROJECT_RE = re.compile(r"project\s*\(", re.IGNORECASE)
_ADD_SUBDIR_ESCAPE_RE = re.compile(r"add_subdirectory\s*\(\s*[\"']\.\.", re.IGNORECASE)
_CXX_STANDARD_RE = re.compile(r"CMAKE_CXX_STANDARD\s+([0-9]+)", re.IGNORECASE)
_EXPORT_COMPILE_RE = re.compile(r"CMAKE_EXPORT_COMPILE_COMMANDS\s+(ON|TRUE|1)", re.IGNORECASE)


def _parse_version(v: str) -> tuple[int, ...]:
    parts = []
    for p in v.strip().split("."):
        if p.isdigit():
            parts.append(int(p))
        else:
            # handle like "3.16.3" vs "3.16"
            m = re.match(r"(\d+)", p)
            if m:
                parts.append(int(m.group(1)))
            else:
                break
    return tuple(parts)


def _version_lt(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    # pad shorter with zeros
    n = max(len(a), len(b))
    a_p = a + (0,) * (n - len(a))
    b_p = b + (0,) * (n - len(b))
    return a_p < b_p


class CMakeLintEngine(BaseEngine):
    """Validates CMakeLists.txt without executing cmake."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("cmake_lint")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))
        min_version_str = cfg.get("min_version", "3.16")
        min_version = _parse_version(str(min_version_str))

        # Find CMakeLists.txt files under project boundary
        cmake_files: list[Path] = []
        for candidate in self.project_root.rglob("CMakeLists.txt"):
            # Respect canonical containment and ignore symlink files
            if candidate.is_symlink():
                continue
            try:
                # Ensure inside project root
                resolve_project_path(
                    self.project_root, str(candidate.relative_to(self.project_root))
                )
            except ValueError:
                continue
            # Also respect _should_ignore_path? For now, include all; ignore build/*
            try:
                if candidate.is_file():
                    # simple ignore: skip build/ and .venv/
                    rel = candidate.relative_to(self.project_root)
                    if any(part in (".venv", "build", ".git") for part in rel.parts):
                        continue
                    cmake_files.append(candidate)
            except ValueError:
                continue

        if not cmake_files:
            duration = time.time() - t0
            return self.create_result(
                name="cmake_lint",
                status=EngineStatus.PASS,
                summary="No CMakeLists.txt found — not a CMake project",
                duration=duration,
                targets=[],
                required=required,
                evidence=EvidenceState.MEASURED,
            )

        targets: list[InspectionTarget] = []
        has_warn = False
        has_fail = False

        for fpath in cmake_files:
            rel_p = str(fpath.relative_to(self.project_root))
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError as err:
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name="CMakeLint:ReadError",
                        status=EngineStatus.WARN,
                        message=f"Could not read CMakeLists.txt: {err}",
                    )
                )
                has_warn = True
                continue

            lines = content.splitlines()
            # Check cmake_minimum_required
            m = _CMAKE_MIN_RE.search(content)
            if not m:
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name="CMakeLint:MinVersion",
                        status=EngineStatus.WARN,
                        message="Missing cmake_minimum_required(VERSION ...) — required for RHEL8 cmake 3.20",
                    )
                )
                has_warn = True
            else:
                ver_str = m.group(1)
                ver = _parse_version(ver_str)
                if _version_lt(ver, min_version):
                    # find line number
                    line_no = 1
                    for idx, line in enumerate(lines, 1):
                        if _CMAKE_MIN_RE.search(line):
                            line_no = idx
                            break
                    targets.append(
                        InspectionTarget(
                            file_path=rel_p,
                            start_line=line_no,
                            target_name="CMakeLint:MinVersion",
                            status=EngineStatus.WARN,
                            message=f"cmake_minimum_required VERSION {ver_str} < required {min_version_str}",
                        )
                    )
                    has_warn = True

            # Check project()
            if not _PROJECT_RE.search(content):
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name="CMakeLint:Project",
                        status=EngineStatus.WARN,
                        message="Missing project() declaration",
                    )
                )
                has_warn = True

            # Check add_subdirectory escape
            for idx, line in enumerate(lines, 1):
                if _ADD_SUBDIR_ESCAPE_RE.search(line):
                    targets.append(
                        InspectionTarget(
                            file_path=rel_p,
                            start_line=idx,
                            target_name="CMakeLint:AddSubdirectory",
                            status=EngineStatus.WARN,
                            message="add_subdirectory with parent traversal '..' may escape project boundary",
                            snippet=line.strip()[:200],
                        )
                    )
                    has_warn = True

            # Check CMAKE_CXX_STANDARD
            m_std = _CXX_STANDARD_RE.search(content)
            if m_std:
                std_val = m_std.group(1)
                if std_val != "17":
                    line_no = 1
                    for idx, line in enumerate(lines, 1):
                        if _CXX_STANDARD_RE.search(line):
                            line_no = idx
                            break
                    # Not necessarily fail, but warn if not 17 (enterprise standard)
                    targets.append(
                        InspectionTarget(
                            file_path=rel_p,
                            start_line=line_no,
                            target_name="CMakeLint:CxxStandard",
                            status=EngineStatus.WARN,
                            message=f"CMAKE_CXX_STANDARD is {std_val}, expected 17",
                        )
                    )
                    has_warn = True
            else:
                # If no standard specified, warn to be explicit (optional, not fail)
                # Only warn if file looks like C++ project (has add_executable/library)
                if re.search(r"add_(executable|library)\s*\(", content, re.IGNORECASE):
                    targets.append(
                        InspectionTarget(
                            file_path=rel_p,
                            start_line=1,
                            target_name="CMakeLint:CxxStandard",
                            status=EngineStatus.WARN,
                            message="Missing CMAKE_CXX_STANDARD — should be 17 for RHEL8",
                        )
                    )
                    has_warn = True

            # Check EXPORT_COMPILE_COMMANDS
            if not _EXPORT_COMPILE_RE.search(content) and re.search(
                r"add_(executable|library)\s*\(", content, re.IGNORECASE
            ):
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name="CMakeLint:ExportCompile",
                        status=EngineStatus.WARN,
                        message="Missing CMAKE_EXPORT_COMPILE_COMMANDS=ON — needed for compile_commands.json",
                    )
                )
                has_warn = True

            # If no warnings for this file, add PASS target for tracking
            if not any(t.file_path == rel_p and t.status != EngineStatus.PASS for t in targets):
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name="CMakeLint",
                        status=EngineStatus.PASS,
                        message="CMakeLists.txt lint passed",
                    )
                )

        duration = time.time() - t0
        # For pass_warn mode, has_fail never true; all issues are WARN
        status = self.evaluate_status(has_fail, has_warn, mode)
        summary = (
            f"CMake lint: {len(cmake_files)} file(s) checked, {sum(1 for t in targets if t.status != EngineStatus.PASS)} issue(s)"
            if has_warn
            else f"CMake lint passed for {len(cmake_files)} file(s)"
        )

        return self.create_result(
            name="cmake_lint",
            status=status,
            summary=summary,
            duration=duration,
            targets=targets,
            required=required,
            evidence=EvidenceState.MEASURED,
        )
