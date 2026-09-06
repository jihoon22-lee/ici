"""Typed records for build outputs no linker produces.

The build adapters find artifacts by reading their shadow tree for linked
binaries. A Python wheel or an analysis report the project emits is neither
linked nor in that tree, so nothing discovered it — and `_artifact_kind`, whose
fallback is `executable`, would have labelled a `.whl` an executable if one had
ever reached it. `[build.artifacts]` declares those outputs with their real kind
instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.config_schema import ConfigError, validate_config
from ici.core.capabilities import CapabilityInventory
from ici.core.context import ArtifactScope, create_analysis_context
from ici.core.models import EngineStatus
from ici.engines.build import BuildEngine


def _project(root: Path) -> dict:
    source = root / "src"
    source.mkdir(exist_ok=True)
    (source / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "declared"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return {
        "project": {"source_dirs": ["src"], "name": "declared", "version": "0.1.0"},
        "type": "python",
    }


def _run(root: Path, artifacts: dict) -> tuple:
    config = _project(root)
    config["engines"] = {"build": {"enabled": True, "required": True}}
    config["build"] = {"artifacts": artifacts}
    context = create_analysis_context(root, config, CapabilityInventory())
    engine = BuildEngine(root, config, analysis_context=context)
    result = engine.run()
    records = [record for manifest in result.artifact_manifests for record in manifest.artifacts]
    return result, records


def test_declared_wheel_and_report_keep_their_own_kinds(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "declared-0.1.0-py3-none-any.whl").write_bytes(b"PK\x03\x04wheel-bytes")
    reports = tmp_path / "build"
    reports.mkdir()
    (reports / "analysis.json").write_text('{"schema": "example/v1"}\n', encoding="utf-8")

    result, records = _run(
        tmp_path,
        {"python_wheel": ["dist/*.whl"], "report": ["build/analysis.json"]},
    )

    assert result.status is EngineStatus.PASS
    declared = {record.path: record for record in records if record.producer == "config.artifacts"}
    assert declared["dist/declared-0.1.0-py3-none-any.whl"].kind == "python-wheel"
    assert declared["build/analysis.json"].kind == "report"
    # A wheel labelled `executable` is exactly the mislabelling this replaces.
    assert all(record.kind != "executable" for record in declared.values())
    for record in declared.values():
        assert record.scope is ArtifactScope.PROJECT
        assert record.sha256.startswith("sha256:")
        assert record.size > 0
        assert record.artifact_id and record.target


def test_a_glob_that_matches_nothing_is_an_error_not_an_absence(tmp_path: Path):
    """A build that was supposed to publish a wheel and did not is a fact.

    Skipping the unmatched glob would leave a consumer to infer the absence from
    a manifest that simply says nothing.
    """

    result, _records = _run(tmp_path, {"python_wheel": ["dist/*.whl"]})

    assert result.status is EngineStatus.ERROR
    assert "matched nothing" in result.summary


def test_declared_artifacts_cannot_escape_the_project(tmp_path: Path):
    outside = tmp_path.parent / "outside-declared.whl"
    outside.write_bytes(b"PK\x03\x04outside")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "link.whl").symlink_to(outside)

    result, records = _run(tmp_path, {"python_wheel": ["dist/*.whl"]})

    assert result.status is EngineStatus.ERROR
    assert not [record for record in records if record.producer == "config.artifacts"]


@pytest.mark.parametrize(
    "artifacts",
    [
        {"python_wheel": ["../outside/*.whl"]},
        {"python_wheel": ["/abs/*.whl"]},
        {"python_wheel": ["dist/a.whl", "dist/a.whl"]},
        {"binary": ["dist/*.whl"]},
        {"python_wheel": "dist/*.whl"},
    ],
)
def test_unsafe_or_unknown_artifact_declarations_are_config_errors(artifacts: dict):
    config = {
        "project": {"source_dirs": ["src"]},
        "build": {"artifacts": artifacts},
    }

    with pytest.raises(ConfigError):
        validate_config(config)


def test_binary_compat_ignores_declared_non_binary_kinds(tmp_path: Path):
    """readelf must never be pointed at a wheel or a JSON report.

    binary_compat selects by kind, so a typed record is what keeps a non-ELF
    output out of the ELF path without an allow-list of file extensions.
    """

    from ici.engines.binary_compat import _BINARY_KINDS

    assert "python-wheel" not in _BINARY_KINDS
    assert "report" not in _BINARY_KINDS
    assert set(_BINARY_KINDS) == {"executable", "shared-library"}
