"""Development-only capture: frozen legacy coverage-probe tool errors; never product code.

    python -I capture_legacy_coverage_probe.py --oracle <v0.13.0 checkout> --python <venv python>

Frozen `engines/test_interpreter.py::_find_coverage_cmd` probes `<interpreter> -m coverage
--version`. Each case runs `ici test --report` with `engines.test.python` naming a wrapper that
answers that probe in one way (exit 2, killed by a signal, more than 1,000,000 output
characters, the bare missing-module line with exit 2) and passes every other argv to the real
interpreter; the `pytest-name` case names a `pytest` wrapper so `_interpreter_from_command`
runs coverage through the `python` beside it. Records the engine verdict and its targets with
the temporary paths as `<root>`/`<bin>`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PROBE = {
    "exit": "echo broken >&2; exit 2",
    "signal": "kill -9 $$",
    "truncated": "head -c 1100000 /dev/zero | tr '\\0' a; exit 0",
    "missing-exit-2": "echo 'No module named coverage'; exit 2",
}
WRAPPER = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "coverage" ] && [ "$3" = "--version" ]; then
  {probe}
fi
exec {python} "$@"
"""
FILES = {
    "src/calc.py": "def add(a, b):\n    return a + b\n",
    "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}


def capture(oracle: Path, python: Path, case: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-legacy-probe-") as temporary:
        base = Path(temporary)
        root, bin_dir = base / "project", base / "bin"
        bin_dir.mkdir()
        for relative, text in FILES.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        if case == "pytest-name":
            (bin_dir / "python").write_text(f'#!/bin/sh\nexec {python} "$@"\n', encoding="utf-8")
            pytest = python.parent / "pytest"
            (bin_dir / "pytest").write_text(f'#!/bin/sh\nexec {pytest} "$@"\n', encoding="utf-8")
            configured = bin_dir / "pytest"
        else:
            text = WRAPPER.format(probe=PROBE[case], python=python)
            (bin_dir / "python").write_text(text, encoding="utf-8")
            configured = bin_dir / "python"
        for item in bin_dir.iterdir():
            item.chmod(0o755)
        (root / "ici.toml").write_text(
            '[ici]\nprofile = "standard"\n[project]\nsource_dirs = ["src"]\n'
            f'[engines.test]\npython = "{configured}"\n',
            encoding="utf-8",
        )
        process = subprocess.run(
            [str(python), "-m", "ici", "test", "--report"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        with (root / "test_report.json").open(encoding="utf-8") as handle:
            engine = json.load(handle)
    text = json.dumps(
        {
            "exit_code": process.returncode,
            "engine": {key: engine[key] for key in ("status", "summary", "evidence")},
            "targets": [
                {key: target.get(key) for key in ("file_path", "target_name", "status")}
                for target in engine["targets"]
            ],
            "tools": [
                {key: tool.get(key) for key in ("name", "argv", "returncode", "truncated")}
                for tool in engine.get("tool_evidence", [])
            ],
        }
    )
    return json.loads(text.replace(str(root), "<root>").replace(str(bin_dir), "<bin>"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE, head
    cases = {case: capture(args.oracle, args.python, case) for case in [*PROBE, "pytest-name"]}
    payload = {"oracle": SOURCE, "python": str(args.python), "cases": cases}
    out = Path(__file__).with_name("legacy-coverage-probe-oracle.json")
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
