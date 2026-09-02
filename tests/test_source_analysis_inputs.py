"""Regression tests for bounded source intake and heuristic source engines."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest

from ici.config import DEFAULT_CONFIG
from ici.config_schema import ConfigError, validate_config
from ici.core.models import EngineStatus, EvidenceState
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources
from ici.engines.dead import DeadCodeEngine
from ici.engines.dup import DuplicateEngine


def _write(root: Path, relative: str, content: str | bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _inventory_paths(root: Path) -> list[Path]:
    return [path.relative_to(root) for path in sorted(root.rglob("*")) if path.is_file()]


@pytest.mark.parametrize(
    ("limit_name", "limit", "expected_code", "expected_file"),
    [
        ("max_file_bytes", 3, "too-large", "src/large.py"),
        ("max_total_bytes", 5, "inventory-too-large", "src/second.py"),
        ("max_files", 1, "too-many-files", "."),
    ],
)
def test_source_intake_enforces_injectable_bounds(
    tmp_path: Path,
    limit_name: str,
    limit: int,
    expected_code: str,
    expected_file: str,
) -> None:
    """Small test limits exercise each fail-closed resource boundary."""

    _write(tmp_path, "src/large.py", "abcd")
    _write(tmp_path, "src/second.py", "def second():\n    return 2\n")
    paths = [Path("src/large.py"), Path("src/second.py")]

    with pytest.raises(AnalysisSourceError) as raised:
        read_analysis_sources(tmp_path, paths, **{limit_name: limit})

    assert raised.value.code == expected_code
    assert raised.value.file_path == expected_file


def test_source_intake_rejects_invalid_utf8_without_replacement(tmp_path: Path) -> None:
    _write(tmp_path, "src/broken.py", b"def broken():\n    return \xff\n")

    with pytest.raises(AnalysisSourceError) as raised:
        read_analysis_sources(tmp_path, [Path("src/broken.py")])

    assert raised.value.code == "invalid-utf8"
    assert raised.value.file_path == "src/broken.py"


def test_source_intake_rejects_an_escaped_symlink(tmp_path: Path) -> None:
    """The source snapshot must not follow a project-relative link outside root."""

    if os.name == "nt":
        pytest.skip("creating symlinks requires platform-specific privileges on Windows")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    link = _write(tmp_path, "src/link.py", "")
    link.unlink()
    link.symlink_to(outside)

    with pytest.raises(AnalysisSourceError) as raised:
        read_analysis_sources(tmp_path, [Path("src/link.py")])

    assert raised.value.code == "unreadable"
    assert raised.value.file_path == "src/link.py"


def test_source_intake_rejects_parent_traversal_before_opening(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")

    with pytest.raises(AnalysisSourceError) as raised:
        read_analysis_sources(tmp_path, [Path("..") / outside.name])

    assert raised.value.code == "outside-project"
    assert raised.value.file_path == "."


def test_source_intake_deduplicates_relative_and_absolute_paths(tmp_path: Path) -> None:
    path = _write(tmp_path, "src/clean.py", "value = 1\n")

    inventory = read_analysis_sources(
        tmp_path,
        [Path("src/clean.py"), path, Path("src/./clean.py"), Path("src/clean.py")],
    )

    assert [source.file_path for source in inventory.sources] == ["src/clean.py"]
    assert inventory.total_bytes == path.stat().st_size


def test_source_intake_excludes_generated_moc_and_vendor_by_default(tmp_path: Path) -> None:
    files = {
        "src/owned.py": "value = 1\n",
        "src/generated/generated.py": "value = 2\n",
        "src/autogen/widget.cpp": "int widget() { return 2; }\n",
        "src/moc_widget.cpp": "int moc_widget() { return 3; }\n",
        "src/qrc_resources.cpp": "int qrc_resources() { return 4; }\n",
        "src/ui_main.hpp": "struct Main {};\n",
        "src/mocs_compilation.cpp": "int mocs() { return 5; }\n",
        "src/widget.moc": "int widget_moc() { return 6; }\n",
        "src/vendor/vendor.py": "value = 7\n",
        "src/third_party/lib.cpp": "int lib() { return 8; }\n",
    }
    for relative, content in files.items():
        _write(tmp_path, relative, content)
    paths = _inventory_paths(tmp_path)

    inventory = read_analysis_sources(tmp_path, paths)

    assert [source.file_path for source in inventory.sources] == ["src/owned.py"]
    assert [item.file_path for item in inventory.excluded] == sorted(
        relative for relative in files if relative != "src/owned.py"
    )
    assert inventory.exclusion_counts == {"generated": 7, "vendor": 2}


def test_source_intake_opt_ins_restore_generated_and_vendor_sources(tmp_path: Path) -> None:
    files = {
        "src/owned.py": "value = 1\n",
        "src/generated/generated.py": "value = 2\n",
        "src/moc_widget.cpp": "int moc_widget() { return 3; }\n",
        "src/vendor/vendor.py": "value = 4\n",
    }
    for relative, content in files.items():
        _write(tmp_path, relative, content)
    paths = _inventory_paths(tmp_path)

    generated_only = read_analysis_sources(tmp_path, paths, include_generated=True)
    vendor_only = read_analysis_sources(tmp_path, paths, include_vendor=True)
    all_owned = read_analysis_sources(
        tmp_path,
        paths,
        include_generated=True,
        include_vendor=True,
    )

    assert [source.file_path for source in generated_only.sources] == [
        "src/generated/generated.py",
        "src/moc_widget.cpp",
        "src/owned.py",
    ]
    assert generated_only.exclusion_counts == {"vendor": 1}
    assert [source.file_path for source in vendor_only.sources] == [
        "src/owned.py",
        "src/vendor/vendor.py",
    ]
    assert vendor_only.exclusion_counts == {"generated": 2}
    assert [source.file_path for source in all_owned.sources] == sorted(files)
    assert all_owned.excluded == ()


def test_dead_engine_reports_estimated_evidence_and_clean_location(tmp_path: Path) -> None:
    _write(tmp_path, "src/clean.py", "def public():\n    return 1\n")

    result = DeadCodeEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["source_files_analyzed"] == 1
    assert any(
        target.file_path == "src/clean.py"
        and target.start_line == 1
        and target.status == EngineStatus.PASS
        for target in result.targets
    )


def test_duplicate_engine_reports_estimated_evidence_and_clean_locations(tmp_path: Path) -> None:
    _write(tmp_path, "src/clean.py", "value = 1\n")
    _write(tmp_path, "src/clean.cpp", "int value() { return 1; }\n")

    result = DuplicateEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["source_files_analyzed"] == 2
    assert {target.file_path for target in result.targets} == {
        "src/clean.cpp",
        "src/clean.py",
    }
    assert all(target.status == EngineStatus.PASS for target in result.targets)
    assert all(target.start_line == 1 for target in result.targets)


@pytest.mark.parametrize("engine_type", [DeadCodeEngine, DuplicateEngine])
@pytest.mark.parametrize(
    ("relative", "content", "expected_code"),
    [
        ("src/missing.py", None, "missing"),
        ("src/invalid.py", b"value = \xff\n", "invalid-utf8"),
    ],
)
def test_engine_source_read_errors_are_error_not_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_type: type[DeadCodeEngine] | type[DuplicateEngine],
    relative: str,
    content: str | bytes | None,
    expected_code: str,
) -> None:
    path = tmp_path / relative
    if content is not None:
        _write(tmp_path, relative, content)
    engine = engine_type(tmp_path)
    monkeypatch.setattr(engine, "project_python_sources", lambda: [path])

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    target = next(target for target in result.targets if target.target_name == "SourceInputError")
    assert target.status == EngineStatus.ERROR
    assert target.file_path == relative
    assert f"{expected_code}:" in target.message


@pytest.mark.parametrize("include_generated", [False, True])
@pytest.mark.parametrize("include_vendor", [False, True])
def test_duplicate_engine_applies_generated_vendor_opt_ins(
    tmp_path: Path, include_generated: bool, include_vendor: bool
) -> None:
    _write(tmp_path, "src/owned.py", "value = 1\n")
    _write(tmp_path, "src/generated/generated.cpp", "int generated() { return 2; }\n")
    _write(tmp_path, "src/vendor/vendor.py", "value = 3\n")
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {
            "dup": {
                "include_generated": include_generated,
                "include_vendor": include_vendor,
            }
        },
    }

    result = DuplicateEngine(tmp_path, config).run()

    expected = 1 + int(include_generated) + int(include_vendor)
    assert result.extra["source_files_analyzed"] == expected
    assert result.extra["source_files_excluded"] == 2 - int(include_generated) - int(include_vendor)


@pytest.mark.parametrize("include_generated", [False, True])
@pytest.mark.parametrize("include_vendor", [False, True])
def test_dead_engine_applies_generated_vendor_opt_ins(
    tmp_path: Path, include_generated: bool, include_vendor: bool
) -> None:
    _write(tmp_path, "src/owned.py", "value = 1\n")
    _write(tmp_path, "src/generated/generated.py", "value = 2\n")
    _write(tmp_path, "src/vendor/vendor.py", "value = 3\n")
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {
            "dead": {
                "include_generated": include_generated,
                "include_vendor": include_vendor,
            }
        },
    }

    result = DeadCodeEngine(tmp_path, config).run()

    expected = 1 + int(include_generated) + int(include_vendor)
    assert result.extra["source_files_analyzed"] == expected
    assert result.extra["source_files_excluded"] == 2 - int(include_generated) - int(include_vendor)


@pytest.mark.parametrize("engine_name", ["dead", "dup"])
@pytest.mark.parametrize("key", ["include_generated", "include_vendor"])
@pytest.mark.parametrize("value", [True, False])
def test_config_accepts_boolean_source_inclusion_policy(
    engine_name: str, key: str, value: bool
) -> None:
    config = deepcopy(DEFAULT_CONFIG)
    config["engines"][engine_name][key] = value

    validate_config(config)


@pytest.mark.parametrize("engine_name", ["dead", "dup"])
@pytest.mark.parametrize("key", ["include_generated", "include_vendor"])
@pytest.mark.parametrize("value", ["yes", 1, None])
def test_config_rejects_non_boolean_source_inclusion_policy(
    engine_name: str, key: str, value: object
) -> None:
    config = deepcopy(DEFAULT_CONFIG)
    config["engines"][engine_name][key] = value

    with pytest.raises(ConfigError, match=rf"engines\.{engine_name}\.{key}"):
        validate_config(config)


_CLONE_A = """def alpha(value):
    if value:
        total = value + 1
        total = total + 2
        total = total + 3
        return total
    return value
"""
_CLONE_B = """def beta(amount):
    if amount:
        result = amount + 9
        result = result + 8
        result = result + 7
        return result
    return amount
"""


def _duplicate_with_python_order(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    paths: list[Path],
):
    engine = DuplicateEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {"dup": {"min_window": 6, "warn_pct": 0.0}},
        },
    )
    monkeypatch.setattr(engine, "project_python_sources", lambda: paths)
    monkeypatch.setattr(engine, "project_cpp_sources", lambda: [])
    return engine.run()


def test_duplicate_fingerprint_is_stable_across_reruns_and_input_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_path = _write(tmp_path, "src/a.py", _CLONE_A)
    second_path = _write(tmp_path, "src/b.py", _CLONE_B)

    first = _duplicate_with_python_order(tmp_path, monkeypatch, [second_path, first_path])
    second = _duplicate_with_python_order(tmp_path, monkeypatch, [first_path, second_path])

    def signature(result):
        return [
            (
                group["fingerprint"],
                group["language"],
                tuple(
                    (item["file_path"], item["start_line"], item["end_line"])
                    for item in group["occurrences"]
                ),
            )
            for group in result.extra["clone_groups"]
        ]

    assert first.evidence == EvidenceState.ESTIMATED
    assert second.evidence == EvidenceState.ESTIMATED
    assert signature(first) == signature(second)
    assert first.extra["clone_groups_count"] == 1


def test_duplicate_does_not_match_python_and_cpp_token_shapes(tmp_path: Path) -> None:
    shared_lines = """value += 1
value -= 2
value *= 3
value /= 4
    value = value + 5
return value
"""
    _write(
        tmp_path,
        "src/example.py",
        f"def example(value):\n{''.join('    ' + line for line in shared_lines.splitlines(keepends=True))}",
    )
    _write(tmp_path, "src/example.cpp", shared_lines)
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {"dup": {"min_window": 6}},
    }

    result = DuplicateEngine(tmp_path, config).run()

    assert result.extra["clone_groups_count"] == 0
    assert {target.file_path for target in result.targets} == {
        "src/example.cpp",
        "src/example.py",
    }
    assert all(target.status == EngineStatus.PASS for target in result.targets)
