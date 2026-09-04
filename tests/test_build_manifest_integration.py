"""Integration contracts for build-session artifact manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ici.core import cmake as cmake_module
from ici.core.capabilities import CapabilityInventory
from ici.core.cmake import BuildSession, TestCaseResult
from ici.core.cmake import build as adapter_build
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    ArtifactManifest,
    ArtifactScope,
    BuildVariant,
    ProjectModel,
)
from ici.core.models import EngineResult, EngineStatus, SupportMatrix, ToolEvidence
from ici.core.runner import ProcessResult
from ici.engines import build as build_module
from ici.engines import sanitize as sanitize_module
from ici.engines import test as test_module
from ici.engines import verify as verify_module
from ici.engines.build import BuildEngine
from ici.engines.sanitize import SanitizeEngine
from ici.engines.test import TestEngine
from ici.engines.thread_sanitize import ThreadSanitizeEngine
from ici.engines.verify import VerifyOrchestrator

_IDENTITY = AnalysisIdentity(
    source_commit="a" * 40,
    config_digest="sha256:" + "b" * 64,
    toolchain_digest="sha256:" + "c" * 64,
)


def _context(root: Path, *, project_type: str = "cpp") -> AnalysisContext:
    project = ProjectModel(
        root=root,
        name="manifest-project",
        version="1.2.3",
        project_type=project_type,
    )
    return AnalysisContext(
        project=project,
        capabilities=CapabilityInventory(),
        identity=_IDENTITY,
    )


def _session(
    root: Path,
    context: AnalysisContext | None,
    *,
    variant: BuildVariant = BuildVariant.RELEASE,
) -> BuildSession:
    shadow = root / "build" / "ici-cmake-build"
    shadow.mkdir(parents=True, exist_ok=True)
    return BuildSession(
        root=root,
        shadow=shadow,
        variant=variant,
        backend="cmake",
        configured=True,
        analysis_context=context,
    )


def _write_cmake_outputs(session: BuildSession, *, escaped: bool = False) -> None:
    binary = session.shadow / "bin" / "app"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x7fELF\x02\x01ici-test-binary")

    archive = session.shadow / "lib" / "libdemo.a"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"!<arch>\n ici-test-archive")

    # These are build by-products, not linked artifacts.
    object_file = session.shadow / "obj" / "main.o"
    object_file.parent.mkdir(parents=True, exist_ok=True)
    object_file.write_bytes(b"\x7fELF\x02\x01ici-object")
    (session.shadow / "generated.txt").write_text("generated", encoding="utf-8")
    (session.shadow / "script.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    alias = session.shadow / "bin" / "app-alias"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    alias.symlink_to(binary)

    if escaped:
        outside = session.root.parent / "escaped-artifact"
        outside.write_bytes(b"\x7fELF\x02\x01outside")
        escape = session.shadow / "bin" / "escape"
        if escape.exists() or escape.is_symlink():
            escape.unlink()
        escape.symlink_to(outside)


def _fake_successful_build(
    monkeypatch: pytest.MonkeyPatch,
    session: BuildSession,
    *,
    escaped: bool = False,
    result: ProcessResult | None = None,
) -> list[tuple[list[str], Path | None]]:
    calls: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(
        cmake_module,
        "_which",
        lambda _session, _name: "/usr/bin/cmake",
    )

    def run_process(
        argv: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> ProcessResult:
        del env
        calls.append((argv, cwd))
        _write_cmake_outputs(session, escaped=escaped)
        return result or ProcessResult(0, "build succeeded", "", 0.01)

    monkeypatch.setattr(cmake_module, "run_process", run_process)
    return calls


def test_cmake_build_publishes_deterministic_manifest_for_linked_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(manifest)\n", encoding="utf-8")
    context = _context(root)
    session = _session(root, context)
    _fake_successful_build(monkeypatch, session)

    assert adapter_build(session)
    assert session.artifact_manifest is not None
    manifest = session.artifact_manifest
    assert manifest.variant is BuildVariant.RELEASE
    assert manifest.project_root == root.resolve()
    assert manifest.shadow_root == session.shadow.resolve()
    assert manifest.source_commit == context.identity.source_commit
    assert manifest.config_digest == context.identity.config_digest
    assert manifest.toolchain_digest == context.identity.toolchain_digest
    assert [record.path for record in manifest.artifacts] == ["bin/app", "lib/libdemo.a"]
    assert all(record.scope is ArtifactScope.SHADOW for record in manifest.artifacts)
    assert all(record.producer == "cmake.build" for record in manifest.artifacts)
    assert [record.artifact_id for record in manifest.artifacts] == [
        "release:shadow:bin/app",
        "release:shadow:lib/libdemo.a",
    ]
    assert [record.target for record in manifest.artifacts] == ["app", "demo"]
    assert all(
        record.command == ("cmake", "--build", "build/ici-cmake-build", "--parallel")
        for record in manifest.artifacts
    )

    second_session = _session(root, context)
    _fake_successful_build(monkeypatch, second_session)
    assert adapter_build(second_session)
    assert second_session.artifact_manifest == manifest


def test_cmake_artifact_discovery_fails_closed_on_entry_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(manifest)\n", encoding="utf-8")
    session = _session(root, _context(root))
    _fake_successful_build(monkeypatch, session)
    monkeypatch.setattr(cmake_module, "_MAX_ARTIFACT_DISCOVERY_ENTRIES", 1)

    assert not adapter_build(session)
    assert session.artifact_manifest is None
    assert any("artifact discovery exceeds" in error for error in session.errors)


def test_producer_command_is_normalized_and_redacted(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    session = _session(root, _context(root))
    session.tool_evidence.append(
        ToolEvidence(
            name="cmake build",
            path="/usr/bin/cmake",
            argv=[
                "/usr/bin/cmake",
                "--build",
                str(session.shadow),
                "--client-secret",
                "super-secret-value",
            ],
        )
    )

    command = cmake_module._producer_command(session)

    assert command == (
        "cmake",
        "--build",
        "build/ici-cmake-build",
        "--client-secret",
        "***REDACTED***",
    )
    assert all(str(root) not in item for item in command)
    assert all("super-secret-value" not in item for item in command)


def test_cmake_build_does_not_publish_an_escaped_symlink_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    session = _session(root, context)
    _fake_successful_build(monkeypatch, session, escaped=True)

    built = adapter_build(session)

    if built:
        assert session.artifact_manifest is not None
        assert all(record.path != "bin/escape" for record in session.artifact_manifest.artifacts)
        assert session.artifact_manifest.validate() is session.artifact_manifest
    else:
        assert session.artifact_manifest is None


@pytest.mark.parametrize(
    "result",
    [
        ProcessResult(1, "partial output", "compiler error", 0.01),
        ProcessResult(0, "timed out", "", 0.01, timed_out=True),
        ProcessResult(0, "truncated", "", 0.01, truncated=True),
    ],
)
def test_failed_or_incomplete_cmake_build_never_publishes_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: ProcessResult
):
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    session = _session(root, context)
    _fake_successful_build(monkeypatch, session, result=result)

    assert not adapter_build(session)
    assert session.artifact_manifest is None


def test_cmake_build_without_analysis_context_keeps_manifest_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    session = _session(root, None)
    _fake_successful_build(monkeypatch, session)

    assert adapter_build(session)
    assert session.artifact_manifest is None


def _manifest_for_session(session: BuildSession, context: AnalysisContext) -> ArtifactManifest:
    _write_cmake_outputs(session)
    return ArtifactManifest.create(
        project_root=context.project.root,
        shadow_root=session.shadow,
        variant=session.variant,
        identity=context.identity,
        paths=[(Path("bin/app"), ArtifactScope.SHADOW, "executable")],
        producer="cmake.build",
    )


def test_build_engine_result_carries_the_adapter_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(manifest)\n", encoding="utf-8")
    context = _context(root)
    session = _session(root, context, variant=BuildVariant.RELEASE)
    manifest = _manifest_for_session(session, context)
    session.artifact_manifest = manifest
    monkeypatch.setattr(build_module, "adapter_configure", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(build_module, "adapter_build", lambda _session: True)

    result = BuildEngine(root, {}, analysis_context=context).run()

    assert result.artifact_manifests == (manifest,)


def test_test_engine_result_carries_the_coverage_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(manifest)\n", encoding="utf-8")
    tests_root = root / "tests"
    tests_root.mkdir()
    (tests_root / "test_case.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    context = _context(root)
    session = _session(root, context, variant=BuildVariant.COVERAGE)
    manifest = _manifest_for_session(session, context)
    session.artifact_manifest = manifest
    monkeypatch.setattr(test_module, "adapter_configure", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(test_module, "adapter_build", lambda _session: True)
    monkeypatch.setattr(
        test_module,
        "adapter_run_tests",
        lambda _session: [TestCaseResult("test_case", True)],
    )
    monkeypatch.setattr(test_module, "adapter_collect_coverage", lambda _session: None)

    result = TestEngine(root, {}, analysis_context=context).run()

    assert result.artifact_manifests == (manifest,)


def test_sanitize_engine_result_carries_the_sanitize_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(manifest)\n", encoding="utf-8")
    tests_root = root / "tests"
    tests_root.mkdir()
    (tests_root / "test_case.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    context = _context(root)
    session = _session(root, context, variant=BuildVariant.SANITIZE)
    manifest = _manifest_for_session(session, context)
    session.artifact_manifest = manifest
    monkeypatch.setattr(sanitize_module, "adapter_configure", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(sanitize_module, "adapter_build", lambda _session: True)
    monkeypatch.setattr(
        sanitize_module,
        "adapter_run_tests",
        lambda _session, env=None: [TestCaseResult("test_case", True)],
    )

    result = SanitizeEngine(root, {}, analysis_context=context).run()

    assert result.artifact_manifests == (manifest,)


def test_thread_sanitize_engine_carries_only_its_isolated_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(thread_manifest)\n", encoding="utf-8")
    tests_root = root / "tests"
    tests_root.mkdir()
    (tests_root / "test_case.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    context = _context(root)
    session = _session(root, context, variant=BuildVariant.THREAD_SANITIZE)
    manifest = _manifest_for_session(session, context)
    session.artifact_manifest = manifest
    monkeypatch.setattr(sanitize_module, "adapter_configure", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(sanitize_module, "adapter_build", lambda _session: True)
    monkeypatch.setattr(
        sanitize_module,
        "adapter_run_tests",
        lambda _session, env=None: [TestCaseResult("test_case", True)],
    )

    result = ThreadSanitizeEngine(root, {}, analysis_context=context).run()

    assert result.engine_name == "thread_sanitize"
    assert result.artifact_manifests == (manifest,)
    assert result.artifact_manifests[0].variant is BuildVariant.THREAD_SANITIZE


def test_verify_derives_manifest_context_without_mutating_original_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    session = _session(root, context, variant=BuildVariant.RELEASE)
    manifest = _manifest_for_session(session, context)

    def discover_project_model(project_root: Path, config: dict[str, Any]) -> ProjectModel:
        del config
        assert project_root == root.resolve()
        return context.project

    def collect_capability_inventory(*args: Any, **kwargs: Any) -> CapabilityInventory:
        del args, kwargs
        return context.capabilities

    def create_analysis_context(*args: Any, **kwargs: Any) -> AnalysisContext:
        del args, kwargs
        return context

    monkeypatch.setattr(verify_module, "discover_project_model", discover_project_model)
    monkeypatch.setattr(verify_module, "collect_capability_inventory", collect_capability_inventory)
    monkeypatch.setattr(verify_module, "create_analysis_context", create_analysis_context)
    monkeypatch.setattr(
        verify_module, "evaluate_support_matrix", lambda *args, **kwargs: SupportMatrix()
    )
    monkeypatch.setattr(verify_module, "print_suite_dashboard", lambda *args, **kwargs: None)

    class ManifestEngine:
        def __init__(
            self,
            project_root: Path,
            config: dict[str, Any],
            *,
            analysis_context: AnalysisContext,
        ) -> None:
            del project_root, config
            assert analysis_context is context

        def run(self) -> EngineResult:
            return EngineResult(
                "line",
                EngineStatus.PASS,
                "ok",
                artifact_manifests=(manifest,),
            )

    monkeypatch.setattr(verify_module, "LineCountEngine", ManifestEngine)
    config = {"engines": {name: {"enabled": name == "line"} for name in verify_module.ENGINE_NAMES}}

    suite = VerifyOrchestrator(root, config).run_all()

    assert suite.analysis_context is not context
    assert suite.analysis_context is not None
    assert suite.analysis_context.manifests == (manifest,)
    assert context.manifests == ()
    assert suite.analysis_context.project is context.project
    assert suite.analysis_context.capabilities is context.capabilities
    assert suite.analysis_context.identity is context.identity
