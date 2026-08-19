"""Adversarial tests for Python dead-code evidence."""

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines.dead import DeadCodeEngine


def test_dead_engine_reports_unreferenced_private_module_function(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def _unused():\n    return 1\n", encoding="utf-8")

    result = DeadCodeEngine(tmp_path).run()

    target = next(target for target in result.targets if target.target_name == "_unused()")
    assert target.file_path == "src/mod.py"
    assert target.start_line == 1
    assert target.status == EngineStatus.WARN
    assert result.status == EngineStatus.WARN


def test_dead_engine_does_not_cross_contaminate_same_name_modules(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "first.py").write_text("def _same():\n    return 1\n", encoding="utf-8")
    (src / "second.py").write_text(
        "def _same():\n    return 2\n\n\ndef use():\n    return _same()\n",
        encoding="utf-8",
    )

    result = DeadCodeEngine(tmp_path).run()

    first = next(target for target in result.targets if target.file_path == "src/first.py")
    second = next(target for target in result.targets if target.file_path == "src/second.py")
    assert first.target_name == "_same()"
    assert first.status == EngineStatus.WARN
    assert second.target_name == "_same()"
    assert second.status == EngineStatus.PASS


def test_dead_engine_excludes_methods_nested_functions_decorated_and_exported(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        """__all__ = ["_exported"]

def register(func):
    return func

@register
def _decorated():
    return 1

def _exported():
    return 2

class Handler:
    def _method(self):
        return 3

def outer():
    def _nested():
        return 4
    return _nested()
""",
        encoding="utf-8",
    )

    result = DeadCodeEngine(tmp_path).run()

    issues = {
        target.target_name
        for target in result.targets
        if target.status in (EngineStatus.WARN, EngineStatus.FAIL)
    }
    assert "_decorated()" not in issues
    assert "_exported()" not in issues
    assert "_method()" not in issues
    assert "_nested()" not in issues


def test_dead_engine_reports_syntax_errors_as_not_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    target = next(target for target in result.targets if target.target_name == "SyntaxError")
    assert target.file_path == "src/broken.py"
    assert target.start_line == 1


def test_dead_engine_without_python_sources_is_explicitly_skipped(tmp_path):
    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.targets[0].status == EngineStatus.SKIP


def test_dead_engine_resolves_from_import_across_modules(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def _from_import():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text(
        "from a import _from_import\n\ndef use():\n    return _from_import()\n",
        encoding="utf-8",
    )
    (src / "unrelated.py").write_text("def _from_import():\n    return 2\n", encoding="utf-8")

    result = DeadCodeEngine(tmp_path).run()

    imported = next(target for target in result.targets if target.file_path == "src/a.py")
    unrelated = next(target for target in result.targets if target.file_path == "src/unrelated.py")
    assert imported.status == EngineStatus.PASS
    assert unrelated.status == EngineStatus.WARN


def test_dead_engine_resolves_module_attribute_reference_across_modules(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def _attribute():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text(
        "import a as module\n\ndef use():\n    return module._attribute()\n",
        encoding="utf-8",
    )

    result = DeadCodeEngine(tmp_path).run()

    target = next(target for target in result.targets if target.file_path == "src/a.py")
    assert target.status == EngineStatus.PASS


def test_dead_engine_returns_pass_target_for_clean_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.py").write_text("def public():\n    return 1\n", encoding="utf-8")

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert any(
        target.file_path == "src/clean.py" and target.status == EngineStatus.PASS
        for target in result.targets
    )


def test_dead_engine_warning_uses_warning_status_not_failure(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "unused.py").write_text("def _unused():\n    return 1\n", encoding="utf-8")

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.WARN


@pytest.mark.parametrize(
    "import_stmt, call",
    [
        ("import pkg.a", "pkg.a._foo()"),
        ("from pkg import a", "a._foo()"),
    ],
)
def test_dead_engine_resolves_nested_module_attribute_references(tmp_path, import_stmt, call):
    src = tmp_path / "src"
    package = src / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "a.py").write_text("def _foo():\n    return 1\n", encoding="utf-8")
    (src / "use.py").write_text(
        f"{import_stmt}\n\ndef use():\n    return {call}\n", encoding="utf-8"
    )
    (src / "unrelated.py").write_text("def _foo():\n    return 2\n", encoding="utf-8")

    result = DeadCodeEngine(tmp_path).run()

    actual = next(target for target in result.targets if target.file_path == "src/pkg/a.py")
    unrelated = next(target for target in result.targets if target.file_path == "src/unrelated.py")
    assert actual.target_name == "_foo()"
    assert actual.status == EngineStatus.PASS
    assert unrelated.status == EngineStatus.WARN


def test_dead_engine_resolves_imported_symbol_used_through_attribute(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def _foo():\n    return 1\n", encoding="utf-8")
    (src / "use.py").write_text(
        "from a import _foo\n\nvalue = _foo.__name__\n", encoding="utf-8"
    )

    result = DeadCodeEngine(tmp_path).run()

    target = next(target for target in result.targets if target.file_path == "src/a.py")
    assert target.target_name == "_foo()"
    assert target.status == EngineStatus.PASS


def test_dead_engine_prefers_first_configured_source_dir_for_shadowed_module(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.py").write_text("def _foo():\n    return 1\n", encoding="utf-8")
    (second / "a.py").write_text("def _foo():\n    return 2\n", encoding="utf-8")
    (first / "use.py").write_text(
        "from a import _foo\n\nvalue = _foo()\n", encoding="utf-8"
    )
    config = {"project": {"source_dirs": ["first", "second"]}}

    result = DeadCodeEngine(tmp_path, config).run()

    first_target = next(target for target in result.targets if target.file_path == "first/a.py")
    second_target = next(target for target in result.targets if target.file_path == "second/a.py")
    assert first_target.status == EngineStatus.PASS
    assert second_target.status == EngineStatus.WARN
