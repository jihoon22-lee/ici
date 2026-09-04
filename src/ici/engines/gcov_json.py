"""Strict, bounded parser for GCC's ``.gcov.json.gz`` evidence files.

The parser deliberately stops at the evidence format boundary.  It does not
resolve source paths, inspect a build tree, or calculate coverage percentages;
those are responsibilities of the coverage engine.  Keeping this module
independent makes it useful for both the C++ engine and focused parser tests.
"""

from __future__ import annotations

import json
import os
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# These limits are intentionally conservative defaults for a single gcov
# evidence file.  Callers can lower the byte limits for untrusted CI inputs.
MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_FILES = 4_096
MAX_FUNCTIONS_PER_FILE = 100_000
MAX_LINES_PER_FILE = 1_000_000
MAX_BRANCHES_PER_LINE = 1_000_000
MAX_CALLS_PER_LINE = 1_000_000
MAX_CONDITIONS_PER_LINE = 1_000_000
MAX_BLOCK_IDS_PER_LINE = 1_000_000
MAX_CONDITION_TERMS = 1_000_000
MAX_STRING_BYTES = 1 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_ARRAY_ITEMS = 2_000_000
MAX_JSON_OBJECT_ITEMS = 128
MAX_COUNTER = (1 << 64) - 1
MAX_SOURCE_POSITION = (1 << 32) - 1

_GZIP_MAGIC = b"\x1f\x8b"
_FORMAT_VERSIONS = {"1": 1, "2": 2}


class GcovJsonError(ValueError):
    """Raised when gcov JSON evidence is unreadable or violates its schema."""

    def __init__(self, message: str, *, code: str = "invalid_gcov_json") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class GcovBranch:
    """One branch edge attached to a source line."""

    count: int
    destination_block_id: int
    fallthrough: bool
    source_block_id: int
    throw: bool


@dataclass(frozen=True)
class GcovCall:
    """One call edge attached to a source line."""

    destination_block_id: int
    returned: int
    source_block_id: int


@dataclass(frozen=True)
class GcovCondition:
    """Condition coverage metadata when gcov was invoked with ``-g``."""

    count: int
    covered: int
    not_covered_false: tuple[int, ...]
    not_covered_true: tuple[int, ...]


@dataclass(frozen=True)
class GcovFunction:
    """Function execution and source-position data."""

    blocks: int
    blocks_executed: int
    demangled_name: str
    end_column: int
    end_line: int
    execution_count: int
    name: str
    start_column: int
    start_line: int


@dataclass(frozen=True)
class GcovLine:
    """Execution data for one source line."""

    block_ids: tuple[int, ...]
    branches: tuple[GcovBranch, ...]
    calls: tuple[GcovCall, ...]
    count: int
    line_number: int
    unexecuted_block: bool
    function_name: str
    conditions: tuple[GcovCondition, ...] = ()


@dataclass(frozen=True)
class GcovFile:
    """Coverage data for one source file, retaining gcov's path verbatim."""

    file: str
    functions: tuple[GcovFunction, ...]
    lines: tuple[GcovLine, ...]

    @property
    def path(self) -> str:
        """Alias for callers that use ``path`` for a source file name."""

        return self.file

    @property
    def source_path(self) -> str:
        """Alias documenting that :attr:`file` is a source path."""

        return self.file


@dataclass(frozen=True)
class GcovReport:
    """A complete gcov JSON report after strict validation."""

    current_working_directory: str
    data_file: str
    format_version: int
    gcc_version: str
    files: tuple[GcovFile, ...]

    @property
    def version(self) -> int:
        """Short alias for the parsed semantic format version."""

        return self.format_version


# Names with an explicit JSON qualifier are convenient at integration sites
# and preserve a discoverable API without introducing separate types.
GcovJsonBranch = GcovBranch
GcovJsonCall = GcovCall
GcovJsonCondition = GcovCondition
GcovJsonFunction = GcovFunction
GcovJsonLine = GcovLine
GcovJsonFile = GcovFile
GcovJsonReport = GcovReport

__all__ = [
    "MAX_COMPRESSED_BYTES",
    "MAX_DECOMPRESSED_BYTES",
    "GcovBranch",
    "GcovCall",
    "GcovCondition",
    "GcovFile",
    "GcovFunction",
    "GcovJsonBranch",
    "GcovJsonCall",
    "GcovJsonCondition",
    "GcovJsonError",
    "GcovJsonFile",
    "GcovJsonFunction",
    "GcovJsonLine",
    "GcovJsonReport",
    "GcovLine",
    "GcovReport",
    "parse_gcov_json",
    "parse_gcov_json_bytes",
    "parse_gcov_json_document",
    "parse_gcov_json_file",
    "parse_gcov_json_gz",
]


def _fail(code: str, message: str) -> None:
    raise GcovJsonError(message, code=code)


def _validate_limit(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail("invalid_limit", f"{name} must be a positive integer")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key", f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    _fail("nonfinite_number", f"JSON number {value!r} is not finite")
    return None  # pragma: no cover - _fail always raises


def _reject_float(value: str) -> object:
    _fail("number_type", f"floating-point JSON number {value!r} is not allowed")
    return None  # pragma: no cover - _fail always raises


def _decode_json(payload: bytes, *, max_decompressed_bytes: int) -> object:
    if len(payload) > max_decompressed_bytes:
        _fail(
            "decompressed_limit",
            f"decompressed JSON is larger than {max_decompressed_bytes} bytes",
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("invalid_utf8", f"gcov JSON is not valid UTF-8: {exc}")
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
            parse_float=_reject_float,
        )
    except GcovJsonError:
        raise
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
        _fail("invalid_json", f"malformed gcov JSON: {exc}")
    return None  # pragma: no cover - all successful paths return from json.loads


def _validate_json_tree(value: object, *, depth: int = 0) -> None:
    """Apply generic depth/cardinality limits before schema traversal."""

    if depth > MAX_JSON_DEPTH:
        _fail("json_depth_limit", f"JSON nesting exceeds {MAX_JSON_DEPTH} levels")
    if isinstance(value, dict):
        if len(value) > MAX_JSON_OBJECT_ITEMS:
            _fail("object_count_limit", "JSON object contains too many members")
        for child in value.values():
            _validate_json_tree(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_JSON_ARRAY_ITEMS:
            _fail("array_count_limit", "JSON array contains too many items")
        for child in value:
            _validate_json_tree(child, depth=depth + 1)


def _object(value: object, context: str, *, required: set[str], allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        _fail("type", f"{context} must be an object")
    missing = required.difference(value)
    if missing:
        _fail("missing_field", f"{context} is missing {', '.join(sorted(missing))}")
    unexpected = set(value).difference(allowed)
    if unexpected:
        _fail("unknown_field", f"{context} has unknown field(s): {', '.join(sorted(unexpected))}")
    return value


def _string(value: object, context: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail("type", f"{context} must be a string")
    if not allow_empty and not value:
        _fail("empty_string", f"{context} must not be empty")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:  # defensive: Python strings may contain surrogates
        _fail("invalid_utf8", f"{context} cannot be encoded as UTF-8: {exc}")
    if size > MAX_STRING_BYTES:
        _fail("string_limit", f"{context} is larger than {MAX_STRING_BYTES} bytes")
    if any(ord(char) < 0x20 for char in value):
        _fail("control_character", f"{context} contains a control character")
    return value


def _integer(value: object, context: str, *, minimum: int = 0, maximum: int = MAX_COUNTER) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("type", f"{context} must be an integer")
    if value < minimum or value > maximum:
        _fail("count_bound", f"{context} must be between {minimum} and {maximum}")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        _fail("type", f"{context} must be a boolean")
    return value


def _array(value: object, context: str, *, maximum: int) -> list:
    if not isinstance(value, list):
        _fail("type", f"{context} must be an array")
    if len(value) > maximum:
        _fail("count_bound", f"{context} contains more than {maximum} items")
    return value


def _format_version(value: object) -> int:
    if not isinstance(value, str):
        _fail("type", "format_version must be a string")
    version = _FORMAT_VERSIONS.get(value)
    if version is None:
        _fail("unsupported_version", 'format_version must be "1" or "2"')
    return version


def _gcc_version(value: object) -> str:
    version = _string(value, "gcc_version")
    # gcov includes a build date/hash after the numeric version.  Only the
    # leading numeric version is contractual, so retain the suffix verbatim.
    if re.fullmatch(r"\d+(?:\.\d+)*(?:[ \t]+[^ \t].*)?", version) is None:
        _fail("invalid_gcc_version", "gcc_version must start with a numeric GCC version")
    return version


def _parse_block_ids(value: object, context: str) -> tuple[int, ...]:
    values = _array(value, context, maximum=MAX_BLOCK_IDS_PER_LINE)
    return tuple(
        _integer(item, f"{context}[{index}]", maximum=MAX_SOURCE_POSITION)
        for index, item in enumerate(values)
    )


def _parse_branch(value: object, index: int) -> GcovBranch:
    context = f"branch[{index}]"
    item = _object(
        value,
        context,
        required={"count", "destination_block_id", "fallthrough", "source_block_id", "throw"},
        allowed={"count", "destination_block_id", "fallthrough", "source_block_id", "throw"},
    )
    return GcovBranch(
        count=_integer(item["count"], f"{context}.count"),
        destination_block_id=_integer(
            item["destination_block_id"],
            f"{context}.destination_block_id",
            maximum=MAX_SOURCE_POSITION,
        ),
        fallthrough=_boolean(item["fallthrough"], f"{context}.fallthrough"),
        source_block_id=_integer(
            item["source_block_id"], f"{context}.source_block_id", maximum=MAX_SOURCE_POSITION
        ),
        throw=_boolean(item["throw"], f"{context}.throw"),
    )


def _parse_call(value: object, index: int) -> GcovCall:
    context = f"call[{index}]"
    item = _object(
        value,
        context,
        required={"destination_block_id", "returned", "source_block_id"},
        allowed={"destination_block_id", "returned", "source_block_id"},
    )
    return GcovCall(
        destination_block_id=_integer(
            item["destination_block_id"],
            f"{context}.destination_block_id",
            maximum=MAX_SOURCE_POSITION,
        ),
        returned=_integer(item["returned"], f"{context}.returned"),
        source_block_id=_integer(
            item["source_block_id"], f"{context}.source_block_id", maximum=MAX_SOURCE_POSITION
        ),
    )


def _condition_terms(value: object, context: str) -> tuple[int, ...]:
    values = _array(value, context, maximum=MAX_CONDITION_TERMS)
    return tuple(
        _integer(item, f"{context}[{index}]", maximum=MAX_SOURCE_POSITION)
        for index, item in enumerate(values)
    )


def _parse_condition(value: object, index: int) -> GcovCondition:
    context = f"condition[{index}]"
    item = _object(
        value,
        context,
        required={"count", "covered", "not_covered_false", "not_covered_true"},
        allowed={"count", "covered", "not_covered_false", "not_covered_true"},
    )
    count = _integer(item["count"], f"{context}.count")
    covered = _integer(item["covered"], f"{context}.covered", maximum=MAX_COUNTER)
    if covered > count:
        _fail("count_bound", f"{context}.covered cannot exceed count")
    not_covered_false = _condition_terms(item["not_covered_false"], f"{context}.not_covered_false")
    not_covered_true = _condition_terms(item["not_covered_true"], f"{context}.not_covered_true")
    return GcovCondition(
        count=count,
        covered=covered,
        not_covered_false=not_covered_false,
        not_covered_true=not_covered_true,
    )


def _parse_function(value: object, index: int) -> GcovFunction:
    context = f"function[{index}]"
    fields = {
        "blocks",
        "blocks_executed",
        "demangled_name",
        "end_column",
        "end_line",
        "execution_count",
        "name",
        "start_column",
        "start_line",
    }
    item = _object(value, context, required=fields, allowed=fields)
    blocks = _integer(item["blocks"], f"{context}.blocks")
    blocks_executed = _integer(item["blocks_executed"], f"{context}.blocks_executed")
    if blocks_executed > blocks:
        _fail("count_bound", f"{context}.blocks_executed cannot exceed blocks")
    start_line = _integer(
        item["start_line"], f"{context}.start_line", minimum=1, maximum=MAX_SOURCE_POSITION
    )
    end_line = _integer(
        item["end_line"], f"{context}.end_line", minimum=1, maximum=MAX_SOURCE_POSITION
    )
    start_column = _integer(
        item["start_column"], f"{context}.start_column", minimum=1, maximum=MAX_SOURCE_POSITION
    )
    end_column = _integer(
        item["end_column"], f"{context}.end_column", minimum=1, maximum=MAX_SOURCE_POSITION
    )
    if (end_line, end_column) < (start_line, start_column):
        _fail("position_order", f"{context} ends before it starts")
    return GcovFunction(
        blocks=blocks,
        blocks_executed=blocks_executed,
        demangled_name=_string(
            item["demangled_name"], f"{context}.demangled_name", allow_empty=True
        ),
        end_column=end_column,
        end_line=end_line,
        execution_count=_integer(item["execution_count"], f"{context}.execution_count"),
        name=_string(item["name"], f"{context}.name", allow_empty=True),
        start_column=start_column,
        start_line=start_line,
    )


def _parse_line(value: object, index: int) -> GcovLine:
    context = f"line[{index}]"
    allowed = {
        "block_ids",
        "branches",
        "calls",
        "conditions",
        "count",
        "line_number",
        "unexecuted_block",
        "function_name",
    }
    # block_ids, count, line_number and unexecuted_block are emitted for each
    # line.  branches/calls are intentionally optional because gcov emits them
    # only when invoked with -b; function_name may be absent for inline code.
    item = _object(
        value,
        context,
        required={"block_ids", "count", "line_number", "unexecuted_block"},
        allowed=allowed,
    )
    branches_value = item.get("branches", [])
    branches = _array(branches_value, f"{context}.branches", maximum=MAX_BRANCHES_PER_LINE)
    calls_value = item.get("calls", [])
    calls = _array(calls_value, f"{context}.calls", maximum=MAX_CALLS_PER_LINE)
    conditions_value = item.get("conditions", [])
    conditions = _array(conditions_value, f"{context}.conditions", maximum=MAX_CONDITIONS_PER_LINE)
    line_number = _integer(
        item["line_number"], f"{context}.line_number", minimum=1, maximum=MAX_SOURCE_POSITION
    )
    return GcovLine(
        block_ids=_parse_block_ids(item["block_ids"], f"{context}.block_ids"),
        branches=tuple(
            _parse_branch(branch, branch_index) for branch_index, branch in enumerate(branches)
        ),
        calls=tuple(_parse_call(call, call_index) for call_index, call in enumerate(calls)),
        conditions=tuple(
            _parse_condition(condition, condition_index)
            for condition_index, condition in enumerate(conditions)
        ),
        count=_integer(item["count"], f"{context}.count"),
        line_number=line_number,
        unexecuted_block=_boolean(item["unexecuted_block"], f"{context}.unexecuted_block"),
        function_name=_string(
            item.get("function_name", ""), f"{context}.function_name", allow_empty=True
        ),
    )


def _parse_file(value: object, index: int) -> GcovFile:
    context = f"file[{index}]"
    item = _object(
        value,
        context,
        required={"file", "functions", "lines"},
        allowed={"file", "functions", "lines"},
    )
    functions_value = _array(
        item["functions"], f"{context}.functions", maximum=MAX_FUNCTIONS_PER_FILE
    )
    lines_value = _array(item["lines"], f"{context}.lines", maximum=MAX_LINES_PER_FILE)
    return GcovFile(
        file=_string(item["file"], f"{context}.file"),
        functions=tuple(
            _parse_function(function, function_index)
            for function_index, function in enumerate(functions_value)
        ),
        lines=tuple(_parse_line(line, line_index) for line_index, line in enumerate(lines_value)),
    )


def _parse_document(value: object) -> GcovReport:
    fields = {
        "current_working_directory",
        "data_file",
        "format_version",
        "gcc_version",
        "files",
    }
    root = _object(value, "root", required=fields, allowed=fields)
    files_value = _array(root["files"], "root.files", maximum=MAX_FILES)
    if not files_value:
        _fail("missing_data", "root.files must contain at least one file")
    files = tuple(_parse_file(item, index) for index, item in enumerate(files_value))
    paths = [item.file for item in files]
    if len(paths) != len(set(paths)):
        _fail("duplicate_file", "root.files contains duplicate source paths")
    return GcovReport(
        current_working_directory=_string(
            root["current_working_directory"], "root.current_working_directory"
        ),
        data_file=_string(root["data_file"], "root.data_file"),
        format_version=_format_version(root["format_version"]),
        gcc_version=_gcc_version(root["gcc_version"]),
        files=files,
    )


def parse_gcov_json_bytes(
    payload: bytes | bytearray | memoryview,
    *,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> GcovReport:
    """Parse an uncompressed UTF-8 gcov JSON document from bytes.

    This function does not decompress or resolve anything.  Use
    :func:`parse_gcov_json_gz` for gcov's normal ``.gcov.json.gz`` artifact.
    """

    _validate_limit(max_decompressed_bytes, "max_decompressed_bytes")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        _fail("type", "payload must be bytes-like")
    raw = bytes(payload)
    document = _decode_json(raw, max_decompressed_bytes=max_decompressed_bytes)
    _validate_json_tree(document)
    return _parse_document(document)


def parse_gcov_json_document(
    document: str | bytes | bytearray | memoryview,
    *,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> GcovReport:
    """Parse a UTF-8 gcov JSON document supplied as text or bytes."""

    if isinstance(document, str):
        try:
            payload = document.encode("utf-8")
        except UnicodeEncodeError as exc:
            _fail("invalid_utf8", f"gcov JSON cannot be encoded as UTF-8: {exc}")
    elif isinstance(document, (bytes, bytearray, memoryview)):
        payload = bytes(document)
    else:
        _fail("type", "document must be text or bytes-like")
    return parse_gcov_json_bytes(payload, max_decompressed_bytes=max_decompressed_bytes)


def _read_compressed_file(path: str | os.PathLike[str], max_compressed_bytes: int) -> bytes:
    try:
        file_path = Path(path)
        size = file_path.stat().st_size
        if size > max_compressed_bytes:
            _fail(
                "compressed_limit",
                f"compressed gcov JSON is larger than {max_compressed_bytes} bytes",
            )
        with file_path.open("rb") as handle:
            payload = handle.read(max_compressed_bytes + 1)
    except GcovJsonError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        _fail("read_error", f"cannot read gcov JSON gzip file: {exc}")
    if len(payload) > max_compressed_bytes:
        _fail(
            "compressed_limit",
            f"compressed gcov JSON is larger than {max_compressed_bytes} bytes",
        )
    return payload


def _decompress_gzip(payload: bytes, max_decompressed_bytes: int) -> bytes:
    if not payload.startswith(_GZIP_MAGIC):
        _fail("invalid_gzip", "gcov JSON evidence is not gzip-compressed")
    try:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = decompressor.decompress(payload, max_decompressed_bytes + 1)
        if len(output) > max_decompressed_bytes or decompressor.unconsumed_tail:
            _fail(
                "decompressed_limit",
                f"decompressed JSON is larger than {max_decompressed_bytes} bytes",
            )
        output += decompressor.flush(max_decompressed_bytes + 1 - len(output))
    except GcovJsonError:
        raise
    except zlib.error as exc:
        _fail("invalid_gzip", f"malformed gzip stream: {exc}")
    if len(output) > max_decompressed_bytes:
        _fail(
            "decompressed_limit",
            f"decompressed JSON is larger than {max_decompressed_bytes} bytes",
        )
    if not decompressor.eof:
        _fail("invalid_gzip", "gzip stream is truncated")
    # A single gcov artifact is one gzip member.  Reject concatenated members
    # and arbitrary trailing bytes so a valid prefix cannot hide bad evidence.
    if decompressor.unused_data:
        _fail("invalid_gzip", "gzip stream contains trailing data")
    return output


def parse_gcov_json_gz(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    *,
    max_compressed_bytes: int = MAX_COMPRESSED_BYTES,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> GcovReport:
    """Parse one bounded ``.gcov.json.gz`` path or gzip byte payload.

    The source path is read as-is.  No path canonicalization, source matching,
    or build-tree access is performed here.
    """

    _validate_limit(max_compressed_bytes, "max_compressed_bytes")
    _validate_limit(max_decompressed_bytes, "max_decompressed_bytes")
    if isinstance(source, (bytes, bytearray, memoryview)):
        payload = bytes(source)
        if len(payload) > max_compressed_bytes:
            _fail(
                "compressed_limit",
                f"compressed gcov JSON is larger than {max_compressed_bytes} bytes",
            )
    elif isinstance(source, (str, os.PathLike)):
        payload = _read_compressed_file(source, max_compressed_bytes)
    else:
        _fail("type", "source must be a path or bytes-like gzip payload")
    document = _decompress_gzip(payload, max_decompressed_bytes)
    return parse_gcov_json_bytes(document, max_decompressed_bytes=max_decompressed_bytes)


def parse_gcov_json_file(
    path: str | os.PathLike[str],
    *,
    max_compressed_bytes: int = MAX_COMPRESSED_BYTES,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> GcovReport:
    """Path-oriented alias for :func:`parse_gcov_json_gz`."""

    return parse_gcov_json_gz(
        path,
        max_compressed_bytes=max_compressed_bytes,
        max_decompressed_bytes=max_decompressed_bytes,
    )


def parse_gcov_json(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    *,
    compressed: bool | None = None,
    max_compressed_bytes: int = MAX_COMPRESSED_BYTES,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> GcovReport:
    """Convenience parser for a gzip path, gzip bytes, or JSON document.

    Paths are treated as gzip evidence.  Byte payloads are detected by the
    gzip magic when ``compressed`` is omitted; callers can force either mode
    explicitly.  A text value beginning with ``{`` is treated as JSON text.
    """

    if compressed is not None and not isinstance(compressed, bool):
        _fail("type", "compressed must be a boolean or None")
    if compressed is True:
        return parse_gcov_json_gz(
            source,
            max_compressed_bytes=max_compressed_bytes,
            max_decompressed_bytes=max_decompressed_bytes,
        )
    if compressed is False:
        if isinstance(source, (str, bytes, bytearray, memoryview)):
            return parse_gcov_json_document(source, max_decompressed_bytes=max_decompressed_bytes)
        _fail("type", "uncompressed source must be JSON text or bytes-like")
    if isinstance(source, (bytes, bytearray, memoryview)):
        payload = bytes(source)
        if payload.startswith(_GZIP_MAGIC):
            return parse_gcov_json_gz(
                payload,
                max_compressed_bytes=max_compressed_bytes,
                max_decompressed_bytes=max_decompressed_bytes,
            )
        return parse_gcov_json_bytes(payload, max_decompressed_bytes=max_decompressed_bytes)
    if isinstance(source, str) and source.lstrip().startswith("{"):
        return parse_gcov_json_document(source, max_decompressed_bytes=max_decompressed_bytes)
    if isinstance(source, (str, os.PathLike)):
        return parse_gcov_json_gz(
            source,
            max_compressed_bytes=max_compressed_bytes,
            max_decompressed_bytes=max_decompressed_bytes,
        )
    _fail("type", "source must be a path, JSON document, or bytes-like payload")
