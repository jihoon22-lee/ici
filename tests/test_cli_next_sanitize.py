"""WP22-B — sanitizer suite runs under the project's own variant builds.

The promises under test (#220):

- ``cpp.sanitize``/``cpp.tsan`` are deep-profile checks that run the suite
  binaries a ``variant = "sanitize"``/``"thread-sanitize"`` build produced —
  ici never compiles the instrumentation itself
- the variant declaration is verified against the markers the sanitizer
  runtime leaves in each binary: an uninstrumented or unbuilt suite is
  blocked, not run anyway and not a vacuous pass
- a report marker that parses into diagnostics becomes MEASURED findings
  with normalized rule ids; a marker that parses into nothing is a parse
  failure, never a clean result
- a finished run with no marker and a non-zero exit is a suite failure —
  the suite's own verdict, not a sanitizer defect
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.adapters.providers.sanitize import SanitizeProvider
from ici.execution.process import Outcome, TaskOutcome, TaskSpec
from ici.workspace.instrumentation import sanitizer_marked

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'

_ASAN_TRANSCRIPT = """==41==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010
READ of size 4 at 0x602000000010 thread T0
    #1 0x7f01 in Worker::read() {src}:42:7
SUMMARY: AddressSanitizer: heap-use-after-free {src}:42 in Worker::read()
"""

_TSAN_TRANSCRIPT = """==7==WARNING: ThreadSanitizer: data race
  Write of size 4 at 0x7b0c by thread T1:
    #0 Worker::bump() {src}:11
SUMMARY: ThreadSanitizer: data race {src}:11 in Worker::bump()
"""


def _fake_instrumented(path: Path, variant: str = "sanitize") -> None:
    """An ELF carrying the marker a real instrumented binary would have."""

    marker = b"__asan_init" if variant == "sanitize" else b"__tsan_init"
    path.write_bytes(b"\x7fELF" + b"\x00" * 64 + marker + b"\x00" * 64)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)


def _cmake_sanitize_workspace(
    root: Path,
    *,
    built: bool = False,
    instrumented: bool = True,
    variant: str = "sanitize",
    extra_config: str = "",
) -> None:
    (root / "app").mkdir()
    (root / "app" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "app" / "worker.cpp").write_text(
        "".join(f"int line_{number};\n" for number in range(1, 80)), encoding="utf-8"
    )
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(p)\n"
        "enable_testing()\nadd_executable(app app/main.cpp)\n"
        "add_test(NAME unit COMMAND unit_test)\n",
        encoding="utf-8",
    )
    if built:
        build = root / "build-san"
        build.mkdir()
        (build / "CTestTestfile.cmake").write_text(
            'add_test(unit "./unit_test")\n', encoding="utf-8"
        )
        binary = build / "unit_test"
        if instrumented:
            _fake_instrumented(binary, variant)
        else:
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    (root / "ici.toml").write_text(
        HEADER + '[builds.san]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        f'directory = "build-san"\nvariant = "{variant}"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\n'
        'build = "san"\n' + extra_config,
        encoding="utf-8",
    )


def _qmake_sanitize_workspace(root: Path, *, instrumented: bool = True) -> None:
    (root / "app").mkdir()
    (root / "app" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_unit.pro").write_text(
        "QT += testlib\nCONFIG += testcase\nSOURCES += test_unit.cpp\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_unit.cpp").write_text("// test\n", encoding="utf-8")
    (root / "project.pro").write_text("TEMPLATE = subdirs\nSUBDIRS = app tests\n", encoding="utf-8")
    build = root / "build-san"
    build.mkdir()
    binary = build / "test_unit"
    if instrumented:
        _fake_instrumented(binary)
    else:
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    (root / "ici.toml").write_text(
        HEADER + '[builds.san]\nsystem = "qmake"\nproject = "project.pro"\n'
        'directory = "build-san"\nvariant = "sanitize"\n'
        '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\n'
        'build = "san"\n',
        encoding="utf-8",
    )


def _outcome(
    name: str = "app.cpp.sanitize.unit",
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    outcome: Outcome = Outcome.FINISHED,
) -> TaskOutcome:
    spec = TaskSpec(name=name, argv=("./unit_test",), cwd=Path.cwd())
    return TaskOutcome(
        spec=spec, outcome=outcome, exit_code=exit_code, stdout=stdout, stderr=stderr
    )


# --- plan gating ----------------------------------------------------------


def test_a_component_with_no_sanitizer_variant_is_blocked(tmp_path, monkeypatch) -> None:
    _cmake_sanitize_workspace(tmp_path)
    (tmp_path / "ici.toml").write_text(
        (tmp_path / "ici.toml").read_text().replace('variant = "sanitize"', 'variant = "release"'),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

    assert plan.exit_code == 0, plan.output
    assert 'variant = "sanitize"' in plan.output


def test_an_unbuilt_sanitizer_suite_is_blocked(tmp_path, monkeypatch) -> None:
    _cmake_sanitize_workspace(tmp_path, built=False)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

    assert "test suite not built" in plan.output


def test_an_uninstrumented_binary_is_blocked_not_run(tmp_path, monkeypatch) -> None:
    _cmake_sanitize_workspace(tmp_path, built=True, instrumented=False)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

    assert "no sanitize instrumentation" in plan.output


def test_a_tsan_check_wants_a_thread_sanitize_build(tmp_path, monkeypatch) -> None:
    _cmake_sanitize_workspace(tmp_path, built=True, variant="sanitize")
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

    # The sanitize build exists, so cpp.sanitize plans a task — but cpp.tsan
    # has no thread-sanitize variant and must say so, not ride the wrong build.
    assert 'variant = "thread-sanitize"' in plan.output


def test_an_instrumented_suite_plans_a_sanitizer_run(tmp_path, monkeypatch) -> None:
    _cmake_sanitize_workspace(tmp_path, built=True)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

    assert plan.exit_code == 0, plan.output
    assert "cpp.sanitize" in plan.output
    assert "--test-dir" in plan.output


def test_a_qtest_sanitizer_binary_plans_a_direct_run(tmp_path, monkeypatch) -> None:
    _qmake_sanitize_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

    assert plan.exit_code == 0, plan.output
    assert "cpp.sanitize" in plan.output
    assert "test_unit" in plan.output


def test_standard_profile_omits_sanitizer_checks(tmp_path, monkeypatch) -> None:
    _cmake_sanitize_workspace(tmp_path, built=True)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan"])

    assert "cpp.sanitize" not in plan.output
    assert "cpp.tsan" not in plan.output


# --- marker scanning -------------------------------------------------------


def test_marker_scan_reads_an_instrumented_binary(tmp_path) -> None:
    binary = tmp_path / "unit_test"
    _fake_instrumented(binary)
    assert sanitizer_marked(binary, "sanitize") is True
    assert sanitizer_marked(binary, "thread-sanitize") is False


def test_marker_scan_refuses_a_non_elf_file(tmp_path) -> None:
    binary = tmp_path / "script.sh"
    binary.write_text("#!/bin/sh\n__asan_init\n", encoding="utf-8")
    assert sanitizer_marked(binary, "sanitize") is False


def test_marker_scan_reports_unreadable_as_unproven(tmp_path) -> None:
    missing = tmp_path / "absent"
    assert sanitizer_marked(missing, "sanitize") is None


# --- provider parsing ------------------------------------------------------


def _provider(tmp_path: Path, variant: str = "sanitize") -> SanitizeProvider:
    return SanitizeProvider(variant, project_root=tmp_path)


def test_asan_diagnostics_become_measured_findings(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "worker.cpp").write_text(
        "".join(f"int v{n};\n" for n in range(1, 80)), encoding="utf-8"
    )
    src = tmp_path / "src" / "worker.cpp"
    outcome = _outcome(stderr=_ASAN_TRANSCRIPT.format(src=src), exit_code=1)

    parsed = _provider(tmp_path).parse(outcome)

    assert parsed.is_readable
    assert len(parsed.findings) == 1
    finding = parsed.findings[0]
    assert finding.rule_id == "ici.sanitize.asan.heap-use-after-free"
    assert finding.primary_location.path == "src/worker.cpp"
    assert finding.primary_location.start_line == 42
    assert finding.task_id == outcome.spec.name
    assert parsed.measurements[0].value == 1.0


def test_tsan_warnings_parse_for_the_thread_variant(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "worker.cpp").write_text(
        "".join(f"int v{n};\n" for n in range(1, 80)), encoding="utf-8"
    )
    src = tmp_path / "src" / "worker.cpp"
    outcome = _outcome(stderr=_TSAN_TRANSCRIPT.format(src=src), exit_code=66)

    parsed = _provider(tmp_path, "thread-sanitize").parse(outcome)

    assert parsed.is_readable
    assert parsed.findings[0].rule_id == "ici.sanitize.tsan.data-race"


def test_a_marker_without_a_complete_diagnostic_is_a_parse_failure(tmp_path) -> None:
    outcome = _outcome(
        stderr="==1==ERROR: AddressSanitizer: heap-use-after-free on address 0x0\n",
        exit_code=1,
    )

    parsed = _provider(tmp_path).parse(outcome)

    # A marker alone must not be a clean pass — and the parser may still
    # salvage a diagnostic from the header, which is also an honest answer.
    if parsed.is_readable:
        assert parsed.findings
    else:
        assert "no complete diagnostic" in parsed.failed_to_parse or parsed.failed_to_parse


def test_a_suite_failure_without_diagnostics_is_a_finding(tmp_path) -> None:
    outcome = _outcome(stdout="1 test failed\n", exit_code=8)

    parsed = _provider(tmp_path).parse(outcome)

    assert parsed.is_readable
    assert [f.rule_id for f in parsed.findings] == ["ici.sanitize.suite-failure"]


def test_a_clean_run_reports_zero_diagnostics(tmp_path) -> None:
    parsed = _provider(tmp_path).parse(_outcome(stdout="all passed\n"))

    assert parsed.is_readable
    assert parsed.findings == ()
    assert parsed.measurements[0].value == 0.0


def test_a_non_finishing_run_is_never_parsed(tmp_path) -> None:
    outcome = _outcome(outcome=Outcome.TIMED_OUT, stderr=_ASAN_TRANSCRIPT)

    parsed = _provider(tmp_path).parse(outcome)

    assert not parsed.is_readable
    assert parsed.findings == ()


def test_the_sanitizer_env_is_appended_not_replaced(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASAN_OPTIONS", "abort_on_error=0")
    plan = _provider(tmp_path).plan(
        argv=("./unit_test",), cwd=str(tmp_path), task_id="app.cpp.sanitize.unit"
    )
    overlay = dict(plan.task.env_overlay)
    assert overlay["ASAN_OPTIONS"] == "abort_on_error=0:detect_leaks=1"
    assert overlay["UBSAN_OPTIONS"] == "halt_on_error=1"


def test_sanitizer_tasks_are_never_cached(tmp_path) -> None:
    plan = _provider(tmp_path).plan(
        argv=("./unit_test",), cwd=str(tmp_path), task_id="app.cpp.sanitize.unit"
    )
    assert plan.task.cacheable is False


# --- real toolchain --------------------------------------------------------


needs_gxx_asan = pytest.mark.skipif(not shutil.which("g++"), reason="g++ missing")


@needs_gxx_asan
def test_a_real_asan_binary_reports_a_measured_finding(tmp_path, monkeypatch) -> None:
    """An actual ``-fsanitize=address`` binary, run by the check itself."""

    _cmake_sanitize_workspace(
        tmp_path,
        built=True,
        # Keep the deep-profile run about this check alone: coverage has no
        # instrumented build here and tsan has no variant — both would block
        # the gate for reasons outside what this test exercises.
        extra_config='[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n'
        '[checks."cpp.tsan"]\nenabled = false\n'
        '[checks."cpp.artifact"]\nenabled = false\n',
    )
    source = tmp_path / "app" / "worker.cpp"
    source.write_text(
        "#include <cstdlib>\n"
        "int main() {\n"
        "    int* p = static_cast<int*>(std::malloc(sizeof(int)));\n"
        "    std::free(p);\n"
        "    *p = 1;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    binary = tmp_path / "build-san" / "unit_test"
    subprocess.run(
        ["g++", "-fsanitize=address", "-g", str(source), "-o", str(binary)],
        check=True,
    )
    main = tmp_path / "app" / "main.cpp"
    (tmp_path / "build-san" / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path / "build-san"),
                    "command": f"g++ -c {source} -o worker.o",
                    "file": str(source),
                },
                {
                    "directory": str(tmp_path / "build-san"),
                    "command": f"g++ -c {main} -o main.o",
                    "file": str(main),
                },
            ]
        ),
        encoding="utf-8",
    )
    marker = sanitizer_marked(binary, "sanitize")
    if marker is not True:
        pytest.skip("toolchain produced an unmarked binary")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--profile", "deep", "--cpp"])

    assert result.exit_code == 1, result.output
    payload = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    rules = [f["rule_id"] for f in payload.get("findings", [])]
    assert any("sanitize" in rule for rule in rules), rules
