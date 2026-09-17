"""Publish a saved result to a configured backend — and only that (#223).

Three rules from SPEC-04 §7 shape this module:

- **The analysis is already over.** ``publish`` reads a stored ``ici.next.run``
  document and the HTML rendered from it; it starts no provider and changes
  no verdict. The verify result file is never rewritten — the outcome lands
  in a separate ``publish.json`` record so a failed upload can be retried
  without re-measuring anything.
- **A stale run cannot clobber a newer one.** The remote path carries the
  head SHA it reports on, so two runs never share a destination; and the
  sticky comment is only updated when this run still names the PR's current
  head and no newer run has already written it.
- **Credentials are ambient, destinations are explicit.** The token comes
  from the environment variable ``token_env`` names — a value in a config
  file would be a leak waiting to be committed. ``api_url``/``server_url``
  must be declared or supplied by the Actions environment; there is no
  github.com default because guessing one is how tokens leave the building.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ici.adapters.ghes import GhesClient, Transport
from ici.config.composition import EffectiveConfig
from ici.domain.enums import PublicationState
from ici.domain.result import RunResult
from ici.domain.serialization import dumps, loads, run_result_to_dict

__all__ = ["PublishError", "PublishOutcome", "publish"]

# Bigger than this and it is not a report — it is a mistake or an attack. The
# Contents API itself rejects >100 MiB; reports are KiB, so 64 MiB is generous.
MAX_PAGE_BYTES = 64 * 1024 * 1024

_COMMENT_MARKER_PREFIX = "<!-- ici-next:"
_META = re.compile(r"head:([0-9a-f]{7,64}) run:(\d+)\.(\d+)")


class PublishError(Exception):
    """A configured publish that could not complete."""


@dataclass(frozen=True)
class PublishOutcome:
    """What publishing did — on its own axis, never touching the verdict."""

    state: PublicationState
    detail: str
    remote_path: str = ""
    viewer_url: str | None = None
    comment_url: str | None = None
    artifact_digest: str = ""
    artifact_bytes: int = 0
    result_digest: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_id": "ici.next.publish",
            "schema_version": 1,
            "state": self.state.value,
            "detail": self.detail,
            "remote_path": self.remote_path,
            "viewer_url": self.viewer_url,
            "comment_url": self.comment_url,
            "artifact": {
                "sha256": self.artifact_digest,
                "bytes": self.artifact_bytes,
            },
            "result_sha256": self.result_digest,
            **self.extra,
        }


def publish(
    root: Path,
    *,
    config: EffectiveConfig,
    result_path: Path,
    page_path: Path,
    env: Mapping[str, str],
    transport: Transport | None = None,
) -> PublishOutcome:
    """Push the rendered page and refresh the sticky comment.

    Returns the outcome for the publication axis; raises ``PublishError``
    only when the request itself is malformed — a configured-but-failed
    upload is data, not an exception.
    """

    result = _load_result(result_path)
    page, digest = _load_artifact(root, page_path)

    target = _target(config, env)
    if target is None:
        return PublishOutcome(
            state=PublicationState.NOT_CONFIGURED,
            detail="no publish destination — set [publish] or run under Actions",
        )
    repo, api_url, server_url, branch, token = target

    client = GhesClient(api_url=api_url, repo=repo, token=token, transport=transport)
    label = _label(config, root)
    pr_number, event_head = _event_context(env)
    run_sha = env.get("GITHUB_SHA", "") or event_head
    run_id = env.get("GITHUB_RUN_ID", "")
    run_attempt = env.get("GITHUB_RUN_ATTEMPT", "1")

    # Ordering guard, part one: if this run's head is not the PR's current
    # head, the report is still uploaded (under its own sha — it cannot
    # overwrite anything) but the sticky comment stays with the newer run.
    stale = False
    if pr_number and run_sha:
        head = client.pr_head_sha(pr_number)
        stale = bool(head) and head != run_sha

    remote_path = _remote_path(label, pr_number, run_sha or "unversioned", env)
    if not client.ensure_branch(branch, env.get("GITHUB_SHA", "")):
        return PublishOutcome(
            state=PublicationState.FAILED,
            detail=f"could not ensure publish branch {branch} on {repo}",
            remote_path=remote_path,
            artifact_digest=digest,
            artifact_bytes=len(page),
        )
    if not client.put_file(branch, remote_path, page, f"ici: publish {remote_path}"):
        return PublishOutcome(
            state=PublicationState.FAILED,
            detail=f"upload of {remote_path} to {repo}:{branch} failed",
            remote_path=remote_path,
            artifact_digest=digest,
            artifact_bytes=len(page),
        )

    site = client.pages_url()
    viewer_url = f"{site.rstrip('/')}/{remote_path}" if site else None

    comment_url = None
    comment_note = ""
    if pr_number:
        if stale:
            comment_note = "comment left alone — a newer head is on the PR"
        else:
            body = _comment_body(
                label,
                result,
                run_sha,
                run_id,
                run_attempt,
                remote_path,
                viewer_url,
                server_url,
                env,
            )
            marker = _marker(label)
            existing = client.find_comment(pr_number, marker)
            if existing is not None and _newer_run_already(existing[1], run_id, run_attempt):
                comment_note = "comment left alone — a newer run already reported"
            else:
                comment_url = client.upsert_comment(
                    existing[0] if existing else None, pr_number, body
                )
                if comment_url is None:
                    return PublishOutcome(
                        state=PublicationState.FAILED,
                        detail="upload succeeded but the PR comment could not be written",
                        remote_path=remote_path,
                        viewer_url=viewer_url,
                        artifact_digest=digest,
                        artifact_bytes=len(page),
                    )

    detail = comment_note or ("published" if viewer_url else "published (Pages is not enabled)")
    return PublishOutcome(
        state=PublicationState.SUCCESS,
        detail=detail,
        remote_path=remote_path,
        viewer_url=viewer_url,
        comment_url=comment_url,
        artifact_digest=digest,
        artifact_bytes=len(page),
        result_digest=_digest(dumps(run_result_to_dict(result)).encode("utf-8")),
        extra={"pr": pr_number, "head": run_sha, "run": f"{run_id}.{run_attempt}"},
    )


def _load_result(path: Path) -> RunResult:
    try:
        return loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PublishError(f"cannot read result {path}: {error}") from error
    except ValueError as error:
        raise PublishError(f"{path} is not a result this version can read: {error}") from error


def _load_artifact(root: Path, path: Path) -> tuple[bytes, str]:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise PublishError(f"publish artifact {path} must live inside the workspace") from None
    if not resolved.is_file():
        raise PublishError(f"no page at {path} — run `ici next report` first")
    payload = resolved.read_bytes()
    if len(payload) > MAX_PAGE_BYTES:
        raise PublishError(
            f"page is {len(payload)} bytes — over the {MAX_PAGE_BYTES} bound, refusing to publish"
        )
    return payload, _digest(payload)


def _target(
    config: EffectiveConfig, env: Mapping[str, str]
) -> tuple[str, str, str, str, str] | None:
    declared = config.publish
    repo = (declared.repo if declared and declared.repo else "") or env.get("GITHUB_REPOSITORY", "")
    api_url = (declared.api_url if declared else "") or env.get("GITHUB_API_URL", "")
    server_url = (declared.server_url if declared else "") or env.get("GITHUB_SERVER_URL", "")
    branch = (declared.branch if declared else "") or env.get("ICI_PUBLISH_BRANCH", "gh-pages")
    token_env = declared.token_env if declared else "GITHUB_TOKEN"
    token = env.get(token_env, "") or env.get("ICI_PUBLISH_TOKEN", "")
    if not repo or not api_url or not token:
        return None
    if not api_url.startswith("https://"):
        raise PublishError(
            f"publish api_url {api_url!r} is not https — the token would travel without TLS"
        )
    return repo, api_url, server_url, branch, token


def _event_context(env: Mapping[str, str]) -> tuple[int | None, str]:
    event_path = env.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        return None, ""
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ""
    pr = payload.get("pull_request")
    if isinstance(pr, dict) and isinstance(pr.get("number"), int):
        head = pr.get("head")
        return pr["number"], head.get("sha", "") if isinstance(head, dict) else ""
    return None, ""


def _label(config: EffectiveConfig, root: Path) -> str:
    name = config.workspace_name.value if config.workspace_name else ""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name or root.name).strip("-").lower()
    return slug or "workspace"


def _remote_path(label: str, pr_number: int | None, sha: str, env: Mapping[str, str]) -> str:
    # The head SHA is in the path on purpose: a delayed rerun of an old run
    # then has its own destination and can never overwrite the report a
    # newer run published for the current head.
    if pr_number:
        return f"ici/{label}/pr-{pr_number}/{sha[:12]}/index.html"
    ref = env.get("GITHUB_REF_NAME", "").replace("/", "-")
    return f"ici/{label}/{ref or 'local'}/{sha[:12]}/index.html"


def _marker(label: str) -> str:
    return f"{_COMMENT_MARKER_PREFIX}{label} -->"


def _newer_run_already(body: str, run_id: str, attempt: str) -> bool:
    """Whether the existing comment already carries a later workflow run.

    The comparison is the ordering contract: a delayed rerun of an older run
    must not overwrite what a newer run wrote. Unparseable metadata means
    we cannot prove we are older, so the update is allowed — the alternative
    would wedge comment updates on any marker-format change.
    """

    meta = _META.search(body)
    if meta is None or not run_id.isdigit():
        return False
    existing_run, existing_attempt = int(meta.group(2)), int(meta.group(3))
    this_run = int(run_id)
    this_attempt = int(attempt) if attempt.isdigit() else 1
    return (existing_run, existing_attempt) > (this_run, this_attempt)


def _comment_body(
    label: str,
    result: RunResult,
    sha: str,
    run_id: str,
    attempt: str,
    remote_path: str,
    viewer_url: str | None,
    server_url: str,
    env: Mapping[str, str],
) -> str:
    verdict = result.gate.selected.value
    lines = [
        _marker(label),
        f"## ici — **{verdict}**",
        "",
    ]
    if result.gate.selected.value == "INCOMPLETE" or result.execution.cancelled:
        lines.append(f"> {_first_reason(result)}")
    if result.findings:
        suppressed = sum(1 for f in result.findings if f.suppression.suppressed)
        suffix = f" ({suppressed} suppressed)" if suppressed else ""
        lines.append(f"{len(result.findings)} finding(s){suffix}")
    else:
        lines.append("no findings recorded")
    if result.baseline is not None and result.baseline.state.value == "comparable":
        delta = result.baseline
        lines.append(
            f"baseline: {len(delta.new)} new, {len(delta.resolved)} resolved, "
            f"{len(delta.carried)} carried"
        )
    lines.append("")
    if viewer_url:
        lines.append(f"report: {viewer_url}")
    else:
        lines.append(f"report uploaded to `{remote_path}` (downloads, not a served page)")
    run_url = _run_url(server_url, env)
    if run_url:
        lines.append(f"run: {run_url}")
    lines.append(f"<!-- head:{sha or 'unknown'} run:{run_id or '0'}.{attempt} -->")
    return "\n".join(lines)


def _first_reason(result: RunResult) -> str:
    if result.gate.reasons:
        return result.gate.reasons[0]
    if result.execution.cancelled:
        return "the run was cancelled"
    return "the run did not finish what it was asked to do"


def _run_url(server_url: str, env: Mapping[str, str]) -> str:
    server = server_url or env.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()
