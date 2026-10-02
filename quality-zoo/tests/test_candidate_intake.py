from __future__ import annotations

import hashlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from runner import candidate_intake
from runner.candidate_intake import ContractError

from tests.helpers import (
    PACKAGE_VERSION,
    REPOSITORY,
    TARGET_SHA,
    candidate_executable,
    candidate_members,
    candidate_provenance,
    sha256_bytes,
    write_candidate_archive,
    write_zip,
)


class CandidateIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.destination = self.root / "extracted"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def archive_for_members(
        self, members: list[tuple[zipfile.ZipInfo, bytes]], name: str = "candidate.zip"
    ) -> Path:
        archive = self.root / name
        write_zip(archive, members)
        return archive

    def valid_archive(
        self,
        *,
        executable: bytes | None = None,
        provenance: dict[str, object] | None = None,
        **kwargs: object,
    ) -> Path:
        archive = self.root / "candidate.zip"
        write_candidate_archive(
            archive,
            executable=executable,
            provenance=provenance,
            **kwargs,
        )
        return archive

    def verify(self, archive: Path, destination: Path | None = None, **kwargs: object):
        if destination is None:
            destination = self.destination
        archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        return candidate_intake.verify_candidate_archive(
            archive,
            destination,
            expected_archive_sha256=kwargs.pop(
                "expected_archive_sha256", archive_digest
            ),
            expected_repository=kwargs.pop("expected_repository", REPOSITORY),
            expected_target_sha=kwargs.pop("expected_target_sha", TARGET_SHA),
            **kwargs,
        )

    def assert_rejected(
        self,
        archive: Path,
        code: str,
        *,
        destination: Path | None = None,
        **kwargs: object,
    ) -> ContractError:
        if destination is None:
            destination = self.destination
        with self.assertRaises(ContractError) as raised:
            self.verify(archive, destination, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def replace_member(
        self,
        members: list[tuple[zipfile.ZipInfo, bytes]],
        name: str,
        info: zipfile.ZipInfo,
        data: bytes,
    ) -> list[tuple[zipfile.ZipInfo, bytes]]:
        return [
            (info, data) if current.filename == name else (current, value)
            for current, value in members
        ]

    def test_valid_bundle_is_extracted_with_verified_identity(self) -> None:
        archive = self.valid_archive()

        bundle = self.verify(archive)

        self.assertEqual(bundle.root, self.destination)
        self.assertEqual(bundle.executable, self.destination / "ici.pyz")
        self.assertEqual(bundle.version, f"ici {PACKAGE_VERSION}")
        self.assertEqual(bundle.archive_sha256, sha256_bytes(archive.read_bytes()))
        self.assertEqual(
            bundle.executable_sha256,
            sha256_bytes((self.destination / "ici.pyz").read_bytes()),
        )
        self.assertEqual(
            bundle.executable_size, (self.destination / "ici.pyz").stat().st_size
        )
        self.assertEqual(bundle.provenance["repository"], REPOSITORY)
        self.assertEqual(
            stat.S_IMODE((self.destination / "ici.pyz").stat().st_mode), 0o755
        )
        self.assertEqual(
            stat.S_IMODE(
                (self.destination / "candidate-provenance.json").stat().st_mode
            ),
            0o644,
        )

    def test_main_json_summary_reports_verified_bundle(self) -> None:
        archive = self.valid_archive()
        archive_digest = sha256_bytes(archive.read_bytes())
        output = io.StringIO()

        with redirect_stdout(output):
            result = candidate_intake.main(
                [
                    "--archive",
                    str(archive),
                    "--archive-sha256",
                    archive_digest,
                    "--repository",
                    REPOSITORY,
                    "--target-sha",
                    TARGET_SHA,
                    "--destination",
                    str(self.destination),
                    "--json",
                ]
            )

        self.assertEqual(result, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["schema"], "quality-zoo.candidate-intake/v1")
        self.assertEqual(summary["archive_sha256"], archive_digest)
        self.assertEqual(summary["target_sha"], TARGET_SHA)
        self.assertEqual(summary["version"], f"ici {PACKAGE_VERSION}")

    def test_duplicate_archive_member_is_rejected_before_extraction(self) -> None:
        members = candidate_members()
        archive = self.archive_for_members(members + [members[-1]])

        self.assert_rejected(archive, "duplicate-archive-member")
        self.assertFalse(self.destination.exists())

    def test_extra_archive_member_is_rejected(self) -> None:
        members = candidate_members()
        extra = candidate_members()[0][0]
        extra.filename = "unexpected.txt"
        archive = self.archive_for_members(members + [(extra, b"unexpected")])

        self.assert_rejected(archive, "unexpected-archive-members")
        self.assertFalse(self.destination.exists())

    def test_traversal_archive_member_is_rejected_and_cannot_escape_destination(
        self,
    ) -> None:
        members = candidate_members()
        traversal = candidate_members()[0][0]
        traversal.filename = "../escaped.txt"
        archive = self.archive_for_members(members + [(traversal, b"outside")])

        self.assert_rejected(archive, "unexpected-archive-members")
        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertFalse(self.destination.exists())

    def test_absolute_archive_member_is_rejected(self) -> None:
        members = candidate_members()
        absolute = candidate_members()[0][0]
        absolute.filename = "/tmp/escaped.txt"
        archive = self.archive_for_members(members + [(absolute, b"outside")])

        self.assert_rejected(archive, "unexpected-archive-members")
        self.assertFalse(self.destination.exists())

    def test_symlink_archive_member_is_rejected(self) -> None:
        members = candidate_members()
        symlink_info = candidate_members()[0][0]
        symlink_info.filename = "ici.pyz"
        symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.archive_for_members(
            self.replace_member(
                members, "ici.pyz", symlink_info, b"candidate-provenance.json"
            )
        )

        self.assert_rejected(archive, "unsafe-archive-mode")
        self.assertFalse(self.destination.exists())

    def test_wrong_mode_archive_member_is_rejected(self) -> None:
        archive = self.valid_archive(executable_mode=0o644)

        self.assert_rejected(archive, "unsafe-archive-mode")
        self.assertFalse(self.destination.exists())

    def test_compressed_archive_member_is_rejected(self) -> None:
        archive = self.valid_archive(compress_type=zipfile.ZIP_DEFLATED)

        self.assert_rejected(archive, "unexpected-compression")
        self.assertFalse(self.destination.exists())

    def test_invalid_zip_is_rejected_and_destination_is_absent(self) -> None:
        archive = self.root / "invalid.zip"
        archive.write_bytes(b"this is not a zip")

        self.assert_rejected(archive, "invalid-archive")
        self.assertFalse(self.destination.exists())

    def test_existing_destination_is_never_replaced(self) -> None:
        archive = self.valid_archive()
        self.destination.mkdir()
        sentinel = self.destination / "sentinel"
        sentinel.write_text("keep me", encoding="utf-8")

        self.assert_rejected(archive, "destination-exists")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep me")

    def test_invalid_expected_digests_are_rejected_before_archive_read(self) -> None:
        archive = self.valid_archive()

        self.assert_rejected(
            archive, "invalid-archive-digest", expected_archive_sha256="g" * 64
        )
        self.assertFalse(self.destination.exists())
        self.assert_rejected(
            archive, "invalid-target-sha", expected_target_sha="not-a-commit"
        )
        self.assertFalse(self.destination.exists())

    def test_archive_digest_mismatch_is_rejected(self) -> None:
        archive = self.valid_archive()

        self.assert_rejected(
            archive, "archive-digest-mismatch", expected_archive_sha256="0" * 64
        )
        self.assertFalse(self.destination.exists())

    def test_provenance_requires_exact_fields(self) -> None:
        executable = candidate_executable()
        provenance = candidate_provenance(executable, unexpected="field")
        archive = self.valid_archive(executable=executable, provenance=provenance)

        self.assert_rejected(archive, "provenance-fields")
        self.assertFalse(self.destination.exists())

    def test_provenance_repository_target_and_workflow_identity_must_match(
        self,
    ) -> None:
        cases = (
            ("repository-mismatch", {"repository": "other/repository"}),
            ("target-sha-mismatch", {"target_sha": "b" * 40}),
            ("workflow-sha-mismatch", {"candidate_workflow_definition_sha": "b" * 40}),
            (
                "gate-job-url-mismatch",
                {"merge_gate_job_url": "https://example.invalid/job"},
            ),
            (
                "gate-run-url-mismatch",
                {"merge_gate_url": "https://example.invalid/run"},
            ),
        )
        for code, overrides in cases:
            with self.subTest(code=code):
                executable = candidate_executable()
                provenance = candidate_provenance(executable, **overrides)
                archive = self.valid_archive(
                    executable=executable, provenance=provenance
                )
                self.assert_rejected(archive, code)
                self.assertFalse(self.destination.exists())

    def test_provenance_digest_and_size_must_match_executable(self) -> None:
        executable = candidate_executable()
        bad_digest = candidate_provenance(executable, artifact_file_sha256="0" * 64)
        archive = self.valid_archive(executable=executable, provenance=bad_digest)
        self.assert_rejected(archive, "executable-digest-mismatch")
        self.assertFalse(self.destination.exists())

        bad_size = candidate_provenance(
            executable, artifact_file_size=len(executable) + 1
        )
        archive = self.valid_archive(executable=executable, provenance=bad_size)
        self.assert_rejected(archive, "executable-size-mismatch")
        self.assertFalse(self.destination.exists())

    def test_sidecar_must_be_canonical(self) -> None:
        executable = candidate_executable()
        members = candidate_members(executable)
        bad_sidecar = candidate_members(executable)[1][0]
        archive = self.archive_for_members(
            self.replace_member(
                members, "ici.pyz.sha256", bad_sidecar, b"0" * 64 + b" ici.pyz\n"
            )
        )

        self.assert_rejected(archive, "sidecar-mismatch")
        self.assertFalse(self.destination.exists())

    def test_version_mismatch_cleans_extracted_destination(self) -> None:
        executable = candidate_executable(version="9.9.9")
        provenance = candidate_provenance(executable, package_version=PACKAGE_VERSION)
        archive = self.valid_archive(executable=executable, provenance=provenance)

        self.assert_rejected(archive, "version-mismatch")
        self.assertFalse(self.destination.exists())

    def test_candidate_version_failure_and_invalid_output_clean_destination(
        self,
    ) -> None:
        for code, executable in (
            ("candidate-version-failed", candidate_executable(exit_code=7)),
            ("candidate-version-invalid", candidate_executable(output="not ici\n")),
        ):
            with self.subTest(code=code):
                provenance = candidate_provenance(executable)
                archive = self.valid_archive(
                    executable=executable, provenance=provenance
                )
                self.assert_rejected(archive, code)
                self.assertFalse(self.destination.exists())

    def test_archive_path_symlink_is_rejected(self) -> None:
        archive = self.valid_archive()
        symlink = self.root / "candidate-link.zip"
        symlink.symlink_to(archive)

        self.assert_rejected(symlink, "unsafe-file")
        self.assertFalse(self.destination.exists())

    def test_provenance_json_duplicate_key_is_rejected_and_cleaned(self) -> None:
        executable = candidate_executable()
        members = candidate_members(executable)
        duplicate_json = (
            json.dumps(candidate_provenance(executable), sort_keys=True).rstrip("}")
            + ', "schema": "ici.candidate/v1"}\n'
        ).encode("utf-8")
        provenance_info = candidate_members(executable)[0][0]
        archive = self.archive_for_members(
            self.replace_member(
                members, "candidate-provenance.json", provenance_info, duplicate_json
            )
        )

        self.assert_rejected(archive, "duplicate-json-key")
        self.assertFalse(self.destination.exists())

    def github_evidence_payloads(
        self, archive: Path, provenance: dict[str, object]
    ) -> dict[str, dict[str, object]]:
        """Build the five exact GitHub API snapshots required by intake."""

        repository = str(provenance["repository"])
        target_sha = str(provenance["target_sha"])
        candidate_run_id = int(provenance["candidate_run_id"])
        candidate_run_attempt = int(provenance["candidate_run_attempt"])
        gate_run_id = int(provenance["merge_gate_run_id"])
        gate_run_attempt = int(provenance["merge_gate_run_attempt"])
        gate_job_id = int(provenance["merge_gate_job_id"])
        api_root = f"https://api.github.com/repos/{repository}"
        web_root = f"https://github.com/{repository}/actions/runs"
        gate_job_url = f"{web_root}/{gate_run_id}/job/{gate_job_id}"
        archive_sha = sha256_bytes(archive.read_bytes())
        artifact_id = 300
        return {
            "artifact.json": {
                "id": artifact_id,
                "name": f"ici-candidate-{target_sha}",
                "size_in_bytes": archive.stat().st_size,
                "digest": f"sha256:{archive_sha}",
                "expired": False,
                "url": f"{api_root}/actions/artifacts/{artifact_id}",
                "archive_download_url": f"{api_root}/actions/artifacts/{artifact_id}/zip",
                "workflow_run": {
                    "id": candidate_run_id,
                    "head_branch": "main",
                    "head_sha": target_sha,
                },
            },
            "candidate-run.json": {
                "id": candidate_run_id,
                "run_attempt": candidate_run_attempt,
                "head_sha": target_sha,
                "head_branch": "main",
                "path": provenance["candidate_workflow"],
                "name": "Build ici Candidate Artifact",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "url": f"{api_root}/actions/runs/{candidate_run_id}",
                "html_url": f"{web_root}/{candidate_run_id}",
                "repository": {"full_name": repository},
            },
            "gate-check.json": {
                "id": provenance["merge_gate_check_run_id"],
                "name": "Merge Gate",
                "head_sha": target_sha,
                "status": "completed",
                "conclusion": "success",
                "details_url": gate_job_url,
                "html_url": gate_job_url,
                "url": f"{api_root}/check-runs/{gate_job_id}",
            },
            "gate-job.json": {
                "id": gate_job_id,
                "run_id": gate_run_id,
                "run_attempt": gate_run_attempt,
                "name": "Merge Gate",
                "workflow_name": "CI Quality Gate (Dogfooding)",
                "head_sha": target_sha,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "html_url": gate_job_url,
                "url": f"{api_root}/actions/jobs/{gate_job_id}",
                "check_run_url": f"{api_root}/check-runs/{gate_job_id}",
            },
            "gate-run.json": {
                "id": gate_run_id,
                "run_attempt": gate_run_attempt,
                "name": "CI Quality Gate (Dogfooding)",
                "head_sha": target_sha,
                "head_branch": "main",
                "path": ".github/workflows/ci.yml",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "url": f"{api_root}/actions/runs/{gate_run_id}",
                "html_url": provenance["merge_gate_url"],
                "repository": {"full_name": repository},
            },
        }

    def write_github_evidence(
        self, evidence_dir: Path, archive: Path, provenance: dict[str, object]
    ) -> dict[str, dict[str, object]]:
        payloads = self.github_evidence_payloads(archive, provenance)
        evidence_dir.mkdir()
        for name, payload in payloads.items():
            (evidence_dir / name).write_text(
                json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
            )
        return payloads

    def test_github_evidence_snapshot_verifies_all_five_api_objects(self) -> None:
        executable = candidate_executable()
        provenance = candidate_provenance(executable)
        archive = self.valid_archive(executable=executable, provenance=provenance)
        evidence_dir = self.root / "github-evidence"
        self.write_github_evidence(evidence_dir, archive, provenance)

        bundle = self.verify(archive, github_evidence_dir=evidence_dir)

        self.assertTrue(bundle.github_evidence_verified)
        self.assertEqual(bundle.provenance["target_sha"], TARGET_SHA)
        self.assertTrue(self.destination.is_dir())

    def test_github_evidence_requires_exactly_five_non_symlink_files(self) -> None:
        archive = self.valid_archive()
        provenance = candidate_provenance(candidate_executable())
        evidence_dir = self.root / "github-evidence-extra"
        self.write_github_evidence(evidence_dir, archive, provenance)
        (evidence_dir / "extra.json").write_text("{}", encoding="utf-8")

        self.assert_rejected(
            archive,
            "github-evidence-files",
            github_evidence_dir=evidence_dir,
        )
        self.assertFalse(self.destination.exists())

        missing_dir = self.root / "github-evidence-missing"
        self.write_github_evidence(missing_dir, archive, provenance)
        (missing_dir / "gate-run.json").unlink()
        self.assert_rejected(
            archive,
            "github-evidence-files",
            destination=self.root / "missing-extracted",
            github_evidence_dir=missing_dir,
        )

    def test_tampering_each_github_evidence_identity_cleans_destination(self) -> None:
        cases = (
            ("artifact.json", "name", "ici-candidate-tampered"),
            ("candidate-run.json", "head_sha", "b" * 40),
            ("gate-check.json", "conclusion", "failure"),
            ("gate-job.json", "run_id", 999),
            ("gate-run.json", "event", "workflow_dispatch"),
        )
        for filename, field, value in cases:
            with self.subTest(filename=filename, field=field):
                archive = self.valid_archive()
                provenance = candidate_provenance(candidate_executable())
                evidence_dir = self.root / f"evidence-{field}"
                payloads = self.write_github_evidence(evidence_dir, archive, provenance)
                payloads[filename][field] = value
                (evidence_dir / filename).write_text(
                    json.dumps(payloads[filename], sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                destination = self.root / f"extracted-{field}"

                self.assert_rejected(
                    archive,
                    "github-evidence-mismatch",
                    destination=destination,
                    github_evidence_dir=evidence_dir,
                )
                self.assertFalse(destination.exists())

    def test_github_evidence_symlink_file_is_rejected(self) -> None:
        archive = self.valid_archive()
        provenance = candidate_provenance(candidate_executable())
        evidence_dir = self.root / "github-evidence-symlink"
        self.write_github_evidence(evidence_dir, archive, provenance)
        target = self.root / "artifact-copy.json"
        target.write_text(
            (evidence_dir / "artifact.json").read_text(), encoding="utf-8"
        )
        (evidence_dir / "artifact.json").unlink()
        (evidence_dir / "artifact.json").symlink_to(target)

        self.assert_rejected(
            archive,
            "unsafe-evidence",
            github_evidence_dir=evidence_dir,
        )
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
