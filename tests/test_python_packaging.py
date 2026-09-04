"""Source-package and bounded wheel inspection contracts."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest

from ici.engines._python_packaging import (
    PackagingPolicy,
    PythonPackagingError,
    analyze_python_packaging,
)
from ici.engines.python_compat import PythonCompatibilityEngine


def _project(root: Path, *, target: str = "demo.cli:main") -> list[Path]:
    package = root / "src" / "demo"
    package.mkdir(parents=True)
    init = package / "__init__.py"
    cli = package / "cli.py"
    init.write_text("VALUE = 1\n", encoding="utf-8")
    cli.write_text("def main():\n    return 0\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "1.2.3"\n[project.scripts]\ndemo = "{target}"\n',
        encoding="utf-8",
    )
    return [init, cli]


def _wheel(
    root: Path,
    *,
    tag: str = "py3-none-any",
    root_is_pure: bool = True,
    include_entrypoints: bool = True,
    include_sources: bool = True,
    native: bool = False,
) -> Path:
    path = root / "dist" / f"demo-1.2.3-{tag}.whl"
    path.parent.mkdir()
    members: dict[str, bytes] = {
        "demo-1.2.3.dist-info/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: test\n"
            f"Root-Is-Purelib: {'true' if root_is_pure else 'false'}\n"
            f"Tag: {tag}\n"
        ).encode(),
        "demo-1.2.3.dist-info/METADATA": b"Name: demo\nVersion: 1.2.3\n",
    }
    if include_sources:
        members.update({"demo/__init__.py": b"VALUE = 1\n", "demo/cli.py": b"def main(): pass\n"})
    if include_entrypoints:
        members["demo-1.2.3.dist-info/entry_points.txt"] = (
            b"[console_scripts]\ndemo=demo.cli:main\n"
        )
    if native:
        members["demo/native.so"] = b"\x7fELF"
    record_name = "demo-1.2.3.dist-info/RECORD"
    rows = [[name, "", ""] for name in [*members, record_name]]
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows(rows)
    members[record_name] = stream.getvalue().encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def test_source_and_pure_wheel_contract_passes(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    _wheel(tmp_path)

    result = analyze_python_packaging(
        tmp_path,
        sources,
        PackagingPolicy(wheel_globs=("dist/*.whl",), wheel_policy="pure"),
    )

    assert result.failures == 0
    assert result.warnings == 0
    assert result.targets == ()
    assert result.metadata["checked"] == 1
    assert result.metadata["pure"] is True
    assert result.metadata["members"] == 6


def test_missing_entrypoint_is_native_exact_finding(tmp_path: Path) -> None:
    sources = _project(tmp_path, target="demo.cli:missing")

    result = analyze_python_packaging(tmp_path, sources, PackagingPolicy())

    assert result.failures == 1
    finding = result.findings[0]
    assert finding.rule_id == "ici.package.entrypoint-missing"
    assert finding.tool_rule_id == "entrypoint-target"
    assert finding.primary_location.path == "pyproject.toml"
    assert finding.primary_location.start_line == 5
    assert result.targets[0].file_path == "pyproject.toml"


def test_missing_optional_and_required_wheel_are_distinct(tmp_path: Path) -> None:
    sources = _project(tmp_path)

    optional = analyze_python_packaging(
        tmp_path, sources, PackagingPolicy(wheel_globs=("dist/*.whl",))
    )

    assert optional.warnings == 1
    assert optional.findings[0].rule_id == "ici.package.wheel-missing"
    with pytest.raises(PythonPackagingError, match="matched no wheel"):
        analyze_python_packaging(
            tmp_path,
            sources,
            PackagingPolicy(wheel_globs=("dist/*.whl",), wheel_required=True),
        )


def test_pure_policy_rejects_native_platform_wheel(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    _wheel(tmp_path, tag="cp310-cp310-linux_x86_64", root_is_pure=False, native=True)

    result = analyze_python_packaging(
        tmp_path,
        sources,
        PackagingPolicy(wheel_globs=("dist/*.whl",), wheel_policy="pure"),
    )

    assert "ici.package.native-wheel-forbidden" in {finding.rule_id for finding in result.findings}
    assert result.metadata["pure"] is False
    assert result.metadata["wheels"][0]["native_members"] == ["demo/native.so"]


def test_wheel_missing_source_and_entrypoint_files_reports_both(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    _wheel(tmp_path, include_entrypoints=False, include_sources=False)

    result = analyze_python_packaging(
        tmp_path, sources, PackagingPolicy(wheel_globs=("dist/*.whl",))
    )

    assert {finding.rule_id for finding in result.findings} == {
        "ici.package.package-files-missing",
        "ici.package.wheel-entrypoints-missing",
    }


def test_wheel_member_and_uncompressed_bounds_fail_closed(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    _wheel(tmp_path)

    with pytest.raises(PythonPackagingError, match="member count"):
        analyze_python_packaging(
            tmp_path,
            sources,
            PackagingPolicy(wheel_globs=("dist/*.whl",), max_wheel_members=1),
        )
    with pytest.raises(PythonPackagingError, match="uncompressed size"):
        analyze_python_packaging(
            tmp_path,
            sources,
            PackagingPolicy(wheel_globs=("dist/*.whl",), max_wheel_uncompressed_bytes=1),
        )


def test_unsafe_wheel_glob_and_oversized_metadata_are_rejected(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    with pytest.raises(PythonPackagingError, match="contained"):
        analyze_python_packaging(tmp_path, sources, PackagingPolicy(wheel_globs=("../*.whl",)))

    path = _wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("other-1.dist-info/WHEEL", b"x" * (1024 * 1024 + 1))
    with pytest.raises(PythonPackagingError, match="exactly one WHEEL"):
        analyze_python_packaging(tmp_path, sources, PackagingPolicy(wheel_globs=("dist/*.whl",)))


def test_distribution_import_mismatch_is_warning_not_failure(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        text.replace('name = "demo"', 'name = "different-name"'), encoding="utf-8"
    )

    result = analyze_python_packaging(tmp_path, sources, PackagingPolicy())

    assert result.failures == 0
    assert result.warnings == 1
    assert result.findings[0].rule_id == "ici.package.import-distribution-mismatch"


def test_python_compat_engine_exposes_native_package_findings(tmp_path: Path) -> None:
    _project(tmp_path, target="demo.cli:missing")
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {
            "python_compat": {
                "enabled": True,
                "mode": "pass_warn_fail",
                "required": True,
                "imports": [],
                "interpreters": [],
                "required_interpreters": [],
                "target_version": "3.10",
                "wheel_globs": [],
                "wheel_policy": "allow-native",
            }
        },
    }

    result = PythonCompatibilityEngine(tmp_path, config).run()

    assert result.status.value == "FAIL"
    assert [finding.rule_id for finding in result.findings] == ["ici.package.entrypoint-missing"]
    assert result.extra["wheel"]["state"] == "MEASURED"
