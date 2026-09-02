"""Adversarial tests for Python dead-code evidence."""

import pytest

from ici.core.findings import findings_for_result
from ici.core.models import (
    EngineStatus,
    EvidenceState,
    FindingConfidence,
    InspectionTarget,
    ToolEvidence,
    aggregate_suite_status,
)
from ici.engines._cpp_unused_functions import (
    CppUnusedFunction,
    CppUnusedFunctionOutcome,
)
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


def test_dead_engine_reports_exact_compiler_unused_function(tmp_path, monkeypatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text(
        "static void unused_helper() {}\nint main() { return 0; }\n",
        encoding="utf-8",
    )
    target = InspectionTarget(
        file_path="src/main.cpp",
        start_line=1,
        start_column=13,
        target_name="Compiler:-Wunused-function",
        status=EngineStatus.WARN,
        message=(
            "Compiler reports an internal-linkage function defined but not used in all 1 "
            "analyzed configuration(s)"
        ),
        metrics={"configurations_checked": 1},
    )
    outcome = CppUnusedFunctionOutcome(
        targets=[target],
        functions=[
            CppUnusedFunction(
                target=target,
                configurations=("sha256:" + "a" * 64,),
                tool_names=("g++",),
                tool_versions=("14.2.0",),
            )
        ],
        mode="exact",
        configurations_checked=1,
        sources_checked=1,
    )
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: outcome,
    )

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["analysis_provenance"] == "cpp-compiler-unused-function"
    assert result.extra["language_evidence"] == {
        "python": "NOT_APPLICABLE",
        "cpp": "MEASURED",
    }
    assert result.extra["cpp_unused_functions_count"] == 1
    assert result.extra["cpp_unused_details"][0]["configurations"] == ["sha256:" + "a" * 64]
    assert result.extra["cpp_unused_details"][0]["start_column"] == 13
    normalized = findings_for_result(result, tmp_path)
    assert len(normalized) == 1
    assert normalized[0].confidence == FindingConfidence.EXACT
    assert normalized[0].tool_rule_id == "-Wunused-function"
    assert normalized[0].primary_location.path == "src/main.cpp"


def test_dead_engine_combines_python_heuristic_and_cpp_exact_evidence(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    outcome = CppUnusedFunctionOutcome(
        targets=[
            InspectionTarget(
                file_path="src/main.cpp",
                start_line=1,
                target_name="C++UnusedFunctions",
                status=EngineStatus.PASS,
                message="Compiler found no unused functions",
            )
        ],
        mode="exact",
        configurations_checked=1,
        sources_checked=1,
    )
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: outcome,
    )

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["language_evidence"] == {
        "python": "ESTIMATED",
        "cpp": "MEASURED",
    }
    assert result.extra["analysis_provenance"] == (
        "python-ast-heuristic+cpp-compiler-unused-function"
    )


def test_cpp_exact_scope_includes_owned_external_build_directory_units(
    tmp_path,
    monkeypatch,
) -> None:
    src = tmp_path / "src"
    gui = src / "gui"
    gui.mkdir(parents=True)
    core = src / "core.cpp"
    widget = gui / "widget.cpp"
    core.write_text("int core() { return 0; }\n", encoding="utf-8")
    widget.write_text("int widget() { return 0; }\n", encoding="utf-8")
    observed: list[str] = []

    def run_scope(_root, cpp_files, _context, **_kwargs):  # type: ignore[no-untyped-def]
        observed.extend(path.relative_to(tmp_path).as_posix() for path in cpp_files)
        return CppUnusedFunctionOutcome(
            mode="exact",
            configurations_checked=2,
            sources_checked=2,
        )

    monkeypatch.setattr("ici.engines.dead.run_cpp_unused_functions", run_scope)

    result = DeadCodeEngine(
        tmp_path,
        {"project": {"cpp_external_build_dirs": ["src/gui"]}},
    ).run()

    assert observed == ["src/core.cpp", "src/gui/widget.cpp"]
    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["cpp_unused_sources_checked"] == 2


def test_hybrid_findings_keep_language_confidence_and_tool_attribution(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def _unused():\n    return 1\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    cpp_target = InspectionTarget(
        file_path="src/main.cpp",
        start_line=1,
        target_name="C++UnusedFunctions",
        status=EngineStatus.PASS,
        message="Compiler found no unused internal-linkage function",
        metrics={"configurations_checked": 1},
    )
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: CppUnusedFunctionOutcome(
            targets=[cpp_target],
            evidence=[
                ToolEvidence(
                    name="g++ unused-function",
                    path="/usr/bin/g++",
                    version="14.2.0",
                )
            ],
            mode="exact",
            configurations_checked=1,
            sources_checked=1,
        ),
    )

    result = DeadCodeEngine(tmp_path).run()
    findings = findings_for_result(result, tmp_path)
    python_finding = next(
        finding for finding in findings if finding.primary_location.path == "src/mod.py"
    )
    cpp_finding = next(
        finding for finding in findings if finding.primary_location.path == "src/main.cpp"
    )

    assert python_finding.confidence == FindingConfidence.MEDIUM
    assert python_finding.tool_name == ""
    assert cpp_finding.confidence == FindingConfidence.EXACT
    assert cpp_finding.tool_name == "g++ unused-function"


def test_cpp_failure_does_not_erase_completed_python_evidence(tmp_path, monkeypatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def _unused():\n    return 1\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: CppUnusedFunctionOutcome(
            targets=[
                InspectionTarget(
                    file_path="src/main.cpp",
                    start_line=1,
                    target_name="C++UnusedFunctionError",
                    status=EngineStatus.ERROR,
                    message="compiler failed",
                )
            ],
            errors=["compiler failed"],
            evidence=[
                ToolEvidence(
                    name="g++ unused-function",
                    path="/usr/bin/g++",
                    version="14.2.0",
                    error="compiler failed",
                )
            ],
            mode="error",
        ),
    )

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["language_evidence"] == {
        "python": "ESTIMATED",
        "cpp": "NOT_RUN",
    }
    python_finding = next(
        finding
        for finding in findings_for_result(result, tmp_path)
        if finding.primary_location.path == "src/mod.py"
    )
    assert python_finding.confidence == FindingConfidence.MEDIUM
    assert python_finding.tool_name == ""


def test_invalidated_cpp_observation_is_low_confidence_tool_attributed_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    invalidated = InspectionTarget(
        file_path="src/main.cpp",
        start_line=1,
        target_name="C++UnusedFunctionsInvalidated",
        status=EngineStatus.SKIP,
        message="Compiler observations were discarded",
    )
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: CppUnusedFunctionOutcome(
            targets=[invalidated],
            evidence=[
                ToolEvidence(
                    name="g++ unused-function",
                    path="/usr/bin/g++",
                    version="14.2.0",
                    error="later configuration failed",
                )
            ],
            errors=["later configuration failed"],
            mode="error",
        ),
    )

    result = DeadCodeEngine(tmp_path).run()
    finding = next(
        item
        for item in result.findings
        if item.primary_location.path == "src/main.cpp"
        and item.primary_location.label == "C++UnusedFunctionsInvalidated"
    )

    assert result.status == EngineStatus.ERROR
    assert finding.confidence == FindingConfidence.LOW
    assert finding.tool_name == "g++ unused-function"
    assert finding.tool_version == "14.2.0"


def test_hybrid_auto_mode_keeps_python_evidence_when_cpp_context_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def _unused():\n    return 1\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: CppUnusedFunctionOutcome(
            warnings=["Exact C++ unused-function analysis requires a compilation database"],
            mode="unavailable",
        ),
    )

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["language_evidence"] == {
        "python": "ESTIMATED",
        "cpp": "NOT_RUN",
    }
    assert "exact C++ analysis was unavailable" in result.summary
    python_finding = next(
        finding for finding in result.findings if finding.primary_location.path == "src/mod.py"
    )
    assert python_finding.confidence == FindingConfidence.MEDIUM
    assert python_finding.tool_name == ""


def test_source_intake_failure_marks_discovered_cpp_scope_not_run(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_bytes(b"\xff\n")

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["language_evidence"]["cpp"] == "NOT_RUN"


def test_required_cpp_unused_analysis_fails_when_exact_context_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: CppUnusedFunctionOutcome(
            warnings=["Exact C++ unused-function analysis requires a compilation database"],
            mode="unavailable",
        ),
    )

    result = DeadCodeEngine(
        tmp_path,
        {"engines": {"dead": {"cpp_unused": "required"}}},
    ).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.required is True
    assert aggregate_suite_status([result]) == EngineStatus.ERROR
    assert result.extra["language_evidence"]["cpp"] == "NOT_RUN"
    assert result.targets[-1].status == EngineStatus.ERROR


def test_auto_cpp_unused_analysis_is_transparently_not_run_without_context(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: CppUnusedFunctionOutcome(
            warnings=["Exact C++ unused-function analysis requires a compilation database"],
            mode="unavailable",
        ),
    )

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.required is False
    assert aggregate_suite_status([result]) == EngineStatus.WARN
    assert result.extra["cpp_unused_mode"] == "unavailable"
    assert result.targets[-1].status == EngineStatus.SKIP


def test_cpp_unused_off_disables_only_the_cpp_scope_without_running_a_compiler(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: pytest.fail("compiler adapter must not run"),
    )

    result = DeadCodeEngine(
        tmp_path,
        {"engines": {"dead": {"cpp_unused": "off"}}},
    ).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.required is False
    assert result.extra["cpp_unused_mode"] == "off"
    assert result.extra["language_evidence"]["cpp"] == "NOT_RUN"


def test_cpp_unused_off_does_not_read_or_block_python_with_invalid_cpp(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def _unused():\n    return 1\n", encoding="utf-8")
    (src / "main.cpp").write_bytes(b"\xff\n")
    monkeypatch.setattr(
        "ici.engines.dead.run_cpp_unused_functions",
        lambda *_args, **_kwargs: pytest.fail("compiler adapter must not run"),
    )

    result = DeadCodeEngine(
        tmp_path,
        {"engines": {"dead": {"cpp_unused": "off"}}},
    ).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["language_evidence"] == {
        "python": "ESTIMATED",
        "cpp": "NOT_RUN",
    }
    assert result.extra["cpp_unused_mode"] == "off"
    assert result.extra["source_files_snapshotted"] == 1
    assert not any(target.status == EngineStatus.ERROR for target in result.targets)


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


def test_dead_engine_without_python_sources_is_not_applicable(tmp_path):
    """No source scope to read is not a weak result — it is no result at all.

    The evidence used to be ESTIMATED, which claimed an approximation that was
    never made. The project is empty here, so neither Python nor C/C++ applies.
    """
    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
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
    (src / "use.py").write_text("from a import _foo\n\nvalue = _foo.__name__\n", encoding="utf-8")

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
    (first / "use.py").write_text("from a import _foo\n\nvalue = _foo()\n", encoding="utf-8")
    config = {"project": {"source_dirs": ["first", "second"]}}

    result = DeadCodeEngine(tmp_path, config).run()

    first_target = next(target for target in result.targets if target.file_path == "first/a.py")
    second_target = next(target for target in result.targets if target.file_path == "second/a.py")
    assert first_target.status == EngineStatus.PASS
    assert second_target.status == EngineStatus.WARN


@pytest.mark.parametrize(
    ("import_stmt", "use"),
    [
        ("from .a import _foo", "_foo()"),
        ("from . import a", "a._foo()"),
    ],
)
def test_dead_engine_resolves_relative_imports_from_package_init(tmp_path, import_stmt, use):
    src = tmp_path / "src"
    package = src / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f"{import_stmt}\nvalue = {use}\n", encoding="utf-8")
    (package / "a.py").write_text("def _foo():\n    return 1\n", encoding="utf-8")

    result = DeadCodeEngine(tmp_path).run()

    target = next(target for target in result.targets if target.file_path == "src/pkg/a.py")
    assert target.status == EngineStatus.PASS


def test_dead_engine_preserves_same_alias_bindings_in_different_function_scopes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def _foo():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def _foo():\n    return 2\n", encoding="utf-8")
    (src / "use.py").write_text(
        "def use_a():\n"
        "    from a import _foo\n"
        "    return _foo()\n\n"
        "def use_b():\n"
        "    from b import _foo\n"
        "    return _foo()\n",
        encoding="utf-8",
    )

    result = DeadCodeEngine(tmp_path).run()

    statuses = {
        target.file_path: target.status
        for target in result.targets
        if target.target_name == "_foo()"
    }
    assert statuses == {"src/a.py": EngineStatus.PASS, "src/b.py": EngineStatus.PASS}


def test_dead_engine_checks_unreachable_else_finally_and_match_bodies(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "control.py").write_text(
        "def run(value):\n"
        "    if value:\n"
        "        pass\n"
        "    else:\n"
        "        return\n"
        "        print('if else')\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        raise\n"
        "    else:\n"
        "        return\n"
        "        print('try else')\n"
        "    finally:\n"
        "        return\n"
        "        print('finally')\n"
        "    match value:\n"
        "        case 1:\n"
        "            return\n"
        "            print('match')\n",
        encoding="utf-8",
    )

    result = DeadCodeEngine(tmp_path).run()

    unreachable = [target for target in result.targets if target.target_name == "UnreachableCode"]
    assert {target.start_line for target in unreachable} >= {6, 13, 16, 20}
