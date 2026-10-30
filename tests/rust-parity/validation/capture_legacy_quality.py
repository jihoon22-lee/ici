"""Development-only capture: frozen legacy deep `ici verify` test-quality targets and findings."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from capture_integration_legacy import DISABLED, SOURCE

FILES = {
    "src/calc.py": "def add(a, b):\n    return a + b\n",
    "tests/test_calc.py": (
        "import pathlib\nimport time\nfrom calc import add\n\n\ndef test_slow():\n"
        "    time.sleep(0.2)\n    assert add(1, 2) == 3\n\n\ndef test_flaky():\n"
        "    marker = pathlib.Path(__file__).with_name('flaky.state')\n"
        "    first = not marker.exists()\n    marker.write_text('x')\n    assert first\n"
    ),
}


def config(python: str) -> str:
    text = (
        '[ici]\nprofile = "deep"\n[project]\nsource_dirs = ["src"]\n[engines]\n'
        f"[engines.test]\nenabled = true\nrequired = true\npython = {json.dumps(python)}\n"
        "[engines.test.quality]\nenabled = true\nmode = 'report'\nrepeat_runs = 2\n"
        "slow_test_threshold = 0.1\n"
    )
    for engine in DISABLED:
        if engine != "test":
            text += f"[engines.{engine}]\nenabled = false\n"
    return text + "[engines.build]\nenabled = false\n[engines.integration]\nenabled = false\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ici-quality-oracle-") as temporary:
        root = Path(temporary)
        for relative, text in FILES.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(config(str(args.python)), encoding="utf-8")
        completed = subprocess.run(
            [
                str(args.python),
                "-m",
                "ici",
                "verify",
                "--report",
                "--profile",
                "deep",
                "--no-cache",
            ],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(args.oracle / "src")),
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        with (root / "verify_report.json").open(encoding="utf-8") as handle:
            report = json.load(handle)
    engine = next(item for item in report["results"] if item["engine_name"] == "test")
    quality = [
        target for target in engine["targets"] if target["target_name"].startswith("[test-quality]")
    ]
    document = {
        "source": SOURCE,
        "config": config("<PYTHON>"),
        "files": FILES,
        "exit_code": completed.returncode,
        "status": engine["status"],
        "quality_targets": [
            {key: target[key] for key in ("file_path", "start_line", "target_name", "status")}
            | {"message": target["message"]}
            for target in quality
        ],
        "quality_findings": sorted(
            (
                {
                    "rule_id": finding["rule_id"],
                    "path": finding["primary_location"]["path"],
                    "line": finding["primary_location"]["start_line"],
                    "fingerprint": finding["fingerprint"],
                    "message": finding["message"],
                }
                for finding in engine["findings"]
                if finding["rule_id"].startswith("ici.test.")
            ),
            key=lambda finding: finding["fingerprint"],
        ),
        "test_quality": engine["extra"]["test_quality"],
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
