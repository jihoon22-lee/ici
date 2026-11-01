"""Capture frozen v0.13.0 `ici analyze` human and `--json` diagnostic output.

    python -I capture_analyze_text.py <oracle-root> <output.json>

Each case runs the frozen CLI in a fresh copy of a small workspace with an empty HOME and a
PATH without ruff/mypy (so blocked checks are deterministic). It records the exit code, stdout,
stderr and the saved result with the project root replaced by `<root>`. Steps of one case run
in the same directory, so baselines compare against an earlier step's result.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
HEAD = 'schema_version = 1\n[workspace]\nname = "demo"\n'
COMPONENT = '[[components]]\nid = "app"\nroot = "."\nlanguages = ["python"]\n'
INTERNAL_ONLY = "".join(
    f'[checks."python.{check}"]\nenabled = false\n' for check in ("lint", "format", "type")
)
CLEAN = {"ici.toml": HEAD + COMPONENT + INTERNAL_ONLY, "main.py": "def hello():\n    return 1\n"}
# Source text the frozen security analysis flags; it is written to disk, never executed.
RISKY = {
    "ici.toml": HEAD + COMPONENT + INTERNAL_ONLY,
    "main.py": (
        "import pickle\n\ndef load(blob):\n    return pickle.loads(blob)\n\n"
        "def run(cmd):\n    return eval(cmd)\n"
    ),
}
TOOLS = {"ici.toml": HEAD + COMPONENT, "main.py": CLEAN["main.py"]}
DEAD_OFF = '[checks."python.dead"]\nenabled = false\n'
CASES = {
    "pass": (CLEAN, [["analyze", "--result", "out.json"]]),
    "pass_json": (CLEAN, [["analyze", "--json", "--result", "out.json"]]),
    "fail": (RISKY, [["analyze", "--result", "out.json"]]),
    "fail_json": (RISKY, [["analyze", "--json", "--result", "out.json"]]),
    "incomplete": (TOOLS, [["analyze", "--result", "out.json"]]),
    "incomplete_json": (TOOLS, [["analyze", "--json", "--result", "out.json"]]),
    "require_full": (CLEAN, [["analyze", "--require-full", "--result", "out.json"]]),
    "baseline_comparable": (
        RISKY,
        [
            ["analyze", "--result", "base.json"],
            ["analyze", "--baseline", "base.json", "--result", "out.json"],
        ],
    ),
    "baseline_incompatible": (
        RISKY,
        [
            ["analyze", "--result", "base.json"],
            ["analyze", "--baseline", "base.json", "--result", "out.json"],
        ],
    ),
    "nothing_selected": (CLEAN, [["analyze", "--language", "cpp"]]),
    "nothing_selected_json": (CLEAN, [["analyze", "--json", "--language", "cpp"]]),
    "unknown_component": (CLEAN, [["analyze", "--component", "ghost"]]),
    "no_sources": ({"README": "nothing to analyze\n"}, [["analyze"]]),
}
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"


def invoke(oracle, base, argv, home):
    return subprocess.run(
        [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
        cwd=base,
        capture_output=True,
        text=True,
        env={"HOME": home, "XDG_CONFIG_HOME": home, "PATH": os.defpath},
        check=False,
    )


def materialize(base, tree):
    for name, text in tree.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (base / ".git").mkdir()


def scrub(text, base):
    return text.replace(str(base), ROOT)


def run_case(oracle, name, tree, steps):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        materialize(base, tree)
        recorded = []
        for index, argv in enumerate(steps):
            if name == "baseline_incompatible" and index == 0:
                # The baseline is taken under a different policy, so the comparison refuses.
                config = base / "ici.toml"
                original = config.read_text(encoding="utf-8")
                config.write_text(original + DEAD_OFF, encoding="utf-8")
                completed = invoke(oracle, base, argv, home)
                config.write_text(original, encoding="utf-8")
            else:
                completed = invoke(oracle, base, argv, home)
            result = None
            if "--result" in argv:
                saved = base / argv[argv.index("--result") + 1]
                if saved.exists():
                    result = json.loads(scrub(saved.read_text(encoding="utf-8"), base))
            recorded.append(
                {
                    "argv": argv,
                    "exit_code": completed.returncode,
                    "stdout": scrub(completed.stdout, base),
                    "stderr": scrub(completed.stderr, base),
                    "result": result,
                }
            )
        return {"tree": tree, "steps": recorded}


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    cases = {name: run_case(oracle, name, tree, steps) for name, (tree, steps) in CASES.items()}
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "root_placeholder": ROOT,
        "baseline_incompatible_policy": DEAD_OFF,
        "cases": cases,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in cases.items():
        last = case["steps"][-1]
        print(name, last["exit_code"], last["stdout"].splitlines()[2:3], last["stderr"][:60])


if __name__ == "__main__":
    main()
