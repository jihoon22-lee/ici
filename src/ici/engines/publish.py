"""GitHub HTML report publisher for ici (gh-pages self/hub deployment)."""

import base64
import html
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ici.core.models import (
    BaselineComparison,
    DeltaState,
    EngineResult,
    EngineStatus,
    VerificationSuiteResult,
)
from ici.core.redaction import redact_suite

PUBLISH_MARKER = "<!-- ici-report -->"

GITHUB_API_FALLBACK = "https://api.github.com"

_STATUS_EMOJI = {
    "PASS": "✅",
    "WARN": "⚠️",
    "FAIL": "❌",
    "ERROR": "⛔",
    "SKIP": "⏭️",
}

_BASELINE_COUNT_FIELDS = {
    "new_count": DeltaState.NEW,
    "unchanged_count": DeltaState.UNCHANGED,
    "moved_count": DeltaState.MOVED,
    "resolved_count": DeltaState.RESOLVED,
}
_BASELINE_SUMMARY_FIELDS = {
    *(_BASELINE_COUNT_FIELDS.keys()),
    "regressed_count",
    "gated_count",
    "fail_on_new",
    "gate_failed",
    "warnings",
    "entries",
}


@dataclass
class _LoadedBaselineComparison(BaselineComparison):
    """A lightweight baseline comparison reconstructed from a report summary.

    The loader intentionally does not rebuild every finding delta (the
    publisher only needs the summary). The normal BaselineComparison API
    derives counts from entries, so retain explicit, validated summary counts
    here without manufacturing fake finding rows.
    """

    summary_counts: dict[DeltaState, int | None] = field(
        default_factory=dict, repr=False, compare=False
    )
    summary_regressed_count: int | None = field(default=None, repr=False, compare=False)
    summary_gated_count: int | None = field(default=None, repr=False, compare=False)
    summary_gate_state_present: bool = field(default=False, repr=False, compare=False)

    def count(self, state: DeltaState) -> int:
        value = self.summary_counts.get(state)
        return value if value is not None else super().count(state)

    @property
    def regressed_count(self) -> int:
        if self.summary_regressed_count is not None:
            return self.summary_regressed_count
        return super().regressed_count

    @property
    def gated_count(self) -> int:
        if self.summary_gated_count is not None:
            return self.summary_gated_count
        return super().gated_count


def _strict_nonnegative_int(value: Any) -> int | None:
    """Return a valid count, rejecting booleans, floats, and negatives."""
    if type(value) is not int or value < 0:
        return None
    return value


def _parse_baseline_summary(value: Any) -> BaselineComparison | None:
    """Read an optional v3 baseline summary without trusting malformed data."""
    if value is None or not isinstance(value, dict):
        return None
    if not _BASELINE_SUMMARY_FIELDS.intersection(value):
        return None

    source_path = value.get("source_path", "")
    if not isinstance(source_path, str):
        return None
    warnings_value = value.get("warnings", [])
    if not isinstance(warnings_value, list) or not all(
        isinstance(item, str) for item in warnings_value
    ):
        return None

    fail_on_new = value.get("fail_on_new", False)
    gate_failed = value.get("gate_failed", False)
    if type(fail_on_new) is not bool or type(gate_failed) is not bool:
        return None

    summary_counts: dict[DeltaState, int | None] = {}
    for field_name, state in _BASELINE_COUNT_FIELDS.items():
        if field_name not in value:
            continue
        count = _strict_nonnegative_int(value[field_name])
        if count is None:
            return None
        summary_counts[state] = count

    summary_regressed_count: int | None = None
    if "regressed_count" in value:
        summary_regressed_count = _strict_nonnegative_int(value["regressed_count"])
        if summary_regressed_count is None:
            return None

    summary_gated_count: int | None = None
    if "gated_count" in value:
        summary_gated_count = _strict_nonnegative_int(value["gated_count"])
        if summary_gated_count is None:
            return None

    if "entries" in value:
        entries = value["entries"]
        if not isinstance(entries, list):
            return None
        entry_counts = {state: 0 for state in DeltaState}
        entry_regressed = 0
        entry_gated = 0
        for item in entries:
            if not isinstance(item, dict):
                return None
            try:
                state = DeltaState(item.get("state"))
            except (TypeError, ValueError):
                return None
            regressed = item.get("regressed", False)
            gated = item.get("gated", False)
            if type(regressed) is not bool or type(gated) is not bool:
                return None
            entry_counts[state] += 1
            entry_regressed += int(regressed)
            entry_gated += int(gated)

        for field_name, state in _BASELINE_COUNT_FIELDS.items():
            expected = entry_counts[state]
            if field_name in value and summary_counts.get(state) != expected:
                return None
            summary_counts.setdefault(state, expected)
        if "regressed_count" in value and summary_regressed_count != entry_regressed:
            return None
        if "gated_count" in value and summary_gated_count != entry_gated:
            return None
        if summary_regressed_count is None:
            summary_regressed_count = entry_regressed
        if summary_gated_count is None:
            summary_gated_count = entry_gated

    return _LoadedBaselineComparison(
        source_path=source_path,
        warnings=list(warnings_value),
        fail_on_new=fail_on_new,
        gate_failed=gate_failed,
        summary_counts=summary_counts,
        summary_regressed_count=summary_regressed_count,
        summary_gated_count=summary_gated_count,
        summary_gate_state_present=("fail_on_new" in value or "gate_failed" in value),
    )


def _escape_comment_value(value: str) -> str:
    """Keep report-derived text on one safe Markdown line."""
    compact = " ".join(value.replace("\r", "\n").splitlines())
    return html.escape(compact, quote=False).replace(chr(96), "&#96;").replace("|", "&#124;")


def _baseline_summary_lines(suite: VerificationSuiteResult | None) -> list[str]:
    """Render a compact baseline summary for a single project comment block."""
    comparison = suite.baseline_comparison if suite is not None else None
    if comparison is None:
        return []

    if isinstance(comparison, _LoadedBaselineComparison):

        def summary_count(state: DeltaState) -> str:
            value = comparison.summary_counts.get(state)
            return str(value) if value is not None else "—"

        new_count = summary_count(DeltaState.NEW)
        regressed_count = (
            str(comparison.summary_regressed_count)
            if comparison.summary_regressed_count is not None
            else "—"
        )
        gated_count = (
            str(comparison.summary_gated_count)
            if comparison.summary_gated_count is not None
            else "—"
        )
    else:
        new_count = str(comparison.count(DeltaState.NEW))
        regressed_count = str(comparison.regressed_count)
        gated_count = str(comparison.gated_count)

    if (
        isinstance(comparison, _LoadedBaselineComparison)
        and not comparison.summary_gate_state_present
    ):
        gate = "— UNKNOWN"
    elif comparison.gate_failed:
        gate = "❌ FAILED"
    elif comparison.fail_on_new:
        gate = "✅ PASSED"
    else:
        gate = "NOT ENFORCED"

    lines = [
        f"> 🔎 **Baseline delta**: new **{new_count}** · regressed **{regressed_count}** "
        f"· gated **{gated_count}** · gate **{gate}**"
    ]
    if comparison.warnings:
        first_warning = _escape_comment_value(comparison.warnings[0])
        remaining = len(comparison.warnings) - 1
        suffix = f" (+{remaining} more)" if remaining else ""
        lines.append(
            f"> ⚠️ **Baseline warnings ({len(comparison.warnings)})**: {first_warning}{suffix}"
        )
    return lines


def _header_line(suite: VerificationSuiteResult | None) -> str:
    if suite is None:
        return "## 🛡️ ici Quality Gate Report"
    status = suite.suite_status.value
    emoji = _STATUS_EMOJI.get(status, "📋")
    tem = (
        f" · TEM **`{suite.tem_score:.2f}/{suite.max_tem_score:.1f}`**"
        if suite.tem_score is not None
        else ""
    )
    return f"## {emoji} ici Quality Gate — **`{status}`**{tem}"


def _report_link_lines(
    viewer_url: str | None, pages_enabled: bool, remote_path: str, uploaded: bool
) -> list[str]:
    if not uploaded:
        return [
            "> ❌ **HTML 리포트 업로드 실패** — 토큰 권한(`contents: write`)과 "
            "Workflow Run 로그를 확인해 주세요."
        ]
    if viewer_url:
        return [
            f"[![Open Report](https://img.shields.io/badge/{quote('📊_HTML_리포트_열기')}-38bdf8?style=for-the-badge)]({viewer_url})",
            "",
            f"<sub>뷰어 URL: {viewer_url}</sub>",
        ]
    if pages_enabled:
        return [f"📄 리포트가 `{remote_path}`에 게시되었습니다."]
    return [
        f"> 📦 리포트가 `{remote_path}`(gh-pages)에 푸시되었습니다. "
        "**최초 1회** Settings → Pages → Source에서 `gh-pages`를 선택하면 이 댓글에 뷰어 링크가 표시됩니다."
    ]


def _stats_table(suite: VerificationSuiteResult | None) -> list[str]:
    if suite is None:
        return ["> ⚠️ 스위트 요약 JSON이 제공되지 않아 엔진 상세가 생략되었습니다."]
    failed_count = max(0, suite.failed_count - suite.error_count)
    tem_cell = (
        f"`{suite.tem_score:.2f}/{suite.max_tem_score:.1f}`" if suite.tem_score is not None else "—"
    )
    header = "| ✅ Pass | ⚠️ Warn | ❌ Fail | ⛔ Error | ⏭️ Skip | 🎯 TEM |"
    divider = "|:---:|:---:|:---:|:---:|:---:|:---:|"
    row = (
        f"| {suite.passed_count} | {suite.warned_count} | {failed_count} "
        f"| {suite.error_count} | {suite.skipped_count} | {tem_cell} |"
    )
    return [header, divider, row]


def _engine_details(suite: VerificationSuiteResult | None) -> list[str]:
    if not suite or not suite.results:
        return []
    rows = ["<details>", "<summary><b>🔍 엔진 상세 결과</b></summary>", ""]
    rows.append("| Engine | Status | Summary |")
    rows.append("|---|:---:|---|")
    for result in sorted(suite.results, key=lambda r: _STATUS_EMOJI.get(r.status.value, "z")):
        emoji = _STATUS_EMOJI.get(result.status.value, "📋")
        summary = result.summary.replace("|", "\\|").replace("\n", " ")
        rows.append(f"| `{result.engine_name}` | {emoji} `{result.status.value}` | {summary} |")
    rows.extend(["", "</details>"])
    return rows


@dataclass
class ReportInput:
    """One project's report in a publish run.

    ``label`` is empty for a single-project repository, which keeps the
    published path and the comment exactly as they were before monorepos were
    supported. For a monorepo it is the subproject directory name, and it both
    namespaces the gh-pages path and titles the row in the comment.
    """

    label: str
    html_path: Path
    suite: VerificationSuiteResult | None


@dataclass
class _PublishedReport:
    """Result of uploading one report, used to build the combined comment."""

    label: str
    suite: VerificationSuiteResult | None
    remote_path: str
    viewer_url: str | None
    uploaded: bool


def _status_cell(suite: VerificationSuiteResult | None) -> str:
    if suite is None:
        return "—"
    status = suite.suite_status.value
    return f"{_STATUS_EMOJI.get(status, '📋')} `{status}`"


def _tem_cell(suite: VerificationSuiteResult | None) -> str:
    if suite is None or suite.tem_score is None:
        return "—"
    return f"`{suite.tem_score:.2f}/{suite.max_tem_score:.1f}`"


def _link_cell(report: _PublishedReport) -> str:
    if not report.uploaded:
        return "❌ 업로드 실패"
    if report.viewer_url:
        return f"[📊 열기]({report.viewer_url})"
    return f"`{report.remote_path}`"


def _multi_report_body(
    reports: list[_PublishedReport], mode: str, run_url: str | None, pages_enabled: bool
) -> str:
    """One sticky comment covering every project verified in this run.

    A comment per project would be simpler to produce but would bury the pull
    request under N near-identical blocks, and ici's sticky marker is a single
    fixed string — each publish would overwrite the previous project's comment
    rather than sit beside it.
    """
    worst = max(
        (r.suite.suite_status.value for r in reports if r.suite is not None),
        key=lambda s: (
            ("PASS", "SKIP", "WARN", "FAIL", "ERROR").index(s)
            if s in ("PASS", "SKIP", "WARN", "FAIL", "ERROR")
            else 0
        ),
        default="",
    )
    emoji = _STATUS_EMOJI.get(worst, "📋")
    lines = [
        PUBLISH_MARKER,
        f"## {emoji} ici Quality Gate — {len(reports)}개 프로젝트",
        "",
        "| 프로젝트 | 상태 | TEM | 리포트 |",
        "|---|:---:|:---:|:---:|",
    ]
    for report in reports:
        lines.append(
            f"| `{report.label or '.'}` | {_status_cell(report.suite)} "
            f"| {_tem_cell(report.suite)} | {_link_cell(report)} |"
        )
    if not pages_enabled:
        lines += [
            "",
            "> 📦 리포트가 gh-pages 에 푸시되었습니다. **최초 1회** Settings → Pages → "
            "Source 에서 `gh-pages` 를 선택하면 이 표에 뷰어 링크가 표시됩니다.",
        ]
    for report in reports:
        details = _engine_details(report.suite)
        baseline_lines = _baseline_summary_lines(report.suite)
        if not details and not baseline_lines:
            continue
        lines += ["", f"#### `{report.label or '.'}`", *_stats_table(report.suite)]
        if baseline_lines:
            lines.extend(["", *baseline_lines])
        if details:
            lines.extend(["", *details])
    lines += ["", "---", _footer_line(mode, ", ".join(r.remote_path for r in reports), run_url)]
    return "\n".join(lines)


def _footer_line(mode: str, remote_path: str, run_url: str | None) -> str:
    parts = [f"모드 `{mode}`", f"경로 `{remote_path}`"]
    if run_url:
        parts.append(f"[⚙️ Workflow Run]({run_url})")
    parts.append("<sub>generated by ici</sub>")
    return "<br/>".join(parts)


def load_suite_from_json(json_path: Path) -> VerificationSuiteResult | None:
    """Reconstruct a lightweight suite from a v2/v3 report file."""
    try:
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None

    results = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        try:
            results.append(
                EngineResult(
                    engine_name=str(item.get("engine_name", "?")),
                    status=EngineStatus(str(item.get("status", "SKIP"))),
                    summary=str(item.get("summary", "")),
                    score=item.get("score"),
                    max_score=item.get("max_score"),
                    duration=float(item.get("duration", 0.0)),
                )
            )
        except (ValueError, TypeError):
            continue

    suite_status_raw = str(payload.get("suite_status", "WARN"))
    try:
        suite_status = EngineStatus(suite_status_raw)
    except ValueError:
        suite_status = EngineStatus.WARN

    tem = payload.get("tem_score")
    max_tem = payload.get("max_tem_score", 5.0)
    return VerificationSuiteResult(
        suite_status=suite_status,
        results=results,
        duration=float(payload.get("duration", 0.0)),
        tem_score=float(tem) if isinstance(tem, (int, float)) else None,
        max_tem_score=float(max_tem) if isinstance(max_tem, (int, float)) else 5.0,
        baseline_comparison=_parse_baseline_summary(payload.get("baseline_comparison")),
    )


@dataclass
class PublishResult:
    """Outcome of a report publish attempt."""

    mode: str
    repo: str
    branch: str
    remote_path: str
    viewer_url: str | None
    pages_enabled: bool
    comment_url: str | None
    message: str
    # True for a genuine success *or* an intentional no-op skip (e.g. running
    # locally without GITHUB_ACTIONS). False only for real failures — the
    # report existed and publish was attempted, but the upload itself did not
    # succeed. Callers whose only job is to publish (the `ici publish` CLI
    # command) should treat False as a hard failure.
    success: bool = True


class ReportPublisher:
    """Publishes the HTML verification report to GitHub and posts a sticky PR comment."""

    def __init__(self, env: dict[str, str] | None = None, project_name: str | None = None):
        self.env = env if env is not None else dict(os.environ)
        self.project_name = project_name

    def publish(self, html_path: Path, suite: VerificationSuiteResult) -> PublishResult:
        """Uploads one HTML report and updates the sticky PR comment."""
        return self._publish_single(html_path, redact_suite(suite))

    def publish_many(self, reports: list[ReportInput]) -> PublishResult:
        """Publishes several projects' reports under one sticky comment.

        Uploads run sequentially on purpose. The Contents API needs the current
        blob sha to overwrite a file, so parallel writes to the same branch race
        and lose; doing them in one job keeps that ordering explicit.
        """
        reports = [
            ReportInput(
                label=report.label,
                html_path=report.html_path,
                suite=redact_suite(report.suite) if report.suite is not None else None,
            )
            for report in reports
        ]
        # Route the single unlabeled case back through publish() rather than the
        # private worker: it is the public seam callers and tests already hook.
        if len(reports) == 1 and not reports[0].label:
            return self.publish(reports[0].html_path, reports[0].suite)  # type: ignore[arg-type]
        return self._publish_multi(reports)

    def _publish_single(
        self, html_path: Path, suite: VerificationSuiteResult | None
    ) -> PublishResult:
        if self.env.get("GITHUB_ACTIONS") != "true":
            return PublishResult(
                mode="none",
                repo="",
                branch="",
                remote_path="",
                viewer_url=None,
                pages_enabled=False,
                comment_url=None,
                message="--publish requires a GitHub Actions environment; skipping.",
            )

        target = self._resolve_target()
        if target is None:
            return PublishResult(
                mode="none",
                repo="",
                branch="",
                remote_path="",
                viewer_url=None,
                pages_enabled=False,
                comment_url=None,
                message="--publish skipped: GITHUB_REPOSITORY or a publish token is unavailable.",
            )

        mode, repo, token, branch, remote_path, pr_number, run_url = target
        api_base = self.env.get("GITHUB_API_URL") or GITHUB_API_FALLBACK

        if not html_path.exists():
            return PublishResult(
                mode=mode,
                repo=repo,
                branch=branch,
                remote_path=remote_path,
                viewer_url=None,
                pages_enabled=False,
                comment_url=None,
                message=f"--publish skipped: HTML report not found at {html_path}.",
                success=False,
            )

        uploaded = False
        if self._ensure_branch(api_base, repo, token, branch):
            uploaded = self._put_file(
                api_base, repo, token, branch, remote_path, html_path.read_bytes()
            )
        pages_enabled, site_url = self._check_pages(api_base, repo, token)
        viewer_url = (
            f"{site_url.rstrip('/')}/{remote_path.rsplit('/', 1)[0]}/"
            if pages_enabled and site_url and uploaded
            else None
        )

        body = self._build_comment_body(
            suite, viewer_url, pages_enabled, mode, remote_path, run_url, uploaded
        )
        comment_url = (
            self._upsert_comment(api_base, self._own_repo(), self._own_token(), pr_number, body)
            if pr_number
            else None
        )

        if not uploaded:
            message = (
                f"--publish failed to upload {remote_path} to {repo} ({branch}). "
                "Check token permissions (contents: write) and branch availability."
            )
        elif pr_number and comment_url is None:
            message = (
                f"--publish uploaded {remote_path}, but failed to update the PR comment. "
                "Check token permissions (pull-requests: write)."
            )
        elif viewer_url:
            message = f"HTML report published: {viewer_url}"
        else:
            message = (
                f"HTML report pushed to {repo}:{branch}/{remote_path}. "
                "Pages is not enabled — enable it once: Settings → Pages → Source: gh-pages."
            )

        return PublishResult(
            mode=mode,
            repo=repo,
            branch=branch,
            remote_path=remote_path,
            viewer_url=viewer_url,
            pages_enabled=pages_enabled,
            comment_url=comment_url,
            message=message,
            success=uploaded and (pr_number is None or comment_url is not None),
        )

    def _publish_multi(self, reports: list[ReportInput]) -> PublishResult:
        guard = self._environment_guard()
        if guard is not None:
            return guard
        first = self._resolve_target(reports[0].label)
        if first is None:
            return self._skipped(
                "--publish skipped: GITHUB_REPOSITORY or a publish token is unavailable."
            )

        mode, repo, token, branch, _, pr_number, run_url = first
        api_base = self.env.get("GITHUB_API_URL") or GITHUB_API_FALLBACK
        branch_ready = self._ensure_branch(api_base, repo, token, branch)
        pages_enabled, site_url = self._check_pages(api_base, repo, token)

        published: list[_PublishedReport] = []
        for report in reports:
            target = self._resolve_target(report.label)
            if target is None:
                continue
            remote_path = target[4]
            uploaded = False
            if branch_ready and report.html_path.exists():
                uploaded = self._put_file(
                    api_base, repo, token, branch, remote_path, report.html_path.read_bytes()
                )
            viewer_url = (
                f"{site_url.rstrip('/')}/{remote_path.rsplit('/', 1)[0]}/"
                if pages_enabled and site_url and uploaded
                else None
            )
            published.append(
                _PublishedReport(report.label, report.suite, remote_path, viewer_url, uploaded)
            )

        body = _multi_report_body(published, mode, run_url, pages_enabled)
        comment_url = (
            self._upsert_comment(api_base, self._own_repo(), self._own_token(), pr_number, body)
            if pr_number
            else None
        )

        failures = [r.label for r in published if not r.uploaded]
        comment_failed = pr_number is not None and comment_url is None
        success = not failures and not comment_failed
        if failures:
            message = f"--publish failed to upload: {', '.join(failures)}"
        elif comment_failed:
            message = (
                "--publish uploaded every report, but failed to update the PR comment. "
                "Check token permissions (pull-requests: write)."
            )
        else:
            message = f"Published {len(published)} report(s) to {repo}:{branch}"
        return PublishResult(
            mode=mode,
            repo=repo,
            branch=branch,
            remote_path=", ".join(r.remote_path for r in published),
            viewer_url=next((r.viewer_url for r in published if r.viewer_url), None),
            pages_enabled=pages_enabled,
            comment_url=comment_url,
            message=message,
            success=success,
        )

    def _environment_guard(self) -> PublishResult | None:
        if self.env.get("GITHUB_ACTIONS") != "true":
            return self._skipped("--publish requires a GitHub Actions environment; skipping.")
        return None

    @staticmethod
    def _skipped(message: str) -> PublishResult:
        return PublishResult(
            mode="none",
            repo="",
            branch="",
            remote_path="",
            viewer_url=None,
            pages_enabled=False,
            comment_url=None,
            message=message,
        )

    def _resolve_target(
        self, label: str = ""
    ) -> tuple[str, str, str, str, str, int | None, str | None] | None:
        hub_repo = self.env.get("ICI_PUBLISH_REPO", "").strip()
        hub_token = self.env.get("ICI_PUBLISH_TOKEN", "").strip()
        own_repo = self.env.get("GITHUB_REPOSITORY", "").strip()
        own_token = self.env.get("GITHUB_TOKEN", "").strip()
        branch = self.env.get("ICI_PUBLISH_BRANCH", "").strip() or "gh-pages"

        if hub_repo:
            mode = "hub"
            repo = hub_repo
            token = hub_token or own_token
            prefix = (self.project_name or own_repo.rsplit("/", 1)[-1] or "project").replace(
                "/", "-"
            )
        elif own_repo:
            mode = "self"
            repo = own_repo
            token = own_token
            prefix = ""
        else:
            return None

        if not token:
            return None

        pr_number = self._detect_pr_number()
        ref_name = self.env.get("GITHUB_REF_NAME", "").strip()
        if pr_number:
            sub_path = f"pr/{pr_number}/index.html"
        elif ref_name in ("main", "master"):
            sub_path = "main/index.html"
        elif ref_name:
            sub_path = f"branch/{ref_name.replace('/', '-')}/index.html"
        else:
            sub_path = "index.html"

        # A monorepo publishes several projects into one gh-pages branch, so the
        # label has to namespace the path — without it every project would write
        # to the same pr/<n>/index.html and only the last one would survive.
        remote_path = "/".join(part for part in (prefix, label, sub_path) if part)
        run_url = None
        server_url = self.env.get("GITHUB_SERVER_URL", "").strip()
        run_id = self.env.get("GITHUB_RUN_ID", "").strip()
        if server_url and own_repo and run_id:
            run_url = f"{server_url.rstrip('/')}/{own_repo}/actions/runs/{run_id}"
        return (mode, repo, token, branch, remote_path, pr_number, run_url)

    def _detect_pr_number(self) -> int | None:
        event_path = self.env.get("GITHUB_EVENT_PATH", "").strip()
        if not event_path:
            return None
        try:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        for key in ("pull_request", "issue"):
            nested = payload.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("number"), int):
                return nested["number"]
        return None

    def _own_repo(self) -> str:
        return self.env.get("GITHUB_REPOSITORY", "").strip()

    def _own_token(self) -> str:
        return self.env.get("GITHUB_TOKEN", "").strip()

    def _ensure_branch(self, api_base: str, repo: str, token: str, branch: str) -> bool:
        """Creates the publish branch from GITHUB_SHA if it does not exist yet."""
        ref_path = quote(f"heads/{branch}", safe="")
        status, _ = self._api("GET", f"{api_base}/repos/{repo}/git/ref/{ref_path}", token)
        if status == 200:
            return True

        base_sha: str | None = self.env.get("GITHUB_SHA", "").strip()
        if not base_sha:
            base_sha = self._default_branch_sha(api_base, repo, token)
        if not base_sha:
            return False

        status, _ = self._api(
            "POST",
            f"{api_base}/repos/{repo}/git/refs",
            token,
            {"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        return status in (200, 201)

    def _default_branch_sha(self, api_base: str, repo: str, token: str) -> str | None:
        status, repo_data = self._api("GET", f"{api_base}/repos/{repo}", token)
        default_branch = (
            repo_data.get("default_branch", "main") if isinstance(repo_data, dict) else "main"
        )
        status, ref_data = self._api(
            "GET", f"{api_base}/repos/{repo}/git/ref/heads/{default_branch}", token
        )
        if status == 200 and isinstance(ref_data, dict):
            obj = ref_data.get("object")
            if isinstance(obj, dict):
                return obj.get("sha")
        return None

    def _put_file(
        self, api_base: str, repo: str, token: str, branch: str, path: str, payload: bytes
    ) -> bool:
        url = f"{api_base}/repos/{repo}/contents/{path}"
        body: dict[str, Any] = {
            "message": f"ci: publish ici verification report {path}",
            "content": base64.b64encode(payload).decode("ascii"),
            "branch": branch,
        }
        for attempt in range(3):
            sha = self._get_file_sha(api_base, repo, token, branch, path)
            if sha:
                body["sha"] = sha
            status, _ = self._api("PUT", url, token, body)
            if status in (200, 201):
                return True
            if status != 409:
                return False
            time.sleep(0.5 * (attempt + 1))
        return False

    def _get_file_sha(
        self, api_base: str, repo: str, token: str, branch: str, path: str
    ) -> str | None:
        url = f"{api_base}/repos/{repo}/contents/{path}?ref={branch}"
        status, data = self._api("GET", url, token)
        if status == 200 and isinstance(data, dict):
            return data.get("sha")
        return None

    def _check_pages(self, api_base: str, repo: str, token: str) -> tuple[bool, str | None]:
        status, data = self._api("GET", f"{api_base}/repos/{repo}/pages", token)
        if status == 200 and isinstance(data, dict):
            return True, data.get("html_url")
        return False, None

    def _find_existing_comment(
        self, api_base: str, repo: str, token: str, pr_number: int
    ) -> int | None:
        """Paginates through issue comments looking for the sticky marker.

        GitHub's default page size is 30; a PR with more comments than that
        would otherwise never find its own prior sticky comment and would
        keep posting duplicates on every run instead of updating in place.
        """
        for page in range(1, 21):  # 20 pages * 100 = 2000 comments, a generous cap
            url = f"{api_base}/repos/{repo}/issues/{pr_number}/comments?per_page=100&page={page}"
            status, comments = self._api("GET", url, token)
            if status != 200 or not isinstance(comments, list) or not comments:
                return None
            for comment in comments:
                if isinstance(comment, dict) and PUBLISH_MARKER in (comment.get("body") or ""):
                    return comment.get("id")
            if len(comments) < 100:
                return None
        return None

    def _upsert_comment(
        self, api_base: str, repo: str, token: str, pr_number: int, body: str
    ) -> str | None:
        if not repo or not token:
            return None
        existing_id = self._find_existing_comment(api_base, repo, token, pr_number)

        if existing_id:
            status, data = self._api(
                "PATCH",
                f"{api_base}/repos/{repo}/issues/comments/{existing_id}",
                token,
                {"body": body},
            )
        else:
            status, data = self._api(
                "POST",
                f"{api_base}/repos/{repo}/issues/{pr_number}/comments",
                token,
                {"body": body},
            )

        if status in (200, 201) and isinstance(data, dict):
            return data.get("html_url")
        return None

    def _build_comment_body(
        self,
        suite: VerificationSuiteResult | None,
        viewer_url: str | None,
        pages_enabled: bool,
        mode: str,
        remote_path: str,
        run_url: str | None,
        uploaded: bool,
    ) -> str:
        lines: list[str] = [PUBLISH_MARKER, _header_line(suite), ""]
        lines.extend(_report_link_lines(viewer_url, pages_enabled, remote_path, uploaded))
        lines.append("")
        lines.extend(_stats_table(suite))
        baseline_lines = _baseline_summary_lines(suite)
        if baseline_lines:
            lines.extend(["", *baseline_lines])
        engine_table = _engine_details(suite)
        if engine_table:
            lines.extend(engine_table)
        lines.append("")
        lines.append(_footer_line(mode, remote_path, run_url))
        return "\n".join(lines)

    def _api(
        self, method: str, url: str, token: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "ici-publish")
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            try:
                return err.code, json.loads(raw)
            except ValueError:
                return err.code, raw
        except OSError as err:
            # Network/DNS/TLS failure — surface it instead of silently
            # returning an empty body, so a real cause is visible if a
            # caller ever inspects it (and in worst case shows up as a
            # generic-but-present reason rather than nothing at all).
            return -1, {"error": str(err)}
