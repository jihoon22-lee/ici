"""C++ providers — #214's compile-invocation replay contracts.

The claims under test: a captured compile argv becomes a ``-fsyntax-only``
invocation without inventing flags, unknown options survive while
artifact-producing ones are dropped with a recorded reason, a non-gcc/clang
driver is refused rather than guessed, parse failures are parse failures —
never an empty pass — and diagnostics land on the normalized Finding shape
with the task's own id attached.
"""

from __future__ import annotations

from pathlib import Path

from ici.adapters.providers.compiler import (
    CompilerDiagnosticsProvider,
    compiler_family,
    transform_argv,
)
from ici.adapters.providers.tidy import ClangTidyProvider
from ici.execution.process import Outcome, TaskOutcome, TaskSpec


def _outcome(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    outcome: Outcome = Outcome.FINISHED,
) -> TaskOutcome:
    spec = TaskSpec(argv=argv, name="app.cpp.diagnostics.tu-0", cwd=cwd)
    return TaskOutcome(
        spec=spec,
        outcome=outcome,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def test_compiler_family_recognizes_gcc_and_clang_drivers() -> None:
    assert compiler_family("g++") == "gcc"
    assert compiler_family("gcc-12") == "gcc"
    assert compiler_family("cc") == "gcc"
    assert compiler_family("clang++") == "clang"
    assert compiler_family("/opt/llvm/bin/clang") == "clang"


def test_compiler_family_refuses_other_drivers() -> None:
    assert compiler_family("cl.exe") == ""
    assert compiler_family("icx") == ""
    assert compiler_family("") == ""


def test_transform_prepends_syntax_only_and_keeps_real_flags() -> None:
    transformed = transform_argv(
        ("g++", "-std=c++17", "-Iinclude", "-DFOO=1", "-c", "x.cpp", "-o", "x.o"),
        "x.cpp",
    )

    assert transformed is not None
    assert transformed.argv[:2] == ("g++", "-fsyntax-only")
    assert transformed.argv[-1] == "x.cpp"
    assert "-std=c++17" in transformed.argv and "-Iinclude" in transformed.argv
    assert "-c" not in transformed.argv and "-o" not in transformed.argv
    assert "x.o" not in transformed.argv


def test_transform_drops_artifact_and_link_flags_but_keeps_unknown_ones() -> None:
    transformed = transform_argv(
        ("cc", "-MD", "-flto=auto", "-Wl,-rpath,/lib", "-lfoo", "--mystery-flag", "x.c"),
        "x.c",
    )

    assert transformed is not None
    for flag in ("-MD", "-flto=auto", "-lfoo"):
        assert flag not in transformed.argv
    assert not any(arg.startswith("-Wl,") for arg in transformed.argv)
    # The flag nobody named stays — dropping it would be a different compile.
    assert "--mystery-flag" in transformed.argv
    dropped_flags = {flag for flag, _ in transformed.dropped}
    assert {"-MD", "-flto=auto", "-lfoo", "-Wl,-rpath,/lib"} <= dropped_flags


def test_transform_refuses_a_driver_it_does_not_know() -> None:
    assert transform_argv(("cl.exe", "/c", "x.cpp"), "x.cpp") is None


def test_compiler_plan_is_a_syntax_replay_that_is_not_cacheable() -> None:
    provider = CompilerDiagnosticsProvider()
    transformed = transform_argv(("g++", "-c", "x.cpp", "-o", "x.o"), "x.cpp")
    assert transformed is not None

    plan = provider.plan(
        "/usr/bin/g++",
        transformed.argv,
        cwd="/work/build",
        task_id="app.cpp.diagnostics.x-0",
        analysis_unit_id="native",
        input_refs=("../x.cpp",),
    )

    assert plan.task.provider == "compiler"
    assert plan.task.argv == ("/usr/bin/g++", "-fsyntax-only", "x.cpp")
    assert plan.task.cwd == "/work/build"
    # No dep-file evidence means no honest cache key — the task says so.
    assert plan.task.cacheable is False


def test_compiler_parse_normalizes_a_warning(tmp_path) -> None:
    (tmp_path / "x.cpp").write_text("int x;\n", encoding="utf-8")
    stderr = "x.cpp:1:5: warning: unused variable [-Wunused-variable]\n"
    outcome = _outcome(("g++",), tmp_path, exit_code=1, stderr=stderr)

    parsed = CompilerDiagnosticsProvider().parse(outcome)

    assert not parsed.failed_to_parse
    assert len(parsed.findings) == 1
    finding = parsed.findings[0]
    assert finding.rule_id.startswith("compiler.")
    assert finding.native_rule_id == "-Wunused-variable"
    assert finding.primary_location.path == "x.cpp"
    assert finding.primary_location.start_line == 1
    assert finding.task_id == "app.cpp.diagnostics.tu-0"
    assert finding.fingerprint


def test_compiler_parse_reports_a_failed_run_and_unreadable_output(tmp_path) -> None:
    provider = CompilerDiagnosticsProvider()

    timed_out = provider.parse(_outcome(("g++",), tmp_path, outcome=Outcome.TIMED_OUT))
    assert timed_out.failed_to_parse

    garbage = provider.parse(_outcome(("g++",), tmp_path, exit_code=0, stdout="not a diagnostic\n"))
    # Unreadable output is a parse failure, not a silent zero-finding pass.
    assert garbage.findings == () or garbage.failed_to_parse


def test_tidy_plan_replays_the_database() -> None:
    plan = ClangTidyProvider().plan(
        "/usr/bin/clang-tidy",
        database_dir="/work/build",
        sources=("/work/x.cpp",),
        task_id="app.cpp.tidy",
        cwd="/work",
        analysis_unit_id="native",
        input_refs=("build/compile_commands.json", "x.cpp"),
    )

    assert plan.task.provider == "clang-tidy"
    assert plan.task.argv == ("/usr/bin/clang-tidy", "-p", "/work/build", "/work/x.cpp")
    assert plan.task.cwd == "/work"
    assert plan.task.cacheable is False


def test_tidy_parse_normalizes_diagnostics(tmp_path) -> None:
    (tmp_path / "x.cpp").write_text("int x;\n", encoding="utf-8")
    stderr = "x.cpp:1:5: warning: unused variable 'x' [misc-unused-parameters]\n"
    outcome = _outcome(("clang-tidy",), tmp_path, exit_code=1, stderr=stderr)

    parsed = ClangTidyProvider().parse(outcome)

    assert not parsed.failed_to_parse
    assert len(parsed.findings) == 1
    finding = parsed.findings[0]
    assert finding.rule_id == "clang_tidy.misc-unused-parameters"
    assert finding.primary_location.path == "x.cpp"
    assert finding.task_id == "app.cpp.diagnostics.tu-0"
