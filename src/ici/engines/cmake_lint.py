"""CMake definition lint (no execution) — offline parser."""

import re
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.path_utils import resolve_project_path
from ici.engines.base import BaseEngine

_CMAKE_MIN_RE = re.compile(r"cmake_minimum_required\s*\(\s*VERSION\s+([0-9.]+)", re.IGNORECASE)
_PROJECT_RE = re.compile(r"project\s*\(", re.IGNORECASE)
_ADD_EXEC_RE = re.compile(r"add_(executable|library)\s*\(", re.IGNORECASE)
_ADD_SUBDIR_ESCAPE_RE = re.compile(r"add_subdirectory\s*\(\s*[\"']\.\.", re.IGNORECASE)
_CXX_STANDARD_RE = re.compile(r"CMAKE_CXX_STANDARD\s+([0-9]+)", re.IGNORECASE)
_EXPORT_COMPILE_RE = re.compile(r"CMAKE_EXPORT_COMPILE_COMMANDS\s+(?:ON|TRUE|1)", re.IGNORECASE)

_SKIP_PARTS = frozenset({".venv", "build", ".git"})


def _parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.strip().split("."):
        match = re.match(r"(\d+)", chunk)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def _version_lt(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    size = max(len(a), len(b))
    padded_a = a + (0,) * (size - len(a))
    padded_b = b + (0,) * (size - len(b))
    return padded_a < padded_b


def _find_cmake_files(project_root: Path) -> list[Path]:
    """Collect CMakeLists.txt inside the canonical project boundary."""
    files: list[Path] = []
    for candidate in sorted(project_root.rglob("CMakeLists.txt")):
        if candidate.is_symlink():
            continue
        rel = candidate.relative_to(project_root)
        if any(part in _SKIP_PARTS for part in rel.parts):
            continue
        try:
            resolve_project_path(project_root, str(rel))
        except ValueError:
            continue
        files.append(candidate)
    return files


class _FileLinter:
    """Accumulates findings for one CMakeLists.txt file."""

    def __init__(self, rel_path: str, content: str, min_version: tuple[int, ...]):
        self.rel_path = rel_path
        self.lines = content.splitlines()
        self.content = content
        self.min_version = min_version
        self.findings: list[InspectionTarget] = []

    def _warn(self, name: str, message: str, line_no: int = 1, snippet: str = "") -> None:
        self.findings.append(
            InspectionTarget(
                file_path=self.rel_path,
                start_line=line_no,
                target_name=f"CMakeLint:{name}",
                status=EngineStatus.WARN,
                message=message,
                snippet=snippet[:200],
            )
        )

    def _line_of(self, pattern: re.Pattern[str]) -> int:
        for idx, line in enumerate(self.lines, 1):
            if pattern.search(line):
                return idx
        return 1

    def check_min_version(self) -> None:
        match = _CMAKE_MIN_RE.search(self.content)
        if not match:
            self._warn(
                "MinVersion",
                "Missing cmake_minimum_required(VERSION ...) — required for RHEL8 cmake",
            )
            return
        found = _parse_version(match.group(1))
        if _version_lt(found, self.min_version):
            self._warn(
                "MinVersion",
                f"cmake_minimum_required VERSION {match.group(1)} is below the required minimum",
                line_no=self._line_of(_CMAKE_MIN_RE),
            )

    def check_project_declared(self) -> None:
        if not _PROJECT_RE.search(self.content):
            self._warn("Project", "Missing project() declaration")

    def check_subdir_escape(self) -> None:
        for idx, line in enumerate(self.lines, 1):
            if _ADD_SUBDIR_ESCAPE_RE.search(line):
                self._warn(
                    "AddSubdirectory",
                    "add_subdirectory('..') may escape the project boundary",
                    line_no=idx,
                    snippet=line.strip(),
                )

    def check_cxx_standard(self) -> None:
        if not _ADD_EXEC_RE.search(self.content):
            return
        match = _CXX_STANDARD_RE.search(self.content)
        if not match:
            self._warn("CxxStandard", "Missing CMAKE_CXX_STANDARD — should be 17 for RHEL8")
        elif match.group(1) != "17":
            self._warn(
                "CxxStandard",
                f"CMAKE_CXX_STANDARD is {match.group(1)}, expected 17",
                line_no=self._line_of(_CXX_STANDARD_RE),
            )

    def check_export_compile_commands(self) -> None:
        if _ADD_EXEC_RE.search(self.content) and not _EXPORT_COMPILE_RE.search(self.content):
            self._warn(
                "ExportCompile",
                "Missing CMAKE_EXPORT_COMPILE_COMMANDS=ON — needed for compile_commands.json",
            )

    def run_checks(self) -> None:
        for check in (
            self.check_min_version,
            self.check_project_declared,
            self.check_subdir_escape,
            self.check_cxx_standard,
            self.check_export_compile_commands,
        ):
            check()


class CMakeLintEngine(BaseEngine):
    """Validates CMakeLists.txt without executing cmake."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("cmake_lint")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))
        min_version = _parse_version(str(cfg.get("min_version", "3.16")))

        cmake_files = _find_cmake_files(self.project_root)
        if not cmake_files:
            return self.create_result(
                name="cmake_lint",
                status=EngineStatus.PASS,
                summary="No CMakeLists.txt found — not a CMake project",
                duration=time.time() - t0,
                targets=[],
                required=required,
                evidence=EvidenceState.MEASURED,
            )

        targets: list[InspectionTarget] = []
        for fpath in cmake_files:
            targets.extend(self._lint_file(fpath, min_version))

        issue_count = sum(1 for target in targets if target.status != EngineStatus.PASS)
        status = self.evaluate_status(False, issue_count > 0, mode)
        summary = (
            f"CMake lint: {len(cmake_files)} file(s) checked, {issue_count} issue(s)"
            if issue_count
            else f"CMake lint passed for {len(cmake_files)} file(s)"
        )
        return self.create_result(
            name="cmake_lint",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=targets,
            required=required,
            evidence=EvidenceState.MEASURED,
        )

    def _lint_file(self, fpath: Path, min_version: tuple[int, ...]) -> list[InspectionTarget]:
        rel_path = str(fpath.relative_to(self.project_root))
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError as err:
            return [
                InspectionTarget(
                    file_path=rel_path,
                    start_line=1,
                    target_name="CMakeLint:ReadError",
                    status=EngineStatus.WARN,
                    message=f"Could not read CMakeLists.txt: {err}",
                )
            ]

        linter = _FileLinter(rel_path, content, min_version)
        linter.run_checks()
        if not linter.findings:
            linter.findings.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=1,
                    target_name="CMakeLint",
                    status=EngineStatus.PASS,
                    message="CMakeLists.txt lint passed",
                )
            )
        return linter.findings
