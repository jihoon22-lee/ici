"""Policy and location contracts for compilation-database verification."""

from __future__ import annotations

from pathlib import Path

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationDiagnostic,
    CompilationSearchPath,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineStatus, EvidenceState
from ici.engines.compile_db import CompileDatabaseEngine


def _context(
    root: Path,
    *,
    sources: tuple[str, ...] = ("src/main.cpp",),
    compilation: CompilationContext | None = None,
) -> AnalysisContext:
    return AnalysisContext(
        project=ProjectModel(
            root=root,
            name="compile-fixture",
            version="1.0.0",
            project_type="cpp" if sources else "python",
            cpp_sources=sources,
            compilable_cpp_sources=sources,
        ),
        capabilities=CapabilityInventory(),
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({}),
            toolchain_digest=canonical_digest([]),
        ),
        compilation=compilation or CompilationContext(),
    )


def _unit(source: str = "src/main.cpp") -> CompilationUnit:
    return CompilationUnit(
        source=source,
        directory="build",
        argv=("g++", "-std=c++17", "-fpermissive", "-c", f"../{source}"),
        compiler="g++",
        language="c++",
        standard="c++17",
        include_paths=(
            CompilationSearchPath(
                path="missing/include",
                kind="include",
                scope="project",
                exists=False,
            ),
        ),
        configuration=canonical_digest({"source": source}),
        diagnostics=(
            CompilationDiagnostic(
                code="missing-include-dir",
                message="The include directory is missing.",
                source=source,
            ),
        ),
    )


def test_python_only_project_is_not_applicable(tmp_path: Path) -> None:
    context = _context(tmp_path, sources=())

    result = CompileDatabaseEngine(tmp_path, {}, analysis_context=context).run()

    assert result.status is EngineStatus.SKIP
    assert result.evidence is EvidenceState.NOT_APPLICABLE
    assert result.targets == []


def test_missing_database_is_visible_and_policy_can_require_it(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() {}\n", encoding="utf-8")
    context = _context(tmp_path)

    optional = CompileDatabaseEngine(tmp_path, {}, analysis_context=context).run()
    required = CompileDatabaseEngine(
        tmp_path,
        {"engines": {"compile_db": {"database_required": True}}},
        analysis_context=context,
    ).run()

    assert optional.status is EngineStatus.WARN
    assert optional.targets[0].file_path == "src/main.cpp"
    assert optional.targets[0].start_line == 1
    assert optional.targets[0].status is EngineStatus.WARN
    assert required.status is EngineStatus.FAIL
    assert required.targets[0].status is EngineStatus.FAIL


def test_coverage_diagnostics_and_flag_policy_are_all_location_bearing(tmp_path: Path) -> None:
    for relative in ("src/main.cpp", "src/missing.cpp"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int value;\n", encoding="utf-8")
    compilation = CompilationContext(
        units=(_unit(),),
        database_path="build/compile_commands.json",
        database_digest=canonical_digest(["unit"]),
        diagnostics=(
            CompilationDiagnostic(
                code="invalid-entry",
                message="One row is malformed.",
                level="error",
                entry_index=1,
            ),
        ),
    )
    context = _context(
        tmp_path,
        sources=("src/main.cpp", "src/missing.cpp"),
        compilation=compilation,
    )
    config = {
        "engines": {
            "compile_db": {
                "required_flags": ["-Wall", "-std=c++20"],
                "forbidden_flags": ["-fpermissive"],
            }
        }
    }

    result = CompileDatabaseEngine(tmp_path, config, analysis_context=context).run()

    assert result.status is EngineStatus.FAIL
    assert result.evidence is EvidenceState.MEASURED
    assert {target.target_name for target in result.targets} >= {
        "ici.compile-db.coverage",
        "ici.compile-db.invalid-entry",
        "ici.compile-db.missing-include-dir",
        "ici.compile-db.required-flag",
        "ici.compile-db.forbidden-flag",
    }
    coverage = [
        target for target in result.targets if target.target_name == "ici.compile-db.coverage"
    ]
    assert [(target.file_path, target.status) for target in coverage] == [
        ("src/main.cpp", EngineStatus.PASS),
        ("src/missing.cpp", EngineStatus.FAIL),
    ]
    assert all(target.file_path and target.start_line == 1 for target in result.targets)
    assert result.extra["production_units"] == 2
    assert result.extra["covered_units"] == 1
    assert result.extra["configurations"] == 1


def test_duplicate_source_configurations_are_inspected_independently(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() {}\n", encoding="utf-8")
    debug = _unit()
    release = CompilationUnit(
        source=debug.source,
        directory="release",
        argv=("g++", "-std=c++20", "-c", "../src/main.cpp"),
        compiler="g++",
        language="c++",
        standard="c++20",
        configuration=canonical_digest({"variant": "release"}),
    )
    context = _context(
        tmp_path,
        compilation=CompilationContext(
            units=(debug, release),
            database_path="compile_commands.json",
            database_digest=canonical_digest(["debug", "release"]),
        ),
    )

    result = CompileDatabaseEngine(tmp_path, {}, analysis_context=context).run()

    assert result.extra["configurations"] == 2
    assert result.extra["covered_units"] == 1
    assert [target.status for target in result.targets if target.target_name.endswith("coverage")] == [
        EngineStatus.PASS
    ]
