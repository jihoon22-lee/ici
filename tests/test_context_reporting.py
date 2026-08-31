"""JSON/reporting contracts for the shared analysis context and artifacts."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    ArtifactManifest,
    ArtifactScope,
    BuildVariant,
    CompilationContext,
    CompilationDefine,
    CompilationDiagnostic,
    CompilationSearchPath,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineResult, EngineStatus, VerificationSuiteResult
from ici.core.redaction import redact_suite
from ici.core.redaction_values import REDACTED
from ici.reporters.json_rep import (
    migrate_report_payload,
    serialize_engine_result,
    serialize_suite_result,
)


def _identity() -> AnalysisIdentity:
    return AnalysisIdentity(
        source_commit="a" * 40,
        config_digest=canonical_digest({"config": "fixture"}),
        toolchain_digest=canonical_digest({"compiler": "fixture"}),
    )


def _project(root: Path) -> ProjectModel:
    return ProjectModel(
        root=root,
        name="reporting-fixture",
        version="v1.2.3",
        project_type="hybrid",
        source_dirs=("src", "include"),
        python_sources=("src/app.py",),
        cpp_sources=("src/main.cpp", "src/z.cpp"),
        cpp_headers=("include/api.hpp",),
        compilable_cpp_sources=("src/main.cpp", "src/z.cpp"),
        external_cpp_dirs=("src/generated",),
        cpp_include_flags=("-Iinclude",),
        backend="cmake",
        backend_descriptor="CMakeLists.txt",
        backend_reason="CMakeLists.txt at the project root selected the CMake backend",
    )


def _manifest(root: Path, identity: AnalysisIdentity) -> ArtifactManifest:
    shadow = root / "build" / "ici-coverage"
    (root / "dist").mkdir(parents=True)
    shadow.mkdir(parents=True)
    project_z = root / "dist" / "z.bin"
    project_a = root / "dist" / "a.bin"
    shadow_z = shadow / "z.o"
    shadow_a = shadow / "a.o"
    project_z.write_bytes(b"project-z")
    project_a.write_bytes(b"project-a")
    shadow_z.write_bytes(b"shadow-z")
    shadow_a.write_bytes(b"shadow-a")

    # The input order must not become report order.
    return ArtifactManifest.create(
        root,
        shadow,
        BuildVariant.COVERAGE,
        identity,
        [
            (Path("z.o"), ArtifactScope.SHADOW, "object"),
            (Path("dist/z.bin"), ArtifactScope.PROJECT, "binary"),
            (Path("a.o"), ArtifactScope.SHADOW, "object"),
            (Path("dist/a.bin"), ArtifactScope.PROJECT, "binary"),
        ],
        "build-engine",
    )


def _context_fixture(tmp_path: Path) -> tuple[AnalysisContext, ArtifactManifest]:
    identity = _identity()
    manifest = _manifest(tmp_path, identity)
    context = AnalysisContext(
        project=_project(tmp_path),
        capabilities=CapabilityInventory(),
        identity=identity,
        compilation=CompilationContext(
            units=(
                CompilationUnit(
                    source="src/main.cpp",
                    directory=".",
                    argv=(
                        "/opt/toolchain/g++",
                        f"-I{tmp_path / 'include'}",
                        "-isystem",
                        "/opt/vendor/include",
                        "-DAPI_TOKEN=secret-value",
                        "--token",
                        "another-secret",
                        "-c",
                        str(tmp_path / "src" / "main.cpp"),
                    ),
                    output="build/main.o",
                    compiler="g++",
                    language="c++",
                    standard="c++20",
                    defines=(
                        CompilationDefine("NAME", "1"),
                        CompilationDefine("API_TOKEN", "secret-value"),
                    ),
                    include_paths=(
                        CompilationSearchPath("include", "include", "project", True),
                        CompilationSearchPath("/opt/vendor/include", "system", "external", True),
                    ),
                    sysroot="/opt/vendor/sysroot",
                    sysroot_scope="external",
                    configuration="sha256:" + "d" * 64,
                    diagnostics=(
                        CompilationDiagnostic(
                            "missing-include-dir",
                            "A configured compiler include directory does not exist.",
                            entry_index=0,
                            source="src/main.cpp",
                        ),
                    ),
                ),
            ),
            database_path="build/compile_commands.json",
            database_digest="sha256:" + "e" * 64,
            diagnostics=(
                CompilationDiagnostic(
                    "invalid-entry",
                    "A compilation database entry is not an object.",
                    level="error",
                    entry_index=1,
                ),
            ),
        ),
        requested_variants=(BuildVariant.SANITIZE, BuildVariant.COVERAGE),
        manifests=(manifest,),
    )
    return context, manifest


def _suite_fixture(
    tmp_path: Path,
) -> tuple[VerificationSuiteResult, AnalysisContext, ArtifactManifest]:
    context, manifest = _context_fixture(tmp_path)
    engine = EngineResult(
        engine_name="build",
        status=EngineStatus.PASS,
        summary="build complete",
        artifact_manifests=(manifest,),
    )
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[engine],
        capability_inventory=context.capabilities,
        analysis_context=context,
    )
    return suite, context, manifest


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.items() for text in _strings(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _strings(item)]
    return []


def test_suite_serializes_context_as_relative_facts_with_all_identity_provenance(
    tmp_path: Path,
) -> None:
    suite, context, manifest = _suite_fixture(tmp_path)

    payload = serialize_suite_result(suite)
    serialized = payload["analysis_context"]
    assert isinstance(serialized, dict)
    assert serialized["schema_version"] == "ici.analysis-context/v1"
    assert context.profile == "standard"
    assert serialized["profile"] == "standard"
    assert "root" not in serialized
    assert "project_root" not in serialized["project"]
    assert serialized["project"] == {
        "name": "reporting-fixture",
        "version": "v1.2.3",
        "type": "hybrid",
        "source_dirs": ["src", "include"],
        "python_sources": ["src/app.py"],
        "cpp_sources": ["src/main.cpp", "src/z.cpp"],
        "cpp_headers": ["include/api.hpp"],
        "compilable_cpp_sources": ["src/main.cpp", "src/z.cpp"],
        "external_cpp_dirs": ["src/generated"],
        "cpp_include_flags": ["-Iinclude"],
        "backend": "cmake",
        "backend_descriptor": "CMakeLists.txt",
        "backend_reason": "CMakeLists.txt at the project root selected the CMake backend",
    }
    assert serialized["identity"] == {
        "source_commit": context.identity.source_commit,
        "config_digest": context.identity.config_digest,
        "toolchain_digest": context.identity.toolchain_digest,
    }
    assert serialized["compilation"] == {
        "database_path": "build/compile_commands.json",
        "database_digest": "sha256:" + "e" * 64,
        "diagnostics": [
            {
                "code": "invalid-entry",
                "message": "A compilation database entry is not an object.",
                "level": "error",
                "entry_index": 1,
                "source": "",
            }
        ],
        "units": [
            {
                "source": "src/main.cpp",
                "directory": ".",
                "argv": [
                    "[external]",
                    "-Iinclude",
                    "-isystem",
                    "[external]",
                    f"-DAPI_TOKEN={REDACTED}",
                    "--token",
                    REDACTED,
                    "-c",
                    "src/main.cpp",
                ],
                "output": "build/main.o",
                "compiler": "g++",
                "language": "c++",
                "standard": "c++20",
                "defines": [
                    {"name": "NAME", "value": "1"},
                    {"name": "API_TOKEN", "value": REDACTED},
                ],
                "include_paths": [
                    {
                        "path": "include",
                        "kind": "include",
                        "scope": "project",
                        "exists": True,
                    },
                    {
                        "path": "[external]",
                        "kind": "system",
                        "scope": "external",
                        "exists": True,
                    },
                ],
                "sysroot": "[external]",
                "sysroot_scope": "external",
                "configuration": "sha256:" + "d" * 64,
                "diagnostics": [
                    {
                        "code": "missing-include-dir",
                        "message": "A configured compiler include directory does not exist.",
                        "level": "warning",
                        "entry_index": 0,
                        "source": "src/main.cpp",
                    }
                ],
            }
        ],
    }
    assert serialized["requested_variants"] == ["coverage", "sanitize"]

    manifest_payload = serialized["artifact_manifests"][0]
    assert manifest_payload["schema_version"] == "ici.artifacts/v1"
    assert manifest_payload["project_root"] == "."
    assert manifest_payload["shadow_root"] == "build/ici-coverage"
    assert manifest_payload["variant"] == "coverage"
    assert manifest_payload["source_commit"] == manifest.source_commit
    assert manifest_payload["config_digest"] == manifest.config_digest
    assert manifest_payload["toolchain_digest"] == manifest.toolchain_digest
    assert [item["path"] for item in manifest_payload["artifacts"]] == [
        "dist/a.bin",
        "dist/z.bin",
        "a.o",
        "z.o",
    ]
    assert all(
        set(item) >= {"path", "scope", "kind", "sha256", "size", "mode", "producer"}
        for item in manifest_payload["artifacts"]
    )

    assert str(tmp_path) not in _strings(payload)


def test_engine_serializer_includes_standalone_artifact_manifest_projection(
    tmp_path: Path,
) -> None:
    suite, _context, manifest = _suite_fixture(tmp_path)
    engine = suite.results[0]

    payload = serialize_engine_result(engine)

    assert (
        payload["artifact_manifests"]
        == serialize_suite_result(suite)["results"][0]["artifact_manifests"]
    )
    assert payload["artifact_manifests"][0]["variant"] == manifest.variant.value
    assert str(tmp_path) not in _strings(payload)


def test_manifest_and_context_report_order_is_deterministic(tmp_path: Path) -> None:
    suite, _context, _manifest_value = _suite_fixture(tmp_path)

    first = serialize_suite_result(suite)
    second = serialize_suite_result(suite)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    records = first["analysis_context"]["artifact_manifests"][0]["artifacts"]
    assert [(record["scope"], record["path"]) for record in records] == [
        ("project", "dist/a.bin"),
        ("project", "dist/z.bin"),
        ("shadow", "a.o"),
        ("shadow", "z.o"),
    ]


def test_redaction_and_serializers_preserve_context_identity_without_aliasing_output(
    tmp_path: Path,
) -> None:
    suite, context, manifest = _suite_fixture(tmp_path)

    redacted = redact_suite(suite)
    assert redacted.analysis_context is not context
    assert redacted.analysis_context is not None
    safe_unit = redacted.analysis_context.compilation.units[0]
    assert safe_unit.argv == (
        "[external]",
        "-Iinclude",
        "-isystem",
        "[external]",
        f"-DAPI_TOKEN={REDACTED}",
        "--token",
        REDACTED,
        "-c",
        "src/main.cpp",
    )
    assert safe_unit.defines[1].value == REDACTED
    assert safe_unit.include_paths[1].path == "[external]"
    assert safe_unit.sysroot == "[external]"
    assert redacted.results[0].artifact_manifests == (manifest,)
    with pytest.raises(FrozenInstanceError):
        context.project.name = "mutated"

    payload = serialize_suite_result(suite)
    payload["analysis_context"]["project"]["name"] = "reporter-mutated"
    payload["analysis_context"]["artifact_manifests"][0]["artifacts"][0]["path"] = "changed"
    assert context.project.name == "reporting-fixture"
    assert context.manifests[0].artifacts[0].path == "dist/a.bin"
    assert suite.analysis_context is context


def test_existing_v3_payload_without_context_extensions_remains_loadable(tmp_path: Path) -> None:
    suite, _context, _manifest_value = _suite_fixture(tmp_path)
    legacy = serialize_suite_result(suite)
    legacy.pop("analysis_context", None)
    for result in legacy["results"]:
        result.pop("artifact_manifests", None)

    migrated = migrate_report_payload(legacy)

    assert migrated["schema_version"] == "ici.result/v3"
    assert migrated["results"][0]["engine_name"] == "build"
    assert "analysis_context" not in legacy
    assert "artifact_manifests" not in migrated["results"][0]


def test_context_profile_is_optional_for_legacy_v3_payloads(tmp_path: Path) -> None:
    suite, context, _manifest_value = _suite_fixture(tmp_path)
    payload = serialize_suite_result(suite)
    payload["analysis_context"].pop("profile")

    migrated = migrate_report_payload(payload)

    assert context.profile == "standard"
    assert "profile" not in migrated["analysis_context"]


def test_legacy_compilation_unit_shape_remains_loadable(tmp_path: Path) -> None:
    suite, _context, _manifest_value = _suite_fixture(tmp_path)
    payload = serialize_suite_result(suite)
    compilation = payload["analysis_context"]["compilation"]
    compilation.pop("database_digest")
    compilation.pop("diagnostics")
    unit = compilation["units"][0]
    for key in tuple(unit):
        if key not in {"source", "directory", "argv", "output"}:
            unit.pop(key)

    migrated = migrate_report_payload(payload)

    assert migrated["analysis_context"]["compilation"] == compilation


def test_checked_in_schema_declares_context_and_manifest_extensions_as_optional() -> None:
    schema_path = (
        Path(__file__).parents[1] / "src" / "ici" / "schemas" / "ici-result-v3.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    suite = schema["$defs"]["suite"]
    engine = schema["$defs"]["engine"]

    assert "analysis_context" in suite["properties"]
    assert "analysis_context" not in suite["required"]
    assert "artifact_manifests" in engine["properties"]
    assert "artifact_manifests" not in engine["required"]

    context_definition = schema["$defs"]["analysisContext"]
    assert context_definition["properties"]["schema_version"] == {
        "const": "ici.analysis-context/v1"
    }
    assert context_definition["properties"]["profile"] == {
        "type": "string",
        "enum": ["fast", "standard", "deep"],
    }
    assert "profile" not in context_definition["required"]
    compilation = context_definition["properties"]["compilation"]
    assert "database_digest" not in compilation["required"]
    assert "diagnostics" not in compilation["required"]
    unit = compilation["properties"]["units"]["items"]
    assert unit["required"] == ["source", "directory", "argv", "output"]
    assert {
        "compiler",
        "language",
        "standard",
        "defines",
        "include_paths",
        "sysroot",
        "sysroot_scope",
        "configuration",
        "diagnostics",
    } <= set(unit["properties"])
    manifest_definition = schema["$defs"]["artifactManifest"]
    assert manifest_definition["properties"]["schema_version"] == {"const": "ici.artifacts/v1"}
