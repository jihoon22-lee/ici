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
    ToyPullRequestVerification,
    WorkflowJobVerification,
    WorkflowRunVerification,
    main,
    required_check_pages,
    select_merge_gate,
    select_merge_gate_pages,
    verify_toy_pull_request,
    verify_workflow_job,
    verify_workflow_run,
)

REPOSITORY = "jihoon22-lee/ici"
TARGET_SHA = "a" * 40
TOY_REPOSITORY = "jihoon22-lee/toy-projects"
TOY_TARGET_SHA = "b" * 40


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _check(
    check_id: int,
    *,
    run_id: int = 2001,
    job_id: int = 3001,
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
        "details_url": (f"https://github.com/{repository}/actions/runs/{run_id}/job/{job_id}"),
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


def _job(
    *,
    check_run_id: int = 101,
    job_id: int = 3001,
    run_id: int = 2001,
    run_attempt: int = 1,
) -> dict[str, object]:
    return {
        "id": job_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "name": "Merge Gate",
        "workflow_name": "CI Quality Gate (Dogfooding)",
        "head_branch": "main",
        "head_sha": TARGET_SHA,
        "status": "completed",
        "conclusion": "success",
        "html_url": (f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/job/{job_id}"),
        "url": f"https://api.github.com/repos/{REPOSITORY}/actions/jobs/{job_id}",
        "run_url": f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}",
        "check_run_url": (f"https://api.github.com/repos/{REPOSITORY}/check-runs/{check_run_id}"),
    }


def _toy_pull_request(
    *,
    number: int = 42,
    sha: str = TOY_TARGET_SHA,
    state: str = "open",
    merged_at: object = None,
    base_ref: str = "main",
    base_repository: str = TOY_REPOSITORY,
    head_repository: str = TOY_REPOSITORY,
    base_id: int = 7001,
    head_id: int = 7001,
) -> dict[str, object]:
    return {
        "number": number,
        "state": state,
        "merged_at": merged_at,
        "base": {
            "ref": base_ref,
            "repo": {"full_name": base_repository, "id": base_id},
        },
        "head": {
            "ref": "feature/quality-zoo",
            "sha": sha,
            "repo": {"full_name": head_repository, "id": head_id},
        },
    }


def test_selects_newest_exact_success(tmp_path: Path) -> None:
    path = tmp_path / "checks.json"
    _checks(path, [_check(100, run_id=2000), _check(101)])

    assert select_merge_gate(path, TARGET_SHA, REPOSITORY) == MergeGateSelection(
        check_run_id=101,
        workflow_run_id=2001,
        workflow_job_id=3001,
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


def test_explicit_page_plan_and_selection_are_bounded(tmp_path: Path) -> None:
    first = tmp_path / "page-01.json"
    second = tmp_path / "page-02.json"
    first_entries = [_check(check_id, run_id=2000) for check_id in range(1, 101)]
    _checks(first, first_entries, total=101)
    _checks(second, [_check(101)], total=101)

    assert required_check_pages(first) == 2
    assert select_merge_gate_pages([first, second], TARGET_SHA, REPOSITORY) == MergeGateSelection(
        check_run_id=101,
        workflow_run_id=2001,
        workflow_job_id=3001,
        details_url=f"https://github.com/{REPOSITORY}/actions/runs/2001/job/3001",
    )

    with pytest.raises(CandidateMergeGateError, match="page count"):
        select_merge_gate_pages([first], TARGET_SHA, REPOSITORY)

    _checks(first, first_entries, total=1001)
    with pytest.raises(CandidateMergeGateError, match="too many pages"):
        required_check_pages(first)

    _checks(first, [_check(1)], total=2)
    with pytest.raises(CandidateMergeGateError, match="incomplete"):
        required_check_pages(first)


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
    assert verify_workflow_run(path, TARGET_SHA, REPOSITORY, 2001) == WorkflowRunVerification(
        workflow_run_id=2001,
        run_attempt=1,
        html_url=f"https://github.com/{REPOSITORY}/actions/runs/2001",
    )


def test_verifies_job_binding_for_exact_workflow_attempt(tmp_path: Path) -> None:
    path = tmp_path / "job.json"
    _write(path, _job())

    assert verify_workflow_job(
        path,
        TARGET_SHA,
        REPOSITORY,
        expected_check_run_id=101,
        expected_job_id=3001,
        expected_run_id=2001,
        expected_run_attempt=1,
    ) == WorkflowJobVerification(
        check_run_id=101,
        workflow_job_id=3001,
        workflow_run_id=2001,
        run_attempt=1,
        html_url=f"https://github.com/{REPOSITORY}/actions/runs/2001/job/3001",
    )


def test_verifies_exact_open_same_repository_toy_pull_request(tmp_path: Path) -> None:
    path = tmp_path / "toy-pr.json"
    _write(path, _toy_pull_request())

    assert verify_toy_pull_request(path, 42, TOY_TARGET_SHA, TOY_REPOSITORY) == (
        ToyPullRequestVerification(
            number=42,
            target_sha=TOY_TARGET_SHA,
            repository=TOY_REPOSITORY,
            repository_id=7001,
            base_branch="main",
        )
    )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("number", 43, "number"),
        ("state", "closed", "not open"),
        ("merged_at", "2026-09-04T00:00:00Z", "merged"),
        ("base_ref", "develop", "base branch"),
        ("head_sha", "c" * 40, "head SHA"),
        ("base_repository", "other/toy-projects", "base repository"),
        ("head_repository", "fork/toy-projects", "head repository"),
        ("base_id", 7002, "repository ID"),
        ("head_id", 7002, "repository ID"),
    ],
)
def test_rejects_wrong_toy_pull_request_identity(
    tmp_path: Path, field: str, replacement: object, message: str
) -> None:
    path = tmp_path / "toy-pr.json"
    payload = _toy_pull_request()
    if field in {"number", "state", "merged_at"}:
        payload[field] = replacement
    elif field == "base_ref":
        payload["base"]["ref"] = replacement  # type: ignore[index]
    elif field == "head_sha":
        payload["head"]["sha"] = replacement  # type: ignore[index]
    elif field == "base_repository":
        payload["base"]["repo"]["full_name"] = replacement  # type: ignore[index]
    elif field == "head_repository":
        payload["head"]["repo"]["full_name"] = replacement  # type: ignore[index]
    elif field == "base_id":
        payload["base"]["repo"]["id"] = replacement  # type: ignore[index]
    else:
        payload["head"]["repo"]["id"] = replacement  # type: ignore[index]
    _write(path, payload)

    with pytest.raises(CandidateMergeGateError, match=message):
        verify_toy_pull_request(path, 42, TOY_TARGET_SHA, TOY_REPOSITORY)


def test_toy_pull_request_requires_explicit_unmerged_marker_and_bounded_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "toy-pr.json"
    payload = _toy_pull_request()
    del payload["merged_at"]
    _write(path, payload)
    with pytest.raises(CandidateMergeGateError, match="merged_at"):
        verify_toy_pull_request(path, 42, TOY_TARGET_SHA, TOY_REPOSITORY)

    path.write_text('{"number":42,"number":42}', encoding="utf-8")
    with pytest.raises(CandidateMergeGateError, match="bounded JSON"):
        verify_toy_pull_request(path, 42, TOY_TARGET_SHA, TOY_REPOSITORY)


@pytest.mark.parametrize("side", ["base", "head"])
def test_toy_pull_request_requires_repository_objects(tmp_path: Path, side: str) -> None:
    path = tmp_path / "toy-pr.json"
    payload = _toy_pull_request()
    payload[side]["repo"] = None  # type: ignore[index]
    _write(path, payload)

    with pytest.raises(CandidateMergeGateError, match="repositories are required"):
        verify_toy_pull_request(path, 42, TOY_TARGET_SHA, TOY_REPOSITORY)


def test_toy_pull_request_rejects_noncanonical_target_sha(tmp_path: Path) -> None:
    path = tmp_path / "toy-pr.json"
    _write(path, _toy_pull_request())

    with pytest.raises(CandidateMergeGateError, match="lowercase 40-character"):
        verify_toy_pull_request(path, 42, "B" * 40, TOY_REPOSITORY)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("id", 3002, "job ID"),
        ("run_id", 2002, "run ID"),
        ("run_attempt", 2, "attempt"),
        ("name", "Other", "name"),
        ("workflow_name", "Other", "workflow_name"),
        ("head_branch", "feature", "head_branch"),
        ("head_sha", "b" * 40, "head_sha"),
        ("status", "in_progress", "status"),
        ("conclusion", "failure", "conclusion"),
        (
            "html_url",
            f"https://github.com/{REPOSITORY}/actions/runs/2001/job/3002",
            "html_url",
        ),
        (
            "url",
            f"https://api.github.com/repos/{REPOSITORY}/actions/jobs/3002",
            "url",
        ),
        (
            "run_url",
            f"https://api.github.com/repos/{REPOSITORY}/actions/runs/2002",
            "run_url",
        ),
        (
            "check_run_url",
            f"https://api.github.com/repos/{REPOSITORY}/check-runs/102",
            "check_run_url",
        ),
    ],
)
def test_rejects_wrong_workflow_job_binding(
    tmp_path: Path, field: str, replacement: object, message: str
) -> None:
    path = tmp_path / "job.json"
    payload = _job()
    payload[field] = replacement
    _write(path, payload)

    with pytest.raises(CandidateMergeGateError, match=message):
        verify_workflow_job(path, TARGET_SHA, REPOSITORY, 101, 3001, 2001, 1)


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
    job = tmp_path / "job.json"
    _checks(checks, [_check(100)])
    _write(run, _run())
    _write(job, _job(check_run_id=100))

    output = StringIO()
    with redirect_stdout(output):
        assert main(["select", str(checks), TARGET_SHA, REPOSITORY]) == 0
    assert json.loads(output.getvalue()) == {
        "check_run_id": 100,
        "details_url": f"https://github.com/{REPOSITORY}/actions/runs/2001/job/3001",
        "workflow_job_id": 3001,
        "workflow_run_id": 2001,
    }

    output = StringIO()
    with redirect_stdout(output):
        assert main(["verify", str(run), TARGET_SHA, REPOSITORY, "2001"]) == 0
    assert json.loads(output.getvalue()) == {
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/2001",
        "run_attempt": 1,
        "workflow_run_id": 2001,
    }

    toy_pr = tmp_path / "toy-pr.json"
    _write(toy_pr, _toy_pull_request())
    output = StringIO()
    with redirect_stdout(output):
        assert (
            main(
                [
                    "verify-toy-pr",
                    str(toy_pr),
                    "42",
                    TOY_TARGET_SHA,
                    TOY_REPOSITORY,
                ]
            )
            == 0
        )
    assert json.loads(output.getvalue()) == {
        "base_branch": "main",
        "number": 42,
        "repository": TOY_REPOSITORY,
        "repository_id": 7001,
        "revision_mode": "pull_request",
        "schema_version": "ici.quality-zoo-toy-revision/v1",
        "target_sha": TOY_TARGET_SHA,
    }

    output = StringIO()
    with redirect_stdout(output):
        assert (
            main(
                [
                    "verify-job",
                    str(job),
                    TARGET_SHA,
                    REPOSITORY,
                    "100",
                    "3001",
                    "2001",
                    "1",
                ]
            )
            == 0
        )
    assert json.loads(output.getvalue()) == {
        "check_run_id": 100,
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/2001/job/3001",
        "run_attempt": 1,
        "workflow_job_id": 3001,
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
