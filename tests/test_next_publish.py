"""``next publish`` — the credential job that only moves stored bytes (#223).

The contract under test is the one SPEC-04 §7 writes down: publishing is a
separate application that validates the saved result and the artifact before
any network call, a stale run cannot overwrite a newer head's comment, and
the outcome lives on the publication axis — the verify result file is never
rewritten.

Every test drives ``application.publish.publish`` (or the CLI) with an
injected transport — a dict-shaped fake of the GHES REST surface — so the
ordering and failure contract runs with no network at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.application.publish import PublishError, publish
from ici.config.composition import compose
from ici.config.errors import NextConfigError
from ici.config.schema import read_root
from ici.domain.enums import PublicationState
from ici.domain.serialization import dumps, run_result_to_dict
from test_next_serialization import minimal_result

runner = CliRunner()

ROOT_CONFIG = """\
schema_version = 1
[workspace]
name = "ws"
[publish]
repo = "org/repo"
api_url = "https://ghes.internal/api/v3"
server_url = "https://ghes.internal"
"""

HEAD = "a" * 40


class FakeGhes:
    """A dict-shaped GHES — enough of the API surface to exercise ordering."""

    def __init__(self, *, head: str = HEAD, pages: bool = True):
        self.head = head
        self.pages = pages
        self.branches: set[str] = {"main"}
        self.files: dict[str, bytes] = {}
        self.comments: dict[int, str] = {}
        self.comment_seq = 0
        self.calls: list[str] = []

    def __call__(self, method, url, token, payload):
        self.calls.append(f"{method} {url}")
        assert token == "secret-token", "the named env var's value must travel"
        if method == "GET" and url.endswith("/git/ref/heads%2Fgh-pages"):
            return (200, {}) if "gh-pages" in self.branches else (404, {})
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "0" * 40}}
        if method == "POST" and url.endswith("/git/refs"):
            self.branches.add(payload["ref"].split("/")[-1])
            return 201, {}
        if "/contents/" in url and method == "GET":
            path = url.split("/contents/")[1].split("?")[0]
            if path in self.files:
                return 200, {"sha": "blob-" + path}
            return 404, {}
        if "/contents/" in url and method == "PUT":
            import base64

            path = url.split("/contents/")[1]
            self.files[path] = base64.b64decode(payload["content"])
            return 201, {}
        if url.endswith("/pages"):
            return (
                (200, {"html_url": "https://ghes.internal/pages/org/repo"})
                if self.pages
                else (404, {})
            )
        if method == "GET" and "/pulls/" in url:
            return 200, {"head": {"sha": self.head}}
        if method == "GET" and "/comments" in url:
            body = [
                {"id": cid, "body": text, "html_url": f"https://ghes.internal/c/{cid}"}
                for cid, text in self.comments.items()
            ]
            return 200, body
        if method == "PATCH" and "/issues/comments/" in url:
            cid = int(url.rsplit("/", 1)[1])
            self.comments[cid] = payload["body"]
            return 200, {"html_url": f"https://ghes.internal/c/{cid}"}
        if method == "POST" and url.endswith("/comments"):
            self.comment_seq += 1
            self.comments[self.comment_seq] = payload["body"]
            return 201, {"html_url": f"https://ghes.internal/c/{self.comment_seq}"}
        return 404, {}


def _workspace(tmp_path: Path, config: str = ROOT_CONFIG) -> None:
    (tmp_path / "ici.toml").write_text(config, encoding="utf-8")
    out = tmp_path / ".ici" / "next"
    out.mkdir(parents=True)
    (out / "result.json").write_text(dumps(run_result_to_dict(minimal_result())), encoding="utf-8")
    (out / "result.html").write_text("<html>report</html>", encoding="utf-8")


def _config(tmp_path: Path):
    return compose(read_root((tmp_path / "ici.toml").read_text(), path="ici.toml"))


def _env(tmp_path: Path, *, pr: int = 7, head: str = HEAD, run: str = "42") -> dict[str, str]:
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": pr, "head": {"sha": head}}}),
        encoding="utf-8",
    )
    return {
        "GITHUB_TOKEN": "secret-token",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": head,
        "GITHUB_RUN_ID": run,
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://ghes.internal",
        "GITHUB_REPOSITORY": "org/repo",
    }


# --- configuration ---------------------------------------------------------


def test_publish_config_is_parsed() -> None:
    config = compose(read_root(ROOT_CONFIG, path="ici.toml"))

    publish_cfg = config.publish
    assert publish_cfg is not None
    assert publish_cfg.repo == "org/repo"
    assert publish_cfg.api_url == "https://ghes.internal/api/v3"
    assert publish_cfg.branch == "gh-pages"
    assert publish_cfg.token_env == "GITHUB_TOKEN"


def test_a_literal_token_key_is_refused() -> None:
    with pytest.raises(NextConfigError, match="token"):
        read_root(ROOT_CONFIG + 'token = "hunter2"\n', path="ici.toml")


def test_an_insecure_api_url_is_refused() -> None:
    config = 'schema_version = 1\n[workspace]\n[publish]\napi_url = "http://x/api"\n'
    with pytest.raises(NextConfigError, match="https"):
        read_root(config, path="ici.toml")


def test_a_malformed_repo_is_refused() -> None:
    config = 'schema_version = 1\n[workspace]\n[publish]\nrepo = "no-slash"\n'
    with pytest.raises(NextConfigError, match="owner/name"):
        read_root(config, path="ici.toml")


# --- input validation ------------------------------------------------------


def test_a_missing_result_is_an_input_error(tmp_path: Path) -> None:
    _workspace(tmp_path)
    (tmp_path / ".ici" / "next" / "result.json").unlink()

    with pytest.raises(PublishError, match="cannot read result"):
        publish(
            tmp_path,
            config=_config(tmp_path),
            result_path=tmp_path / ".ici/next/result.json",
            page_path=tmp_path / ".ici/next/result.html",
            env=_env(tmp_path),
            transport=FakeGhes(),
        )


def test_a_v3_document_is_not_a_publishable_result(tmp_path: Path) -> None:
    _workspace(tmp_path)
    (tmp_path / ".ici" / "next" / "result.json").write_text(
        json.dumps({"schema_version": "ici.result/v3", "results": []}), encoding="utf-8"
    )

    with pytest.raises(PublishError, match="not a result"):
        publish(
            tmp_path,
            config=_config(tmp_path),
            result_path=tmp_path / ".ici/next/result.json",
            page_path=tmp_path / ".ici/next/result.html",
            env=_env(tmp_path),
            transport=FakeGhes(),
        )


def test_an_artifact_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    _workspace(tmp_path)
    outside = tmp_path.parent / "outside.html"
    outside.write_text("<html></html>", encoding="utf-8")

    with pytest.raises(PublishError, match="inside the workspace"):
        publish(
            tmp_path,
            config=_config(tmp_path),
            result_path=tmp_path / ".ici/next/result.json",
            page_path=outside,
            env=_env(tmp_path),
            transport=FakeGhes(),
        )


def test_an_oversized_artifact_is_refused(tmp_path: Path, monkeypatch) -> None:
    _workspace(tmp_path)
    page = tmp_path / ".ici" / "next" / "result.html"
    monkeypatch.setattr("ici.application.publish.MAX_PAGE_BYTES", 10)
    page.write_text("<html>1234567890</html>", encoding="utf-8")

    with pytest.raises(PublishError, match="bound"):
        publish(
            tmp_path,
            config=_config(tmp_path),
            result_path=tmp_path / ".ici" / "next" / "result.json",
            page_path=page,
            env=_env(tmp_path),
            transport=FakeGhes(),
        )


def test_validation_runs_before_any_network_call(tmp_path: Path) -> None:
    _workspace(tmp_path)
    outside = tmp_path.parent / "x.html"
    outside.write_text("x", encoding="utf-8")
    ghes = FakeGhes()

    with pytest.raises(PublishError):
        publish(
            tmp_path,
            config=_config(tmp_path),
            result_path=tmp_path / ".ici/next/result.json",
            page_path=outside,
            env=_env(tmp_path),
            transport=ghes,
        )

    assert ghes.calls == [], "a refused artifact must not reach the network"


# --- the happy path --------------------------------------------------------


def test_a_publish_uploads_the_page_and_updates_one_comment(tmp_path: Path) -> None:
    _workspace(tmp_path)
    ghes = FakeGhes()

    outcome = publish(
        tmp_path,
        config=_config(tmp_path),
        result_path=tmp_path / ".ici/next/result.json",
        page_path=tmp_path / ".ici/next/result.html",
        env=_env(tmp_path),
        transport=ghes,
    )

    assert outcome.state is PublicationState.SUCCESS
    assert outcome.comment_url == "https://ghes.internal/c/1"
    assert HEAD[:12] in outcome.remote_path
    assert ghes.files[outcome.remote_path] == b"<html>report</html>"
    assert len(ghes.comments) == 1


def test_a_second_run_updates_the_same_comment(tmp_path: Path) -> None:
    _workspace(tmp_path)
    ghes = FakeGhes()

    for run in ("42", "43"):
        publish(
            tmp_path,
            config=_config(tmp_path),
            result_path=tmp_path / ".ici" / "next" / "result.json",
            page_path=tmp_path / ".ici" / "next" / "result.html",
            env=_env(tmp_path, run=run),
            transport=ghes,
        )

    assert len(ghes.comments) == 1, "rerun must update, not duplicate"
    assert "run:43.1" in ghes.comments[1]


def test_a_stale_head_does_not_take_the_comment(tmp_path: Path) -> None:
    _workspace(tmp_path)
    ghes = FakeGhes(head="b" * 40)  # the PR moved on since this run started

    outcome = publish(
        tmp_path,
        config=_config(tmp_path),
        result_path=tmp_path / ".ici/next/result.json",
        page_path=tmp_path / ".ici/next/result.html",
        env=_env(tmp_path, head=HEAD),
        transport=ghes,
    )

    assert outcome.state is PublicationState.SUCCESS
    assert "newer head" in outcome.detail
    assert ghes.comments == {}, "a stale run must not write the sticky comment"
    # ...but its report still lands, under its own head so nothing is lost.
    assert any(HEAD[:12] in path for path in ghes.files)


def test_an_out_of_order_attempt_does_not_overwrite_a_newer_run(tmp_path: Path) -> None:
    _workspace(tmp_path)
    ghes = FakeGhes()
    publish(
        tmp_path,
        config=_config(tmp_path),
        result_path=tmp_path / ".ici" / "next" / "result.json",
        page_path=tmp_path / ".ici" / "next" / "result.html",
        env=_env(tmp_path, run="43"),
        transport=ghes,
    )

    outcome = publish(
        tmp_path,
        config=_config(tmp_path),
        result_path=tmp_path / ".ici" / "next" / "result.json",
        page_path=tmp_path / ".ici" / "next" / "result.html",
        env=_env(tmp_path, run="42"),  # the older run finishes late
        transport=ghes,
    )

    assert outcome.state is PublicationState.SUCCESS
    assert "newer run" in outcome.detail
    assert "run:43.1" in ghes.comments[1]


# --- the publication axis --------------------------------------------------


def test_a_publish_failure_is_failed_not_silent(tmp_path: Path) -> None:
    _workspace(tmp_path)
    ghes = FakeGhes()

    def broken(method, url, token, payload):
        if "/contents/" in url and method == "PUT":
            return 500, {"error": "nope"}
        return ghes(method, url, token, payload)

    outcome = publish(
        tmp_path,
        config=_config(tmp_path),
        result_path=tmp_path / ".ici" / "next" / "result.json",
        page_path=tmp_path / ".ici" / "next" / "result.html",
        env=_env(tmp_path),
        transport=broken,
    )

    assert outcome.state is PublicationState.FAILED
    assert "failed" in outcome.detail


def test_no_destination_is_not_configured_not_a_failure(tmp_path: Path) -> None:
    _workspace(tmp_path, config="schema_version = 1\n[workspace]\n")

    outcome = publish(
        tmp_path,
        config=_config(tmp_path),
        result_path=tmp_path / ".ici" / "next" / "result.json",
        page_path=tmp_path / ".ici" / "next" / "result.html",
        env={},
    )

    assert outcome.state is PublicationState.NOT_CONFIGURED


def test_the_verify_result_file_is_not_modified(tmp_path: Path) -> None:
    _workspace(tmp_path)
    result_file = tmp_path / ".ici" / "next" / "result.json"
    before = result_file.read_bytes()

    publish(
        tmp_path,
        config=_config(tmp_path),
        result_path=result_file,
        page_path=tmp_path / ".ici" / "next" / "result.html",
        env=_env(tmp_path),
        transport=FakeGhes(),
    )

    assert result_file.read_bytes() == before, "publish must not rewrite the result"


# --- the CLI ---------------------------------------------------------------


def test_the_cli_writes_a_publish_record(tmp_path: Path, monkeypatch) -> None:
    _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    ghes = FakeGhes()
    monkeypatch.setattr("ici.adapters.ghes._urllib_transport", ghes)
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)

    result = runner.invoke(app, ["next", "publish"])

    assert result.exit_code == 0, result.output
    record = json.loads((tmp_path / ".ici" / "next" / "publish.json").read_text())
    assert record["schema_id"] == "ici.next.publish"
    assert record["state"] == "SUCCESS"
    assert record["artifact"]["sha256"].startswith("sha256:")


def test_the_cli_exits_2_when_nothing_is_configured(tmp_path: Path, monkeypatch) -> None:
    _workspace(tmp_path, config="schema_version = 1\n[workspace]\n")
    monkeypatch.chdir(tmp_path)
    for key in (
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "GITHUB_API_URL",
        "GITHUB_SERVER_URL",
        "ICI_PUBLISH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    result = runner.invoke(app, ["next", "publish"])

    assert result.exit_code == 2
    record = json.loads((tmp_path / ".ici" / "next" / "publish.json").read_text())
    assert record["state"] == "NOT_CONFIGURED"


def test_the_cli_exits_1_on_a_publish_failure(tmp_path: Path, monkeypatch) -> None:
    _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ici.adapters.ghes._urllib_transport", lambda *a: (500, {"error": "down"}))
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)

    result = runner.invoke(app, ["next", "publish"])

    assert result.exit_code == 1
    record = json.loads((tmp_path / ".ici" / "next" / "publish.json").read_text())
    assert record["state"] == "FAILED"
