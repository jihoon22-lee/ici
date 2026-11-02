"""Capture frozen v0.13.0 `ici test --report` TEM/branch threshold targets of Python projects.

    python -I capture_python_thresholds.py <oracle-root> <output.json>

Each case runs the frozen stable `ici test --report` in a fresh small Python project (empty
HOME, PATH without tools, the capture interpreter with pytest and coverage) and records the
saved engine report with the project root as `<root>`. `breach`: branch coverage under the
default minimum (TEM minimum lowered to 0.0), one `Threshold: Branch coverage` WARN target;
`met`: every branch covered, no threshold target; `failure`: a failing test, which frozen
`_threshold_breaches` never judges.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
SOURCE = (
    "def classify(x):\n"
    "    if x > 0:\n"
    '        return "pos"\n'
    "    if x < 0:\n"
    '        return "neg"\n'
    '    return "zero"\n'
)
HEADER = "from src.calc import classify\n\n\n"
POSITIVE = 'def test_positive():\n    assert classify(1) == "pos"\n'
EVERY = (
    POSITIVE
    + '\n\ndef test_negative():\n    assert classify(-1) == "neg"\n'
    + '\n\ndef test_zero():\n    assert classify(0) == "zero"\n'
)
FAILING = POSITIVE + '\n\ndef test_wrong():\n    assert classify(-1) == "pos"\n'
CONFIG = "[engines.test]\nmin_tem_score = 0.0\n"
CASES = {
    "breach": HEADER + POSITIVE,
    "met": HEADER + EVERY,
    "failure": HEADER + FAILING,
}


def run_case(oracle, tests):
    tree = {
        "ici.toml": CONFIG,
        "src/__init__.py": "",
        "src/calc.py": SOURCE,
        "tests/test_calc.py": tests,
    }
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in tree.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": os.defpath}
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "test", "--report"],
            cwd=base,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        report = (base / "test_report.json").read_text(encoding="utf-8")
        return {
            "tree": tree,
            "exit_code": completed.returncode,
            "report": json.loads(report.replace(str(base), ROOT)),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
        "cases": {name: run_case(oracle, tests) for name, tests in CASES.items()},
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        targets = [t["target_name"] for t in case["report"]["targets"]]
        print(name, case["exit_code"], case["report"]["status"], targets)


if __name__ == "__main__":
    main()
