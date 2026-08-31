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
from ici.core.context import BuildVariant, canonical_digest


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

    first_project = _context(first_root).project
    second_project = _context(second_root).project
    first_digest = project_source_digest(first_project)

    assert first_digest == project_source_digest(first_project)
    assert first_digest == project_source_digest(second_project)

    (first_root / "notes.txt").write_text("documentation changed\n", encoding="utf-8")
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
