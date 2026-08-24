"""Tests for Typer CLI commands."""

import json

import pytest
from typer.testing import CliRunner

from ici import __version__
from ici.__main__ import app
from ici.core.models import EngineResult, EngineStatus, VerificationSuiteResult

runner = CliRunner()


def test_cli_version():
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert f"ici {__version__}" in res.stdout
    assert __version__ == "0.5.0"


def test_cli_doctor():
    res = runner.invoke(app, ["doctor", "--brief"])
    assert res.exit_code == 0
    assert f"ici {__version__} brief" in res.stdout


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


def test_cli_verify_skip_suite_uses_skip_exit_code(monkeypatch):
    class SkippedOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            return VerificationSuiteResult(suite_status=EngineStatus.SKIP, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", SkippedOrchestrator)

    res = runner.invoke(app, ["verify"])

    assert res.exit_code == 2


@pytest.mark.parametrize(
    "command, engine_name",
    [("sanitize", "sanitize"), ("dead", "dead"), ("exception", "exception")],
)
@pytest.mark.parametrize("status, exit_code", [(EngineStatus.ERROR, 1), (EngineStatus.SKIP, 2)])
def test_cli_safety_commands_map_error_and_skip_to_exit_codes(
    monkeypatch, command, engine_name, status, exit_code
):
    class FakeEngine:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run(self):
            return EngineResult(
                engine_name=engine_name,
                status=status,
                summary=f"{status.value} result",
                required=False,
            )

    engine_attr = {
        "sanitize": "SanitizeEngine",
        "dead": "DeadCodeEngine",
        "exception": "ExceptionSafetyEngine",
    }[command]
    monkeypatch.setattr(f"ici.__main__.{engine_attr}", FakeEngine)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})

    result = runner.invoke(app, [command])

    assert result.exit_code == exit_code


@pytest.mark.parametrize(
    ("command", "engine_attr", "engine_name"),
    [
        ("build", "BuildEngine", "build"),
        ("line", "LineCountEngine", "line"),
        ("lint", "LintEngine", "lint"),
        ("test", "TestEngine", "test"),
        ("type", "TypeCheckEngine", "type"),
        ("complexity", "ComplexityEngine", "complexity"),
        ("dup", "DuplicateEngine", "dup"),
        ("sanitize", "SanitizeEngine", "sanitize"),
        ("dead", "DeadCodeEngine", "dead"),
        ("exception", "ExceptionSafetyEngine", "exception"),
    ],
)
def test_cli_all_engine_commands_map_skip_to_exit_code(
    monkeypatch, command, engine_attr, engine_name
):
    class FakeEngine:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run(self):
            return EngineResult(
                engine_name=engine_name,
                status=EngineStatus.SKIP,
                summary="not run [unsafe]",
                required=False,
            )

    monkeypatch.setattr(f"ici.__main__.{engine_attr}", FakeEngine)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})

    result = runner.invoke(app, [command])

    assert result.exit_code == 2


def test_build_error_is_not_reported_as_green_success(monkeypatch):
    class ErrorBuild:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run(self):
            return EngineResult(
                engine_name="build",
                status=EngineStatus.ERROR,
                summary="build failed [unsafe]",
                required=True,
            )

    monkeypatch.setattr("ici.__main__.BuildEngine", ErrorBuild)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})

    result = runner.invoke(app, ["build"])

    assert result.exit_code == 1
    assert "build failed [unsafe]" in result.output
    assert "✔" not in result.output


def test_standalone_report_uses_v2_serializer(tmp_path, monkeypatch):
    class FakeLine:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run(self):
            return EngineResult(
                engine_name="line",
                status=EngineStatus.WARN,
                summary="warning",
                required=False,
            )

    monkeypatch.setattr("ici.__main__.LineCountEngine", FakeLine)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["line", "--report"])

    assert result.exit_code == 0
    data = json.loads((tmp_path / "line_report.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == "ici.result/v2"
    assert data["engine_name"] == "line"
    assert "tool_evidence" in data


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


@pytest.mark.parametrize(
    "pathological_config",
    [
        pytest.param("value = " + "[" * 1000 + "1" + "]" * 1000 + "\n", id="nested-arrays"),
        pytest.param("a" + ".a" * 1000 + " = 1\n", id="dotted-key-parts"),
    ],
)
def test_cli_reports_pathological_toml_without_traceback(
    tmp_path, monkeypatch, pathological_config
):
    (tmp_path / "ici.toml").write_text(pathological_config, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ICI_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["line"])

    assert result.exit_code == 2
    assert "Configuration error:" in result.output
    assert "Traceback" not in result.output
