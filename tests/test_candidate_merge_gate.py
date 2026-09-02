"""Tests for the fail-closed candidate Merge Gate provenance audit."""

from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from candidate_merge_gate import (  # noqa: E402
    MAX_CHECK_RUN_PAGES,
    MAX_JSON_BYTES,
    CandidateMergeGateError,
    MergeGateSelection,
    main,
    select_merge_gate,
    verify_workflow_run,
)

REPOSITORY = "jihoon22-lee/ici"
TARGET_SHA = "a" * 40


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _check(
    check_id: int,
    *,
    run_id: int = 2001,
    sha: str = TARGET_SHA,
    app: str = "github-actions",
    status: str = "completed",
    conclusion: str | None = "success",
    repository: str = REPOSITORY,
) -> dict[str, object]:
    return {
        "id": check_id,
        "name": "Merge Gate",
        "head_sha": sha,
        "app": {"slug": app},
        "status": status,
        "conclusion": conclusion,
        "details_url": (f"https://github.com/{repository}/actions/runs/{run_id}/job/3001"),
    }


def _checks(path: Path, entries: list[dict[str, object]], total: int | None = None) -> None:
    _write(
        path,
        {
            "total_count": len(entries) if total is None else total,
            "check_runs": entries,
        },
    )


def _run(run_id: int = 2001) -> dict[str, object]:
    return {
        "id": run_id,
        "name": "CI Quality Gate (Dogfooding)",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": TARGET_SHA,
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
    }


def test_selects_newest_exact_success(tmp_path: Path) -> None:
    path = tmp_path / "checks.json"
    _checks(path, [_check(100, run_id=2000), _check(101)])

    assert select_merge_gate(path, TARGET_SHA, REPOSITORY) == MergeGateSelection(
        check_run_id=101,
        workflow_run_id=2001,
        details_url=f"https://github.com/{REPOSITORY}/actions/runs/2001/job/3001",
    )


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [("in_progress", None), ("completed", "failure")],
)
def test_newest_non_success_never_falls_back(
    tmp_path: Path, status: str, conclusion: str | None
) -> None:
    path = tmp_path / "checks.json"
    _checks(
        path,
        [
            _check(100, run_id=2000),
            _check(101, status=status, conclusion=conclusion),
        ],
    )

    with pytest.raises(CandidateMergeGateError, match="newest exact"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)


def test_spoofed_app_and_wrong_sha_are_ineligible(tmp_path: Path) -> None:
    path = tmp_path / "checks.json"
    _checks(
        path,
        [
            _check(999, app="other-app"),
            _check(998, sha="b" * 40),
            _check(100),
        ],
    )
    assert select_merge_gate(path, TARGET_SHA, REPOSITORY).check_run_id == 100

    _checks(path, [_check(999, app="other-app"), _check(998, sha="b" * 40)])
    with pytest.raises(CandidateMergeGateError, match="no exact"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)


def test_paginated_response_must_be_complete_and_unique(tmp_path: Path) -> None:
    path = tmp_path / "checks.json"
    _write(
        path,
        [
            {"total_count": 2, "check_runs": [_check(100, run_id=2000)]},
            {"total_count": 2, "check_runs": [_check(101)]},
        ],
    )
    assert select_merge_gate(path, TARGET_SHA, REPOSITORY).check_run_id == 101

    _write(path, [{"total_count": 2, "check_runs": [_check(100)]}])
    with pytest.raises(CandidateMergeGateError, match="incomplete"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)

    duplicate = _check(100)
    _write(
        path,
        [
            {"total_count": 2, "check_runs": [duplicate]},
            {"total_count": 2, "check_runs": [duplicate]},
        ],
    )
    with pytest.raises(CandidateMergeGateError, match="duplicate"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)

    _write(
        path,
        [{"total_count": 0, "check_runs": []}] * (MAX_CHECK_RUN_PAGES + 1),
    )
    with pytest.raises(CandidateMergeGateError, match="1 to 10"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)


def test_check_response_rejects_bad_url_counts_and_shape(tmp_path: Path) -> None:
    path = tmp_path / "checks.json"
    bad = _check(100)
    bad["details_url"] = f"https://github.com/{REPOSITORY}/actions/runs/2001"
    _checks(path, [bad])
    with pytest.raises(CandidateMergeGateError, match="details_url"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)

    _checks(path, [_check(100)], total=2)
    with pytest.raises(CandidateMergeGateError, match="incomplete"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)

    _write(path, [])
    with pytest.raises(CandidateMergeGateError, match="1 to 10"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)


def test_bounded_json_rejects_duplicate_key_oversize_symlink_and_fifo(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checks.json"
    path.write_text('{"total_count":0,"total_count":0,"check_runs":[]}', encoding="utf-8")
    with pytest.raises(CandidateMergeGateError, match="bounded JSON"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)

    path.write_bytes(b"x" * (MAX_JSON_BYTES + 1))
    with pytest.raises(CandidateMergeGateError, match="size"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)

    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW unavailable")
    source = tmp_path / "source.json"
    _checks(source, [_check(100)])
    path.unlink()
    path.symlink_to(source)
    with pytest.raises(CandidateMergeGateError, match="opened safely"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(CandidateMergeGateError, match="regular file"):
        select_merge_gate(path, TARGET_SHA, REPOSITORY)


def test_verifies_exact_canonical_main_workflow(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    _write(path, _run())
    assert verify_workflow_run(path, TARGET_SHA, REPOSITORY, 2001) == (
        f"https://github.com/{REPOSITORY}/actions/runs/2001"
    )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("id", 2002, "ID"),
        ("name", "Other", "name"),
        ("path", ".github/workflows/other.yml", "path"),
        ("event", "pull_request", "event"),
        ("head_branch", "feature", "head_branch"),
        ("head_sha", "b" * 40, "head_sha"),
        ("status", "in_progress", "status"),
        ("conclusion", "failure", "conclusion"),
        ("run_attempt", 0, "attempt"),
        (
            "html_url",
            f"https://github.com/{REPOSITORY}/actions/runs/2001?stale=1",
            "html_url",
        ),
        ("repository", {"full_name": "other/repo"}, "repository"),
        ("head_repository", {"full_name": "other/repo"}, "head_repository"),
    ],
)
def test_rejects_wrong_workflow_identity(
    tmp_path: Path, field: str, replacement: object, message: str
) -> None:
    path = tmp_path / "run.json"
    payload = _run()
    payload[field] = replacement
    _write(path, payload)
    with pytest.raises(CandidateMergeGateError, match=message):
        verify_workflow_run(path, TARGET_SHA, REPOSITORY, 2001)


@pytest.mark.parametrize(
    ("sha", "repository"),
    [
        ("A" * 40, REPOSITORY),
        ("a" * 39, REPOSITORY),
        (TARGET_SHA, "owner"),
        (TARGET_SHA, "owner/repo/extra"),
        (TARGET_SHA, "owner name/repo"),
    ],
)
def test_rejects_unsafe_identity_inputs(tmp_path: Path, sha: str, repository: str) -> None:
    path = tmp_path / "checks.json"
    _checks(path, [_check(100)])
    with pytest.raises(CandidateMergeGateError):
        select_merge_gate(path, sha, repository)


def test_cli_emits_bounded_json_and_fails_without_success_output(tmp_path: Path) -> None:
    checks = tmp_path / "checks.json"
    run = tmp_path / "run.json"
    _checks(checks, [_check(100)])
    _write(run, _run())

    output = StringIO()
    with redirect_stdout(output):
        assert main(["select", str(checks), TARGET_SHA, REPOSITORY]) == 0
    assert json.loads(output.getvalue()) == {
        "details_url": f"https://github.com/{REPOSITORY}/actions/runs/2001/job/3001",
        "workflow_run_id": 2001,
    }

    output = StringIO()
    with redirect_stdout(output):
        assert main(["verify", str(run), TARGET_SHA, REPOSITORY, "2001"]) == 0
    assert json.loads(output.getvalue()) == {
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/2001",
        "workflow_run_id": 2001,
    }

    _checks(checks, [_check(101, status="in_progress", conclusion=None)])
    output = StringIO()
    error = StringIO()
    with (
        redirect_stdout(output),
        redirect_stderr(error),
        pytest.raises(SystemExit) as raised,
    ):
        main(["select", str(checks), TARGET_SHA, REPOSITORY])
    assert raised.value.code == 1
    assert output.getvalue() == ""
    assert "audit failed" in error.getvalue()
