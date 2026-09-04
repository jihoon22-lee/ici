"""Source-package and bounded wheel inspection contracts."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import stat
import zipfile
from pathlib import Path

import pytest

from ici.config import DEFAULT_CONFIG
from ici.config_schema import ConfigError, validate_config
from ici.core.models import EngineStatus
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
    valid_record: bool = True,
    wheel_entrypoint: str = "demo.cli:main",
    metadata_name: str = "demo",
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
        "demo-1.2.3.dist-info/METADATA": (f"Name: {metadata_name}\nVersion: 1.2.3\n".encode()),
    }
    if include_sources:
        members.update({"demo/__init__.py": b"VALUE = 1\n", "demo/cli.py": b"def main(): pass\n"})
    if include_entrypoints:
        members["demo-1.2.3.dist-info/entry_points.txt"] = (
            f"[console_scripts]\ndemo={wheel_entrypoint}\n".encode()
        )
    if native:
        members["demo/native.so"] = b"\x7fELF"
    record_name = "demo-1.2.3.dist-info/RECORD"
    rows = [
        [
            name,
            (
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                .rstrip(b"=")
                .decode("ascii")
                if valid_record
                else ""
            ),
            str(len(payload)) if valid_record else "",
        ]
        for name, payload in members.items()
    ]
    rows.append([record_name, "", ""])
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
    assert [(target.file_path, target.status.value) for target in result.targets] == [
        ("pyproject.toml", "PASS"),
        ("dist/demo-1.2.3-py3-none-any.whl", "PASS"),
    ]
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


def test_entrypoint_constant_is_rejected_but_imported_symbol_is_accepted(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    sources[1].write_text("main = 3\n", encoding="utf-8")
    rejected = analyze_python_packaging(tmp_path, sources, PackagingPolicy())
    assert [finding.rule_id for finding in rejected.findings] == ["ici.package.entrypoint-missing"]

    sources[0].write_text("from .cli import main\n", encoding="utf-8")
    sources[1].write_text("def main():\n    return 0\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace("demo.cli:main", "demo:main"),
        encoding="utf-8",
    )
    accepted = analyze_python_packaging(tmp_path, sources, PackagingPolicy())
    assert accepted.findings == ()


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


def test_wheel_record_hashes_and_declared_entrypoints_are_verified(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    _wheel(tmp_path, valid_record=False, wheel_entrypoint="demo.cli:other")

    result = analyze_python_packaging(
        tmp_path, sources, PackagingPolicy(wheel_globs=("dist/*.whl",))
    )

    assert {finding.rule_id for finding in result.findings} == {
        "ici.package.wheel-record-integrity",
        "ici.package.wheel-entrypoints-mismatch",
    }
    assert result.targets[1].file_path.endswith(".whl")
    assert result.targets[1].status.value == "FAIL"


def test_wheel_missing_metadata_identity_is_not_accepted(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    _wheel(tmp_path, metadata_name="")

    result = analyze_python_packaging(
        tmp_path, sources, PackagingPolicy(wheel_globs=("dist/*.whl",))
    )

    assert "ici.package.wheel-metadata-mismatch" in {finding.rule_id for finding in result.findings}


def test_wheel_symlink_member_is_rejected(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    path = _wheel(tmp_path)
    link = zipfile.ZipInfo("demo/link.py")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(link, "cli.py")

    result = analyze_python_packaging(
        tmp_path, sources, PackagingPolicy(wheel_globs=("dist/*.whl",))
    )

    assert [finding.rule_id for finding in result.findings] == ["ici.package.wheel-invalid"]
    assert result.targets[1].file_path == "dist/demo-1.2.3-py3-none-any.whl"
    assert result.targets[1].status is EngineStatus.FAIL
    assert result.metadata["attempted"] == 1
    assert result.metadata["checked"] == 0
    assert result.metadata["invalid"] == 1


def test_wheel_member_and_uncompressed_bounds_fail_closed(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    _wheel(tmp_path)

    members = analyze_python_packaging(
        tmp_path,
        sources,
        PackagingPolicy(wheel_globs=("dist/*.whl",), max_wheel_members=1),
    )
    assert members.findings[0].rule_id == "ici.package.wheel-invalid"
    assert "member count" in members.findings[0].message

    size = analyze_python_packaging(
        tmp_path,
        sources,
        PackagingPolicy(wheel_globs=("dist/*.whl",), max_wheel_uncompressed_bytes=1),
    )
    assert size.findings[0].rule_id == "ici.package.wheel-invalid"
    assert "uncompressed size" in size.findings[0].message


def test_unsafe_wheel_glob_and_oversized_metadata_are_rejected(tmp_path: Path) -> None:
    sources = _project(tmp_path)
    with pytest.raises(PythonPackagingError, match="contained"):
        analyze_python_packaging(tmp_path, sources, PackagingPolicy(wheel_globs=("../*.whl",)))

    path = _wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("other-1.dist-info/WHEEL", b"x" * (1024 * 1024 + 1))
    result = analyze_python_packaging(
        tmp_path, sources, PackagingPolicy(wheel_globs=("dist/*.whl",))
    )
    assert result.findings[0].rule_id == "ici.package.wheel-invalid"
    assert "exactly one WHEEL" in result.findings[0].message


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


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("wheel_policy", "unknown", "allow-native, pure"),
        ("wheel_globs", ["../dist/*.whl"], "contained POSIX glob"),
        ("max_wheels", 33, "less than or equal to 32"),
        ("max_wheel_members", 8193, "less than or equal to 8192"),
        (
            "max_wheel_uncompressed_bytes",
            64 * 1024 * 1024 + 1,
            "less than or equal",
        ),
    ],
)
def test_package_policy_schema_rejects_unbounded_values(
    key: str, value: object, message: str
) -> None:
    config = {
        **DEFAULT_CONFIG,
        "engines": {
            **DEFAULT_CONFIG["engines"],
            "python_compat": {
                **DEFAULT_CONFIG["engines"]["python_compat"],
                key: value,
            },
        },
    }

    with pytest.raises(ConfigError, match=message):
        validate_config(config)
