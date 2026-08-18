"""Tests for the GitHub HTML report publisher (engines/publish.py)."""

import json
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, VerificationSuiteResult
from ici.engines.publish import PUBLISH_MARKER, ReportPublisher


class FakeGitHubApi:
    """Simulates the GitHub REST endpoints used by ReportPublisher."""

    def __init__(
        self,
        pages_enabled: bool = True,
        existing_comment_id: int | None = None,
        fail_put: bool = False,
    ):
        self.pages_enabled = pages_enabled
        self.existing_comment_id = existing_comment_id
        self.fail_put = fail_put
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
            return 200, {"html_url": "https://ghes.example/org/my-proj/issues/42#issuecomment-1"}
        if method == "POST" and "/comments" in url:
            self.post_count += 1
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


def test_upload_failure_reflected_in_comment(tmp_path: Path, monkeypatch):
    html = tmp_path / "verify_report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    fake = FakeGitHubApi(pages_enabled=True, fail_put=True)
    monkeypatch.setattr(ReportPublisher, "_api", fake)
    result = ReportPublisher(env=gh_env(tmp_path)).publish(html, make_suite())
    assert "failed" in result.message
    post_payload = next(p for m, u, p in fake.calls if m == "POST" and "/comments" in u)
    assert post_payload is not None
    assert "업로드에 실패" in post_payload["body"]
    assert result.viewer_url not in post_payload["body"]
