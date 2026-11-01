"""Capture frozen v0.13.0 `ici verify` on a `[workspace]` root (cli/cutover.py).

    python -I capture_verify_cutover.py <oracle-root> <output.json>

Each case runs the frozen CLI in a fresh copy of a small next workspace with an empty HOME and
a PATH without ruff/mypy. It records exit code, stdout, stderr (project root replaced by
`<root>`), the files the run created outside `.ici/cache`, and the schema of each JSON file.
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
    f'[checks."python.{check}"]\nenabled = false\n'
    for check in ("lint", "format", "type", "test", "coverage")
)
# Source text the frozen security analysis flags; it is written to disk, never executed.
RISKY = {
    "ici.toml": HEAD + COMPONENT + INTERNAL_ONLY,
    "main.py": (
        "import pickle\n\ndef load(blob):\n    return pickle.loads(blob)\n\n"
        "def run(cmd):\n    return eval(cmd)\n"
    ),
}
# No ici.toml: the stable (legacy) verify path, whose --publish needs GitHub Actions.
LEGACY = {"main.py": RISKY["main.py"]}
CASES = {
    "plain": (RISKY, [["verify"]]),
    "report": (RISKY, [["verify", "--report"]]),
    "sarif": (RISKY, [["verify", "--sarif", "out.sarif"]]),
    "html": (RISKY, [["verify", "--html", "page.html"]]),
    "max_findings_default": (RISKY, [["verify", "--max-findings", "5"]]),
    "fail_on_new": (RISKY, [["verify", "--fail-on-new", "--baseline", "b.json"]]),
    "legacy_publish_outside_actions": (LEGACY, [["verify", "--publish"]]),
    "stable_only": (
        RISKY,
        [["verify", "--verbose", "--group-by", "file", "--write-baseline", "b.json"]],
    ),
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


def inventory(base):
    files = {}
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base).as_posix()
        if not path.is_file() or relative.startswith((".git/", ".ici/cache/")):
            continue
        if relative in ("ici.toml", "main.py"):
            continue
        schema = None
        if path.suffix in (".json", ".sarif"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            schema = (
                payload.get("schema_id") or payload.get("schema_version") or payload.get("version")
            )
        files[relative] = schema
    return files


def run_case(oracle, tree, steps):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        materialize(base, tree)
        recorded = []
        for argv in steps:
            completed = invoke(oracle, base, argv, home)
            recorded.append(
                {
                    "argv": argv,
                    "exit_code": completed.returncode,
                    "stdout": scrub(completed.stdout, base),
                    "stderr": scrub(completed.stderr, base),
                    "files": inventory(base),
                }
            )
        return {"tree": tree, "steps": recorded}


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    cases = {name: run_case(oracle, tree, steps) for name, (tree, steps) in CASES.items()}
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "root_placeholder": ROOT,
        "cases": cases,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in cases.items():
        last = case["steps"][-1]
        print(name, last["exit_code"], sorted(last["files"]), last["stderr"][:80])


if __name__ == "__main__":
    main()
