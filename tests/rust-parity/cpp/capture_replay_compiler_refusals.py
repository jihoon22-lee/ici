"""Capture frozen v0.13.0 replay refusals of a recorded compiler across lint, cycle and dead.

    python -I capture_replay_compiler_refusals.py <oracle-root> <output.json>

Development only; never called by the Rust product. Frozen `build_replay_command` backs all three
engines and approves only a compiler a successful capability probe resolved to.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = (
    "[project]\ntype='cpp'\nsource_dirs=['src']\ncompile_database='compile_commands.json'\n"
    "[engines.dead]\ncpp_unused='required'\n"
    "[engines.lint]\nclang_tidy='off'\nclazy='off'\n"
)
PASS = '#!/bin/sh\nexec /usr/bin/g++ "$@"\n'
VERSION_FAILS = '#!/bin/sh\nif [ "$1" = --version ]; then exit 1; fi\nexec /usr/bin/g++ "$@"\n'
UNKNOWN_BANNER = (
    '#!/bin/sh\nif [ "$1" = --version ]; then echo "mystery compiler 1.0"; exit 0; fi\n'
    'exec /usr/bin/g++ "$@"\n'
)
ENGINES = ("lint", "cycle", "dead")
CASES = {
    "accepted": {"compiler": "g++", "tools": {"g++": PASS}},
    "compiler-not-probed": {"compiler": "g++-12", "tools": {"g++": PASS, "g++-12": PASS}},
    "compiler-not-executable": {
        "compiler": "../g++",
        "tools": {"g++": PASS},
        "outside": {"g++": "not a program\n"},
    },
    "version-fails": {"compiler": "g++", "tools": {"g++": VERSION_FAILS}},
    "version-unknown-banner": {"compiler": "g++", "tools": {"g++": UNKNOWN_BANNER}},
}
SOURCE = "int main() { return 0; }\n"
FIELDS = ("target_name", "status", "message", "start_line", "file_path")


def run(oracle, case):
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary).resolve()
        base, tools = work / "project", work / "tools"
        (base / "src").mkdir(parents=True)
        tools.mkdir()
        (base / ".git").mkdir()
        (base / "src/a.cpp").write_text(SOURCE, encoding="utf-8")
        (base / "ici.toml").write_text(CONFIG, encoding="utf-8")
        row = {
            "directory": ".",
            "command": f"{case['compiler']} -std=c++17 -c src/a.cpp",
            "file": "src/a.cpp",
        }
        (base / "compile_commands.json").write_text(json.dumps([row]), encoding="utf-8")
        for name, text in case["tools"].items():
            (tools / name).write_text(text, encoding="utf-8")
            (tools / name).chmod(0o755)
        for name, text in case.get("outside", {}).items():
            (work / name).write_text(text, encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                ENTRY,
                str(oracle / "src"),
                "verify",
                "--profile",
                "deep",
                "--no-cache",
                "--report",
            ],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                "HOME": str(work),
                "XDG_CONFIG_HOME": str(work),
                "PATH": f"{tools}:/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": temporary,
            },
            check=False,
        )
        text = (base / "verify_report.json").read_text(encoding="utf-8")
        text = text.replace(str(tools), "<tools>").replace(str(base), "<root>")
        report = json.loads(text)
        result = {"exit_code": completed.returncode}
        for engine in report["results"]:
            if engine["engine_name"] in ENGINES:
                result[engine["engine_name"]] = {
                    "status": engine["status"],
                    "summary": engine["summary"],
                    "evidence": engine["evidence"],
                    "targets": [{key: t.get(key) for key in FIELDS} for t in engine["targets"]],
                }
        return result


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "config": CONFIG,
        "source": SOURCE,
        "cases": {name: {**case, "frozen": run(oracle, case)} for name, case in CASES.items()},
    }
    for name, case in document["cases"].items():
        frozen = case["frozen"]
        print(name, frozen["exit_code"], {e: frozen[e]["summary"][:110] for e in ENGINES})
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
