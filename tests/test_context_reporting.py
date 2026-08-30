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
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineResult, EngineStatus, VerificationSuiteResult
from ici.core.redaction import redact_suite
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
                    argv=("g++", "-Iinclude", "-c", "src/main.cpp"),
                    output="build/main.o",
                ),
            ),
            database_path="build/compile_commands.json",
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
        "units": [
            {
                "source": "src/main.cpp",
                "directory": ".",
                "argv": ["g++", "-Iinclude", "-c", "src/main.cpp"],
                "output": "build/main.o",
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
    assert redacted.analysis_context is context
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
    manifest_definition = schema["$defs"]["artifactManifest"]
    assert manifest_definition["properties"]["schema_version"] == {"const": "ici.artifacts/v1"}
