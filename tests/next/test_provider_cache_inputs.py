"""Changing automatic tool configuration must invalidate an observation."""

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.languages.python.checks import PYTHON_CHECKS


@pytest.mark.parametrize("tool", ["ruff", "mypy"])
def test_config_changes_agree_with_an_uncached_run(tmp_path, monkeypatch, tool):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.chdir(tmp_path)
    check = "python.lint" if tool == "ruff" else "python.type"
    (tmp_path / "ici.toml").write_text(
        'schema_version=1\n[workspace]\nname="example"\n'
        '[[components]]\nid="app"\nroot="."\nlanguages=["python"]\n'
        + "".join(
            f'\n[checks."{c.id}"]\nenabled={str(c.id == check).lower()}\nrequired=true\n'
            for c in PYTHON_CHECKS
        )
    )
    if tool == "ruff":
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg/main.py").write_text("import os\n")
        config = tmp_path / "pkg/ruff.toml"
        before, after = '[lint]\nignore=["F401"]\n', "[lint]\nignore=[]\n"
    else:
        (tmp_path / "main.py").write_text('value: int = "wrong"\n')
        config = tmp_path / "mypy.ini"
        before, after = "[mypy]\nignore_errors=True\n", "[mypy]\nignore_errors=False\n"
    runner = CliRunner()
    config.write_text(before)
    assert runner.invoke(app, ["next", "verify", "--json"]).exit_code == 0
    config.write_text(after)
    cached = runner.invoke(app, ["next", "verify", "--json"])
    uncached = runner.invoke(app, ["next", "verify", "--json", "--no-cache"])
    assert cached.exit_code == uncached.exit_code == 1
    cached_data, uncached_data = json.loads(cached.stdout), json.loads(uncached.stdout)
    assert cached_data["findings"] == uncached_data["findings"]
    assert not cached_data["execution"]["reused_task_ids"]
    config.unlink()
    assert runner.invoke(app, ["next", "verify", "--json"]).exit_code == 1
