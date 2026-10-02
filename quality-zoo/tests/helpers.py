"""Small stdlib-only fixtures shared by the quality-zoo contract tests."""

from __future__ import annotations

import hashlib
import json
import os
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


def location(
    path: str = "src/example.py",
    *,
    line: int = 1,
    end_line: int | None = None,
    start_column: int | None = 1,
    end_column: int | None = 5,
    label: str = "example",
) -> dict[str, Any]:
    return {
        "path": path,
        "start_line": line,
        "end_line": line if end_line is None else end_line,
        "start_column": start_column,
        "end_column": end_column,
        "label": label,
    }


def finding(
    *,
    index: int = 0,
    rule_id: str = "ici.example.rule",
    category: str = "correctness",
    severity: str = "medium",
    confidence: str = "high",
    location_value: dict[str, Any] | None = None,
    related_locations: list[dict[str, Any]] | None = None,
    message: str = "example finding",
    explanation: str = "example explanation",
    remediation: str = "example remediation",
    tool_rule_id: str = "tool.example",
    tool_name: str = "example-tool",
    tool_version: str = "1.0",
    suppressed: bool = False,
    suppression_kind: str = "none",
    suppression_reason: str = "",
    metrics: dict[str, dict[str, Any]] | None = None,
    snippet: str = "example snippet",
) -> dict[str, Any]:
    if location_value is None:
        location_value = location(line=index + 1)
    if related_locations is None:
        related_locations = []
    if metrics is None:
        metrics = {"score": {"value": 1.0, "unit": "count"}}
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "fingerprint": f"sha256:{index + 1:064x}",
        "primary_location": location_value,
        "related_locations": related_locations,
        "message": message,
        "explanation": explanation,
        "remediation": remediation,
        "tool_rule_id": tool_rule_id,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "suppression": {
            "suppressed": suppressed,
            "kind": suppression_kind,
            "reason": suppression_reason,
        },
        "metrics": metrics,
        "snippet": snippet,
    }


def engine(
    name: str = "example",
    *,
    status: str = "PASS",
    evidence: str = "MEASURED",
    required: bool = True,
    cache_hit: bool = False,
    findings: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "ici.result/v3",
        "engine_name": name,
        "status": status,
        "evidence": evidence,
        "required": required,
        "cache_hit": cache_hit,
        "findings": [] if findings is None else findings,
        "extra": {} if extra is None else extra,
    }


def report(
    engines: list[dict[str, Any]] | None = None,
    *,
    suite_status: str = "PASS",
    producer_version: str = PRODUCER_VERSION,
    duration: float = 0.25,
) -> dict[str, Any]:
    if engines is None:
        engines = [engine()]
    statuses = [item["status"] for item in engines]
    return {
        "schema_version": "ici.result/v3",
        "suite_status": suite_status,
        "duration": duration,
        "passed_count": statuses.count("PASS"),
        "warned_count": statuses.count("WARN"),
        "failed_count": sum(status in {"FAIL", "ERROR"} for status in statuses),
        "error_count": statuses.count("ERROR"),
        "skipped_count": statuses.count("SKIP"),
        "total_count": len(engines),
        "results": engines,
        "analysis_metadata": {"producer_version": producer_version},
    }


def write_fake_ici(
    path: Path,
    report_payload: dict[str, Any] | None,
    *,
    version: str = PRODUCER_VERSION,
    version_output: str | None = None,
    version_exit: int = 0,
    verify_exit: int = 0,
    stdout: str = "fake stdout\n",
    stderr: str = "fake stderr\n",
    output_bytes: int = 0,
) -> None:
    """Write a self-contained fake ici executable for runner tests."""

    if version_output is None:
        version_output = f"ici {version}\n"
    report_literal = repr(report_payload)
    script = f"""#!/usr/bin/env python3
import json
import pathlib
import sys

REPORT = {report_literal}

if len(sys.argv) > 1 and sys.argv[1] == "--version":
    sys.stdout.write({version_output!r})
    raise SystemExit({version_exit})

if REPORT is not None:
    pathlib.Path("verify_report.json").write_text(
        json.dumps(REPORT), encoding="utf-8"
    )
    pathlib.Path("verify_report.html").write_text("<html>fake</html>\\n", encoding="utf-8")

sys.stdout.write({stdout!r})
sys.stderr.write({stderr!r})
if {output_bytes!r}:
    sys.stdout.write("x" * {output_bytes!r})
raise SystemExit({verify_exit})
"""
    path.write_text(script, encoding="utf-8")
    os.chmod(path, 0o755)


def write_scenario(
    root: Path,
    *,
    scenario_id: str = "python.example",
    scenario_class: str = "stable",
    profile: str = "fast",
    command: list[str] | None = None,
    project_root: str = ".",
    expected_status: str = "PASS",
    producer_version: str = PRODUCER_VERSION,
    engines: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    forbidden_findings: list[dict[str, Any]] | None = None,
) -> tuple[Path, dict[str, Any]]:
    scenario_root = root / "scenario"
    project = scenario_root / project_root
    project.mkdir(parents=True, exist_ok=True)
    (project / "ici.toml").write_text(
        f'[ici]\nprofile = "{profile}"\n', encoding="utf-8"
    )
    if command is None:
        command = ["verify", "--profile", profile, "--no-cache"]
    if engines is None:
        engines = [
            {
                "name": "example",
                "status": expected_status,
                "evidence": "MEASURED",
                "required": True,
            }
        ]
    if findings is None:
        findings = []
    scenario = {
        "schema": 1,
        "scenario_id": scenario_id,
        "class": scenario_class,
        "project_root": project_root,
        "profile": profile,
        "command": command,
        "suite_status": expected_status,
        "producer_version": producer_version,
        "engines": engines,
        "findings": findings,
        "forbidden_findings": [] if forbidden_findings is None else forbidden_findings,
        "required_capabilities": [],
        "skip_policy": "forbid",
    }
    (scenario_root / "scenario.json").write_text(
        json.dumps(scenario, indent=2) + "\n", encoding="utf-8"
    )
    return scenario_root, scenario


def write_manifest(path: Path, scenarios: Iterable[tuple[str, str]]) -> None:
    payload = {
        "schema": 1,
        "scenarios": [
            {"id": scenario_id, "path": scenario_path}
            for scenario_id, scenario_path in scenarios
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
