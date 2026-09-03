"""Contracts for bounded runtime-sanitizer diagnostic normalization."""

from pathlib import Path

import pytest

from ici.engines._sanitizer_diagnostics import (
    MAX_SANITIZER_DIAGNOSTICS,
    MAX_SANITIZER_FRAMES,
    MAX_SANITIZER_OUTPUT_CHARS,
    SanitizerDiagnosticError,
    parse_sanitizer_diagnostics,
)


def _source(root: Path, relative: str, lines: int = 80) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"int value_{number};\n" for number in range(1, lines + 1)))
    return path


def test_asan_normalizes_defect_and_project_stack_locations(tmp_path: Path) -> None:
    source = _source(tmp_path, "src/worker.cpp")
    helper = _source(tmp_path, "src/helper.cpp")
    output = f"""==41==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010
READ of size 4 at 0x602000000010 thread T0
    #0 0x7f00 in __asan_load4 /usr/lib/llvm/asan_rtl.cpp:95
    #1 0x7f01 in Worker::read() {source}:42:7
    #2 0x7f02 in run_worker {helper}:12
SUMMARY: AddressSanitizer: heap-use-after-free {source}:42 in Worker::read()
"""

    diagnostics = parse_sanitizer_diagnostics(output, tmp_path)

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.kind == "address"
    assert diagnostic.tool_name == "AddressSanitizer"
    assert diagnostic.defect == "heap-use-after-free"
    assert diagnostic.rule_id == "ici.sanitize.address.heap-use-after-free"
    assert diagnostic.primary_location is not None
    assert diagnostic.primary_location.path == "src/worker.cpp"
    assert diagnostic.primary_location.start_line == 42
    assert diagnostic.primary_location.start_column == 7
    assert diagnostic.primary_location.label == "frame #1: Worker::read()"
    assert [(item.path, item.start_line) for item in diagnostic.related_locations] == [
        ("[external]", 95),
        ("src/helper.cpp", 12),
    ]
    assert diagnostic.frames_observed == 3
    assert diagnostic.project_frames == 2


def test_ubsan_runtime_location_is_primary_without_stack(tmp_path: Path) -> None:
    source = _source(tmp_path, "src/math.cpp")
    output = f"{source}:8:13: runtime error: signed integer overflow: 2147483647 + 1\n"

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.kind == "undefined-behavior"
    assert diagnostic.defect == "signed-integer-overflow"
    assert diagnostic.rule_id == "ici.sanitize.undefined-behavior.signed-integer-overflow"
    assert diagnostic.primary_location is not None
    assert diagnostic.primary_location.path == "src/math.cpp"
    assert diagnostic.primary_location.start_line == 8
    assert diagnostic.primary_location.start_column == 13
    assert diagnostic.primary_location.label == "runtime error"
    assert diagnostic.related_locations == ()


def test_lsan_keeps_a_report_without_a_project_frame(tmp_path: Path) -> None:
    output = """==42==ERROR: LeakSanitizer: detected memory leaks
Direct leak of 64 byte(s) in 1 object(s)
    #0 0x7f00 in operator new /usr/lib/llvm/asan_new_delete.cpp:95
SUMMARY: AddressSanitizer: 64 byte(s) leaked in 1 allocation(s)
"""

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.kind == "leak"
    assert diagnostic.defect == "memory-leak"
    assert diagnostic.primary_location is None
    assert [(item.path, item.start_line) for item in diagnostic.related_locations] == [
        ("[external]", 95)
    ]
    assert diagnostic.frames_observed == 1
    assert diagnostic.project_frames == 0


def test_multiple_reports_are_kept_in_process_order(tmp_path: Path) -> None:
    first = _source(tmp_path, "src/first.cpp")
    second = _source(tmp_path, "src/second.cpp")
    output = f"""ERROR: AddressSanitizer: double-free
    #0 0x1 in release {first}:3
ERROR: AddressSanitizer: stack-buffer-overflow
    #0 0x2 in copy {second}:4
"""

    diagnostics = parse_sanitizer_diagnostics(output, tmp_path)

    assert [item.defect for item in diagnostics] == ["double-free", "stack-buffer-overflow"]
    assert [item.primary_location.path for item in diagnostics if item.primary_location] == [
        "src/first.cpp",
        "src/second.cpp",
    ]


def test_summary_only_report_is_normalized(tmp_path: Path) -> None:
    source = _source(tmp_path, "src/summary.cpp")
    output = f"SUMMARY: AddressSanitizer: heap-buffer-overflow {source}:9 in copy\n"

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.defect == "heap-buffer-overflow"
    assert diagnostic.primary_location is not None
    assert diagnostic.primary_location.path == "src/summary.cpp"
    assert diagnostic.primary_location.start_line == 9


def test_outside_missing_and_out_of_range_locations_are_not_published(tmp_path: Path) -> None:
    source = _source(tmp_path, "src/short.cpp", lines=2)
    outside = _source(tmp_path.parent, f"{tmp_path.name}-outside.cpp", lines=10)
    output = f"""ERROR: AddressSanitizer: stack-buffer-overflow
    #0 0x1 in outside {outside}:2
    #1 0x2 in missing {tmp_path / "src/missing.cpp"}:1
    #2 0x3 in too_far {source}:99
"""

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.primary_location is None
    assert [(item.path, item.start_line) for item in diagnostic.related_locations] == [
        ("[external]", 2)
    ]
    assert diagnostic.frames_observed == 3


@pytest.mark.parametrize(
    "output",
    [
        "x" * (MAX_SANITIZER_OUTPUT_CHARS + 1),
        "ERROR: AddressSanitizer: heap-use-after-free\x00",
        "ERROR: AddressSanitizer: heap-use-after-free\ud800",
    ],
    ids=["characters", "nul", "surrogate"],
)
def test_invalid_transcripts_fail_closed(tmp_path: Path, output: str) -> None:
    with pytest.raises(SanitizerDiagnosticError):
        parse_sanitizer_diagnostics(output, tmp_path)


def test_diagnostic_count_is_bounded(tmp_path: Path) -> None:
    output = "\n".join(
        "ERROR: AddressSanitizer: heap-use-after-free" for _ in range(MAX_SANITIZER_DIAGNOSTICS + 1)
    )

    with pytest.raises(SanitizerDiagnosticError, match="diagnostic limit"):
        parse_sanitizer_diagnostics(output, tmp_path)


def test_stack_frame_count_is_bounded(tmp_path: Path) -> None:
    output = "ERROR: AddressSanitizer: heap-use-after-free\n" + "\n".join(
        f"    #{index} 0x1 in function /outside/runtime.cpp:1"
        for index in range(MAX_SANITIZER_FRAMES + 1)
    )

    with pytest.raises(SanitizerDiagnosticError, match="frame limit"):
        parse_sanitizer_diagnostics(output, tmp_path)


def test_utf8_byte_limit_is_enforced_independently_of_character_count(tmp_path: Path) -> None:
    output = "가" * (MAX_SANITIZER_OUTPUT_CHARS // 2)

    with pytest.raises(SanitizerDiagnosticError, match="UTF-8 byte"):
        parse_sanitizer_diagnostics(output, tmp_path)


def test_project_path_with_spaces_is_normalized_and_windows_path_is_external(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, "src/worker with spaces.cpp")
    output = f"""ERROR: AddressSanitizer: heap-buffer-overflow
    #0 0x1 in system C:\\sdk\\runtime.cpp:20:4
    #1 0x2 in worker {source}:7:3
"""

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.primary_location is not None
    assert diagnostic.primary_location.path == "src/worker with spaces.cpp"
    assert diagnostic.primary_location.start_line == 7
    assert diagnostic.related_locations[0].path == "[external]"
    assert "C:\\sdk" not in repr(diagnostic)


def test_thread_sanitizer_is_not_misclassified(tmp_path: Path) -> None:
    output = "WARNING: ThreadSanitizer: data race\nSUMMARY: ThreadSanitizer: data race\n"

    assert parse_sanitizer_diagnostics(output, tmp_path) == ()
