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
    details_url: str


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


def _details_url(repository: str, value: object) -> tuple[str, int]:
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
    _positive_id(int(match.group(2)), "workflow job ID")
    return value, run_id


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


def select_merge_gate(path: Path, target_sha: str, repository: str) -> MergeGateSelection:
    """Select the newest exact successful GitHub Actions Merge Gate."""

    _validate_sha(target_sha)
    _validate_repository(repository)
    entries = _flatten_check_pages(_read_json(path, "check-runs response"))
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
    details_url, workflow_run_id = _details_url(repository, selected.get("details_url"))
    return MergeGateSelection(check_id, workflow_run_id, details_url)


def verify_workflow_run(
    path: Path,
    target_sha: str,
    repository: str,
    expected_run_id: int,
) -> str:
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
    _positive_id(value.get("run_attempt"), "workflow run attempt")
    html_url = value.get("html_url")
    canonical = f"https://github.com/{repository}/actions/runs/{run_id}"
    if html_url != canonical:
        raise CandidateMergeGateError("workflow run html_url is not canonical")
    return canonical


def _cli_id(raw: str) -> int:
    if not raw.isascii() or not raw.isdecimal():
        raise argparse.ArgumentTypeError("ID must contain decimal digits")
    value = int(raw)
    if value <= 0 or value > MAX_GITHUB_ID:
        raise argparse.ArgumentTypeError("ID is outside the accepted range")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("check_runs_json", type=Path)
    select.add_argument("target_sha")
    select.add_argument("repository")
    verify = commands.add_parser("verify")
    verify.add_argument("workflow_run_json", type=Path)
    verify.add_argument("target_sha")
    verify.add_argument("repository")
    verify.add_argument("workflow_run_id", type=_cli_id)
    args = parser.parse_args(argv)
    try:
        if args.command == "select":
            chosen = select_merge_gate(args.check_runs_json, args.target_sha, args.repository)
            payload = {
                "details_url": chosen.details_url,
                "workflow_run_id": chosen.workflow_run_id,
            }
        else:
            url = verify_workflow_run(
                args.workflow_run_json,
                args.target_sha,
                args.repository,
                args.workflow_run_id,
            )
            payload = {"html_url": url, "workflow_run_id": args.workflow_run_id}
    except CandidateMergeGateError as exc:
        parser.exit(1, f"candidate Merge Gate audit failed: {exc}\n")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
