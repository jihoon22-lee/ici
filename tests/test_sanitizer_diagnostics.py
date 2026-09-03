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
    assert diagnostic.kind == "asan"
    assert diagnostic.tool_name == "AddressSanitizer"
    assert diagnostic.defect == "heap-use-after-free"
    assert diagnostic.rule_id == "ici.sanitize.asan.heap-use-after-free"
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

    assert diagnostic.kind == "ubsan"
    assert diagnostic.defect == "signed-integer-overflow"
    assert diagnostic.rule_id == "ici.sanitize.ubsan.signed-integer-overflow"
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

    assert diagnostic.kind == "lsan"
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


def test_project_symlink_location_is_not_published(tmp_path: Path) -> None:
    outside = _source(tmp_path.parent, f"{tmp_path.name}-outside-link.cpp", lines=10)
    linked = tmp_path / "src/linked.cpp"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")
    output = f"""ERROR: AddressSanitizer: heap-use-after-free
    #0 0x1 in linked {linked}:4
"""

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.primary_location is None
    assert diagnostic.related_locations == ()
    assert diagnostic.project_frames == 0


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


def test_thread_sanitizer_normalizes_data_race_and_project_stack(tmp_path: Path) -> None:
    writer = _source(tmp_path, "src/writer.cpp")
    reader = _source(tmp_path, "src/reader.cpp")
    output = f"""WARNING: ThreadSanitizer: data race (pid=41)
  Write of size 4 at 0x7b0400000800 by thread T1:
    #0 write_value {writer}:17:9 (race+0x1234)
  Previous read of size 4 at 0x7b0400000800 by main thread:
    #1 read_value {reader}:29:5 (race+0x5678)
SUMMARY: ThreadSanitizer: data race {writer}:17 in write_value
"""

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.kind == "tsan"
    assert diagnostic.tool_name == "ThreadSanitizer"
    assert diagnostic.defect == "data-race"
    assert diagnostic.rule_id == "ici.sanitize.tsan.data-race"
    assert diagnostic.primary_location is not None
    assert diagnostic.primary_location.path == "src/writer.cpp"
    assert diagnostic.primary_location.start_line == 17
    assert [(item.path, item.start_line) for item in diagnostic.related_locations] == [
        ("src/reader.cpp", 29)
    ]
    assert diagnostic.frames_observed == 2
    assert diagnostic.project_frames == 2


def test_thread_sanitizer_name_without_report_signature_is_ignored(tmp_path: Path) -> None:
    output = "ThreadSanitizer: data race checks enabled\n"

    assert parse_sanitizer_diagnostics(output, tmp_path) == ()


def test_clang_thread_sanitizer_keeps_columns_and_redacts_external_frames(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, "src/clang-race.cpp")
    output = f"""WARNING: ThreadSanitizer: data race
  Read of size 8 at 0x7b0400000800 by thread T2:
    #0 read_value /opt/llvm/lib/tsan_interceptors.cpp:44:2 (race+0x1000)
    #1 consume {source}:31:7 (race+0x1001)
SUMMARY: ThreadSanitizer: data race {source}:31:7 in consume
"""

    diagnostic = parse_sanitizer_diagnostics(output, tmp_path)[0]

    assert diagnostic.rule_id == "ici.sanitize.tsan.data-race"
    assert diagnostic.primary_location is not None
    assert diagnostic.primary_location.path == "src/clang-race.cpp"
    assert diagnostic.primary_location.start_line == 31
    assert diagnostic.primary_location.start_column == 7
    assert [
        (item.path, item.start_line, item.start_column) for item in diagnostic.related_locations
    ] == [("[external]", 44, 2)]


def test_unknown_thread_sanitizer_wording_never_becomes_a_rule_id(tmp_path: Path) -> None:
    source = _source(tmp_path, "src/unknown.cpp")
    outputs = (
        f"WARNING: ThreadSanitizer: runtime wording 0x1234\n    #0 run {source}:4\n",
        f"WARNING: ThreadSanitizer: unrelated changing wording 99\n    #0 run {source}:4\n",
    )

    diagnostics = [parse_sanitizer_diagnostics(output, tmp_path)[0] for output in outputs]

    assert {item.defect for item in diagnostics} == {"thread-safety-defect"}
    assert {item.rule_id for item in diagnostics} == {"ici.sanitize.tsan.thread-safety-defect"}


def test_thread_sanitizer_never_borrows_memory_sanitizer_defect_ids(
    tmp_path: Path,
) -> None:
    source = tmp_path / "race.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    diagnostics = parse_sanitizer_diagnostics(
        f"WARNING: ThreadSanitizer: heap-use-after-free\n    #0 run {source}:1\n",
        tmp_path,
    )

    assert diagnostics[0].defect == "thread-safety-defect"
    assert diagnostics[0].rule_id == "ici.sanitize.tsan.thread-safety-defect"
