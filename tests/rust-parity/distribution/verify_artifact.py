"""Actual offline native archive/install/recovery/analysis and explicit HTTPS setup corpus."""

import argparse
import contextlib
import hashlib
import http.server
import json
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import ClassVar


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArchiveEndpoint(http.server.BaseHTTPRequestHandler):
    artifact: ClassVar[Path]

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/artifact")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/forbidden":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(self.artifact.stat().st_size))
        self.end_headers()
        with self.artifact.open("rb") as source, contextlib.suppress(OSError, ssl.SSLError):
            shutil.copyfileobj(source, self.wfile, 65536)

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--builder", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve()
    builder = args.builder.resolve()
    installer = args.installer.resolve()
    assert sha(archive) == args.sha256, (
        "actual downloaded/captured bytes must match the trusted digest"
    )
    args.evidence.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="native-artifact-", dir=args.evidence.resolve()))
    logs = root / "logs"
    logs.mkdir()
    standard = root / "bootstrap-bin"
    standard.mkdir()
    for name in ("mktemp", "dd", "wc", "sha256sum", "tar", "gzip", "chmod", "rm", "od", "tr"):
        source = shutil.which(name)
        assert source, f"actual bootstrap fixture requires {name}"
        (standard / name).symlink_to(source)
    foreign_path = standard / "ici"
    foreign_body = b"#!/bin/sh\nexit 99\n"
    foreign_path.write_bytes(foreign_body)
    foreign_path.chmod(0o755)
    trace_tool = shutil.which("strace")
    assert trace_tool, "strace is required; a missing trace tool is not silently skipped"
    temporary = root / "temporary"
    temporary.mkdir()
    home = root / "empty-home"
    home.mkdir()
    prefix = root / "native prefix"
    environment = {
        "PATH": "",
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "ICI_PYTHON": "/missing/legacy/python",
        "PYTHONHOME": "/missing/python",
        "PYTHONPATH": "/missing/modules",
    }
    records = []

    def invoke(name, command, *, cwd=root, env=None, expected=(0,), trace=True):
        actual = environment if env is None else env
        executable = list(map(str, command))
        command = executable
        trace_path = logs / (name + ".trace")
        if trace:
            command = [
                trace_tool,
                "-f",
                "-s",
                "4096",
                "-e",
                "trace=process,network",
                "-o",
                str(trace_path),
                *command,
            ]
        result = subprocess.run(
            command, cwd=cwd, env=actual, capture_output=True, timeout=120, check=False
        )
        (logs / (name + ".stdout")).write_bytes(result.stdout)
        (logs / (name + ".stderr")).write_bytes(result.stderr)
        if result.returncode not in expected:
            (root / "failed-case.json").write_text(
                json.dumps({"case": name, "exit": result.returncode, "expected": expected}) + "\n",
                encoding="utf-8",
            )
        assert result.returncode in expected, (
            name,
            result.returncode,
            result.stdout[-2048:],
            result.stderr[-2048:],
        )
        record = {
            "case": name,
            "argv": executable,
            "environment": actual,
            "exit": result.returncode,
        }
        if trace:
            text = trace_path.read_text(encoding="utf-8")
            programs = re.findall(r'execve\("([^"\n]+)"', text)
            assert not any(
                re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(program).name)
                for program in programs
            ), name
            assert not any(
                Path(program).name in {"pip", "pip3", "uv", "curl", "wget"} for program in programs
            ), name
            record.update(programs=programs, network_connects=text.count("connect("))
        records.append(record)
        (root / "progress.json").write_text(
            json.dumps(
                {
                    "archive_sha256": args.sha256,
                    "completed_cases": records,
                    "full_acceptance": "Only report.json records a completed full run",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return result, record

    def inspect():
        result, _ = invoke(
            "inspect-" + str(len(records)),
            [prefix / "bin/ici", "setup", "--inspect", "--prefix", prefix, "--json"],
        )
        return json.loads(result.stdout)

    # The product builder first verifies all structure, provenance and digests;
    # the development harness then materializes only those verified regular files.
    result, _ = invoke(
        "inspect-archive",
        [builder, "inspect", "--archive", archive, "--sha256", args.sha256],
        trace=False,
    )
    manifest = json.loads(result.stdout)
    assert not any(
        PurePosixPath(name).suffix in {".py", ".pyc", ".pyz"} for name in manifest["files"]
    ), "A native distribution must not contain an ici Python runtime or worker"
    payload = root / "verified-payload"
    payload.mkdir()
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            assert (
                member.isfile()
                and not path.is_absolute()
                and all(part not in (".", "..") for part in path.parts)
            )
            output = payload.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as destination, source.extractfile(member) as input_file:
                shutil.copyfileobj(input_file, destination, 65536)
            output.chmod(
                0o755 if manifest["files"].get(member.name, {}).get("executable") else 0o644
            )
    readelf = shutil.which("readelf")
    assert readelf, "Actual ELF dependency evidence requires readelf"
    for name in ("bin/ici", "tools/bin/ruff", "tools/bin/ty"):
        result, _ = invoke(
            "elf-" + name.replace("/", "-"),
            [readelf, "--program-headers", "--dynamic", "--wide", payload / name],
            trace=False,
        )
        if name != "bin/ici" or manifest["target"].endswith("-musl"):
            assert b"INTERP" not in result.stdout and b"NEEDED" not in result.stdout, name
    for tool, compiler, notices, inventory in (
        ("ici-cli", "1.99.1", "THIRD-PARTY-NOTICES.txt", "licenses.json"),
        ("ruff", "1.97.1", "ruff-third-party.txt", "third-party/ruff-licenses.json"),
        ("ty", "1.99.0", "ty-third-party.txt", "third-party/ty-licenses.json"),
    ):
        document = json.loads((payload / "licenses" / inventory).read_text(encoding="utf-8"))
        assert document["root_package"] == tool
        assert document["notices_sha256"] == sha(payload / "licenses" / notices)
        additional = {record["id"] for record in document["additional"]}
        assert {
            f"rust-{compiler}-libunwind-LICENSE.TXT",
            f"rust-{compiler}-compiler-rt-LICENSE.TXT",
            f"rust-{compiler}-llvm-CREDITS.TXT",
        } <= additional
        if tool != "ici-cli" or manifest["target"].endswith("-musl"):
            assert "musl-1.2.5" in additional
        assert document["packages"] and all(
            record["files"] or record["evidence_status"] == "missing_upstream_notice"
            for record in document["packages"]
        ), "Unresolved upstream notice omissions must remain explicit"
    inputs = root / "build-inputs.json"
    inputs.write_text(json.dumps(manifest["build_inputs"]), encoding="utf-8")
    files = root / "files.json"
    files.write_text(
        json.dumps(
            [
                {"path": name, "source": str(payload / name), "executable": record["executable"]}
                for name, record in manifest["files"].items()
            ]
        ),
        encoding="utf-8",
    )
    reproduced = root / "reproduced.tar.gz"
    invoke(
        "reproduce-archive",
        [
            builder,
            "bundle",
            "--version",
            manifest["version"],
            "--inputs",
            inputs,
            "--files",
            files,
            "--output",
            reproduced,
        ],
        trace=False,
    )
    assert sha(reproduced) == args.sha256, (
        "archive bytes must reproduce from the same compiled inputs"
    )
    variant_inputs = {
        **manifest["build_inputs"],
        "source_date_epoch": manifest["build_inputs"]["source_date_epoch"] + 1,
    }
    variant_file = root / "lifecycle-inputs.json"
    variant_file.write_text(json.dumps(variant_inputs), encoding="utf-8")
    variant = root / "lifecycle-variant.tar.gz"
    invoke(
        "lifecycle-variant",
        [
            builder,
            "bundle",
            "--version",
            manifest["version"],
            "--inputs",
            variant_file,
            "--files",
            files,
            "--output",
            variant,
        ],
        trace=False,
    )
    variant_sha = sha(variant)
    assert variant_sha != args.sha256
    bootstrap_environment = {
        "PATH": str(standard),
        "TMPDIR": str(temporary),
        "ICI_PYTHON": "/missing/legacy/python",
    }
    _, record = invoke(
        "bootstrap-empty-python-path-no-home",
        ["/bin/sh", installer, "--archive", archive, "--sha256", args.sha256, "--prefix", prefix],
        env=bootstrap_environment,
    )
    assert record["network_connects"] == 0
    assert str(foreign_path) not in record["programs"]
    assert foreign_path.read_bytes() == foreign_body
    assert not list(temporary.iterdir()), "bootstrap snapshots must be cleaned after success"
    collision_result, _ = invoke(
        "report-foreign-path-command",
        [prefix / "bin/ici", "setup", "--inspect", "--prefix", prefix, "--json"],
        env={**environment, "PATH": str(standard)},
    )
    assert json.loads(collision_result.stdout)["path_collisions"] == [str(foreign_path)]
    first = inspect()
    for name, body in {
        "user-config.toml": b"[user]\nnewest = true\n",
        "user-results.json": b'{"newest":"observations"}',
    }.items():
        (prefix / name).write_bytes(body)
    preserved = {path.name: path.read_bytes() for path in prefix.glob("user-*")}
    # Deliberate selector collision is a failure injection, not a user-data cleanup.
    (prefix / "previous").write_text("owned fixture sentinel", encoding="utf-8")
    invoke(
        "failed-atomic-update",
        [
            prefix / "bin/ici",
            "setup",
            "--archive",
            variant,
            "--sha256",
            variant_sha,
            "--prefix",
            prefix,
        ],
        expected=(2,),
    )
    assert (prefix / "current").readlink() == Path("versions") / first["selected"]
    assert (prefix / "previous").read_text(encoding="utf-8") == "owned fixture sentinel"
    (prefix / "previous").unlink()
    assert inspect()["selected"] == first["selected"]
    invoke(
        "update",
        [
            prefix / "bin/ici",
            "setup",
            "--archive",
            variant,
            "--sha256",
            variant_sha,
            "--prefix",
            prefix,
        ],
    )
    second = inspect()
    assert second["selected"] != first["selected"] and second["previous"] == first["selected"]
    invoke("rollback", [prefix / "bin/ici", "setup", "--rollback", "--prefix", prefix])
    assert inspect()["selected"] == first["selected"]
    invoke(
        "reactivate",
        [prefix / "bin/ici", "setup", "--activate", second["selected"], "--prefix", prefix],
    )
    assert inspect()["selected"] == second["selected"]
    assert all((prefix / name).read_bytes() == body for name, body in preserved.items())
    assert foreign_path.read_bytes() == foreign_body
    assert not list((prefix / "versions").glob(".install-*"))

    def inspect_xdg(name, selected_root, xdg_environment):
        result, record = invoke(
            name,
            [selected_root / "bin/ici", "setup", "--inspect", "--json"],
            env=xdg_environment,
        )
        assert record["network_connects"] == 0
        value = json.loads(result.stdout)
        assert value["root"] == str(selected_root)
        return value

    for case, with_home in (("xdg-only", False), ("xdg-before-home", True)):
        data_home = root / case
        selected_root = data_home / "ici/native"
        xdg_environment = {**bootstrap_environment, "XDG_DATA_HOME": str(data_home)}
        if with_home:
            xdg_environment["HOME"] = str(home)
        _, bootstrap_record = invoke(
            case + "-bootstrap",
            ["/bin/sh", installer, "--archive", archive, "--sha256", args.sha256],
            env=xdg_environment,
        )
        assert bootstrap_record["network_connects"] == 0
        assert not list(temporary.iterdir())

        xdg_first = inspect_xdg(case + "-inspect-initial", selected_root, xdg_environment)
        _, update_record = invoke(
            case + "-update",
            [
                selected_root / "bin/ici",
                "setup",
                "--archive",
                variant,
                "--sha256",
                variant_sha,
            ],
            env=xdg_environment,
        )
        assert update_record["network_connects"] == 0
        xdg_second = inspect_xdg(case + "-inspect-updated", selected_root, xdg_environment)
        assert xdg_second["selected"] != xdg_first["selected"]
        assert xdg_second["previous"] == xdg_first["selected"]
        _, rollback_record = invoke(
            case + "-rollback",
            [selected_root / "bin/ici", "setup", "--rollback"],
            env=xdg_environment,
        )
        assert rollback_record["network_connects"] == 0
        assert (
            inspect_xdg(case + "-inspect-rolled-back", selected_root, xdg_environment)["selected"]
            == xdg_first["selected"]
        )
        assert not (home / ".local/share/ici/native").exists()
        assert foreign_path.read_bytes() == foreign_body
    # A malformed archive still contains the same verified native core; the core
    # must reject the traversal before activation rather than trust its own header.
    malformed = root / "malformed.tar.gz"
    with (
        tarfile.open(malformed, "w:gz", format=tarfile.USTAR_FORMAT) as output,
        tarfile.open(archive, "r:gz") as source,
    ):
        for member in source:
            output.addfile(member, source.extractfile(member))
        member = tarfile.TarInfo("../../outside-fixture")
        output.addfile(member)
    invoke(
        "unsafe-extraction",
        [
            prefix / "bin/ici",
            "setup",
            "--archive",
            malformed,
            "--sha256",
            sha(malformed),
            "--prefix",
            prefix,
        ],
        expected=(2,),
    )
    assert inspect()["selected"] == second["selected"]
    assert not (root / "outside-fixture").exists()
    invoke(
        "wrong-trusted-digest",
        [
            prefix / "bin/ici",
            "setup",
            "--archive",
            archive,
            "--sha256",
            "0" * 64,
            "--prefix",
            prefix,
        ],
        expected=(2,),
    )
    assert inspect()["selected"] == second["selected"]
    project = root / "consumer project"
    project.mkdir()
    source = project / "app.py"
    source.write_text(
        'def compute(value):\n    return value + missing_name\n\nanswer: int = "bad"\n',
        encoding="utf-8",
    )
    _, plan_record = invoke(
        "offline-plan", [prefix / "bin/ici", "analyze", "--plan", "--json"], cwd=project
    )
    assert len(plan_record["programs"]) == 1 and plan_record["network_connects"] == 0
    assert not (project / ".ici").exists()
    result, run_record = invoke(
        "offline-analysis",
        [prefix / "bin/ici", "analyze", "--json", "--no-cache"],
        cwd=project,
        expected=(1,),
    )
    parsed = json.loads(result.stdout)
    assert parsed["schema_id"] == "ici.next.run" and parsed["gate"]["selected"] == "FAIL"
    assert parsed["gate"]["workspace"] == "NOT_EVALUATED"
    assert any(
        finding["rule_id"] == "ruff.F821"
        and finding["primary_location"]["path"] == "app.py"
        and finding["primary_location"]["start_line"] == 2
        for finding in parsed["findings"]
    )
    programs = {Path(program).name for program in run_record["programs"]}
    assert (
        programs <= {"ici", "ruff", "ty"}
        and {"ruff", "ty"} <= programs
        and run_record["network_connects"] == 0
    )
    _, report_record = invoke(
        "offline-report",
        [prefix / "bin/ici", "next", "report", "--out", "report.html"],
        cwd=project,
    )
    assert len(report_record["programs"]) == 1 and report_record["network_connects"] == 0
    html = (project / "report.html").read_text(encoding="utf-8")
    assert "F821" in html and not re.search(
        r"<(?:script|link|img)\b[^>]*(?:src|href)\s*=\s*[\"\x27]https?://", html, re.I
    )
    source.write_text(
        "def compute(value: int) -> int:\n    return value + 1\n\n\nanswer: int = 1\n",
        encoding="utf-8",
    )
    result, fixed_record = invoke(
        "offline-fixed-analysis",
        [prefix / "bin/ici", "analyze", "--json", "--no-cache"],
        cwd=project,
    )
    assert (
        json.loads(result.stdout)["gate"]["selected"] == "PASS"
        and fixed_record["network_connects"] == 0
    )
    # Explicit network setup uses the actual product transport and a private CA;
    # private fixture keys exist only inside this temporary directory.
    with tempfile.TemporaryDirectory(prefix="native-setup-ca-", dir=root) as tls_temporary:
        tls = Path(tls_temporary)
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
                "/CN=ici artifact fixture CA",
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
        (tls / "extensions").write_text(
            "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n",
            encoding="utf-8",
        )
        for command in commands:
            subprocess.run(command, cwd=tls, check=True, capture_output=True)
        (tls / "empty").mkdir()
        ArchiveEndpoint.artifact = archive
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ArchiveEndpoint)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls / "server.pem", tls / "server.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"https://127.0.0.1:{server.server_port}"
        try:
            invoke(
                "untrusted-setup-ca",
                [
                    prefix / "bin/ici",
                    "setup",
                    "--url",
                    url + "/artifact",
                    "--sha256",
                    args.sha256,
                    "--prefix",
                    prefix,
                ],
                expected=(2,),
            )
            assert inspect()["selected"] == second["selected"]
            trusted = {
                **environment,
                "SSL_CERT_FILE": str(tls / "ca.pem"),
                "SSL_CERT_DIR": str(tls / "empty"),
            }
            invoke(
                "forbidden-setup-download",
                [
                    prefix / "bin/ici",
                    "setup",
                    "--url",
                    url + "/forbidden",
                    "--sha256",
                    args.sha256,
                    "--prefix",
                    prefix,
                ],
                env=trusted,
                expected=(2,),
            )
            assert inspect()["selected"] == second["selected"]
            invoke(
                "verified-redirected-setup",
                [
                    prefix / "bin/ici",
                    "setup",
                    "--url",
                    url + "/redirect",
                    "--sha256",
                    args.sha256,
                    "--prefix",
                    prefix,
                ],
                env=trusted,
            )
            assert inspect()["selected"] == first["selected"]
        finally:
            server.shutdown()
            server.server_close()
    initialized = root / "init consumer"
    initialized.mkdir()
    initialized_source = initialized / "app.py"
    initialized_source.write_text("answer = 42\n", encoding="utf-8")
    _, preview_record = invoke(
        "offline-init-preview", [prefix / "bin/ici", "next", "init", "--preview"], cwd=initialized
    )
    assert list(initialized.iterdir()) == [initialized_source]
    _, init_record = invoke("offline-init", [prefix / "bin/ici", "next", "init"], cwd=initialized)
    assert (initialized / "ici.toml").is_file()
    assert initialized_source.read_text(encoding="utf-8") == "answer = 42\n"
    assert all(
        len(record["programs"]) == 1 and record["network_connects"] == 0
        for record in (preview_record, init_record)
    )
    _, doctor_record = invoke(
        "offline-doctor", [prefix / "bin/ici", "next", "doctor", "--json"], cwd=project
    )
    assert {Path(program).name for program in doctor_record["programs"]} <= {"ici", "ruff", "ty"}
    assert doctor_record["network_connects"] == 0
    report = {
        "archive_sha256": args.sha256,
        "archive_size": archive.stat().st_size,
        "target": manifest["target"],
        "source_tree_sha256": manifest["build_inputs"]["source_tree_sha256"],
        "archive_reproduction": "identical from fixed compiled payload/build inputs; not a second compiler rebuild",
        "lifecycle_variant": "same native version with an explicit epoch input change; not a separate release",
        "cases": records,
        "evidence_directory": str(root),
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "cases": len(records),
                "target": manifest["target"],
                "evidence_directory": str(root),
                "archive_sha256": args.sha256,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
