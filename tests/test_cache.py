"""Contract tests for the deterministic analysis-result cache."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import ici.core.cache as cache_module
from ici.core.cache import (
    CACHE_SCHEMA_VERSION,
    AnalysisCache,
    AnalysisCacheKey,
    build_analysis_cache_key,
    is_cacheable_result,
    project_source_digest,
)
from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    ArtifactManifest,
    ArtifactScope,
    BuildVariant,
    ProjectModel,
    canonical_digest,
)
from ici.core.findings import finding_fingerprint
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    FindingSuppression,
    SourceLocation,
    SuppressionKind,
    ToolEvidence,
)
from ici.core.pipeline import EngineDescriptor, EngineExecution

_SOURCE_DIGEST = "sha256:" + "a" * 64
_CONFIG_DIGEST = "sha256:" + "b" * 64
_TOOLCHAIN_DIGEST = "sha256:" + "c" * 64
_OTHER_DIGEST = "sha256:" + "d" * 64


class _ImplementationOne:
    """First implementation used to exercise implementation identity."""

    def execute(self) -> str:
        return "one"


class _ImplementationTwo:
    """Second implementation with different source."""

    def execute(self) -> str:
        return "two"


def _context(
    root: Path,
    *,
    config_digest: str = _CONFIG_DIGEST,
    toolchain_digest: str = _TOOLCHAIN_DIGEST,
) -> AnalysisContext:
    project = ProjectModel(
        root=root,
        name="cache-project",
        version="1.0.0",
        project_type="python",
        python_sources=("src/app.py",),
    )
    return AnalysisContext(
        project=project,
        capabilities=CapabilityInventory(),
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=config_digest,
            toolchain_digest=toolchain_digest,
        ),
    )


def _descriptor(
    *,
    name: str = "line",
    variant: BuildVariant | None = None,
) -> EngineDescriptor:
    return EngineDescriptor(
        name=name,
        factory_name=f"{name.title()}Engine",
        produces=(f"findings:{name}",),
        execution=EngineExecution.BUILD if variant is not None else EngineExecution.READ_ONLY,
        build_variant=variant,
    )


def _key(
    context: AnalysisContext,
    *,
    descriptor: EngineDescriptor | None = None,
    source_digest: str = _SOURCE_DIGEST,
    implementation: object | type[object] | None = None,
) -> AnalysisCacheKey:
    return build_analysis_cache_key(
        descriptor or _descriptor(),
        context,
        source_digest,
        implementation=implementation,
    )


def _result(
    *,
    engine_name: str = "line",
    status: EngineStatus = EngineStatus.PASS,
    evidence: EvidenceState = EvidenceState.MEASURED,
    tool_evidence: tuple[ToolEvidence, ...] = (),
    artifact_manifests: tuple[ArtifactManifest, ...] = (),
    findings: tuple[Finding, ...] = (),
    summary: str = "analysis completed",
) -> EngineResult:
    return EngineResult(
        engine_name=engine_name,
        status=status,
        summary=summary,
        score=0.9,
        max_score=1.0,
        duration=0.25,
        raw_output="analysis output",
        extra={"nested": {"stable": True}, "count": 2},
        required=True,
        evidence=evidence,
        tool_evidence=list(tool_evidence),
        findings=list(findings),
        artifact_manifests=artifact_manifests,
    )


def _entry_path(cache: AnalysisCache, key: AnalysisCacheKey) -> Path:
    return cache.entries_dir / f"{key.digest.removeprefix('sha256:')}.json"


def _roundtrip_finding() -> Finding:
    location = SourceLocation(path="src/app.py", start_line=2, end_line=2, label="check")
    rule_id = "ici.cache.roundtrip"
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        fingerprint=finding_fingerprint(rule_id, location, symbol=location.label),
        primary_location=location,
        related_locations=[],
        message="cache finding",
        explanation="cache roundtrip explanation",
        remediation="cache roundtrip remediation",
        tool_rule_id="tool-rule",
        tool_name="checker",
        tool_version="1.2",
        suppression=FindingSuppression(
            suppressed=False,
            kind=SuppressionKind.NONE,
            reason="",
        ),
        metrics={"count": FindingMetric(value=1.0, unit="items")},
        snippet="value = 1",
    )


def _snapshot_files(root: Path) -> dict[str, tuple[int, int, bytes]]:
    snapshot: dict[str, tuple[int, int, bytes]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        details = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (
            details.st_mode,
            details.st_mtime_ns,
            path.read_bytes(),
        )
    return snapshot


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


def _artifact_manifest(root: Path, identity: AnalysisIdentity) -> tuple[ArtifactManifest, Path]:
    shadow = root / "build"
    shadow.mkdir(parents=True, exist_ok=True)
    output = shadow / "app"
    output.write_bytes(b"artifact-v1")
    manifest = ArtifactManifest.create(
        project_root=root,
        shadow_root=shadow,
        variant=BuildVariant.RELEASE,
        identity=identity,
        paths=[(Path("app"), ArtifactScope.SHADOW, "binary")],
        producer="cache-test",
    )
    return manifest, output


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
