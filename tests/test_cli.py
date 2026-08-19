"""Tests for Typer CLI commands."""

from typer.testing import CliRunner

from ici.__main__ import app
from ici.core.models import EngineStatus, VerificationSuiteResult

runner = CliRunner()


def test_cli_version():
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert "ici 0.3.3" in res.stdout


def test_cli_doctor():
    res = runner.invoke(app, ["doctor", "--brief"])
    assert res.exit_code == 0
    assert "ici 0.3.3 brief" in res.stdout


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


def test_cli_verify_error_suite_exits_nonzero(monkeypatch):
    class ErrorOrchestrator:
        def run_all(self, **kwargs):
            return VerificationSuiteResult(suite_status=EngineStatus.ERROR, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", ErrorOrchestrator)

    res = runner.invoke(app, ["verify"])

    assert res.exit_code == 1


def test_line_command_uses_project_config(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "large.py").write_text("x = 1\n" * 3, encoding="utf-8")
    (tmp_path / "ici.toml").write_text(
        "[engines.line]\nwarn_limit = 1\nfail_limit = 2\nmode = 'pass_warn_fail'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ICI_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["line"])

    assert result.exit_code == 1


def test_cli_reports_config_error_without_traceback(tmp_path, monkeypatch):
    (tmp_path / "ici.toml").write_bytes(b"[engines.line]\nwarn_limit = \xff\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ICI_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["line"])

    assert result.exit_code != 0
    assert "Configuration error:" in result.output
    assert "Traceback" not in result.output


def test_cli_reports_parser_level_ten_thousand_digit_integer_without_traceback(
    tmp_path, monkeypatch
):
    huge_integer = "9" * 10_000
    (tmp_path / "ici.toml").write_text(
        f"[engines.test]\nmin_tem_score = {huge_integer}\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ICI_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["line"])

    assert result.exit_code == 2
    assert "Configuration error:" in result.output
    assert "Traceback" not in result.output
