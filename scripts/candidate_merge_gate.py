#!/usr/bin/env python3
"""Validate the exact successful ici Merge Gate behind a candidate build.

The candidate workflow receives independent Checks and Actions API responses.
This module selects the newest exact ``Merge Gate`` check for one commit and
then proves that its details URL belongs to the canonical successful main-push
workflow.  It deliberately refuses to fall back to an older successful check.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 20_000_000
MAX_JSON_NODES = 100_000
MAX_JSON_DEPTH = 100
MAX_CHECK_RUN_PAGES = 10
MAX_CHECK_RUNS_PER_PAGE = 100
MAX_GITHUB_ID = (1 << 63) - 1
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_URL_ID = r"[1-9][0-9]{0,18}"


class CandidateMergeGateError(ValueError):
    """A check response or workflow run violates the candidate contract."""


@dataclass(frozen=True)
class MergeGateSelection:
    """Identity selected from the newest eligible Merge Gate check-run."""

    check_run_id: int
    workflow_run_id: int
    workflow_job_id: int
    details_url: str


@dataclass(frozen=True)
class WorkflowRunVerification:
    """Canonical successful workflow identity verified independently."""

    workflow_run_id: int
    run_attempt: int
    html_url: str


@dataclass(frozen=True)
class WorkflowJobVerification:
    """Canonical job identity tying a check-run to one workflow attempt."""

    check_run_id: int
    workflow_job_id: int
    workflow_run_id: int
    run_attempt: int
    html_url: str


def _bounded_int(raw: str) -> int:
    if len(raw.lstrip("-")) > 19:
        raise ValueError("JSON integer exceeds 19 decimal digits")
    return int(raw)


def _bounded_float(raw: str) -> float:
    if len(raw) > 100:
        raise ValueError("JSON float exceeds 100 characters")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("JSON float must be finite")
    return value


def _reject_constant(raw: str) -> None:
    raise ValueError(f"non-standard JSON constant: {raw}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_json(path: Path, label: str) -> Any:
    """Read a bounded regular file without following its final component."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CandidateMergeGateError("O_NOFOLLOW is required for candidate audits")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CandidateMergeGateError(f"{label} cannot be opened safely: {exc}") from exc

    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise CandidateMergeGateError(f"{label} must be a regular file")
        if not 0 < initial.st_size <= MAX_JSON_BYTES:
            raise CandidateMergeGateError(
                f"{label} size is outside the accepted range: {initial.st_size}"
            )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_JSON_BYTES + 1)
            final = os.fstat(stream.fileno())
        named = path.stat(follow_symlinks=False)
        if _signature(initial) != _signature(final) or _signature(initial) != _signature(named):
            raise CandidateMergeGateError(f"{label} changed while it was read")
    except CandidateMergeGateError:
        raise
    except OSError as exc:
        raise CandidateMergeGateError(f"{label} cannot be read safely: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(payload) > MAX_JSON_BYTES:
        raise CandidateMergeGateError(f"{label} exceeds the read bound")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_int=_bounded_int,
            parse_float=_bounded_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        MemoryError,
        RecursionError,
    ) as exc:
        raise CandidateMergeGateError(f"{label} is not valid bounded JSON: {exc}") from exc
    _bound_shape(value, label)
    return value


def _bound_shape(value: Any, label: str) -> None:
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise CandidateMergeGateError(f"{label} contains too many values")
        if depth > MAX_JSON_DEPTH:
            raise CandidateMergeGateError(f"{label} is nested too deeply")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _validate_sha(target_sha: str) -> None:
    if SHA_PATTERN.fullmatch(target_sha) is None:
        raise CandidateMergeGateError("target SHA must be a full lowercase 40-character commit SHA")


def _validate_repository(repository: str) -> None:
    if len(repository.encode("utf-8")) > 200 or REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise CandidateMergeGateError("repository must be an unescaped owner/name pair")


def _positive_id(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > MAX_GITHUB_ID:
        raise CandidateMergeGateError(f"{label} must be a positive GitHub ID")
    return value


def _details_url(repository: str, value: object) -> tuple[str, int, int]:
    if not isinstance(value, str):
        raise CandidateMergeGateError("Merge Gate details_url must be a string")
    pattern = re.compile(
        rf"^https://github\.com/{re.escape(repository)}/actions/runs/"
        rf"({_URL_ID})/job/({_URL_ID})$"
    )
    match = pattern.fullmatch(value)
    if match is None:
        raise CandidateMergeGateError("Merge Gate details_url is not canonical")
    run_id = _positive_id(int(match.group(1)), "workflow run ID")
    job_id = _positive_id(int(match.group(2)), "workflow job ID")
    return value, run_id, job_id


def _flatten_check_pages(value: Any) -> list[dict[str, Any]]:
    pages = [value] if isinstance(value, dict) else value
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_CHECK_RUN_PAGES:
        raise CandidateMergeGateError("check-runs response must contain 1 to 10 pages")
    expected_total: int | None = None
    entries: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise CandidateMergeGateError(f"check-runs page {page_index} must be an object")
        total = page.get("total_count")
        runs = page.get("check_runs")
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or not isinstance(runs, list)
            or len(runs) > MAX_CHECK_RUNS_PER_PAGE
        ):
            raise CandidateMergeGateError(f"check-runs page {page_index} has invalid counts")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise CandidateMergeGateError("check-runs pages disagree on total_count")
        for entry_index, item in enumerate(runs):
            if not isinstance(item, dict):
                raise CandidateMergeGateError(
                    f"check-run {page_index}:{entry_index} must be an object"
                )
            check_id = _positive_id(item.get("id"), "check-run ID")
            if check_id in seen_ids:
                raise CandidateMergeGateError(f"duplicate check-run ID: {check_id}")
            seen_ids.add(check_id)
            entries.append(item)
    if expected_total is None or len(entries) != expected_total:
        raise CandidateMergeGateError(
            f"check-runs response is incomplete: {len(entries)} != {expected_total}"
        )
    return entries


def _select_merge_gate_value(value: Any, target_sha: str, repository: str) -> MergeGateSelection:
    _validate_sha(target_sha)
    _validate_repository(repository)
    entries = _flatten_check_pages(value)
    candidates: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        app = entry.get("app")
        if (
            entry.get("name") == "Merge Gate"
            and entry.get("head_sha") == target_sha
            and isinstance(app, dict)
            and app.get("slug") == "github-actions"
        ):
            candidates.append((_positive_id(entry.get("id"), "check-run ID"), entry))
    if not candidates:
        raise CandidateMergeGateError("no exact GitHub Actions Merge Gate was found")
    check_id, selected = max(candidates, key=lambda item: item[0])
    if selected.get("status") != "completed" or selected.get("conclusion") != "success":
        raise CandidateMergeGateError(
            "newest exact Merge Gate is not completed successfully; "
            f"status={selected.get('status')!r} conclusion={selected.get('conclusion')!r}"
        )
    details_url, workflow_run_id, workflow_job_id = _details_url(
        repository, selected.get("details_url")
    )
    return MergeGateSelection(check_id, workflow_run_id, workflow_job_id, details_url)


def required_check_pages(path: Path) -> int:
    """Return a bounded exact page count from the first Checks API page."""

    value = _read_json(path, "first check-runs page")
    if not isinstance(value, dict):
        raise CandidateMergeGateError("first check-runs page must be an object")
    total = value.get("total_count")
    runs = value.get("check_runs")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or not isinstance(runs, list)
        or len(runs) > MAX_CHECK_RUNS_PER_PAGE
    ):
        raise CandidateMergeGateError("first check-runs page has invalid counts")
    expected_first_count = min(total, MAX_CHECK_RUNS_PER_PAGE)
    if len(runs) != expected_first_count:
        raise CandidateMergeGateError(
            f"first check-runs page is incomplete: {len(runs)} != {expected_first_count}"
        )
    page_count = max(
        1,
        (total + MAX_CHECK_RUNS_PER_PAGE - 1) // MAX_CHECK_RUNS_PER_PAGE,
    )
    if page_count > MAX_CHECK_RUN_PAGES:
        raise CandidateMergeGateError(f"check-runs response needs too many pages: {page_count}")
    return page_count


def select_merge_gate(path: Path, target_sha: str, repository: str) -> MergeGateSelection:
    """Select the newest exact successful GitHub Actions Merge Gate."""

    return _select_merge_gate_value(_read_json(path, "check-runs response"), target_sha, repository)


def select_merge_gate_pages(
    paths: Sequence[Path], target_sha: str, repository: str
) -> MergeGateSelection:
    """Select from an explicitly bounded set of individual API pages."""

    if not 1 <= len(paths) <= MAX_CHECK_RUN_PAGES:
        raise CandidateMergeGateError("check-runs response must contain 1 to 10 pages")
    values = [
        _read_json(path, f"check-runs page {index}") for index, path in enumerate(paths, start=1)
    ]
    first = values[0]
    if not isinstance(first, dict):
        raise CandidateMergeGateError("first check-runs page must be an object")
    total = first.get("total_count")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise CandidateMergeGateError("first check-runs page has invalid total_count")
    expected_pages = max(
        1,
        (total + MAX_CHECK_RUNS_PER_PAGE - 1) // MAX_CHECK_RUNS_PER_PAGE,
    )
    if len(values) != expected_pages:
        raise CandidateMergeGateError(
            f"check-runs page count is incomplete: {len(values)} != {expected_pages}"
        )
    return _select_merge_gate_value(values, target_sha, repository)


def verify_workflow_run(
    path: Path,
    target_sha: str,
    repository: str,
    expected_run_id: int,
) -> WorkflowRunVerification:
    """Verify the independent canonical main-push workflow response."""

    _validate_sha(target_sha)
    _validate_repository(repository)
    run_id = _positive_id(expected_run_id, "expected workflow run ID")
    value = _read_json(path, "workflow-run response")
    if not isinstance(value, dict):
        raise CandidateMergeGateError("workflow-run response must be an object")
    if _positive_id(value.get("id"), "workflow run ID") != run_id:
        raise CandidateMergeGateError("workflow run ID does not match")
    for field in ("repository", "head_repository"):
        item = value.get(field)
        if not isinstance(item, dict) or item.get("full_name") != repository:
            raise CandidateMergeGateError(f"workflow run {field} does not match")
    expected = {
        "name": "CI Quality Gate (Dogfooding)",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": target_sha,
        "status": "completed",
        "conclusion": "success",
    }
    for field, wanted in expected.items():
        if value.get(field) != wanted:
            raise CandidateMergeGateError(
                f"workflow run {field} mismatch: {value.get(field)!r} != {wanted!r}"
            )
    run_attempt = _positive_id(value.get("run_attempt"), "workflow run attempt")
    html_url = value.get("html_url")
    canonical = f"https://github.com/{repository}/actions/runs/{run_id}"
    if html_url != canonical:
        raise CandidateMergeGateError("workflow run html_url is not canonical")
    return WorkflowRunVerification(run_id, run_attempt, canonical)


def verify_workflow_job(
    path: Path,
    target_sha: str,
    repository: str,
    expected_check_run_id: int,
    expected_job_id: int,
    expected_run_id: int,
    expected_run_attempt: int,
) -> WorkflowJobVerification:
    """Bind the selected check-run to one exact successful workflow job attempt."""

    _validate_sha(target_sha)
    _validate_repository(repository)
    check_run_id = _positive_id(expected_check_run_id, "expected check-run ID")
    job_id = _positive_id(expected_job_id, "expected workflow job ID")
    run_id = _positive_id(expected_run_id, "expected workflow run ID")
    run_attempt = _positive_id(expected_run_attempt, "expected workflow run attempt")
    value = _read_json(path, "workflow-job response")
    if not isinstance(value, dict):
        raise CandidateMergeGateError("workflow-job response must be an object")

    identifiers = {
        "id": (job_id, "workflow job ID"),
        "run_id": (run_id, "workflow job run ID"),
        "run_attempt": (run_attempt, "workflow job run attempt"),
    }
    for field, (wanted, label) in identifiers.items():
        if _positive_id(value.get(field), label) != wanted:
            raise CandidateMergeGateError(f"{label} does not match")
    expected = {
        "name": "Merge Gate",
        "workflow_name": "CI Quality Gate (Dogfooding)",
        "head_branch": "main",
        "head_sha": target_sha,
        "status": "completed",
        "conclusion": "success",
    }
    for field, wanted in expected.items():
        if value.get(field) != wanted:
            raise CandidateMergeGateError(
                f"workflow job {field} mismatch: {value.get(field)!r} != {wanted!r}"
            )

    html_url = f"https://github.com/{repository}/actions/runs/{run_id}/job/{job_id}"
    api_url = f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}"
    run_url = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}"
    check_run_url = f"https://api.github.com/repos/{repository}/check-runs/{check_run_id}"
    expected_urls = {
        "html_url": html_url,
        "url": api_url,
        "run_url": run_url,
        "check_run_url": check_run_url,
    }
    for field, wanted in expected_urls.items():
        if value.get(field) != wanted:
            raise CandidateMergeGateError(f"workflow job {field} is not canonical")
    return WorkflowJobVerification(check_run_id, job_id, run_id, run_attempt, html_url)


def _cli_id(raw: str) -> int:
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
        raise argparse.ArgumentTypeError("ID must contain at most 19 decimal digits")
    value = int(raw)
    if value <= 0 or value > MAX_GITHUB_ID:
        raise argparse.ArgumentTypeError("ID is outside the accepted range")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    page_count = commands.add_parser("page-count")
    page_count.add_argument("first_check_runs_page", type=Path)
    select = commands.add_parser("select")
    select.add_argument("check_runs_json", type=Path)
    select.add_argument("target_sha")
    select.add_argument("repository")
    select_pages = commands.add_parser("select-pages")
    select_pages.add_argument("target_sha")
    select_pages.add_argument("repository")
    select_pages.add_argument("check_runs_pages", nargs="+", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("workflow_run_json", type=Path)
    verify.add_argument("target_sha")
    verify.add_argument("repository")
    verify.add_argument("workflow_run_id", type=_cli_id)
    verify_job = commands.add_parser("verify-job")
    verify_job.add_argument("workflow_job_json", type=Path)
    verify_job.add_argument("target_sha")
    verify_job.add_argument("repository")
    verify_job.add_argument("check_run_id", type=_cli_id)
    verify_job.add_argument("workflow_job_id", type=_cli_id)
    verify_job.add_argument("workflow_run_id", type=_cli_id)
    verify_job.add_argument("workflow_run_attempt", type=_cli_id)
    args = parser.parse_args(argv)
    try:
        if args.command == "page-count":
            payload = {"page_count": required_check_pages(args.first_check_runs_page)}
        elif args.command == "select":
            chosen = select_merge_gate(args.check_runs_json, args.target_sha, args.repository)
            payload = {
                "check_run_id": chosen.check_run_id,
                "details_url": chosen.details_url,
                "workflow_job_id": chosen.workflow_job_id,
                "workflow_run_id": chosen.workflow_run_id,
            }
        elif args.command == "select-pages":
            chosen = select_merge_gate_pages(
                args.check_runs_pages, args.target_sha, args.repository
            )
            payload = {
                "check_run_id": chosen.check_run_id,
                "details_url": chosen.details_url,
                "workflow_job_id": chosen.workflow_job_id,
                "workflow_run_id": chosen.workflow_run_id,
            }
        elif args.command == "verify":
            verified = verify_workflow_run(
                args.workflow_run_json,
                args.target_sha,
                args.repository,
                args.workflow_run_id,
            )
            payload = {
                "html_url": verified.html_url,
                "run_attempt": verified.run_attempt,
                "workflow_run_id": verified.workflow_run_id,
            }
        else:
            verified_job = verify_workflow_job(
                args.workflow_job_json,
                args.target_sha,
                args.repository,
                args.check_run_id,
                args.workflow_job_id,
                args.workflow_run_id,
                args.workflow_run_attempt,
            )
            payload = {
                "check_run_id": verified_job.check_run_id,
                "html_url": verified_job.html_url,
                "run_attempt": verified_job.run_attempt,
                "workflow_job_id": verified_job.workflow_job_id,
                "workflow_run_id": verified_job.workflow_run_id,
            }
    except CandidateMergeGateError as exc:
        parser.exit(1, f"candidate Merge Gate audit failed: {exc}\n")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
