"""Development-only capture from the fixed v0.13.0 product source, never shipped."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    actual = subprocess.check_output(
        ["git", "-C", str(args.oracle_root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert actual == SOURCE
    sys.path.insert(0, str(args.oracle_root / "src"))
    from ici.analysis._sanitizer_diagnostics import (
        _MEMORY_DEFECT_PREFIXES,
        _TSAN_DEFECT_PREFIXES,
        SanitizerDiagnosticError,
        parse_sanitizer_diagnostics,
    )

    source = "int fixture_value = 0; // captured source line\n" * 80
    names = [
        "worker",
        "helper",
        "math",
        "first",
        "second",
        "summary",
        "writer",
        "reader",
        "clang-race",
        "unknown",
        "worker 😀 with spaces",
    ]
    sources = {f"src/{name}.cpp": source for name in names}
    sources["src/short.cpp"] = source.splitlines(keepends=True)[0] * 2
    cases = [
        (
            "asan-owned-external",
            "ERROR: AddressSanitizer: heap-use-after-free on address 0x123\n#0 0x11 in system /usr/lib/asan.cpp:95:2\n#1 0x12 in Worker::read() <ROOT>/src/worker.cpp:42:7\n#2 0x13 in helper <ROOT>/src/helper.cpp:12\nSUMMARY: AddressSanitizer: heap-use-after-free\n",
        ),
        (
            "ubsan",
            "<ROOT>/src/math.cpp:8:13: runtime error: signed integer overflow: 2147483647 + 1\n",
        ),
        (
            "lsan-external-only",
            "ERROR: LeakSanitizer: detected memory leaks\n#0 0x1 in operator new /usr/lib/asan.cpp:95\nSUMMARY: AddressSanitizer: 64 bytes leaked\n",
        ),
        (
            "multiple",
            "ERROR: AddressSanitizer: double-free\n#0 0x1 in release <ROOT>/src/first.cpp:3\nERROR: AddressSanitizer: stack-buffer-overflow\n#0 0x2 in copy <ROOT>/src/second.cpp:4\n",
        ),
        (
            "summary-only",
            "SUMMARY: AddressSanitizer: heap-buffer-overflow <ROOT>/src/summary.cpp:9 in copy\n",
        ),
        (
            "unconfirmed-locations",
            "ERROR: AddressSanitizer: stack-buffer-overflow\n#0 0x1 in outside <ROOT>/../outside.cpp:2\n#1 0x2 in missing <ROOT>/src/missing.cpp:1\n#2 0x3 in too_far <ROOT>/src/short.cpp:99\n",
        ),
        (
            "symlink",
            "ERROR: AddressSanitizer: heap-use-after-free\n#0 0x1 in linked <ROOT>/src/linked.cpp:4\n",
        ),
        (
            "spaces-unicode-windows",
            "ERROR: AddressSanitizer: heap-buffer-overflow\n#0 0x1 in system C:\\sdk\\runtime.cpp:20:4\n#1 0x2 in worker <ROOT>/src/worker 😀 with spaces.cpp:7:3\n",
        ),
        (
            "tsan-race",
            "WARNING: ThreadSanitizer: data race (pid=41)\n#0 0x1 in write_value <ROOT>/src/writer.cpp:17:9\n#1 0x2 in read_value <ROOT>/src/reader.cpp:29:5\nSUMMARY: ThreadSanitizer: data race\n",
        ),
        ("tsan-no-signature", "ThreadSanitizer: data race checks enabled\n"),
        (
            "clang-tsan",
            "WARNING: ThreadSanitizer: data race\n#0 0x1 in interceptor /opt/llvm/tsan.cpp:44:2\n#1 0x2 in consume <ROOT>/src/clang-race.cpp:31:7\nSUMMARY: ThreadSanitizer: data race\n",
        ),
        (
            "tsan-unknown-one",
            "WARNING: ThreadSanitizer: runtime wording 0x1234\n#0 0x1 in run <ROOT>/src/unknown.cpp:4\n",
        ),
        (
            "tsan-unknown-two",
            "WARNING: ThreadSanitizer: unrelated wording 99\n#0 0x1 in run <ROOT>/src/unknown.cpp:4\n",
        ),
        (
            "tsan-not-memory-taxonomy",
            "WARNING: ThreadSanitizer: heap-use-after-free\n#0 0x1 in run <ROOT>/src/unknown.cpp:4\n",
        ),
        ("invalid-signature", "WARNING: AddressSanitizer: heap-use-after-free\n"),
        ("diagnostics-over-limit", "ERROR: AddressSanitizer: heap-buffer-overflow\n" * 65),
        (
            "frames-over-limit",
            "ERROR: AddressSanitizer: heap-buffer-overflow\n"
            + "#0 0x1 in run <ROOT>/src/worker.cpp:3\n" * 33,
        ),
        ("nul", "ERROR: AddressSanitizer: heap-use-after-free\0"),
    ]
    for index, (prefix, _) in enumerate(_MEMORY_DEFECT_PREFIXES):
        cases.append(
            (
                f"memory-taxonomy-{index}",
                f"ERROR: AddressSanitizer: {prefix} fixture\n#0 0x1 in run <ROOT>/src/worker.cpp:4\n",
            )
        )
    for index, (prefix, _) in enumerate(_TSAN_DEFECT_PREFIXES):
        cases.append(
            (
                f"thread-taxonomy-{index}",
                f"WARNING: ThreadSanitizer: {prefix} fixture\n#0 0x1 in run <ROOT>/src/worker.cpp:4\n",
            )
        )
    cases.append(("utf8-bytes-over-limit", "😀" * 262145))
    output = []
    with tempfile.TemporaryDirectory(prefix="ici-sanitizer-oracle-") as temporary:
        base = Path(temporary)
        root = base / "project"
        root.mkdir()
        for path, text in sources.items():
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
        outside = base / "outside.cpp"
        outside.write_text(source, encoding="utf-8")
        (root / "src/linked.cpp").symlink_to(outside)
        for name, text in cases:
            case = (
                {"name": name, "output": text}
                if name != "utf8-bytes-over-limit"
                else {"name": name, "output_recipe": {"repeat_text": "😀", "repeat_count": 262145}}
            )
            try:
                diagnostics = parse_sanitizer_diagnostics(text.replace("<ROOT>", str(root)), root)
                case["expected"] = [dataclasses.asdict(item) for item in diagnostics]
            except SanitizerDiagnosticError as error:
                case["expected_error"] = str(error)
            output.append(case)
    source_file = args.oracle_root / "src/ici/analysis/_sanitizer_diagnostics.py"
    args.output.write_text(
        json.dumps(
            {
                "oracle_source": SOURCE,
                "source_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
                "oracle_python": sys.version.split()[0],
                "sources": sources,
                "cases": output,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
