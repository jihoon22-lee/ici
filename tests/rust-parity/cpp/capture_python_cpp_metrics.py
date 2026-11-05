"""Development-only frozen captures of a legacy `type='python'` project with C/C++ sources.

Never called by the Rust product. Frozen engines read `project_cpp_sources()` whatever the
declared project type (engines/base.py:76): lint, compile_db, type, cycle, complexity, dead,
dup and exception measure a Python project's C/C++ translation units exactly as a hybrid's.
Only the declared-type gates differ: ComplexityEngine measures C++ only when the type is
cpp/hybrid or `project_root.rglob("*.cpp")` finds a path (complexity.py:62, so a `.cc`-only
tree is not measured), and TestEngine names a C++ suite only for `tests/**/*.cpp`
(test.py:268). CognitiveEngine always measures the C/C++ sources (cognitive.py:98).

Each case copies parity-fixtures/cpp/python-with-cpp (one Python module) into a fresh temporary
project (with an empty `.git`), writes its `ici.toml` and C/C++ files, runs the frozen oracle
`ici <command> --report` (`verify` in full: every default engine; `verify-deep` is
`verify --profile deep`) with PATH=/usr/bin:/bin and
ICI_PYTHON the capture interpreter, and records the exit code, stdout and the whole saved
report. Only the project root is normalized (`<root>`). The Rust replay is
`crates/ici-cli/tests/legacy_engine_extras.rs`
`legacy_python_project_cpp_sources_equal_frozen_except_allowed`.

Usage: `capture_python_cpp_metrics.py [case-prefix ...]`.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/python-cpp-metrics-oracle.json"
FIXTURES = ROOT / "parity-fixtures/cpp"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
PYTHON_PROJECT = "[project]\ntype='python'\nsource_dirs=['src']\n"
LIMITS = "[engines.complexity]\nwarn_cc=3\nfail_cc=6\n[engines.cognitive]\nwarn=3\nfail=6\n"
ZOO = FIXTURES / "heuristic-metrics/zoo/src"
FLOW = (ZOO / "flow.cpp").read_text(encoding="utf-8")
OPS = (ZOO / "ops.cc").read_text(encoding="utf-8")
OPEN = (FIXTURES / "metric-sources/unterminated/src/open.cpp").read_text(encoding="utf-8")
TREES = {
    # Positive and negative: functions under and over the thresholds in a `.cpp` source.
    "cpp": {"src/flow.cpp": FLOW},
    # Only a `.cc` source: cognitive measures it, complexity's `*.cpp` rglob does not.
    "cc": {"src/ops.cc": OPS},
    # A `.cc` source plus a `*.cpp` outside `source_dirs`: the rglob opens complexity's C++
    # scope, which still measures only the selected `src/ops.cc`.
    "cc-rglob": {"src/ops.cc": OPS, "tools/gen.cpp": "int gen() { return 1; }\n"},
    # Only a top-level `include/` header, no translation unit: cycle, dup and dead read it
    # (`get_all_cpp_headers`), the translation-unit readers see no C/C++.
    "header": {"include/api.hpp": "#pragma once\nint api(int x);\n"},
    # Failure: a C++ body the scanner cannot close.
    "unterminated": {"src/flow.cpp": FLOW, "src/open.cpp": OPEN},
}
CASES = {}
for _tree, _files in TREES.items():
    for _command in ("complexity", "cognitive", "verify"):
        CASES[f"{_command}:{_tree}"] = (_command, PYTHON_PROJECT + LIMITS, _files)
# The deep profile's declared-type gates: BuildEngine compiles C++ only for cpp/hybrid or a build
# descriptor (build.py:64-72); thread_sanitize, binary_compat, integration and cognitive follow
# their own C/C++ source and test inventories.
CASES["verify-deep:cpp"] = ("verify-deep", PYTHON_PROJECT + LIMITS, TREES["cpp"])
# The same deep run declared `type='hybrid'` (no `tests/`): `_mark_zero_tests` for both facets,
# then the deep Python quality pass (test_quality.py:332, `proj_type in ("python", "hybrid")`).
CASES["verify-deep:hybrid"] = (
    "verify-deep",
    PYTHON_PROJECT.replace("type='python'", "type='hybrid'") + LIMITS,
    TREES["cpp"],
)
# A `tests/**/*.cpp` suite: TestEngine runs C++ tests whatever the declared type (test.py:268).
CASES["verify:tests-cpp"] = (
    "verify",
    PYTHON_PROJECT + LIMITS,
    {**TREES["cpp"], "tests/test_flow.cpp": "int main() { return 0; }\n"},
)


def run(command: str, config: str, files: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cppval-capture-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURES / "python-with-cpp", project)
        (project / ".git").mkdir()
        (project / "ici.toml").write_text(config, encoding="utf-8")
        for relative, text in files.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        home = work / "home"
        home.mkdir()
        argv = ["verify", "--profile", "deep"] if command == "verify-deep" else [command]
        result = subprocess.run(
            [PYTHON, "-m", "ici", *argv, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home),
                "ICI_PYTHON": PYTHON,
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = project / f"{argv[0]}_report.json"
        text = report.read_text(encoding="utf-8") if report.exists() else "null"
        return {
            "fixture": "python-with-cpp",
            "command": command,
            "config": config,
            "path": "tools",
            "exit_code": result.returncode,
            "stdout": result.stdout.replace(str(project), "<root>"),
            "report": json.loads(text.replace(str(project), "<root>")),
            "files": files,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    prefixes = sys.argv[1:]
    existing = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    cases = dict(existing.get("cases", {}))
    for name, spec in CASES.items():
        if prefixes and not any(name.startswith(prefix) for prefix in prefixes):
            continue
        cases[name] = run(*spec)
        print(name, cases[name]["exit_code"], flush=True)
    gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
    GOLDEN.write_text(
        json.dumps(
            {"oracle": EXPECTED, "gxx": gxx, "cases": dict(sorted(cases.items()))},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
