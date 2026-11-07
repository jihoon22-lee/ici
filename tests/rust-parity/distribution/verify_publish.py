"""Actual local HTTPS publishing: explicit endpoints, private CA and separate outcomes.

Python is a development fixture driver. Every traced product command is the
native ici executable; this server never opens a connection to a remote host.
Cases cover `ici next publish` (schema v2 and v1 [publish], the Actions-default
destination without [publish], and [publish] overriding the Actions environment)
and historical `ici publish` (--html/--json, repeated --report-dir, the hub
repository with a separate comment repository, and the default public API,
whose CONNECT is refused by the local deny proxy so nothing leaves the machine).
"""

import argparse
import base64
import contextlib
import hashlib
import http.server
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

TOKEN = "fixture-named-publish-token-MUST-NOT-LOG"
FALLBACK_TOKEN = "fixture-fallback-token-MUST-NOT-USE"
# The analyzed repository when reports go elsewhere (legacy hub, next [publish] override): only
# its pull request and comments are addressed, with its own Actions token.
SOURCE_TOKEN = "fixture-source-repository-token-MUST-NOT-LOG"
SOURCE_REPOSITORY = "fixture/source"
REPOSITORY = "fixture/repository"
BRANCH = "reports/native"
PR = 23
WORKSPACE = "Native publisher fixture"
MARKER = "<!-- ici-next:native-publisher-fixture -->"
LEGACY_MARKER = "<!-- ici-report -->"
SUITE = (
    json.dumps(
        {
            "schema_version": "ici.result/v3",
            "suite_status": "FAIL",
            "tem_score": None,
            "max_tem_score": 5.0,
            "results": [{"engine_name": "line", "status": "FAIL", "summary": "too long"}],
            "baseline_comparison": None,
        }
    )
    + "\n"
).encode()
PAGE = b"<!doctype html><meta charset=utf-8><p>Frozen failing analysis evidence.</p>\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class FixtureServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *_args):
        # A deliberately rejected CA can end its TLS connection abruptly.
        pass


class Endpoint(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def do_PUT(self):
        self.respond()

    def do_PATCH(self):
        self.respond()

    def respond(self):
        state = self.server.fixture
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        route = urlsplit(self.path)
        source_prefix = state["prefix"] + "/repos/" + SOURCE_REPOSITORY
        source = route.path.startswith(source_prefix + "/")
        expected_prefix = source_prefix if source else state["prefix"] + "/repos/" + REPOSITORY
        authorized = self.headers.get("Authorization") == "Bearer " + (
            SOURCE_TOKEN if source else TOKEN
        )
        headers_valid = (
            self.headers.get("Accept") == "application/vnd.github+json"
            and self.headers.get("X-GitHub-Api-Version") == "2022-11-28"
            and self.headers.get("Content-Type") == "application/json"
            and self.headers.get("User-Agent", "").startswith("ici/")
        )
        event = {
            "method": self.command,
            "path": self.path,
            "authorized": authorized,
            "required_headers": headers_valid,
            "body_sha256": digest(body),
            "body_bytes": len(body),
        }
        state["events"].append(event)
        if not authorized or not headers_valid or not route.path.startswith(expected_prefix):
            self.reply(403, {"message": "fixture refused endpoint or headers"})
            return
        relative = unquote(route.path[len(expected_prefix) :])
        state["repositories"].add(SOURCE_REPOSITORY if source else REPOSITORY)
        if source != (state["comment_repo"] == SOURCE_REPOSITORY) and (
            relative.startswith(("/pulls/", "/issues/"))
        ):
            self.reply(403, {"message": "pull request addressed in the wrong repository"})
        elif source and not relative.startswith(("/pulls/", "/issues/")):
            self.reply(403, {"message": "reports must not be written to the source repository"})
        elif state["mode"] == "forbidden":
            self.reply(403, {"message": TOKEN})
        elif relative == "" and self.command == "GET":
            # A separate publication repository has none of the analyzed commits: the branch
            # starts from its own default branch.
            self.reply(200, {"default_branch": "main"})
        elif relative == "/git/ref/heads/main":
            self.reply(200, {"object": {"sha": state["head"]}})
        elif relative.startswith("/git/ref/"):
            self.reply(200 if state["branch"] else 404, {})
        elif relative == "/git/refs" and self.command == "POST":
            value = json.loads(body)
            assert value == {"ref": "refs/heads/" + BRANCH, "sha": state["head"]}
            state["branch"] = True
            self.reply(201, {"ref": "refs/heads/" + BRANCH})
        elif relative == "/pages":
            if state["mode"] == "malformed":
                self.reply_raw(200, b"not-json:" + TOKEN.encode())
            else:
                self.reply(200, {"html_url": state["origin"] + "/pages/fixture"})
        elif relative.startswith("/contents/"):
            if self.command == "GET":
                assert unquote(route.query) == "ref=" + BRANCH
                self.reply(404, {})
            else:
                assert self.command == "PUT"
                value = json.loads(body)
                assert value["branch"] == BRANCH
                payload = base64.b64decode(value["content"], validate=True)
                assert payload == PAGE
                if state["marker"] == LEGACY_MARKER:
                    # Frozen engines/publish._resolve_target: the stable pr/<n>/index.html layout.
                    assert relative[len("/contents/") :] in state["paths"], relative
                else:
                    assert relative.endswith("/pr-23/" + state["head"][:12] + "/index.html")
                event["uploaded_sha256"] = digest(payload)
                event["uploaded_path"] = relative
                self.reply(201, {"content": {"sha": "fixture-blob"}})
        elif relative == f"/pulls/{PR}":
            # Only the next path's ordering guard reads the PR head.
            assert state["marker"] != LEGACY_MARKER
            self.reply(200, {"head": {"sha": state["head"]}})
        elif relative == f"/issues/{PR}/comments" and self.command == "GET":
            assert route.query == "per_page=100&page=1"
            self.reply(
                200,
                [
                    {
                        "id": 99,
                        "body": state["marker"] + "\n<!-- head:" + state["head"] + " run:1.1 -->",
                    }
                ]
                if state["mode"] == "patch"
                else [],
            )
        elif relative in (f"/issues/{PR}/comments", "/issues/comments/99"):
            assert self.command == ("PATCH" if state["mode"] == "patch" else "POST")
            value = json.loads(body)
            assert value["body"].startswith(state["marker"])
            if state["marker"] == LEGACY_MARKER:
                # The frozen sticky comment: the footer names the stable path, no trailer.
                assert all(
                    "`" in value["body"] and path in value["body"] for path in state["paths"]
                )
                assert "<!-- head:" not in value["body"]
            else:
                assert "head:" + state["head"] + " run:42.2" in value["body"]
            assert "FAIL" in value["body"]
            assert state["marker"] == LEGACY_MARKER or "Open report" in value["body"]
            assert len(value["body"].encode()) <= 65536
            event["comment_body_sha256"] = digest(value["body"].encode())
            self.reply(
                403 if state["mode"] == "comment-forbidden" else 201,
                {"message": TOKEN}
                if state["mode"] == "comment-forbidden"
                else {"html_url": state["origin"] + "/fixture/comment/99"},
            )
        else:
            self.reply(404, {"message": "unexpected local fixture route"})

    def reply(self, status, value):
        self.reply_raw(status, json.dumps(value).encode())

    def reply_raw(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(OSError, ssl.SSLError):
            self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DenyProxy(http.server.BaseHTTPRequestHandler):
    def do_CONNECT(self):
        self.server.unexpected_requests += 1
        self.server.targets.append(self.path)
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


def next_v1_config(api, server_origin):
    """A schema_version = 1 next workspace naming the same destination."""
    return (
        "schema_version=1\n[workspace]\nname="
        + json.dumps(WORKSPACE)
        + "\n[publish]\nrepo="
        + json.dumps(REPOSITORY)
        + "\napi_url="
        + json.dumps(api)
        + "\nserver_url="
        + json.dumps(server_origin)
        + "\nbranch="
        + json.dumps(BRANCH)
        + "\ntoken_env='ICI_FIXTURE_PUBLISH_TOKEN'\n"
        + "[[components]]\nid='removed'\nroot='source-was-deleted'\nlanguages=['python']\n"
    )


def state_repositories(server):
    return server.fixture["repositories"]


def certificates(directory):
    (directory / "extensions").write_text(
        "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
        "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    commands = [
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=ici publish fixture CA",
            "-keyout",
            "ca.key",
            "-out",
            "ca.pem",
        ],
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
            "-keyout",
            "server.key",
            "-out",
            "server.csr",
        ],
        [
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            "server.csr",
            "-CA",
            "ca.pem",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            "server.pem",
            "-extfile",
            "extensions",
        ],
    ]
    for command in commands:
        subprocess.run(command, cwd=directory, capture_output=True, check=True, timeout=30)
    (directory / "empty-roots").mkdir()
    (directory / "untrusted.pem").write_bytes(b"")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    repo = Path(__file__).resolve().parents[3]
    fixture = (repo / "tests/fixtures/ici-next/run-code-fail.json").read_bytes()
    head = json.loads(fixture)["identity"]["source"]["commit"]
    assert len(head) == 40
    trace_tool = shutil.which("strace")
    assert trace_tool, "Actual process/network trace is required; no silent skip"
    args.evidence.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="native-publish-", dir=args.evidence.resolve()))
    records = []
    with tempfile.TemporaryDirectory(prefix="private-ca-", dir=root) as temporary:
        tls = Path(temporary)
        certificates(tls)
        server = FixtureServer(("127.0.0.1", 0), Endpoint)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls / "server.pem", tls / "server.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        proxy = FixtureServer(("127.0.0.1", 0), DenyProxy)
        proxy.unexpected_requests = 0
        proxy.targets = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        origin = f"https://127.0.0.1:{server.server_port}"

        def run(
            name,
            mode,
            prefix,
            trusted,
            expected,
            result_bytes=None,
            *,
            config=None,
            command_args=None,
            files=None,
            result_name="saved.json",
            environment_update=None,
            marker=MARKER,
            comment_repo=REPOSITORY,
            uploads=1,
            branch=None,
            connects=(),
            paths=(),
        ):
            result_fixture = fixture if result_bytes is None else result_bytes
            project = root / name
            project.mkdir()
            inputs = {"saved.json": result_fixture, "saved.html": PAGE} if files is None else files
            for relative, data in inputs.items():
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_bytes(data)
            result_fixture = inputs.get(result_name, result_fixture)
            event_path = project / "event.json"
            event_path.write_text(
                json.dumps({"pull_request": {"number": PR, "head": {"sha": head}}}),
                encoding="utf-8",
            )
            configuration = (
                "schema_version=2\n[workspace]\nname="
                + json.dumps(WORKSPACE)
                + "\n[publish]\nrepo="
                + json.dumps(REPOSITORY)
                + "\napi_url="
                + json.dumps(origin + prefix)
                + "\nserver_url="
                + json.dumps(origin)
                + "\nbranch="
                + json.dumps(BRANCH)
                + "\ntoken_env='ICI_FIXTURE_PUBLISH_TOKEN'\n"
                + "[[components]]\nid='removed'\nroot='source-was-deleted'\nlanguages=['python']\n"
            )
            if config is not None:
                configuration = config(origin + prefix, origin)
            if configuration:
                (project / "ici.toml").write_text(configuration, encoding="utf-8")
            server.fixture = {
                "mode": mode,
                "prefix": prefix,
                "head": head,
                "origin": origin,
                "branch": mode == "patch" if branch is None else branch,
                "events": [],
                "marker": marker,
                "comment_repo": comment_repo,
                "repositories": set(),
                "paths": list(paths),
            }
            proxy.targets.clear()
            environment = {
                "PATH": "",
                "HOME": str(project),
                "RAYON_NUM_THREADS": "2",
                "OMP_NUM_THREADS": "2",
                "ICI_PYTHON": "/missing/legacy/python",
                "PYTHONHOME": "/missing/python",
                "PYTHONPATH": "/missing/modules",
                "ICI_FIXTURE_PUBLISH_TOKEN": TOKEN,
                "GITHUB_TOKEN": FALLBACK_TOKEN,
                "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_SHA": head,
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_RUN_ID": "42",
                "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_REF_NAME": "feature/fixture",
                "SSL_CERT_FILE": str(tls / ("ca.pem" if trusted else "untrusted.pem")),
                "SSL_CERT_DIR": str(tls / "empty-roots"),
                "HTTPS_PROXY": f"http://127.0.0.1:{proxy.server_port}",
                "NO_PROXY": "127.0.0.1,localhost",
            }
            for key, value in (environment_update or {}).items():
                if value is None:
                    environment.pop(key, None)
                else:
                    environment[key] = value
            trace = project / "trace.log"
            command = [
                binary,
                *(
                    command_args
                    or ["next", "publish", "--result", "saved.json", "--out", "saved.html"]
                ),
            ]
            result = subprocess.run(
                [
                    trace_tool,
                    "-f",
                    "-s",
                    "4096",
                    "-e",
                    "trace=process,network",
                    "-o",
                    trace,
                    *map(str, command),
                ],
                cwd=project,
                env=environment,
                capture_output=True,
                timeout=60,
                check=False,
            )
            (project / "stdout.log").write_bytes(result.stdout)
            (project / "stderr.log").write_bytes(result.stderr)
            record_path = project / ".ici/rust-v1/publish.json"
            record_bytes = record_path.read_bytes() if record_path.exists() else b""
            assert result.returncode == expected, (
                name,
                result.returncode,
                result.stdout[-2048:],
                result.stderr[-2048:],
            )
            record = json.loads(record_bytes)
            assert record["schema_id"] == "ici.next.publish" and record["schema_version"] == 1
            assert record["state"] == ("SUCCESS" if expected == 0 else "FAILED"), record
            for relative, data in inputs.items():
                assert (project / relative).read_bytes() == data, relative
            assert not (project / "source-was-deleted").exists()
            if marker == MARKER:
                assert json.loads(result_fixture)["gate"]["selected"] == "FAIL"
            assert all(
                secret.encode() not in result.stdout + result.stderr + record_bytes
                for secret in (TOKEN, FALLBACK_TOKEN, SOURCE_TOKEN)
            ), "Credentials or private response bodies escaped the failure boundary"
            assert proxy.targets == list(connects), proxy.targets
            text = trace.read_text(encoding="utf-8")
            programs = re.findall(r'execve\("([^"\n]+)"', text)
            assert programs == [str(binary)], programs
            for line in text.splitlines():
                if "connect(" in line and "AF_INET" in line:
                    assert '"127.0.0.1"' in line or '"::1"' in line, line
            events = server.fixture["events"]
            assert all(event["authorized"] and event["required_headers"] for event in events)
            if not trusted:
                assert not events, "An untrusted CA must prevent HTTP requests"
            if connects:
                assert not events, "The default public API must only be reached through the proxy"
            if expected == 0:
                assert record["result_sha256"] == "sha256:" + digest(result_fixture)
                assert record["reports"][0]["artifact"]["sha256"] == "sha256:" + digest(PAGE)
                uploaded = [event for event in events if "uploaded_sha256" in event]
                assert len(uploaded) == uploads, uploaded
                assert all(event["uploaded_sha256"] == digest(PAGE) for event in uploaded)
                assert len({event["uploaded_path"] for event in uploaded}) == uploads
                assert state_repositories(server) == {REPOSITORY, comment_repo}
                assert any("comment_body_sha256" in event for event in events)
                assert any(
                    event["method"] == ("PATCH" if mode == "patch" else "POST")
                    and "/comments" in event["path"]
                    for event in events
                )
            if mode == "comment-forbidden":
                assert record["reports"][0]["uploaded"] is True
            records.append(
                {
                    "case": name,
                    "exit": result.returncode,
                    "publication": record["state"],
                    "api_prefix": prefix,
                    "result_sha256_before": digest(result_fixture),
                    "result_sha256_after": digest((project / result_name).read_bytes()),
                    "programs": programs,
                    "http_requests": events,
                    "proxy_connects": list(proxy.targets),
                    "stdout": result.stdout.decode(errors="replace")[-2048:],
                }
            )
            (root / "progress.json").write_text(
                json.dumps(records, indent=2) + "\n", encoding="utf-8"
            )

        try:
            run("untrusted-private-ca", "success", "/api/v3", False, 1)
            run("trusted-ghes-create-comment", "success", "/api/v3", True, 0)
            run("trusted-github-style-update-comment", "patch", "", True, 0)
            run("http-403-preserves-analysis", "forbidden", "/api/v3", True, 1)
            run("malformed-api-json-preserves-analysis", "malformed", "/api/v3", True, 1)
            run("uploaded-page-comment-403-is-failed", "comment-forbidden", "/api/v3", True, 1)
            large = json.loads(fixture)
            large["findings"][0]["message"] += " details " + "x" * 100000
            run(
                "large-finding-has-bounded-comment",
                "success",
                "/api/v3",
                True,
                0,
                result_bytes=json.dumps(large).encode(),
            )
            run("next-v1-config-publish", "success", "/api/v3", True, 0, config=next_v1_config)
            # No [publish]: the destination is the Actions repository, API and token.
            run(
                "next-actions-default-destination",
                "success",
                "/api/v3",
                True,
                0,
                config=lambda _api, _origin: (
                    "schema_version=2\n[workspace]\nname=" + json.dumps(WORKSPACE) + "\n"
                ),
                environment_update={
                    "GITHUB_API_URL": origin + "/api/v3",
                    "GITHUB_TOKEN": TOKEN,
                    "ICI_PUBLISH_BRANCH": BRANCH,
                    "ICI_FIXTURE_PUBLISH_TOKEN": None,
                },
            )
            # [publish] overrides the Actions repository, API and token; the PR comment goes to
            # the analyzed (Actions) repository with its own token.
            run(
                "next-config-overrides-actions-environment",
                "success",
                "/api/v3",
                True,
                0,
                environment_update={
                    "GITHUB_REPOSITORY": SOURCE_REPOSITORY,
                    "GITHUB_API_URL": "https://127.0.0.1:9/never-used",
                    "GITHUB_TOKEN": SOURCE_TOKEN,
                },
                comment_repo=SOURCE_REPOSITORY,
            )
            legacy_environment = {
                "GITHUB_ACTIONS": "true",
                "GITHUB_API_URL": origin + "/api/v3",
                "GITHUB_TOKEN": TOKEN,
                "ICI_PUBLISH_BRANCH": BRANCH,
                "ICI_FIXTURE_PUBLISH_TOKEN": None,
            }
            legacy = {"config": lambda _api, _origin: "", "marker": LEGACY_MARKER}
            run(
                "legacy-html-json-self",
                "success",
                "/api/v3",
                True,
                0,
                command_args=["publish", "--html", "custom.html", "--json", "custom.json"],
                files={"custom.html": PAGE, "custom.json": SUITE},
                result_name="custom.json",
                environment_update=legacy_environment,
                paths=("pr/23/index.html",),
                **legacy,
            )
            run(
                "legacy-report-dirs-self",
                "success",
                "/api/v3",
                True,
                0,
                command_args=["publish", "--report-dir", "a", "--report-dir", "second=b"],
                files={
                    "a/verify_report.html": PAGE,
                    "a/verify_report.json": SUITE,
                    "b/verify_report.html": PAGE,
                    "b/verify_report.json": SUITE,
                },
                result_name="a/verify_report.json",
                environment_update=legacy_environment,
                uploads=2,
                paths=("a/pr/23/index.html", "second/pr/23/index.html"),
                **legacy,
            )
            # Hub: reports go to ICI_PUBLISH_REPO with ICI_PUBLISH_TOKEN; the comment goes to the
            # analyzed repository with GITHUB_TOKEN (frozen _resolve_target hub mode).
            run(
                "legacy-hub-repository-default-pages",
                "success",
                "/api/v3",
                True,
                0,
                command_args=["publish"],
                files={"verify_report.html": PAGE, "verify_report.json": SUITE},
                result_name="verify_report.json",
                environment_update={
                    **legacy_environment,
                    "ICI_PUBLISH_REPO": REPOSITORY,
                    "ICI_PUBLISH_TOKEN": TOKEN,
                    "GITHUB_REPOSITORY": SOURCE_REPOSITORY,
                    "GITHUB_TOKEN": SOURCE_TOKEN,
                },
                comment_repo=SOURCE_REPOSITORY,
                branch=True,
                # Hub prefix: the analyzed repository's name (`ici publish` names no project).
                paths=("source/pr/23/index.html",),
                **legacy,
            )
            # Without GITHUB_API_URL the historical caller targets the public GitHub API; the
            # deny proxy refuses the CONNECT, so publication fails (exit 1) and nothing leaves.
            run(
                "legacy-default-public-api-through-proxy",
                "success",
                "/api/v3",
                True,
                1,
                command_args=["publish"],
                files={"verify_report.html": PAGE, "verify_report.json": SUITE},
                result_name="verify_report.json",
                environment_update={**legacy_environment, "GITHUB_API_URL": None},
                # Like frozen, publication continues after the refused branch step: the Pages
                # check and the sticky-comment lookup are refused at the proxy too.
                connects=("api.github.com:443",) * 3,
                paths=("pr/23/index.html",),
                **legacy,
            )
            assert proxy.unexpected_requests == 3, "Only the refused public-API CONNECTs may leave"
        finally:
            server.shutdown()
            server.server_close()
            proxy.shutdown()
            proxy.server_close()
    report = {
        "count": len(records),
        "binary_sha256": digest(binary.read_bytes()),
        "result_sha256": digest(fixture),
        "cases": records,
        "evidence_directory": str(root),
        "external_requests": 0,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "count": len(records),
                "evidence_directory": str(root),
                "binary_sha256": report["binary_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
