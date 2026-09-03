"""Tests for resource engine."""

from pathlib import Path

import pytest

from ici.core.findings import findings_for_result
from ici.core.models import EngineStatus, EvidenceState, FindingCategory
from ici.engines.resource import ResourceEngine

_CFG = {"engines": {"resource": {"mode": "pass_warn"}}}


def test_clean_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "def foo():\n    with open('x') as f:\n        return f.read()\n", encoding="utf-8"
    )
    result = ResourceEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_open_without_with_warns(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("f = open('x')\n", encoding="utf-8")
    result = ResourceEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("OpenWithoutWith" in t.target_name for t in result.targets)


def test_mutable_default_warns(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo(x=[]):\n    return x\n", encoding="utf-8")
    result = ResourceEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("MutableDefault" in t.target_name for t in result.targets)


@pytest.mark.parametrize(
    "body",
    [
        "f = open('x')\nf.close()\n",
        "f = open('x')\ng = f\ng.close()\n",
        "f = open('x')\nreturn f\n",
        "f = open('x')\nself.handle = f\n",
        "f = open('x')\nwith f:\n    pass\n",
        "f = open('x')\ntry:\n    consume(f)\nfinally:\n    f.close()\n",
    ],
)
def test_closed_or_transferred_resources_are_not_reported(tmp_path: Path, body: str):
    src = tmp_path / "src"
    src.mkdir()
    indented = "".join(f"    {line}\n" for line in body.splitlines())
    (src / "a.py").write_text(f"def use(self):\n{indented}", encoding="utf-8")

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.PASS
    assert not any("OpenWithoutWith" in target.target_name for target in result.targets)


def test_conditional_close_retains_open_exit_finding(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "def use(close_it):\n    handle = open('x')\n    if close_it:\n        handle.close()\n",
        encoding="utf-8",
    )

    result = ResourceEngine(tmp_path, _CFG).run()

    target = next(target for target in result.targets if "OpenWithoutWith" in target.target_name)
    assert target.start_line == 2
    assert target.metrics == {"open_path": 1, "closed_path": 1, "transferred_path": 0}


def test_return_on_one_branch_and_close_on_the_other_is_clean(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "def use(transfer):\n"
        "    handle = open('x')\n"
        "    if transfer:\n"
        "        return handle\n"
        "    handle.close()\n",
        encoding="utf-8",
    )

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.PASS


def test_exit_stack_ownership_transfer_is_recognized(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "from contextlib import ExitStack as Stack\n"
        "def use():\n"
        "    handle = open('x')\n"
        "    with Stack() as stack:\n"
        "        stack.enter_context(handle)\n",
        encoding="utf-8",
    )

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.PASS


def test_import_alias_resource_factory_is_recognized(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "from tempfile import TemporaryFile as scratch\ndef use():\n    handle = scratch()\n",
        encoding="utf-8",
    )

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.WARN
    assert "tempfile.TemporaryFile" in result.targets[-1].message


def test_shadowed_open_is_not_treated_as_builtin(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "def use(open):\n    value = open('not-a-file')\n    return value\n",
        encoding="utf-8",
    )

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.PASS


def test_nested_scope_binding_does_not_shadow_outer_builtin_open(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "def outer():\n"
        "    handle = open('x')\n"
        "    def inner(open):\n"
        "        return open('not-a-file')\n"
        "    return inner\n",
        encoding="utf-8",
    )

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.WARN


@pytest.mark.parametrize("default", ["[]", "{}", "set()", "bytearray()"])
def test_mutable_defaults_are_native_correctness_findings(tmp_path: Path, default: str):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(f"def use(value={default}):\n    return value\n", encoding="utf-8")

    result = ResourceEngine(tmp_path, _CFG).run()
    finding = next(
        finding
        for finding in findings_for_result(result, tmp_path)
        if "MutableDefault" in finding.tool_rule_id
    )

    assert finding.category == FindingCategory.CORRECTNESS
    assert finding.primary_location.start_column is not None


def test_invalid_python_fails_closed_with_location(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.targets[0].file_path == "src/broken.py"
    assert result.targets[0].start_line == 1


def test_selected_symlink_source_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src"
    src.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("handle = open('x')\n", encoding="utf-8")
    selected = src / "linked.py"
    try:
        selected.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable")
    engine = ResourceEngine(tmp_path, _CFG)
    monkeypatch.setattr(engine, "project_python_sources", lambda: [selected])

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert "read safely" in result.targets[0].message.casefold()


def test_no_python_scope_is_not_applicable(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() {}\n", encoding="utf-8")

    result = ResourceEngine(tmp_path, _CFG).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert result.targets[0].file_path == "."


def test_each_successfully_checked_file_has_pass_target(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("value = 1\n", encoding="utf-8")
    (src / "b.py").write_text("value = 2\n", encoding="utf-8")

    result = ResourceEngine(tmp_path, _CFG).run()

    pass_paths = {
        target.file_path for target in result.targets if target.target_name == "Resource:ASTFlow"
    }
    assert pass_paths == {"src/a.py", "src/b.py"}
    assert result.extra["files_checked"] == 2
