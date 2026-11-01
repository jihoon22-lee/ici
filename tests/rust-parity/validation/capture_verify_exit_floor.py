"""Development-only capture: frozen `ici verify` exit for a coverage-minimum FAIL per schema.

A passing suite below `engines.test.min_line_cov` is a frozen legacy TestEngine FAIL and the
legacy suite exits 1. Frozen v0.13.0 has no other schema that can express that verdict: it
rejects `schema_version = 2` as an unknown key and a next (`schema_version = 1`) workspace
rejects `[engines]`, both before any engine runs (exit 2).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from capture_integration_legacy import DISABLED, SOURCE

SOURCE_FILE = "def f(x):\n    if x:\n        return 1\n    return 2\n\n\ndef g():\n    return 3\n"
TEST = "import app\n\n\ndef test_f():\n    assert app.f(1) == 1\n"
NEXT_V1 = (
    "schema_version = 1\n[workspace]\nname = 'w'\nprofile = 'standard'\n[[components]]\n"
    "id = 'project'\nroot = '.'\nlanguages = ['python']\n"
)


def legacy(python: str) -> str:
    text = (
        '[ici]\nprofile = "standard"\n[project]\nsource_dirs = ["."]\n[engines]\n'
        f"[engines.test]\nenabled = true\nrequired = true\npython = {json.dumps(python)}\n"
        "min_line_cov = 99.0\n"
    )
    for engine in DISABLED:
        if engine != "test":
            text += f"[engines.{engine}]\nenabled = false\n"
    return text


def scenarios(python: str) -> dict[str, str]:
    return {
        "legacy": legacy(python),
        "sparse_v2": "schema_version = 2\n[engines.test]\nmin_line_cov = 99.0\n",
        "next_v1_engines": NEXT_V1 + "[engines.test]\nmin_line_cov = 99.0\n",
    }


def capture(oracle: Path, python: Path, config: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-exit-floor-") as temporary:
        root = Path(temporary).resolve()
        (root / "app.py").write_text(SOURCE_FILE, encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests/test_app.py").write_text(TEST, encoding="utf-8")
        (root / "ici.toml").write_text(config, encoding="utf-8")
        completed = subprocess.run(
            [str(python), "-m", "ici", "verify", "--report", "--no-cache"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src"), PATH="/usr/bin:/bin"),
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        report_path = root / "verify_report.json"
        run: dict[str, object] = {"exit_code": completed.returncode}
        if report_path.exists():
            with report_path.open(encoding="utf-8") as handle:
                report = json.load(handle)
            run["suite_status"] = report["suite_status"]
            run["engines"] = {item["engine_name"]: item["status"] for item in report["results"]}
        else:
            run["stderr"] = completed.stderr.strip().replace(str(root), "<ROOT>")
        return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = {}
    for name, config in scenarios(str(args.python)).items():
        runs[name] = capture(args.oracle, args.python, config)
        runs[name]["config"] = config.replace(str(args.python), "<PYTHON>")
    document = {"source": SOURCE, "app": SOURCE_FILE, "test": TEST, "runs": runs}
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
