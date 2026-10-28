"""Development-only private CA/CONNECT fixture for the Rust R0 transport spike.

No ici runtime imports, global CA installation, or external requests by default.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import select
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import ClassVar


class Endpoint(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/slow":
            time.sleep(4)
        status = {"/forbidden": 403, "/redirect": 302}.get(self.path, 200)
        self.send_response(status)
        if self.path == "/redirect":
            self.send_header("Location", "https://unrequested.invalid/")
        body = "x" * 5000 if self.path == "/oversize" else json.dumps({"path": self.path})
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        with contextlib.suppress(OSError, ssl.SSLError):
            self.wfile.write(body.encode())

    def log_message(self, *_args: object) -> None:
        pass


class ConnectProxy(socketserver.BaseRequestHandler):
    requests: ClassVar[list[str]] = []

    def handle(self) -> None:
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = self.request.recv(1024)
            if not chunk:
                return
            data += chunk
        method, authority, _ = data.split(b"\r\n", 1)[0].decode().split()
        assert method == "CONNECT"
        host, port = authority.rsplit(":", 1)
        assert host in ("127.0.0.1", "localhost"), "fixture proxy is loopback only"
        self.requests.append(authority)
        with socket.create_connection((host, int(port)), timeout=3) as remote:
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            peers = (self.request, remote)
            while True:
                readable, _, _ = select.select(peers, [], [], 5)
                if not readable:
                    return
                for source in readable:
                    packet = source.recv(65536)
                    if not packet:
                        return
                    (remote if source is self.request else self.request).sendall(packet)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("client", type=Path)
    parser.add_argument("--public-system-ca", action="store_true")
    args = parser.parse_args()
    client = args.client.resolve()
    env = dict(os.environ)
    for key in list(env):
        if key.lower().endswith("_proxy") or key in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            env.pop(key)
    results = []

    def request(name: str, url: str, expected: int, extra: dict[str, str] | None = None):
        completed = subprocess.run(
            [str(client), url],
            env={**env, **(extra or {})},
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        assert completed.returncode == expected, (name, completed.stdout, completed.stderr)
        payload = json.loads(completed.stdout)
        assert payload["publication"] == ("SUCCEEDED" if expected == 0 else "FAILED")
        assert completed.stderr == ""
        results.append({"case": name, "exit": expected, "result": payload})
        return payload

    with tempfile.TemporaryDirectory(prefix="ici-rust-ca-") as temporary:
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
                "/CN=ici fixture root",
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
                "ext.cnf",
            ],
        ]
        (root / "ext.cnf").write_text(
            "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
            "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        )
        for command in commands:
            subprocess.run(command, cwd=root, check=True, capture_output=True)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root / "server.pem", root / "server.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), ConnectProxy)
        proxy.daemon_threads = True
        threads = [
            threading.Thread(target=item.serve_forever, daemon=True) for item in (server, proxy)
        ]
        for thread in threads:
            thread.start()
        try:
            base = f"https://127.0.0.1:{server.server_port}"
            roots = {"SSL_CERT_FILE": str(root / "ca.pem"), "SSL_CERT_DIR": str(root / "empty")}
            (root / "empty").mkdir()
            untrusted = request("untrusted_private_ca", base, 2)
            assert untrusted["reason"] == "transport"
            path = "/api/v3/repos/example/project"
            trusted = request("private_ca_ghes_path", base + path, 0, roots)
            assert json.loads(trusted["body"])["path"] == path
            request("hostname_mismatch", f"https://localhost:{server.server_port}", 2, roots)
            request(
                "https_connect_proxy",
                base + path,
                0,
                {
                    **roots,
                    "HTTPS_PROXY": f"http://127.0.0.1:{proxy.server_address[1]}",
                },
            )
            assert ConnectProxy.requests == [f"127.0.0.1:{server.server_port}"]
            request(
                "proxy_connection_failure",
                base + path,
                2,
                {
                    **roots,
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                },
            )
            request(
                "no_proxy_bypass",
                base + path,
                0,
                {
                    **roots,
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                    "NO_PROXY": "127.0.0.1",
                },
            )
            denied = request("http_403", base + "/forbidden", 2, roots)
            assert denied["http_status"] == 403
            timeout = request("bounded_timeout", base + "/slow", 2, roots)
            assert timeout["reason"] == "timeout"
            oversize = request("bounded_response", base + "/oversize", 2, roots)
            assert oversize["reason"] == "invalid_or_oversize_body"
            redirect = request("no_automatic_redirect", base + "/redirect", 2, roots)
            assert redirect["http_status"] == 302
            request("reject_plain_http", "http://127.0.0.1:9", 2, roots)
            if args.public_system_ca:
                request("public_system_ca", "https://api.github.com", 0)
        finally:
            for item in (server, proxy):
                item.shutdown()
                item.server_close()
            for thread in threads:
                thread.join(timeout=5)
    print(json.dumps({"cases": results, "count": len(results)}, indent=2))


if __name__ == "__main__":
    main()
