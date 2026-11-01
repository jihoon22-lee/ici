"""Development-only capture: frozen legacy `ici verify` integration verdicts for refused input."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from capture_integration_legacy import DISABLED, SOURCE

CASES = {
    "unknown_python_target": (
        '[[engines.integration.cases]]\nname = "one"\nargv = ["{python:missing}", "x.py"]\n'
    ),
    "unknown_placeholder": ('[[engines.integration.cases]]\nname = "one"\nargv = ["{nope}"]\n'),
    "no_build": '[[engines.integration.cases]]\nname = "one"\nargv = ["{python:py}", "x.py"]\n',
    # binary_compat shares the `build` dependency; integration is left disabled here.
    "binary_compat_no_build": "",
    # A standard profile disables every deep-only engine, so nothing is refused.
    "no_build_standard": (
        '[[engines.integration.cases]]\nname = "one"\nargv = ["{python:py}", "x.py"]\n'
    ),
}
PROFILES = {"no_build_standard": "standard"}


def config(python: str, scenario: str) -> str:
    integration = "false" if scenario == "binary_compat_no_build" else "true"
    text = (
        f'[ici]\nprofile = "{PROFILES.get(scenario, "deep")}"\n[engines]\n'
        f"[engines.integration]\nenabled = {integration}\n"
        f"required = true\npython_targets = {{ py = {json.dumps(python)} }}\n"
    ) + CASES[scenario]
    for engine in DISABLED:
        # The standard run keeps one engine so the pipeline is not empty.
        kept = {("binary_compat", "binary_compat_no_build"), ("line", "no_build_standard")}
        enabled = "true" if (engine, scenario) in kept else "false"
        text += f"[engines.{engine}]\nenabled = {enabled}\n"
    build = "false" if scenario.endswith(("no_build", "no_build_standard")) else "true"
    return text + f"[engines.build]\nenabled = {build}\n"


def capture(oracle: Path, python: Path, scenario: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-integration-blocked-") as temporary:
        root = Path(temporary)
        (root / "ici.toml").write_text(config(str(python), scenario), encoding="utf-8")
        (root / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (root / "x.py").write_text("print('x')\n", encoding="utf-8")
        completed = subprocess.run(
            [str(python), "-m", "ici", "verify", "--report", "--no-cache"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        report_path = root / "verify_report.json"
        if not report_path.exists():
            # Refused before any engine ran: frozen prints the reason and writes no report.
            lines = (completed.stdout + completed.stderr).strip().splitlines()
            return {
                "config": config("<PYTHON>", scenario),
                "exit_code": completed.returncode,
                "report": None,
                "reason": " ".join(line.strip(" │╰╭─") for line in lines[-2:]).strip(),
            }
        with report_path.open(encoding="utf-8") as handle:
            report = json.load(handle)
    engine = next(
        (item for item in report["results"] if item["engine_name"] == "integration"), None
    )
    if engine is None:
        return {
            "config": config("<PYTHON>", scenario),
            "exit_code": completed.returncode,
            "suite_status": report["suite_status"],
            "engines": sorted(item["engine_name"] for item in report["results"]),
        }
    return {
        "config": config("<PYTHON>", scenario),
        "exit_code": completed.returncode,
        "suite_status": report["suite_status"],
        "integration": {
            key: engine[key] for key in ("status", "summary", "evidence", "targets", "findings")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "runs": {name: capture(args.oracle, args.python, name) for name in CASES},
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
