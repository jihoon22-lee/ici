"""Actual local HTTPS publishing: explicit endpoints, private CA and separate outcomes.

Python is a development fixture driver. Every traced product command is the
native ici executable; this server never opens a connection to a remote host.
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
REPOSITORY = "fixture/repository"
BRANCH = "reports/native"
PR = 23
WORKSPACE = "Native publisher fixture"
MARKER = "<!-- ici-next:native-publisher-fixture -->"
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
        expected_prefix = state["prefix"] + "/repos/" + REPOSITORY
        authorized = self.headers.get("Authorization") == "Bearer " + TOKEN
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
        if state["mode"] == "forbidden":
            self.reply(403, {"message": TOKEN})
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
                assert relative.endswith("/pr-23/" + state["head"][:12] + "/index.html")
                event["uploaded_sha256"] = digest(payload)
                self.reply(201, {"content": {"sha": "fixture-blob"}})
        elif relative == f"/pulls/{PR}":
            self.reply(200, {"head": {"sha": state["head"]}})
        elif relative == f"/issues/{PR}/comments" and self.command == "GET":
            assert route.query == "per_page=100&page=1"
            self.reply(
                200,
                [{"id": 99, "body": MARKER + "\n<!-- head:" + state["head"] + " run:1.1 -->"}]
                if state["mode"] == "patch"
                else [],
            )
        elif relative in (f"/issues/{PR}/comments", "/issues/comments/99"):
            assert self.command == ("PATCH" if state["mode"] == "patch" else "POST")
            value = json.loads(body)
            assert MARKER in value["body"]
            assert "head:" + state["head"] + " run:42.2" in value["body"]
            assert "Open report" in value["body"] and "FAIL" in value["body"]
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
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


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
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        origin = f"https://127.0.0.1:{server.server_port}"

        def run(name, mode, prefix, trusted, expected, result_bytes=None):
            result_fixture = fixture if result_bytes is None else result_bytes
            project = root / name
            project.mkdir()
            (project / "saved.json").write_bytes(result_fixture)
            (project / "saved.html").write_bytes(PAGE)
            event_path = project / "event.json"
            event_path.write_text(
                json.dumps({"pull_request": {"number": PR, "head": {"sha": head}}}),
                encoding="utf-8",
            )
            (project / "ici.toml").write_text(
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
                + "[[components]]\nid='removed'\nroot='source-was-deleted'\nlanguages=['python']\n",
                encoding="utf-8",
            )
            server.fixture = {
                "mode": mode,
                "prefix": prefix,
                "head": head,
                "origin": origin,
                "branch": mode == "patch",
                "events": [],
            }
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
            trace = project / "trace.log"
            command = [binary, "next", "publish", "--result", "saved.json", "--out", "saved.html"]
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
            assert (project / "saved.json").read_bytes() == result_fixture
            assert (project / "saved.html").read_bytes() == PAGE
            assert not (project / "source-was-deleted").exists()
            assert json.loads(result_fixture)["gate"]["selected"] == "FAIL"
            assert all(
                secret.encode() not in result.stdout + result.stderr + record_bytes
                for secret in (TOKEN, FALLBACK_TOKEN)
            ), "Credentials or private response bodies escaped the failure boundary"
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
            if expected == 0:
                assert record["result_sha256"] == "sha256:" + digest(result_fixture)
                assert record["reports"][0]["artifact"]["sha256"] == "sha256:" + digest(PAGE)
                assert any(event.get("uploaded_sha256") == digest(PAGE) for event in events)
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
                    "result_sha256_after": digest((project / "saved.json").read_bytes()),
                    "programs": programs,
                    "http_requests": events,
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
            assert proxy.unexpected_requests == 0, "No request may target an external server"
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
