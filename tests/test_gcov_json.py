"""Focused contract tests for the strict gcov JSON gzip parser."""

from __future__ import annotations

import copy
import gzip
import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ici.engines.gcov_json import (
    GcovJsonError,
    parse_gcov_json,
    parse_gcov_json_document,
    parse_gcov_json_gz,
)


def _function() -> dict:
    return {
        "blocks": 2,
        "blocks_executed": 2,
        "demangled_name": "demo::run()",
        "end_column": 9,
        "end_line": 4,
        "execution_count": 3,
        "name": "_ZN4demo3runEv",
        "start_column": 1,
        "start_line": 2,
    }


def _line() -> dict:
    return {
        "block_ids": [0, 1],
        "branches": [
            {
                "count": 3,
                "destination_block_id": 2,
                "fallthrough": True,
                "source_block_id": 1,
                "throw": False,
            }
        ],
        "calls": [{"destination_block_id": 3, "returned": 3, "source_block_id": 1}],
        "conditions": [
            {
                "count": 2,
                "covered": 1,
                "not_covered_false": [1],
                "not_covered_true": [],
            }
        ],
        "count": 3,
        "line_number": 2,
        "unexecuted_block": False,
        "function_name": "demo::run()",
    }


def _document(*, version: str = "2") -> dict:
    return {
        "current_working_directory": "/build/tree",
        "data_file": "app.gcda",
        "format_version": version,
        "gcc_version": "15.2.0 20250914",
        "files": [{"file": "../src/demo.cpp", "functions": [_function()], "lines": [_line()]}],
    }


def _gzip(document: dict | str, *, compresslevel: int = 9) -> bytes:
    if isinstance(document, dict):
        document = json.dumps(document, separators=(",", ":"), allow_nan=False)
    return gzip.compress(document.encode("utf-8"), compresslevel=compresslevel)


def _error(document: object, *, code: str | None = None, **kwargs: object) -> GcovJsonError:
    with pytest.raises(GcovJsonError) as caught:
        parse_gcov_json_gz(_gzip(document), **kwargs)
    if code is not None:
        assert caught.value.code == code
    return caught.value


def test_parses_version_two_gzip_and_retains_nested_evidence(tmp_path: Path):
    artifact = tmp_path / "demo.gcov.json.gz"
    artifact.write_bytes(_gzip(_document()))

    report = parse_gcov_json_gz(artifact)

    assert report.format_version == 2
    assert report.version == 2
    assert report.gcc_version == "15.2.0 20250914"
    assert report.files[0].file == "../src/demo.cpp"
    assert report.files[0].path == "../src/demo.cpp"
    assert report.files[0].functions[0].execution_count == 3
    assert report.files[0].functions[0].start_line == 2
    assert report.files[0].functions[0].start_column == 1
    line = report.files[0].lines[0]
    assert line.count == 3
    assert line.branches[0].fallthrough is True
    assert line.branches[0].throw is False
    assert line.calls[0].returned == 3
    assert line.conditions[0].covered == 1


def test_accepts_and_bounds_gcc_15_prime_path_metadata():
    document = _document()
    function = document["files"][0]["functions"][0]
    function.update(
        total_prime_paths=3,
        covered_prime_paths=2,
        prime_path_coverage=[
            {
                "id": 0,
                "sequence": [
                    {
                        "block_id": 2,
                        "locations": [{"file": "demo.cpp", "line_numbers": [2, 3]}],
                        "edge_kind": "fallthru",
                    }
                ],
            }
        ],
    )

    report = parse_gcov_json_gz(_gzip(document))

    parsed = report.files[0].functions[0]
    assert parsed.total_prime_paths == 3
    assert parsed.covered_prime_paths == 2
    assert parsed.prime_path_coverage[0].sequence[0].locations[0].line_numbers == (2, 3)

    function["covered_prime_paths"] = 4
    _error(document, code="count_bound")


def test_rejects_partial_prime_path_metadata_and_duplicate_function_geometry():
    document = _document()
    document["files"][0]["functions"][0]["total_prime_paths"] = 0
    _error(document, code="missing_field")

    document = _document()
    document["files"][0]["functions"].append(copy.deepcopy(_function()))
    _error(document, code="duplicate_function")


def test_accepts_repeated_line_numbers_from_template_instantiations():
    document = _document()
    duplicate = copy.deepcopy(_line())
    duplicate["function_name"] = "demo::run<long>()"
    document["files"][0]["lines"].append(duplicate)

    report = parse_gcov_json_gz(_gzip(document))

    assert [line.line_number for line in report.files[0].lines] == [2, 2]


def test_accepts_missing_compilation_directory_on_empty_gcc_report():
    document = _document()
    document.pop("current_working_directory")
    document["files"] = []

    report = parse_gcov_json_gz(_gzip(document))

    assert report.current_working_directory == ""


def test_accepts_version_one_without_optional_calls():
    document = _document(version="1")
    line = document["files"][0]["lines"][0]
    del line["calls"]
    del line["block_ids"]
    del line["branches"][0]["source_block_id"]
    del line["branches"][0]["destination_block_id"]

    report = parse_gcov_json_gz(_gzip(document))

    assert report.format_version == 1
    parsed_line = report.files[0].lines[0]
    assert parsed_line.block_ids == ()
    assert parsed_line.calls == ()
    assert parsed_line.branches[0].source_block_id is None
    assert parsed_line.branches[0].destination_block_id is None


def test_version_one_rejects_calls_and_partial_branch_block_ids():
    document = _document(version="1")
    line = document["files"][0]["lines"][0]
    del line["block_ids"]
    del line["branches"][0]["destination_block_id"]
    _error(document, code="version_field")

    del line["calls"]
    _error(document, code="missing_field")


def test_version_two_requires_line_and_branch_block_ids():
    document = _document()
    del document["files"][0]["lines"][0]["block_ids"]
    _error(document, code="missing_field")

    document = _document()
    del document["files"][0]["lines"][0]["branches"][0]["source_block_id"]
    _error(document, code="missing_field")


def test_document_and_convenience_byte_apis_accept_uncompressed_json():
    payload = json.dumps(_document(), separators=(",", ":")).encode("utf-8")

    from_document = parse_gcov_json_document(payload)
    from_bytes = parse_gcov_json(payload)
    from_gzip = parse_gcov_json(_gzip(_document()))

    assert from_document == from_bytes == from_gzip


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", "3"),
        ("gcc_version", "gcc version unknown"),
        ("data_file", ""),
    ],
)
def test_rejects_invalid_root_metadata(field: str, value: object):
    document = _document()
    document[field] = value

    _error(document)


def test_accepts_empty_file_inventory_from_non_source_compiler_artifact():
    document = _document()
    document["files"] = []

    report = parse_gcov_json_gz(_gzip(document))

    assert report.files == ()


def test_rejects_missing_required_nested_structure():
    document = _document()
    del document["files"][0]["functions"][0]["start_column"]
    _error(document, code="missing_field")

    document = _document()
    del document["files"][0]["lines"][0]["count"]
    _error(document, code="missing_field")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["files"][0]["lines"][0]["count"].__class__,
        lambda d: d["files"][0]["lines"][0].update(count=True),
        lambda d: d["files"][0]["lines"][0]["branches"][0].update(throw=1),
        lambda d: d["files"][0]["functions"][0].update(blocks_executed=3),
        lambda d: d["files"][0]["functions"][0].update(start_line=5),
        lambda d: d["files"][0]["lines"][0].update(extra=1),
    ],
)
def test_rejects_wrong_types_invariants_and_unknown_fields(mutate):
    document = _document()
    result = mutate(document)
    # The first mutation above is intentionally a no-op expression; replace
    # the count with a float to exercise integer type validation.
    if result is int:
        document["files"][0]["lines"][0]["count"] = 1.5
    _error(document)


@pytest.mark.parametrize(
    "text",
    [
        '{"current_working_directory":"/build","current_working_directory":"/other"}',
        '{"files":[],"files":[]}',
    ],
)
def test_rejects_duplicate_json_keys(text: str):
    # These malformed roots fail duplicate-key detection before schema checks.
    with pytest.raises(GcovJsonError, match="duplicate_key"):
        parse_gcov_json_gz(_gzip(text))


def test_rejects_duplicate_nested_json_keys():
    text = json.dumps(_document()).replace(
        '"count": 3, "line_number"', '"count": 3, "count": 4, "line_number"'
    )

    _error(text, code="duplicate_key")


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"not utf-8: \xff", "invalid_utf8"),
        (b'{"format_version": NaN}', "nonfinite_number"),
        (b'{"format_version": 1.5}', "number_type"),
        (b"{", "invalid_json"),
    ],
)
def test_rejects_strict_json_encoding_and_numbers(payload: bytes, code: str):
    with pytest.raises(GcovJsonError) as caught:
        parse_gcov_json_gz(gzip.compress(payload))
    assert caught.value.code == code


def test_rejects_oversized_json_integer_with_typed_error():
    payload = b'{"value":' + (b"9" * 5_000) + b"}"

    with pytest.raises(GcovJsonError) as caught:
        parse_gcov_json_gz(gzip.compress(payload))

    assert caught.value.code == "number_bound"


def test_rejects_invalid_gzip_and_trailing_members():
    with pytest.raises(GcovJsonError, match="invalid_gzip"):
        parse_gcov_json_gz(b"\x1f\x8bnot-a-stream")
    with pytest.raises(GcovJsonError, match="invalid_gzip"):
        parse_gcov_json_gz(_gzip(_document()) + _gzip(_document()))


def test_enforces_compressed_and_decompressed_bounds():
    payload = _gzip(_document())
    with pytest.raises(GcovJsonError, match="compressed_limit"):
        parse_gcov_json_gz(payload, max_compressed_bytes=len(payload) - 1)
    with pytest.raises(GcovJsonError, match="decompressed_limit"):
        parse_gcov_json_gz(payload, max_decompressed_bytes=32)
    with pytest.raises(GcovJsonError, match="invalid_limit"):
        parse_gcov_json_gz(payload, max_compressed_bytes=0)
    with pytest.raises(GcovJsonError, match="invalid_limit"):
        parse_gcov_json_gz(payload, max_decompressed_bytes=10**100)


def test_path_reader_rejects_symlinks_and_special_files(tmp_path: Path):
    artifact = tmp_path / "report.gcov.json.gz"
    artifact.write_bytes(_gzip(_document()))
    link = tmp_path / "link.gcov.json.gz"
    link.symlink_to(artifact)

    with pytest.raises(GcovJsonError, match="read_error"):
        parse_gcov_json_gz(link)

    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "report.fifo"
        os.mkfifo(fifo)
        with pytest.raises(GcovJsonError, match="regular non-symlink"):
            parse_gcov_json_gz(fifo)


def test_enforces_count_bounds_and_rejects_duplicate_file_paths():
    document = _document()
    document["files"].append(copy.deepcopy(document["files"][0]))
    _error(document, code="duplicate_file")

    document = _document()
    document["files"][0]["lines"][0]["line_number"] = 0
    _error(document, code="count_bound")


def test_result_dataclasses_are_immutable_and_use_tuples():
    report = parse_gcov_json_gz(_gzip(_document()))

    assert isinstance(report.files, tuple)
    assert isinstance(report.files[0].lines, tuple)
    with pytest.raises(FrozenInstanceError):
        report.format_version = 1
