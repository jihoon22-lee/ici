"""Capture frozen Clippy/ESLint/tsc parsing (development-only oracle, no tool runs)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from ici.adapters.providers.ecosystem import EcosystemProvider

ROOT = "/workspace"


def clippy_record(level: str, code: str | None, line: int, column: int, text: str) -> str:
    span = {"file_name": "src/lib.rs", "line_start": line, "column_start": column}
    message = {
        "level": level,
        "message": text,
        "code": {"code": code} if code else None,
        "spans": [{**span, "is_primary": True}],
    }
    return json.dumps({"reason": "compiler-message", "message": message})


FINISHED = json.dumps({"reason": "build-finished", "success": True})
CASES = {
    "eslint": [
        (
            "rules",
            json.dumps(
                [
                    {
                        "filePath": f"{ROOT}/src/a.js",
                        "messages": [
                            {
                                "ruleId": "no-unused-vars",
                                "severity": 2,
                                "message": "'x' is assigned a value but never used.",
                                "line": 1,
                                "column": 7,
                            },
                            {
                                "ruleId": "eqeqeq",
                                "severity": 1,
                                "message": "Expected '===' and instead saw '=='.",
                                "line": 2,
                                "column": 3,
                            },
                        ],
                    },
                    {"filePath": f"{ROOT}/src/clean.js", "messages": []},
                ]
            ),
            1,
        ),
        (
            "fatal",
            json.dumps(
                [
                    {
                        "filePath": f"{ROOT}/src/b.ts",
                        "messages": [
                            {
                                "ruleId": None,
                                "fatal": True,
                                "severity": 2,
                                "message": "Parsing error: ';' expected.",
                                "line": 3,
                                "column": 5,
                            }
                        ],
                    }
                ]
            ),
            1,
        ),
        (
            "difference:ignored-file",
            json.dumps(
                [
                    {
                        "filePath": f"{ROOT}/src/a.js",
                        "messages": [
                            {
                                "ruleId": None,
                                "severity": 1,
                                "message": "File ignored because of a matching ignore pattern. "
                                "Use --no-ignore to override.",
                            }
                        ],
                    }
                ]
            ),
            0,
        ),
        ("not-json", "Oops! Something went wrong!", 2),
        ("exit-without-findings", "[]", 1),
    ],
    "clippy": [
        (
            "warnings",
            "\n".join(
                [
                    clippy_record("warning", "clippy::needless_return", 3, 5, "unneeded `return`"),
                    clippy_record("error", "E0308", 7, 9, "mismatched types"),
                    clippy_record("warning", None, 9, 1, "unused import"),
                    FINISHED,
                ]
            ),
            101,
        ),
        ("no-finish", clippy_record("warning", "clippy::x", 1, 1, "m"), 0),
        (
            "difference:unknown-reason",
            json.dumps({"reason": "future-record"}) + "\n" + FINISHED,
            0,
        ),
        ("clean", FINISHED, 0),
    ],
    "tsc": [
        (
            "chain",
            "src/a.ts(4,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
            "  Type 'A' is missing.\n"
            "src/b.ts(1,1): error TS2304: Cannot find name 'q'.\n",
            2,
        ),
        ("global", "error TS5058: The specified path does not exist.\n", 1),
        ("clean", "", 0),
    ],
}


def main() -> None:
    result: dict[str, object] = {"source": "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188", "root": ROOT}
    for name, cases in CASES.items():
        provider = EcosystemProvider(name, Path(ROOT))
        rows = []
        for case, stdout, exit_code in cases:
            outcome = SimpleNamespace(
                parseable=stdout, exit_code=exit_code, spec=SimpleNamespace(cwd=ROOT, name="t")
            )
            parsed = provider.parse(outcome)
            rows.append(
                {
                    "case": case,
                    "stdout": stdout,
                    "exit": exit_code,
                    "failed": not parsed.is_readable,
                    "findings": [
                        {
                            "rule_id": item.rule_id,
                            "native_rule_id": item.native_rule_id,
                            "message": item.message,
                            "severity": item.severity,
                            "path": item.primary_location.path,
                            "line": item.primary_location.start_line,
                            "column": item.primary_location.start_column,
                            "fingerprint": item.fingerprint,
                        }
                        for item in parsed.findings
                    ],
                }
            )
        result[name] = rows
    Path(sys.argv[1]).write_text(
        json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
