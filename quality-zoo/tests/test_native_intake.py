from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from runner import native_intake
from runner.common import ContractError

from tests.helpers import candidate_executable

VERSION = "0.14.0-dev.0"
REVISION = "1" * 40
TARGET = "x86_64-unknown-linux-musl"


def manifest(files: dict[str, tuple[bytes, bool]], **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_id": "ici.native-distribution",
        "schema_version": 1,
        "version": VERSION,
        "target": TARGET,
        "build_inputs": {
            "source_revision": REVISION,
            "target": TARGET,
            "platform_evidence": {"development_snapshot": "1"},
        },
        "build_inputs_sha256": "2" * 64,
        "files": {
            name: {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "executable": executable,
            }
            for name, (data, executable) in files.items()
        },
    }
    payload.update(updates)
    return payload


def add(archive: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644, **kind: object):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    for key, value in kind.items():
        setattr(info, key, value)
    archive.addfile(info, io.BytesIO(data) if info.isreg() else None)


class NativeIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.destination = self.root / "candidate"
        self.executable = candidate_executable(VERSION)
        self.files = {
            "bin/ici": (self.executable, True),
            "README.txt": (b"ici native candidate\n", False),
            "tools/bin/ruff": (b"#!/bin/sh\n", True),
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def archive(
        self,
        *,
        payload: dict[str, object] | None = None,
        members: dict[str, tuple[bytes, bool]] | None = None,
        extra=None,
    ) -> Path:
        members = self.files if members is None else members
        payload = manifest(self.files) if payload is None else payload
        path = self.root / "ici.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            add(archive, "manifest.json", json.dumps(payload).encode())
            for name, (data, executable) in members.items():
                add(archive, name, data, 0o755 if executable else 0o644)
            if extra is not None:
                extra(archive)
        return path

    def verify(self, archive: Path, **kwargs: object):
        digest = kwargs.pop("expected", hashlib.sha256(archive.read_bytes()).hexdigest())
        return native_intake.verify_native_archive(
            archive, self.destination, expected_archive_sha256=digest, **kwargs
        )

    def assert_rejected(self, archive: Path, code: str, **kwargs: object) -> None:
        with self.assertRaises(ContractError) as raised:
            self.verify(archive, **kwargs)
        self.assertEqual(raised.exception.code, code, raised.exception.message)
        self.assertFalse(self.destination.exists())

    def test_valid_archive_extracts_only_the_core_into_a_private_directory(self) -> None:
        archive = self.archive()
        candidate = self.verify(archive)
        self.assertEqual(candidate.version, VERSION)
        self.assertEqual(candidate.target, TARGET)
        self.assertEqual(candidate.source_revision, REVISION)
        self.assertTrue(candidate.development_snapshot)
        self.assertEqual(candidate.archive_sha256, hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(candidate.executable_sha256, hashlib.sha256(self.executable).hexdigest())
        self.assertEqual(candidate.executable, self.destination / "bin/ici")
        self.assertEqual(
            sorted(str(path.relative_to(self.destination)) for path in self.destination.rglob("*")),
            ["bin", "bin/ici", "manifest.json"],
        )
        for directory in (self.destination, self.destination / "bin"):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(candidate.executable.stat().st_mode), 0o700)

    def test_cli_prints_the_producer_version_and_identity(self) -> None:
        archive = self.archive()
        output = io.StringIO()
        with redirect_stdout(output):
            code = native_intake.main(
                [
                    "--archive",
                    str(archive),
                    "--archive-sha256",
                    hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "--destination",
                    str(self.destination),
                ]
            )
        self.assertEqual(code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["schema"], "quality-zoo.native-intake/v1")
        self.assertEqual(summary["producer_version"], VERSION)
        self.assertEqual(summary["executable"], str(self.destination / "bin/ici"))

    def test_untrusted_digest_and_existing_destination_are_refused(self) -> None:
        archive = self.archive()
        self.assert_rejected(archive, "archive-digest-mismatch", expected="0" * 64)
        self.assert_rejected(archive, "invalid-archive-digest", expected="ABC")
        self.destination.mkdir()
        with self.assertRaises(ContractError) as raised:
            self.verify(archive)
        self.assertEqual(raised.exception.code, "destination-exists")

    def test_member_set_must_equal_the_manifest(self) -> None:
        self.assert_rejected(
            self.archive(members={**self.files, "extra.txt": (b"x", False)}),
            "unexpected-archive-members",
        )
        partial = dict(self.files)
        del partial["README.txt"]
        self.assert_rejected(self.archive(members=partial), "unexpected-archive-members")

    def test_unsafe_members_are_refused(self) -> None:
        self.assert_rejected(
            self.archive(extra=lambda a: add(a, "../escape", b"x")), "unsafe-archive-path"
        )
        self.assert_rejected(
            self.archive(extra=lambda a: add(a, "/abs", b"x")), "unsafe-archive-path"
        )
        self.assert_rejected(
            self.archive(
                extra=lambda a: add(a, "link", b"", type=tarfile.SYMTYPE, linkname="bin/ici")
            ),
            "unsafe-archive-member",
        )
        self.assert_rejected(
            self.archive(extra=lambda a: add(a, "README.txt", b"again")),
            "duplicate-archive-member",
        )

    def test_core_bytes_and_version_must_equal_the_manifest(self) -> None:
        other = candidate_executable("9.9.9")
        self.assert_rejected(
            self.archive(members={**self.files, "bin/ici": (other, True)}),
            "executable-member",
        )
        same_size = self.executable.replace(VERSION.encode(), b"0.14.0-dev.9")
        self.assert_rejected(
            self.archive(members={**self.files, "bin/ici": (same_size, True)}),
            "executable-digest-mismatch",
        )
        lying = candidate_executable("0.14.1-dev.0")
        files = {**self.files, "bin/ici": (lying, True)}
        self.assert_rejected(
            self.archive(payload=manifest(files), members=files), "version-mismatch"
        )
        not_executable = {**self.files, "bin/ici": (self.executable, False)}
        self.assert_rejected(
            self.archive(payload=manifest(not_executable)), "manifest-executable"
        )

    def test_manifest_identity_is_checked(self) -> None:
        for updates, code in (
            ({"schema_id": "ici.candidate/v1"}, "manifest-schema"),
            ({"schema_version": 2}, "manifest-schema"),
            ({"target": "aarch64-apple-darwin"}, "manifest-target"),
            ({"version": "latest"}, "manifest-version"),
            (
                {"build_inputs": {"source_revision": "main", "target": TARGET}},
                "manifest-revision",
            ),
        ):
            self.assert_rejected(self.archive(payload=manifest(self.files, **updates)), code)
        path = self.root / "ici.tar.gz"
        path.write_bytes(b"not a gzip stream")
        self.assert_rejected(path, "invalid-archive")


if __name__ == "__main__":
    unittest.main()
