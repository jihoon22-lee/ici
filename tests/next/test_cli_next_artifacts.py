"""#220 — the declared-artifact contract, checked apart from any build run.

``[builds.<id>] artifacts`` is the build's claim about its own outputs;
``cpp.artifact`` verifies it against the tree the project's build left —
ici never builds, and a declaration the tree cannot satisfy is a finding,
not silence. The check is advisory by default (a component whose builds
declare nothing has no contract to break) and ``required = true`` is how a
workspace makes a broken contract fail the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ici.__main__ import app

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'

#: The artifact contract is the subject — the compile-input checks are
#: disabled so the suite stays hermetic (no compiler or build tool needed).
OFF = (
    '[checks."cpp.compile"]\nenabled = false\n'
    '[checks."cpp.diagnostics"]\nenabled = false\n'
    '[checks."cpp.tidy"]\nenabled = false\n'
    '[checks."cpp.test"]\nenabled = false\n'
    '[checks."cpp.coverage"]\nenabled = false\n'
)


def _workspace(root: Path, artifacts: str | None = None, extra: str = "") -> None:
    declared = f"artifacts = [{artifacts}]\n" if artifacts is not None else ""
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "ici.toml").write_text(
        HEADER + '[builds.release]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "build"\nvariant = "release"\n'
        f"{declared}"
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\n'
        'build = "release"\n'
        f"{OFF}{extra}",
        encoding="utf-8",
    )


def test_the_check_is_blocked_where_no_contract_exists(tmp_path, monkeypatch) -> None:
    _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan"])

    assert result.exit_code == 0, result.output
    assert "app.cpp.artifact: blocked — no artifact contract" in result.output


def test_declared_outputs_are_verified_against_the_build_tree(tmp_path, monkeypatch) -> None:
    _workspace(tmp_path, artifacts='"app/app", "lib/*.a"')
    (tmp_path / "build" / "app").mkdir(parents=True)
    (tmp_path / "build" / "app" / "app").write_bytes(b"\x7fELF fake")
    (tmp_path / "build" / "lib").mkdir()
    (tmp_path / "build" / "lib" / "core.a").write_bytes(b"!<arch>")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    assert not [f for f in document["findings"] if f["rule_id"].startswith("artifact.")]
    satisfied = next(m for m in document["metrics"] if m["name"] == "artifacts.satisfied")
    assert (satisfied["numerator"], satisfied["denominator"]) == (2, 2)


def test_a_declared_but_absent_output_is_a_measured_finding(tmp_path, monkeypatch) -> None:
    _workspace(tmp_path, artifacts='"bin/app"')
    (tmp_path / "build").mkdir()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    # Advisory by default: the finding is recorded, the run's gate passes.
    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    missing = [f for f in document["findings"] if f["rule_id"] == "artifact.missing"]
    assert len(missing) == 1
    assert missing[0]["evidence"] == "MEASURED"
    assert "bin/app" in missing[0]["message"]


def test_a_required_contract_fails_the_gate_when_broken(tmp_path, monkeypatch) -> None:
    _workspace(
        tmp_path,
        artifacts='"bin/app"',
        extra='[checks."cpp.artifact"]\nrequired = true\n',
    )
    (tmp_path / "build").mkdir()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 1, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    assert document["gate"]["selected"] == "FAIL"


def test_a_missing_build_directory_produces_nothing(tmp_path, monkeypatch) -> None:
    _workspace(tmp_path, artifacts='"bin/app"')

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    missing = [f for f in document["findings"] if f["rule_id"] == "artifact.missing"]
    assert len(missing) == 1
    assert "build directory is absent" in missing[0]["message"]


def test_a_glob_matching_only_directories_satisfies_nothing(tmp_path, monkeypatch) -> None:
    _workspace(tmp_path, artifacts='"bin/*"')
    (tmp_path / "build" / "bin" / "subdir").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    missing = [f for f in document["findings"] if f["rule_id"] == "artifact.missing"]
    assert len(missing) == 1
    assert "non-file" in missing[0]["message"]


def test_an_escaping_match_is_invalid_not_an_output(tmp_path, monkeypatch) -> None:
    _workspace(tmp_path, artifacts='"bin/app"')
    outside = tmp_path.parent / "outside-workspace.txt"
    outside.write_text("x\n", encoding="utf-8")
    link_dir = tmp_path / "build" / "bin"
    link_dir.mkdir(parents=True)
    (link_dir / "app").symlink_to(outside)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    rules = {f["rule_id"] for f in document["findings"]}
    assert "artifact.invalid" in rules


def test_an_escaping_glob_is_a_config_error_not_a_silent_drop(tmp_path, monkeypatch) -> None:
    _workspace(tmp_path, artifacts='"../outside.txt"')
    (tmp_path / "outside.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 2, result.output
    assert "artifact glob" in result.output
