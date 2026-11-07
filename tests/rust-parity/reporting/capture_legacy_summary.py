"""Capture frozen v0.13.0 `ici verify --github-summary` step summaries.

    python -I capture_legacy_summary.py <oracle-root> <output.json>

Each case runs the frozen stable verify in a fresh small Python project (empty HOME, PATH
without tools) with GITHUB_STEP_SUMMARY pointing at a file, and records the saved v3 suite
(`--report`) and the exact bytes frozen appended to the summary, with the project root as
`<root>`. The baseline case first writes a baseline, edits the code, then compares with
`--fail-on-new`; the bounded case has more than 100 targets in one engine; the append case
starts from an existing summary file.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
# Source text the frozen analyses flag; written to disk, never executed.
ISSUES = {
    "src/__init__.py": '"""Package."""\n',
    "src/core.py": (
        "import pickle\nimport subprocess\n\n\ndef load(blob):\n    return pickle.loads(blob)\n\n\n"
        "def run(cmd):\n    return subprocess.call(cmd, shell=True)\n\n\n"
        "def read(path):\n    f = open(path)\n    return f.read()\n"
    ),
    "src/exc.py": "def bad():\n    try:\n        return 1 / 0\n    except:\n        pass\n",
}
MANY = {
    "src/__init__.py": '"""Package."""\n',
    "src/many.py": "".join(f"def f{i}(x):\n    return x + {i}\n\n\n" for i in range(130)),
}
EDITED = {
    "src/exc.py": ISSUES["src/exc.py"]
    + "\n\ndef worse():\n    try:\n        return 2\n    except:\n        pass\n"
}
CASES = {
    "issues": (ISSUES, None, [["verify", "--report", "--github-summary"]], ""),
    "bounded": (MANY, None, [["verify", "--report", "--github-summary"]], ""),
    "append": (ISSUES, None, [["verify", "--report", "--github-summary"]], "# Earlier step\n"),
    "baseline": (
        ISSUES,
        EDITED,
        [
            ["verify", "--write-baseline", "base.json"],
            ["verify", "--report", "--github-summary", "--baseline", "base.json", "--fail-on-new"],
        ],
        "",
    ),
}


def write(base, tree):
    for name, text in tree.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def run_case(oracle, tree, edit, steps, existing):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        write(base, tree)
        (base / ".git").mkdir()
        summary = Path(home) / "summary.md"
        environment = {
            "HOME": home,
            "XDG_CONFIG_HOME": home,
            "PATH": os.defpath,
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        codes = []
        for index, argv in enumerate(steps):
            if index == len(steps) - 1:
                if edit:
                    write(base, edit)
                summary.write_text(existing, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
                cwd=base,
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            codes.append(completed.returncode)
        report = (base / "verify_report.json").read_text(encoding="utf-8")
        return {
            "tree": tree,
            "edit": edit,
            "steps": steps,
            "exit_codes": codes,
            "existing": existing,
            "suite": json.loads(report.replace(str(base), ROOT)),
            "summary": summary.read_text(encoding="utf-8").replace(str(base), ROOT),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "cases": {name: run_case(oracle, *case) for name, case in CASES.items()},
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        print(name, case["exit_codes"], len(case["summary"]))


if __name__ == "__main__":
    main()
