"""Tests for pyproject_lint engine."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.pyproject_lint import PyProjectLintEngine

_CFG = {"engines": {"pyproject_lint": {"mode": "pass_warn", "required": False}}}


def test_no_pyproject_without_python_sources_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    result = PyProjectLintEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_missing_pyproject_with_python_sources_warns(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = PyProjectLintEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("Missing" in t.target_name for t in result.targets)


def test_valid_metadata_passes(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0.0"\n'
        'requires-python = ">=3.10"\ndependencies = ["rich>=13"]\n'
        '[project.scripts]\ndemo = "demo.cli:main"\n',
        encoding="utf-8",
    )
    result = PyProjectLintEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS
    assert any(t.status == EngineStatus.PASS for t in result.targets)


def test_bad_name_and_scripts_warn(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "bad name!"\nversion = "1.0.0"\nrequires-python = ">=3.10"\n'
        '[project.scripts]\ndemo = "not-a-entrypoint"\n',
        encoding="utf-8",
    )
    result = PyProjectLintEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    names = {t.target_name for t in result.targets}
    assert "PyProjectLint:Name" in names
    assert "PyProjectLint:Scripts" in names


def test_missing_requires_python_warns(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    result = PyProjectLintEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("RequiresPython" in t.target_name for t in result.targets)


def test_malformed_toml_or_missing_project_table_warns(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nkey = 1\n", encoding="utf-8")
    result = PyProjectLintEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("ProjectTable" in t.target_name for t in result.targets)
