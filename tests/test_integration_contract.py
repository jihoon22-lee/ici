"""Shell-free integration case parsing and placeholder resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines._integration import IntegrationConfigError, parse_integration_cases
from ici.engines.integration import IntegrationEngine


def test_case_parser_preserves_typed_assertions() -> None:
    cases = parse_integration_cases(
        {
            "cases": [
                {
                    "name": "smoke",
                    "argv": ["{python:current}", "producer.py", "{artifact:app}"],
                    "expected_exit": 3,
                    "stdout_contains": ["ok"],
                    "output_artifacts": [
                        {"path": "reports/result.json", "kind": "report", "min_size": 2}
                    ],
                    "env": {"MODE": "test"},
                }
            ]
        }
    )

    assert cases[0].argv[-1] == "{artifact:app}"
    assert cases[0].expected_exit == 3
    assert cases[0].output_artifacts[0].path == "reports/result.json"
    assert cases[0].env == (("MODE", "test"),)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ({"name": "empty", "argv": []}, "must not be empty"),
        (
            {
                "name": "escape",
                "argv": ["{artifact:tool}"],
                "output_artifacts": [{"path": "../x"}],
            },
            "contained",
        ),
        ({"name": "shell", "argv": ["sh", "-c", "echo"]}, "typed Python or artifact"),
        (
            {"name": "timeout", "argv": ["{artifact:tool}"], "timeout_seconds": 301},
            "between",
        ),
    ],
)
def test_invalid_case_contracts_fail_before_execution(case: dict, message: str) -> None:
    with pytest.raises(IntegrationConfigError, match=message):
        parse_integration_cases({"cases": [case]})


def test_placeholder_resolution_is_whole_token_and_catalog_only(tmp_path: Path) -> None:
    case = parse_integration_cases(
        {"cases": [{"name": "smoke", "argv": ["{python:current}", "{artifact:app}"]}]}
    )[0]
    app = tmp_path / "app"

    assert IntegrationEngine._resolve_argv(case, {"app": app}, {"current": "/python"}) == [
        "/python",
        str(app),
    ]
    unknown = parse_integration_cases({"cases": [{"name": "bad", "argv": ["{artifact:missing}"]}]})[
        0
    ]
    with pytest.raises(IntegrationConfigError, match="unknown artifact"):
        IntegrationEngine._resolve_argv(unknown, {}, {})
    embedded = parse_integration_cases(
        {
            "cases": [
                {
                    "name": "bad",
                    "argv": ["{python:current}", "prefix-{artifact:app}"],
                }
            ]
        }
    )[0]
    with pytest.raises(IntegrationConfigError, match="non-whole"):
        IntegrationEngine._resolve_argv(embedded, {"app": app}, {"current": "/python"})


def test_engine_runs_python_contract_with_explicit_environment_and_output(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    config = {
        "engines": {
            "integration": {
                "enabled": True,
                "cases": [
                    {
                        "name": "python-to-report",
                        "argv": [
                            "{python:current}",
                            "-c",
                            (
                                "import os, pathlib; "
                                "pathlib.Path('reports/result.json').write_text("
                                "os.environ['MODE'], encoding='utf-8'); "
                                "print('contract-ok')"
                            ),
                        ],
                        "env": {"MODE": "verified"},
                        "stdout_contains": ["contract-ok"],
                        "output_artifacts": [
                            {"path": "reports/result.json", "kind": "report", "min_size": 8}
                        ],
                    }
                ],
            }
        }
    }

    result = IntegrationEngine(tmp_path, config).run()

    assert result.status is EngineStatus.PASS
    assert result.evidence is EvidenceState.MEASURED
    assert not result.findings
    assert result.targets[0].file_path == "ici.toml"
    assert (tmp_path / "reports" / "result.json").read_text(encoding="utf-8") == "verified"
    assert result.tool_evidence[0].argv[0]


def test_engine_returns_not_run_error_for_unknown_artifact(tmp_path: Path) -> None:
    config = {
        "engines": {
            "integration": {
                "enabled": True,
                "cases": [{"name": "missing", "argv": ["{artifact:missing}"]}],
            }
        }
    }

    result = IntegrationEngine(tmp_path, config).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert "unknown artifact" in result.summary


def test_optional_failed_assertions_warn_with_native_finding(tmp_path: Path) -> None:
    config = {
        "engines": {
            "integration": {
                "enabled": True,
                "cases": [
                    {
                        "name": "optional-contract",
                        "argv": [
                            "{python:current}",
                            "-c",
                            "import sys; print('actual'); sys.stderr.write('unsafe'); sys.exit(2)",
                        ],
                        "expected_exit": 0,
                        "stdout_contains": ["expected"],
                        "stderr_not_contains": ["unsafe"],
                        "required": False,
                    }
                ],
            }
        }
    }

    result = IntegrationEngine(tmp_path, config).run()

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert result.findings[0].rule_id == "ici.hybrid.process-contract"
    assert result.targets[0].status is EngineStatus.WARN
    assert result.extra["integration"]["cases"][0]["status"] == "WARN"
    assert result.extra["integration"]["cases"][0]["assertions"] == {
        "exit_code": False,
        "stdout_contains": False,
        "stderr_contains": True,
        "stdout_not_contains": True,
        "stderr_not_contains": False,
        "output_artifacts": True,
    }


def test_empty_case_policy_distinguishes_optional_and_required(tmp_path: Path) -> None:
    optional = IntegrationEngine(
        tmp_path,
        {"engines": {"integration": {"enabled": True}}},
    ).run()
    required = IntegrationEngine(
        tmp_path,
        {"engines": {"integration": {"enabled": True, "required": True}}},
    ).run()

    assert optional.status is EngineStatus.SKIP
    assert optional.evidence is EvidenceState.NOT_APPLICABLE
    assert optional.targets[0].status is EngineStatus.SKIP
    assert required.status is EngineStatus.ERROR
    assert required.evidence is EvidenceState.NOT_RUN
    assert "has no cases" in required.summary


def test_unavailable_python_target_fails_before_case_execution(tmp_path: Path) -> None:
    config = {
        "engines": {
            "integration": {
                "enabled": True,
                "python_targets": {"missing": "tools/missing-python"},
                "cases": [{"name": "smoke", "argv": ["{python:missing}"]}],
            }
        }
    }

    result = IntegrationEngine(tmp_path, config).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert "Python target is unavailable" in result.summary
    assert not result.tool_evidence
