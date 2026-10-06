"""Small stdlib-only fixtures shared by the quality-zoo contract tests."""

from __future__ import annotations

import hashlib
import json
import shlex
import stat
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

TARGET_SHA = "a" * 40
REPOSITORY = "example/ici"
PACKAGE_VERSION = "1.2.3"
PRODUCER_VERSION = "1.2.3"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def candidate_executable(
    version: str = PACKAGE_VERSION,
    *,
    exit_code: int = 0,
    output: str | None = None,
) -> bytes:
    """Return a tiny executable that implements the candidate --version probe."""

    if output is None:
        output = f"ici {version}\n"
    output_text = output.rstrip("\n")
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f"  printf '%s\\n' {shlex.quote(output_text)}\n"
        f"  exit {exit_code}\n"
        "fi\n"
        "exit 0\n"
    ).encode()


def candidate_provenance(
    executable: bytes,
    *,
    package_version: str = PACKAGE_VERSION,
    repository: str = REPOSITORY,
    target_sha: str = TARGET_SHA,
    **overrides: Any,
) -> dict[str, Any]:
    """Build the complete provenance object accepted by candidate_intake."""

    merge_gate_run_id = 100
    merge_gate_job_id = 200
    provenance: dict[str, Any] = {
        "artifact_file": "ici.pyz",
        "artifact_file_sha256": sha256_bytes(executable),
        "artifact_file_size": len(executable),
        "candidate_run_attempt": 1,
        "candidate_run_id": 10,
        "candidate_workflow": ".github/workflows/candidate-artifact.yml",
        "candidate_workflow_definition_sha": target_sha,
        "channel": "candidate",
        "merge_gate_check_run_id": merge_gate_job_id,
        "merge_gate_job_id": merge_gate_job_id,
        "merge_gate_job_url": (
            f"https://github.com/{repository}/actions/runs/{merge_gate_run_id}"
            f"/job/{merge_gate_job_id}"
        ),
        "merge_gate_run_attempt": 1,
        "merge_gate_run_id": merge_gate_run_id,
        "merge_gate_url": f"https://github.com/{repository}/actions/runs/{merge_gate_run_id}",
        "package_version": package_version,
        "repository": repository,
        "retention_days": 7,
        "schema": "ici.candidate/v1",
        "stable": False,
        "target_sha": target_sha,
    }
    provenance.update(overrides)
    return provenance


def zip_member(
    name: str,
    data: bytes = b"",
    *,
    mode: int = 0o644,
    compress_type: int = zipfile.ZIP_STORED,
    flag_bits: int = 0,
    file_type: int = stat.S_IFREG,
) -> zipfile.ZipInfo:
    """Create a Unix-mode-aware ZIP member descriptor."""

    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (file_type | mode) << 16
    info.compress_type = compress_type
    info.flag_bits = flag_bits
    return info


def write_zip(path: Path, members: Iterable[tuple[zipfile.ZipInfo, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        for info, data in members:
            archive.writestr(info, data)


def candidate_members(
    executable: bytes | None = None,
    *,
    provenance: dict[str, Any] | None = None,
    executable_mode: int = 0o755,
    provenance_mode: int = 0o644,
    sidecar_mode: int = 0o644,
    compress_type: int = zipfile.ZIP_STORED,
) -> list[tuple[zipfile.ZipInfo, bytes]]:
    if executable is None:
        executable = candidate_executable()
    if provenance is None:
        provenance = candidate_provenance(executable)
    sidecar = f"{sha256_bytes(executable)}  ici.pyz\n".encode("ascii")
    return [
        (
            zip_member(
                "candidate-provenance.json",
                mode=provenance_mode,
                compress_type=compress_type,
            ),
            (json.dumps(provenance, sort_keys=True) + "\n").encode("utf-8"),
        ),
        (
            zip_member(
                "ici.pyz.sha256", mode=sidecar_mode, compress_type=compress_type
            ),
            sidecar,
        ),
        (
            zip_member("ici.pyz", mode=executable_mode, compress_type=compress_type),
            executable,
        ),
    ]


def write_candidate_archive(
    path: Path,
    *,
    executable: bytes | None = None,
    provenance: dict[str, Any] | None = None,
    executable_mode: int = 0o755,
    provenance_mode: int = 0o644,
    sidecar_mode: int = 0o644,
    compress_type: int = zipfile.ZIP_STORED,
) -> tuple[bytes, dict[str, Any]]:
    if executable is None:
        executable = candidate_executable()
    if provenance is None:
        provenance = candidate_provenance(executable)
    write_zip(
        path,
        candidate_members(
            executable,
            provenance=provenance,
            executable_mode=executable_mode,
            provenance_mode=provenance_mode,
            sidecar_mode=sidecar_mode,
            compress_type=compress_type,
        ),
    )
    return executable, provenance


def write_manifest(path: Path, scenarios: Iterable[tuple[str, str]]) -> None:
    payload = {
        "schema": 1,
        "scenarios": [
            {"id": scenario_id, "path": scenario_path}
            for scenario_id, scenario_path in scenarios
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
