"""Contract tests for the immutable, root-bounded artifact manifest.

The manifest is deliberately tested at its filesystem boundary.  An artifact
record is useful to later engines only when its digest and metadata describe a
regular file that is still inside the root it claims to belong to.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ici.core import context as context_module
from ici.core.context import (
    AnalysisIdentity,
    ArtifactManifest,
    ArtifactRecord,
    ArtifactScope,
    BuildVariant,
)

_IDENTITY = {
    "source_commit": "a" * 40,
    "config_digest": "sha256:" + "b" * 64,
    "toolchain_digest": "sha256:" + "c" * 64,
}
_ANALYSIS_IDENTITY = AnalysisIdentity(**_IDENTITY)


def _paths(
    *entries: tuple[str | Path, ArtifactScope, str],
) -> list[tuple[Path, ArtifactScope, str]]:
    return [(Path(path), scope, kind) for path, scope, kind in entries]


def _create(
    project_root: Path,
    shadow_root: Path | None,
    paths: list[tuple[Path, ArtifactScope, str]],
    *,
    variant: BuildVariant = BuildVariant.RELEASE,
) -> ArtifactManifest:
    return ArtifactManifest.create(
        project_root,
        shadow_root,
        variant,
        _ANALYSIS_IDENTITY,
        paths,
        "pytest",
    )


def _record(
    path: str,
    *,
    scope: ArtifactScope = ArtifactScope.PROJECT,
    kind: str = "binary",
    sha256: str = "sha256:" + "a" * 64,
    size: int = 1,
    mode: int = 0o644,
    producer: str = "pytest",
    artifact_id: str = "",
    target: str = "",
    command: tuple[str, ...] = (),
) -> ArtifactRecord:
    return ArtifactRecord(
        path,
        scope,
        kind,
        sha256,
        size,
        mode,
        producer,
        artifact_id,
        target,
        command,
    )


def _manifest(
    project_root: Path,
    shadow_root: Path | None,
    artifacts: tuple[ArtifactRecord, ...],
) -> ArtifactManifest:
    return ArtifactManifest(
        project_root,
        shadow_root,
        BuildVariant.RELEASE,
        _IDENTITY["source_commit"],
        _IDENTITY["config_digest"],
        _IDENTITY["toolchain_digest"],
        artifacts,
    )


def test_create_computes_metadata_and_orders_records_deterministically(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()

    first = project_root / "z.bin"
    first.write_bytes(b"last")
    second = project_root / "a.bin"
    second.write_bytes(b"first artifact")
    second.chmod(0o640)

    manifest = _create(
        project_root,
        shadow_root,
        _paths(
            ("z.bin", ArtifactScope.PROJECT, "binary"),
            ("a.bin", ArtifactScope.PROJECT, "binary"),
        ),
    )

    assert manifest.validate() is manifest
    assert [str(record.path) for record in manifest.artifacts] == ["a.bin", "z.bin"]
    a_record = manifest.artifacts[0]
    assert a_record.sha256 == "sha256:" + hashlib.sha256(second.read_bytes()).hexdigest()
    assert a_record.size == len(b"first artifact")
    assert a_record.mode == stat.S_IMODE(second.stat().st_mode)
    assert a_record.scope is ArtifactScope.PROJECT
    assert a_record.producer == "pytest"
    assert manifest.source_commit == _IDENTITY["source_commit"]
    assert manifest.config_digest == _IDENTITY["config_digest"]
    assert manifest.toolchain_digest == _IDENTITY["toolchain_digest"]

    reversed_manifest = _create(
        project_root,
        shadow_root,
        _paths(
            ("a.bin", ArtifactScope.PROJECT, "binary"),
            ("z.bin", ArtifactScope.PROJECT, "binary"),
        ),
    )
    assert manifest == reversed_manifest


@pytest.mark.parametrize(
    "path",
    [
        "../outside.bin",
        "nested/../../outside.bin",
        "nested/../inside.bin",
    ],
)
def test_create_rejects_parent_segments_even_when_they_normalize_inside(tmp_path: Path, path: str):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "inside.bin").write_bytes(b"inside")

    with pytest.raises(ValueError, match=r"(relative|parent|\.\.)"):
        _create(project_root, shadow_root, _paths((path, ArtifactScope.PROJECT, "binary")))


@pytest.mark.parametrize("scope", [ArtifactScope.PROJECT, ArtifactScope.SHADOW])
def test_create_rejects_absolute_paths(tmp_path: Path, scope: ArtifactScope):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    root = project_root if scope is ArtifactScope.PROJECT else shadow_root
    absolute_file = root / "absolute.bin"
    absolute_file.write_bytes(b"absolute")

    with pytest.raises(ValueError, match=r"(relative|absolute|path)"):
        _create(project_root, shadow_root, _paths((str(absolute_file), scope, "binary")))


def test_create_rejects_missing_and_nonregular_artifacts(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "directory").mkdir()

    with pytest.raises(ValueError, match=r"(missing|not found|regular|file)"):
        _create(
            project_root,
            shadow_root,
            _paths(("missing.bin", ArtifactScope.PROJECT, "binary")),
        )

    with pytest.raises(ValueError, match=r"(regular|directory|file)"):
        _create(
            project_root,
            shadow_root,
            _paths(("directory", ArtifactScope.PROJECT, "binary")),
        )


def test_project_artifacts_do_not_require_a_shadow_root(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "app").write_bytes(b"app")

    manifest = _create(
        project_root,
        None,
        _paths(("app", ArtifactScope.PROJECT, "executable")),
    )

    assert manifest.shadow_root is None
    assert manifest.validate() is manifest


def test_shadow_artifact_requires_shadow_root_and_is_contained_there(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "app").write_bytes(b"app")

    with pytest.raises(ValueError, match="shadow"):
        _create(
            project_root,
            None,
            _paths(("app", ArtifactScope.SHADOW, "executable")),
        )

    shadow_root = project_root / "shadow"
    shadow_root.mkdir()
    (shadow_root / "app").write_bytes(b"shadow app")
    manifest = _create(
        project_root,
        shadow_root,
        _paths(("app", ArtifactScope.SHADOW, "executable")),
    )
    assert manifest.artifacts[0].scope is ArtifactScope.SHADOW
    assert manifest.validate() is manifest


@pytest.mark.parametrize("scope", [ArtifactScope.PROJECT, ArtifactScope.SHADOW])
def test_symlink_escape_is_rejected(tmp_path: Path, scope: ArtifactScope):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    declared_root = project_root if scope is ArtifactScope.PROJECT else shadow_root
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (declared_root / "escape.bin").symlink_to(outside)

    with pytest.raises(ValueError, match=r"(outside|symlink|root|contain)"):
        _create(
            project_root,
            shadow_root,
            _paths(("escape.bin", scope, "binary")),
        )


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
@pytest.mark.parametrize("scope", [ArtifactScope.PROJECT, ArtifactScope.SHADOW])
def test_internal_symlink_records_resolved_canonical_relative_path(
    tmp_path: Path, scope: ArtifactScope
):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    declared_root = project_root if scope is ArtifactScope.PROJECT else shadow_root
    (declared_root / "real.bin").write_bytes(b"same root")
    (declared_root / "alias.bin").symlink_to(declared_root / "real.bin")

    manifest = _create(
        project_root,
        shadow_root,
        _paths(("alias.bin", scope, "binary")),
    )

    record = manifest.artifacts[0]
    assert str(record.path) == "real.bin"
    assert record.sha256 == "sha256:" + hashlib.sha256(b"same root").hexdigest()
    assert manifest.validate() is manifest


def test_internal_symlink_to_directory_is_not_a_regular_artifact(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "directory").mkdir()
    (project_root / "alias").symlink_to(project_root / "directory", target_is_directory=True)

    with pytest.raises(ValueError, match=r"(regular|directory|file)"):
        _create(
            project_root,
            shadow_root,
            _paths(("alias", ArtifactScope.PROJECT, "directory")),
        )


def test_duplicate_logical_records_are_rejected(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "app").write_bytes(b"app")

    with pytest.raises(ValueError, match="duplicate"):
        _create(
            project_root,
            shadow_root,
            _paths(
                ("app", ArtifactScope.PROJECT, "binary"),
                ("app", ArtifactScope.PROJECT, "report"),
            ),
        )


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_symlink_aliases_are_duplicate_after_canonicalization(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "real.bin").write_bytes(b"same")
    (project_root / "alias.bin").symlink_to(project_root / "real.bin")

    with pytest.raises(ValueError, match="duplicate"):
        _create(
            project_root,
            shadow_root,
            _paths(
                ("real.bin", ArtifactScope.PROJECT, "binary"),
                ("alias.bin", ArtifactScope.PROJECT, "binary"),
            ),
        )


def test_manifest_and_records_are_immutable(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "app").write_bytes(b"app")
    manifest = _create(
        project_root,
        shadow_root,
        _paths(("app", ArtifactScope.PROJECT, "binary")),
    )

    assert isinstance(manifest.artifacts, tuple)
    with pytest.raises(FrozenInstanceError):
        manifest.variant = "coverage"
    with pytest.raises(FrozenInstanceError):
        manifest.artifacts[0].path = "other"
    with pytest.raises(FrozenInstanceError):
        manifest.artifacts += (manifest.artifacts[0],)


def test_legacy_artifact_record_positionals_keep_empty_typed_metadata_defaults():
    record = _record("app")

    assert record.artifact_id == ""
    assert record.target == ""
    assert record.command == ()


def test_artifact_record_normalizes_command_and_rejects_untyped_metadata():
    record = ArtifactRecord(
        "app",
        ArtifactScope.PROJECT,
        "binary",
        "sha256:" + "a" * 64,
        1,
        0o644,
        "pytest",
        target="app",
        command=["cmake", "--build"],
    )

    assert record.command == ("cmake", "--build")
    with pytest.raises(ValueError, match="command"):
        ArtifactRecord(
            "app",
            ArtifactScope.PROJECT,
            "binary",
            "sha256:" + "a" * 64,
            1,
            0o644,
            "pytest",
            command="cmake",
        )
    with pytest.raises(ValueError, match="target"):
        ArtifactRecord(
            "app",
            ArtifactScope.PROJECT,
            "binary",
            "sha256:" + "a" * 64,
            1,
            0o644,
            "pytest",
            target=1,
        )


def test_validate_rejects_a_manually_constructed_duplicate_manifest(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "app").write_bytes(b"app")
    valid = _create(
        project_root,
        shadow_root,
        _paths(("app", ArtifactScope.PROJECT, "binary")),
    )
    record = valid.artifacts[0]
    duplicate = ArtifactRecord(
        record.path,
        record.scope,
        "report",
        record.sha256,
        record.size,
        record.mode,
        record.producer,
    )
    manifest = _manifest(
        project_root,
        shadow_root,
        (record, duplicate),
    )

    with pytest.raises(ValueError, match="duplicate"):
        manifest.validate()


def test_validate_rejects_duplicate_nonempty_artifact_ids(tmp_path: Path):
    project_root = tmp_path / "project"
    shadow_root = project_root / "shadow"
    project_root.mkdir()
    shadow_root.mkdir()
    (project_root / "app").write_bytes(b"app")
    (project_root / "other").write_bytes(b"other")
    valid = _create(
        project_root,
        shadow_root,
        _paths(
            ("app", ArtifactScope.PROJECT, "binary"),
            ("other", ArtifactScope.PROJECT, "binary"),
        ),
    )
    record, second_record = valid.artifacts
    duplicate = ArtifactRecord(
        second_record.path,
        second_record.scope,
        "binary-copy",
        second_record.sha256,
        second_record.size,
        second_record.mode,
        second_record.producer,
        artifact_id="release:project:app",
    )
    first = ArtifactRecord(
        record.path,
        record.scope,
        record.kind,
        record.sha256,
        record.size,
        record.mode,
        record.producer,
        artifact_id="release:project:app",
    )
    manifest = _manifest(project_root, shadow_root, (first, duplicate))

    with pytest.raises(ValueError, match="duplicate artifact id"):
        manifest.validate()


def test_create_rejects_manifest_record_count_over_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "one").write_bytes(b"1")
    (project_root / "two").write_bytes(b"2")
    monkeypatch.setattr(context_module, "MAX_ARTIFACT_MANIFEST_RECORDS", 1)

    with pytest.raises(ValueError, match="record limit"):
        _create(
            project_root,
            None,
            _paths(
                ("one", ArtifactScope.PROJECT, "binary"),
                ("two", ArtifactScope.PROJECT, "binary"),
            ),
        )


def test_create_rejects_per_file_and_aggregate_byte_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "one").write_bytes(b"1234")
    (project_root / "two").write_bytes(b"5678")

    monkeypatch.setattr(context_module, "MAX_ARTIFACT_FILE_BYTES", 3)
    with pytest.raises(ValueError, match="per-file byte limit"):
        _create(project_root, None, _paths(("one", ArtifactScope.PROJECT, "binary")))

    monkeypatch.setattr(context_module, "MAX_ARTIFACT_FILE_BYTES", 4)
    monkeypatch.setattr(context_module, "MAX_ARTIFACT_TOTAL_BYTES", 7)
    with pytest.raises(ValueError, match="aggregate byte limit"):
        _create(
            project_root,
            None,
            _paths(
                ("one", ArtifactScope.PROJECT, "binary"),
                ("two", ArtifactScope.PROJECT, "binary"),
            ),
        )
