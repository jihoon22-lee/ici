"""Adversarial tests for Python dead-code evidence."""

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
