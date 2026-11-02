"""Development-only frozen legacy C++ lint captures; never called by the Rust product.

`cases`: heuristic fallback (no compilation database) with PATH=/usr/bin:/bin, where clang-tidy
and clazy-standalone are absent; g++ wording depends on its version, which is recorded.

`tool_cases`: `ici verify` with an exact compilation database and a temporary bin directory
first on PATH holding `clang-tidy` (a symlink to ICI_TEST_CLANG_TIDY) and/or `clazy-standalone`
(the names core/toolchain.py probes). Frozen runs clang tools with a replaced minimal
environment (core/cpp_replay.replay_environment), so a clazy that needs LD_LIBRARY_PATH is
wrapped in a script that sets ICI_TEST_CLANG_LIBRARY_PATH itself; the `clazy-unloadable` case
links the raw binary instead. Capability probe cases: `clazy-env-library-path` links the raw
binary with LD_LIBRARY_PATH in the caller environment (frozen probes with it and runs without
it); `clazy-run-fails` passes the `--version` probe and exits 3 on every run;
`tidy-no-version` exits 0 without a parseable version.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-lint-capture"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
DATABASE = (
    '[{"directory":"{root}","file":"src/clean.cpp",'
    '"arguments":["g++","-std=c++17","-Iinclude","-c","src/clean.cpp"]}]'
)
MAKE = "[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
# name: (fixture, command, ici.toml tail, extra files with {root} = the project directory)
CASES = {
    "lint": ("lint-fixture", "lint", "", {}),
    "lint-warnings-only": ("lint-warnings", "lint", "", {}),
    # No selected Python or C++ source: frozen LintEngine reports LintScope SKIP (lint.py:92).
    "lint-no-sources": ("lint-empty", "lint", "", {}),
    "lint-tidy-off": ("lint-fixture", "lint", "[engines.lint]\nclang_tidy='off'\n", {}),
    "lint-clazy-required": ("lint-warnings", "lint", "[engines.lint]\nclazy='required'\n", {}),
    "verify": ("lint-fixture", "verify", "", {}),
    # Standalone `ici lint` has no analysis context: a compilation database or a declared Make
    # build changes nothing and nothing is built (the Makefile would fail on src/broken.cpp).
    "lint-with-database": (
        "lint-fixture",
        "lint",
        "",
        {"build/compile_commands.json": DATABASE},
    ),
    "lint-explicit-database": (
        "lint-fixture",
        "lint",
        "compile_database='build/compile_commands.json'\n",
        {"build/compile_commands.json": DATABASE},
    ),
    "lint-make": (
        "lint-fixture",
        "lint",
        MAKE,
        {"Makefile": "all:\n\tmkdir -p build/ici-make\n\tg++ src/*.cpp -o build/ici-make/app\n"},
    ),
}


def run(
    fixture: str,
    command: str,
    config: str,
    files: dict,
    path: str = "/usr/bin:/bin",
    extra_env: dict | None = None,
) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        for name, text in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text.replace("{root}", str(project)), encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={
                "PATH": path,
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
                **(extra_env or {}),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
        built = (project / "build/ici-make").exists()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engine = report
    if command == "verify":
        engine = next(item for item in report["results"] if item.get("engine_name") == "lint")
    captured = {
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in engine["targets"]],
        "findings": engine["findings"],
        # The frozen LintEngine `extra` counters and its process `tool_evidence`, with the
        # project as {root} and the tool directory as {bin}.
        **json.loads(
            json.dumps(
                {
                    "extra": engine.get("extra", {}),
                    "tool_evidence": engine.get("tool_evidence", []),
                }
            )
            .replace(str(project), "{root}")
            .replace(str(Path(tempfile.gettempdir()) / "ici-cpp-lint-tools/bin"), "{bin}")
        ),
    }
    if command != "verify":
        captured["exit_code"] = result.returncode
    if files:
        captured["built"] = built
    return captured


cases = {
    name: {
        "fixture": fixture,
        "command": command,
        "config": config,
        "files": files,
        "frozen": run(fixture, command, config, files),
    }
    for name, (fixture, command, config, files) in CASES.items()
}
TOOLS = Path(tempfile.gettempdir()) / "ici-cpp-lint-tools"
QT = "-DQT_CORE_LIB"
LIBRARY = "{clang-library-path}"
# name: (fixture, database sources (None: every src/ file), extra compiler flags, tools,
# extra caller environment with LIBRARY = ICI_TEST_CLANG_LIBRARY_PATH, ici.toml tail)
TOOL_CASES = {
    "tidy-only": ("lint-tidy", None, [], ("clang-tidy",), {}, ""),
    "clazy-only": ("lint-tidy", None, [], ("clazy",), {}, ""),
    "tidy-and-clazy": ("lint-tidy", None, [], ("clang-tidy", "clazy"), {}, ""),
    # clazy --only-qt reports only for Qt code (QT_CORE_LIB), which also turns on frozen
    # Qt compatibility verification (QtCompatibility targets).
    "tidy-and-clazy-qt": ("lint-tidy", None, [QT], ("clang-tidy", "clazy"), {}, ""),
    "tidy-partial-database": ("lint-fixture", ["src/clean.cpp"], [], ("clang-tidy",), {}, ""),
    "clazy-unloadable": ("lint-tidy", None, [], ("clazy-raw",), {}, ""),
    "clazy-env-library-path": (
        "lint-tidy",
        None,
        [],
        ("clazy-raw",),
        {"LD_LIBRARY_PATH": LIBRARY},
        "",
    ),
    "clazy-run-fails": ("lint-tidy", None, [], ("clazy-probe-only",), {}, ""),
    "tidy-no-version": ("lint-tidy", None, [], ("tidy-no-version",), {}, ""),
    # No compilation database ([] sources): a probed clang-tidy "requires an exact compilation
    # database", the raw clazy that fails its probe is "unavailable or incompletely probed".
    "no-database-probe": ("lint-tidy", [], [], ("clang-tidy", "clazy-raw"), {}, ""),
    # Required tools: frozen ERROR with NOT_RUN evidence under verify.
    "clazy-required-unloadable": (
        "lint-tidy",
        None,
        [],
        ("clazy-raw",),
        {},
        "[engines.lint]\nclazy='required'\n",
    ),
    "tidy-required-no-database": (
        "lint-tidy",
        [],
        [],
        ("clang-tidy",),
        {},
        "[engines.lint]\nclang_tidy='required'\n",
    ),
}


def tool_bin(directory: Path, tools: tuple[str, ...]) -> str:
    directory.mkdir(parents=True)
    for tool in tools:
        if tool == "clang-tidy":
            os.symlink(os.environ["ICI_TEST_CLANG_TIDY"], directory / "clang-tidy")
        elif tool == "clazy-raw":
            os.symlink(os.environ["ICI_TEST_CLAZY"], directory / "clazy-standalone")
        elif tool in ("clazy-probe-only", "tidy-no-version"):
            name = "clazy-standalone" if tool == "clazy-probe-only" else "clang-tidy"
            script = (
                '#!/bin/sh\nif [ "$1" = --version ]; then LD_LIBRARY_PATH={} exec {} "$@"; fi\n'
                "exit 3\n".format(
                    os.environ["ICI_TEST_CLANG_LIBRARY_PATH"], os.environ["ICI_TEST_CLAZY"]
                )
                if tool == "clazy-probe-only"
                else "#!/bin/sh\necho clang-tidy without a version banner\nexit 0\n"
            )
            (directory / name).write_text(script, encoding="utf-8")
            (directory / name).chmod(0o755)
        else:
            wrapper = directory / "clazy-standalone"
            wrapper.write_text(
                '#!/bin/sh\nLD_LIBRARY_PATH={} exec {} "$@"\n'.format(
                    os.environ["ICI_TEST_CLANG_LIBRARY_PATH"], os.environ["ICI_TEST_CLAZY"]
                ),
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
    return f"{directory}:/usr/bin:/bin"


def database(fixture: str, sources: list[str] | None, flags: list[str]) -> str:
    if sources is None:
        sources = sorted(f"src/{path.name}" for path in (FIXTURES / fixture / "src").iterdir())
    return json.dumps(
        [
            {
                "directory": "{root}",
                "file": source,
                "arguments": ["g++", "-std=c++17", *flags, "-Iinclude", "-c", source],
            }
            for source in sources
        ]
    )


tool_cases = {}
for name, (fixture, sources, flags, tools, extra, config) in TOOL_CASES.items():
    shutil.rmtree(TOOLS, ignore_errors=True)
    try:
        path = tool_bin(TOOLS / "bin", tools)
        files = (
            {"build/compile_commands.json": database(fixture, sources, flags)}
            if sources != []
            else {}
        )
        library = os.environ["ICI_TEST_CLANG_LIBRARY_PATH"]
        environment = {key: value.replace(LIBRARY, library) for key, value in extra.items()}
        tool_cases[name] = {
            "fixture": fixture,
            "command": "verify",
            "config": config,
            "files": files,
            "tools": list(tools),
            "frozen": run(fixture, "verify", config, files, path, environment),
        }
        if extra:
            tool_cases[name]["env"] = extra
    finally:
        shutil.rmtree(TOOLS, ignore_errors=True)
VERSIONS = {
    name: subprocess.run(
        [os.environ[variable], "--version"],
        env={"LD_LIBRARY_PATH": os.environ["ICI_TEST_CLANG_LIBRARY_PATH"]},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    for name, variable in (("clang-tidy", "ICI_TEST_CLANG_TIDY"), ("clazy", "ICI_TEST_CLAZY"))
}
(ROOT / "tests/rust-parity/cpp/lint-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "gxx": GXX,
            "base": BASE,
            "cases": cases,
            "tool_cases": tool_cases,
            "tool_versions": VERSIONS,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} lint cases and {len(tool_cases)} clang tool cases with {GXX}")
