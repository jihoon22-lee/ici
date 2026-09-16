"""A GitHub/GHES REST client for publishing results (#223).

Deliberately small: the operations a publish needs and nothing more. Every
call goes through :meth:`GhesClient.api`, which uses ``urllib`` — the system
CA bundle, per the no-``requests`` rule in AGENTS §3 — so a GHES host with an
internal CA works wherever the machine already trusts it.

The client is constructed with an explicit ``api_url``: github.com is a valid
value, not a default. Tests inject ``transport`` — a callable taking
``(method, url, token, payload)`` and returning ``(status, body)`` — so the
ordering and failure contract is exercised without a network.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

__all__ = ["GhesClient"]

Transport = Callable[[str, str, str, dict[str, Any] | None], tuple[int, Any]]

_API_VERSION = "2022-11-28"


class GhesClient:
    """The five endpoints publishing uses, against one repo."""

    def __init__(
        self,
        *,
        api_url: str,
        repo: str,
        token: str,
        transport: Transport | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._repo = repo
        self._token = token
        self._transport = transport or _urllib_transport

    def api(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        return self._transport(
            method, f"{self._api_url}/repos/{self._repo}{path}", self._token, payload
        )

    def ensure_branch(self, branch: str, base_sha: str) -> bool:
        """Create the publish branch from ``base_sha`` if it does not exist."""

        status, _ = self.api("GET", f"/git/ref/{quote(f'heads/{branch}', safe='')}")
        if status == 200:
            return True
        if not base_sha:
            status, repo = self.api("GET", "")
            default = repo.get("default_branch", "main") if isinstance(repo, dict) else "main"
            status, ref = self.api("GET", f"/git/ref/heads/{default}")
            if status == 200 and isinstance(ref, dict):
                obj = ref.get("object")
                base_sha = obj.get("sha", "") if isinstance(obj, dict) else ""
        if not base_sha:
            return False
        status, _ = self.api("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})
        return status in (200, 201)

    def put_file(self, branch: str, path: str, payload: bytes, message: str) -> bool:
        """Write one file on the publish branch, retrying on a sha race."""

        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(payload).decode("ascii"),
            "branch": branch,
        }
        for attempt in range(3):
            status, data = self.api("GET", f"/contents/{path}?ref={branch}")
            if status == 200 and isinstance(data, dict) and data.get("sha"):
                body["sha"] = data["sha"]
            status, _ = self.api("PUT", f"/contents/{path}", body)
            if status in (200, 201):
                return True
            if status != 409:
                return False
            time.sleep(0.5 * (attempt + 1))
        return False

    def pages_url(self) -> str | None:
        """The Pages base URL when the repo serves one, else ``None``."""

        status, data = self.api("GET", "/pages")
        if status == 200 and isinstance(data, dict):
            return data.get("html_url")
        return None

    def pr_head_sha(self, pr_number: int) -> str | None:
        status, data = self.api("GET", f"/pulls/{pr_number}")
        if status == 200 and isinstance(data, dict):
            head = data.get("head")
            if isinstance(head, dict):
                return head.get("sha")
        return None

    def find_comment(self, pr_number: int, marker: str) -> tuple[int, str] | None:
        """The sticky comment's id and body, paginating until the end."""

        for page in range(1, 21):  # 20 pages * 100 = 2000 comments, a generous cap
            status, comments = self.api(
                "GET", f"/issues/{pr_number}/comments?per_page=100&page={page}"
            )
            if status != 200 or not isinstance(comments, list) or not comments:
                return None
            for comment in comments:
                if isinstance(comment, dict) and marker in (comment.get("body") or ""):
                    return comment.get("id"), comment.get("body") or ""
            if len(comments) < 100:
                return None
        return None

    def upsert_comment(self, comment_id: int | None, pr_number: int, body: str) -> str | None:
        if comment_id is not None:
            status, data = self.api("PATCH", f"/issues/comments/{comment_id}", {"body": body})
        else:
            status, data = self.api("POST", f"/issues/{pr_number}/comments", {"body": body})
        if status in (200, 201) and isinstance(data, dict):
            return data.get("html_url")
        return None


def _urllib_transport(
    method: str, url: str, token: str, payload: dict[str, Any] | None
) -> tuple[int, Any]:
    request = urllib.request.Request(url, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", _API_VERSION)
    request.add_header("User-Agent", "ici-next-publish")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data=data, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, raw
    except OSError as error:
        # A network/DNS/TLS failure is a publish failure, not silence — the
        # caller reports it on the publication axis, where a retry is safe.
        return -1, {"error": str(error)}
