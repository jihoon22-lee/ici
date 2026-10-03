"""Safely verify and extract one provenance-bound ici candidate bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runner.common import (
    SHA256_RE,
    ContractError,
    load_json_object,
    require_bool,
    require_int,
    require_string,
    sha256_file,
)

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
EXPECTED_MEMBERS = {
    "candidate-provenance.json": 0o644,
    "ici.pyz.sha256": 0o644,
    "ici.pyz": 0o755,
}
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_PATH = ".github/workflows/candidate-artifact.yml"


@dataclass(frozen=True)
class CandidateBundle:
    root: Path
    executable: Path
    archive_sha256: str
    executable_sha256: str
    executable_size: int
    version: str
    provenance: dict[str, Any]
    github_evidence_verified: bool


def _validate_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ContractError(
            "duplicate-archive-member", "candidate ZIP repeats a member"
        )
    if set(names) != set(EXPECTED_MEMBERS) or len(names) != len(EXPECTED_MEMBERS):
        raise ContractError(
            "unexpected-archive-members",
            f"expected exactly {sorted(EXPECTED_MEMBERS)!r}, got {sorted(names)!r}",
        )
    total_size = 0
    for info in infos:
        if (
            info.filename.startswith("/")
            or "\\" in info.filename
            or ".." in Path(info.filename).parts
        ):
            raise ContractError(
                "unsafe-archive-path", f"unsafe member {info.filename!r}"
            )
        if info.flag_bits & 0x1:
            raise ContractError(
                "encrypted-archive", f"encrypted member {info.filename!r}"
            )
        if info.compress_type != zipfile.ZIP_STORED:
            raise ContractError(
                "unexpected-compression", f"compressed member {info.filename!r}"
            )
        if info.file_size > MAX_MEMBER_BYTES or info.compress_size > MAX_MEMBER_BYTES:
            raise ContractError(
                "archive-member-too-large", f"oversized member {info.filename!r}"
            )
        total_size += info.file_size
        mode = (info.external_attr >> 16) & 0xFFFF
        if (
            not stat.S_ISREG(mode)
            or stat.S_IMODE(mode) != EXPECTED_MEMBERS[info.filename]
        ):
            raise ContractError(
                "unsafe-archive-mode",
                f"{info.filename!r} mode is {mode:#o}, expected regular {EXPECTED_MEMBERS[info.filename]:#o}",
            )
    if total_size > MAX_ARCHIVE_BYTES:
        raise ContractError(
            "archive-too-large", "candidate ZIP expands beyond its limit"
        )
    return infos


def _safe_write_member(destination: Path, info: zipfile.ZipInfo, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    mode = EXPECTED_MEMBERS[info.filename]
    try:
        descriptor = os.open(destination, flags, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
    except OSError as error:
        raise ContractError(
            "extract-failed", f"cannot create {destination}: {error}"
        ) from error


def _extract_archive(archive_path: Path, destination: Path) -> None:
    if os.path.lexists(destination):
        raise ContractError("destination-exists", f"refusing to replace {destination}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
        )
    except OSError as error:
        raise ContractError(
            "extract-failed", f"cannot prepare destination: {error}"
        ) from error
    try:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = _validate_archive_members(archive)
                for info in infos:
                    data = archive.read(info)
                    if len(data) != info.file_size:
                        raise ContractError("truncated-archive-member", info.filename)
                    _safe_write_member(scratch / info.filename, info, data)
        except zipfile.BadZipFile as error:
            raise ContractError(
                "invalid-archive", f"candidate ZIP is invalid: {error}"
            ) from error
        scratch.replace(destination)
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


def _validate_provenance(
    payload: dict[str, Any], *, expected_repository: str, expected_target_sha: str
) -> None:
    required_keys = {
        "artifact_file",
        "artifact_file_sha256",
        "artifact_file_size",
        "candidate_run_attempt",
        "candidate_run_id",
        "candidate_workflow",
        "candidate_workflow_definition_sha",
        "channel",
        "merge_gate_check_run_id",
        "merge_gate_job_id",
        "merge_gate_job_url",
        "merge_gate_run_attempt",
        "merge_gate_run_id",
        "merge_gate_url",
        "package_version",
        "repository",
        "retention_days",
        "schema",
        "stable",
        "target_sha",
    }
    if set(payload) != required_keys:
        raise ContractError(
            "provenance-fields",
            f"candidate provenance fields differ: {sorted(set(payload) ^ required_keys)!r}",
        )
    if require_string(payload["schema"], "schema") != "ici.candidate/v1":
        raise ContractError("provenance-schema", "expected ici.candidate/v1")
    if require_string(payload["channel"], "channel") != "candidate":
        raise ContractError("provenance-channel", "candidate channel is required")
    if require_bool(payload["stable"], "stable") is not False:
        raise ContractError("stable-candidate", "candidate must declare stable=false")
    if require_string(payload["artifact_file"], "artifact_file") != "ici.pyz":
        raise ContractError("artifact-name", "candidate executable must be ici.pyz")
    if (
        require_string(payload["candidate_workflow"], "candidate_workflow")
        != WORKFLOW_PATH
    ):
        raise ContractError("workflow-path", "unexpected candidate workflow path")
    repository = require_string(payload["repository"], "repository")
    if not REPOSITORY_RE.fullmatch(repository) or repository != expected_repository:
        raise ContractError(
            "repository-mismatch", f"expected {expected_repository}, got {repository}"
        )
    target_sha = require_string(payload["target_sha"], "target_sha")
    if not FULL_SHA_RE.fullmatch(target_sha) or target_sha != expected_target_sha:
        raise ContractError(
            "target-sha-mismatch", f"expected {expected_target_sha}, got {target_sha}"
        )
    definition_sha = require_string(
        payload["candidate_workflow_definition_sha"],
        "candidate_workflow_definition_sha",
    )
    if definition_sha != target_sha:
        raise ContractError(
            "workflow-sha-mismatch", "workflow definition SHA must equal target SHA"
        )
    digest = require_string(payload["artifact_file_sha256"], "artifact_file_sha256")
    if not SHA256_RE.fullmatch(digest):
        raise ContractError(
            "invalid-artifact-digest", "artifact digest must be lowercase SHA-256"
        )
    require_int(payload["artifact_file_size"], "artifact_file_size", minimum=1)
    for key in (
        "candidate_run_attempt",
        "candidate_run_id",
        "merge_gate_check_run_id",
        "merge_gate_job_id",
        "merge_gate_run_attempt",
        "merge_gate_run_id",
        "retention_days",
    ):
        require_int(payload[key], key, minimum=1)
    if payload["merge_gate_check_run_id"] != payload["merge_gate_job_id"]:
        raise ContractError(
            "gate-id-mismatch", "Merge Gate check and Actions job IDs differ"
        )
    expected_job_url = (
        f"https://github.com/{repository}/actions/runs/{payload['merge_gate_run_id']}"
        f"/job/{payload['merge_gate_job_id']}"
    )
    expected_run_url = (
        f"https://github.com/{repository}/actions/runs/{payload['merge_gate_run_id']}"
    )
    if (
        require_string(payload["merge_gate_job_url"], "merge_gate_job_url")
        != expected_job_url
    ):
        raise ContractError(
            "gate-job-url-mismatch", "Merge Gate job URL is not canonical"
        )
    if require_string(payload["merge_gate_url"], "merge_gate_url") != expected_run_url:
        raise ContractError(
            "gate-run-url-mismatch", "Merge Gate run URL is not canonical"
        )
    require_string(payload["package_version"], "package_version")


def _read_version(executable: Path, *, timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError("candidate-execution-failed", str(error)) from error
    if completed.returncode != 0:
        raise ContractError(
            "candidate-version-failed",
            f"exit {completed.returncode}: {completed.stderr.strip()}",
        )
    version = completed.stdout.strip()
    if not re.fullmatch(r"ici [0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ContractError(
            "candidate-version-invalid", f"unexpected version output {version!r}"
        )
    return version


def _exact_identity(
    payload: dict[str, Any], expected: dict[str, Any], *, label: str
) -> None:
    for field, value in expected.items():
        if payload.get(field) != value:
            raise ContractError(
                "github-evidence-mismatch",
                f"{label}.{field} is {payload.get(field)!r}, expected {value!r}",
            )


def _validate_github_evidence(
    evidence_dir: Path,
    provenance: dict[str, Any],
    *,
    archive_sha256: str,
    archive_size: int,
) -> None:
    expected_files = {
        "artifact.json",
        "candidate-run.json",
        "gate-check.json",
        "gate-job.json",
        "gate-run.json",
    }
    try:
        if not evidence_dir.is_dir() or evidence_dir.is_symlink():
            raise ContractError(
                "unsafe-evidence", "GitHub evidence must be a directory"
            )
        entries = {item.name for item in evidence_dir.iterdir()}
    except OSError as error:
        raise ContractError("evidence-read-failed", str(error)) from error
    if entries != expected_files:
        raise ContractError(
            "github-evidence-files",
            f"expected exactly {sorted(expected_files)!r}, got {sorted(entries)!r}",
        )
    for name in expected_files:
        path = evidence_dir / name
        if not path.is_file() or path.is_symlink():
            raise ContractError("unsafe-evidence", f"unsafe evidence file {name!r}")

    artifact = load_json_object(
        evidence_dir / "artifact.json",
        label="artifact API evidence",
        max_bytes=2 * 1024 * 1024,
    )
    candidate_run = load_json_object(
        evidence_dir / "candidate-run.json",
        label="candidate run API evidence",
        max_bytes=2 * 1024 * 1024,
    )
    check = load_json_object(
        evidence_dir / "gate-check.json",
        label="gate check API evidence",
        max_bytes=2 * 1024 * 1024,
    )
    job = load_json_object(
        evidence_dir / "gate-job.json",
        label="gate job API evidence",
        max_bytes=2 * 1024 * 1024,
    )
    gate_run = load_json_object(
        evidence_dir / "gate-run.json",
        label="gate run API evidence",
        max_bytes=2 * 1024 * 1024,
    )

    repository = provenance["repository"]
    target_sha = provenance["target_sha"]
    candidate_run_id = provenance["candidate_run_id"]
    gate_run_id = provenance["merge_gate_run_id"]
    gate_job_id = provenance["merge_gate_job_id"]
    api_root = f"https://api.github.com/repos/{repository}"
    web_root = f"https://github.com/{repository}/actions/runs"
    artifact_id = require_int(artifact.get("id"), "artifact.id", minimum=1)
    _exact_identity(
        artifact,
        {
            "name": f"ici-candidate-{target_sha}",
            "size_in_bytes": archive_size,
            "digest": f"sha256:{archive_sha256}",
            "expired": False,
            "url": f"{api_root}/actions/artifacts/{artifact_id}",
            "archive_download_url": f"{api_root}/actions/artifacts/{artifact_id}/zip",
        },
        label="artifact",
    )
    workflow_run = artifact.get("workflow_run")
    if not isinstance(workflow_run, dict):
        raise ContractError(
            "github-evidence-mismatch", "artifact.workflow_run is absent"
        )
    _exact_identity(
        workflow_run,
        {"id": candidate_run_id, "head_branch": "main", "head_sha": target_sha},
        label="artifact.workflow_run",
    )
    _exact_identity(
        candidate_run,
        {
            "id": candidate_run_id,
            "run_attempt": provenance["candidate_run_attempt"],
            "head_sha": target_sha,
            "head_branch": "main",
            "path": provenance["candidate_workflow"],
            "name": "Build ici Candidate Artifact",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "url": f"{api_root}/actions/runs/{candidate_run_id}",
            "html_url": f"{web_root}/{candidate_run_id}",
        },
        label="candidate-run",
    )
    if (candidate_run.get("repository") or {}).get("full_name") != repository:
        raise ContractError(
            "github-evidence-mismatch", "candidate-run repository differs"
        )
    gate_job_url = f"{web_root}/{gate_run_id}/job/{gate_job_id}"
    _exact_identity(
        check,
        {
            "id": provenance["merge_gate_check_run_id"],
            "name": "Merge Gate",
            "head_sha": target_sha,
            "status": "completed",
            "conclusion": "success",
            "details_url": gate_job_url,
            "html_url": gate_job_url,
            "url": f"{api_root}/check-runs/{gate_job_id}",
        },
        label="gate-check",
    )
    _exact_identity(
        job,
        {
            "id": gate_job_id,
            "run_id": gate_run_id,
            "run_attempt": provenance["merge_gate_run_attempt"],
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
        label="gate-job",
    )
    _exact_identity(
        gate_run,
        {
            "id": gate_run_id,
            "run_attempt": provenance["merge_gate_run_attempt"],
            "name": "CI Quality Gate (Dogfooding)",
            "head_sha": target_sha,
            "head_branch": "main",
            "path": ".github/workflows/ci.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "url": f"{api_root}/actions/runs/{gate_run_id}",
            "html_url": provenance["merge_gate_url"],
        },
        label="gate-run",
    )
    if (gate_run.get("repository") or {}).get("full_name") != repository:
        raise ContractError("github-evidence-mismatch", "gate-run repository differs")


def verify_candidate_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_archive_sha256: str,
    expected_repository: str,
    expected_target_sha: str,
    github_evidence_dir: Path | None = None,
    timeout_seconds: int = 30,
) -> CandidateBundle:
    """Verify out-of-band identity plus in-bundle provenance before use."""

    if not SHA256_RE.fullmatch(expected_archive_sha256):
        raise ContractError(
            "invalid-archive-digest", "expected archive SHA-256 is invalid"
        )
    if not FULL_SHA_RE.fullmatch(expected_target_sha):
        raise ContractError(
            "invalid-target-sha", "expected target must be a full lowercase SHA"
        )
    if not REPOSITORY_RE.fullmatch(expected_repository):
        raise ContractError("invalid-repository", "expected repository is invalid")
    archive_sha256, archive_size = sha256_file(
        archive_path, max_bytes=MAX_ARCHIVE_BYTES
    )
    if archive_sha256 != expected_archive_sha256:
        raise ContractError(
            "archive-digest-mismatch",
            f"expected {expected_archive_sha256}, got {archive_sha256}",
        )
    _extract_archive(archive_path, destination)
    try:
        provenance = load_json_object(
            destination / "candidate-provenance.json", label="candidate provenance"
        )
        _validate_provenance(
            provenance,
            expected_repository=expected_repository,
            expected_target_sha=expected_target_sha,
        )
        executable = destination / "ici.pyz"
        executable_sha256, executable_size = sha256_file(
            executable, max_bytes=MAX_MEMBER_BYTES
        )
        if executable_sha256 != provenance["artifact_file_sha256"]:
            raise ContractError("executable-digest-mismatch", "ici.pyz digest differs")
        if executable_size != provenance["artifact_file_size"]:
            raise ContractError("executable-size-mismatch", "ici.pyz size differs")
        try:
            sidecar = (destination / "ici.pyz.sha256").read_text(encoding="ascii")
        except (OSError, UnicodeError) as error:
            raise ContractError("sidecar-read-failed", str(error)) from error
        if sidecar != f"{executable_sha256}  ici.pyz\n":
            raise ContractError("sidecar-mismatch", "checksum sidecar is not canonical")
        version = _read_version(executable, timeout_seconds=timeout_seconds)
        if version != f"ici {provenance['package_version']}":
            raise ContractError(
                "version-mismatch", "executable and provenance versions differ"
            )
        if github_evidence_dir is not None:
            _validate_github_evidence(
                github_evidence_dir,
                provenance,
                archive_sha256=archive_sha256,
                archive_size=archive_size,
            )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return CandidateBundle(
        root=destination,
        executable=executable,
        archive_sha256=archive_sha256,
        executable_sha256=executable_sha256,
        executable_size=executable_size,
        version=version,
        provenance=provenance,
        github_evidence_verified=github_evidence_dir is not None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--github-evidence", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        bundle = verify_candidate_archive(
            args.archive,
            args.destination,
            expected_archive_sha256=args.archive_sha256,
            expected_repository=args.repository,
            expected_target_sha=args.target_sha,
            github_evidence_dir=args.github_evidence,
        )
    except ContractError as error:
        parser.exit(1, f"candidate intake ERROR [{error.code}]: {error.message}\n")
    summary = {
        "schema": "quality-zoo.candidate-intake/v1",
        "archive_sha256": bundle.archive_sha256,
        "executable": str(bundle.executable),
        "executable_sha256": bundle.executable_sha256,
        "executable_size": bundle.executable_size,
        "target_sha": bundle.provenance["target_sha"],
        "version": bundle.version,
        "github_evidence_verified": bundle.github_evidence_verified,
    }
    print(
        json.dumps(summary, sort_keys=True)
        if args.json
        else f"verified {bundle.version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
