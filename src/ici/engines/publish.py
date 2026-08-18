"""GitHub HTML report publisher for ici (gh-pages self/hub deployment)."""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ici.core.models import EngineStatus, VerificationSuiteResult

PUBLISH_MARKER = "<!-- ici-report -->"

GITHUB_API_FALLBACK = "https://api.github.com"


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


class ReportPublisher:
    """Publishes the HTML verification report to GitHub and posts a sticky PR comment."""

    def __init__(self, env: dict[str, str] | None = None, project_name: str | None = None):
        self.env = env if env is not None else dict(os.environ)
        self.project_name = project_name

    def publish(self, html_path: Path, suite: VerificationSuiteResult) -> PublishResult:
        """Uploads the HTML report via the Contents API and updates the PR comment."""
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
            )

        uploaded = False
        if self._ensure_branch(api_base, repo, token, branch):
            uploaded = self._put_file(
                api_base, repo, token, branch, remote_path, html_path.read_bytes()
            )
        pages_enabled, site_url = self._check_pages(api_base, repo, token)
        viewer_url = (
            f"{site_url.rstrip('/')}/{remote_path.rsplit('/', 1)[0]}/"
            if pages_enabled and site_url
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
        )

    def _resolve_target(
        self,
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

        remote_path = f"{prefix}/{sub_path}" if prefix else sub_path
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

    def _upsert_comment(
        self, api_base: str, repo: str, token: str, pr_number: int, body: str
    ) -> str | None:
        if not repo or not token:
            return None
        existing_id = None
        status, comments = self._api(
            "GET", f"{api_base}/repos/{repo}/issues/{pr_number}/comments", token
        )
        if status == 200 and isinstance(comments, list):
            for comment in comments:
                if isinstance(comment, dict) and PUBLISH_MARKER in comment.get("body", ""):
                    existing_id = comment.get("id")
                    break

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
        suite: VerificationSuiteResult,
        viewer_url: str | None,
        pages_enabled: bool,
        mode: str,
        remote_path: str,
        run_url: str | None,
        uploaded: bool,
    ) -> str:
        status = suite.suite_status.value
        emoji = "✅" if suite.suite_status == EngineStatus.PASS else "❌"
        tem = (
            f" (TEM **`{suite.tem_score:.2f} / {suite.max_tem_score:.1f}`**)"
            if suite.tem_score is not None
            else ""
        )
        lines = [
            PUBLISH_MARKER,
            f"## {emoji} ici Verification Report — `{status}`{tem}",
            "",
        ]
        if not uploaded:
            lines.append(
                "❌ HTML 리포트 업로드에 실패했습니다. "
                "토큰 권한(`contents: write`)과 Workflow Run 로그를 확인하세요."
            )
        elif viewer_url:
            lines.append(f"🔗 **인터랙티브 HTML 리포트**: [{viewer_url}]({viewer_url})")
        elif pages_enabled:
            lines.append(f"HTML 리포트가 `{remote_path}`에 게시되었습니다.")
        else:
            lines.append(
                f"📦 HTML 리포트가 `{remote_path}`에 푸시되었습니다. "
                "**최초 1회만** Settings → Pages → Source에서 `gh-pages` 브랜치를 선택하면 "
                "이 댓글에 뷰어 링크가 표시됩니다."
            )
        if run_url:
            lines.append(f"⚙️ [Workflow Run]({run_url})")
        lines.append(
            f"\n> 모드: `{mode}` · 엔진 {suite.total_count}개 "
            f"({suite.passed_count} PASS / {suite.warned_count} WARN / {suite.failed_count} FAIL)"
        )
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
        except OSError:
            return -1, None
