"""Tests for security engine."""

import os
from pathlib import Path

import pytest

from ici.config_schema import ConfigError, validate_config
from ici.core.models import EngineStatus, EvidenceState
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


def test_import_aliases_are_resolved_for_security_calls(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "import hashlib as hashes\n"
        "import subprocess as process\n"
        "from pickle import loads as restore\n"
        "from random import choice as choose\n"
        "hashes.sha1(b'data')\n"
        "restore(payload)\n"
        "choose(items)\n"
        "process.run(command, shell=True)\n",
        encoding="utf-8",
    )

    result = SecurityEngine(tmp_path, _CFG).run()

    names = {target.target_name for target in result.targets}
    assert {
        "Security:WeakCryptoSHA1",
        "Security:PickleLoad",
        "Security:WeakRandom",
        "Security:ShellTrue",
    } <= names
    assert result.extra["analysis_mode"] == "python-ast-rules-v1"
    assert result.extra["calls_checked"] == 4


def test_nested_parameter_does_not_shadow_outer_builtin(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "eval(payload)\ndef inner(eval):\n    return eval(payload)\n",
        encoding="utf-8",
    )

    result = SecurityEngine(tmp_path, _CFG).run()

    eval_targets = [
        target for target in result.targets if target.target_name == "Security:EvalExec"
    ]
    assert [target.start_line for target in eval_targets] == [1]


def test_function_import_aliases_do_not_leak_to_sibling_scope(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "def unsafe(items):\n"
        "    import random as chooser\n"
        "    return chooser.choice(items)\n"
        "def safe(chooser, items):\n"
        "    return chooser.choice(items)\n",
        encoding="utf-8",
    )

    result = SecurityEngine(tmp_path, _CFG).run()

    weak_random = [
        target for target in result.targets if target.target_name == "Security:WeakRandom"
    ]
    assert [target.start_line for target in weak_random] == [3]


def test_similar_text_without_risky_call_shape_is_clean(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "import hashlib as hashes\n"
        "def md5(value):\n"
        "    return value\n"
        "class Evaluator:\n"
        "    def eval(self, value):\n"
        "        return value\n"
        "eval = lambda value: value\n"
        "hashes = Evaluator()\n"
        "md5(payload)\n"
        "Evaluator().eval(payload)\n"
        "eval(payload)\n"
        "hashes.sha1(payload)\n",
        encoding="utf-8",
    )

    result = SecurityEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.PASS
    assert not [target for target in result.targets if target.status == EngineStatus.WARN]
    assert any(target.target_name == "Security:ASTScan" for target in result.targets)


def test_secret_dictionary_value_is_redacted_without_source_echo(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    secret = "ghp_SUPER_SECRET_VALUE_123456789"
    (src / "a.py").write_text(
        f"settings = {{'auth_token': '{secret}'}}\n",
        encoding="utf-8",
    )

    result = SecurityEngine(tmp_path, _CFG).run()

    target = next(target for target in result.targets if "HardcodedSecret" in target.target_name)
    assert secret not in target.message
    assert secret not in target.snippet
    assert target.snippet == 'auth_token = "***REDACTED***"'
    assert target.start_column == 12


def test_placeholder_and_exact_name_allowlist_reduce_secret_noise(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        'password = "example-password"\nAPI_KEY = "sk-live-actual-value"\n',
        encoding="utf-8",
    )
    cfg = {
        "engines": {
            "security": {
                "mode": "pass_warn",
                "secret_name_allowlist": ["API_KEY"],
            }
        }
    }

    result = SecurityEngine(tmp_path, cfg).run()

    assert result.status == EngineStatus.PASS
    assert result.extra["secret_name_allowlist_count"] == 1


def test_nosec_inside_string_does_not_suppress_call(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text('note = "# nosec"\neval(payload)\n', encoding="utf-8")

    result = SecurityEngine(tmp_path, _CFG).run()

    assert any(target.target_name == "Security:EvalExec" for target in result.targets)


def test_invalid_python_is_an_explicit_incomplete_analysis(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def broken(:\n", encoding="utf-8")

    result = SecurityEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    target = next(target for target in result.targets if target.status == EngineStatus.ERROR)
    assert target.target_name == "Security:SyntaxUnavailable"
    assert target.file_path == "src/a.py"


def test_no_python_scope_is_not_applicable(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() {}\n", encoding="utf-8")

    result = SecurityEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert result.targets[0].file_path == "."


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_selected_symlink_source_is_rejected_instead_of_followed(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text('password = "secret123"\n', encoding="utf-8")
    linked = src / "a.py"
    os.symlink(outside, linked)
    engine = SecurityEngine(tmp_path, _CFG)
    monkeypatch.setattr(engine, "project_python_sources", lambda: [linked])

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(target.target_name == "Security:SourceInput" for target in result.targets)


@pytest.mark.parametrize(
    "allowlist",
    ["API_KEY", [""], ["not-valid-name"], ["TOKEN", "token"], ["x" * 129]],
)
def test_secret_allowlist_schema_is_bounded(allowlist: object):
    with pytest.raises(ConfigError, match=r"engines\.security\.secret_name_allowlist"):
        validate_config({"engines": {"security": {"secret_name_allowlist": allowlist}}})
