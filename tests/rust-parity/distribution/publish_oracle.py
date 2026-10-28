"""Verify the untouched frozen publisher's large-diagnostic behavior without network."""

import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    evidence = json.loads(Path(__file__).with_name("publish-large-oracle.json").read_text())
    frozen = Path(sys.argv[1]).resolve()
    repo = Path(__file__).resolve().parents[3]
    for name, expected in evidence["oracle_files"].items():
        assert sha((frozen / name).read_bytes()) == expected, name
    raw = (repo / evidence["base_result"]).read_bytes()
    assert sha(raw) == evidence["base_result_sha256"]
    result = json.loads(raw)
    result["findings"][0]["message"] += " details " + "x" * 100000
    encoded = json.dumps(result).encode()
    assert sha(encoded) == evidence["result_sha256"]
    head = result["identity"]["source"]["commit"]
    sys.path.insert(0, str(frozen / "src"))
    publishing = importlib.import_module("ici.application.publish")
    composition = importlib.import_module("ici.config.composition")
    schema = importlib.import_module("ici.config.schema")
    assert Path(publishing.__file__).is_relative_to(frozen)
    comments = []

    def transport(method, url, _token, payload):
        if "/git/ref/" in url:
            return 200, {}
        if "/contents/" in url:
            return (404, {}) if method == "GET" else (201, {})
        if url.endswith("/pages"):
            return 200, {"html_url": "https://fixture.invalid/pages"}
        if "/pulls/" in url:
            return 200, {"head": {"sha": head}}
        if "/comments" in url and method == "GET":
            return 200, []
        if url.endswith("/comments") and method == "POST":
            comments.append(payload["body"])
            return 201, {"html_url": "https://fixture.invalid/comment"}
        raise AssertionError((method, url))

    with tempfile.TemporaryDirectory(prefix="frozen-publish-") as temporary:
        root = Path(temporary)
        (root / "saved.json").write_bytes(encoded)
        page = b"<!doctype html>report"
        (root / "saved.html").write_bytes(page)
        event = root / "event.json"
        event.write_text(json.dumps({"pull_request": {"number": 23, "head": {"sha": head}}}))
        config = composition.compose(
            schema.read_root(
                "schema_version=1\n[workspace]\nname='Native publisher fixture'\n"
                "[publish]\nrepo='fixture/repository'\napi_url='https://fixture.invalid/api/v3'\n"
                "server_url='https://fixture.invalid'\n",
                path="ici.toml",
            )
        )
        outcome = publishing.publish(
            root,
            config=config,
            result_path=root / "saved.json",
            page_path=root / "saved.html",
            env={
                "GITHUB_TOKEN": "fixture",
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_SHA": head,
                "GITHUB_RUN_ID": "42",
            },
            transport=transport,
        )
        assert outcome.state.value == evidence["original"]["publication"]
        assert len(comments[0].encode()) == evidence["original"]["comment_bytes"]
        assert (root / "saved.json").read_bytes() == encoded
        assert (root / "saved.html").read_bytes() == page
    print("1 frozen publishing size fixture matches")


if __name__ == "__main__":
    main()
