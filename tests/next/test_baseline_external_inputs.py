"""External input ownership and in-run drift constrain baseline completion."""

import json
import sys

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.languages.python.checks import PYTHON_CHECKS


def _checks(enabled):
    return "".join(
        f'\n[checks."{check.id}"]\nenabled={str(check.id in enabled).lower()}\nrequired=true\n'
        for check in PYTHON_CHECKS
    )


@pytest.mark.parametrize("shared", [False, True])
def test_partial_recheck_uses_only_the_components_own_externals(tmp_path, monkeypatch, shared):
    monkeypatch.chdir(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "data.txt").write_text("data\n")
    config = 'schema_version=1\n[workspace]\nname="example"\n'
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "main.py").write_text("eval(input())\n")
        config += f'[[components]]\nid="{name}"\nroot="{name}"\nlanguages=["python"]\n'
        if name == "b" or shared:
            config += f"external=[{json.dumps(str(assets))}]\n"
    (tmp_path / "ici.toml").write_text(config + _checks({"python.security"}))
    runner = CliRunner()
    first = runner.invoke(app, ["next", "verify", "--result", "before.json", "--json"])
    assert first.exit_code == 1, first.output
    old = json.loads(first.stdout)
    assert {finding["component_id"] for finding in old["findings"]} == {"a", "b"}
    (tmp_path / "a/main.py").write_text("value=1\n")
    second = runner.invoke(
        app, ["next", "verify", "--component", "a", "--baseline", "before.json", "--json"]
    )
    assert second.exit_code == 0, second.output
    new = json.loads(second.stdout)
    assert len(new["baseline"]["resolved"]) == 1
    assert len(new["baseline"]["carried"]) == 1
    original_a = next(check for check in old["checks"] if check["component_id"] == "a")
    assert new["checks"][0]["scope_digest"] == original_a["scope_digest"]


@pytest.mark.parametrize(
    "change",
    ["file-content", "file-remove", "file-create", "dir-add", "dir-remove", "dir-content", "none"],
)
def test_external_drift_cannot_resolve_an_old_finding(tmp_path, monkeypatch, change):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    if change.startswith("dir-"):
        external.mkdir()
        (external / "member.txt").write_text("before\n")
    elif change != "file-create":
        external.write_text("before\n")
    monkeypatch.chdir(project)
    script = project / "probe.py"
    script.write_text("raise SystemExit(1)\n")
    (project / "ici.toml").write_text(
        'schema_version=1\n[workspace]\nname="example"\n'
        '[[components]]\nid="app"\nroot="."\nlanguages=["python"]\n'
        f"external=[{json.dumps(str(external))}]\n"
        f"[components.python]\nexecutable={json.dumps(sys.executable)}\n"
        '[[components.integrations]]\nname="probe"\n'
        'argv=["{python:declared}","probe.py"]\nrequired=true\n'
        "[checks.integration]\nenabled=true\nrequired=true\n" + _checks(set())
    )
    runner = CliRunner()
    first = runner.invoke(
        app, ["next", "verify", "--profile", "deep", "--result", "before.json", "--json"]
    )
    assert first.exit_code == 1, first.output
    commands = {
        "file-content": "path.write_text('after\\n')",
        "file-remove": "path.unlink()",
        "file-create": "path.write_text('created\\n')",
        "dir-add": "(path / 'added.txt').write_text('added\\n')",
        "dir-remove": "(path / 'member.txt').unlink()",
        "dir-content": "(path / 'member.txt').write_text('after\\n')",
        "none": "pass",
    }
    script.write_text(
        f"from pathlib import Path\npath=Path({str(external)!r})\n{commands[change]}\n"
    )
    second = runner.invoke(
        app, ["next", "verify", "--profile", "deep", "--baseline", "before.json", "--json"]
    )
    assert second.exit_code == 0, second.output
    result = json.loads(second.stdout)
    if change == "none":
        assert result["checks"][0]["evidence_complete"]
        assert len(result["baseline"]["resolved"]) == 1
    else:
        assert not result["checks"][0]["evidence_complete"]
        assert result["baseline"]["resolved"] == []
        assert len(result["baseline"]["carried"]) == 1
        assert any("external" in message for message in result["limitations"])
