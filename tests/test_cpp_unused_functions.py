"""Contracts for exact compiler-backed C/C++ unused-function evidence."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationDiagnostic,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineStatus
from ici.core.runner import ProcessResult, run_process
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_unused_functions import run_cpp_unused_functions


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path.resolve(strict=True)


def _context(
    tmp_path: Path,
    sources: dict[str, str],
    *,
    configurations: dict[str, tuple[str, ...]] | None = None,
    compiler: Path | None = None,
    database: bool = True,
) -> tuple[Path, AnalysisContext, dict[str, str]]:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "build").mkdir()
    for relative, text in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    compiler = compiler or _executable(tmp_path / "tools" / "g++")
    compiler_name = "clang++" if "clang" in compiler.name else "g++"
    configuration_rows = configurations or {
        source: (canonical_digest({"source": source}),) for source in sources
    }
    units: list[CompilationUnit] = []
    for source, identities in configuration_rows.items():
        for index, _identity in enumerate(identities):
            source_path = root / source
            output = f"build/unit-{index}.o"
            argv = (
                str(compiler),
                "-std=c++20",
                f"-DCONFIGURATION={index}",
                "-Werror",
                "-c",
                str(source_path),
                "-o",
                f"unit-{index}.o",
            )
            units.append(
                CompilationUnit(
                    source=source,
                    directory="build",
                    argv=argv,
                    output=output,
                    compiler=compiler_name,
                    language="c++",
                    standard="c++20",
                    configuration=canonical_digest(
                        {
                            "directory": "build",
                            "argv": list(argv),
                            "output": output,
                        }
                    ),
                )
            )
    capability = ToolCapability(
        name=compiler_name,
        path=str(compiler),
        available=True,
        version=("clang version 18.1.0" if compiler_name == "clang++" else "g++ (GCC) 14.2.0"),
        version_tuple=(18, 1, 0) if compiler_name == "clang++" else (14, 2, 0),
        complete=True,
        returncode=0,
    )
    context = AnalysisContext(
        project=ProjectModel(
            root=root,
            name="unused-functions",
            version="1.0.0",
            project_type="cpp",
            source_dirs=("src",),
            cpp_sources=tuple(sorted(sources)),
            compilable_cpp_sources=tuple(sorted(sources)),
        ),
        capabilities=CapabilityInventory(capabilities={compiler_name: capability}),
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({"config": "test"}),
            toolchain_digest=canonical_digest({"toolchain": "test"}),
        ),
        compilation=CompilationContext(
            units=tuple(units),
            database_path="build/compile_commands.json" if database else None,
            database_digest=canonical_digest({"units": len(units)}),
            origin="cmake",
            generator="Ninja",
            unity_build=False,
        ),
    )
    return root, context, sources


def _diagnostic(
    path: Path,
    line: int,
    column: int,
    *,
    option: str = "-Wunused-function",
    kind: str = "warning",
) -> dict[str, object]:
    return {
        "kind": kind,
        "message": "'void unused_helper()' defined but not used",
        "option": option,
        "locations": [
            {
                "caret": {
                    "file": str(path),
                    "line": line,
                    "display-column": column,
                }
            }
        ],
    }


def _result(
    diagnostics: list[dict[str, object]] | None = None,
    *,
    returncode: int = 0,
    timed_out: bool = False,
    truncated: bool = False,
) -> ProcessResult:
    return ProcessResult(
        returncode=returncode,
        stdout="",
        stderr=json.dumps(diagnostics or []),
        duration=0.01,
        timed_out=timed_out,
        truncated=truncated,
    )


def test_exact_unused_function_diagnostic_uses_discarded_assembly_probe(
    tmp_path: Path,
) -> None:
    source_text = "static void unused_helper() {}\nint main() { return 0; }\n"
    root, context, snapshots = _context(tmp_path, {"src/main.cpp": source_text})
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        commands.append(command)
        return _result([_diagnostic(root / "src/main.cpp", 1, 13)])

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert outcome.configurations_checked == 1
    assert outcome.sources_checked == 1
    assert len(outcome.functions) == 1
    function = outcome.functions[0]
    assert function.target.file_path == "src/main.cpp"
    assert (function.target.start_line, function.target.start_column) == (1, 13)
    assert (function.target.end_line, function.target.end_column) == (1, 13)
    assert function.target.status == EngineStatus.WARN
    assert function.configurations == (context.compilation.units[0].configuration,)
    assert function.diagnostic_message == "warning: 'void unused_helper()' defined but not used"
    assert "unused_helper" in function.target.message
    command = commands[0]
    assert command.count(str(root / "src/main.cpp")) == 1
    assert command[-5:] == [
        "-S",
        "-o",
        os.devnull,
        "-fdiagnostics-format=json",
        "-fdiagnostics-show-option",
    ]
    assert "-S" in command
    assert "-Wunused-function" in command
    assert "-Wno-error=unused-function" in command
    assert "-Werror" not in command
    assert not (root / "build/unit-0.o").exists()
    assert outcome.evidence[0].argv == command


def test_exact_clean_source_has_a_located_pass_target(tmp_path: Path) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "static void used() {}\nint main() { used(); }\n"},
    )

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: _result(),
    )

    assert outcome.mode == "exact"
    assert outcome.functions == []
    assert [(target.file_path, target.start_line, target.status) for target in outcome.targets] == [
        ("src/main.cpp", 1, EngineStatus.PASS)
    ]


def test_clang_text_unused_function_diagnostic_is_normalized(tmp_path: Path) -> None:
    compiler = _executable(tmp_path / "tools" / "clang++")
    source_text = "static void unused_helper() {}\nint main() { return 0; }\n"
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": source_text},
        compiler=compiler,
    )
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        commands.append(command)
        return ProcessResult(
            0,
            "",
            f"{root / 'src/main.cpp'}:1:13: warning: unused function "
            "'unused_helper' [-Wunused-function]\n",
            0.01,
        )

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "exact"
    assert len(outcome.functions) == 1
    assert "-fdiagnostics-parseable-fixits" in commands[0]
    assert "-fdiagnostics-format=json" not in commands[0]


def test_unrelated_compiler_warnings_do_not_become_dead_findings(tmp_path: Path) -> None:
    text = "int main() { int value = 1; return 0; }\n"
    root, context, snapshots = _context(tmp_path, {"src/main.cpp": text})

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: _result(
            [
                _diagnostic(
                    root / "src/main.cpp",
                    1,
                    18,
                    option="-Wunused-variable",
                )
            ]
        ),
    )

    assert outcome.mode == "exact"
    assert outcome.functions == []
    assert outcome.targets[0].status == EngineStatus.PASS


def test_header_diagnostics_are_counted_but_outside_the_exact_source_contract(
    tmp_path: Path,
) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": '#include "helper.hpp"\nint main() { return 0; }\n'},
    )
    header = root / "src/helper.hpp"
    header.write_text("static void unused_header() {}\n", encoding="utf-8")

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: _result([_diagnostic(header, 1, 13)]),
    )

    assert outcome.mode == "exact"
    assert outcome.non_tu_diagnostics_excluded == 1
    assert outcome.functions == []
    assert outcome.targets[0].status == EngineStatus.PASS


def test_configuration_disagreement_fails_closed_without_exact_findings(tmp_path: Path) -> None:
    source = "#if CONFIGURATION\nstatic void unused_helper() {}\n#endif\n"
    configurations = {
        "src/main.cpp": (
            canonical_digest({"configuration": 0}),
            canonical_digest({"configuration": 1}),
        )
    }
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": source},
        configurations=configurations,
    )
    calls = 0

    def runner(*_args: object, **_kwargs: object) -> ProcessResult:
        nonlocal calls
        calls += 1
        return _result([] if calls == 1 else [_diagnostic(root / "src/main.cpp", 2, 13)])

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert calls == 2
    assert outcome.mode == "error"
    assert outcome.functions == []
    assert "vary across source configurations" in outcome.errors[-1]
    assert outcome.targets[-1].status == EngineStatus.ERROR


def test_later_configuration_disagreement_discards_earlier_source_targets(
    tmp_path: Path,
) -> None:
    sources = {
        "src/accepted.cpp": "static void unused_first() {}\n",
        "src/disagrees.cpp": "static void unused_second() {}\n",
    }
    configurations = {
        "src/accepted.cpp": (canonical_digest({"accepted": 0}),),
        "src/disagrees.cpp": (
            canonical_digest({"disagrees": 0}),
            canonical_digest({"disagrees": 1}),
        ),
    }
    root, context, snapshots = _context(
        tmp_path,
        sources,
        configurations=configurations,
    )
    disagree_calls = 0

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        nonlocal disagree_calls
        source = next(Path(value) for value in command if value.endswith(".cpp"))
        if source.name == "accepted.cpp":
            return _result([_diagnostic(source, 1, 13)])
        disagree_calls += 1
        return _result([] if disagree_calls == 1 else [_diagnostic(source, 1, 13)])

    outcome = run_cpp_unused_functions(
        root,
        [root / path for path in sources],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "error"
    assert outcome.functions == []
    assert all(target.status == EngineStatus.ERROR for target in outcome.targets)
    assert not any(target.file_path == "src/accepted.cpp" for target in outcome.targets)


def test_missing_compilation_database_is_unavailable_without_execution(tmp_path: Path) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
        database=False,
    )
    called = False

    def runner(*_args: object, **_kwargs: object) -> ProcessResult:
        nonlocal called
        called = True
        return _result()

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "unavailable"
    assert outcome.warnings == [
        "Exact C++ unused-function analysis requires a compilation database"
    ]
    assert not called


def test_compilation_database_without_canonical_digest_fails_before_execution(
    tmp_path: Path,
) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
    )
    context = replace(
        context,
        compilation=replace(context.compilation, database_digest=""),
    )

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: pytest.fail("compiler must not run"),
    )

    assert outcome.mode == "error"
    assert "no canonical digest" in outcome.errors[-1]


def test_translation_unit_ingestion_error_fails_before_execution(tmp_path: Path) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
    )
    unit = replace(
        context.compilation.units[0],
        diagnostics=(
            CompilationDiagnostic(
                code="ambiguous-standard",
                message="Conflicting language standards",
                level="error",
                source="src/main.cpp",
            ),
        ),
    )
    context = replace(
        context,
        compilation=replace(context.compilation, units=(unit,)),
    )

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: pytest.fail("compiler must not run"),
    )

    assert outcome.mode == "error"
    assert "ambiguous-standard" in outcome.errors[-1]


def test_configuration_digest_must_match_the_normalized_command(tmp_path: Path) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
    )
    unit = replace(
        context.compilation.units[0],
        configuration=canonical_digest({"fabricated": True}),
    )
    context = replace(
        context,
        compilation=replace(context.compilation, units=(unit,)),
    )

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: pytest.fail("compiler must not run"),
    )

    assert outcome.mode == "error"
    assert "configuration identity does not match" in outcome.errors[-1]


def test_rule_owned_note_is_not_misclassified_as_an_unused_function(
    tmp_path: Path,
) -> None:
    source = "static void helper() {}\nint main() { return 0; }\n"
    root, context, snapshots = _context(tmp_path, {"src/main.cpp": source})

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: _result(
            [_diagnostic(root / "src/main.cpp", 1, 13, kind="note")]
        ),
    )

    assert outcome.mode == "exact"
    assert outcome.functions == []
    assert outcome.targets[0].status == EngineStatus.PASS


def test_missing_production_configuration_fails_closed(tmp_path: Path) -> None:
    sources = {
        "src/main.cpp": "int main() { return 0; }\n",
        "src/other.cpp": "int other() { return 1; }\n",
    }
    root, context, snapshots = _context(
        tmp_path,
        sources,
        configurations={"src/main.cpp": (canonical_digest({"main": 1}),)},
    )

    outcome = run_cpp_unused_functions(
        root,
        [root / path for path in sources],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: pytest.fail("compiler must not run"),
    )

    assert outcome.mode == "error"
    assert outcome.configurations_checked == 0
    assert outcome.targets[0].file_path == "src/other.cpp"
    assert outcome.targets[0].status == EngineStatus.ERROR


@pytest.mark.parametrize(
    "result",
    [
        ProcessResult(-9, "", "", 0.01),
        ProcessResult(0, "", "", 0.01, timed_out=True),
        ProcessResult(0, "", "", 0.01, truncated=True),
        ProcessResult(0, "", "not a diagnostic", 0.01),
        ProcessResult(1, "", json.dumps([]), 0.01),
    ],
)
def test_process_and_output_failures_are_not_partial_evidence(
    tmp_path: Path,
    result: ProcessResult,
) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
    )

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=lambda *_args, **_kwargs: result,
    )

    assert outcome.mode == "error"
    assert outcome.functions == []
    assert outcome.targets[-1].status == EngineStatus.ERROR
    assert outcome.evidence[-1].error


def test_first_unit_failure_stops_later_compiler_replays(tmp_path: Path) -> None:
    sources = {
        "src/a.cpp": "int a() { return 0; }\n",
        "src/b.cpp": "int b() { return 0; }\n",
    }
    root, context, snapshots = _context(tmp_path, sources)
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        commands.append(command)
        return ProcessResult(1, "", json.dumps([]), 0.01)

    outcome = run_cpp_unused_functions(
        root,
        [root / path for path in sources],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "error"
    assert len(commands) == 1
    assert any(Path(value).name == "a.cpp" for value in commands[0])


def test_source_change_during_probe_is_rejected(tmp_path: Path) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
    )

    def runner(*_args: object, **_kwargs: object) -> ProcessResult:
        (root / "src/main.cpp").write_text("int main() { return 1; }\n", encoding="utf-8")
        return _result()

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "error"
    assert "source changed during compiler analysis" in outcome.errors[-1]


def test_compiler_replacement_during_probe_is_rejected(tmp_path: Path) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
    )
    compiler = Path(context.capabilities.capabilities["g++"].path)

    def runner(*_args: object, **_kwargs: object) -> ProcessResult:
        compiler.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        return _result()

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "error"
    assert "compiler identity changed during execution" in outcome.errors[-1]


def test_working_directory_replacement_during_probe_is_rejected(tmp_path: Path) -> None:
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": "int main() { return 0; }\n"},
    )
    outside = tmp_path / "outside"
    outside.mkdir()

    def runner(*_args: object, **_kwargs: object) -> ProcessResult:
        (root / "build").rename(root / "original-build")
        (root / "build").symlink_to(outside, target_is_directory=True)
        return _result()

    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=runner,
    )

    assert outcome.mode == "error"
    assert "working directory changed during execution" in outcome.errors[-1]


def test_real_gcc_reports_only_the_unused_internal_function(tmp_path: Path) -> None:
    compiler_value = shutil.which("g++")
    if compiler_value is None:
        pytest.skip("g++ is unavailable")
    compiler = Path(compiler_value).resolve(strict=True)
    version_result = run_process(
        [str(compiler), "-dumpfullversion", "-dumpversion"],
        timeout=5.0,
        max_output_chars=1_024,
    )
    try:
        version = tuple(int(part) for part in version_result.stdout.strip().split("."))
    except ValueError:
        pytest.skip("g++ version is not parseable")
    if version < (9,):
        pytest.skip("GCC JSON diagnostics require GCC 9+")
    source_text = (
        "static int unused_internal() { return 1; }\n"
        "namespace { int unused_anonymous() { return 2; } }\n"
        "static int used_internal() { return 3; }\n"
        "[[maybe_unused]] static int intentionally_unused() { return 4; }\n"
        "inline int unused_inline() { return 5; }\n"
        "template <typename T> int unused_template() { return 6; }\n"
        "int external_unreferenced() { return 7; }\n"
        "int main() { return used_internal(); }\n"
    )
    root, context, snapshots = _context(
        tmp_path,
        {"src/main.cpp": source_text},
        compiler=compiler,
    )
    outcome = run_cpp_unused_functions(
        root,
        [root / "src/main.cpp"],
        context,
        source_texts=snapshots,
        runner=run_process,
    )

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert [(item.target.file_path, item.target.start_line) for item in outcome.functions] == [
        ("src/main.cpp", 1),
        ("src/main.cpp", 2),
    ]
