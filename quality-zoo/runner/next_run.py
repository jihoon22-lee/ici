"""Execute quality-zoo scenarios against the ``ici.next.run`` contract.

A schema-3 scenario owns its own build: ``prepare`` argv steps run in the
sandbox copy of the project *before* ici is invoked, because ici never
builds. ici is then driven through the flat CLI — ``verify --result`` and
``report --out`` — and the stored result is validated by
``runner.next_contract``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from runner.common import (
    SHA256_RE,
    ContractError,
    contained_path,
    copy_artifact,
    isolated_environment,
    load_json_object,
    load_registry,
    read_bounded,
    reject_symlinks,
    require_string,
    run_version,
    sha256_file,
    validate_command,
)
from runner.next_contract import SUITE_SCOPE, evaluate_next_contract

MAX_PREPARE_STEPS = 8
PREPARE_TIMEOUT_SECONDS = 300


def _validate_prepare(value: Any, scenario_id: str) -> list[list[str]]:
    """Scenario-owned build steps — argv lists, never shell strings."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_PREPARE_STEPS:
        raise ContractError(
            "unsafe-prepare", f"{scenario_id} prepare must be ≤{MAX_PREPARE_STEPS} argv lists"
        )
    steps: list[list[str]] = []
    for index, step in enumerate(value):
        if (
            not isinstance(step, list)
            or not step
            or any(not isinstance(item, str) or not item for item in step)
        ):
            raise ContractError(
                "unsafe-prepare", f"{scenario_id} prepare[{index}] is not an argv list"
            )
        steps.append(list(step))
    return steps


def _load_next_scenario(
    scenario_id: str,
    scenario_root: Path,
    ici_sha256: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(scenario_header, expectation)`` for a schema-2 or schema-3 file.

    Schema 3 carries the expectation path directly. Schema 2 keeps the digest
    selector for candidates whose known answers differ per artifact — the
    selected file must still be a schema-3 expectation.
    """
    reject_symlinks(scenario_root)
    payload = load_json_object(
        scenario_root / "scenario.json", label=f"scenario {scenario_id}"
    )
    if payload.get("scenario_id") != scenario_id:
        raise ContractError(
            "scenario-schema", f"scenario identity mismatch for {scenario_id}"
        )
    schema = payload.get("schema")
    if schema == 2:
        raw = payload.get("expectations")
        if not isinstance(raw, dict) or not raw:
            raise ContractError(
                "scenario-schema", f"{scenario_id} needs digest-keyed expectations"
            )
        paths = {
            digest: contained_path(scenario_root, require_string(path, "expectation path"))
            for digest, path in raw.items()
            if isinstance(digest, str) and SHA256_RE.fullmatch(digest)
        }
        if not paths or ici_sha256 is None or ici_sha256 not in paths:
            raise ContractError(
                "unsupported-ici",
                f"{scenario_id} has no expectation for ici SHA-256 {ici_sha256!r}",
            )
        scenario = load_json_object(paths[ici_sha256], label=f"{scenario_id} expectation")
    elif schema == 3:
        scenario = load_json_object(
            contained_path(scenario_root, require_string(payload.get("expectation"), "expectation")),
            label=f"{scenario_id} expectation",
        )
    else:
        raise ContractError(
            "scenario-schema", f"{scenario_id} scenario schema must be 2 or 3"
        )
    if scenario.get("schema") != 3 or scenario.get("scenario_id") != scenario_id:
        raise ContractError(
            "scenario-schema", f"{scenario_id} expectation must be schema 3 with the same id"
        )
    if scenario.get("class") not in {"stable", "experimental", "red"}:
        raise ContractError("scenario-schema", f"{scenario_id} has invalid class")
    validate_command(scenario.get("command"))
    scenario["_prepare_steps"] = _validate_prepare(scenario.get("prepare"), scenario_id)
    project_root = contained_path(
        scenario_root, require_string(scenario.get("project_root"), "project_root")
    )
    if not project_root.is_dir() or not (project_root / "ici.toml").is_file():
        raise ContractError("scenario-project", f"{scenario_id} project lacks ici.toml")
    if scenario.get("profile") != scenario["command"][2]:
        raise ContractError("scenario-profile", "profile and command profile differ")
    return scenario, scenario


def _run_prepare(
    steps: list[list[str]],
    project_root: Path,
    env: dict[str, str],
    scenario_id: str,
) -> None:
    for argv in steps:
        try:
            completed = subprocess.run(
                argv,
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=PREPARE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ContractError(
                "prepare-failed", f"{scenario_id} prepare {argv[:1]}: {error}"
            ) from error
        if completed.returncode != 0:
            raise ContractError(
                "prepare-failed",
                f"{scenario_id} prepare {argv!r} exited {completed.returncode}: "
                f"{(completed.stderr or completed.stdout).strip()[:400]}",
            )


def run_next_scenario(
    scenario_id: str,
    scenario_root: Path,
    ici_bin: Path,
    output_root: Path,
    *,
    timeout_seconds: int,
    producer_version: str,
    ici_sha256: str,
) -> dict[str, Any]:
    scenario, expectation = _load_next_scenario(scenario_id, scenario_root, ici_sha256)
    project_relative = require_string(scenario["project_root"], "project_root")
    scenario_output = output_root / scenario_id
    if scenario_output.exists():
        raise ContractError("output-exists", f"refusing to replace {scenario_output}")
    scenario_output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="quality-zoo-next-") as temp_name:
        temp_root = Path(temp_name)
        copied_scenario = temp_root / "scenario"
        shutil.copytree(scenario_root, copied_scenario, symlinks=False)
        project_root = contained_path(copied_scenario, project_relative)
        env = isolated_environment(temp_root)
        _run_prepare(scenario["_prepare_steps"], project_root, env, scenario_id)
        stdout_path = temp_root / "stdout.txt"
        stderr_path = temp_root / "stderr.txt"
        command = [
            str(ici_bin),
            *validate_command(scenario["command"]),
            "--result",
            "verify_result.json",
        ]
        try:
            with (
                stdout_path.open("wb") as stdout_stream,
                stderr_path.open("wb") as stderr_stream,
            ):
                completed = subprocess.run(
                    command,
                    cwd=project_root,
                    env=env,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    check=False,
                    timeout=timeout_seconds,
                )
        except subprocess.TimeoutExpired as error:
            raise ContractError(
                "runner-timeout", f"{scenario_id} exceeded {timeout_seconds} seconds"
            ) from error
        except OSError as error:
            raise ContractError("runner-execution", f"cannot execute ici: {error}") from error
        stdout_text, stdout_truncated = read_bounded(stdout_path)
        stderr_text, stderr_truncated = read_bounded(stderr_path)
        result_path = project_root / "verify_result.json"
        if not result_path.is_file():
            raise ContractError(
                "runner-report-missing",
                f"{scenario_id} exit {completed.returncode} produced no result JSON: "
                f"{stderr_text.strip()[:400]}",
            )
        report_command = [
            str(ici_bin),
            "report",
            "--result",
            "verify_result.json",
            "--out",
            "verify_report.html",
        ]
        subprocess.run(
            report_command,
            cwd=project_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        result = load_json_object(result_path, label=f"{scenario_id} ici result")
        contract = evaluate_next_contract(result, expectation)
        expected_exit = {
            "PASS": 0,
            "FAIL": 1,
            "INCOMPLETE": 3,
        }[expectation["expected"]["gate"]]
        if "exit_code" in expectation["expected"]:
            expected_exit = expectation["expected"]["exit_code"]
        failures = [failure.as_dict() for failure in contract.failures]
        if completed.returncode != expected_exit:
            if completed.returncode < 0:
                signal_name = signal.Signals(-completed.returncode).name
                detail = f"ici terminated by {signal_name}"
            else:
                detail = (
                    f"ici exit {completed.returncode} does not match gate "
                    f"{expectation['expected']['gate']} (expected {expected_exit})"
                )
            failures.append({"check": SUITE_SCOPE, "kind": "exit-code", "detail": detail})
        if producer_version != contract.producer_version:
            failures.append(
                {
                    "check": SUITE_SCOPE,
                    "kind": "producer-version",
                    "detail": (
                        f"--version producer {producer_version} "
                        f"!= result {contract.producer_version}"
                    ),
                }
            )
        errors = [failure["detail"] for failure in failures]
        copy_artifact(result_path, scenario_output / "result.json")
        html_path = project_root / "verify_report.html"
        if html_path.is_file():
            copy_artifact(html_path, scenario_output / "report.html")
        summary = {
            "schema": "quality-zoo.run/v2",
            "scenario_id": scenario_id,
            "scenario_class": scenario["class"],
            "contract_verdict": "PASS" if not errors else "FAIL",
            "observed_gate": contract.observed_gate,
            "producer_version": contract.producer_version,
            "ici_sha256": ici_sha256,
            "argv": command[1:],
            "exit_code": completed.returncode,
            "matched_findings": contract.matched_findings,
            "stdout": stdout_text,
            "stdout_truncated": stdout_truncated,
            "stderr": stderr_text,
            "stderr_truncated": stderr_truncated,
            "errors": errors,
            "failures": failures,
            "artifacts": {
                "json": f"{scenario_id}/result.json",
                "html": f"{scenario_id}/report.html",
            },
        }
        (scenario_output / "run.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary


def run_manifest(
    manifest_path: Path,
    scenario_ids: list[str],
    ici_bin: Path,
    output_root: Path,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    _, registry = load_registry(manifest_path)
    selected = scenario_ids or sorted(registry)
    unknown = sorted(set(selected) - set(registry))
    if unknown or len(selected) != len(set(selected)):
        raise ContractError("scenario-selection", f"invalid selection: {unknown!r}")
    if ici_bin.is_symlink() or not ici_bin.is_file() or not os.access(ici_bin, os.X_OK):
        raise ContractError("unsafe-ici-bin", "ICI_BIN must be an executable regular file")
    ici_bin = ici_bin.resolve(strict=True)
    ici_sha256, _ = sha256_file(ici_bin, max_bytes=32 * 1024 * 1024)
    producer_version = run_version(ici_bin, timeout_seconds)
    if output_root.exists():
        raise ContractError("output-exists", f"refusing to replace {output_root}")
    output_root.mkdir(parents=True)
    results = [
        run_next_scenario(
            scenario_id,
            registry[scenario_id],
            ici_bin,
            output_root,
            timeout_seconds=timeout_seconds,
            producer_version=producer_version,
            ici_sha256=ici_sha256,
        )
        for scenario_id in selected
    ]
    final_sha256, _ = sha256_file(ici_bin, max_bytes=32 * 1024 * 1024)
    if final_sha256 != ici_sha256:
        raise ContractError("ici-changed", "ICI_BIN changed during scenario execution")
    aggregate = {
        "schema": "quality-zoo.suite/v2",
        "contract_verdict": (
            "PASS" if all(item["contract_verdict"] == "PASS" for item in results) else "FAIL"
        ),
        "ici": {
            "path": str(ici_bin),
            "sha256": ici_sha256,
            "version": producer_version,
        },
        "scenario_count": len(results),
        "results": results,
    }
    (output_root / "suite.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return aggregate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("manifest.next.json"))
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--ici-bin", type=Path, default=os.environ.get("ICI_BIN"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.ici_bin is None:
        parser.error("--ici-bin or ICI_BIN is required")
    try:
        aggregate = run_manifest(
            args.manifest,
            args.scenario,
            args.ici_bin,
            args.output_dir,
            timeout_seconds=args.timeout_seconds,
        )
    except ContractError as error:
        print(f"quality-zoo contract error: {error}", file=os.sys.stderr)
        return 2
    failed = [r["scenario_id"] for r in aggregate["results"] if r["contract_verdict"] != "PASS"]
    for line in [
        f"quality-zoo {aggregate['contract_verdict']} - "
        f"{aggregate['scenario_count'] - len(failed)}/{aggregate['scenario_count']} "
        f"scenario contracts passed (ici {aggregate['ici']['version']})",
        *(f"  FAIL {name}" for name in failed),
    ]:
        print(line)
    return 0 if aggregate["contract_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
