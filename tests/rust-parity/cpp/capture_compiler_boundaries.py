"""Development-only frozen legacy C++ compiler-backed function boundary captures; never Rust.

Frozen `ComplexityEngine` / `CognitiveEngine` with an exact compilation database run the
dedicated `clang-tidy --checks=-*,readability-function-size` boundary probe
(analysis/_cpp_function_boundaries.py) only inside `ici verify` (an analysis context); the
standalone `ici complexity` / `ici cognitive` commands have no analysis context and keep the
brace-heuristic rows whatever tools exist. Every case writes a fresh `<work>/project` from
parity-fixtures/cpp/compiler-boundaries/<tree> plus the case's `ici.toml` and
`compile_commands.json` (rows below, `{root}` = the project), runs each command with
PATH=<work>/bin:/usr/bin:/bin (LANG/LC_ALL=C.UTF-8) and keeps its exit code and, for `verify`,
the complexity and cognitive results in full (for the engine commands the whole saved report).

`<work>/bin` holds the case's tools: `tidy` is a `clang-tidy` symlink to ICI_TEST_CLANG_TIDY
(LLVM 21.1.8, runs without LD_LIBRARY_PATH); `failing-tidy` / `garbage-tidy` are `clang-tidy`
scripts (`scripts` in the golden, `{clang-tidy}` the real tool) that answer `--version` through
the real tool and otherwise exit 3 / print one unparseable line; no tools leaves the directory empty
(the host /usr/bin has no clang-tidy). Saved text writes the project as `<root>`, the bin
directory as `<bin>` (the `--header-filter` pattern's escaped project as `<root>` too) and the
resolved clang-tidy executable as `<clang-tidy>`; every
compile-configuration digest (it covers the absolute project directory) is written as
`<configuration:N>`, N its first appearance in a sorted-key walk of that result.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURES = ROOT / "parity-fixtures/cpp/compiler-boundaries"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-compiler-boundaries"
GOLDEN = ROOT / "tests/rust-parity/cpp/compiler-boundaries-oracle.json"
TIDY = os.environ["ICI_TEST_CLANG_TIDY"]
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
DISABLED = (
    "line",
    "lint",
    "dup",
    "dead",
    "exception",
    "cycle",
    "test",
    "sanitize",
    "thread_sanitize",
    "type",
    "security",
    "resource",
    "python_compat",
    "binary_compat",
    "compile_db",
    "build",
)
FAILING = '#!/bin/sh\nif [ "$1" = --version ]; then exec {clang-tidy} "$@"; fi\nexit 3\n'
GARBAGE = (
    '#!/bin/sh\nif [ "$1" = --version ]; then exec {clang-tidy} "$@"; fi\n'
    "echo 'not a diagnostic'\nexit 0\n"
)
# Race wrappers (argv $5 is the unit source): after its first boundary run the tool touches
# itself (`_executable_identity` changes before the next unit), rewrites the source it analysed
# (changed during analysis) or the next unit's source (changed before analysis).
RACES = '#!/bin/sh\nif [ "$1" = --version ]; then exec {clang-tidy} "$@"; fi\n'
TOUCHING = RACES + 'touch "$0"\nexec {clang-tidy} "$@"\n'
DURING = RACES + '{clang-tidy} "$@"\nstatus=$?\nprintf "\\n" >> "$5"\nexit $status\n'
BEFORE = (
    RACES
    + 'case "$5" in */a.cpp) printf "\\n" >> "${5%/a.cpp}/b.cpp";; esac\nexec {clang-tidy} "$@"\n'
)
SCRIPTS = {
    "failing-tidy": FAILING,
    "garbage-tidy": GARBAGE,
    "touching-tidy": TOUCHING,
    "rewriting-tidy": DURING,
    "next-rewriting-tidy": BEFORE,
}

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clang++"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"


def row(source: str, *flags: str, output: str = "") -> dict:
    target = output or "build/" + Path(source).stem + ".o"
    return {
        "directory": "{root}",
        "file": source,
        "arguments": ["g++", "-std=c++17", *flags, "-c", source, "-o", target],
    }


def config(policy: str = "", database: bool = True, cognitive: bool = True) -> str:
    # Frozen runs the cognitive engine in the deep profile only (core/pipeline.py).
    text = '[ici]\nprofile = "deep"\n\n[project]\ntype = "cpp"\nsource_dirs = ["src"]\n'
    if database:
        text += 'compile_database = "compile_commands.json"\n'
    for engine in DISABLED:
        text += f"\n[engines.{engine}]\nenabled = false\n"
    text += "\n[engines.complexity]\n"
    if policy:
        text += f'cpp_boundaries = "{policy}"\n'
    text += f"\n[engines.cognitive]\nenabled = {'true' if cognitive else 'false'}\n"
    if policy:
        text += f'cpp_boundaries = "{policy}"\n'
    return text


EXACT = [row("src/a.cpp"), row("src/b.cpp")]
ALL = ["verify", "complexity", "cognitive"]
# name: tree, ici.toml, compile_commands rows (None: no database file), tools, commands.
CASES = {
    # Every function confirmed (operator, member with a lambda, namespace, template); the
    # engine commands keep the heuristic rows.
    "exact": ("exact", config(), EXACT, "tidy", ALL),
    # A function generated by a function-like macro is a scope exclusion.
    "macro": ("macro", config(), [row("src/m.cpp")], "tidy", ["verify"]),
    # `#if 0` hides a scanned function from Clang: partial (complexity) / mixed (cognitive).
    "scanner-only": ("scanner-only", config(), [row("src/s.cpp")], "tidy", ["verify"]),
    # A production source without a recorded command stops the boundary run.
    "uncovered": ("uncovered", config(), [row("src/covered.cpp")], "tidy", ["verify"]),
    # Two configurations of one source: an `#ifdef` function and an `#ifdef` body.
    "configurations": (
        "configurations",
        config(),
        [row("src/c.cpp", "-DFEATURE", output="build/c-feature.o"), row("src/c.cpp")],
        "tidy",
        ["verify"],
    ),
    # Required boundaries with a scanner-only function.
    "required-partial": (
        "scanner-only",
        config("required"),
        [row("src/s.cpp")],
        "tidy",
        ["verify"],
    ),
    # Required boundaries without clang-tidy (verify) and without an analysis context.
    "required-unavailable": ("exact", config("required"), EXACT, "", ALL),
    "off": ("exact", config("off"), EXACT, "tidy", ["verify"]),
    "no-database": ("exact", config(database=False), None, "tidy", ["verify"]),
    # A clang-tidy that passes its capability probe and fails every boundary run.
    "tool-failure": ("exact", config(), EXACT, "failing-tidy", ["verify"]),
    # A unit whose recorded compiler is not on PATH: frozen's per-unit `compiler-unavailable`
    # replay error, then the next unit still runs (_run_unit).
    "compiler-unavailable": (
        "exact",
        config(),
        [{**row("src/a.cpp"), "arguments": ["g++-99", *row("src/a.cpp")["arguments"][1:]]},
         row("src/b.cpp")],
        "tidy",
        ["verify"],
    ),
    # Races, complexity only (one boundary run): tool identity, source during/before analysis.
    "identity-race": ("exact", config(cognitive=False), EXACT, "touching-tidy", ["verify"]),
    "source-during": ("exact", config(cognitive=False), EXACT, "rewriting-tidy", ["verify"]),
    "source-before": ("exact", config(cognitive=False), EXACT, "next-rewriting-tidy", ["verify"]),
    # One that exits 0 with output the frozen clang-tidy parser rejects.
    "garbage-output": ("exact", config(), EXACT, "garbage-tidy", ["verify"]),
}


def mask_configurations(value, seen: dict, key: str = ""):
    if isinstance(value, dict):
        return {name: mask_configurations(value[name], seen, name) for name in sorted(value)}
    if isinstance(value, list):
        return [mask_configurations(item, seen, key) for item in value]
    if (
        key in ("configuration", "configurations")
        and isinstance(value, str)
        and DIGEST.match(value)
    ):
        seen.setdefault(value, f"<configuration:{len(seen)}>")
        return seen[value]
    return value


def materialize(case: tuple, project: Path, bin_dir: Path) -> None:
    tree, text, rows, tools, _commands = case
    shutil.copytree(FIXTURES / tree, project)
    (project / ".git").mkdir()
    (project / "ici.toml").write_text(text, encoding="utf-8")
    if rows is not None:
        payload = json.dumps(rows).replace("{root}", str(project))
        (project / "compile_commands.json").write_text(payload, encoding="utf-8")
    bin_dir.mkdir()
    if tools == "tidy":
        (bin_dir / "clang-tidy").symlink_to(TIDY)
    elif tools in SCRIPTS:
        script = bin_dir / "clang-tidy"
        script.write_text(SCRIPTS[tools].replace("{clang-tidy}", TIDY), encoding="utf-8")
        script.chmod(0o755)


def run(project: Path, bin_dir: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "ici", command, "--report"],
        cwd=project,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(WORK / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(ORACLE / "src"),
        },
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


def capture(name: str, case: tuple) -> dict:
    out = {}
    for command in case[4]:
        shutil.rmtree(WORK, ignore_errors=True)
        (WORK / "home").mkdir(parents=True)
        try:
            project = WORK / "project"
            bin_dir = WORK / "bin"
            materialize(case, project, bin_dir)
            result = run(project, bin_dir, command)
            text = (project / f"{command}_report.json").read_text(encoding="utf-8")
            escaped = json.dumps(re.escape(str(project)))[1:-1]
            text = (
                text.replace(os.path.realpath(TIDY), "<clang-tidy>")
                .replace(escaped, "<root>")
                .replace(str(project), "<root>")
                .replace(str(bin_dir), "<bin>")
            )
            report = json.loads(text)
        finally:
            shutil.rmtree(WORK, ignore_errors=True)
        if command == "verify":
            results = [
                mask_configurations(engine, {})
                for engine in report["results"]
                if engine["engine_name"] in ("complexity", "cognitive")
            ]
            out[command] = {"exit_code": result.returncode, "results": results}
        else:
            out[command] = {
                "exit_code": result.returncode,
                "report": mask_configurations(report, {}),
            }
    return out


cases = {}
for name, case in CASES.items():
    tree, text, rows, tools, commands = case
    cases[name] = {
        "tree": tree,
        "config": text,
        "database": rows,
        "tools": tools,
        **capture(name, case),
    }
GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "scripts": SCRIPTS, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} compiler boundary cases")
