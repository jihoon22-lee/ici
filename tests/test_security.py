"""Tests for security engine."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.security import SecurityEngine

_CFG = {"engines": {"security": {"mode": "pass_warn", "scan_tests": False}}}


def test_clean_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_hardcoded_secret_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text('password = "super-secret-12345"\n', encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("HardcodedSecret" in t.target_name for t in result.targets)


def test_weak_crypto_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("import hashlib\nhashlib.md5(b'hello')\n", encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("WeakCrypto" in t.target_name for t in result.targets)


def test_eval_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("eval(user_input)\n", encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("EvalExec" in t.target_name for t in result.targets)


def test_nosec_suppresses(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text('password = "secret123"  # nosec\n', encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_tests_skipped_by_default(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text('password = "secret12345"\n', encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS
