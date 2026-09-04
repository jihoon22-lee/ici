"""Tests for analysis-cache result storage, validation, and lifecycle."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import ici.core.cache as cache_module
from cache_fixtures import (
    _OTHER_DIGEST,
    _SOURCE_DIGEST,
    _artifact_manifest,
    _context,
    _descriptor,
    _entry_path,
    _key,
    _result,
    _roundtrip_finding,
    _snapshot_files,
)
from ici.core.cache import (
    CACHE_SCHEMA_VERSION,
    AnalysisCache,
    is_cacheable_result,
    project_source_digest,
)
from ici.core.context import BuildVariant
from ici.core.models import EngineResult, EngineStatus, EvidenceState, ToolEvidence


def test_store_and_load_roundtrip_preserves_result_and_marks_cache_hit(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    result = _result(
        tool_evidence=(
            ToolEvidence(
                name="checker",
                path="/usr/bin/checker",
                version="1.2",
                argv=["checker", "--check"],
                returncode=0,
            ),
        ),
        findings=(_roundtrip_finding(),),
    )
    cache = AnalysisCache(tmp_path / "cache")

    assert cache.store(key, result, root)
    assert result.cache_hit is False
    assert result.cache_key == ""

    loaded = cache.load(key, root)

    assert loaded is not None
    assert loaded.cache_hit is True
    assert loaded.cache_key == key.digest
    assert replace(loaded, cache_hit=False, cache_key="") == result


@pytest.mark.parametrize("status", [EngineStatus.WARN, EngineStatus.FAIL])
def test_warn_and_fail_results_are_cacheable(tmp_path: Path, status: EngineStatus) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    result = _result(status=status)
    cache = AnalysisCache(tmp_path / "cache")

    assert is_cacheable_result(result, key)
    assert cache.store(key, result, root)
    loaded = cache.load(key, root)
    assert loaded is not None
    assert loaded.status is status


@pytest.mark.parametrize("status", [EngineStatus.ERROR, EngineStatus.SKIP])
def test_error_and_skip_results_are_never_cached(tmp_path: Path, status: EngineStatus) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")

    result = _result(status=status)
    assert not is_cacheable_result(result, key)
    assert not cache.store(key, result, root)
    assert not cache.entries_dir.exists()


def test_not_run_results_are_never_cached(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")

    result = _result(evidence=EvidenceState.NOT_RUN)
    assert not is_cacheable_result(result, key)
    assert not cache.store(key, result, root)
    assert not cache.entries_dir.exists()


@pytest.mark.parametrize(
    "tool",
    [
        ToolEvidence(name="checker", path="/usr/bin/checker", timed_out=True),
        ToolEvidence(name="checker", path="/usr/bin/checker", truncated=True),
        ToolEvidence(name="checker", path="/usr/bin/checker", error="failed to execute"),
    ],
)
def test_timeout_truncation_and_tool_errors_are_never_cached(
    tmp_path: Path,
    tool: ToolEvidence,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")

    result = _result(tool_evidence=(tool,))
    assert not is_cacheable_result(result, key)
    assert not cache.store(key, result, root)
    assert not cache.entries_dir.exists()


def test_artifact_manifest_is_revalidated_on_store_and_load(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    descriptor = _descriptor(variant=BuildVariant.RELEASE)
    key = _key(context, descriptor=descriptor)
    manifest, output = _artifact_manifest(root, context.identity)
    result = _result(artifact_manifests=(manifest,))
    cache = AnalysisCache(tmp_path / "cache")

    assert is_cacheable_result(result, key)
    assert cache.store(key, result, root)
    loaded = cache.load(key, root)
    assert loaded is not None
    assert loaded.artifact_manifests == (manifest,)

    output.write_bytes(b"artifact-v2")
    assert cache.load(key, root) is None


def test_v2_artifact_manifest_roundtrips_producer_metadata(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    descriptor = _descriptor(variant=BuildVariant.RELEASE)
    key = _key(context, descriptor=descriptor)
    manifest, _output = _artifact_manifest(root, context.identity)
    enriched = replace(
        manifest,
        artifacts=(
            replace(
                manifest.artifacts[0],
                artifact_id="build/app",
                target="app",
                command=("cmake", "--build", "build", "--target", "app"),
            ),
        ),
    )
    cache = AnalysisCache(tmp_path / "cache")

    assert cache.store(key, _result(artifact_manifests=(enriched,)), root)
    payload = json.loads(_entry_path(cache, key).read_text(encoding="utf-8"))
    assert payload["result"]["artifact_manifests"][0]["schema_version"] == "ici.artifacts/v2"
    assert payload["result"]["artifact_manifests"][0]["artifacts"][0]["id"] == "build/app"

    loaded = cache.load(key, root)

    assert loaded is not None
    assert loaded.artifact_manifests == (enriched,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 1),
        ("target", None),
        ("command", "cmake"),
        ("command", ["cmake", 1]),
    ],
)
def test_v2_artifact_metadata_is_typed_and_bounded_on_load(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    descriptor = _descriptor(variant=BuildVariant.RELEASE)
    key = _key(context, descriptor=descriptor)
    manifest, _output = _artifact_manifest(root, context.identity)
    enriched = replace(
        manifest,
        artifacts=(replace(manifest.artifacts[0], artifact_id="build/app"),),
    )
    cache = AnalysisCache(tmp_path / "cache")
    assert cache.store(key, _result(artifact_manifests=(enriched,)), root)

    path = _entry_path(cache, key)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["result"]["artifact_manifests"][0]["artifacts"][0][field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.load(key, root) is None


def test_artifact_manifest_identity_and_variant_mismatches_are_not_cacheable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    descriptor = _descriptor(variant=BuildVariant.RELEASE)
    key = _key(context, descriptor=descriptor)
    manifest, _output = _artifact_manifest(root, context.identity)

    assert not is_cacheable_result(
        _result(artifact_manifests=(replace(manifest, variant=BuildVariant.COVERAGE),)),
        key,
    )
    assert not is_cacheable_result(
        _result(artifact_manifests=(replace(manifest, config_digest=_OTHER_DIGEST),)),
        key,
    )
    assert not is_cacheable_result(
        _result(artifact_manifests=(replace(manifest, toolchain_digest=_OTHER_DIGEST),)),
        key,
    )
    assert not is_cacheable_result(_result(artifact_manifests=(manifest,)), _key(context))


def test_load_rejects_corrupt_cache_payload(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")
    assert cache.store(key, _result(), root)
    path = _entry_path(cache, key)

    path.write_text("not valid json", encoding="utf-8")

    assert cache.load(key, root) is None


@pytest.mark.parametrize(
    "poisoned",
    [
        '{"schema_version":"first","schema_version":"second"}',
        '{"value":NaN}',
        '{"value":Infinity}',
    ],
)
def test_load_rejects_duplicate_keys_and_nonfinite_json(
    tmp_path: Path,
    poisoned: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")
    assert cache.store(key, _result(), root)
    _entry_path(cache, key).write_text(poisoned, encoding="utf-8")

    assert cache.load(key, root) is None
    assert cache.inventory().corrupt_entries == 1


def test_load_rejects_oversized_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")
    assert cache.store(key, _result(), root)
    path = _entry_path(cache, key)
    existing = path.read_bytes()
    monkeypatch.setattr(cache_module, "MAX_CACHE_ENTRY_BYTES", len(existing))
    path.write_bytes(existing + b"x")

    assert cache.load(key, root) is None


def test_load_rejects_symlinked_cache_entry(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")
    assert cache.store(key, _result(), root)
    path = _entry_path(cache, key)
    target = tmp_path / "valid-copy.json"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)

    assert path.is_symlink()
    assert cache.load(key, root) is None


def test_symlinked_entries_directory_cannot_escape_cache_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")
    cache.root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_text("keep", encoding="utf-8")
    cache.entries_dir.symlink_to(outside, target_is_directory=True)

    assert not cache.store(key, _result(), root)
    assert cache.load(key, root) is None
    assert cache.inventory().corrupt_entries == 1
    assert cache.clear() == 0
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_inventory_counts_valid_corrupt_and_oversized_entries(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")
    assert cache.store(key, _result(), root)
    valid_path = _entry_path(cache, key)
    valid_size = valid_path.stat().st_size

    corrupt_path = cache.entries_dir / "corrupt.json"
    corrupt_path.write_text("not valid json", encoding="utf-8")
    oversized_path = cache.entries_dir / "oversized.json"
    monkeypatch.setattr(cache_module, "MAX_CACHE_ENTRY_BYTES", valid_size + 1)
    oversized_path.write_bytes(b"x" * (valid_size + 2))
    symlink_path = cache.entries_dir / "symlink.json"
    symlink_path.symlink_to(valid_path)

    inventory = cache.inventory()

    assert inventory.root == cache.root
    assert inventory.entries == 1
    assert inventory.corrupt_entries == 3
    assert (
        inventory.bytes == valid_size + corrupt_path.stat().st_size + oversized_path.stat().st_size
    )


def test_clear_removes_only_cache_entry_and_temp_files(tmp_path: Path) -> None:
    cache = AnalysisCache(tmp_path / "cache")
    cache.entries_dir.mkdir(parents=True)
    entry = cache.entries_dir / "entry.json"
    temporary = cache.entries_dir / ".entry.tmp"
    keep = cache.entries_dir / "keep.txt"
    directory = cache.entries_dir / "directory.json"
    entry.write_text("{}", encoding="utf-8")
    temporary.write_text("temporary", encoding="utf-8")
    keep.write_text("keep", encoding="utf-8")
    directory.mkdir()

    assert cache.clear() == 2
    assert not entry.exists()
    assert not temporary.exists()
    assert keep.exists()
    assert directory.is_dir()
    assert cache.clear() == 0


def test_concurrent_store_and_load_are_atomic(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    context = _context(root)
    key = _key(context)
    cache = AnalysisCache(tmp_path / "cache")
    base = _result()

    def writer(index: int) -> bool:
        return cache.store(
            key,
            replace(base, summary=f"writer-{index}", duration=float(index)),
            root,
        )

    def reader(_index: int) -> list[EngineResult]:
        observed: list[EngineResult] = []
        for _ in range(20):
            loaded = cache.load(key, root)
            if loaded is not None:
                observed.append(loaded)
        return observed

    with ThreadPoolExecutor(max_workers=8) as executor:
        readers = [executor.submit(reader, index) for index in range(4)]
        writers = [executor.submit(writer, index) for index in range(8)]
        reader_results = [future.result() for future in readers]
        writer_results = [future.result() for future in writers]

    assert all(writer_results)
    allowed_summaries = {f"writer-{index}" for index in range(8)}
    for observed in reader_results:
        assert all(item.cache_hit and item.cache_key == key.digest for item in observed)
        assert all(item.summary in allowed_summaries for item in observed)
    loaded = cache.load(key, root)
    assert loaded is not None
    assert loaded.summary in allowed_summaries
    assert tuple(cache.entries_dir.glob("*.tmp")) == ()


def test_cache_operations_do_not_modify_project_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "ici.toml").write_text('name = "cache"\n', encoding="utf-8")
    project = _context(root).project
    context = _context(root)
    key = _key(context, source_digest=project_source_digest(project))
    cache = AnalysisCache(tmp_path / "cache")
    before = _snapshot_files(root)
    before_digest = project_source_digest(project)

    assert cache.store(key, _result(), root)
    assert cache.load(key, root) is not None
    assert project_source_digest(project) == before_digest
    assert _snapshot_files(root) == before


def test_inventory_validation_uses_schema_and_filename_identity(tmp_path: Path) -> None:
    cache = AnalysisCache(tmp_path / "cache")
    cache.entries_dir.mkdir(parents=True)
    path = cache.entries_dir / "entry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "key": _SOURCE_DIGEST,
                "inputs": {"key_version": "ici.analysis-cache-key/v1"},
                "result": {},
            }
        ),
        encoding="utf-8",
    )

    inventory = cache.inventory()

    assert inventory.entries == 0
    assert inventory.corrupt_entries == 1
