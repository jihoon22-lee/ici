"""Actual product transport methods, bounds, redaction and explicit redirect fixtures."""

import argparse
import contextlib
import hashlib
import http.server
import json
import os
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import ClassVar

PAYLOAD = b"verified-native-download-fixture"
SECRET = "fixture-private-token-DO-NOT-LOG"


class Endpoint(http.server.BaseHTTPRequestHandler):
    events: ClassVar[list[dict]] = []
    other: ClassVar[str] = ""
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def do_PUT(self):
        self.respond()

    def respond(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.events.append(
            {
                "port": self.server.server_port,
                "path": self.path,
                "method": self.command,
                "authorized": self.headers.get("Authorization") == "Bearer " + SECRET,
                "body": body,
            }
        )
        redirects = {
            "/same": "/payload",
            "/cross": self.other + "/payload",
            "/cycle": "/cycle",
            "/plain": "http://127.0.0.1:9/unrequested",
            "/userinfo": "https://private-user:" + SECRET + "@127.0.0.1:9/unrequested",
        }
        if self.path.startswith("/hop/"):
            redirects[self.path] = "/hop/" + str(int(self.path.rsplit("/", 1)[1]) + 1)
        if self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response({"POST": 201, "PUT": 202}.get(self.command, 200))
        self.send_header("X-Fixture", "first")
        self.send_header("X-Fixture", "second")
        if self.path == "/stream-large":
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            with contextlib.suppress(OSError, ssl.SSLError):
                self.wfile.write(b"1388\r\n" + b"x" * 5000 + b"\r\n0\r\n\r\n")
            return
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        if self.path == "/slow-body":
            time.sleep(1)
        with contextlib.suppress(OSError, ssl.SSLError):
            self.wfile.write(PAYLOAD)

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    driver = args.driver.resolve()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.lower().endswith("_proxy") and key not in ("SSL_CERT_FILE", "SSL_CERT_DIR")
    }
    results = []
    with tempfile.TemporaryDirectory(prefix="ici-product-transport-") as temporary:
        root = Path(temporary)
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
                "/CN=ici transport fixture root",
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
        (root / "extensions").write_text(
            "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        )
        for command in commands:
            subprocess.run(command, cwd=root, capture_output=True, check=True)
        (root / "empty").mkdir()
        environment.update(SSL_CERT_FILE=str(root / "ca.pem"), SSL_CERT_DIR=str(root / "empty"))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root / "server.pem", root / "server.key")
        servers = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), Endpoint) for _ in range(2)]
        for server in servers:
            server.socket = context.wrap_socket(server.socket, server_side=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"https://127.0.0.1:{servers[0].server_port}"
        Endpoint.other = f"https://127.0.0.1:{servers[1].server_port}"

        def request(name, path="/payload", **values):
            invocation = {
                "url": base + path,
                "headers": {"Authorization": "Bearer " + SECRET},
                **values,
            }
            result = subprocess.run(
                [str(driver)],
                input=json.dumps(invocation),
                text=True,
                capture_output=True,
                env=environment,
                timeout=8,
                check=True,
            )
            assert SECRET not in result.stdout + result.stderr, name
            assert "private-user" not in result.stdout + result.stderr, name
            assert result.stderr == "", name
            response = json.loads(result.stdout)
            results.append({"case": name, "response": response})
            return response

        try:
            for method, status in [("POST", 201), ("PUT", 202)]:
                response = request(
                    method.lower() + "_body", method=method, body="explicit request body"
                )
                assert response["status"] == status
                assert Endpoint.events[-1]["body"] == b"explicit request body"
                assert Endpoint.events[-1]["authorized"]
                assert response["body_sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
                assert (
                    response["fixture_header_sha256"]
                    == hashlib.sha256(b"first\nsecond").hexdigest()
                )
            before = len(Endpoint.events)
            response = request("publisher_does_not_redirect", "/cross")
            assert response["status"] == 302 and len(Endpoint.events) == before + 1
            expected = hashlib.sha256(PAYLOAD).hexdigest()
            assert request("download_same_origin", "/same", mode="download", sha256=expected)["ok"]
            assert Endpoint.events[-1]["authorized"]
            assert request("download_cross_origin", "/cross", mode="download", sha256=expected)[
                "ok"
            ]
            assert not Endpoint.events[-1]["authorized"]
            assert Endpoint.events[-1]["port"] == servers[1].server_port
            for case, path in [
                ("loop", "/cycle"),
                ("downgrade", "/plain"),
                ("userinfo", "/userinfo"),
                ("hop_bound", "/hop/0"),
            ]:
                assert (
                    request(case, path, mode="download", sha256=expected)["reason"]
                    == "download-redirect"
                )
            assert (
                request("wrong_digest", mode="download", sha256="0" * 64)["reason"]
                == "download-digest"
            )
            assert request("stream_bound", "/stream-large")["reason"] == "ResponseTooLarge"
            assert request("body_timeout", "/slow-body", timeout_ms=100)["reason"] == "Timeout"
            assert (
                request("invalid_header", headers={"Authorization": SECRET + "\ninvalid"})["reason"]
                == "InvalidRequest"
            )
            assert (
                request("userinfo_request", url="https://private-user:" + SECRET + "@127.0.0.1:9/")[
                    "reason"
                ]
                == "InvalidRequest"
            )
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
    print(json.dumps({"count": len(results), "cases": results}, indent=2))


if __name__ == "__main__":
    main()
