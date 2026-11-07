"""Development-only frozen gcov JSON per-report refusal captures; never called by Rust.

Feeds crafted `.gcov.json.gz` payloads (gzip framing faults, UTF-8 faults, JSON syntax faults
in CPython 3.10's scanner, frozen's number/duplicate-key hooks, the generic tree limits and every
schema rule) to the frozen `analysis/gcov_json.parse_gcov_json_gz` with the per-report budgets
frozen `_read_report_within_budget` passes, and records `exc.code` and `str(exc)` (or acceptance).
The Rust replay is `crates/ici-validation/src/gcov_legacy_json.rs` `frozen_capture_tests`.
"""

import base64
import gzip
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
GOLDEN = ROOT / "tests/rust-parity/cpp/gcov-json-errors-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.analysis.gcov_json import GcovJsonError, parse_gcov_json_gz  # noqa: E402

MIB = 1024 * 1024
FUNCTION = {
    "blocks": 2,
    "blocks_executed": 1,
    "demangled_name": "f()",
    "name": "_Z1fv",
    "start_line": 1,
    "end_line": 3,
    "start_column": 1,
    "end_column": 2,
    "execution_count": 1,
}
BRANCH = {
    "count": 1,
    "fallthrough": True,
    "throw": False,
    "source_block_id": 0,
    "destination_block_id": 1,
}
LINE = {
    "line_number": 2,
    "count": 1,
    "unexecuted_block": False,
    "block_ids": [0],
    "function_name": "_Z1fv",
    "branches": [BRANCH],
    "calls": [{"source_block_id": 0, "destination_block_id": 1, "returned": 1}],
    "conditions": [{"count": 2, "covered": 1, "not_covered_false": [1], "not_covered_true": []}],
}
PRIME = {
    "total_prime_paths": 2,
    "covered_prime_paths": 1,
    "prime_path_coverage": [
        {
            "id": 0,
            "sequence": [
                {
                    "block_id": 1,
                    "edge_kind": "",
                    "locations": [{"file": "a.cpp", "line_numbers": [2]}],
                }
            ],
        }
    ],
}


def document(**root) -> dict:
    base = {
        "format_version": "2",
        "gcc_version": "15.2.0 20250914",
        "current_working_directory": "/w",
        "data_file": "a.gcda",
        "files": [{"file": "a.cpp", "functions": [dict(FUNCTION)], "lines": [dict(LINE)]}],
    }
    base.update(root)
    return base


def with_file(**changes) -> dict:
    file = {"file": "a.cpp", "functions": [dict(FUNCTION)], "lines": [dict(LINE)]}
    file.update(changes)
    return document(files=[file])


def with_function(**changes) -> dict:
    function = dict(FUNCTION)
    function.update(changes)
    return with_file(functions=[function])


def without(mapping: dict, *keys: str) -> dict:
    return {key: value for key, value in mapping.items() if key not in keys}


def with_line(line: dict, version: str = "2") -> dict:
    return document(
        format_version=version,
        files=[{"file": "a.cpp", "functions": [dict(FUNCTION)], "lines": [line]}],
    )


def text(value: dict) -> bytes:
    return json.dumps(value).encode()


def gz(payload: bytes) -> bytes:
    return gzip.compress(payload, mtime=0)


GOOD = text(document())
GOOD_GZ = gz(GOOD)


def flip(payload: bytes, index: int) -> bytes:
    data = bytearray(payload)
    data[index] ^= 0xFF
    return bytes(data)


def header(payload: bytes, index: int, value: int) -> bytes:
    data = bytearray(payload)
    data[index] = value
    return bytes(data)


DEEP = b"[" * 65 + b"0" + b"]" * 65
WIDE = json.dumps({f"k{index}": 0 for index in range(129)}).encode()
LONG = with_file(file="x" * (MIB + 1))
# (name, gzip payload, max_compressed, max_decompressed)
CASES = [
    ("valid", GOOD_GZ),
    ("valid-v1", gz(text(with_line(without(LINE, "calls", "block_ids"), "1")))),
    ("not-gzip", GOOD),
    ("trailing-data", GOOD_GZ + b"x"),
    ("concatenated", GOOD_GZ + GOOD_GZ),
    ("truncated", GOOD_GZ[:-5]),
    ("truncated-header", GOOD_GZ[:6]),
    ("crc", flip(GOOD_GZ, len(GOOD_GZ) - 6)),
    ("length", flip(GOOD_GZ, len(GOOD_GZ) - 2)),
    ("method", header(GOOD_GZ, 2, 7)),
    ("flags", header(GOOD_GZ, 3, 0xE0)),
    ("deflate-body", flip(GOOD_GZ, 12)),
    ("deflate-body-2", flip(GOOD_GZ, 30)),
    ("decompressed-limit", GOOD_GZ, 8 * MIB, 100),
    ("compressed-limit", GOOD_GZ, 10, 64 * MIB),
    ("utf8-start", gz(b'{"a":"\xff"}')),
    ("utf8-continuation", gz(b'{"a":"\xe2\x28"}')),
    ("utf8-continuation-2", gz(b'{"a":"\xe2\x82\x28"}')),
    ("utf8-end", gz(b'{"a":"\xe2\x82')),
    ("bom", gz("﻿{}".encode())),
    ("empty", gz(b"")),
    ("whitespace", gz(b" \n ")),
    ("missing-comma", gz(b'{"a":1 "b":2}')),
    ("missing-colon", gz(b'{"a" 1}')),
    ("object-trailing-comma", gz(b'{"a":1,}')),
    ("array-trailing-comma", gz(b"[1,]")),
    ("unquoted-key", gz(b"{a:1}")),
    ("unterminated-object", gz(b'{"a":1')),
    ("unterminated-array", gz(b"[1")),
    ("unterminated-string", gz(b'{"a":"b')),
    ("bad-escape", gz(b'{"a":"\\x"}')),
    ("bad-unicode", gz(b'{"a":"\\u12G4"}')),
    ("short-unicode", gz(b'{"a":"\\u12')),
    ("bad-low-surrogate", gz(b'{"a":"\\ud800\\u12G4"}')),
    ("control", gz(b'{"a":"\x01"}')),
    ("extra-data", gz(b"{} {}")),
    ("leading-zero", gz(b"[01]")),
    ("minus", gz(b"[-]")),
    ("bare-word", gz(b"[nul]")),
    ("multiline", gz(b'{\n  "a": 1,\n  "b": \xc3\xa9x\n}')),
    ("duplicate-key", gz(b'{"a":1,"b":{"c":1,"c":2},"a":3}')),
    ("duplicate-key-quote", gz(b'{"it\'s":1,"it\'s":2}')),
    ("nan", gz(b"[NaN]")),
    ("infinity", gz(b"[Infinity]")),
    ("minus-infinity", gz(b"[-Infinity]")),
    ("float", gz(b"[1.5]")),
    ("exponent", gz(b"[1e5]")),
    ("exponent-backtrack", gz(b"[1e]")),
    ("number-bound", gz(b"[123456789012345678901]")),
    ("depth", gz(DEEP)),
    ("object-members", gz(WIDE)),
    ("array-items", gz(b"[" + b"0," * 2_000_000 + b"0]")),
    ("root-array", gz(b"[]")),
    ("root-missing", gz(text(without(document(), "files", "data_file")))),
    ("root-unknown", gz(text(document(extra=1, alpha=2)))),
    ("version-int", gz(text(document(format_version=2)))),
    ("version-3", gz(text(document(format_version="3")))),
    ("files-object", gz(text(document(files={})))),
    ("files-bound", gz(text(document(files=[{}] * 4097)))),
    ("file-missing", gz(text(document(files=[{"file": "a.cpp"}])))),
    ("functions-type", gz(text(with_file(functions=None)))),
    ("blocks-bool", gz(text(with_function(blocks=True)))),
    ("blocks-executed", gz(text(with_function(blocks_executed=3)))),
    ("start-line-zero", gz(text(with_function(start_line=0)))),
    ("counter-bound", gz(text(with_function(execution_count=2**64)))),
    ("position-order", gz(text(with_function(end_line=1, end_column=1, start_column=5)))),
    ("prime-partial", gz(text(with_function(total_prime_paths=1)))),
    ("prime-covered", gz(text(with_function(**dict(PRIME, covered_prime_paths=3))))),
    (
        "prime-too-many",
        gz(text(with_function(**dict(PRIME, total_prime_paths=0, covered_prime_paths=0)))),
    ),
    (
        "prime-empty-sequence",
        gz(text(with_function(**dict(PRIME, prime_path_coverage=[{"id": 0, "sequence": []}])))),
    ),
    (
        "prime-duplicate",
        gz(
            text(with_function(**dict(PRIME, prime_path_coverage=PRIME["prime_path_coverage"] * 2)))
        ),
    ),
    (
        "prime-location-line",
        gz(
            text(
                with_function(
                    **dict(
                        PRIME,
                        prime_path_coverage=[
                            {
                                "id": 0,
                                "sequence": [
                                    {
                                        "block_id": 1,
                                        "edge_kind": "",
                                        "locations": [{"file": "", "line_numbers": [0]}],
                                    }
                                ],
                            }
                        ],
                    )
                )
            )
        ),
    ),
    ("duplicate-function", gz(text(with_file(functions=[dict(FUNCTION), dict(FUNCTION)])))),
    (
        "duplicate-file",
        gz(
            text(
                document(
                    files=[
                        {"file": "a.cpp", "functions": [], "lines": []},
                        {"file": "a.cpp", "functions": [], "lines": []},
                    ]
                )
            )
        ),
    ),
    ("v1-calls", gz(text(with_line(without(LINE, "block_ids"), "1")))),
    ("v2-block-ids", gz(text(with_line(without(LINE, "block_ids"))))),
    (
        "branch-partial",
        gz(text(with_line(dict(LINE, branches=[without(BRANCH, "source_block_id")])))),
    ),
    (
        "branch-v2-missing",
        gz(
            text(
                with_line(
                    dict(
                        LINE, branches=[without(BRANCH, "source_block_id", "destination_block_id")]
                    )
                )
            )
        ),
    ),
    ("branches-null", gz(text(with_line(dict(LINE, branches=None))))),
    (
        "condition-covered",
        gz(
            text(
                with_line(
                    dict(
                        LINE,
                        conditions=[
                            {
                                "count": 1,
                                "covered": 2,
                                "not_covered_false": [],
                                "not_covered_true": [],
                            }
                        ],
                    )
                )
            )
        ),
    ),
    ("block-id-bound", gz(text(with_line(dict(LINE, block_ids=[2**32]))))),
    ("line-zero", gz(text(with_line(dict(LINE, line_number=0))))),
    ("unexecuted-int", gz(text(with_line(dict(LINE, unexecuted_block=0))))),
    ("function-name-null", gz(text(with_line(dict(LINE, function_name=None))))),
    (
        "call-returned",
        gz(
            text(
                with_line(
                    dict(
                        LINE,
                        calls=[{"source_block_id": 0, "destination_block_id": 1, "returned": -1}],
                    )
                )
            )
        ),
    ),
    ("empty-data-file", gz(text(document(data_file="")))),
    ("control-file", gz(text(with_file(file="a\tb.cpp")))),
    (
        "surrogate",
        gz(
            b'{"format_version":"2","gcc_version":"1","data_file":"a",'
            b'"files":[{"file":"x\\ud800y","functions":[],"lines":[]}]}'
        ),
    ),
    (
        "surrogate-run",
        gz(
            b'{"format_version":"2","gcc_version":"1","data_file":"a",'
            b'"files":[{"file":"x\\udc00\\ud800","functions":[],"lines":[]}]}'
        ),
    ),
    (
        "surrogate-pair",
        gz(
            b'{"format_version":"2","gcc_version":"1","data_file":"a",'
            b'"files":[{"file":"x\\ud83d\\ude00","functions":[],"lines":[]}]}'
        ),
    ),
    ("gcc-version", gz(text(document(gcc_version="gcc 15")))),
    ("gcc-version-suffix", gz(text(document(gcc_version="15.2.0  ")))),
    ("cwd-int", gz(text(document(current_working_directory=1)))),
    ("string-limit", gz(text(LONG))),
]


def run(
    name: str, payload: bytes, max_compressed: int = 8 * MIB, max_decompressed: int = 64 * MIB
) -> dict:
    try:
        parse_gcov_json_gz(
            payload,
            max_compressed_bytes=max_compressed,
            max_decompressed_bytes=max_decompressed,
        )
        result = None
    except GcovJsonError as exc:
        result = {"code": exc.code, "message": str(exc)}
    return {
        "name": name,
        "payload": base64.b64encode(payload).decode(),
        "max_compressed": max_compressed,
        "max_decompressed": max_decompressed,
        "result": result,
    }


def main() -> None:
    cases = [run(*case) for case in CASES]
    GOLDEN.write_text(
        json.dumps(
            {"oracle": EXPECTED, "python": sys.version.split()[0], "cases": cases},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    for case in cases:
        print(case["name"], (case["result"] or {}).get("message", "accepted")[:150])


if __name__ == "__main__":
    main()
