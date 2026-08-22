"""pyproject.toml metadata lint (PEP 621 subset) — offline validation."""

import re
import time
from pathlib import Path

import tomli

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import get_all_python_sources
from ici.engines.base import BaseEngine

_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
_SCRIPT_RE = re.compile(r"^[A-Za-z0-9._-]+:[A-Za-z0-9_.-]+$")
_VERSION_SAFE_RE = re.compile(r"[A-Za-z0-9._+!-]+")


class _ProjectMetaLinter:
    """Accumulates findings for one pyproject.toml [project] table."""

    def __init__(self, rel_path: str, project_table: dict):
        self.rel_path = rel_path
        self.table = project_table
        self.findings: list[InspectionTarget] = []

    def _warn(self, name: str, message: str) -> None:
        self.findings.append(
            InspectionTarget(
                file_path=self.rel_path,
                start_line=1,
                target_name=f"PyProjectLint:{name}",
                status=EngineStatus.WARN,
                message=message,
            )
        )

    def check_name(self) -> None:
        value = self.table.get("name")
        if not isinstance(value, str) or not value:
            self._warn("Name", "Missing or non-string [project].name")
        elif not _NAME_RE.fullmatch(value):
            self._warn("Name", f"[project].name {value!r} contains unsafe characters")

    def check_version(self) -> None:
        value = self.table.get("version")
        if value is None:
            return
        if not isinstance(value, str) or not _VERSION_SAFE_RE.fullmatch(value):
            self._warn("Version", "[project].version must be a path-safe version string")

    def check_requires_python(self) -> None:
        value = self.table.get("requires-python")
        if value is None:
            self._warn("RequiresPython", "Missing requires-python — target floor is undocumented")
        elif not isinstance(value, str) or not value.strip():
            self._warn("RequiresPython", "requires-python must be a non-empty specifier string")

    def check_dependencies(self) -> None:
        value = self.table.get("dependencies")
        if value is None:
            return
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            self._warn("Dependencies", "dependencies must be a list of requirement strings")

    def check_scripts(self) -> None:
        scripts = self.table.get("scripts")
        if scripts is None:
            return
        if not isinstance(scripts, dict):
            self._warn("Scripts", "[project.scripts] must be a table")
            return
        for script_name, entry in scripts.items():
            if not isinstance(entry, str) or not _SCRIPT_RE.match(entry):
                self._warn(
                    "Scripts",
                    f"[project.scripts].{script_name} = {entry!r} must be 'module:callable'",
                )

    def run_checks(self) -> None:
        for check in (
            self.check_name,
            self.check_version,
            self.check_requires_python,
            self.check_dependencies,
            self.check_scripts,
        ):
            check()


def _load_project_table(path: Path) -> dict | None:
    """Return the [project] table, or None when unreadable/malformed."""
    try:
        with path.open("rb") as stream:
            data = tomli.load(stream)
    except (OSError, ValueError, RecursionError):
        return None
    table = data.get("project")
    return table if isinstance(table, dict) else None


class PyProjectLintEngine(BaseEngine):
    """Validates pyproject.toml metadata without external tooling."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("pyproject_lint")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))

        pyproject = self.project_root / "pyproject.toml"
        has_python_sources = bool(get_all_python_sources(self.project_root, self.config))
        if not pyproject.is_file():
            status, summary, targets = self._missing_file_status(has_python_sources)
            return self.create_result(
                name="pyproject_lint",
                status=status,
                summary=summary,
                duration=time.time() - t0,
                targets=targets,
                required=required,
                evidence=EvidenceState.MEASURED,
            )

        table = _load_project_table(pyproject)
        targets = self._lint_table(pyproject, table)
        issue_count = sum(1 for target in targets if target.status != EngineStatus.PASS)
        status = self.evaluate_status(False, issue_count > 0, mode)
        summary = (
            f"pyproject.toml: {issue_count} metadata issue(s)"
            if issue_count
            else "pyproject.toml metadata passed"
        )
        return self.create_result(
            name="pyproject_lint",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=targets,
            required=required,
            evidence=EvidenceState.MEASURED,
        )

    def _missing_file_status(
        self, has_python_sources: bool
    ) -> tuple[EngineStatus, str, list[InspectionTarget]]:
        """A Python project without pyproject.toml warns; others pass untouched."""
        if has_python_sources:
            target = InspectionTarget(
                file_path="pyproject.toml",
                start_line=1,
                target_name="PyProjectLint:Missing",
                status=EngineStatus.WARN,
                message="Python sources found but no pyproject.toml metadata file",
            )
            return EngineStatus.WARN, "Python project is missing pyproject.toml", [target]
        return EngineStatus.PASS, "No pyproject.toml — nothing to validate", []

    def _lint_table(self, pyproject: Path, table: dict | None) -> list[InspectionTarget]:
        rel_path = str(pyproject.relative_to(self.project_root))
        if table is None:
            return [
                InspectionTarget(
                    file_path=rel_path,
                    start_line=1,
                    target_name="PyProjectLint:ProjectTable",
                    status=EngineStatus.WARN,
                    message="pyproject.toml is missing a valid [project] table",
                )
            ]
        linter = _ProjectMetaLinter(rel_path, table)
        linter.run_checks()
        if not linter.findings:
            linter.findings.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=1,
                    target_name="PyProjectLint",
                    status=EngineStatus.PASS,
                    message="[project] metadata passed",
                )
            )
        return linter.findings
