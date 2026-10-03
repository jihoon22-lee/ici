"""Execute quality-zoo scenarios with an explicit local ici binary."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from runner.common import (
    SCENARIO_ID_RE,
    SHA256_RE,
    ContractError,
    contained_path,
    load_json_object,
    require_int,
    require_string,
    sha256_file,
)
from runner.report_contract import SUITE_SCOPE, evaluate_contract

MAX_TOOL_OUTPUT_BYTES = 1024 * 1024
ALLOWED_PROFILES = {"fast", "standard", "deep"}


def _validate_command(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError("unsafe-command", "command must be an argv string array")
    if len(value) not in {3, 4} or value[:2] != ["verify", "--profile"]:
        raise ContractError(
            "unsafe-command",
            "command must be verify --profile <fast|standard|deep> [--no-cache]",
        )
    if value[2] not in ALLOWED_PROFILES:
        raise ContractError("unsafe-command", f"unsupported profile {value[2]!r}")
    if len(value) == 4 and value[3] != "--no-cache":
        raise ContractError("unsafe-command", f"unsupported argument {value[3]!r}")
    return list(value)


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ContractError("unsafe-scenario", f"scenario root is a symlink: {root}")
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ContractError(
                    "unsafe-scenario",
                    f"scenario contains symlink: {path.relative_to(root)}",
                )
    except OSError as error:
        raise ContractError("scenario-scan-failed", str(error)) from error


def _load_registry(manifest_path: Path) -> tuple[Path, dict[str, Path]]:
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    payload = load_json_object(manifest_path, label="quality-zoo manifest")
    if payload.get("schema") != 1:
        raise ContractError("manifest-schema", "quality-zoo manifest schema must be 1")
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ContractError(
            "manifest-scenarios", "manifest needs at least one scenario"
        )
    registry: dict[str, Path] = {}
    for index, item in enumerate(raw_scenarios):
        if not isinstance(item, dict) or set(item) != {"id", "path"}:
            raise ContractError("manifest-entry", f"scenario entry {index} is invalid")
        scenario_id = require_string(item["id"], f"scenarios[{index}].id")
        if not SCENARIO_ID_RE.fullmatch(scenario_id) or scenario_id in registry:
            raise ContractError(
                "manifest-entry", f"invalid/duplicate scenario ID {scenario_id!r}"
            )
        scenario_path = contained_path(
            root, require_string(item["path"], "scenario path")
        )
        if not scenario_path.is_dir():
            raise ContractError(
                "manifest-entry", f"scenario path is not a directory: {scenario_path}"
            )
        registry[scenario_id] = scenario_path
    return root, registry


def _load_scenario(
    scenario_id: str,
    scenario_root: Path,
    ici_sha256: str | None = None,
) -> dict[str, Any]:
    _reject_symlinks(scenario_root)
    payload = load_json_object(
        scenario_root / "scenario.json", label=f"scenario {scenario_id}"
    )
    if payload.get("scenario_id") != scenario_id:
        raise ContractError(
            "scenario-schema", f"scenario identity mismatch for {scenario_id}"
        )
    if payload.get("schema") == 2:
        if set(payload) != {"schema", "scenario_id", "expectations"}:
            raise ContractError(
                "scenario-schema", f"scenario selector fields differ for {scenario_id}"
            )
        raw_expectations = payload.get("expectations")
        if not isinstance(raw_expectations, dict) or not raw_expectations:
            raise ContractError(
                "scenario-schema", f"scenario {scenario_id} needs expectations"
            )
        expectation_paths: dict[str, Path] = {}
        for digest, raw_path in raw_expectations.items():
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                raise ContractError(
                    "scenario-schema",
                    f"scenario {scenario_id} has an invalid ici SHA-256 selector",
                )
            expectation_paths[digest] = contained_path(
                scenario_root,
                require_string(raw_path, f"scenario {scenario_id} expectation path"),
            )
        if ici_sha256 is None or ici_sha256 not in expectation_paths:
            raise ContractError(
                "unsupported-ici",
                f"scenario {scenario_id} has no expectation for ici SHA-256 "
                f"{ici_sha256!r}",
            )
        expectation_path = expectation_paths[ici_sha256]
        payload = load_json_object(
            expectation_path,
            label=f"scenario {scenario_id} expectation {ici_sha256}",
        )
    if payload.get("schema") != 1 or payload.get("scenario_id") != scenario_id:
        raise ContractError(
            "scenario-schema", f"scenario identity mismatch for {scenario_id}"
        )
    if payload.get("class") not in {"stable", "experimental", "red"}:
        raise ContractError(
            "scenario-schema", f"scenario {scenario_id} has invalid class"
        )
    _validate_command(payload.get("command"))
    project_root = contained_path(
        scenario_root, require_string(payload.get("project_root"), "project_root")
    )
    if not project_root.is_dir() or not (project_root / "ici.toml").is_file():
        raise ContractError("scenario-project", f"{scenario_id} project lacks ici.toml")
    if payload.get("profile") != payload["command"][2]:
        raise ContractError("scenario-profile", "profile and command profile differ")
    return payload


def _read_bounded(path: Path) -> tuple[str, bool]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        data = stream.read(MAX_TOOL_OUTPUT_BYTES)
    return data.decode("utf-8", errors="replace"), size > MAX_TOOL_OUTPUT_BYTES


def _isolated_environment(temp_root: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "HOME": str(temp_root / "home"),
        "XDG_CONFIG_HOME": str(temp_root / "config"),
        "ICI_CACHE_DIR": str(temp_root / "cache"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
    }
    for name in ("ICI_PYTHON", "QT_QPA_PLATFORM"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    for path in (temp_root / "home", temp_root / "config", temp_root / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    return env


def _run_version(ici_bin: Path, timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            [str(ici_bin), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError("ici-version-failed", str(error)) from error
    version = completed.stdout.strip()
    if completed.returncode != 0 or not version.startswith("ici "):
        raise ContractError(
            "ici-version-failed", f"exit {completed.returncode}, output {version!r}"
        )
    return version.removeprefix("ici ")


def _copy_artifact(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ContractError("output-exists", f"refusing to replace {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def run_scenario(
    scenario_id: str,
    scenario_root: Path,
    ici_bin: Path,
    output_root: Path,
    *,
    timeout_seconds: int,
    producer_version: str,
    ici_sha256: str,
) -> dict[str, Any]:
    scenario = _load_scenario(scenario_id, scenario_root, ici_sha256)
    project_relative = require_string(scenario["project_root"], "project_root")
    scenario_output = output_root / scenario_id
    if scenario_output.exists():
        raise ContractError("output-exists", f"refusing to replace {scenario_output}")
    scenario_output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="quality-zoo-") as temp_name:
        temp_root = Path(temp_name)
        copied_scenario = temp_root / "scenario"
        shutil.copytree(scenario_root, copied_scenario, symlinks=False)
        project_root = contained_path(copied_scenario, project_relative)
        stdout_path = temp_root / "stdout.txt"
        stderr_path = temp_root / "stderr.txt"
        command = [
            str(ici_bin),
            *_validate_command(scenario["command"]),
            "--report",
            "--html",
            "verify_report.html",
        ]
        try:
            with (
                stdout_path.open("wb") as stdout_stream,
                stderr_path.open("wb") as stderr_stream,
            ):
                completed = subprocess.run(
                    command,
                    cwd=project_root,
                    env=_isolated_environment(temp_root),
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
            raise ContractError(
                "runner-execution", f"cannot execute ici: {error}"
            ) from error
        stdout_text, stdout_truncated = _read_bounded(stdout_path)
        stderr_text, stderr_truncated = _read_bounded(stderr_path)
        report_path = project_root / "verify_report.json"
        html_path = project_root / "verify_report.html"
        if not report_path.is_file() or not html_path.is_file():
            error_message = f"{scenario_id} exit {completed.returncode} did not produce both reports"
            diagnostic = {
                "schema": "quality-zoo.runner-error/v1",
                "scenario_id": scenario_id,
                "scenario_class": scenario["class"],
                "error_code": "runner-report-missing",
                "error": error_message,
                "ici_sha256": ici_sha256,
                "argv": command[1:],
                "exit_code": completed.returncode,
                "stdout": stdout_text,
                "stdout_truncated": stdout_truncated,
                "stderr": stderr_text,
                "stderr_truncated": stderr_truncated,
                "reports_present": {
                    "json": report_path.is_file(),
                    "html": html_path.is_file(),
                },
            }
            (scenario_output / "run.json").write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            raise ContractError(
                "runner-report-missing",
                error_message,
            )
        report = load_json_object(report_path, label=f"{scenario_id} ici report")
        contract = evaluate_contract(report, scenario)
        expected_exit = {
            "PASS": 0,
            "WARN": 0,
            "FAIL": 1,
            "ERROR": 1,
            "SKIP": 2,
        }[contract.observed_suite_status]
        failures = [failure.as_dict() for failure in contract.failures]
        if completed.returncode != expected_exit:
            if completed.returncode < 0:
                signal_name = signal.Signals(-completed.returncode).name
                detail = f"ici terminated by {signal_name}"
            else:
                detail = (
                    f"ici exit {completed.returncode} does not match suite status "
                    f"{contract.observed_suite_status} (expected {expected_exit})"
                )
            failures.append(
                {"engine": SUITE_SCOPE, "kind": "exit-code", "detail": detail}
            )
        if producer_version != contract.producer_version:
            failures.append(
                {
                    "engine": SUITE_SCOPE,
                    "kind": "producer-version",
                    "detail": (
                        f"--version producer {producer_version} "
                        f"!= report {contract.producer_version}"
                    ),
                }
            )
        errors = [failure["detail"] for failure in failures]
        _copy_artifact(report_path, scenario_output / "report.json")
        _copy_artifact(html_path, scenario_output / "report.html")
        summary = {
            "schema": "quality-zoo.run/v1",
            "scenario_id": scenario_id,
            "scenario_class": scenario["class"],
            "contract_verdict": "PASS" if not errors else "FAIL",
            "observed_suite_status": contract.observed_suite_status,
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
                "json": f"{scenario_id}/report.json",
                "html": f"{scenario_id}/report.html",
            },
        }
        (scenario_output / "run.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary


MAX_SUMMARY_DETAILS = 3


def _engine_regressions(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group every contract failure by the engine that owns it.

    A failing suite otherwise reads as a flat list of sentences spread over
    sixteen scenario objects, which answers "what did not match" but not the
    question an ici change actually raises: which engine regressed.
    """

    by_engine: dict[str, dict[str, Any]] = {}
    for result in results:
        for failure in result.get("failures", []):
            engine = failure.get("engine") or SUITE_SCOPE
            entry = by_engine.setdefault(
                engine,
                {"engine": engine, "failure_count": 0, "scenarios": [], "kinds": [], "details": []},
            )
            entry["failure_count"] += 1
            scenario_id = result["scenario_id"]
            if scenario_id not in entry["scenarios"]:
                entry["scenarios"].append(scenario_id)
            kind = failure.get("kind", "unknown")
            if kind not in entry["kinds"]:
                entry["kinds"].append(kind)
            if len(entry["details"]) < MAX_SUMMARY_DETAILS:
                entry["details"].append(f"{scenario_id}: {failure.get('detail', '')}")
    for entry in by_engine.values():
        entry["scenarios"].sort()
        entry["kinds"].sort()
    # Most failures first, then by name, so the engine to look at is on top.
    return sorted(by_engine.values(), key=lambda item: (-item["failure_count"], item["engine"]))


def render_engine_regression_summary(aggregate: dict[str, Any]) -> str:
    """Render the one-screen "which engine regressed" view of a suite run."""

    verdict = aggregate["contract_verdict"]
    scenario_count = aggregate["scenario_count"]
    failed = [item for item in aggregate["results"] if item["contract_verdict"] != "PASS"]
    lines = [
        f"quality-zoo {verdict} - {scenario_count - len(failed)}/{scenario_count} "
        f"scenario contracts passed (ici {aggregate['ici']['version']})",
    ]
    if verdict == "PASS":
        return lines[0]
    lines.append("")
    lines.append(f"{'ENGINE':<18} {'FAILS':>5}  SCENARIOS")
    for entry in aggregate.get("engine_regressions", []):
        engine = entry["engine"] or "<suite>"
        scenarios = ", ".join(entry["scenarios"])
        lines.append(f"{engine:<18} {entry['failure_count']:>5}  {scenarios}")
        lines.append(f"{'':<18} {'':>5}  kinds: {', '.join(entry['kinds'])}")
        for detail in entry["details"]:
            lines.append(f"{'':<18} {'':>5}  - {detail}")
        remaining = entry["failure_count"] - len(entry["details"])
        if remaining > 0:
            lines.append(f"{'':<18} {'':>5}  - (+{remaining} more in suite.json)")
    return "\n".join(lines)


def run_manifest(
    manifest_path: Path,
    scenario_ids: list[str],
    ici_bin: Path,
    output_root: Path,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    _, registry = _load_registry(manifest_path)
    selected = scenario_ids or sorted(registry)
    unknown = sorted(set(selected) - set(registry))
    if unknown or len(selected) != len(set(selected)):
        raise ContractError("scenario-selection", f"invalid selection: {unknown!r}")
    if ici_bin.is_symlink() or not ici_bin.is_file() or not os.access(ici_bin, os.X_OK):
        raise ContractError(
            "unsafe-ici-bin", "ICI_BIN must be an executable regular file"
        )
    ici_bin = ici_bin.resolve(strict=True)
    ici_sha256, _ = sha256_file(ici_bin, max_bytes=32 * 1024 * 1024)
    producer_version = _run_version(ici_bin, timeout_seconds)
    version_probe_sha256, _ = sha256_file(ici_bin, max_bytes=32 * 1024 * 1024)
    if version_probe_sha256 != ici_sha256:
        raise ContractError("ici-changed", "ICI_BIN changed during version probing")
    if output_root.exists():
        raise ContractError("output-exists", f"refusing to replace {output_root}")
    output_root.mkdir(parents=True)
    results = [
        run_scenario(
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
        "schema": "quality-zoo.suite/v1",
        "contract_verdict": (
            "PASS"
            if all(item["contract_verdict"] == "PASS" for item in results)
            else "FAIL"
        ),
        "ici": {
            "path": str(ici_bin),
            "sha256": ici_sha256,
            "version": producer_version,
        },
        "scenario_count": len(results),
        "results": results,
    }
    aggregate["engine_regressions"] = _engine_regressions(results)
    (output_root / "suite.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return aggregate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--ici-bin", type=Path, default=os.environ.get("ICI_BIN"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.ici_bin is None:
        parser.error("--ici-bin or ICI_BIN is required")
    try:
        timeout_seconds = require_int(
            args.timeout_seconds, "timeout_seconds", minimum=1
        )
        result = run_manifest(
            args.manifest,
            args.scenario,
            args.ici_bin,
            args.output_dir,
            timeout_seconds=timeout_seconds,
        )
    except (ContractError, OSError) as error:
        parser.exit(2, f"quality-zoo ERROR: {error}\n")
    # stdout stays machine-readable for the acceptance workflows; the
    # human summary goes to stderr so both can be captured separately.
    print(json.dumps(result, sort_keys=True))
    print(render_engine_regression_summary(result), file=sys.stderr)
    return 0 if result["contract_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
