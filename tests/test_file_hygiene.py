"""Tests for file_hygiene engine."""

import stat
from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.file_hygiene import FileHygieneEngine

_CFG = {"engines": {"file_hygiene": {"mode": "pass_warn", "required": False}}}


def test_clean_project_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = FileHygieneEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_exec_bit_on_source_warns(tmp_path: Path):
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    result = FileHygieneEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("ExecBit" in t.target_name for t in result.targets)


def test_crlf_and_bom_warn(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bom.py").write_bytes(b"\xef\xbb\xbfx = 1\r\n")
    result = FileHygieneEngine(tmp_path, _CFG).run()
    names = {t.target_name for t in result.targets}
    assert "Hygiene:Bom" in names
    assert "Hygiene:Crlf" in names


def test_stray_pyc_warns_but_runtime_pycache_ignored(tmp_path: Path):
    cache = tmp_path / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "app.cpython-310.pyc").write_bytes(b"\x00")
    (tmp_path / "src" / "stray.pyc").write_bytes(b"\x00")
    result = FileHygieneEngine(tmp_path, _CFG).run()
    names = {t.target_name for t in result.targets}
    assert "Hygiene:PycFile" in names
    stray = [t for t in result.targets if t.target_name == "Hygiene:PycFile"]
    assert all("stray" in t.file_path for t in stray)


def test_broken_shell_script_warns_with_evidence(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "broken.sh").write_text("if [ then\n", encoding="utf-8")
    result = FileHygieneEngine(tmp_path, _CFG).run()
    assert any("ShellSyntax" in t.target_name for t in result.targets)
    assert result.tool_evidence, "bash -n should be recorded as ToolEvidence"


def test_disabled_checks_are_respected(tmp_path: Path):
    cfg = {
        "engines": {
            "file_hygiene": {
                "mode": "pass_warn",
                "required": False,
                "check_crlf": False,
                "check_bom": False,
            }
        }
    }
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bom.py").write_bytes(b"\xef\xbb\xbfx = 1\r\n")
    result = FileHygieneEngine(tmp_path, cfg).run()
    names = {t.target_name for t in result.targets}
    assert "Hygiene:Bom" not in names and "Hygiene:Crlf" not in names
