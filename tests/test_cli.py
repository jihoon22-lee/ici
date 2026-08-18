"""Tests for Typer CLI commands."""

from typer.testing import CliRunner

from ici.__main__ import app

runner = CliRunner()


def test_cli_version():
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert "ici 0.3.2" in res.stdout


def test_cli_doctor():
    res = runner.invoke(app, ["doctor", "--brief"])
    assert res.exit_code == 0
    assert "ici 0.3.2 brief" in res.stdout


def test_cli_env():
    res = runner.invoke(app, ["env", "--sh"])
    assert res.exit_code == 0
    assert "export PATH=" in res.stdout

    res_csh = runner.invoke(app, ["env", "--csh"])
    assert res_csh.exit_code == 0
    assert "setenv PATH" in res_csh.stdout


def test_cli_any_command_creates_global_config(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("ICI_CONFIG", raising=False)
    monkeypatch.chdir(proj)

    res = runner.invoke(app, ["doctor", "--brief"])
    assert res.exit_code == 0
    global_conf = xdg / "ici" / "ici.toml"
    assert global_conf.exists()
    assert "engines" in global_conf.read_text(encoding="utf-8")
