"""Actual explicit `ici setup --url` through a local CONNECT proxy with a private CA.

Development-only driver. It bootstraps the candidate archive into a scratch XDG root with
`install-native.sh`, then runs the installed native core's explicit setup download through a
loopback HTTP CONNECT proxy to a loopback HTTPS server whose certificate comes from a private
CA created for this run. The artifact URL uses a host name that only the proxy resolves, so a
successful download proves the proxy was used. Cases: upper/lower-case proxy variables,
untrusted CA through the tunnel, CONNECT refusal, proxy credentials (sent and redacted on
failure) and a NO_PROXY bypass. Failures must leave the current selection unchanged.
Every product process is traced; only native executables and loopback connections may occur.
"""

import argparse
import base64
import contextlib
import http.server
import json
import re
import select
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path

HOST = "ici-fixture.test"
SECRET = "r8-secret-token"
WRONG = "wrong-secret-value"


def certificates(tls: Path) -> None:
    (tls / "extensions").write_text(
        f"subjectAltName=DNS:{HOST},IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    commands = [
        [
            *("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1"),
            *("-subj", "/CN=ici proxy fixture CA", "-keyout", "ca.key", "-out", "ca.pem"),
        ],
        [
            *("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={HOST}"),
            *("-keyout", "server.key", "-out", "server.csr"),
        ],
        [
            *("openssl", "x509", "-req", "-days", "1", "-in", "server.csr", "-CA", "ca.pem"),
            *("-CAkey", "ca.key", "-CAcreateserial", "-out", "server.pem"),
            *("-extfile", "extensions"),
        ],
    ]
    for command in commands:
        subprocess.run(command, cwd=tls, check=True, capture_output=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve()
    installer = args.installer.resolve()
    strace = shutil.which("strace")
    assert strace, "strace is required; a missing trace tool is not silently skipped"
    args.evidence.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="native-proxy-", dir=args.evidence.resolve()))
    tls = root / "tls"
    tls.mkdir()
    (tls / "empty").mkdir()
    certificates(tls)

    class Artifact(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(archive.stat().st_size))
            self.end_headers()
            with archive.open("rb") as source, contextlib.suppress(OSError):
                shutil.copyfileobj(source, self.wfile, 65536)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Artifact)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tls / "server.pem", tls / "server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server_port = server.server_port
    requests = []
    policy = {"deny": False, "authorization": None}

    class Proxy(http.server.BaseHTTPRequestHandler):
        def refuse(self, status):
            self.send_response(status)
            if status == 407:
                self.send_header("Proxy-Authenticate", "Basic")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_CONNECT(self):
            authorization = self.headers.get("Proxy-Authorization")
            requests.append({"target": self.path, "proxy_authorization": authorization})
            name, _, port = self.path.rpartition(":")
            if policy["deny"]:
                return self.refuse(403)
            if policy["authorization"] and authorization != policy["authorization"]:
                return self.refuse(407)
            if name != HOST or port != str(server_port):
                return self.refuse(502)
            upstream = socket.create_connection(("127.0.0.1", server_port))
            self.send_response(200)
            self.end_headers()
            sockets = [self.connection, upstream]
            try:
                while True:
                    ready, _, _ = select.select(sockets, [], [], 30)
                    if not ready:
                        break
                    for endpoint in ready:
                        data = endpoint.recv(65536)
                        if not data:
                            return None
                        peer = upstream if endpoint is self.connection else self.connection
                        peer.sendall(data)
            except OSError:
                pass
            finally:
                upstream.close()
            return None

        def log_message(self, *_args):
            pass

    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    proxy_url = f"http://127.0.0.1:{proxy.server_port}"

    bootstrap = root / "bootstrap-bin"
    bootstrap.mkdir()
    for name in ("mktemp", "dd", "wc", "sha256sum", "tar", "gzip", "chmod", "rm", "od", "tr"):
        source = shutil.which(name)
        assert source, f"actual bootstrap fixture requires {name}"
        (bootstrap / name).symlink_to(source)
    xdg = root / "xdg"
    for name in ("home", "tmp"):
        (root / name).mkdir()
    base = {
        "PATH": str(bootstrap),
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "XDG_DATA_HOME": str(xdg / "data"),
        "XDG_CONFIG_HOME": str(xdg / "config"),
        "XDG_CACHE_HOME": str(xdg / "cache"),
        "ICI_PYTHON": "/missing/legacy/python",
    }
    trusted = {"SSL_CERT_FILE": str(tls / "ca.pem"), "SSL_CERT_DIR": str(tls / "empty")}
    prefix = xdg / "data/ici/native"
    records = []

    def invoke(name, argv, environment, expected):
        trace = root / f"{name}.trace"
        requests.clear()
        result = subprocess.run(
            [strace, "-f", "-e", "trace=execve,connect", "-o", str(trace), *map(str, argv)],
            env=environment,
            capture_output=True,
            timeout=180,
            check=False,
        )
        text = trace.read_text(encoding="utf-8")
        programs = sorted(
            {Path(p).name for p in re.findall(r'execve\("([^"]+)".*= 0$', text, re.M)}
        )
        connects = sorted(
            set(re.findall(r'sin_port=htons\((\d+)\), sin_addr=inet_addr\("([^"]+)"\)', text))
        )
        stderr = result.stderr.decode(errors="replace")
        record = {
            "case": name,
            "exit": result.returncode,
            "expected": expected,
            "programs": programs,
            "connects": connects,
            "proxy_requests": list(requests),
        }
        records.append(record)
        (root / "progress.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        assert result.returncode == expected, (name, result.returncode, stderr[-2048:])
        assert not any(re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", p) for p in programs), name
        assert all(address == "127.0.0.1" for _, address in connects), name
        assert SECRET not in stderr and WRONG not in stderr, name
        return record

    def selected():
        result = subprocess.run(
            [prefix / "bin/ici", "setup", "--inspect", "--prefix", prefix, "--json"],
            env=base,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)["selected"]

    proxy_port = str(proxy.server_port)
    url = f"https://{HOST}:{server_port}/artifact"
    setup = [prefix / "bin/ici", "setup", "--url", url, "--sha256", args.sha256, "--prefix", prefix]
    try:
        invoke(
            "bootstrap",
            ["/bin/sh", installer, "--archive", archive, "--sha256", args.sha256],
            base,
            0,
        )
        initial = selected()
        record = invoke("proxy-trusted-ca", setup, {**base, **trusted, "HTTPS_PROXY": proxy_url}, 0)
        assert [r["target"] for r in record["proxy_requests"]] == [f"{HOST}:{server_port}"]
        assert record["connects"] == [(proxy_port, "127.0.0.1")]
        record = invoke("proxy-lowercase", setup, {**base, **trusted, "https_proxy": proxy_url}, 0)
        assert record["proxy_requests"]
        untrusted = {
            "SSL_CERT_FILE": str(tls / "empty/none.pem"),
            "SSL_CERT_DIR": str(tls / "empty"),
        }
        record = invoke(
            "proxy-untrusted-ca", setup, {**base, **untrusted, "HTTPS_PROXY": proxy_url}, 2
        )
        assert record["proxy_requests"] and selected() == initial
        policy["deny"] = True
        invoke("proxy-refuses-connect", setup, {**base, **trusted, "HTTPS_PROXY": proxy_url}, 2)
        assert selected() == initial
        policy["deny"] = False
        policy["authorization"] = "Basic " + base64.b64encode(f"r8user:{SECRET}".encode()).decode()
        credentials = f"http://r8user:{SECRET}@127.0.0.1:{proxy_port}"
        record = invoke(
            "proxy-credentials", setup, {**base, **trusted, "HTTPS_PROXY": credentials}, 0
        )
        assert record["proxy_requests"][0]["proxy_authorization"] == policy["authorization"]
        wrong = f"http://r8user:{WRONG}@127.0.0.1:{proxy_port}"
        invoke("proxy-wrong-credentials", setup, {**base, **trusted, "HTTPS_PROXY": wrong}, 2)
        assert selected() == initial
        policy["authorization"] = None
        direct = [*setup[:3], f"https://127.0.0.1:{server_port}/artifact", *setup[4:]]
        bypass = {**base, **trusted, "HTTPS_PROXY": proxy_url, "NO_PROXY": "127.0.0.1"}
        record = invoke("no-proxy-bypass", direct, bypass, 0)
        assert not record["proxy_requests"]
        assert record["connects"] == [(str(server_port), "127.0.0.1")]
    finally:
        server.shutdown()
        proxy.shutdown()
        for key in ("ca.key", "server.key"):
            (tls / key).unlink(missing_ok=True)
    report = {"archive_sha256": args.sha256, "cases": len(records), "evidence_directory": str(root)}
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
