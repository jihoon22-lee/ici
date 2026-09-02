"""Tests for Typer CLI commands."""

import json

import click
import pytest
from typer.testing import CliRunner

from ici import __version__
from ici.__main__ import app
from ici.core.baseline import BaselineError
from ici.core.models import EngineResult, EngineStatus, SupportMatrix, VerificationSuiteResult
from ici.core.pipeline import AnalysisProfile
from ici.reporters.issue_view import DEFAULT_MAX_FINDINGS, ConsoleGroupBy, ConsoleOptions

runner = CliRunner()


def test_cli_version():
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert f"ici {__version__}" in res.stdout


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


def test_cli_verify_forwards_baseline_options_to_orchestrator(tmp_path, monkeypatch):
    captured = {}

    class FakeOrchestrator:
        def __init__(self, project_root, config):
            captured["project_root"] = project_root
            captured["config"] = config

        def run_all(self, **kwargs):
            captured["run_all"] = kwargs
            return VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FakeOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "verify",
            "--report",
            "--html",
            "report.html",
            "--github-summary",
            "--baseline",
            "baseline.json",
            "--fail-on-new",
            "--write-baseline",
            "new-baseline.json",
        ],
    )

    assert result.exit_code == 0
    assert captured["project_root"] == tmp_path.resolve()
    assert captured["config"] == {}
    assert captured["run_all"] == {
        "report_json": "verify_report.json",
        "report_html": "report.html",
        "github_summary": True,
        "publish": False,
        "baseline_path": tmp_path / "baseline.json",
        "fail_on_new": True,
        "write_baseline": tmp_path / "new-baseline.json",
        "console_options": ConsoleOptions(
            verbose=False,
            max_findings=DEFAULT_MAX_FINDINGS,
            group_by=ConsoleGroupBy.ENGINE,
        ),
        "profile": None,
        "use_cache": True,
    }


def test_cli_verify_forwards_profile_to_orchestrator(tmp_path, monkeypatch):
    captured = {}

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            captured.update(kwargs)
            return VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FakeOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", "--profile", "deep"])

    assert result.exit_code == 0
    assert captured["profile"] is AnalysisProfile.DEEP


def test_cli_verify_help_documents_profile_option():
    result = runner.invoke(app, ["verify", "--help"], color=False)
    output = click.unstyle(result.output)

    assert result.exit_code == 0
    assert "--profile" in output
    assert all(value in output for value in ("fast", "standard", "deep"))


def test_cli_verify_forwards_console_options_to_orchestrator(tmp_path, monkeypatch):
    captured = {}

    class FakeOrchestrator:
        def __init__(self, project_root, config):
            captured["project_root"] = project_root
            captured["config"] = config

        def run_all(self, **kwargs):
            captured["run_all"] = kwargs
            return VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FakeOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "verify",
            "--verbose",
            "--max-findings",
            "7",
            "--group-by",
            "severity",
        ],
    )

    assert result.exit_code == 0
    assert captured["run_all"]["console_options"] == ConsoleOptions(
        verbose=True,
        max_findings=7,
        group_by=ConsoleGroupBy.SEVERITY,
    )


def test_cli_verify_forwards_default_console_options(tmp_path, monkeypatch):
    captured = {}

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            captured.update(kwargs)
            return VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FakeOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 0
    assert captured["console_options"] == ConsoleOptions()
    assert captured["use_cache"] is True


def test_cli_verify_forwards_no_cache_to_orchestrator(tmp_path, monkeypatch):
    captured = {}

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            captured.update(kwargs)
            return VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FakeOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", "--no-cache"])

    assert result.exit_code == 0
    assert captured["use_cache"] is False


def test_cli_cache_inventory_and_clear_output(tmp_path, monkeypatch):
    cache_root = tmp_path / "analysis-cache"
    entries = cache_root / "entries-v1"
    entries.mkdir(parents=True)
    invalid = entries / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    temporary = entries / ".entry.tmp"
    temporary.write_text("in-progress", encoding="utf-8")

    monkeypatch.setenv("ICI_CACHE_DIR", str(cache_root))
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    inventory = runner.invoke(app, ["cache"])

    assert inventory.exit_code == 0
    assert f"Cache directory: {cache_root.resolve()}" in inventory.output
    assert "Entries: 0 valid, 1 corrupt" in inventory.output
    assert "Key contract: ici.analysis-cache-key/v3" in inventory.output

    cleared = runner.invoke(app, ["cache", "--clear"])

    assert cleared.exit_code == 0
    assert "Removed 2 cache file(s)." in cleared.output
    assert "Entries: 0 valid, 0 corrupt, 0 bytes" in cleared.output
    assert not invalid.exists()
    assert not temporary.exists()


@pytest.mark.parametrize(
    ("argv", "option_name"),
    [
        (["--max-findings=-1"], "--max-findings"),
        (["--max-findings", "not-an-integer"], "--max-findings"),
        (["--group-by", "unknown"], "--group-by"),
    ],
)
def test_cli_verify_rejects_invalid_console_options(tmp_path, monkeypatch, argv, option_name):
    class UnexpectedOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            pytest.fail("orchestrator must not be constructed for invalid console options")

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", UnexpectedOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", *argv], color=True)

    assert result.exit_code == 2
    plain_output = click.unstyle(result.output)
    assert "Invalid value" in plain_output
    assert option_name in plain_output


def test_cli_verify_accepts_zero_max_findings(tmp_path, monkeypatch):
    captured = {}

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            captured.update(kwargs)
            return VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FakeOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", "--max-findings", "0"])

    assert result.exit_code == 0
    assert captured["console_options"].max_findings == 0


def test_cli_engine_commands_do_not_accept_console_options(monkeypatch, tmp_path):
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["line", "--verbose"])

    assert result.exit_code == 2
    assert "No such option" in result.output


def test_cli_verify_fail_on_new_requires_baseline(tmp_path, monkeypatch):
    class UnexpectedOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            del kwargs
            pytest.fail("orchestrator must not run without a baseline")

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", UnexpectedOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", "--fail-on-new"])

    assert result.exit_code == 2
    assert "--fail-on-new requires --baseline" in result.output


@pytest.mark.parametrize("option", ["--baseline", "--write-baseline"])
@pytest.mark.parametrize("path", ["../outside.json", "escape/outside.json"])
def test_cli_verify_rejects_baseline_paths_outside_project_root(
    tmp_path, monkeypatch, option, path
):
    outside = tmp_path.parent / f"ici-cli-outside-{tmp_path.name}"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)

    class UnexpectedOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            del kwargs
            pytest.fail("orchestrator must not run for an unsafe baseline path")

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", UnexpectedOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", option, path])

    assert result.exit_code == 2
    assert "Baseline error:" in result.output
    assert "outside project root" in result.output


def test_cli_verify_rejects_report_baseline_collision(tmp_path, monkeypatch):
    class UnexpectedOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            del kwargs
            pytest.fail("orchestrator must not run when report output collides")

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", UnexpectedOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", "--report", "--write-baseline", "verify_report.json"])

    assert result.exit_code == 2
    assert "--write-baseline must not overwrite --report output" in result.output


def test_cli_verify_maps_baseline_error_to_exit_code_two(tmp_path, monkeypatch):
    class FailingOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            del kwargs
            raise BaselineError("invalid baseline")

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FailingOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", "--baseline", "baseline.json"])

    assert result.exit_code == 2
    assert "Baseline error: invalid baseline" in result.output
    assert "Traceback" not in result.output


def test_cli_verify_allows_same_baseline_input_and_output_path(tmp_path, monkeypatch):
    captured = {}

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def run_all(self, **kwargs):
            captured.update(kwargs)
            return VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])

    monkeypatch.setattr("ici.__main__.VerifyOrchestrator", FakeOrchestrator)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["verify", "--baseline", "shared.json", "--write-baseline", "shared.json"],
    )

    assert result.exit_code == 0
    assert captured["baseline_path"] == tmp_path / "shared.json"
    assert captured["write_baseline"] == tmp_path / "shared.json"


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


def test_cli_dead_prepares_and_injects_shared_analysis_context(tmp_path, monkeypatch):
    marker = object()
    project = object()
    captured = {}

    class ContextDead:
        ANALYSIS_CONTEXT_ENGINES = frozenset({"dead"})

        def __init__(self, project_root, config, *, analysis_context=None):
            captured["engine_root"] = project_root
            captured["engine_config"] = config
            captured["analysis_context"] = analysis_context

        def run(self):
            return EngineResult("dead", EngineStatus.PASS, "exact context received")

    def prepare(project_root, config, **kwargs):
        captured["prepare_root"] = project_root
        captured["prepare_config"] = config
        captured["prepare_kwargs"] = kwargs
        return project, marker

    config = {"ici": {"profile": "deep"}}
    monkeypatch.setattr("ici.__main__.DeadCodeEngine", ContextDead)
    monkeypatch.setattr("ici.__main__.prepare_analysis_context", prepare)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(
        "ici.__main__.evaluate_support_matrix",
        lambda *args, **kwargs: SupportMatrix(),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["dead"])

    assert result.exit_code == 0
    assert captured["analysis_context"] is marker
    assert captured["engine_root"] == tmp_path.resolve()
    assert captured["prepare_root"] == tmp_path.resolve()
    assert captured["prepare_kwargs"] == {
        "engine_names": frozenset({"dead"}),
        "profile": "deep",
        "probe_all_tools": False,
    }


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
    assert data["schema_version"] == "ici.result/v3"
    assert data["engine_name"] == "line"
    assert "tool_evidence" in data
    assert data["support_matrix"] is not None
    assert {item["engine_name"] for item in data["support_matrix"]["entries"]} == {"line"}


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
