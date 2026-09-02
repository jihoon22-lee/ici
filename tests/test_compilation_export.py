"""Focused contracts for the standalone compilation-context export."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ici import __version__
from ici.__main__ import app
from ici.config import get_global_config_path
from ici.core import compilation_export as export_module
from ici.core._compilation_export_project import discover_export_project
from ici.core.compilation_export import (
    CompilationExportError,
    compilation_export_payload,
    config_with_database,
    load_export_context,
    render_compilation_export,
    validate_export_output,
    write_compilation_export,
)
from ici.core.context import (
    CompilationContext,
    CompilationDefine,
    CompilationDiagnostic,
    CompilationSearchPath,
    CompilationUnit,
    ProjectModel,
)
from ici.core.redaction_values import REDACTED

runner = CliRunner()
_DIGEST = "sha256:" + "a" * 64


def _project(root: Path, *, backend: str | None = "cmake") -> ProjectModel:
    return ProjectModel(
        root=root,
        name="sample-project",
        version="1.2.3",
        project_type="hybrid",
        backend=backend,
    )


def _unit(
    *,
    source: str = "src/main.cpp",
    directory: str = "build",
    output: str = "build/main.o",
    argv: tuple[str, ...] = ("clang++", "-std=c++20", "-c", "src/main.cpp"),
    compiler: str = "clang++",
    standard: str = "c++20",
    defines: tuple[CompilationDefine, ...] = (),
    include_paths: tuple[CompilationSearchPath, ...] = (),
    sysroot: str = "",
    sysroot_scope: str = "",
    target: str = "app",
    configuration: str = _DIGEST,
    diagnostics: tuple[CompilationDiagnostic, ...] = (),
) -> CompilationUnit:
    return CompilationUnit(
        source=source,
        directory=directory,
        argv=argv,
        output=output,
        compiler=compiler,
        language="c++",
        standard=standard,
        target=target,
        defines=defines,
        include_paths=include_paths,
        sysroot=sysroot,
        sysroot_scope=sysroot_scope,
        configuration=configuration,
        diagnostics=diagnostics,
    )


def _context(
    *units: CompilationUnit,
    database_path: str | None = "build/compile_commands.json",
    database_digest: str = _DIGEST,
    diagnostics: tuple[CompilationDiagnostic, ...] = (),
    unity_build: bool | None = False,
    origin: str = "cmake",
    generator: str = "Ninja",
) -> CompilationContext:
    return CompilationContext(
        units=units,
        database_path=database_path,
        database_digest=database_digest,
        origin=origin,
        generator=generator,
        unity_build=unity_build,
        diagnostics=diagnostics,
    )


def _schema() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[1]
        / "src/ici/schemas/ici-compilation-export-v1.schema.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_schema_shape(payload: dict[str, Any]) -> None:
    schema = _schema()
    required = set(schema["required"])
    assert set(payload) == required
    assert payload["schema_version"] == schema["properties"]["schema_version"]["const"]

    compilation = payload["compilation"]
    assert isinstance(compilation, dict)
    compilation_schema = schema["properties"]["compilation"]
    assert set(compilation) == set(compilation_schema["required"])

    project = payload["project"]
    assert isinstance(project, dict)
    project_schema = schema["properties"]["project"]
    assert set(project) == set(project_schema["required"])

    producer = payload["producer"]
    assert isinstance(producer, dict)
    assert set(producer) == {"name", "version"}
    assert producer["name"] == "ici"
    assert producer["version"] == __version__

    for unit in compilation["units"]:
        unit_schema = schema["$defs"]["unit"]
        assert set(unit) == set(unit_schema["required"])
        assert "argv" not in unit
        assert "command" not in unit
        for definition in unit["defines"]:
            assert set(definition) == {"name", "value", "value_state"}
        for include in unit["include_paths"]:
            assert set(include) == {"exists", "kind", "order", "path", "scope"}
        if unit["sysroot"] is not None:
            assert set(unit["sysroot"]) == {"path", "scope"}
        for diagnostic in unit["diagnostics"]:
            assert set(diagnostic) == {"code", "entry_index", "level", "message", "source"}
    for diagnostic in compilation["diagnostics"]:
        assert set(diagnostic) == {"code", "entry_index", "level", "message", "source"}

    try:
        import jsonschema
    except ImportError:
        return
    jsonschema.validate(payload, schema)


def test_payload_is_schema_shaped_deterministic_and_ordered(tmp_path: Path) -> None:
    project = _project(tmp_path)
    context = _context(
        _unit(source="src/a.cpp", output="build/a.o"),
        _unit(source="src/b.cpp", output="build/b.o"),
    )

    first = compilation_export_payload(project, context)
    second = compilation_export_payload(project, context)

    assert first == second
    assert [unit["source"] for unit in first["compilation"]["units"]] == [
        "src/a.cpp",
        "src/b.cpp",
    ]
    _assert_schema_shape(first)
    assert render_compilation_export(first) == render_compilation_export(second)


def test_public_digests_are_stable_when_checkout_root_moves(tmp_path: Path) -> None:
    first_root = tmp_path / "checkout-one"
    second_root = tmp_path / "checkout-two"
    first = compilation_export_payload(_project(first_root), _context(_unit()))
    second = compilation_export_payload(_project(second_root), _context(_unit()))

    assert first == second
    assert (
        first["compilation"]["units"][0]["configuration_digest"]
        == second["compilation"]["units"][0]["configuration_digest"]
    )
    rendered = render_compilation_export(first).decode("utf-8")
    assert str(first_root) not in rendered
    assert str(second_root) not in rendered


def test_default_context_load_uses_static_metadata_without_subprocess_or_recursive_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    build = root / "build"
    source.parent.mkdir(parents=True)
    build.mkdir()
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "ici.toml").write_text(
        'name = "static-project"\nversion = "v2.3.4"\n',
        encoding="utf-8",
    )
    (root / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(build),
                    "file": str(source),
                    "arguments": [
                        "clang++",
                        "-std=c++20",
                        "-c",
                        str(source),
                    ],
                    "output": str(build / "main.o"),
                }
            ]
        ),
        encoding="utf-8",
    )

    def unexpected_side_effect(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("default export loading must not discover or execute build tools")

    monkeypatch.setattr(export_module, "discover_project_model", unexpected_side_effect)
    monkeypatch.setattr("ici.core.project._iter_project_files", unexpected_side_effect)
    monkeypatch.setattr("ici.core.project.subprocess.run", unexpected_side_effect)

    project, context = load_export_context(root, {"type": "cpp"})

    assert project.name == "static-project"
    assert project.version == "v2.3.4"
    assert project.backend is None
    assert context.database_path == "compile_commands.json"
    assert context.origin == "discovered"
    assert len(context.units) == 1


def test_unknown_compiler_options_have_distinct_digests_and_structured_inconclusive_evidence(
    tmp_path: Path,
) -> None:
    pic = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=("clang++", "-std=c++20", "-fPIC", "-c", "src/main.cpp"),
            )
        ),
    )["compilation"]["units"][0]
    no_plt = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=("clang++", "-std=c++20", "-fno-plt", "-c", "src/main.cpp"),
            )
        ),
    )["compilation"]["units"][0]

    assert pic["comparison_state"] == no_plt["comparison_state"] == "inconclusive"
    assert pic["invocation"]["digest"] != no_plt["invocation"]["digest"]
    assert pic["configuration_digest"] != no_plt["configuration_digest"]
    assert [item["name"] for item in pic["invocation"]["unmodeled_options"]] == ["-fPIC"]
    assert [item["name"] for item in no_plt["invocation"]["unmodeled_options"]] == ["-fno-plt"]
    assert all(
        set(item) == {"name", "order", "token_digest"}
        for item in pic["invocation"]["unmodeled_options"]
    )


def test_unmodeled_option_names_never_expose_attached_paths(tmp_path: Path) -> None:
    private_path = "-F/private/company/Frameworks"
    payload = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=("clang++", private_path, "-c", "src/main.cpp"),
            )
        ),
    )
    invocation = payload["compilation"]["units"][0]["invocation"]
    rendered = render_compilation_export(payload).decode("utf-8")

    assert invocation["unmodeled_options"][0]["name"] == REDACTED
    assert private_path not in rendered
    assert "/private/company" not in rendered


def test_msvc_option_spelling_is_case_sensitive_in_public_model(tmp_path: Path) -> None:
    payload = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=(
                    "cl.exe",
                    "/UDEBUG",
                    "/udifferent",
                    "/clang:--target=x86_64-pc-windows-msvc",
                    "/clang:--TARGET=must-not-be-modeled",
                    "/c",
                    "src/main.cpp",
                ),
                compiler="cl.exe",
            )
        ),
    )
    unit = payload["compilation"]["units"][0]

    assert unit["undefines"] == ["DEBUG"]
    assert unit["invocation"]["target_triple"] == "x86_64-pc-windows-msvc"
    assert [item["name"] for item in unit["invocation"]["unmodeled_options"]] == [
        "/udifferent",
        REDACTED,
    ]


def test_compiler_wrappers_paths_targets_and_undefines_are_safely_projected(
    tmp_path: Path,
) -> None:
    wrapped = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=(
                    "env",
                    "BUILD_MODE=release",
                    "ccache",
                    "../tools/clang++-18",
                    "--target",
                    "x86_64-linux-gnu",
                    "-U",
                    "OLD_MODE",
                    "-UINVALID-NAME",
                    "-MF",
                    "deps.d",
                    "--",
                    "-ignored-after-separator",
                ),
            )
        ),
    )["compilation"]["units"][0]
    windows = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=(r"C:\LLVM\bin\clang-cl.exe", "/c", "src/main.cpp"),
                compiler="clang-cl.exe",
            )
        ),
    )["compilation"]["units"][0]

    assert wrapped["compiler"] == {
        "family": "clang",
        "name": "clang++-18",
        "path": "tools/clang++-18",
        "wrappers": ["env", "ccache"],
    }
    assert wrapped["invocation"]["target_triple"] == "x86_64-linux-gnu"
    assert wrapped["invocation"]["unmodeled_options"] == []
    assert wrapped["undefines"] == ["OLD_MODE"]
    assert windows["compiler"] == {
        "family": "clang-cl",
        "name": "clang-cl.exe",
        "path": "[external]",
        "wrappers": [],
    }


def test_unsafe_separated_target_and_missing_undefine_are_not_exposed(tmp_path: Path) -> None:
    payload = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=(
                    "/usr/bin/g++",
                    "--target",
                    "/private/toolchain/triple",
                    "-U",
                ),
            )
        ),
    )
    unit = payload["compilation"]["units"][0]
    rendered = render_compilation_export(payload).decode("utf-8")

    assert unit["compiler"]["path"] == "[external]"
    assert unit["invocation"]["target_triple"] == REDACTED
    assert unit["undefines"] == []
    assert "/private/toolchain" not in rendered


@pytest.mark.parametrize(
    ("files", "config", "expected_type", "expected_backend"),
    [
        (("pyproject.toml",), {}, "python", None),
        (("Makefile",), {}, "cpp", None),
        (("Makefile", "pyproject.toml"), {}, "hybrid", None),
        (("CMakeLists.txt", "pyproject.toml"), {}, "hybrid", "cmake"),
        (("CMakeLists.txt",), {"project": {"type": "python"}}, "python", "cmake"),
    ],
)
def test_static_export_project_type_uses_root_descriptors_without_scanning(
    tmp_path: Path,
    files: tuple[str, ...],
    config: dict[str, Any],
    expected_type: str,
    expected_backend: str | None,
) -> None:
    for name in files:
        content = (
            "[project]\nname='static-fixture'\nversion='1.0.0'\n"
            if name == "pyproject.toml"
            else ""
        )
        (tmp_path / name).write_text(content, encoding="utf-8")

    project = discover_export_project(tmp_path, config)

    assert project.project_type == expected_type
    assert project.backend == expected_backend


def test_source_bytes_and_semantic_digests_are_distinct_and_deterministic(tmp_path: Path) -> None:
    same_units = _context(_unit(), database_digest="sha256:" + "b" * 64)
    first = compilation_export_payload(_project(tmp_path), _context(_unit()))["compilation"]
    second = compilation_export_payload(_project(tmp_path), same_units)["compilation"]
    changed = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                argv=("clang++", "-std=c++20", "-DDEBUG", "-c", "src/main.cpp"),
                defines=(CompilationDefine(name="DEBUG"),),
            )
        ),
    )["compilation"]

    assert first["source_bytes_digest"] != second["source_bytes_digest"]
    assert first["semantic_digest"] == second["semantic_digest"]
    assert first["source_bytes_digest"].startswith("sha256:")
    assert first["semantic_digest"].startswith("sha256:")
    assert changed["semantic_digest"] != first["semantic_digest"]


def test_units_are_sorted_and_state_tracks_duplicate_configurations(tmp_path: Path) -> None:
    default_a = _unit(source="src/a.cpp", output="build/a.o")
    debug_a = _unit(
        source="src/a.cpp",
        output="build/a.o",
        argv=("clang++", "-std=c++20", "-DDEBUG", "-c", "src/a.cpp"),
        defines=(CompilationDefine(name="DEBUG"),),
    )
    payload = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(source="src/b.cpp", output="build/b.o"),
            default_a,
            debug_a,
            _unit(source="src/a.cpp", output="build/a.o"),
        ),
    )
    units = payload["compilation"]["units"]

    def sort_key(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            item["source"],
            item["target"] or "",
            item["configuration_digest"],
            item["directory"],
            item["output"],
        )

    assert [sort_key(item) for item in units] == sorted(sort_key(item) for item in units)
    assert [item["state"]["unit_index"] for item in units] == list(range(4))
    assert [item["state"]["source_configuration_count"] for item in units] == [2, 2, 2, 1]
    assert sum(item["state"]["duplicate"] for item in units) == 2
    assert all(
        item["state"]["duplicate"] is False for item in units if item["source"] == "src/b.cpp"
    )


def test_external_and_secret_values_are_redacted_and_inconclusive(tmp_path: Path) -> None:
    secret = CompilationDefine(name="API_TOKEN", value="super-secret-value")
    external_include = CompilationSearchPath(
        path="/opt/company-sdk/include",
        kind="system",
        scope="external",
        exists=True,
    )
    unit = _unit(
        defines=(secret,),
        include_paths=(external_include,),
        sysroot="/opt/company-sdk",
        sysroot_scope="external",
    )

    payload = compilation_export_payload(_project(tmp_path), _context(unit))
    exported_unit = payload["compilation"]["units"][0]
    rendered = render_compilation_export(payload).decode("utf-8")

    assert exported_unit["comparison_state"] == "inconclusive"
    assert payload["compilation"]["comparison_state"] == "inconclusive"
    assert exported_unit["defines"] == [
        {"name": "API_TOKEN", "value": REDACTED, "value_state": "redacted"}
    ]
    assert exported_unit["include_paths"][0]["exists"] is None
    assert exported_unit["include_paths"][0]["path"] == "[external]"
    assert exported_unit["sysroot"] == {"path": "[external]", "scope": "external"}
    assert "super-secret-value" not in rendered
    assert "/opt/company-sdk" not in rendered


def test_unsafe_standard_generator_and_relative_define_path_are_redacted(
    tmp_path: Path,
) -> None:
    raw_define_path = "../../outside/private-sdk"
    raw_standard = "/private/toolchain/c++20"
    raw_generator = "Ninja /private/toolchain"
    payload = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                defines=(CompilationDefine(name="SDK_ROOT", value=raw_define_path),),
                standard=raw_standard,
            ),
            generator=raw_generator,
        ),
    )
    exported = payload["compilation"]
    exported_define = exported["units"][0]["defines"][0]
    rendered = render_compilation_export(payload).decode("utf-8")

    assert exported["generator"] == REDACTED
    assert exported["units"][0]["standard"] == REDACTED
    assert exported_define["value_state"] == "redacted"
    assert exported_define["value"] == "[external]"
    assert raw_define_path not in rendered
    assert raw_standard not in rendered
    assert raw_generator not in rendered
    assert exported["comparison_state"] == "inconclusive"


def test_quoted_relative_define_paths_are_resolved_before_public_projection(
    tmp_path: Path,
) -> None:
    project_value = '"../fixtures/sample.json"'
    external_value = '"../../private-sdk/license"'
    payload = compilation_export_payload(
        _project(tmp_path),
        _context(
            _unit(
                defines=(
                    CompilationDefine(name="PROJECT_FIXTURE", value=project_value),
                    CompilationDefine(name="SDK_LICENSE", value=external_value),
                ),
            )
        ),
    )
    definitions = payload["compilation"]["units"][0]["defines"]
    rendered = render_compilation_export(payload).decode("utf-8")

    assert definitions == [
        {
            "name": "PROJECT_FIXTURE",
            "value": '"fixtures/sample.json"',
            "value_state": "redacted",
        },
        {
            "name": "SDK_LICENSE",
            "value": '"[external]"',
            "value_state": "redacted",
        },
    ]
    assert "private-sdk" not in rendered


def test_public_export_does_not_expose_raw_argv_or_command(tmp_path: Path) -> None:
    unit = _unit(
        argv=("clang++", "--token", "argv-secret", "-c", "src/main.cpp"),
    )

    payload = compilation_export_payload(_project(tmp_path), _context(unit))
    rendered = render_compilation_export(payload).decode("utf-8")

    assert '"argv":' not in rendered
    assert '"command":' not in rendered
    assert "argv" not in payload["compilation"]["units"][0]
    assert "argv-secret" not in rendered


@pytest.mark.parametrize(
    "context",
    [
        _context(),
        _context(origin=""),
        _context(_unit(compiler="")),
    ],
)
def test_empty_or_untrusted_contexts_fail_closed(
    tmp_path: Path, context: CompilationContext
) -> None:
    with pytest.raises(CompilationExportError):
        compilation_export_payload(_project(tmp_path), context)


@pytest.mark.parametrize(
    ("context", "exit_code"),
    [
        (CompilationContext(), 2),
        (
            _context(
                diagnostics=(
                    CompilationDiagnostic(
                        code="database-malformed",
                        message="database is malformed",
                        level="error",
                    ),
                )
            ),
            1,
        ),
        (
            _context(
                _unit(
                    diagnostics=(
                        CompilationDiagnostic(
                            code="source-invalid",
                            message="source is invalid",
                            level="error",
                        ),
                    )
                )
            ),
            1,
        ),
    ],
)
def test_unavailable_and_fatal_contexts_have_stable_exit_codes(
    tmp_path: Path,
    context: CompilationContext,
    exit_code: int,
) -> None:
    with pytest.raises(CompilationExportError) as raised:
        compilation_export_payload(_project(tmp_path), context)

    assert raised.value.exit_code == exit_code


def test_database_override_is_project_relative_and_does_not_mutate_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = {"project": {"source_dirs": ["src"]}}

    effective = config_with_database(config, root, "build/../out/compile_commands.json")

    assert effective is not config
    assert config == {"project": {"source_dirs": ["src"]}}
    assert effective["project"]["compile_database"] == "out/compile_commands.json"


@pytest.mark.parametrize(
    "database",
    [
        "../outside/compile_commands.json",
        "/tmp/compile_commands.json",
        r"C:\tmp\compile_commands.json",
        r"build\compile_commands.json",
    ],
)
def test_database_override_rejects_traversal_absolute_and_windows_paths(
    tmp_path: Path, database: str
) -> None:
    with pytest.raises(
        CompilationExportError,
        match=r"project-relative POSIX|unsafe --database path",
    ) as raised:
        config_with_database({}, tmp_path, database)
    assert raised.value.exit_code == 2


def test_database_override_rejects_drive_relative_windows_path_with_exit_two(
    tmp_path: Path,
) -> None:
    with pytest.raises(CompilationExportError, match="project-relative POSIX") as raised:
        config_with_database({}, tmp_path, "C:foo")
    assert raised.value.exit_code == 2


def test_database_override_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    (root / "generated").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CompilationExportError, match="unsafe --database path"):
        config_with_database({}, root, "generated/compile_commands.json")


def test_render_rejects_oversized_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = compilation_export_payload(_project(tmp_path), _context(_unit()))
    monkeypatch.setattr(export_module, "MAX_EXPORT_BYTES", 1)

    with pytest.raises(CompilationExportError, match="output limit"):
        render_compilation_export(payload)


def test_atomic_write_replaces_target_and_cleans_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "nested" / "export.json"
    target.parent.mkdir()
    target.write_bytes(b"old")

    write_compilation_export(target, b"new")

    assert target.read_bytes() == b"new"
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []

    target.write_bytes(b"preserved")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(CompilationExportError, match="could not write compilation export"):
        write_compilation_export(target, b"must-not-win")

    assert target.read_bytes() == b"preserved"
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_validated_symlink_output_replaces_link_without_touching_referent(tmp_path: Path) -> None:
    referent = tmp_path / "referent.json"
    referent.write_bytes(b"must-survive")
    link = tmp_path / "context.json"
    link.symlink_to(referent)

    target = validate_export_output(
        tmp_path,
        "context.json",
        "build/compile_commands.json",
    )

    assert target == link
    assert target is not None
    write_compilation_export(target, b"export")
    assert not link.is_symlink()
    assert link.read_bytes() == b"export"
    assert referent.read_bytes() == b"must-survive"


def test_writer_enforces_output_bound_before_replacing_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "context.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(export_module, "MAX_EXPORT_BYTES", 3)

    with pytest.raises(CompilationExportError, match="output limit"):
        write_compilation_export(target, b"too-large")

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


@pytest.mark.parametrize(
    "protected_output",
    [
        "build/compile_commands.json",
        "ici.toml",
        "dev.toml",
        "pyproject.toml",
    ],
)
def test_output_validation_rejects_database_and_policy_overwrite(
    tmp_path: Path, protected_output: str
) -> None:
    output = tmp_path / protected_output

    with pytest.raises(CompilationExportError, match="must not overwrite") as raised:
        validate_export_output(
            tmp_path,
            str(output),
            "build/compile_commands.json",
        )

    assert raised.value.exit_code == 2
    assert validate_export_output(tmp_path, "-", "build/compile_commands.json") is None


def test_output_validation_rejects_protected_alias_and_non_regular_target(
    tmp_path: Path,
) -> None:
    database = tmp_path / "build" / "compile_commands.json"
    database.parent.mkdir()
    database.write_text("[]", encoding="utf-8")
    hardlink = tmp_path / "hardlink.json"
    hardlink.hardlink_to(database)
    fifo = tmp_path / "context.fifo"
    os.mkfifo(fifo)

    for output in (hardlink, fifo):
        with pytest.raises(CompilationExportError) as raised:
            validate_export_output(tmp_path, str(output), "build/compile_commands.json")
        assert raised.value.exit_code == 2


def test_cli_stdout_is_json_only_and_output_file_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    context = _context(_unit())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "ici.compilation_export_cli.load_export_context",
        lambda root, config, *, prepare: (project, context),
    )

    stdout_result = runner.invoke(app, ["export-compilation-context"])

    assert stdout_result.exit_code == 0
    assert stdout_result.stderr == ""
    stdout_payload = json.loads(stdout_result.stdout)
    _assert_schema_shape(stdout_payload)

    output = tmp_path / "context.json"
    file_result = runner.invoke(
        app,
        ["export-compilation-context", "--output", str(output), "--pretty"],
    )

    assert file_result.exit_code == 0
    assert file_result.stdout == ""
    assert file_result.stderr == ""
    _assert_schema_shape(json.loads(output.read_text(encoding="utf-8")))


def test_cli_maps_unavailable_and_fatal_contexts_to_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})

    for context, expected_code in ((_context(database_path=None, database_digest=""), 2),):
        monkeypatch.setattr(
            "ici.compilation_export_cli.load_export_context",
            lambda root, config, *, prepare, context=context: (project, context),
        )
        result = runner.invoke(app, ["export-compilation-context"])
        assert result.exit_code == expected_code
        assert result.stdout == ""
        assert "Compilation export error:" in result.stderr

    fatal = _context(
        diagnostics=(CompilationDiagnostic(code="fatal-db", message="fatal", level="error"),)
    )
    monkeypatch.setattr(
        "ici.compilation_export_cli.load_export_context",
        lambda root, config, *, prepare: (project, fatal),
    )
    result = runner.invoke(app, ["export-compilation-context"])
    assert result.exit_code == 1
    assert "fatal-db" in result.stderr


def test_cli_prepare_flag_is_delegated_and_database_override_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    context = _context(_unit())
    captured: dict[str, Any] = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ici.__main__.load_config", lambda *args, **kwargs: {})

    def fake_load_export_context(root: Path, config: dict[str, Any], *, prepare: bool):
        captured["root"] = root
        captured["config"] = config
        captured["prepare"] = prepare
        return project, context

    monkeypatch.setattr("ici.compilation_export_cli.load_export_context", fake_load_export_context)
    result = runner.invoke(
        app,
        [
            "export-compilation-context",
            "--database",
            "build/custom.json",
            "--prepare",
        ],
    )

    assert result.exit_code == 0
    assert captured["root"] == tmp_path.resolve()
    assert captured["prepare"] is True
    assert captured["config"]["project"]["compile_database"] == "build/custom.json"


def test_prepare_context_delegates_to_the_selected_cmake_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, backend="cmake")
    context = _context(_unit())
    captured: dict[str, object] = {}

    monkeypatch.setattr(export_module, "discover_project_model", lambda root, config: project)

    def fake_prepare(root: Path, config: dict[str, Any], selected: ProjectModel):
        captured["root"] = root
        captured["config"] = config
        captured["project"] = selected
        return context

    monkeypatch.setattr(
        "ici.core.cmake_context.prepare_cmake_compilation_context",
        fake_prepare,
    )

    selected, prepared = load_export_context(tmp_path, {"project": {}}, prepare=True)

    assert selected is project
    assert prepared is context
    assert captured == {
        "root": tmp_path,
        "config": {"project": {}},
        "project": project,
    }


def test_prepare_keeps_an_explicit_missing_database_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(export_module, "discover_project_model", lambda root, config: project)

    def unexpected_configure(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("an explicitly selected database must not be replaced")

    monkeypatch.setattr("ici.core.cmake_context.configure", unexpected_configure)

    selected, context = load_export_context(
        tmp_path,
        {"project": {"compile_database": "metadata/missing.json"}},
        prepare=True,
    )

    assert selected is project
    assert context.database_path == "metadata/missing.json"
    assert context.origin == "configured"
    assert [item.code for item in context.diagnostics] == ["database-missing"]


def test_export_callback_disables_global_default_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    context = _context(_unit())
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("ICI_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def wrapped_load_config(*args, **kwargs):
        captured.update(kwargs)
        from ici.config import load_config as actual_load_config

        return actual_load_config(*args, **kwargs)

    monkeypatch.setattr("ici.__main__.load_config", wrapped_load_config)
    monkeypatch.setattr(
        "ici.compilation_export_cli.load_export_context",
        lambda root, config, *, prepare: (project, context),
    )

    result = runner.invoke(app, ["export-compilation-context"])

    assert result.exit_code == 0
    assert captured["create_global_default"] is False
    assert not get_global_config_path().exists()
