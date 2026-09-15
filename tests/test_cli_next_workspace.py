"""``ici next`` on a workspace — #207 PR C's wiring, end to end.

What changed here versus the single-component path: the commands read the
*workspace* — ``workspace.build`` over the composed config — so selection,
identity and scope are the model's, not one component's. The stored result
carries the real source inventory (content digests, VCS state) and task ids
qualified per component, which is the item-2 promise that a shared source and
a repeated check stay distinguishable facts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app

runner = CliRunner()
RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'


def _write_project(root: Path, config: str) -> None:
    (root / "ici.toml").write_text(config, encoding="utf-8")


def _two_python_components(root: Path) -> None:
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir(parents=True)
    (root / "alpha" / "one.py").write_text("import os\nx = 1\n", encoding="utf-8")
    (root / "beta" / "two.py").write_text("y = 2\n", encoding="utf-8")
    _write_project(
        root,
        HEADER + '[[components]]\nid = "alpha"\nroot = "alpha"\nlanguages = ["python"]\n'
        '[[components]]\nid = "beta"\nroot = "beta"\nlanguages = ["python"]\n',
    )


@needs_ruff
def test_every_component_runs_and_keeps_its_own_task_ids(tmp_path, monkeypatch) -> None:
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 1, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["kind"] == "full"
    assert stored["scope"]["full_required_satisfied"] is True
    task_ids = {finding["task_id"] for finding in stored["findings"]}
    assert task_ids == {"alpha.python.lint"}
    assert stored["scope"]["selected_components"] == ["alpha", "beta"]


@needs_ruff
def test_a_subset_run_says_what_it_omitted(tmp_path, monkeypatch) -> None:
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--component", "beta"])

    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["kind"] == "partial"
    assert stored["scope"]["selected_components"] == ["beta"]
    assert stored["scope"]["omitted_components"] == ["alpha"]
    assert stored["scope"]["required_components"] == ["alpha", "beta"]


def test_an_unknown_component_is_a_config_error(tmp_path, monkeypatch) -> None:
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--component", "ghost"])

    assert result.exit_code == 2
    assert "ghost" in result.output


def test_a_component_cycle_is_a_config_error(tmp_path, monkeypatch) -> None:
    _write_project(
        tmp_path,
        HEADER + '[[components]]\nid = "a"\nroot = "a"\nlanguages = ["python"]\n'
        'needs = ["b"]\n'
        '[[components]]\nid = "b"\nroot = "b"\nlanguages = ["python"]\n'
        'needs = ["a"]\n',
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 2
    assert "cycle" in result.output


@needs_ruff
def test_the_snapshot_is_real_content_not_path_names(tmp_path, monkeypatch) -> None:
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert runner.invoke(app, ["next", "verify"]).exit_code != 2
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    snapshot = stored["identity"]["source"]

    assert snapshot["digest"].startswith("sha256:")
    assert sorted(snapshot["files"]) == ["alpha/one.py", "beta/two.py"]
    # tmp_path is no repository, so the snapshot cannot claim clean.
    assert "commit" not in snapshot
    assert snapshot["dirty"] is True


@needs_ruff
def test_a_repository_supplies_commit_and_cleanliness(tmp_path, monkeypatch) -> None:
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )

    assert runner.invoke(app, ["next", "verify"]).exit_code != 2
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    snapshot = stored["identity"]["source"]

    assert snapshot["commit"] is not None
    # The snapshot is taken before the run writes anything, so the committed
    # tree reads clean; .ici/next/result.json does not exist yet at that point.
    assert snapshot["dirty"] is False


@needs_ruff
def test_a_cpp_component_is_counted_not_dropped(tmp_path, monkeypatch) -> None:
    # Since #208 the C++ pack exists: a cpp component runs cpp.line through the
    # same line counter rather than reporting "no checks apply".
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text("int core() { return 1; }\n", encoding="utf-8")
    _write_project(
        tmp_path,
        HEADER + '[[components]]\nid = "native"\nroot = "native"\nlanguages = ["cpp"]\n',
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    # #212: cpp.line still counts the file, but a component with no
    # compilation database is INCOMPLETE — not a C++ pass.
    assert result.exit_code == 3, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    counted = [m for m in stored["metrics"] if m["name"] == "files_counted"]
    assert counted and counted[0]["value"] == 1
    assert "native.cpp.compile" in stored["execution"]["blocked_task_ids"]


@needs_ruff
def test_a_component_with_no_applicable_checks_is_a_limitation_not_a_crash(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.rs").write_text("fn core() -> i32 { 1 }\n", encoding="utf-8")
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _write_project(
        tmp_path,
        HEADER + '[[components]]\nid = "native"\nroot = "native"\nlanguages = ["rust"]\n'
        '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n',
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert any("native" in item for item in stored["limitations"])


@needs_ruff
def test_a_warm_run_reuses_the_cold_runs_evidence(tmp_path, monkeypatch) -> None:
    # #209: identical inputs reuse the stored observation, and the result says
    # which tasks were reused rather than run.
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)

    result_file = tmp_path / ".ici" / "next" / "result.json"
    cold = runner.invoke(app, ["next", "verify"])
    assert cold.exit_code in (0, 1), cold.output
    cold_result = json.loads(result_file.read_text("utf-8"))
    warm = runner.invoke(app, ["next", "verify"])
    assert warm.exit_code in (0, 1), warm.output

    stored = json.loads(result_file.read_text("utf-8"))
    reused = stored["execution"]["reused_task_ids"]
    assert "alpha.python.lint" in reused and "beta.python.lint" in reused
    # Reuse changes nothing about the answer: the findings are the same set.
    assert cold_result["findings"] == stored["findings"]


@needs_ruff
def test_a_changed_source_is_a_fresh_run_not_a_reused_one(tmp_path, monkeypatch) -> None:
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert runner.invoke(app, ["next", "verify"]).exit_code in (0, 1)
    (tmp_path / "alpha" / "one.py").write_text("import os\nx = 2\n", encoding="utf-8")
    assert runner.invoke(app, ["next", "verify"]).exit_code in (0, 1)

    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert "alpha.python.lint" not in stored["execution"]["reused_task_ids"]


@needs_ruff
def test_no_cache_runs_everything(tmp_path, monkeypatch) -> None:
    _two_python_components(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert runner.invoke(app, ["next", "verify"]).exit_code in (0, 1)
    assert runner.invoke(app, ["next", "verify", "--no-cache"]).exit_code in (0, 1)

    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["execution"]["reused_task_ids"] == []
