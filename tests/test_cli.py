"""Tests for Typer CLI commands."""

from typer.testing import CliRunner

from ici.__main__ import app

runner = CliRunner()


def test_cli_version():
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert "ici 0.1.0" in res.stdout


def test_cli_doctor():
    res = runner.invoke(app, ["doctor", "--brief"])
    assert res.exit_code == 0
    assert "ici 0.1.0 brief" in res.stdout


def test_cli_env():
    res = runner.invoke(app, ["env", "--sh"])
    assert res.exit_code == 0
    assert "export PATH=" in res.stdout

    res_csh = runner.invoke(app, ["env", "--csh"])
    assert res_csh.exit_code == 0
    assert "setenv PATH" in res_csh.stdout
