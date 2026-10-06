"""Real-toolchain fixtures driven through the next path end to end.

The ``kind = "real-tool"`` entries in ``tests/fixtures/manifest.toml`` need an
actual compiler or build system — these tests provide it. The pattern is the
same one CI follows: the *test* owns the build (cmake, qmake, g++), and ici
verifies what the build left behind. ici never compiles anything itself.

Each fixture is copied into a scratch workspace first: verify writes ``.ici/``
beside the config and the build products must not land in ``examples/``.

When a toolchain piece is missing the manifest decides skip vs fail —
``ICI_REQUIRE_BUILD_ADAPTERS=1`` (set in CI) turns the skip into a failure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.fixture_manifest import require
from typer.testing import CliRunner

from ici.__main__ import app
from ici.workspace.instrumentation import sanitizer_marked

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "corpus"\n'


def _prepare(fixture_id: str, tmp_path: Path) -> Path:
    """Copy the registered fixture into a scratch workspace."""

    entry = require(fixture_id)
    target = tmp_path / entry.path.name
    shutil.copytree(entry.path, target)
    return target


def _run(*argv: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        list(argv), cwd=cwd, capture_output=True, text=True, check=False, timeout=600
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"{' '.join(argv)} exited {completed.returncode}\n"
            f"stdout: {completed.stdout[-3000:]}\nstderr: {completed.stderr[-3000:]}"
        )
    return completed


def _result(root: Path) -> dict:
    return json.loads((root / ".ici" / "next" / "result.json").read_text("utf-8"))


def _metrics(result: dict) -> dict[str, dict]:
    return {m["name"]: m for m in result.get("metrics", [])}


def test_asan_fixture_reports_a_sanitizer_finding(tmp_path, monkeypatch) -> None:
    """The registered overflow fixture, compiled with ASan/UBSan and run by ici."""

    root = _prepare("cpp/asan_overflow", tmp_path)

    # The fixture ships sources only — the build contract is the workspace's,
    # so the test supplies the minimal cmake declaration the suite discovery
    # reads, then builds the instrumented binary itself.
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(f)\n"
        "enable_testing()\nadd_test(NAME unit COMMAND unit_test)\n",
        encoding="utf-8",
    )
    build = root / "build-san"
    build.mkdir()
    binary = build / "unit_test"
    sources = [str(root / "src" / "buffer.cpp"), str(root / "tests" / "test_buffer.cpp")]
    _run("g++", "-fsanitize=address,undefined", "-g", *sources, "-o", str(binary), cwd=root)
    if sanitizer_marked(binary, "sanitize") is not True:
        pytest.skip("toolchain produced an unmarked binary")
    (build / "CTestTestfile.cmake").write_text('add_test(unit "./unit_test")\n', encoding="utf-8")
    (build / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(build),
                    "command": f"g++ -fsanitize=address,undefined -c {src} -o x.o",
                    "file": src,
                }
                for src in sources
            ]
        ),
        encoding="utf-8",
    )
    (root / "ici.toml").write_text(
        HEADER + '[builds.san]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "build-san"\nvariant = "sanitize"\n'
        '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\n'
        'build = "san"\nsources = ["src/**/*.cpp", "src/**/*.hpp", "tests/**/*.cpp"]\n'
        # Keep the deep run about the sanitizer: no gcov/tsan/artifact
        # evidence exists here, so those checks would only block the gate.
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n'
        '[checks."cpp.tsan"]\nenabled = false\n'
        '[checks."cpp.artifact"]\nenabled = false\n'
        '[checks."cpp.binary-compat"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    result = runner.invoke(app, ["verify", "--profile", "deep", "--cpp"])

    assert result.exit_code in (1, 3), result.output
    stored = _result(root)
    rules = {finding["rule_id"] for finding in stored["findings"]}
    assert any(rule.startswith("ici.sanitize.") for rule in rules), (
        f"expected a sanitizer diagnostic, got rules: {sorted(rules)}"
    )


def test_cmake_fixture_builds_tests_and_measures_coverage(tmp_path, monkeypatch) -> None:
    """Real cmake configure + build + ctest + gcov, verified by the next path."""

    root = _prepare("cpp/cmake_project", tmp_path)
    (root / "ici.toml").write_text(
        HEADER + '[builds.dev]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "build"\nvariant = "coverage"\n'
        '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\n'
        'build = "dev"\nsources = ["src/**/*.cpp", "src/**/*.hpp", "tests/**/*.cpp"]\n'
        # Heuristic checks are not the subject here; keep the gate about the
        # evidence the build produced.
        '[checks."cpp.dup"]\nrequired = false\n'
        '[checks."cpp.complexity"]\nrequired = false\n'
        '[checks."cpp.cognitive"]\nrequired = false\n'
        '[checks."cpp.exception"]\nrequired = false\n'
        '[checks."cpp.artifact"]\nenabled = false\n'
        '[checks."cpp.binary-compat"]\nenabled = false\n',
        encoding="utf-8",
    )
    _run(
        "cmake",
        "-S",
        ".",
        "-B",
        "build",
        "-DCMAKE_BUILD_TYPE=Debug",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DCMAKE_CXX_FLAGS=--coverage",
        "-DCMAKE_EXE_LINKER_FLAGS=--coverage",
        cwd=root,
    )
    _run("cmake", "--build", "build", "-j4", cwd=root)
    monkeypatch.chdir(root)

    result = runner.invoke(app, ["verify", "--cpp"])

    assert result.exit_code in (0, 1), result.output
    stored = _result(root)
    metrics = _metrics(stored)
    cases = metrics.get("ctest.cases")
    assert cases is not None and cases.get("denominator") == 1, (
        f"expected exactly one ctest suite case, got: {cases}"
    )
    assert cases["numerator"] == cases["denominator"], "test_counter must pass"
    lines = metrics.get("coverage.cpp.lines")
    assert lines is not None and lines.get("value", 0) > 0, (
        f"gcov evidence missing: {sorted(metrics)}"
    )


def test_qmake_fixture_builds_and_reports_the_qtest_suite(tmp_path, monkeypatch) -> None:
    """A real qmake SUBDIRS build: one QTest binary, discovered and run."""

    root = _prepare("cpp/qmake_project", tmp_path)
    qmake = shutil.which("qmake6") or shutil.which("qmake") or "qmake"
    build = root / "build" / "ici-qmake-build"
    build.mkdir(parents=True)
    _run(qmake, str(root / "qmake_project.pro"), cwd=build)
    _run("make", "-j4", cwd=build)

    # qmake does not emit a compile database — the project supplies one the
    # same way a real workspace would (bear/compiledb or a generator), and
    # the compile checks read it where the build declared it.
    compile_commands = [
        {
            "directory": str(build / "src"),
            "command": f"g++ -c {root / 'src' / 'counter.cpp'} -o counter.o",
            "file": str(root / "src" / "counter.cpp"),
        },
        {
            "directory": str(build / "tests"),
            "command": f"g++ -c {root / 'tests' / 'test_counter.cpp'} -o test_counter.o",
            "file": str(root / "tests" / "test_counter.cpp"),
        },
    ]
    (build / "compile_commands.json").write_text(json.dumps(compile_commands), encoding="utf-8")
    (root / "ici.toml").write_text(
        HEADER + '[builds.dev]\nsystem = "qmake"\nproject = "qmake_project.pro"\n'
        'directory = "build/ici-qmake-build"\nvariant = "default"\n'
        '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\n'
        'build = "dev"\nsources = ["src/**/*.cpp", "src/**/*.hpp", "tests/**/*.cpp"]\n'
        '[checks."cpp.coverage"]\nenabled = false\n'
        '[checks."cpp.artifact"]\nenabled = false\n'
        '[checks."cpp.binary-compat"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    result = runner.invoke(app, ["verify", "--cpp"])

    assert result.exit_code in (0, 1), result.output
    stored = _result(root)
    metrics = _metrics(stored)
    cases = metrics.get("qtest.cases")
    assert cases is not None, f"no qtest evidence recorded: {sorted(metrics)}"
    # qtest.cases counts cases inside the binary, and the fixture's test
    # binary is test_counter with its four functions — the register's "one
    # reported test" is about the suite the SUBDIRS build produced.
    assert cases["denominator"] >= 1 and cases["numerator"] == cases["denominator"], (
        f"test_counter's cases must all pass: {cases}"
    )
