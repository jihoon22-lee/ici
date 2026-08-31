"""Contract tests for qmake's compiler-wrapper compilation context capture.

The qmake adapter is intentionally exercised with fake configure/build
functions.  The only subprocess in this module runs the generated wrapper
against ``sys.executable`` so that argv preservation and journal writing are
tested without invoking a project toolchain.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import ici.core.cmake as cmake_module
from ici.core import qmake_context
from ici.core._compile_db_paths import _ReadError
from ici.core.cmake import BuildSession, ConfigureOptions, qmake_configure_argv
from ici.core.compile_db import (
    MAX_COMPILE_ARGUMENT_CHARS,
    MAX_COMPILE_ARGUMENTS,
)
from ici.core.context import BuildVariant, CompilationContext, ProjectModel
from ici.core.runner import ProcessResult

_QMAKE_DATABASE = "build/ici-qmake-build/compile_commands.json"
_CAPTURE_ENV = "ICI_QMAKE_CAPTURE_PATH"


def _project(
    root: Path,
    *,
    sources: tuple[str, ...] = ("src/main.cpp",),
    backend: str | None = "qmake",
) -> ProjectModel:
    return ProjectModel(
        root=root,
        name="qmake-context",
        version="1.0.0",
        project_type="cpp",
        cpp_sources=sources,
        compilable_cpp_sources=sources,
        backend=backend,
        backend_descriptor="app.pro" if backend == "qmake" else "",
    )


def _make_project(root: Path, sources: tuple[str, ...] = ("src/main.cpp",)) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    for source in sources:
        path = root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int value() { return 0; }\n", encoding="utf-8")


def _row(
    root: Path,
    source: str,
    *,
    directory: Path | None = None,
    arguments: list[str] | None = None,
) -> dict[str, Any]:
    working_directory = directory or root
    source_path = root / source
    return {
        "directory": str(working_directory),
        "arguments": arguments or ["/usr/bin/c++", "-c", str(source_path)],
        "file": source,
    }


def _write_journal(journal: Path, rows: list[dict[str, Any]]) -> None:
    journal.write_text(
        "".join(
            json.dumps(
                {key: row[key] for key in ("directory", "arguments")},
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _install_compiler_lookup(monkeypatch: pytest.MonkeyPatch, root: Path) -> dict[str, str]:
    """Provide deterministic executable paths for ``CC=gcc``/``CXX=g++``."""

    tool_dir = root / "fake-toolchain"
    tool_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name in ("gcc", "g++"):
        path = tool_dir / f"{name}-driver"
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o700)
        paths[name] = str(path.resolve(strict=True))

    monkeypatch.setattr(qmake_context.shutil, "which", lambda name: paths.get(name))
    return paths


def _write_probe_makefiles(shadow: Path, payload: str = "CC=gcc\nCXX=g++\n") -> None:
    shadow.mkdir(parents=True, exist_ok=True)
    (shadow / "Makefile").write_text(payload, encoding="utf-8")
    nested = shadow / "nested"
    nested.mkdir()
    (nested / "Makefile.sub").write_text(payload, encoding="utf-8")


def _session(root: Path, *, configured: bool = True) -> BuildSession:
    shadow = root / "build" / "ici-qmake-build"
    shadow.mkdir(parents=True, exist_ok=True)
    return BuildSession(
        root=root,
        shadow=shadow,
        variant=BuildVariant.RELEASE,
        backend="qmake",
        descriptor="app.pro",
        configured=configured,
    )


def _install_fake_qmake(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    configure_ok: bool = True,
    capture_configure_ok: bool = True,
    build_ok: bool = True,
    compiler_paths: dict[str, str] | None = None,
    probe_makefiles: str = "CC=gcc\nCXX=g++\n",
    before_build: Callable[[BuildSession], None] | None = None,
) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"configure": [], "build": []}

    def fake_configure(project_root: Path, options: ConfigureOptions) -> BuildSession:
        calls["configure"].append((project_root, options))
        shadow = project_root / "build" / "ici-qmake-build"
        if len(calls["configure"]) == 1:
            assert options.analysis_database is True
            assert options.qmake_capture_wrapper == ""
            assert options.qmake_capture_cc == ""
            assert options.qmake_capture_cxx == ""
            if configure_ok:
                _write_probe_makefiles(shadow, probe_makefiles)
            return _session(project_root, configured=configure_ok)
        assert options.analysis_database is True
        assert options.qmake_capture_wrapper
        assert Path(options.qmake_capture_wrapper).is_file()
        assert options.qmake_capture_cc
        assert options.qmake_capture_cxx
        if compiler_paths is not None:
            assert options.qmake_capture_cc == compiler_paths["gcc"]
            assert options.qmake_capture_cxx == compiler_paths["g++"]
        return _session(project_root, configured=capture_configure_ok)

    def fake_build(session: BuildSession, *, env: dict[str, str] | None = None) -> bool:
        calls["build"].append((session, env))
        if build_ok and before_build is not None:
            before_build(session)
        if build_ok and rows is not None:
            capture_path = Path((env or {})[_CAPTURE_ENV])
            _write_journal(capture_path, rows)
        return build_ok

    monkeypatch.setattr(qmake_context, "configure", fake_configure)
    monkeypatch.setattr(qmake_context, "build", fake_build)
    return calls


def _diagnostic_codes(context: CompilationContext) -> list[str]:
    return [diagnostic.code for diagnostic in context.diagnostics]


def test_explicit_and_discovered_databases_take_precedence_over_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    explicit = root / "metadata" / "compile_commands.json"
    explicit.parent.mkdir()
    explicit.write_text(json.dumps([_row(root, "src/main.cpp")]), encoding="utf-8")
    discovered = root / "compile_commands.json"
    discovered.write_text(json.dumps([]), encoding="utf-8")

    monkeypatch.setattr(
        qmake_context,
        "configure",
        lambda *_args, **_kwargs: pytest.fail("qmake must not run when a database exists"),
    )

    configured = qmake_context.prepare_qmake_compilation_context(
        root,
        {"project": {"compile_database": "metadata/compile_commands.json"}},
        _project(root),
    )
    assert configured.database_path == "metadata/compile_commands.json"
    assert configured.origin == "configured"
    assert len(configured.units) == 1

    explicit.unlink()
    discovered_context = qmake_context.prepare_qmake_compilation_context(root, {}, _project(root))
    assert discovered_context.database_path == "compile_commands.json"
    assert discovered_context.origin == "discovered"
    assert discovered_context.units == ()


@pytest.mark.parametrize(
    ("config", "expected_code"),
    [
        (
            {"project": {"compile_database": "metadata/missing.json"}},
            "database-missing",
        ),
        ({}, "database-malformed"),
    ],
)
def test_selected_database_diagnostics_also_precede_qmake_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any],
    expected_code: str,
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    if expected_code == "database-malformed":
        (root / "compile_commands.json").write_text("{", encoding="utf-8")

    monkeypatch.setattr(
        qmake_context,
        "configure",
        lambda *_args, **_kwargs: pytest.fail("selected database must remain authoritative"),
    )

    result = qmake_context.prepare_qmake_compilation_context(root, config, _project(root))

    assert expected_code in _diagnostic_codes(result)
    assert result.database_path is not None
    assert result.origin in {"configured", "discovered"}


@pytest.mark.parametrize(
    ("backend", "sources"),
    [(None, ("src/main.cpp",)), ("qmake", ())],
)
def test_non_qmake_or_no_compilable_source_is_a_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str | None,
    sources: tuple[str, ...],
) -> None:
    root = tmp_path / "project"
    _make_project(root, sources)
    project = _project(root, sources=sources, backend=backend)
    monkeypatch.setattr(
        qmake_context,
        "configure",
        lambda *_args, **_kwargs: pytest.fail("qmake must not run for an ineligible project"),
    )

    assert (
        qmake_context.prepare_qmake_compilation_context(root, {}, project) == CompilationContext()
    )


def test_configure_failure_returns_bounded_error_without_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    calls = _install_fake_qmake(monkeypatch, root, configure_ok=False)

    result = qmake_context.prepare_qmake_compilation_context(root, {}, _project(root))

    assert result.units == ()
    assert _diagnostic_codes(result) == ["qmake-configure-failed"]
    assert calls["configure"]
    assert calls["build"] == []
    assert len(result.diagnostics[0].message) <= 512


def test_build_failure_returns_bounded_error_after_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    compiler_paths = _install_compiler_lookup(monkeypatch, root)
    calls = _install_fake_qmake(
        monkeypatch,
        root,
        rows=[],
        build_ok=False,
        compiler_paths=compiler_paths,
    )

    result = qmake_context.prepare_qmake_compilation_context(root, {}, _project(root))

    assert result.units == ()
    assert _diagnostic_codes(result) == ["qmake-capture-build-failed"]
    assert len(calls["configure"]) == 2
    assert len(calls["build"]) == 1


def test_capture_generates_sorted_rows_and_qmake_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root, ("src/a.cpp", "src/z.cpp"))
    shadow = root / "build" / "ici-qmake-build"
    generated = shadow / "generated" / "moc_widget.cpp"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("int moc_widget() { return 0; }\n", encoding="utf-8")
    rows = [
        _row(
            root,
            "generated/moc_widget.cpp",
            directory=shadow / "generated",
            arguments=["/usr/bin/g++", "-c", "moc_widget.cpp"],
        ),
        _row(
            root,
            "src/z.cpp",
            directory=shadow,
            arguments=[
                "/usr/bin/g++",
                "-I",
                "src/a.cpp",
                "-D",
                "not-a-source.cpp",
                "-c",
                str(root / "src/z.cpp"),
            ],
        ),
    ]
    compiler_paths = _install_compiler_lookup(monkeypatch, root)
    calls = _install_fake_qmake(
        monkeypatch,
        root,
        rows=rows,
        compiler_paths=compiler_paths,
        before_build=lambda session: (
            (session.shadow / "generated").mkdir(parents=True, exist_ok=True),
            (session.shadow / "generated" / "moc_widget.cpp").write_text(
                "int moc_widget() { return 0; }\n", encoding="utf-8"
            ),
        ),
    )

    result = qmake_context.prepare_qmake_compilation_context(
        root, {}, _project(root, sources=("src/z.cpp",))
    )

    assert result.database_path == _QMAKE_DATABASE
    assert result.origin == "qmake"
    assert result.generator == "qmake"
    assert result.unity_build is None
    assert [unit.source for unit in result.units] == [
        "build/ici-qmake-build/generated/moc_widget.cpp",
        "src/z.cpp",
    ]
    assert result.units[1].argv == tuple(rows[1]["arguments"])
    assert len(calls["configure"]) == 2
    _root, probe_options = calls["configure"][0]
    assert probe_options.variant is BuildVariant.RELEASE
    assert probe_options.analysis_database is True
    assert probe_options.qmake_capture_wrapper == ""
    _root, capture_options = calls["configure"][1]
    assert capture_options.variant is BuildVariant.RELEASE
    assert capture_options.analysis_database is True
    assert capture_options.qmake_capture_wrapper
    assert capture_options.qmake_capture_cc == compiler_paths["gcc"]
    assert capture_options.qmake_capture_cxx == compiler_paths["g++"]
    assert (root / "build" / "ici-qmake-build" / "Makefile").is_file()
    assert len(calls["build"]) == 1
    build_env = calls["build"][0][1]
    assert build_env is not None
    assert set(build_env) == {_CAPTURE_ENV}
    assert build_env[_CAPTURE_ENV].endswith("capture.jsonl")
    capture_session, _environment = calls["build"][0]
    for row in rows:
        directory = Path(row["directory"]).resolve(strict=True)
        directory.relative_to(capture_session.shadow.resolve(strict=True))

    database = root / _QMAKE_DATABASE
    assert database.is_file()
    assert json.loads(database.read_text(encoding="utf-8")) == sorted(
        [{**row, "file": "moc_widget.cpp"} for row in [rows[0]]]
        + [{**rows[1], "file": rows[1]["arguments"][-1]}],
        key=lambda row: (row["directory"], row["file"], row["arguments"]),
    )


def test_qmake_configure_argv_keeps_wrapper_and_literal_compiler_tokens(
    tmp_path: Path,
) -> None:
    pro = tmp_path / "app.pro"
    wrapper = tmp_path / "compiler-wrapper"
    resolved_cxx = "/opt/qt/bin/g++"
    resolved_cc = "/opt/qt/bin/gcc"
    options = ConfigureOptions(
        BuildVariant.RELEASE,
        analysis_database=True,
        qmake_capture_wrapper=str(wrapper),
        qmake_capture_cxx=resolved_cxx,
        qmake_capture_cc=resolved_cc,
    )

    argv = qmake_configure_argv("/usr/bin/qmake6", pro, options)

    assert argv[:3] == ["/usr/bin/qmake6", "-recursive", str(pro)]
    assert "-after" in argv
    assert f"QMAKE_CXX={wrapper} {resolved_cxx}" in argv
    assert f"QMAKE_CC={wrapper} {resolved_cc}" in argv
    assert "$$" not in " ".join(argv)
    assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" not in argv


def test_invalid_probe_compiler_metadata_fails_closed_before_capture_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    compiler_paths = _install_compiler_lookup(monkeypatch, root)
    calls = _install_fake_qmake(
        monkeypatch,
        root,
        rows=[],
        compiler_paths=compiler_paths,
        probe_makefiles="CC=gcc\nCXX=g++ -mavx\n",
    )

    result = qmake_context.prepare_qmake_compilation_context(root, {}, _project(root))

    assert _diagnostic_codes(result) == ["qmake-capture-unavailable"]
    assert len(calls["configure"]) == 1
    assert calls["build"] == []


def test_qmake_compiler_metadata_accepts_one_gcc_and_gxx_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = _install_compiler_lookup(monkeypatch, tmp_path / "project")
    shadow = tmp_path / "shadow"
    _write_probe_makefiles(shadow)

    assert qmake_context._qmake_compilers(shadow) == (lookup["g++"], lookup["gcc"])


@pytest.mark.parametrize(
    "payload",
    [
        "CC=gcc\nCXX=g++\nCC=gcc-12\n",
        "CC=gcc\nCXX=g++ -mavx\n",
        "CC=gcc\nCXX=icc\n",
        "CXX=g++\n",
    ],
    ids=["inconsistent", "multiword", "unrecognized", "missing"],
)
def test_qmake_compiler_metadata_invalid_values_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    _install_compiler_lookup(monkeypatch, tmp_path / "project")
    shadow = tmp_path / "shadow"
    _write_probe_makefiles(shadow, payload)

    with pytest.raises(ValueError):
        qmake_context._qmake_compilers(shadow)


def test_qmake_compiler_metadata_missing_executable_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _install_compiler_lookup(monkeypatch, project)
    monkeypatch.setattr(qmake_context.shutil, "which", lambda _name: None)
    shadow = tmp_path / "shadow"
    _write_probe_makefiles(shadow)

    with pytest.raises(ValueError, match="unavailable"):
        qmake_context._qmake_compilers(shadow)


def test_qmake_compiler_metadata_rejects_oversized_makefile_and_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_compiler_lookup(monkeypatch, tmp_path / "project")
    shadow = tmp_path / "shadow"
    _write_probe_makefiles(shadow)

    monkeypatch.setattr(qmake_context, "MAX_QMAKE_MAKEFILE_BYTES", 8)
    with pytest.raises(_ReadError):
        qmake_context._qmake_compilers(shadow)

    monkeypatch.setattr(qmake_context, "MAX_QMAKE_MAKEFILE_BYTES", 1024)
    monkeypatch.setattr(qmake_context, "MAX_QMAKE_CAPTURE_BYTES", 8)
    with pytest.raises(ValueError, match="aggregate size"):
        qmake_context._qmake_compilers(shadow)


def test_qmake_compiler_metadata_rejects_symlinked_makefile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_compiler_lookup(monkeypatch, tmp_path / "project")
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    target = shadow / "compiler-metadata"
    target.write_text("CC=gcc\nCXX=g++\n", encoding="utf-8")
    (shadow / "Makefile").symlink_to(target)

    with pytest.raises(ValueError):
        qmake_context._qmake_compilers(shadow)


def test_qmake_compiler_metadata_enforces_makefile_entry_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_compiler_lookup(monkeypatch, tmp_path / "project")
    shadow = tmp_path / "shadow"
    _write_probe_makefiles(shadow)
    monkeypatch.setattr(qmake_context, "MAX_QMAKE_MAKEFILES", 1)

    with pytest.raises(ValueError, match="file limit"):
        qmake_context._qmake_compilers(shadow)


def test_capture_incomplete_production_coverage_is_diagnosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root, ("src/a.cpp", "src/b.cpp"))
    shadow = root / "build" / "ici-qmake-build"
    rows = [_row(root, "src/a.cpp", directory=shadow)]
    compiler_paths = _install_compiler_lookup(monkeypatch, root)
    calls = _install_fake_qmake(
        monkeypatch,
        root,
        rows=rows,
        compiler_paths=compiler_paths,
    )

    result = qmake_context.prepare_qmake_compilation_context(
        root,
        {},
        _project(root, sources=("src/a.cpp", "src/b.cpp")),
    )

    assert result.origin == "qmake"
    assert [unit.source for unit in result.units] == ["src/a.cpp"]
    assert _diagnostic_codes(result) == ["qmake-capture-incomplete"]
    assert "1 production" in result.diagnostics[0].message
    assert len(calls["configure"]) == 2


@pytest.mark.skipif(os.name != "posix", reason="the capture wrapper is POSIX-only")
def test_capture_wrapper_records_exact_argv_and_working_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    helper_dir = root / "capture-helper"
    helper_dir.mkdir()
    wrapper, journal = qmake_context._create_capture_files(helper_dir)
    compiler_argv = [
        sys.executable,
        "-c",
        "import sys; sys.exit(0)",
        "-c",
        "src/main.cpp",
        "--preserve-this",
    ]
    environment = os.environ.copy()
    environment[_CAPTURE_ENV] = str(journal)

    completed = subprocess.run(
        [str(wrapper), *compiler_argv],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert wrapper.read_text(encoding="utf-8").splitlines()[0] == (
        f"#!{Path(sys.executable).resolve(strict=True)}"
    )
    assert json.loads(journal.read_text(encoding="utf-8")) == {
        "arguments": compiler_argv,
        "directory": str(root),
    }
    assert wrapper.stat().st_mode & 0o777 == 0o700
    assert journal.stat().st_mode & 0o777 == 0o600


def test_qmake_build_passes_capture_environment_to_clean_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    shadow = root / "build" / "ici-qmake-build"
    shadow.mkdir(parents=True)
    calls: list[tuple[list[str], dict[str, Any]]] = []
    environment = {_CAPTURE_ENV: str(tmp_path / "capture.jsonl")}

    monkeypatch.setattr(
        cmake_module.shutil,
        "which",
        lambda name: {"make": "/usr/bin/make"}.get(name),
    )
    monkeypatch.setattr(cmake_module.os, "cpu_count", lambda: 2)

    def fake_run(argv: list[str], **kwargs: Any) -> ProcessResult:
        calls.append((argv, kwargs))
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr(cmake_module, "run_process", fake_run)
    session = BuildSession(
        root=root,
        shadow=shadow,
        variant=BuildVariant.RELEASE,
        backend="qmake",
        configured=True,
    )

    assert cmake_module.build(session, env=environment) is True
    assert [argv for argv, _kwargs in calls] == [
        ["/usr/bin/make", "clean"],
        ["/usr/bin/make", "--jobs=2"],
    ]
    assert [kwargs["env"] for _argv, kwargs in calls] == [environment, environment]


def test_source_operand_rejects_ambiguous_outside_malformed_and_oversized_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _make_project(root, ("src/a.cpp", "src/b.cpp"))
    shadow = root / "build" / "ici-qmake-build"
    shadow.mkdir(parents=True)
    outside = tmp_path / "outside.cpp"
    outside.write_text("int outside;\n", encoding="utf-8")

    cases = [
        {"directory": str(shadow), "arguments": ["c++", "-c"]},
        {
            "directory": str(shadow),
            "arguments": ["c++", "-c", str(root / "src/a.cpp"), str(root / "src/b.cpp")],
        },
        {"directory": str(shadow), "arguments": ["c++", "-c", str(outside)]},
        {"directory": 42, "arguments": ["c++", "-c", "src/a.cpp"]},
        {"directory": str(shadow), "arguments": "c++ -c src/a.cpp"},
        {
            "directory": str(shadow),
            "arguments": [
                "c++",
                "-D" + ("X" * MAX_COMPILE_ARGUMENT_CHARS),
                "-c",
                str(root / "src/a.cpp"),
            ],
        },
        {
            "directory": str(shadow),
            "arguments": ["c++"] * (MAX_COMPILE_ARGUMENTS + 1),
        },
    ]

    for row in cases:
        assert qmake_context._source_operand(root, shadow, row) is None


def test_capture_rows_rejects_malformed_json_duplicate_keys_and_blank_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    shadow = root / "build" / "ici-qmake-build"
    shadow.mkdir(parents=True)

    journal = tmp_path / "malformed.jsonl"
    journal.write_text('{"directory":', encoding="utf-8")
    with pytest.raises(ValueError):
        qmake_context._capture_rows(root, shadow, journal)

    journal.write_text(
        '{"directory":'
        + json.dumps(str(root))
        + ',"directory":'
        + json.dumps(str(root))
        + ',"arguments":["c++","-c","src/main.cpp"]}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        qmake_context._capture_rows(root, shadow, journal)

    journal.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError):
        qmake_context._capture_rows(root, shadow, journal)


def test_capture_rows_enforces_journal_size_and_record_count_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    shadow = root / "build" / "ici-qmake-build"
    shadow.mkdir(parents=True)
    journal = tmp_path / "capture.jsonl"
    row = _row(root, "src/main.cpp")

    journal.write_bytes(b"x" * 33)
    monkeypatch.setattr(qmake_context, "MAX_QMAKE_CAPTURE_BYTES", 32)
    with pytest.raises(_ReadError):
        qmake_context._capture_rows(root, shadow, journal)

    monkeypatch.setattr(qmake_context, "MAX_QMAKE_CAPTURE_BYTES", 1024 * 1024)
    monkeypatch.setattr(qmake_context, "MAX_QMAKE_CAPTURE_RECORDS", 1)
    _write_journal(journal, [row, row])
    with pytest.raises(ValueError, match="record count"):
        qmake_context._capture_rows(root, shadow, journal)


def test_capture_journal_symlink_is_rejected_without_reading_outside(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    shadow = root / "build" / "ici-qmake-build"
    shadow.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    _write_journal(outside, [_row(root, "src/main.cpp")])
    journal = tmp_path / "capture.jsonl"
    journal.symlink_to(outside)

    with pytest.raises(_ReadError) as raised:
        qmake_context._capture_rows(root, shadow, journal)

    assert raised.value.code in {"unreadable", "not-file"}


def test_source_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _make_project(root)
    outside = tmp_path / "outside.cpp"
    outside.write_text("int outside;\n", encoding="utf-8")
    escaped = root / "src" / "escaped.cpp"
    escaped.symlink_to(outside)
    shadow = root / "build" / "ici-qmake-build"
    shadow.mkdir(parents=True)

    row = _row(root, "src/escaped.cpp", directory=shadow)

    assert qmake_context._source_operand(root, shadow, row) is None


def test_write_database_rejects_shadow_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside-build"
    outside.mkdir()
    (root / "build").symlink_to(outside, target_is_directory=True)
    shadow = root / "build" / "ici-qmake-build"

    with pytest.raises(OSError, match=r"unsafe|identity"):
        qmake_context._write_database(root, shadow, [])

    assert not list(outside.iterdir())


def test_capture_helper_rejects_whitespace_path_and_reports_lower_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "capture helper"
    helper.mkdir()
    with pytest.raises(ValueError, match="whitespace"):
        qmake_context._create_capture_files(helper)

    root = tmp_path / "project"
    _make_project(root)
    whitespace_directory = tmp_path / "temporary capture"
    whitespace_directory.mkdir()

    class FakeTemporaryDirectory:
        def __enter__(self) -> str:
            return str(whitespace_directory)

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(
        qmake_context.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: FakeTemporaryDirectory(),
    )
    monkeypatch.setattr(
        qmake_context,
        "configure",
        lambda *_args, **_kwargs: pytest.fail("helper path failure must precede configure"),
    )

    result = qmake_context.prepare_qmake_compilation_context(root, {}, _project(root))

    assert _diagnostic_codes(result) == ["qmake-capture-unavailable"]
    assert result.diagnostics[0].level == "warning"


def test_non_posix_qmake_capture_is_explicitly_lower_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _make_project(root)
    monkeypatch.setattr(qmake_context.os, "name", "nt")
    monkeypatch.setattr(
        qmake_context,
        "configure",
        lambda *_args, **_kwargs: pytest.fail("unsupported host must not configure qmake"),
    )

    result = qmake_context.prepare_qmake_compilation_context(root, {}, _project(root))

    assert _diagnostic_codes(result) == ["qmake-capture-unsupported"]
    assert result.diagnostics[0].level == "warning"
