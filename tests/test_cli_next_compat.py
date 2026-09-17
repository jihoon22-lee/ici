"""WP22-C — compatibility migration: static floor, declared runtime, ELF/ABI.

The promises under test (#220 item 5):

- ``python.compat`` runs the same ``analyze_static_compatibility`` the
  stable engine uses, against the floor the component's — or the
  workspace's — ``requires-python`` declares; a component with no floor is
  told so, not silently passed
- ``python.compat-runtime`` asks the *declared* interpreter itself: ``-VV``
  against the floor, then ``compileall`` over the scope. Missing
  interpreter, unparseable output and a runtime outside the floor are
  blocked, failed-to-parse or findings — never a pass
- ``cpp.binary-compat`` reads only the ELF binaries the artifact contract
  names, through ``readelf`` — never executing them — and judges them by
  the stable policy (absolute/build-path loader entries are forbidden)
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.adapters.providers.binarycompat import BinaryCompatProvider
from ici.adapters.providers.pycompat import CompileallProvider, PythonVersionProvider
from ici.execution.process import Outcome, TaskOutcome, TaskSpec
from ici.languages.compat import CompatRequest, measure_python_compat

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'

_READELF = """
ELF Header:
  Class:                             ELF64
  Type:                              DYN (Shared object file)
  Machine:                           Advanced Micro Devices X86-64
Section Headers:
  [ 1] .interp PROGBITS 0000
  [27] .symtab SYMTAB 0000
Dynamic section:
 0x1 (NEEDED) Shared library: [libstdc++.so.6]
 0x1 (NEEDED) Shared library: [libc.so.6]
 0x1d (RUNPATH) Library runpath: [$ORIGIN/../lib]
Version needs section:
  0x0010: Name: GLIBC_2.17  Flags: none  Version: 4
  0x0030: Name: GLIBCXX_3.4.29  Flags: none  Version: 2
"""

_COMPILEALL_FAIL = """*** Error compiling '/ws/app/broken.py'...
  File "/ws/app/broken.py", line 3
    def broken(:
               ^
SyntaxError: invalid syntax
"""


def _outcome(
    name: str = "app.python.compat-runtime.version",
    *,
    argv: tuple[str, ...] = ("python", "-VV"),
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    outcome: Outcome = Outcome.FINISHED,
) -> TaskOutcome:
    spec = TaskSpec(
        name=name,
        argv=argv,
        cwd=cwd or Path.cwd(),
        environment=env or {},
    )
    return TaskOutcome(
        spec=spec, outcome=outcome, exit_code=exit_code, stdout=stdout, stderr=stderr
    )


def _python_workspace(
    root: Path,
    *,
    requires_python: str | None = ">=3.8",
    source: str = "import tomllib\n",
    interpreter: str | None = sys.executable,
) -> None:
    (root / "app").mkdir()
    (root / "app" / "modern.py").write_text(source, encoding="utf-8")
    if requires_python is not None:
        (root / "app" / "pyproject.toml").write_text(
            f'[project]\nname = "app"\nrequires-python = "{requires_python}"\n',
            encoding="utf-8",
        )
    declared = (
        f'[components.python]\nexecutable = "{interpreter}"\n' if interpreter is not None else ""
    )
    (root / "ici.toml").write_text(
        HEADER
        + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        + declared
        + '[components.checks."python.test"]\nenabled = false\n'
        + '[components.checks."python.coverage"]\nenabled = false\n'
        + '[components.checks."python.type"]\nenabled = false\n',
        encoding="utf-8",
    )


def _cpp_workspace(root: Path, *, artifacts: str | None = '"libapp.so"') -> None:
    declared = f"artifacts = [{artifacts}]\n" if artifacts is not None else ""
    (root / "app").mkdir()
    (root / "app" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "ici.toml").write_text(
        HEADER + '[builds.main]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "build"\n'
        f"{declared}"
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\n'
        'build = "main"\n'
        '[checks."cpp.compile"]\nenabled = false\n'
        '[checks."cpp.diagnostics"]\nenabled = false\n'
        '[checks."cpp.tidy"]\nenabled = false\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )


# --- the static floor scan ------------------------------------------------


def test_a_use_above_the_declared_floor_is_a_measured_finding(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "modern.py").write_text("import tomllib\n", encoding="utf-8")
    (tmp_path / "app" / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.8"\n', encoding="utf-8"
    )
    request = CompatRequest(
        project_root=tmp_path / "app",
        files=(tmp_path / "app" / "modern.py",),
        task_id="app.python.compat",
        component_id="app",
        workspace_root=tmp_path,
    )

    observation = measure_python_compat(request)

    assert observation.state.value == "SUCCEEDED"
    rules = {finding.rule_id for finding in observation.findings}
    assert rules == {"python.compat.standard-library-floor"}
    finding = observation.findings[0]
    assert finding.evidence.value == "MEASURED"
    assert finding.primary_location.path == "modern.py"
    assert "floor asserted: Python 3.8" in "\n".join(observation.limitations)


def test_a_syntax_floor_violation_is_reported(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    source = tmp_path / "app" / "modern.py"
    source.write_text("match x:\n    case 1:\n        pass\n", encoding="utf-8")
    (tmp_path / "app" / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.8"\n', encoding="utf-8"
    )
    request = CompatRequest(
        project_root=tmp_path / "app",
        files=(source,),
        task_id="app.python.compat",
        component_id="app",
    )

    observation = measure_python_compat(request)

    rules = {finding.rule_id for finding in observation.findings}
    assert "python.compat.syntax-floor" in rules


def test_no_declared_floor_is_a_limitation_not_a_pass(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "modern.py").write_text("import tomllib\n", encoding="utf-8")
    request = CompatRequest(
        project_root=tmp_path / "app",
        files=(tmp_path / "app" / "modern.py",),
        task_id="app.python.compat",
        component_id="app",
        workspace_root=tmp_path,
    )

    observation = measure_python_compat(request)

    assert not observation.findings
    assert "no requires-python floor could be inferred" in "\n".join(observation.limitations)


def test_the_workspace_floor_applies_when_the_component_declares_none(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "modern.py").write_text("import tomllib\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.8"\n', encoding="utf-8"
    )
    request = CompatRequest(
        project_root=tmp_path / "app",
        files=(tmp_path / "app" / "modern.py",),
        task_id="app.python.compat",
        component_id="app",
        workspace_root=tmp_path,
    )

    observation = measure_python_compat(request)

    assert {f.rule_id for f in observation.findings} == {"python.compat.standard-library-floor"}


# --- the declared runtime --------------------------------------------------


def test_a_runtime_inside_the_floor_is_recorded_not_judged_wrong() -> None:
    provider = PythonVersionProvider()
    outcome = _outcome(
        stdout="Python 3.11.9 (main, Nov  1 2023) [GCC 12]",
        env={"ICI_REQUIRES_PYTHON": ">=3.10"},
    )

    parsed = provider.parse(outcome)

    assert not parsed.findings
    assert "runtime satisfies requires-python '>=3.10'" in "\n".join(parsed.limitations)


def test_a_runtime_outside_the_floor_is_a_finding() -> None:
    provider = PythonVersionProvider()
    outcome = _outcome(
        stdout="Python 3.9.18 [GCC 11]",
        env={"ICI_REQUIRES_PYTHON": ">=3.10"},
    )

    parsed = provider.parse(outcome)

    assert [f.rule_id for f in parsed.findings] == ["python.compat.runtime-version"]
    assert parsed.findings[0].primary_location.path == "pyproject.toml"


def test_a_runtime_without_a_floor_is_not_pretended_verified() -> None:
    provider = PythonVersionProvider()
    outcome = _outcome(stdout="Python 3.11.9", env={"ICI_REQUIRES_PYTHON": ""})

    parsed = provider.parse(outcome)

    assert not parsed.findings
    assert "not judged against a floor" in "\n".join(parsed.limitations)


def test_an_unanswerable_interpreter_is_a_parse_failure() -> None:
    provider = PythonVersionProvider()
    outcome = _outcome(exit_code=2, stderr="bad option")

    parsed = provider.parse(outcome)

    assert parsed.failed_to_parse and "exited 2" in parsed.failed_to_parse


def test_unparseable_version_output_is_a_parse_failure() -> None:
    provider = PythonVersionProvider()
    outcome = _outcome(stdout="not a version banner")

    parsed = provider.parse(outcome)

    assert parsed.failed_to_parse


def test_compileall_names_the_file_the_runtime_rejected(tmp_path) -> None:
    provider = CompileallProvider()
    outcome = _outcome(
        name="app.python.compat-runtime.compileall",
        argv=("python", "-B", "-m", "compileall", "-q", "-f", "broken.py"),
        cwd=tmp_path,
        stderr=_COMPILEALL_FAIL,
        exit_code=1,
    )

    parsed = provider.parse(outcome)

    assert [f.rule_id for f in parsed.findings] == ["python.compat.compile-failure"]
    finding = parsed.findings[0]
    assert finding.primary_location.start_line == 3
    assert "SyntaxError" in finding.message


def test_compileall_success_counts_the_files_it_checked(tmp_path) -> None:
    provider = CompileallProvider()
    outcome = _outcome(
        name="app.python.compat-runtime.compileall",
        argv=("python", "-B", "-m", "compileall", "-q", "-f", "a.py", "b.py"),
        cwd=tmp_path,
    )

    parsed = provider.parse(outcome)

    assert not parsed.findings
    assert parsed.measurements[0].value == 2.0


def test_an_unattributable_compileall_failure_is_a_parse_failure(tmp_path) -> None:
    provider = CompileallProvider()
    outcome = _outcome(
        name="app.python.compat-runtime.compileall",
        argv=("python", "-B", "-m", "compileall", "-q", "-f", "a.py"),
        cwd=tmp_path,
        stderr="something else went wrong",
        exit_code=1,
    )

    parsed = provider.parse(outcome)

    assert parsed.failed_to_parse and "cannot be attributed" in parsed.failed_to_parse


# --- binary compatibility ---------------------------------------------------


def _readelf_outcome(
    stdout: str = _READELF,
    *,
    exit_code: int = 0,
    stderr: str = "",
    binary: str = "/ws/build/libapp.so",
    outcome: Outcome = Outcome.FINISHED,
) -> TaskOutcome:
    spec = TaskSpec(
        name="app.cpp.binary-compat.main-1",
        argv=(
            "readelf",
            "--file-header",
            "--sections",
            "--dynamic",
            "--version-info",
            "--wide",
            binary,
        ),
        cwd=Path("/ws"),
    )
    return TaskOutcome(
        spec=spec, outcome=outcome, exit_code=exit_code, stdout=stdout, stderr=stderr
    )


def test_readelf_facts_are_measured_and_the_missing_contract_is_said(tmp_path) -> None:
    provider = BinaryCompatProvider(project_root=Path("/ws"))
    parsed = provider.parse(_readelf_outcome())

    assert not parsed.findings
    assert parsed.measurements[0].name == "binary.checked"
    limitation = "\n".join(parsed.limitations)
    assert "ELF64" in limitation
    assert "no ABI floors are declared" in limitation


def test_an_absolute_loader_path_is_a_policy_finding(tmp_path) -> None:
    transcript = _READELF.replace("$ORIGIN/../lib", "$ORIGIN/../lib:/opt/vendor/lib")
    provider = BinaryCompatProvider(project_root=Path("/ws"))
    parsed = provider.parse(_readelf_outcome(stdout=transcript))

    assert [f.rule_id for f in parsed.findings] == ["ici.binary.forbidden-rpath"]
    assert parsed.findings[0].primary_location.path == "build/libapp.so"


def test_a_build_path_leak_is_a_policy_finding(tmp_path) -> None:
    transcript = _READELF.replace("$ORIGIN/../lib", "/srv/build/main")
    provider = BinaryCompatProvider(
        project_root=Path("/ws"), build_roots=(Path("/srv/build/main"),)
    )
    parsed = provider.parse(_readelf_outcome(stdout=transcript))

    rules = {f.rule_id for f in parsed.findings}
    assert "ici.binary.build-path-leak" in rules


def test_a_failing_or_incomplete_readelf_is_a_parse_failure(tmp_path) -> None:
    provider = BinaryCompatProvider(project_root=Path("/ws"))

    failed = provider.parse(_readelf_outcome(stdout="", exit_code=1, stderr="not an ELF"))
    assert failed.failed_to_parse and "exited 1" in failed.failed_to_parse

    truncated = provider.parse(_readelf_outcome(stdout="ELF Header:\n  Class:  ELF64\n"))
    assert truncated.failed_to_parse and "incomplete" in truncated.failed_to_parse


# --- plan gating -------------------------------------------------------------


def test_a_component_without_an_interpreter_is_blocked(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path, interpreter=None)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan"])

    assert result.exit_code == 0, result.output
    assert "app.python.compat-runtime: blocked — no project interpreter" in result.output


def test_the_declared_interpreter_plans_two_runtime_tasks(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan"])

    assert result.exit_code == 0, result.output
    assert f"app.python.compat-runtime.version: {sys.executable} -VV" in result.output
    assert "app.python.compat-runtime.compileall:" in result.output
    assert "compileall -q -f modern.py" in result.output


def test_binary_compat_is_blocked_without_an_artifact_contract(tmp_path, monkeypatch) -> None:
    _cpp_workspace(tmp_path, artifacts=None)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan"])

    assert result.exit_code == 0, result.output
    assert "app.cpp.binary-compat: blocked — no artifact contract" in result.output


def test_binary_compat_expands_one_readelf_task_per_elf_artifact(tmp_path, monkeypatch) -> None:
    _cpp_workspace(tmp_path)
    build = tmp_path / "build"
    build.mkdir()
    (build / "libapp.so").write_bytes(b"\x7fELF" + b"\x00" * 32)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan"])

    assert result.exit_code == 0, result.output
    assert "readelf --file-header --sections --dynamic --version-info --wide" in result.output
    assert "app.cpp.binary-compat.main-1 needs app.cpp.artifact" in result.output


def test_non_elf_artifacts_leave_binary_compat_blocked(tmp_path, monkeypatch) -> None:
    _cpp_workspace(tmp_path)
    build = tmp_path / "build"
    build.mkdir()
    script = build / "libapp.so"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan"])

    assert result.exit_code == 0, result.output
    assert "matched no ELF binaries" in result.output


# --- end to end ----------------------------------------------------------------


def test_verify_measures_the_declared_runtime(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path, requires_python="<3.0", source="x = 1\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    # Advisory by default: the violation is recorded as a finding, and a
    # workspace that wants it gate-failing declares required = true.
    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    runtime = next(
        f for f in document["findings"] if f["rule_id"] == "python.compat.runtime-version"
    )
    assert "requires-python '<3.0'" in runtime["message"]


needs_gxx_and_readelf = pytest.mark.skipif(
    not (shutil.which("g++") and shutil.which("readelf")),
    reason="g++ or readelf missing",
)


@needs_gxx_and_readelf
def test_verify_reads_a_real_elf_artifact(tmp_path, monkeypatch) -> None:
    _cpp_workspace(tmp_path)
    build = tmp_path / "build"
    build.mkdir()
    subprocess.run(
        [
            "g++",
            "-shared",
            "-o",
            str(build / "libapp.so"),
            str(tmp_path / "app" / "main.cpp"),
        ],
        check=True,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    checked = [m for m in document["metrics"] if m["name"] == "binary.checked"]
    assert checked, "the readelf task produced no measurement"
    limitation = "\n".join(document["limitations"])
    assert "build/libapp.so: ELF" in limitation
