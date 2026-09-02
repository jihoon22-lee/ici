"""Focused contract tests for the bounded candidate bundle helper."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "candidate_bundle.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("candidate_bundle", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate_bundle = _load_module()

REPOSITORY = "jihoon22-lee/ici"
TARGET_SHA = "a" * 40
WORKFLOW_SHA = TARGET_SHA
MERGE_GATE_RUN_ID = 456
MERGE_GATE_JOB_ID = 789
MERGE_GATE_URL = f"https://github.com/{REPOSITORY}/actions/runs/{MERGE_GATE_RUN_ID}"
MERGE_GATE_JOB_URL = f"{MERGE_GATE_URL}/job/{MERGE_GATE_JOB_ID}"


def _arguments() -> dict[str, object]:
    return {
        "repository": REPOSITORY,
        "target_sha": TARGET_SHA,
        "package_version": "0.10.2",
        "candidate_workflow_definition_sha": WORKFLOW_SHA,
        "candidate_run_id": 123,
        "candidate_run_attempt": 1,
        "merge_gate_check_run_id": 234,
        "merge_gate_job_id": MERGE_GATE_JOB_ID,
        "merge_gate_run_id": MERGE_GATE_RUN_ID,
        "merge_gate_run_attempt": 2,
        "merge_gate_job_url": MERGE_GATE_JOB_URL,
        "merge_gate_url": MERGE_GATE_URL,
    }


def _create(source: Path, output: Path, **overrides: object) -> dict[str, object]:
    arguments = _arguments()
    arguments.update(overrides)
    return candidate_bundle.create_bundle(source, output, **arguments)


def test_happy_path_has_exact_bundle_and_provenance(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    payload = b"candidate pyz bytes\x00\x01"
    source.write_bytes(payload)
    source.chmod(0o640)
    output = tmp_path / "bundle"

    provenance = _create(source, output)

    assert set(output.iterdir()) == {
        output / "ici.pyz",
        output / "ici.pyz.sha256",
        output / "candidate-provenance.json",
    }
    assert (output / "ici.pyz").read_bytes() == payload
    assert stat.S_IMODE((output / "ici.pyz").stat().st_mode) == 0o755
    digest = hashlib.sha256(payload).hexdigest()
    assert (output / "ici.pyz.sha256").read_bytes() == f"{digest}  ici.pyz\n".encode()
    assert json.loads((output / "candidate-provenance.json").read_text()) == provenance
    assert set(provenance) == set(candidate_bundle._PROVENANCE_FIELDS)
    assert provenance["artifact_file_sha256"] == digest
    assert provenance["artifact_file_size"] == len(payload)
    assert provenance["stable"] is False
    assert provenance["retention_days"] == 14
    assert provenance["schema"] == "ici.candidate/v1"
    assert provenance["channel"] == "candidate"
    assert provenance["repository"] == REPOSITORY
    assert provenance["target_sha"] == TARGET_SHA
    assert provenance["package_version"] == "0.10.2"
    assert provenance["candidate_workflow"] == candidate_bundle.CANDIDATE_WORKFLOW
    assert provenance["candidate_workflow_definition_sha"] == WORKFLOW_SHA
    assert provenance["candidate_run_id"] == 123
    assert provenance["candidate_run_attempt"] == 1
    assert provenance["merge_gate_check_run_id"] == 234
    assert provenance["merge_gate_job_id"] == MERGE_GATE_JOB_ID
    assert provenance["merge_gate_run_id"] == MERGE_GATE_RUN_ID
    assert provenance["merge_gate_run_attempt"] == 2
    assert provenance["merge_gate_job_url"] == MERGE_GATE_JOB_URL
    assert provenance["merge_gate_url"] == MERGE_GATE_URL
    assert provenance["artifact_file"] == "ici.pyz"

    assert stat.S_IMODE((output / "ici.pyz.sha256").stat().st_mode) == 0o644
    assert stat.S_IMODE((output / "candidate-provenance.json").stat().st_mode) == 0o644


def test_manifest_is_deterministic_and_utf8_with_final_newline(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"pyz payload")
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_provenance = _create(source, first)
    second_provenance = _create(source, second)

    first_manifest = (first / "candidate-provenance.json").read_bytes()
    second_manifest = (second / "candidate-provenance.json").read_bytes()
    assert first_provenance == second_provenance
    assert first_manifest == second_manifest
    assert first_manifest.endswith(b"\n")
    assert first_manifest.decode("utf-8").count("\n") == 1
    assert (
        first_manifest
        == json.dumps(
            first_provenance,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "not-an-owner-name"),
        ("repository", "owner/name?query"),
        ("target_sha", "A" * 40),
        ("target_sha", "a" * 39),
        ("candidate_workflow_definition_sha", "g" * 40),
        ("candidate_workflow_definition_sha", "b" * 40),
        ("package_version", "v0.10.2"),
        ("package_version", "1.2"),
        ("package_version", "1.02.3"),
        ("package_version", "1.2.3-01"),
        ("candidate_run_id", 0),
        ("candidate_run_id", -1),
        ("candidate_run_id", True),
        ("candidate_run_attempt", 0),
        ("merge_gate_check_run_id", 0),
        ("merge_gate_job_id", 0),
        ("merge_gate_run_id", 0),
        ("merge_gate_run_id", "456"),
        ("merge_gate_run_attempt", 0),
        ("merge_gate_url", "https://github.com/jihoon22-lee/ici/actions/runs/457"),
        ("merge_gate_url", "https://github.com/jihoon22-lee/ici/actions/runs/456/"),
        ("merge_gate_url", "https://github.com/other/ici/actions/runs/456"),
        (
            "merge_gate_job_url",
            "https://github.com/jihoon22-lee/ici/actions/runs/457/job/789",
        ),
        (
            "merge_gate_job_url",
            "https://github.com/jihoon22-lee/ici/actions/runs/456/job/0",
        ),
        (
            "merge_gate_job_id",
            790,
        ),
    ],
)
def test_invalid_provenance_arguments_fail_closed(tmp_path: Path, field: str, value: object):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")

    with pytest.raises(candidate_bundle.CandidateBundleError, match=field.split("_")[0]):
        _create(source, tmp_path / f"bundle-{field}", **{field: value})


def test_rejects_source_symlink_directory_and_fifo(tmp_path: Path):
    regular = tmp_path / "regular.pyz"
    regular.write_bytes(b"payload")
    link = tmp_path / "link.pyz"
    link.symlink_to(regular)
    with pytest.raises(candidate_bundle.CandidateBundleError, match="source"):
        _create(link, tmp_path / "link-bundle")

    directory = tmp_path / "directory.pyz"
    directory.mkdir()
    with pytest.raises(candidate_bundle.CandidateBundleError, match="regular"):
        _create(directory, tmp_path / "directory-bundle")

    fifo = tmp_path / "pipe.pyz"
    os.mkfifo(fifo)
    with pytest.raises(candidate_bundle.CandidateBundleError, match="regular"):
        _create(fifo, tmp_path / "fifo-bundle")


def test_rejects_source_parent_symlink(tmp_path: Path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    source = real_parent / "ici.pyz"
    source.write_bytes(b"payload")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(candidate_bundle.CandidateBundleError, match="symlink"):
        _create(linked_parent / "ici.pyz", tmp_path / "bundle")


def test_rejects_oversized_source_before_creating_output(tmp_path: Path):
    source = tmp_path / "large.pyz"
    with source.open("wb") as stream:
        stream.truncate(candidate_bundle.MAX_PYZ_BYTES + 1)
    output = tmp_path / "bundle"

    with pytest.raises(candidate_bundle.CandidateBundleError, match="bound"):
        _create(source, output)
    assert not output.exists()


def test_rejects_empty_source_before_creating_output(tmp_path: Path):
    source = tmp_path / "empty.pyz"
    source.touch()
    output = tmp_path / "bundle"

    with pytest.raises(candidate_bundle.CandidateBundleError, match="bound"):
        _create(source, output)
    assert not output.exists()


def test_rejects_preexisting_output_directory_and_does_not_touch_it(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    output = tmp_path / "bundle"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(candidate_bundle.CandidateBundleError, match="pre-exist"):
        _create(source, output)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_rejects_preexisting_output_symlink_without_writing_target(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    target = tmp_path / "target"
    target.mkdir()
    output = tmp_path / "bundle"
    output.symlink_to(target, target_is_directory=True)

    with pytest.raises(candidate_bundle.CandidateBundleError, match="pre-exist"):
        _create(source, output)
    assert list(target.iterdir()) == []


def test_rejects_output_parent_symlink(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(candidate_bundle.CandidateBundleError, match="symlink"):
        _create(source, linked_parent / "bundle")
    assert not (real_parent / "bundle").exists()


def test_verify_requires_exact_file_set(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    output = tmp_path / "bundle"
    _create(source, output)

    (output / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(candidate_bundle.CandidateBundleError, match="unexpected file"):
        candidate_bundle.verify_bundle(output)

    (output / "unexpected.txt").unlink()
    (output / "ici.pyz.sha256").unlink()
    with pytest.raises(candidate_bundle.CandidateBundleError, match="unexpected file"):
        candidate_bundle.verify_bundle(output)


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("ici.pyz", 0o644),
        ("ici.pyz.sha256", 0o600),
        ("candidate-provenance.json", 0o600),
    ],
)
def test_verify_rejects_unexpected_file_modes(tmp_path: Path, name: str, mode: int):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    output = tmp_path / "bundle"
    _create(source, output)
    (output / name).chmod(mode)

    with pytest.raises(candidate_bundle.CandidateBundleError, match="unexpected mode"):
        candidate_bundle.verify_bundle(output)


def test_verify_rejects_tampered_pyz(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    output = tmp_path / "bundle"
    _create(source, output)
    (output / "ici.pyz").write_bytes(b"payloAd")

    with pytest.raises(candidate_bundle.CandidateBundleError, match="artifact SHA-256"):
        candidate_bundle.verify_bundle(output)


def test_verify_rejects_duplicate_and_noncanonical_manifest_json(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")

    duplicate_output = tmp_path / "duplicate"
    _create(source, duplicate_output)
    (duplicate_output / "candidate-provenance.json").write_text(
        '{"schema":"ici.candidate/v1","schema":"ici.candidate/v1"}\n',
        encoding="utf-8",
    )
    with pytest.raises(candidate_bundle.CandidateBundleError, match="valid JSON"):
        candidate_bundle.verify_bundle(duplicate_output)

    noncanonical_output = tmp_path / "noncanonical"
    _create(source, noncanonical_output)
    manifest_path = noncanonical_output / "candidate-provenance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(candidate_bundle.CandidateBundleError, match="deterministic"):
        candidate_bundle.verify_bundle(noncanonical_output)


def test_verify_rejects_bundle_and_parent_symlinks(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    real_bundle = real_parent / "bundle"
    _create(source, real_bundle)

    bundle_link = tmp_path / "bundle-link"
    bundle_link.symlink_to(real_bundle, target_is_directory=True)
    with pytest.raises(candidate_bundle.CandidateBundleError, match=r"safely|symlink"):
        candidate_bundle.verify_bundle(bundle_link)

    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(candidate_bundle.CandidateBundleError, match="symlink"):
        candidate_bundle.verify_bundle(parent_link / "bundle")


def test_verify_rejects_tampered_manifest_sidecar_and_symlink(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    output = tmp_path / "bundle"
    _create(source, output)
    manifest_path = output / "candidate-provenance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stable"] = True
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    with pytest.raises(candidate_bundle.CandidateBundleError, match=r"stable|deterministic"):
        candidate_bundle.verify_bundle(output)

    output = tmp_path / "bundle-sidecar"
    _create(source, output)
    (output / "ici.pyz.sha256").write_text("0" * 64 + "  ici.pyz\n", encoding="ascii")
    with pytest.raises(candidate_bundle.CandidateBundleError, match=r"checksum|SHA"):
        candidate_bundle.verify_bundle(output)

    output = tmp_path / "bundle-link"
    _create(source, output)
    sidecar = output / "ici.pyz.sha256"
    sidecar.unlink()
    sidecar.symlink_to(source)
    with pytest.raises(candidate_bundle.CandidateBundleError, match=r"safely|symlink"):
        candidate_bundle.verify_bundle(output)


def test_source_change_after_copy_is_rejected_and_partial_bundle_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"payload")
    output = tmp_path / "bundle"
    real_read = candidate_bundle.os.read
    state = {"changed": False}

    def read_and_change(descriptor: int, size: int) -> bytes:
        data = real_read(descriptor, size)
        if data and not state["changed"]:
            state["changed"] = True
            source.write_bytes(b"changed")
        return data

    monkeypatch.setattr(candidate_bundle.os, "read", read_and_change)
    with pytest.raises(candidate_bundle.CandidateBundleError, match="changed"):
        _create(source, output)
    assert not output.exists()


def test_cli_success_and_failure(tmp_path: Path):
    source = tmp_path / "ici.pyz"
    source.write_bytes(b"cli payload")
    output = tmp_path / "cli-bundle"
    command = [
        sys.executable,
        str(SCRIPT),
        "create",
        "--source-pyz",
        str(source),
        "--output-dir",
        str(output),
        "--repository",
        REPOSITORY,
        "--target-sha",
        TARGET_SHA,
        "--package-version",
        "0.10.2",
        "--candidate-workflow-definition-sha",
        WORKFLOW_SHA,
        "--candidate-run-id",
        "123",
        "--candidate-run-attempt",
        "1",
        "--merge-gate-check-run-id",
        "234",
        "--merge-gate-job-id",
        str(MERGE_GATE_JOB_ID),
        "--merge-gate-run-id",
        str(MERGE_GATE_RUN_ID),
        "--merge-gate-run-attempt",
        "2",
        "--merge-gate-job-url",
        MERGE_GATE_JOB_URL,
        "--merge-gate-url",
        MERGE_GATE_URL,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(
        (output / "candidate-provenance.json").read_text(encoding="utf-8")
    )
    verified = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout) == json.loads(
        (output / "candidate-provenance.json").read_text(encoding="utf-8")
    )

    failed = subprocess.run(
        [*command[:-1], "https://github.com/other/ici/actions/runs/456"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode != 0
