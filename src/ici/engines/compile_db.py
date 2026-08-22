"""Compile database engine — validates coverage and flag policy offline."""

import time
from pathlib import Path

from ici.build_adapters.base import BuildAdapterError
from ici.core.compile_db import (
    CompileCommand,
    extract_include_dirs,
    extract_standard,
    load_compile_database,
)
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
)
from ici.core.project import get_all_cpp_sources
from ici.engines.base import BaseEngine


def _db_discovery_paths(project_root: Path) -> list[Path]:
    return [
        project_root / "build" / "ici" / "cmake" / "compile_commands.json",
        project_root / "build" / "compile_commands.json",
        project_root / "compile_commands.json",
    ]


class CompileDbEngine(BaseEngine):
    """Checks every C++ source is compiled with the expected flags."""

    def __init__(self, project_root=None, config=None, db_path: Path | None = None):
        super().__init__(project_root, config)
        self._explicit_db_path = db_path

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("compile_db")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))
        cpp_sources = get_all_cpp_sources(self.project_root, self.config)

        if not cpp_sources:
            return self._no_cpp_scope(required, t0)

        db_path = self._find_database(cfg)
        if db_path is None:
            status = EngineStatus.WARN if not required else EngineStatus.ERROR
            return self.create_result(
                name="compile_db",
                status=status,
                summary="No compile_commands.json found — run build_definition first",
                duration=time.time() - t0,
                targets=[
                    InspectionTarget(
                        file_path="",
                        start_line=1,
                        target_name="CompileDb:Missing",
                        status=status,
                        message="compile_commands.json was not located in known build paths",
                    )
                ],
                required=required,
                evidence=EvidenceState.NOT_RUN,
            )

        try:
            commands = load_compile_database(
                db_path, self.project_root, db_path.parent.parent.parent
            )
        except BuildAdapterError as err:
            return self._database_error(str(err), required, t0)

        findings = _collect_findings(
            cpp_sources, commands, cfg.get("required_flags") or [], self.project_root
        )
        issue_count = sum(1 for f in findings if f.status != EngineStatus.PASS)
        status = self.evaluate_status(False, issue_count > 0, mode)
        summary = (
            f"compile db: {issue_count} issue(s) across {len(commands)} entr(ies)"
            if issue_count
            else f"compile db OK — {len(cpp_sources)} source(s) covered"
        )
        return self.create_result(
            name="compile_db",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=findings,
            extra={"entries": len(commands), "db_path": str(db_path)},
            required=required,
            evidence=EvidenceState.MEASURED,
        )

    def _no_cpp_scope(self, required: bool, t0: float) -> EngineResult:
        return self.create_result(
            name="compile_db",
            status=EngineStatus.PASS,
            summary="No C++ sources — compile db check not applicable",
            duration=time.time() - t0,
            targets=[],
            required=bool(required),
            evidence=EvidenceState.MEASURED,
        )

    def _database_error(self, message: str, required: bool, t0: float) -> EngineResult:
        return self.create_result(
            name="compile_db",
            status=EngineStatus.WARN,
            summary=f"Invalid compile database: {message}",
            duration=time.time() - t0,
            targets=[
                InspectionTarget(
                    file_path="",
                    start_line=1,
                    target_name="CompileDb:Invalid",
                    status=EngineStatus.WARN,
                    message=message,
                )
            ],
            required=bool(required),
            evidence=EvidenceState.NOT_RUN,
        )

    def _find_database(self, cfg: dict) -> Path | None:
        explicit = self._explicit_db_path or cfg.get("path")
        candidates = [Path(explicit)] if explicit else _db_discovery_paths(self.project_root)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None


def _collect_findings(
    cpp_sources: list[Path],
    commands: list[CompileCommand],
    required_flags: list[str],
    project_root: Path,
) -> list[InspectionTarget]:
    covered = {command.file.resolve() for command in commands}
    by_file: dict[Path, CompileCommand] = {command.file.resolve(): command for command in commands}

    findings: list[InspectionTarget] = []
    for source in cpp_sources:
        resolved = source.resolve()
        rel = str(source.relative_to(project_root))
        if resolved not in covered:
            findings.append(_finding(rel, "NotInDb", f"{rel} is missing from the compile database"))
            continue
        findings.extend(_flag_findings(rel, by_file[resolved], required_flags, project_root))

    if not findings:
        sample = next(iter(commands), None)
        if sample is not None:
            findings.append(
                InspectionTarget(
                    file_path=str(sample.file.relative_to(project_root)),
                    start_line=1,
                    target_name="CompileDb",
                    status=EngineStatus.PASS,
                    message=f"All {len(covered)} database sources validated",
                )
            )
    return findings


def _flag_findings(
    rel: str, command: CompileCommand, required_flags: list[str], project_root: Path
) -> list[InspectionTarget]:
    results: list[InspectionTarget] = []
    std = extract_standard(command.flags)
    for expected in required_flags:
        satisfied = (
            expected.startswith("-std=") and std == expected[5:]
        ) or expected in command.arguments
        if not satisfied:
            results.append(_finding(rel, "MissingFlag", f"{rel} missing required flag {expected}"))
    missing_includes = [
        str(path)
        for path in extract_include_dirs(command.flags, command.directory)
        if not path.exists()
    ]
    for include in missing_includes[:3]:
        results.append(
            _finding(rel, "MissingInclude", f"{rel} references non-existent include dir {include}")
        )
    return results


def _finding(rel_path: str, name: str, message: str) -> InspectionTarget:
    return InspectionTarget(
        file_path=rel_path,
        start_line=1,
        target_name=f"CompileDb:{name}",
        status=EngineStatus.WARN,
        message=message,
    )
