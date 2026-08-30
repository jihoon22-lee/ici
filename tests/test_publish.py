"""Tests for the GitHub HTML report publisher (engines/publish.py)."""

import json
from pathlib import Path

import pytest

from ici.core.models import EngineResult, EngineStatus, VerificationSuiteResult
from ici.engines.publish import PUBLISH_MARKER, ReportInput, ReportPublisher


class FakeGitHubApi:
    """Simulates the GitHub REST endpoints used by ReportPublisher."""

    def __init__(
        self,
        pages_enabled: bool = True,
        existing_comment_id: int | None = None,
        fail_put: bool = False,
        fail_comment: bool = False,
    ):
        self.pages_enabled = pages_enabled
        self.existing_comment_id = existing_comment_id
        self.fail_put = fail_put
        self.fail_comment = fail_comment
        self.calls: list[tuple[str, str, dict | None]] = []
        self.put_count = 0
        self.patch_count = 0
        self.post_count = 0
        self.refs_post_count = 0

    def __call__(self, method: str, url: str, token: str, payload: dict | None = None):
        self.calls.append((method, url, payload))
        if method == "GET" and url.endswith("/pages"):
            if not self.pages_enabled:
                return 404, None
            return 200, {"html_url": "https://pages.example/org/repo"}
        if method == "GET" and "/contents/" in url:
            return 404, None
        if method == "GET" and "/git/ref/" in url:
            return 404, None
        if method == "POST" and url.endswith("/git/refs"):
            self.refs_post_count += 1
            return 201, {"ref": "refs/heads/gh-pages"}
        if method == "PUT":
            self.put_count += 1
            if self.fail_put:
                return 403, {"message": "Resource not accessible by integration"}
            return 201, {"content": {}}
        if method == "GET" and "/comments" in url:
            if self.existing_comment_id:
                return 200, [
                    {"id": self.existing_comment_id, "body": f"{PUBLISH_MARKER} old report"}
                ]
            return 200, []
        if method == "PATCH":
            self.patch_count += 1
            if self.fail_comment:
                return 403, {"message": "Resource not accessible by integration"}
            return 200, {"html_url": "https://ghes.example/org/my-proj/issues/42#issuecomment-1"}
        if method == "POST" and "/comments" in url:
            self.post_count += 1
            if self.fail_comment:
                return 403, {"message": "Resource not accessible by integration"}
            return 201, {"html_url": "https://ghes.example/org/my-proj/issues/42#issuecomment-2"}
        return 404, None


def make_suite() -> VerificationSuiteResult:
    res = EngineResult(
        engine_name="test",
        status=EngineStatus.PASS,
        summary="All tests pass",
        score=4.5,
        max_score=5.0,
    )
    return VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[res],
        duration=1.0,
        tem_score=4.5,
        max_tem_score=5.0,
    )


def gh_env(tmp_path: Path, pr_number: int = 42) -> dict[str, str]:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"number": pr_number}}), encoding="utf-8")
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "org/my-proj",
        "GITHUB_TOKEN": "gh-token",
        "GITHUB_API_URL": "https://api.example",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_REF_NAME": f"{pr_number}/merge",
        "GITHUB_SERVER_URL": "https://ghes.example",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_SHA": "abc1234",
    }


def test_publish_requires_github_actions(tmp_path: Path):
    html = tmp_path / "verify_report.html"
    html.write_text("<html></html>", encoding="utf-8")
    result = ReportPublisher(env={}).publish(html, make_suite())
    assert result.mode == "none"
    assert "GitHub Actions" in result.message


def test_self_mode_target_resolution(tmp_path: Path):
    target = ReportPublisher(env=gh_env(tmp_path))._resolve_target()
    assert target is not None
    mode, repo, token, branch, path, pr, run_url = target
    assert mode == "self"
    assert repo == "org/my-proj"
    assert token == "gh-token"
    assert branch == "gh-pages"
    assert path == "pr/42/index.html"
    assert pr == 42
    assert run_url == "https://ghes.example/org/my-proj/actions/runs/123456"


def test_hub_mode_target_resolution(tmp_path: Path):
    env = gh_env(tmp_path)
    env["ICI_PUBLISH_REPO"] = "org/ci-reports"
    env["ICI_PUBLISH_TOKEN"] = "hub-token"
    target = ReportPublisher(env=env, project_name="proj-a")._resolve_target()
    assert target is not None
    mode, repo, token, branch, path, pr, _ = target
    assert mode == "hub"
    assert repo == "org/ci-reports"
    assert token == "hub-token"
    assert branch == "gh-pages"
    assert path == "proj-a/pr/42/index.html"
    assert pr == 42


def test_main_branch_target_resolution(tmp_path: Path):
    env = gh_env(tmp_path)
    env["GITHUB_REF_NAME"] = "main"
    env.pop("GITHUB_EVENT_PATH")
    target = ReportPublisher(env=env)._resolve_target()
    assert target is not None
    mode, _, _, _, path, pr, _ = target
    assert mode == "self"
    assert path == "main/index.html"
    assert pr is None


def test_publish_flow_with_pages_and_new_comment(tmp_path: Path, monkeypatch):
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    fake = FakeGitHubApi(pages_enabled=True)
    monkeypatch.setattr(ReportPublisher, "_api", fake)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())
    assert fake.refs_post_count == 1
    refs_payload = next(p for m, u, p in fake.calls if m == "POST" and "/git/refs" in u)
    assert refs_payload is not None
    assert refs_payload["sha"] == "abc1234"
    assert fake.put_count == 1
    assert fake.post_count == 1
    assert fake.patch_count == 0
    assert result.pages_enabled is True
    assert result.viewer_url == "https://pages.example/org/repo/pr/42/"
    assert result.comment_url is not None
    post_payload = next(p for m, u, p in fake.calls if m == "POST" and "/comments" in u)
    assert post_payload is not None
    post_body = post_payload["body"]
    assert PUBLISH_MARKER in post_body
    assert result.viewer_url and result.viewer_url in post_body
    assert "PASS" in post_body


def test_publish_flow_pages_disabled_guidance(tmp_path: Path, monkeypatch):
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    fake = FakeGitHubApi(pages_enabled=False)
    monkeypatch.setattr(ReportPublisher, "_api", fake)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())
    assert result.pages_enabled is False
    assert result.viewer_url is None
    assert "Pages" in result.message
    post_payload = next(p for m, u, p in fake.calls if m == "POST" and "/comments" in u)
    assert post_payload is not None
    post_body = post_payload["body"]
    assert "gh-pages" in post_body


def test_sticky_comment_updates_existing(tmp_path: Path, monkeypatch):
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    fake = FakeGitHubApi(pages_enabled=True, existing_comment_id=77)
    monkeypatch.setattr(ReportPublisher, "_api", fake)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())
    assert fake.patch_count == 1
    assert fake.post_count == 0
    assert result.comment_url is not None


def test_publish_skips_missing_html(tmp_path: Path, monkeypatch):
    fake = FakeGitHubApi(pages_enabled=True)
    monkeypatch.setattr(ReportPublisher, "_api", fake)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(tmp_path / "missing.html", make_suite())
    assert "not found" in result.message
    assert fake.put_count == 0
    assert result.success is False


def test_publish_flow_success_marks_result_success_true(tmp_path: Path, monkeypatch):
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    fake = FakeGitHubApi(pages_enabled=True)
    monkeypatch.setattr(ReportPublisher, "_api", fake)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())
    assert result.success is True


def test_pr_comment_failure_makes_single_publish_fail(tmp_path: Path, monkeypatch):
    """A PR publish is incomplete until its sticky comment can be discovered."""
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    fake = FakeGitHubApi(pages_enabled=True, fail_comment=True)
    monkeypatch.setattr(ReportPublisher, "_api", fake)

    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())

    assert result.viewer_url == "https://pages.example/org/repo/pr/42/"
    assert result.comment_url is None
    assert result.success is False
    assert "comment" in result.message.lower()


def test_upload_failure_reflected_in_comment(tmp_path: Path, monkeypatch):
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    fake = FakeGitHubApi(pages_enabled=True, fail_put=True)
    monkeypatch.setattr(ReportPublisher, "_api", fake)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())
    assert "failed" in result.message
    assert result.success is False
    post_payload = next(p for m, u, p in fake.calls if m == "POST" and "/comments" in u)
    assert post_payload is not None
    assert "업로드 실패" in post_payload["body"]
    assert result.viewer_url is None


def test_comment_body_modern_layout(tmp_path: Path, monkeypatch):
    """New comment layout: badge link, stats table, collapsible engine details."""
    fake = FakeGitHubApi(existing_comment_id=777)
    monkeypatch.setattr("ici.engines.publish.ReportPublisher._api", fake)
    suite = make_suite()
    html_path = tmp_path / "report.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    result = ReportPublisher(env=gh_env(tmp_path), project_name="my-proj").publish(html_path, suite)

    assert result.comment_url is not None
    patch_payload = next(c for c in fake.calls if c[0] == "PATCH")[2]
    body = patch_payload["body"]
    assert PUBLISH_MARKER in body
    assert "## ✅ ici Quality Gate — **`PASS`**" in body
    assert "TEM" in body and "`4.50/5.0`" in body
    assert "for-the-badge" in body  # shield badge linking to viewer
    assert "<details>" in body and "</details>" in body
    assert "| `test` |" in body  # engine row present


def test_load_suite_from_json_roundtrip(tmp_path: Path):
    from ici.engines.publish import load_suite_from_json
    from ici.reporters.json_rep import save_json_report

    suite = make_suite()
    out = tmp_path / "verify_report.json"
    save_json_report(suite, out)
    loaded = load_suite_from_json(out)
    assert loaded is not None
    assert loaded.suite_status == EngineStatus.PASS
    assert loaded.tem_score == pytest.approx(4.5)
    assert loaded.results[0].engine_name == "test"


def test_publish_command_uses_saved_json(tmp_path: Path, monkeypatch):
    """The standalone publish command feeds the saved JSON into the publisher."""
    from typer.testing import CliRunner

    from ici.__main__ import app
    from ici.reporters.json_rep import save_json_report

    suite = make_suite()
    out = tmp_path / "verify_report.json"
    save_json_report(suite, out)

    captured = {}

    def fake_publish(self, html_path, suite_arg):
        captured["suite"] = suite_arg
        from ici.engines.publish import PublishResult

        return PublishResult(
            mode="none",
            repo="",
            branch="",
            remote_path="",
            viewer_url=None,
            pages_enabled=False,
            comment_url=None,
            message="skipped",
        )

    monkeypatch.setattr("ici.engines.publish.ReportPublisher.publish", fake_publish)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["publish", "--html", "r.html", "--json", str(out)],
        env={"GITHUB_ACTIONS": ""},
    )
    assert result.exit_code == 0, result.output
    assert captured["suite"] is not None
    assert captured["suite"].results[0].engine_name == "test"


def test_publish_command_exits_nonzero_on_real_failure(tmp_path: Path, monkeypatch):
    """`ici publish`'s only job is to publish — a real failure must not exit 0."""
    from typer.testing import CliRunner

    from ici.__main__ import app

    def fake_publish(self, html_path, suite_arg):
        from ici.engines.publish import PublishResult

        return PublishResult(
            mode="self",
            repo="org/repo",
            branch="gh-pages",
            remote_path="index.html",
            viewer_url=None,
            pages_enabled=False,
            comment_url=None,
            message="--publish failed to upload index.html to org/repo (gh-pages).",
            success=False,
        )

    monkeypatch.setattr("ici.engines.publish.ReportPublisher.publish", fake_publish)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["publish", "--html", "r.html", "--json", str(tmp_path / "missing.json")],
        env={"GITHUB_ACTIONS": "true"},
    )
    assert result.exit_code != 0


def test_sticky_comment_finds_marker_past_first_page(tmp_path: Path, monkeypatch):
    """The sticky comment must be found even behind >100 unrelated comments."""
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")

    filler_page = [{"id": i, "body": f"unrelated comment {i}"} for i in range(100)]
    marker_page = [{"id": 999, "body": f"{PUBLISH_MARKER} old report"}]
    calls: list[tuple[str, str, dict | None]] = []

    def paged_api(self, method: str, url: str, token: str, payload: dict | None = None):
        calls.append((method, url, payload))
        if method == "GET" and url.endswith("/pages"):
            return 200, {"html_url": "https://pages.example/org/repo"}
        if method == "GET" and "/contents/" in url:
            return 404, None
        if method == "GET" and "/git/ref/" in url:
            return 404, None
        if method == "POST" and url.endswith("/git/refs"):
            return 201, {"ref": "refs/heads/gh-pages"}
        if method == "PUT":
            return 201, {"content": {}}
        if method == "GET" and "/comments" in url:
            return (200, filler_page) if url.endswith("page=1") else (200, marker_page)
        if method == "PATCH":
            return 200, {"html_url": "https://ghes.example/org/my-proj/issues/42#issuecomment-1"}
        return 404, None

    monkeypatch.setattr(ReportPublisher, "_api", paged_api)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())
    assert result.comment_url == "https://ghes.example/org/my-proj/issues/42#issuecomment-1"
    patch_calls = [c for c in calls if c[0] == "PATCH"]
    assert len(patch_calls) == 1
    post_calls = [c for c in calls if c[0] == "POST" and "/comments" in c[1]]
    assert not post_calls  # updated in place, never created a duplicate


def _monorepo_env() -> dict:
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "owner/monorepo",
        "GITHUB_TOKEN": "t0ken",
        "GITHUB_REF_NAME": "feature",
        "GITHUB_EVENT_NAME": "pull_request",
    }


def test_report_dir_namespaces_the_published_path(tmp_path: Path):
    """Without a label every project would write to the same pr/<n>/index.html."""
    publisher = ReportPublisher(env=_monorepo_env())
    first = publisher._resolve_target("diskmap")
    second = publisher._resolve_target("loglens")
    assert first is not None and second is not None
    assert first[4] != second[4]
    assert first[4].startswith("branch/feature/diskmap/") or "diskmap" in first[4]
    assert "loglens" in second[4]


def test_unlabeled_target_keeps_the_original_path(tmp_path: Path):
    """A single-project repository must publish exactly where it always did."""
    publisher = ReportPublisher(env=_monorepo_env())
    target = publisher._resolve_target()
    assert target is not None
    assert "diskmap" not in target[4]
    assert target[4].endswith("index.html")


def test_publish_many_delegates_single_unlabeled_report(tmp_path: Path, monkeypatch):
    """The one-report path stays on publish(), which callers and tests hook."""
    seen = {}

    def fake_publish(self, html_path, suite):
        seen["html"] = html_path
        return "sentinel"

    monkeypatch.setattr(ReportPublisher, "publish", fake_publish)
    html = tmp_path / "verify_report.html"
    html.write_text("<html></html>", encoding="utf-8")
    result = ReportPublisher(env=_monorepo_env()).publish_many([ReportInput("", html, None)])
    assert result == "sentinel"
    assert seen["html"] == html


def test_multi_report_comment_lists_every_project(tmp_path: Path, monkeypatch):
    """One sticky comment, one row per project — not one comment per project."""
    posted = {}

    def fake_put_file(self, api_base, repo, token, branch, remote_path, payload):
        posted.setdefault("paths", []).append(remote_path)
        return True

    def fake_upsert(self, api_base, repo, token, pr_number, body):
        posted["body"] = body
        return "https://example.invalid/comment"

    monkeypatch.setattr(ReportPublisher, "_ensure_branch", lambda *a, **k: True)
    monkeypatch.setattr(ReportPublisher, "_check_pages", lambda *a, **k: (True, "https://pages/"))
    monkeypatch.setattr(ReportPublisher, "_put_file", fake_put_file)
    monkeypatch.setattr(ReportPublisher, "_upsert_comment", fake_upsert)

    reports = []
    for name in ("diskmap", "loglens"):
        html = tmp_path / f"{name}.html"
        html.write_text("<html></html>", encoding="utf-8")
        reports.append(ReportInput(name, html, make_suite()))

    result = ReportPublisher(env=gh_env(tmp_path)).publish_many(reports)

    assert result.success
    # Distinct destinations, so neither project overwrites the other.
    assert len(set(posted["paths"])) == 2
    body = posted["body"]
    assert body.count(PUBLISH_MARKER) == 1
    assert "diskmap" in body and "loglens" in body
    assert "https://pages/diskmap/pr/42/" in body
    assert "https://pages/loglens/pr/42/" in body
    assert "diskmap/pr/42/index.html, loglens/pr/42/index.html" in body


def test_multi_report_comment_failure_makes_publish_fail(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ReportPublisher, "_ensure_branch", lambda *a, **k: True)
    monkeypatch.setattr(ReportPublisher, "_check_pages", lambda *a, **k: (True, "https://pages/"))
    monkeypatch.setattr(ReportPublisher, "_put_file", lambda *a, **k: True)
    monkeypatch.setattr(ReportPublisher, "_upsert_comment", lambda *a, **k: None)
    reports = []
    for name in ("diskmap", "loglens"):
        html = tmp_path / f"{name}.html"
        html.write_text("<html></html>", encoding="utf-8")
        reports.append(ReportInput(name, html, make_suite()))

    result = ReportPublisher(env=gh_env(tmp_path)).publish_many(reports)

    assert result.comment_url is None
    assert result.success is False
    assert "comment" in result.message.lower()


def test_multi_report_reports_upload_failure(tmp_path: Path, monkeypatch):
    """A missing HTML must surface as a failure, not a quietly successful run."""
    monkeypatch.setattr(ReportPublisher, "_ensure_branch", lambda *a, **k: True)
    monkeypatch.setattr(ReportPublisher, "_check_pages", lambda *a, **k: (False, None))
    monkeypatch.setattr(ReportPublisher, "_put_file", lambda *a, **k: True)
    monkeypatch.setattr(ReportPublisher, "_upsert_comment", lambda *a, **k: None)

    present = tmp_path / "there.html"
    present.write_text("<html></html>", encoding="utf-8")
    reports = [
        ReportInput("here", present, make_suite()),
        ReportInput("gone", tmp_path / "missing.html", None),
    ]
    result = ReportPublisher(env=_monorepo_env()).publish_many(reports)
    assert not result.success
    assert "gone" in result.message
