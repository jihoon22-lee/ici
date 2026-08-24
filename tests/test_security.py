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


def test_scan_tests_enabled_finds_secret_in_tests_dir(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text('password = "secret12345"\n', encoding="utf-8")
    cfg = {"engines": {"security": {"mode": "pass_warn", "scan_tests": True}}}
    result = SecurityEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.WARN
    assert any(t.file_path == "tests/a.py" for t in result.targets)


def test_hardcoded_secret_value_is_masked_in_report(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text('API_KEY = "sk-live-SUPERSECRET-abcdef123456"\n', encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    target = next(t for t in result.targets if "HardcodedSecret" in t.target_name)
    assert "sk-live-SUPERSECRET-abcdef123456" not in target.message
    assert "sk-live-SUPERSECRET-abcdef123456" not in target.snippet
    assert "REDACTED" in target.message
    assert "REDACTED" in target.snippet


def test_private_key_block_is_masked_in_report(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        'KEY = "-----BEGIN RSA PRIVATE KEY-----\\nMIIsupersecretmaterial\\n"\n',
        encoding="utf-8",
    )
    result = SecurityEngine(tmp_path, _CFG).run()
    target = next(t for t in result.targets if "PrivateKey" in t.target_name)
    assert "MIIsupersecretmaterial" not in target.message
    assert "MIIsupersecretmaterial" not in target.snippet


def test_secret_masked_even_when_another_pattern_matches_same_line(tmp_path: Path):
    # A line can trip a secret pattern AND a non-secret one at once. The
    # non-secret finding must not echo the raw line back and leak the secret.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text('password = "sk-LEAKED-SECRET-999"; eval(x)\n', encoding="utf-8")
    result = SecurityEngine(tmp_path, _CFG).run()

    names = {t.target_name for t in result.targets}
    assert "Security:HardcodedSecret" in names
    assert "Security:EvalExec" in names
    for target in result.targets:
        assert "sk-LEAKED-SECRET-999" not in target.message, target.target_name
        assert "sk-LEAKED-SECRET-999" not in target.snippet, target.target_name


def test_private_key_masked_when_another_pattern_matches_same_line(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        'eval("-----BEGIN RSA PRIVATE KEY-----MIIsecretmaterial")\n', encoding="utf-8"
    )
    result = SecurityEngine(tmp_path, _CFG).run()

    assert result.targets
    for target in result.targets:
        assert "MIIsecretmaterial" not in target.message, target.target_name
        assert "MIIsecretmaterial" not in target.snippet, target.target_name


def test_commented_out_line_is_not_flagged(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        '# password = "not-a-real-secret-just-a-doc-example"\nx = 1\n', encoding="utf-8"
    )
    result = SecurityEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS
