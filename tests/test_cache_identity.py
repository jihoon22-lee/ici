"""Tests for analysis-cache source and key identity."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cache_fixtures import (
    _CONFIG_DIGEST,
    _OTHER_DIGEST,
    _SOURCE_DIGEST,
    _TOOLCHAIN_DIGEST,
    _context,
    _descriptor,
    _ImplementationOne,
    _ImplementationTwo,
    _key,
    _result,
)
from ici.core.cache import AnalysisCache, AnalysisCacheKey, project_source_digest
from ici.core.context import (
    BuildVariant,
    CompilationContext,
    CompilationDiagnostic,
    CompilationUnit,
    canonical_digest,
)


def test_project_source_digest_is_deterministic_and_scoped_to_analysis_inputs(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root in (first_root, second_root):
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "ici.toml").write_text('name = "cache"\n', encoding="utf-8")
        (root / "notes.txt").write_text("not an analysis input\n", encoding="utf-8")
        (root / "verify_report.json").write_text('{"generated": 1}\n', encoding="utf-8")

    first_project = _context(first_root).project
    second_project = _context(second_root).project
    first_digest = project_source_digest(first_project)

    assert first_digest == project_source_digest(first_project)
    assert first_digest == project_source_digest(second_project)

    (first_root / "notes.txt").write_text("documentation changed\n", encoding="utf-8")
    (first_root / "verify_report.json").write_text('{"generated": 2}\n', encoding="utf-8")
    assert project_source_digest(first_project) == first_digest

    (first_root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert project_source_digest(first_project) != first_digest

    source_changed_digest = project_source_digest(first_project)
    (first_root / "ici.toml").write_text('name = "cache-renamed"\n', encoding="utf-8")
    assert project_source_digest(first_project) != source_changed_digest


@pytest.mark.parametrize("name", ["README.md", "policy.json", "check.sh", "workflow.yml"])
def test_project_source_digest_tracks_every_line_engine_input_type(
    tmp_path: Path,
    name: str,
) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    tracked = root / "src" / name
    tracked.write_text("first\n", encoding="utf-8")
    project = _context(root).project
    before = project_source_digest(project)

    tracked.write_text("second\n", encoding="utf-8")

    assert project_source_digest(project) != before


def test_analysis_cache_key_is_deterministic_and_includes_all_identity_dimensions(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path / "project")
    descriptor = _descriptor()

    first = _key(context, descriptor=descriptor)
    second = _key(context, descriptor=descriptor)

    assert first.payload() == second.payload()
    assert first.digest == second.digest
    assert first.digest.startswith("sha256:")
    assert first.project_root_digest == canonical_digest(str(context.project.root))

    config_changed = replace(
        context,
        identity=replace(context.identity, config_digest=_OTHER_DIGEST),
    )
    toolchain_changed = replace(
        context,
        identity=replace(context.identity, toolchain_digest=_OTHER_DIGEST),
    )
    release = _descriptor(variant=BuildVariant.RELEASE)

    assert _key(config_changed, descriptor=descriptor).digest != first.digest
    assert _key(toolchain_changed, descriptor=descriptor).digest != first.digest
    assert _key(context, descriptor=release).digest != first.digest
    assert _key(context, descriptor=descriptor, source_digest=_OTHER_DIGEST).digest != first.digest


def test_analysis_cache_key_separates_project_roots_with_identical_contents(
    tmp_path: Path,
) -> None:
    roots = (tmp_path / "project-a", tmp_path / "project-b")
    for root in roots:
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    contexts = tuple(_context(root) for root in roots)
    source_digests = tuple(project_source_digest(context.project) for context in contexts)
    keys = tuple(
        _key(context, source_digest=digest)
        for context, digest in zip(contexts, source_digests, strict=True)
    )

    assert source_digests[0] == source_digests[1]
    assert keys[0].project_root_digest != keys[1].project_root_digest
    assert keys[0].digest != keys[1].digest

    cache = AnalysisCache(tmp_path / "cache")
    assert cache.store(keys[0], _result(), roots[0])
    assert cache.load(keys[0], roots[0]) is not None
    assert cache.load(keys[1], roots[1]) is None


def test_analysis_cache_key_tracks_compilation_database_and_parse_state(tmp_path: Path) -> None:
    context = _context(tmp_path / "project")
    descriptor = _descriptor()
    unit = CompilationUnit(
        source="src/app.cpp",
        directory="build",
        argv=("g++", "-c", "../src/app.cpp"),
        configuration=_SOURCE_DIGEST,
    )
    first_context = replace(
        context,
        compilation=CompilationContext(
            units=(unit,),
            database_path="build/compile_commands.json",
            database_digest=_SOURCE_DIGEST,
        ),
    )
    changed_database = replace(
        first_context,
        compilation=replace(first_context.compilation, database_digest=_OTHER_DIGEST),
    )
    invalid_database = replace(
        first_context,
        compilation=replace(
            first_context.compilation,
            diagnostics=(
                CompilationDiagnostic(
                    code="database-malformed",
                    message="The database is malformed.",
                    level="error",
                ),
            ),
        ),
    )

    first = _key(first_context, descriptor=descriptor)

    assert first.compilation_digest.startswith("sha256:")
    assert _key(changed_database, descriptor=descriptor).digest != first.digest
    assert _key(invalid_database, descriptor=descriptor).digest != first.digest
    assert _key(context, descriptor=descriptor).digest != first.digest


@pytest.mark.parametrize("metadata", ["origin", "generator", "unity_build", "unit_target"])
def test_analysis_cache_key_tracks_compilation_provenance_metadata(
    tmp_path: Path, metadata: str
) -> None:
    context = _context(tmp_path / "project")
    descriptor = _descriptor(name="compile_db")
    unit = CompilationUnit(
        source="src/app.cpp",
        directory=".",
        argv=("g++", "-c", "src/app.cpp"),
        output="build/app.o",
    )
    compilation = CompilationContext(
        units=(unit,),
        database_path="build/compile_commands.json",
        database_digest=_SOURCE_DIGEST,
    )
    base = replace(context, compilation=compilation)

    if metadata == "origin":
        changed_compilation = replace(compilation, origin="cmake")
    elif metadata == "generator":
        changed_compilation = replace(compilation, generator="Ninja")
    elif metadata == "unity_build":
        changed_compilation = replace(compilation, unity_build=True)
    else:
        changed_compilation = replace(
            compilation,
            units=(replace(unit, target="app"),),
        )

    changed = replace(base, compilation=changed_compilation)

    assert _key(changed, descriptor=descriptor).digest != _key(base, descriptor=descriptor).digest


@pytest.mark.parametrize(
    "target",
    ["../escape", "nested/target", r"nested\target", "line\nbreak", "x" * 513],
)
def test_compilation_unit_rejects_unsafe_target_metadata(target: str) -> None:
    with pytest.raises(ValueError, match="compilation target"):
        CompilationUnit(
            source="src/app.cpp",
            directory=".",
            argv=("g++", "-c", "src/app.cpp"),
            target=target,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("origin", "manual"),
        ("origin", None),
        ("generator", None),
        ("generator", "g" * 513),
        ("unity_build", 1),
        ("unity_build", "false"),
    ],
)
def test_compilation_context_rejects_invalid_provenance_metadata(
    field_name: str, value: object
) -> None:
    with pytest.raises(ValueError):
        CompilationContext(**{field_name: value})


def test_implementation_source_and_descriptor_identity_invalidate_keys(tmp_path: Path) -> None:
    context = _context(tmp_path / "project")
    descriptor = _descriptor()

    one_as_type = _key(context, descriptor=descriptor, implementation=_ImplementationOne)
    one_as_instance = _key(context, descriptor=descriptor, implementation=_ImplementationOne())
    two = _key(context, descriptor=descriptor, implementation=_ImplementationTwo)

    assert one_as_type.digest == one_as_instance.digest
    assert one_as_type.descriptor_digest != _key(context, descriptor=descriptor).descriptor_digest
    assert two.digest != one_as_type.digest


def test_cache_key_rejects_non_sha256_digests() -> None:
    with pytest.raises(ValueError):
        AnalysisCacheKey(
            engine_name="line",
            project_root_digest="not-a-digest",
            source_digest=_SOURCE_DIGEST,
            config_digest=_CONFIG_DIGEST,
            toolchain_digest=_TOOLCHAIN_DIGEST,
            build_variant="none",
            descriptor_digest=_SOURCE_DIGEST,
        )
