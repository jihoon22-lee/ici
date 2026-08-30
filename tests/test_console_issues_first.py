"""Contract tests for the bounded, issues-first Rich console projection.

These tests intentionally keep the structured suite as the source of truth:
the console may coalesce and cap its display, but selection must not rewrite
the v3 finding inventory consumed by JSON and other reporters.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path

from rich.cells import cell_len
from rich.console import Console

from ici.core.models import (
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
    VerificationSuiteResult,
)
from ici.reporters.console import print_suite_dashboard
from ici.reporters.html import generate_html_report
from ici.reporters.issue_view import (
    ConsoleGroupBy,
    ConsoleOptions,
    IssueLocation,
    select_issue_groups,
)
from ici.reporters.json_rep import serialize_suite_result


def _finding(
    *,
    path: str = "src/app.py",
    start: int = 1,
    end: int | None = None,
    message: str = "finding",
    rule_id: str = "ici.test.issue",
    fingerprint: str | None = None,
    severity: FindingSeverity = FindingSeverity.HIGH,
    label: str = "",
    related: list[SourceLocation] | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.CORRECTNESS,
        severity=severity,
        confidence=FindingConfidence.EXACT,
        fingerprint=fingerprint or f"sha256:{start:064x}",
        primary_location=SourceLocation(path, start, end, label=label),
        message=message,
        related_locations=related or [],
    )


def _result(
    engine_name: str,
    findings: list[Finding],
    *,
    status: EngineStatus = EngineStatus.WARN,
    extra: dict | None = None,
    targets: list[InspectionTarget] | None = None,
) -> EngineResult:
    return EngineResult(
        engine_name=engine_name,
        status=status,
        summary=f"{engine_name} summary",
        findings=findings,
        targets=targets or [],
        extra=extra or {},
    )


def _suite(results: list[EngineResult]) -> VerificationSuiteResult:
    return VerificationSuiteResult(
        suite_status=EngineStatus.WARN,
        results=results,
        duration=0.25,
    )


def _select(
    suite: VerificationSuiteResult,
    project_root: Path,
    options: ConsoleOptions | None = None,
):
    return select_issue_groups(suite, project_root, options)


def _render(
    suite: VerificationSuiteResult,
    project_root: Path,
    *,
    options: ConsoleOptions | None = None,
    width: int = 100,
) -> str:
    stream = io.StringIO()
    output_console = Console(
        file=stream,
        width=width,
        force_terminal=False,
        color_system=None,
        soft_wrap=False,
    )
    print_suite_dashboard(
        suite,
        project_root,
        options=options,
        output_console=output_console,
    )
    return stream.getvalue()


def test_console_caps_findings_independently_per_engine(tmp_path: Path):
    lint = _result(
        "lint",
        [
            _finding(path="src/lint.py", start=index, message=f"lint-{index}")
            for index in range(1, 4)
        ],
    )
    security = _result(
        "security",
        [
            _finding(path="src/security.py", start=index, message=f"security-{index}")
            for index in range(1, 3)
        ],
    )

    selection = _select(
        _suite([lint, security]),
        tmp_path,
        ConsoleOptions(max_findings=2, group_by=ConsoleGroupBy.ENGINE),
    )

    assert [group.message for group in selection.visible_groups] == [
        "lint-1",
        "lint-2",
        "security-1",
        "security-2",
    ]
    assert selection.total_findings == 5
    assert selection.visible_findings == 4
    assert selection.hidden_findings == 1
    assert selection.hidden_groups == 1


def test_verbose_disables_the_cap_and_preserves_all_issue_groups(tmp_path: Path):
    result = _result(
        "lint",
        [
            _finding(path="src/lint.py", start=index, message=f"all-{index}")
            for index in range(1, 5)
        ],
    )

    selection = _select(
        _suite([result]),
        tmp_path,
        ConsoleOptions(max_findings=1, verbose=True),
    )

    assert [group.message for group in selection.visible_groups] == [
        "all-1",
        "all-2",
        "all-3",
        "all-4",
    ]
    assert selection.hidden_findings == 0
    assert selection.hidden_groups == 0


def test_zero_max_hides_all_findings_but_console_reports_the_hidden_count(tmp_path: Path):
    suite = _suite(
        [
            _result(
                "lint",
                [
                    _finding(path="src/lint.py", start=index, message=f"hidden-{index}")
                    for index in range(1, 4)
                ],
            )
        ]
    )
    selection = _select(suite, tmp_path, ConsoleOptions(max_findings=0))

    assert selection.visible_groups == ()
    assert selection.hidden_findings == 3
    assert selection.hidden_groups == 3

    output = _render(suite, tmp_path, options=ConsoleOptions(max_findings=0))
    assert "hidden-1" not in output
    assert "hidden-2" not in output
    assert "hidden-3" not in output
    assert "hidden" in output.casefold()
    assert "3" in output


def test_native_v3_finding_without_legacy_targets_is_displayed(tmp_path: Path):
    finding = _finding(
        path="src/native.py",
        start=17,
        end=19,
        rule_id="ici.security.native",
        message="native-v3-only-marker",
    )
    suite = _suite([_result("security", [finding], status=EngineStatus.FAIL)])

    selection = _select(suite, tmp_path)
    assert len(selection.all_groups) == 1
    assert selection.all_groups[0].message == "native-v3-only-marker"

    output = _render(suite, tmp_path)
    assert "native-v3-only-marker" in output
    assert "ici.security.native" in output
    assert "src/native.py" in output


def test_same_fingerprint_same_file_unions_only_overlapping_regions(tmp_path: Path, monkeypatch):
    fingerprint = "sha256:" + "a" * 64
    findings = [
        _finding(path="src/a.py", start=10, end=12, fingerprint=fingerprint, message="overlap-1"),
        _finding(path="src/a.py", start=12, end=15, fingerprint=fingerprint, message="overlap-2"),
        _finding(path="src/a.py", start=20, end=22, fingerprint=fingerprint, message="separate-1"),
        _finding(path="src/a.py", start=25, end=26, fingerprint=fingerprint, message="separate-2"),
    ]
    suite = _suite([_result("lint", findings)])

    # The selector consumes the already-normalized v3 stream.  Bypass the
    # canonicalization adapter here so the fixture can model repeated
    # occurrences that intentionally share one producer fingerprint.
    monkeypatch.setattr(
        "ici.reporters.issue_view.findings_for_result",
        lambda result, project_root: list(result.findings),
    )
    selection = _select(suite, tmp_path)

    assert len(selection.all_groups) == 3
    assert [
        (location.path, location.start_line, location.end_line)
        for group in selection.all_groups
        for location in group.locations
    ] == [
        ("src/a.py", 10, 15),
        ("src/a.py", 20, 22),
        ("src/a.py", 25, 26),
    ]
    assert selection.total_findings == 4
    assert selection.visible_findings == 4


def test_clone_group_unions_same_file_overlap_and_keeps_cross_file_locations(
    tmp_path: Path,
):
    clone_id = 7
    result = _result(
        "dup",
        [
            _finding(
                path="src/a.py",
                start=10,
                end=12,
                label=f"CloneGroup#{clone_id}",
                message="clone-a",
            ),
            _finding(
                path="src/a.py",
                start=12,
                end=15,
                label=f"CloneGroup#{clone_id}",
                message="clone-a-overlap",
            ),
            _finding(
                path="src/b.py",
                start=3,
                end=7,
                label=f"CloneGroup#{clone_id}",
                message="clone-b",
            ),
        ],
        extra={
            "clone_groups": [
                {
                    "id": clone_id,
                    "lines_count": 6,
                    "occurrences": [
                        {"file_path": "src/a.py", "start_line": 10, "end_line": 12},
                        {"file_path": "src/a.py", "start_line": 12, "end_line": 15},
                        {"file_path": "src/b.py", "start_line": 3, "end_line": 7},
                    ],
                }
            ]
        },
    )

    selection = _select(_suite([result]), tmp_path)

    assert len(selection.all_groups) == 1
    group = selection.all_groups[0]
    assert group.clone_group_id == str(clone_id)
    assert group.original_finding_count == 3
    assert group.locations == (
        IssueLocation("src/a.py", 10, 15),
        IssueLocation("src/b.py", 3, 7),
    )


def test_selection_is_independent_of_engine_input_order(tmp_path: Path):
    alpha = _result(
        "alpha",
        [_finding(path="src/alpha.py", start=4, message="alpha-marker")],
    )
    beta = _result(
        "beta",
        [_finding(path="src/beta.py", start=4, message="beta-marker")],
    )

    first = _select(_suite([beta, alpha]), tmp_path)
    second = _select(_suite([alpha, beta]), tmp_path)

    first_keys = [(group.engine_name, group.message) for group in first.all_groups]
    second_keys = [(group.engine_name, group.message) for group in second.all_groups]
    assert (
        first_keys
        == second_keys
        == [
            ("alpha", "alpha-marker"),
            ("beta", "beta-marker"),
        ]
    )


def test_console_selection_does_not_mutate_suite_or_json_inventory(tmp_path: Path):
    suite = _suite(
        [
            _result(
                "lint",
                [
                    _finding(
                        path="src/lint.py",
                        start=8,
                        end=9,
                        message="inventory-marker",
                    )
                ],
                extra={"nested": {"keep": [1, 2, 3]}},
            )
        ]
    )
    suite_before = copy.deepcopy(suite)
    json_before = serialize_suite_result(suite, project_root=tmp_path)

    _select(suite, tmp_path, ConsoleOptions(max_findings=0))
    _render(suite, tmp_path, options=ConsoleOptions(max_findings=0))
    html_path = tmp_path / "full-inventory.html"
    generate_html_report(suite, html_path, base_dir=tmp_path)

    assert suite == suite_before
    assert serialize_suite_result(suite, project_root=tmp_path) == json_before
    assert "inventory-marker" in html_path.read_text(encoding="utf-8")


def test_rich_console_stays_within_80_columns_without_character_vertical_wrap(
    tmp_path: Path,
):
    marker = "long-console-marker remains a readable sentence"
    result = _result(
        "engine-with-a-deliberately-long-name",
        [
            _finding(
                path="src/a/very-long-file-name-that-needs-wrapping.py",
                start=42,
                end=45,
                message=marker,
            )
        ],
        status=EngineStatus.FAIL,
    )

    output = _render(
        _suite([result]),
        tmp_path,
        options=ConsoleOptions(verbose=True, max_findings=1),
        width=80,
    )
    lines = output.splitlines()

    assert lines
    assert max(cell_len(line) for line in lines) <= 80
    assert marker in output
    # A narrow Rich table can put one character per physical line.  The
    # issue message must remain a contiguous cell/line instead.
    assert any(marker in line for line in lines)
