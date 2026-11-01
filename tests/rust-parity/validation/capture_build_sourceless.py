"""Development-only capture: frozen deep `ici verify` BuildEngine for a source-less Python project.

The legacy `type='python'` project selects no Python source, so the frozen BuildEngine creates no
artifact and reports the measured `BuildArtifacts` FAIL. `ici build` prints only its summary line;
`verify --report` records the whole engine result.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from capture_integration_legacy import DISABLED, SOURCE

MODES = {"default": "", "pass_warn": "mode = 'pass_warn'\n"}


def config(mode: str) -> str:
    text = "[project]\ntype = 'python'\nsource_dirs = ['src']\n[ici]\nprofile = 'deep'\n"
    for engine in (*DISABLED, "integration"):
        text += f"[engines.{engine}]\nenabled = false\n"
    return text + "[engines.build]\nenabled = true\n" + MODES[mode]


def capture(oracle: Path, python: Path, mode: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-build-sourceless-") as temporary:
        root = Path(temporary)
        (root / "ici.toml").write_text(config(mode), encoding="utf-8")
        (root / "pyproject.toml").write_text(
            "[project]\nname='pkg'\nversion='2.3.4'\n", encoding="utf-8"
        )
        (root / "src").mkdir()
        completed = subprocess.run(
            [str(python), "-m", "ici", "verify", "--report", "--no-cache"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        with (root / "verify_report.json").open(encoding="utf-8") as handle:
            report = json.load(handle)
    engine = next(item for item in report["results"] if item["engine_name"] == "build")
    return {
        "config": config(mode),
        "exit_code": completed.returncode,
        "suite_status": report["suite_status"],
        "build": {
            key: engine[key]
            for key in ("status", "summary", "evidence", "required", "targets", "findings")
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
        "runs": {mode: capture(args.oracle, args.python, mode) for mode in MODES},
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
