"""``ici next`` selection surface — #210's union, scope, and output contracts.

The claims under test are the ones the issue's acceptance list names: language
flags union and intersect with ``--component`` without re-reading the model,
a subdirectory cwd does not re-scope the run, ``--require-full`` turns
uncovered required scope into exit 3 with the gap named, machine output never
shares a stream with diagnostics, and ``init``/``doctor``/``plan`` leave no
side effects behind.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app

runner = CliRunner()
RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'


def _hybrid_workspace(root: Path, with_db: bool = True) -> None:
    (root / "app").mkdir(parents=True)
    (root / "native").mkdir(parents=True)
    (root / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (root / "native" / "core.cpp").write_text("int core() { return 1; }\n", encoding="utf-8")
    if with_db:
        # A C++ component's verification needs compile invocations (#212):
        # the fixture's database covers the component's single TU so a plain
        # run is a full pass, and missing-database cases opt out below.
        build = root / "native" / "build"
        build.mkdir()
        (build / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(build),
                        "file": str(root / "native" / "core.cpp"),
                        "arguments": ["g++", "-c", "../core.cpp"],
                    }
                ]
            ),
            encoding="utf-8",
        )
    (root / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[[components]]\nid = "native"\nroot = "native"\nlanguages = ["cpp"]\n'
        '[checks."python.test"]\nenabled = false\n'
        '[checks."python.coverage"]\nenabled = false\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )


@needs_ruff
def test_language_flags_union_and_intersect_with_component(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    python_only = runner.invoke(app, ["next", "verify", "--python"])
    assert python_only.exit_code == 0, python_only.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    # A language filter leaves declared coverage out — the result says PARTIAL,
    # and only the Python unit's one file was counted.
    assert stored["scope"]["kind"] == "partial"
    assert stored["scope"]["selected_languages"] == ["python"]
    counted = sum(m["value"] for m in stored["metrics"] if m["name"] == "files_counted")
    assert counted == 1

    union = runner.invoke(app, ["next", "verify", "--python", "--cpp"])
    assert union.exit_code == 0, union.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["kind"] == "full"
    counted = sum(m["value"] for m in stored["metrics"] if m["name"] == "files_counted")
    assert counted == 2


@needs_ruff
def test_repeated_component_options_intersect_with_languages(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["next", "verify", "--component", "app", "--component", "native", "--cpp"]
    )
    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["selected_components"] == ["app", "native"]
    assert stored["scope"]["selected_languages"] == ["cpp"]
    assert not any(f["task_id"].startswith("app.") for f in stored["findings"])


def test_a_subdirectory_cwd_does_not_rescope_the_run(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    nested = tmp_path / "app"
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["next", "plan"])
    assert result.exit_code == 0, result.output
    assert f"root: {tmp_path}" in result.output
    assert "components=app,native" in result.output


def test_a_selection_of_nothing_is_a_config_error(tmp_path, monkeypatch) -> None:
    (tmp_path / "docs").mkdir()
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path / "docs")

    result = runner.invoke(app, ["next", "verify", "--component", "ghost"])
    assert result.exit_code == 2
    assert "ghost" in result.output and "app" in result.output


@needs_ruff
def test_require_full_demotes_a_partial_scope_to_incomplete(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--component", "app", "--require-full"])
    assert result.exit_code == 3, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["gate"]["selected"] == "INCOMPLETE"
    assert any("native" in reason for reason in stored["gate"]["reasons"])
    # The subset's own verdict stays visible: what ran still passed.
    assert stored["scope"]["kind"] == "partial"
    assert stored["scope"]["full_required_satisfied"] is False


@needs_ruff
def test_require_full_passes_when_coverage_is_complete(tmp_path, monkeypatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[checks."python.test"]\nenabled = false\n'
        '[checks."python.coverage"]\nenabled = false\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--require-full"])
    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["full_required_satisfied"] is True


@needs_ruff
def test_json_output_keeps_diagnostics_off_stdout(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--component", "app", "--json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)  # stdout is the result, nothing else
    assert document["schema_id"] == "ici.next.run"
    assert "root:" in result.stderr  # diagnostics went to stderr
    assert "scope:" in result.stderr


@needs_ruff
def test_the_event_stream_is_valid_jsonl_with_monotonic_seq(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    events = tmp_path / "run.jsonl"

    result = runner.invoke(app, ["next", "verify", "--events", str(events)])
    assert result.exit_code in (0, 1), result.output

    lines = [json.loads(line) for line in events.read_text("utf-8").splitlines()]
    assert [line["seq"] for line in lines] == list(range(len(lines)))
    assert lines[0]["event_type"] == "run.started"
    assert lines[-1]["event_type"] == "run.completed"
    assert all(line["schema_id"] == "ici.next.event" for line in lines)
    assert any(line["event_type"] == "task.completed" for line in lines)


def test_plan_names_blockers_dependencies_and_cost(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan", "--profile", "fast"])
    assert result.exit_code == 0, result.output
    assert "profile=fast" in result.output
    assert "app:" in result.output and "native:" in result.output

    as_json = runner.invoke(app, ["next", "plan", "--json"])
    document = json.loads(as_json.stdout)
    assert document["schema_id"] == "ici.next.plan"
    assert document["scope"]["profile"] == "standard"
    kinds = {check["kind"] for plan in document["plans"] for check in plan["checks"]}
    assert kinds <= {"process", "internal", "blocked"}


def test_doctor_reports_tools_inputs_and_the_resolving_keys(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "doctor"])
    assert result.exit_code == 0, result.output
    assert "app (python):" in result.output
    assert "python.line" in result.output and "python.lint" in result.output
    if Path(RUFF).exists():
        assert "ruff at" in result.output

    as_json = runner.invoke(app, ["next", "doctor", "--json"])
    document = json.loads(as_json.stdout)
    assert document["schema_id"] == "ici.next.doctor"
    statuses = {check["status"] for entry in document["components"] for check in entry["checks"]}
    assert "selected" in statuses


def test_doctor_explains_a_disabled_check_by_its_origin(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    (tmp_path / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[components.checks."python.lint"]\nenabled = false\n'
        '[checks."python.test"]\nenabled = false\n'
        '[checks."python.coverage"]\nenabled = false\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "doctor"])
    assert result.exit_code == 0, result.output
    assert "python.lint: omitted" in result.output
    assert "disabled" in result.output


def test_init_writes_a_workspace_without_touching_the_tree(tmp_path, monkeypatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text("int c;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    preview = runner.invoke(app, ["next", "init", "--preview"])
    assert preview.exit_code == 0
    assert not (tmp_path / "ici.toml").exists()  # preview writes nothing
    assert 'id = "app"' in preview.output and 'id = "native"' in preview.output

    written = runner.invoke(app, ["next", "init"])
    assert written.exit_code == 0
    document = (tmp_path / "ici.toml").read_text("utf-8")
    assert 'languages = ["python"]' in document

    again = runner.invoke(app, ["next", "init"])
    assert again.exit_code == 2  # never silently overwrites
    assert "--force" in again.output


def test_doctor_and_verify_report_partial_compile_coverage(tmp_path, monkeypatch) -> None:
    # #211: a database naming half a component's TUs is a partial input — the
    # run and the diagnostic both say so rather than reading as full coverage.
    _hybrid_workspace(tmp_path, with_db=False)
    (tmp_path / "native" / "second.cpp").write_text("int s();\n", encoding="utf-8")
    build = tmp_path / "native" / "build"
    build.mkdir()
    (build / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(build),
                    "file": str(tmp_path / "native" / "core.cpp"),
                    "arguments": ["g++", "-c", "../core.cpp"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    doctor = runner.invoke(app, ["next", "doctor"])
    assert doctor.exit_code == 0
    assert "compile db: native/build/compile_commands.json" in doctor.output
    assert "1/2 TU(s)" in doctor.output
    assert "missing: native/second.cpp" in doctor.output

    verify = runner.invoke(app, ["next", "verify", "--cpp"])
    # #212: a partial capture is incomplete evidence — the gate says
    # INCOMPLETE (exit 3) rather than letting 1/2 coverage read as a pass.
    assert verify.exit_code == 3, verify.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert any("1/2 translation units" in item for item in stored["limitations"])


def test_verify_reports_a_cpp_component_without_a_database(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path, with_db=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--cpp"])
    # No database at all is INCOMPLETE, not a pass and not a code failure.
    assert result.exit_code == 3, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert any("no compilation database" in item for item in stored["limitations"])
    assert "native.cpp.compile" in stored["execution"]["blocked_task_ids"]


def test_two_cpp_components_share_one_declared_build(tmp_path, monkeypatch) -> None:
    # #212 acceptance: one SUBDIRS build, two components — the single database
    # the build produced answers both components' coverage.
    for name in ("app", "lib"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        (tmp_path / name / f"{name}.pro").write_text(
            "TEMPLATE = app\nSOURCES += main.cpp\n", encoding="utf-8"
        )
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += app lib\n", encoding="utf-8"
    )
    build = tmp_path / "build"
    build.mkdir()
    build.joinpath("compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(build),
                    "file": str(tmp_path / name / "main.cpp"),
                    "arguments": ["g++", "-c", f"../{name}/main.cpp"],
                }
                for name in ("app", "lib")
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "ici.toml").write_text(
        HEADER + '[builds.native]\nsystem = "qmake"\nproject = "product.pro"\n'
        'directory = "build"\nvariant = "release"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\nbuild = "native"\n'
        '[[components]]\nid = "lib"\nroot = "lib"\nlanguages = ["cpp"]\nbuild = "native"\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan"])
    assert plan.exit_code == 0, plan.output
    assert "app.cpp.compile" in plan.output and "lib.cpp.compile" in plan.output

    verify = runner.invoke(app, ["next", "verify"])
    assert verify.exit_code == 0, verify.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    covered = [m for m in stored["metrics"] if m["name"] == "units.covered"]
    assert len(covered) == 2 and all(m["value"] == 1 for m in covered)

    doctor = runner.invoke(app, ["next", "doctor", "--cpp"])
    assert "target: app → app/app.pro" in doctor.output
    assert "target: lib → lib/lib.pro" in doctor.output


def test_plan_blocks_cpp_compile_and_names_the_remedy(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path, with_db=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan", "--cpp"])

    assert result.exit_code == 0, result.output
    assert "native.cpp.compile" in result.output
    assert "no compilation database" in result.output
    assert "declare [builds.<id>]" in result.output


def test_require_full_denies_a_partial_compile_capture(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    (tmp_path / "native" / "second.cpp").write_text("int s();\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--require-full"])

    assert result.exit_code == 3, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["gate"]["selected"] == "INCOMPLETE"
    assert any("no compile invocation" in r for r in stored["gate"]["reasons"])


def test_init_restricts_candidates_to_asked_languages(tmp_path, monkeypatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text("int c;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "init", "--preview", "--cpp"])
    assert result.exit_code == 0
    assert 'id = "native"' in result.output and 'id = "app"' not in result.output


def _cmake_workspace(root: Path, component_root: str = "app") -> None:
    """A cmake build tree: root lists, one subdir, a database the build wrote."""
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(product)\nadd_subdirectory(app)\n",
        encoding="utf-8",
    )
    (root / "app").mkdir(parents=True)
    (root / "app" / "CMakeLists.txt").write_text("add_executable(app main.cpp)\n", encoding="utf-8")
    (root / "app" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    build = root / "build"
    build.mkdir()
    build.joinpath("compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(build),
                    "file": str(root / "app" / "main.cpp"),
                    "arguments": ["g++", "-c", "../app/main.cpp"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "ici.toml").write_text(
        HEADER + '[builds.release]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "build"\nvariant = "release"\n'
        f'[[components]]\nid = "app"\nroot = "{component_root}"\nlanguages = ["cpp"]\n'
        'build = "release"\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )


def test_a_cmake_builds_reach_is_reported_like_qmakes(tmp_path, monkeypatch) -> None:
    # #213: a cmake input lands in the same unit/provenance shape as a qmake
    # one — the doctor report shows the resolved target and the origin names
    # the build whose directory produced the database.
    _cmake_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    doctor = runner.invoke(app, ["next", "doctor", "--cpp"])
    assert doctor.exit_code == 0, doctor.output
    assert "target: app → app/CMakeLists.txt" in doctor.output
    assert "cmake build 'release'" in doctor.output

    verify = runner.invoke(app, ["next", "verify"])
    assert verify.exit_code == 0, verify.output


def test_a_component_outside_the_cmake_tree_fails_require_full(tmp_path, monkeypatch) -> None:
    _cmake_workspace(tmp_path, component_root="elsewhere")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.cpp").write_text("int x;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--require-full"])
    assert result.exit_code == 3, result.output
    assert "cmake" in result.output and "would not compile" in result.output


def test_plan_lists_linked_builds_and_their_impact_directories(tmp_path, monkeypatch) -> None:
    _cmake_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan", "--json"])

    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["builds"] == [
        {
            "id": "release",
            "system": "cmake",
            "variant": "release",
            "directory": "build",
            "definition": "CMakeLists.txt",
            "artifacts": [],
            "linked_by": ["app"],
        }
    ]
    text = runner.invoke(app, ["next", "plan"])
    assert "builds:" in text.output
    assert "release: cmake release → build (for app)" in text.output


def test_diagnostics_expands_to_one_task_per_covered_tu(tmp_path, monkeypatch) -> None:
    # #214: one declared check becomes one task per TU, each argv the
    # invocation the build recorded — not a merged guess.
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--cpp"])
    assert plan.exit_code == 0, plan.output
    assert "native.cpp.diagnostics.native-core-cpp-0" in plan.output

    events = tmp_path / "run.jsonl"
    verify = runner.invoke(app, ["next", "verify", "--cpp", "--events", str(events)])
    assert verify.exit_code in (0, 1), verify.output
    completed = {
        json.loads(line)["task_id"]
        for line in events.read_text("utf-8").splitlines()
        if json.loads(line)["event_type"] == "task.completed"
    }
    assert "native.cpp.compile" in completed
    assert any(t.startswith("native.cpp.diagnostics.") for t in completed)


def test_an_unsupported_driver_is_a_blocked_tu_not_a_silent_skip(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    db = tmp_path / "native" / "build" / "compile_commands.json"
    entries = json.loads(db.read_text("utf-8"))
    entries.append(
        {
            "directory": str(tmp_path / "native" / "build"),
            "file": str(tmp_path / "native" / "other.cpp"),
            "arguments": ["cl.exe", "/c", "../other.cpp"],
        }
    )
    (tmp_path / "native" / "other.cpp").write_text("int other;\n", encoding="utf-8")
    db.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--cpp"])
    assert plan.exit_code == 0, plan.output
    assert "unsupported compiler 'cl.exe'" in plan.output
    assert "blocked" in plan.output


def test_python_type_runs_the_component_chosen_checker(tmp_path, monkeypatch) -> None:
    # #215: type_provider is the component's own choice — "ty" asks for ty,
    # and a host without it reports that tool missing rather than quietly
    # substituting mypy.
    _hybrid_workspace(tmp_path)
    (tmp_path / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[components.python]\ntype_provider = "ty"\n'
        '[[components]]\nid = "native"\nroot = "native"\nlanguages = ["cpp"]\n'
        '[checks."python.test"]\nenabled = false\n'
        '[checks."python.coverage"]\nenabled = false\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan", "--component", "app"])
    assert plan.exit_code == 0, plan.output
    if shutil.which("ty"):
        assert "ty check --output-format concise" in plan.output
    else:
        assert "app.python.type: blocked — ty is not available" in plan.output


def test_an_unknown_type_provider_is_a_config_error(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    (tmp_path / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[components.python]\ntype_provider = "pyright"\n'
        '[[components]]\nid = "native"\nroot = "native"\nlanguages = ["cpp"]\n'
        '[checks."python.test"]\nenabled = false\n'
        '[checks."python.coverage"]\nenabled = false\n'
        '[checks."cpp.test"]\nenabled = false\n'
        '[checks."cpp.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan", "--component", "app"])
    assert result.exit_code == 2, result.output
    assert "type_provider" in result.output


def test_python_format_reports_unformatted_files(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    (tmp_path / "app" / "one.py").write_text("x=1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    verify = runner.invoke(app, ["next", "verify", "--component", "app"])
    assert verify.exit_code == 1, verify.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert any(f["rule_id"] == "ruff.format" for f in stored["findings"])
