"""Contract tests for the immutable shared analysis context.

The context is deliberately tested as a boundary object: discovery owns only
project facts, capability collection remains an already-created snapshot, and
the factory normalizes all inputs that participate in reproducible analysis.
"""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    BuildVariant,
    CompilationContext,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
    create_analysis_context,
    discover_project_model,
    source_commit,
)


def _project_config() -> dict[str, object]:
    """Return a small hybrid project configuration with an external C++ tree."""

    return {
        "project": {
            "source_dirs": ["python", "src", "external"],
            "cpp_external_build_dirs": ["external"],
        }
    }


def _write_discovery_project(root: Path) -> dict[str, object]:
    """Create files in intentionally non-discovery order for ordering checks."""

    (root / "ici.toml").write_text(
        'name = "context-fixture"\ntype = "hybrid"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )
    python_dir = root / "python"
    source_dir = root / "src"
    external_dir = root / "external"
    include_dir = root / "include"
    (python_dir / "pkg").mkdir(parents=True)
    (source_dir / "nested").mkdir(parents=True)
    external_dir.mkdir()
    (include_dir / "public").mkdir(parents=True)

    # Creation order is intentionally the reverse of the expected canonical order.
    (python_dir / "pkg" / "z.py").write_text("Z = 1\n", encoding="utf-8")
    (python_dir / "pkg" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (source_dir / "nested" / "z.cc").write_text("int z() { return 0; }\n", encoding="utf-8")
    (source_dir / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (external_dir / "generated.cpp").write_text("int generated() { return 0; }\n", encoding="utf-8")

    return _project_config()


def test_build_variant_members_have_stable_declared_order() -> None:
    assert tuple(member.name for member in BuildVariant) == (
        "RELEASE",
        "COVERAGE",
        "SANITIZE",
    )


def test_project_discovery_is_canonical_and_deterministically_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NAS_SHARED_DIR", str(tmp_path / "missing-nas"))
    config = _write_discovery_project(tmp_path)

    first = discover_project_model(tmp_path, config)
    second = discover_project_model(tmp_path, config)

    assert first == second
    assert isinstance(first, ProjectModel)
    assert first.root == tmp_path.resolve()
    assert first.name == "context-fixture"
    assert first.version == "v2.3.4"
    assert first.project_type == "hybrid"
    assert first.source_dirs == (
        "external",
        "python",
        "src",
    )
    assert first.python_sources == (
        "python/pkg/a.py",
        "python/pkg/z.py",
    )
    assert first.cpp_sources == (
        "external/generated.cpp",
        "src/main.cpp",
        "src/nested/z.cc",
    )
    assert first.compilable_cpp_sources == (
        "src/main.cpp",
        "src/nested/z.cc",
    )
    assert first.external_cpp_dirs == ("external",)
    assert first.cpp_include_flags == (
        f"-I{(tmp_path / 'include').resolve()}",
        f"-I{(tmp_path / 'include/public').resolve()}",
    )


def test_configured_symlink_source_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.py").write_text("SECRET = True\n", encoding="utf-8")
    (root / "src").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside project root"):
        discover_project_model(root, {"project": {"source_dirs": ["src"]}})


def test_default_symlink_source_escape_is_ignored(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.py").write_text("SECRET = True\n", encoding="utf-8")
    (root / "ici.toml").write_text(
        'name = "safe-default"\ntype = "python"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    (root / "src").symlink_to(outside, target_is_directory=True)

    model = discover_project_model(root, {})

    assert model.source_dirs == ()
    assert model.python_sources == ()
    assert model.cpp_sources == ()


def test_discovery_ignores_symlinked_files_and_directories_inside_source(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "src"
    source.mkdir()
    (source / "safe.py").write_text("SAFE = True\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.py").write_text("SECRET = True\n", encoding="utf-8")
    (source / "escaped.py").symlink_to(outside / "escaped.py")
    (source / "escaped-dir").symlink_to(outside, target_is_directory=True)
    (root / "ici.toml").write_text(
        'name = "safe-nested"\ntype = "python"\nversion = "1.0.0"\n', encoding="utf-8"
    )

    model = discover_project_model(root, {"project": {"source_dirs": ["src"]}})

    assert model.python_sources == ("src/safe.py",)


def test_context_models_are_frozen_and_nested_collections_are_immutable(tmp_path: Path) -> None:
    unit = CompilationUnit(
        source="src/main.cpp",
        directory=".",
        argv=["g++", "-c", "main.cpp"],
        output="build/main.o",
    )
    compilation = CompilationContext(units=[unit], database_path="build/compile_commands.json")
    identity = AnalysisIdentity(
        "unavailable", canonical_digest({"config": True}), canonical_digest({"tools": True})
    )
    project = ProjectModel(
        root=tmp_path,
        name="demo",
        version="v1.0.0",
        project_type="cpp",
        source_dirs=("src",),
        python_sources=(),
        cpp_sources=("src/main.cpp",),
        compilable_cpp_sources=("src/main.cpp",),
        external_cpp_dirs=(),
        cpp_include_flags=(),
        backend="none",
        backend_descriptor="none",
        backend_reason="no build backend",
    )
    capabilities = CapabilityInventory()
    context = AnalysisContext(project, capabilities, identity, compilation=compilation)

    assert context.profile == "standard"
    assert isinstance(unit.argv, tuple)
    assert isinstance(compilation.units, tuple)
    assert isinstance(context.requested_variants, tuple)
    assert isinstance(context.capabilities.capabilities, MappingProxyType)
    for field_name in (
        "source_dirs",
        "python_sources",
        "cpp_sources",
        "compilable_cpp_sources",
        "external_cpp_dirs",
        "cpp_include_flags",
    ):
        assert isinstance(getattr(project, field_name), tuple)

    with pytest.raises(FrozenInstanceError):
        project.name = "changed"
    with pytest.raises(FrozenInstanceError):
        identity.config_digest = "changed"
    with pytest.raises(FrozenInstanceError):
        unit.output = "changed"
    with pytest.raises(FrozenInstanceError):
        compilation.database_path = None
    with pytest.raises(FrozenInstanceError):
        context.project = project
    with pytest.raises(TypeError):
        context.capabilities.capabilities["new"] = object()


def test_canonical_digest_is_order_independent_and_mutation_sensitive() -> None:
    first = {
        "project": {"source_dirs": ["src", "lib"], "type": "hybrid"},
        "engines": {"line": {"enabled": True}, "test": {"enabled": False}},
    }
    same_values_different_order = {
        "engines": {"test": {"enabled": False}, "line": {"enabled": True}},
        "project": {"type": "hybrid", "source_dirs": ["src", "lib"]},
    }

    assert canonical_digest(first) == canonical_digest(same_values_different_order)

    first["project"]["source_dirs"].append("app")
    assert canonical_digest(first) != canonical_digest(same_values_different_order)


def test_source_commit_returns_actual_head_for_git_project(tmp_path: Path) -> None:
    git_root = tmp_path / "git-project"
    git_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=git_root, check=True)
    (git_root / "README.md").write_text("context\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=git_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ici-test",
            "-c",
            "user.email=ici-test@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=git_root,
        check=True,
    )
    expected = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_root, text=True
    ).strip()

    assert source_commit(git_root) == expected


def test_source_commit_is_explicitly_unavailable_outside_git(tmp_path: Path) -> None:
    root = tmp_path / "not-a-repository"
    root.mkdir()

    assert source_commit(root) == "unavailable"


def test_factory_retains_capability_identity_and_normalizes_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "ici.toml").write_text(
        'name = "context-factory"\ntype = "python"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    monkeypatch.setenv("NAS_SHARED_DIR", str(tmp_path / "missing-nas"))
    config = {"project": {"source_dirs": []}}
    capabilities = CapabilityInventory()

    first = create_analysis_context(
        root,
        config,
        capabilities,
        requested_variants=[BuildVariant.SANITIZE, BuildVariant.RELEASE, BuildVariant.SANITIZE],
    )
    second = create_analysis_context(
        root,
        config,
        capabilities,
        requested_variants=[BuildVariant.RELEASE, BuildVariant.SANITIZE],
    )

    assert first.capabilities is capabilities
    assert second.capabilities is capabilities
    assert first.requested_variants == (
        BuildVariant.RELEASE,
        BuildVariant.SANITIZE,
    )
    assert second.requested_variants == first.requested_variants
    assert first.project == second.project
    assert first.identity.source_commit == "unavailable"
    assert first.identity.config_digest == canonical_digest(config)
    assert first.identity.config_digest.startswith("sha256:")
    assert first.identity.toolchain_digest.startswith("sha256:")
