"""Python runtime and source-floor compatibility engine tests."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest
from packaging.version import Version

from ici.config import DEFAULT_CONFIG
from ici.config_schema import ConfigError, validate_config
from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.engines._python_compatibility import (
    PythonMetadataError,
    analyze_static_compatibility,
    inferred_target_version,
    parse_runtime_version,
    requires_python_allows,
)
from ici.engines.python_compat import PythonCompatibilityEngine


def _project(tmp_path: Path, *, requires_python: str = ">=3.10") -> Path:
    package = tmp_path / "src" / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "1.0.0"\nrequires-python = "{requires_python}"\n',
        encoding="utf-8",
    )
    return package


def _config(**overrides: object) -> dict[str, object]:
    policy: dict[str, object] = {
        "enabled": True,
        "mode": "pass_warn_fail",
        "required": True,
        "interpreters": [],
        "required_interpreters": [],
        "imports": ["demo"],
        "target_version": "3.10",
    }
    policy.update(overrides)
    return {
        "project": {"source_dirs": ["src"]},
        "engines": {"python_compat": policy},
    }


def test_current_interpreter_compiles_and_imports_without_project_bytecode(
    tmp_path: Path,
) -> None:
    _project(tmp_path)

    result = PythonCompatibilityEngine(tmp_path, _config()).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.MEASURED
    assert [item.name for item in result.tool_evidence] == [
        "python -VV",
        "python -m compileall",
        "python import smoke",
    ]
    assert result.tool_evidence[0].argv == [str(Path(sys.executable).resolve()), "-VV"]
    assert result.tool_evidence[0].version.startswith("3.10")
    assert result.tool_evidence[1].argv[1:4] == ["-B", "-m", "compileall"]
    assert result.tool_evidence[2].argv[1:3] == ["-I", "-B"]
    assert any(target.target_name.endswith(":Verified") for target in result.targets)
    assert not list(tmp_path.rglob("__pycache__"))


def test_required_runtime_version_mismatch_is_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, requires_python=">=3.12")

    def fake_run(argv: list[str], **_kwargs: object) -> ProcessResult:
        stdout = "Python 3.10.21" if argv[-1] == "-VV" else ""
        return ProcessResult(0, stdout, "", 0.01)

    monkeypatch.setattr("ici.engines.python_compat.run_process", fake_run)
    config = _config(
        interpreters=[sys.executable],
        required_interpreters=[sys.executable],
        target_version="3.12",
    )

    result = PythonCompatibilityEngine(tmp_path, config).run()

    assert result.status == EngineStatus.FAIL
    mismatch = next(target for target in result.targets if target.status == EngineStatus.FAIL)
    assert "does not satisfy requires-python >=3.12" in mismatch.message
    assert len(result.tool_evidence) == 3


@pytest.mark.parametrize(
    ("required_interpreters", "expected_status", "expected_evidence"),
    [
        ([], EngineStatus.WARN, EvidenceState.MEASURED),
        (["python-that-does-not-exist-ici"], EngineStatus.ERROR, EvidenceState.NOT_RUN),
    ],
)
def test_unavailable_interpreter_distinguishes_optional_and_required_policy(
    tmp_path: Path,
    required_interpreters: list[str],
    expected_status: EngineStatus,
    expected_evidence: EvidenceState,
) -> None:
    _project(tmp_path)
    missing = "python-that-does-not-exist-ici"
    config = _config(
        interpreters=[missing],
        required_interpreters=required_interpreters,
    )

    result = PythonCompatibilityEngine(tmp_path, config).run()

    assert result.status == expected_status
    assert result.evidence == expected_evidence
    target = next(
        target for target in result.targets if target.target_name.endswith(":Unavailable")
    )
    assert target.metrics["required"] == int(bool(required_interpreters))
    assert result.tool_evidence == []


def test_static_floor_reports_imports_and_qualified_apis_at_exact_lines() -> None:
    source = (
        "from typing import Self\nimport tomllib\nimport pathlib as paths\nWALK = paths.Path.walk\n"
    )

    result = analyze_static_compatibility("src/demo.py", source, (3, 10))

    warnings = [target for target in result.targets if "StandardLibraryFloor" in target.target_name]
    assert [(target.start_line, target.message.split()[0]) for target in warnings] == [
        (1, "typing.Self"),
        (2, "tomllib"),
        (4, "pathlib.Path.walk"),
    ]
    assert all(target.start_column is not None for target in warnings)


def test_static_floor_does_not_reuse_an_import_shadowed_by_a_parameter() -> None:
    source = "import typing\ndef use(typing):\n    return typing.Self\n"

    result = analyze_static_compatibility("src/demo.py", source, (3, 10))

    assert not result.targets


def test_static_floor_reports_newer_syntax_when_host_can_parse_it() -> None:
    if sys.version_info < (3, 11):
        pytest.skip("host AST cannot parse except-star syntax")
    source = "try:\n    pass\nexcept* ValueError:\n    pass\n"

    result = analyze_static_compatibility("src/demo.py", source, (3, 10))

    warning = next(target for target in result.targets if "SyntaxFloor" in target.target_name)
    assert warning.start_line == 3
    assert "Python 3.10 floor" in warning.message


def test_invalid_metadata_fails_closed(tmp_path: Path) -> None:
    package = tmp_path / "src"
    package.mkdir()
    (package / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nrequires-python = 42\n", encoding="utf-8")

    result = PythonCompatibilityEngine(tmp_path, _config(imports=[])).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.targets[0].file_path == "pyproject.toml"


def test_aggregate_ast_budget_fails_closed_before_runtime_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    monkeypatch.setattr("ici.engines.python_compat.MAX_COMPAT_TOTAL_AST_NODES", 1)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(argv)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.python_compat.run_process", fake_run)

    result = PythonCompatibilityEngine(tmp_path, _config(imports=[])).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert "aggregate AST exceeds" in result.summary
    assert calls == []


def test_unparseable_source_does_not_execute_runtime_or_import_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(argv)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.python_compat.run_process", fake_run)

    result = PythonCompatibilityEngine(tmp_path, _config(imports=["broken"])).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert "syntax could not be parsed" in result.summary
    assert calls == []


def test_no_python_source_is_not_applicable(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    result = PythonCompatibilityEngine(tmp_path, _config()).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert result.tool_evidence == []


def test_runtime_version_and_requires_python_parsing_is_pep_440_aware() -> None:
    assert parse_runtime_version("Python 3.13.2 (main, build)\n[GCC]") == Version("3.13.2")
    assert parse_runtime_version("Python 3.14.0rc2 (main, build)\n[GCC]") == Version("3.14.0rc2")
    assert requires_python_allows(">=3.10,<3.13", Version("3.12.9"))
    assert not requires_python_allows(">=3.10,<3.13", Version("3.13.0"))
    assert inferred_target_version(">=3.10,<3.13") == (3, 10)
    assert inferred_target_version("~=3.10.5") == (3, 10)
    assert inferred_target_version("==3.10.21") == (3, 10)
    with pytest.raises(PythonMetadataError, match="invalid"):
        requires_python_allows("not a specifier", Version("3.10"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target_version", "2.7", "target_version"),
        ("imports", ["not-a-module"], "dotted Python module"),
        ("interpreters", ["python3", "python3"], "duplicate"),
        ("required_interpreters", ["python3.10"], "subset"),
    ],
)
def test_python_compat_config_rejects_ambiguous_or_unsafe_values(
    field: str, value: object, message: str
) -> None:
    config = deepcopy(DEFAULT_CONFIG)
    config["engines"]["python_compat"][field] = value

    with pytest.raises(ConfigError, match=message):
        validate_config(config)
