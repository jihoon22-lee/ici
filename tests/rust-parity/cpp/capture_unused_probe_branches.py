"""Capture frozen v0.13.0 `ici verify --profile deep` (dead engine only) for every `_compiler_result_failure` branch.

    python -I capture_unused_probe_branches.py <oracle-root> <output.json>

Development only; never called by the Rust product. Each case is a small project with a
`compile_commands.json` naming `g++`, a fake `g++` first on PATH in a `tools` directory beside the
project (version probes delegate to /usr/bin/g++, everything else keys on the source name; frozen
refuses a project-contained compiler, the `project-compiler` case) and
`engines.dead.cpp_unused='required'`, run with an empty HOME, `PYTHONHASHSEED=0` and VCS discovery
bounded by the project's `.git`. The saved engine report is recorded with the project root as
`<root>` and the tools directory as `<tools>`.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"


def location(file, line, column, end_line=None, end_column=None):
    caret = f'{{"file":"{file}","line":{line},"column":{column},"byte-column":{column},"display-column":{column}}}'
    if end_line is None:
        return f'{{"caret":{caret}}}'
    finish = f'{{"file":"{file}","line":{end_line},"column":{end_column},"byte-column":{end_column},"display-column":{end_column}}}'
    return f'{{"caret":{caret},"finish":{finish}}}'


def diagnostic(locations):
    joined = ",".join(locations)
    return (
        '{"kind":"warning","message":"int helper() defined but not used",'
        '"option":"-Wunused-function","option_url":"","children":[],"column-origin":1,'
        f'"locations":[{joined}],"escape-source":false}}'
    )


def json_output(name, *diagnostics):
    return f"*{name}.cpp*) printf '%s' '[{','.join(diagnostics)}]' >&2; exit 0 ;;\n"


FAKE = (
    "#!/bin/sh\n"
    'case "$*" in\n'
    '  *--version*) exec /usr/bin/g++ "$@" ;;\n'
    "  *sig.cpp*) kill -KILL $$ ;;\n"
    "  *exit3.cpp*) exit 3 ;;\n"
    "  *quiet1.cpp*) exit 1 ;;\n"
    '  *err0.cpp*) /usr/bin/g++ "$@"; exit 0 ;;\n'
    '  *err1.cpp*) /usr/bin/g++ "$@"; exit 1 ;;\n'
    "  *big.cpp*) head -c 1000001 /dev/zero | tr '\\0' x; exit 0 ;;\n"
    "  *garbage.cpp*) echo 'this is not a compiler diagnostic'; exit 0 ;;\n"
    "  *badjson.cpp*) echo '[{\"kind\":'; exit 0 ;;\n"
    "  *garbage1.cpp*) echo 'this is not a compiler diagnostic'; exit 1 ;;\n"
    "  *nulbyte.cpp*) printf 'a\\000b'; exit 0 ;;\n"
    "  *slow.cpp*) sleep 125; exit 0 ;;\n"
    + "  "
    + json_output("nolocation", diagnostic([]))
    + "  "
    + json_output(
        "repeat",
        diagnostic([location("src/repeat.cpp", 1, 12)]),
        diagnostic([location("src/repeat.cpp", 1, 12)]),
    )
    + "  "
    + json_output("outsideline", diagnostic([location("src/outsideline.cpp", 99, 1)]))
    + "  "
    + json_output("outsidecolumn", diagnostic([location("src/outsidecolumn.cpp", 1, 999)]))
    + "  "
    + json_output("outsideendline", diagnostic([location("src/outsideendline.cpp", 1, 1, 99, 1)]))
    + "  "
    + json_output(
        "outsideendcolumn", diagnostic([location("src/outsideendcolumn.cpp", 1, 1, 1, 999)])
    )
    + "  "
    + json_output("othertu", diagnostic([location("src/other.h", 1, 1)]))
    + "esac\n"
    'exec /usr/bin/g++ "$@"\n'
)
SOURCE = (
    "int used() { return 1; }\nstatic int helper() { return 2; }\nint main() { return used(); }\n"
)
ERROR = "int main() { return missing; }\n"
UNUSED = "static int helper() { return 2; }\nint main() { return 0; }\n"
PYTHON = "def caller():\n    return 1\n    print('never')\n\n\ndef unused_function():\n    return 1\n\n\ndef used_function():\n    return 2\n\n\nused_function()\n"
CONFIG = (
    "[project]\ntype='cpp'\nsource_dirs=['src']\ncompile_database='compile_commands.json'\n"
    "[engines.dead]\ncpp_unused='required'\ncpp_linker='off'\n"
    + "".join(
        f"[engines.{engine}]\nenabled=false\n"
        for engine in (
            "line",
            "lint",
            "compile_db",
            "test",
            "type",
            "python_compat",
            "complexity",
            "sanitize",
            "thread_sanitize",
            "dup",
            "exception",
            "cognitive",
            "security",
            "resource",
            "cycle",
            "binary_compat",
            "build",
        )
    )
)
HYBRID = CONFIG.replace("type='cpp'", "type='hybrid'")


def database(*sources):
    return json.dumps(
        [
            {"directory": ".", "command": f"g++ -std=c++17 -c {source}", "file": source}
            for source in sources
        ]
    )


def single(name, text=SOURCE):
    return {f"src/{name}.cpp": text, "compile_commands.json": database(f"src/{name}.cpp")}


CASES = {
    "control": single("clean"),
    "project-compiler": single("clean"),
    "signal": single("sig"),
    "exit-code": single("exit3"),
    "exit-code-quiet": single("quiet1"),
    "error-with-exit-zero": single("err0", ERROR),
    "error-with-exit-one": single("err1", ERROR),
    "truncated": single("big"),
    "unparseable-text": single("garbage"),
    "unparseable-json": single("badjson"),
    "unparseable-with-exit": single("garbage1"),
    "null-byte": single("nulbyte"),
    "timeout": single("slow"),
    "rejected-no-location": single("nolocation"),
    "rejected-repeat": single("repeat"),
    "rejected-outside-line": single("outsideline"),
    "rejected-outside-column": single("outsidecolumn"),
    "rejected-outside-end-line": single("outsideendline"),
    "rejected-outside-end-column": single("outsideendcolumn"),
    "non-tu-diagnostic": {**single("othertu"), "src/other.h": "int other();\n"},
    "second-source-fails": {
        "src/a.cpp": SOURCE,
        "src/sig.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp", "src/sig.cpp"),
    },
    "first-source-fails": {
        "src/a.cpp": SOURCE,
        "src/zexit3.cpp": SOURCE,
        "src/z.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp", "src/zexit3.cpp", "src/z.cpp"),
    },
    # `_selected_replay_units` and `_prepare_replay_unit` stops before any replay.
    "uncovered-source": {
        "src/a.cpp": SOURCE,
        "src/b.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp"),
    },
    "empty-database": {"src/a.cpp": SOURCE, "compile_commands.json": "[]"},
    "database-other-source": {
        "src/a.cpp": SOURCE,
        "tools/other.cpp": SOURCE,
        "compile_commands.json": database("tools/other.cpp"),
    },
    "repeated-configuration": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp", "src/a.cpp"),
    },
    "context-ingestion-error": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [
                {"directory": ".", "command": "g++ -c src/a.cpp", "file": "src/a.cpp"},
                {"directory": "."},
            ]
        ),
    },
    "unit-ingestion-error": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [{"directory": ".", "command": "g++ -c 'src/a.cpp", "file": "src/a.cpp"}]
        ),
    },
    "unity-build": {
        "src/a.cpp": SOURCE,
        "CMakeCache.txt": "CMAKE_UNITY_BUILD:BOOL=ON\n",
        "compile_commands.json": database("src/a.cpp"),
    },
    "replay-refused": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [{"directory": ".", "command": "g++ --token -c src/a.cpp", "file": "src/a.cpp"}]
        ),
    },
    "unsupported-language": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [{"directory": ".", "command": "g++ -x assembler -c src/a.cpp", "file": "src/a.cpp"}]
        ),
    },
    "compiler-unavailable": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [{"directory": ".", "command": "nosuchcompiler -c src/a.cpp", "file": "src/a.cpp"}]
        ),
    },
    "compiler-unavailable-auto": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [{"directory": ".", "command": "nosuchcompiler -c src/a.cpp", "file": "src/a.cpp"}]
        ),
        "ici.toml": CONFIG.replace("cpp_unused='required'", "cpp_unused='auto'"),
    },
    "missing-database": {"src/a.cpp": SOURCE},
    "malformed-database": {"src/a.cpp": SOURCE, "compile_commands.json": "[{"},
    "clang-absent": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [{"directory": ".", "command": "clang++-99 -c src/a.cpp", "file": "src/a.cpp"}]
        ),
    },
    "unity-named-source": single("unity_clean"),
    "vendor-unit": {
        "src/a.cpp": SOURCE,
        "src/vendor/b.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp", "src/vendor/b.cpp"),
    },
    "uncovered-vendor": {
        "src/a.cpp": SOURCE,
        "src/vendor/b.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp"),
    },
    "too-many-units": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [
                {"directory": ".", "command": f"g++ -DN={i} -c src/a.cpp", "file": "src/a.cpp"}
                for i in range(2049)
            ]
        ),
    },
    "objective-c": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [{"directory": ".", "command": "g++ -x objective-c -c src/a.cpp", "file": "src/a.cpp"}]
        ),
    },
    "other-source-compile-db": {
        "src/a.cpp": SOURCE,
        "tools/other.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp", "tools/other.cpp"),
        "ici.toml": CONFIG.replace("[engines.compile_db]\nenabled=false\n", ""),
    },
    "ingestion-errors-compile-db": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": json.dumps(
            [
                {"directory": ".", "command": "g++ -c 'src/a.cpp", "file": "src/a.cpp"},
                {"directory": "."},
            ]
        ),
        "ici.toml": CONFIG.replace("[engines.compile_db]\nenabled=false\n", ""),
    },
    "malformed-compile-db": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": "[{",
        "ici.toml": CONFIG.replace("[engines.compile_db]\nenabled=false\n", ""),
    },
    "missing-compile-db": {
        "src/a.cpp": SOURCE,
        "ici.toml": CONFIG.replace("[engines.compile_db]\nenabled=false\n", ""),
    },
    "repeated-lint": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp", "src/a.cpp"),
        "ici.toml": CONFIG.replace("[engines.lint]\nenabled=false\n", ""),
    },
    "repeated-compile-db": {
        "src/a.cpp": SOURCE,
        "compile_commands.json": database("src/a.cpp", "src/a.cpp"),
        "ici.toml": CONFIG.replace("[engines.compile_db]\nenabled=false\n", ""),
    },
    "failing-auto": {
        **single("exit3"),
        "ici.toml": CONFIG.replace("cpp_unused='required'", "cpp_unused='auto'"),
    },
    "control-auto": {
        **single("clean"),
        "ici.toml": CONFIG.replace("cpp_unused='required'", "cpp_unused='auto'"),
    },
    # Mixed scopes: the heuristic Python rows stand beside the compiler scope.
    "mixed-python-clean": {**single("clean"), "src/dead.py": PYTHON, "ici.toml": HYBRID},
    "mixed-python-unused": {**single("mixed", UNUSED), "src/dead.py": PYTHON, "ici.toml": HYBRID},
    "mixed-python-failing": {**single("exit3"), "src/dead.py": PYTHON, "ici.toml": HYBRID},
    "mixed-python-unavailable": {
        "src/a.cpp": SOURCE,
        "src/dead.py": PYTHON,
        "ici.toml": HYBRID.replace("cpp_unused='required'", "cpp_unused='auto'"),
    },
}


def run_case(oracle, tree, contained=False):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        tools = base / "bin" if contained else base.parent / "tools"
        tools.mkdir(parents=True)
        (tools / "g++").write_text(FAKE, encoding="utf-8")
        (tools / "g++").chmod(0o755)
        for name, text in tree.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
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
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "PATH": f"{tools}:/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": temporary,
            },
            check=False,
        )
        report = (base / "verify_report.json").read_text(encoding="utf-8")
        return {
            "exit_code": completed.returncode,
            "report": json.loads(report.replace(str(tools), "<tools>").replace(str(base), ROOT)),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "gxx": gxx,
        "fake": FAKE,
        "cases": {},
    }
    for name, tree in CASES.items():
        tree = {"ici.toml": CONFIG, **tree}
        case = {"tree": tree, **run_case(oracle, tree, name == "project-compiler")}
        document["cases"][name] = case
        report = next(item for item in case["report"]["results"] if item["engine_name"] == "dead")
        print(
            name, case["exit_code"], report["status"], report["evidence"], report["summary"][:100]
        )
        for target in report["targets"]:
            print("   ", target["target_name"], target["status"], target["message"][:110])
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
