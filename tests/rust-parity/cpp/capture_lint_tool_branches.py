"""Development-only frozen legacy clang-tidy / Clazy branch captures; never called by Rust.

Every case is `ici verify --report` over a copy of a parity-fixtures/cpp fixture with an exact
compilation database, comparing the frozen lint engine (analysis/_clang_tidy.py,
analysis/_clazy.py) for the lint configuration keys (clang_tidy_checks, clang_tidy_config,
discovered `.clang-tidy`, clazy_checks, clazy_profile), the Clazy compiler-wrapper provider,
config validation, context/replay/toolchain errors, process failures, unparseable output and
the per-unit and global time budgets.

Tools live in a temporary bin directory first on PATH. Their shell scripts are stored in the
golden with placeholders the Rust replay substitutes: {clang-tidy} (ICI_TEST_CLANG_TIDY),
{clazy} (ICI_TEST_CLAZY), {clazy-wrapper} (the `clazy` compiler-wrapper script next to it),
{clang} (ICI_TEST_CLANG), {lib} (ICI_TEST_CLANG_LIBRARY_PATH) and {log} (the argv log). Frozen
runs clang tools with a replaced minimal environment (core/cpp_replay.replay_environment), so
every wrapper sets LD_LIBRARY_PATH itself. `-log` tools append their argv (one argument per
line, then `--END--`) to {log}; the capture keeps every non-`--version` invocation with the
project as {root} and the bin directory as {bin}.

`patches` replace frozen module constants (`_TIMEOUT_SECONDS`, `_GLOBAL_TIMEOUT_SECONDS`) through
a launcher, as capture_lint_budget.py does; the Rust replay sets the same values through its
process-local test hook. The project is `<work>/project`, so `../outside.yaml` is outside it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-lint-tool-branches"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
QT = "-DQT_CORE_LIB"
LAUNCH = (
    "import json, sys\n"
    "import ici.analysis._clang_tidy as tidy\n"
    "import ici.analysis._clazy as clazy\n"
    "for name, value in json.loads(sys.argv.pop(1)).items():\n"
    "    module, constant = name.split('.')\n"
    "    setattr({'tidy': tidy, 'clazy': clazy}[module], constant, float(value))\n"
    "from ici.__main__ import main\n"
    "sys.argv[0] = 'ici'\n"
    "main()\n"
)
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone", "clazy", "clang++"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]

BOUNDARY = "--checks=-*,readability-function-size"
LOG = 'printf "%s\\n" "$@" --END-- >> {log}\n'
VERSION = 'if [ "$1" = --version ]; then LD_LIBRARY_PATH={lib} exec REAL "$@"; fi\n'
# Fake behaviours applied to the sources the case pattern matches; other sources run the tool.
BEHAVIOURS = {
    "exit3": "exit 3",
    "signal": "kill -KILL $$",
    "big": "head -c 1000001 /dev/zero | tr '\\0' x; exit 0",
    "summary-only": "echo '3 warnings generated.' >&2; exit 0",
    "nul": "printf 'x\\000y\\n'; exit 0",
    "slow": "sleep 30; exit 0",
    # One frozen `parse_clazy_diagnostics` refusal each; the text reaches the lint summary.
    "clazy-fixit": 'echo \'fix-it:"src/qt.cpp":{1:1-1:2}:"x"\'; exit 0',
    "clazy-no-check": "echo 'src/qt.cpp:1:1: warning: msg' >&2; exit 0",
    "clazy-compiler-error": "echo 'src/qt.cpp:1:1: error: msg [-Wformat]' >&2; exit 0",
    "clazy-unsafe-rule": "echo 'src/qt.cpp:1:1: warning: msg [foo]' >&2; exit 0",
    "clazy-bad-rule": "echo 'src/qt.cpp:1:1: warning: msg [-Wclazy-A]' >&2; exit 0",
    "clazy-conflict": (
        "echo 'src/qt.cpp:1:1: warning: x [-Wclazy-y] z [-Wclazy-old-style-connect]' >&2; exit 0"
    ),
    "clazy-context-only": "echo 'In file included from src/qt.cpp:1:' >&2; exit 0",
}


def script(real: str, *, log: bool = False, behaviour: str = "", pattern: str = "") -> str:
    text = "#!/bin/sh\n" + VERSION.replace("REAL", real)
    if log:
        text += LOG
    if behaviour:
        text += 'case "$*" in\n  ' + pattern + ") " + BEHAVIOURS[behaviour] + " ;;\nesac\n"
    return text + "LD_LIBRARY_PATH={lib} exec " + real + ' "$@"\n'


TIDY = {"clang-tidy": script("{clang-tidy}")}
CLAZY = {"clazy-standalone": script("{clazy}")}
TOOLS = {**TIDY, **CLAZY}
TIDY_LOG = {"clang-tidy": script("{clang-tidy}", log=True)}
CLAZY_LOG = {"clazy-standalone": script("{clazy}", log=True)}
# The Clazy compiler wrapper (frozen `clazy` alias): the packaged script loads ClazyPlugin.so
# into $CLANGXX, the approved clang++ capability, which needs the library path itself.
CLANGXX = {"clang++": '#!/bin/sh\nLD_LIBRARY_PATH={lib} exec {clang} "$@"\n'}
WRAPPER = {"clazy": "#!/bin/sh\n" + LOG + 'exec {clazy-wrapper} "$@"\n'}
# g++ whose C++ include search (`-E -x c++ -v <input>`) fails: the GCC stdlib projection error.
FAILING_SEARCH = (
    '#!/bin/sh\ncase "$*" in\n  *\'-E -x c++ -v \'*) exit 1 ;;\nesac\nexec /usr/bin/g++ "$@"\n'
)


def row(source: str, *flags: str, directory: str = "{root}", extra: tuple = ()) -> dict:
    return {
        "directory": directory,
        "file": source,
        "arguments": ["g++", "-std=c++17", *flags, "-Iinclude", "-c", source, *extra],
    }


TIDY_SOURCES = ["src/clean.cpp", "src/qt.cpp", "src/tidy.cpp"]
DEFAULT = [row(source) for source in TIDY_SOURCES]
QT_ROWS = [row(source, QT) for source in TIDY_SOURCES]
TIDY_ONLY = "[engines.lint]\nclazy='off'\n"
CLAZY_ONLY = "[engines.lint]\nclang_tidy='off'\n"
SUB = "int extra_value() { return 7; }\n"
MACRO = (
    '#include "minimal_qbytearray.h"\n\n'
    "#define ENV_EMPTY(name) qgetenv(name).isEmpty()\n\n"
    'bool macro_configured() { return ENV_EMPTY("ICI_FIXTURE"); }\n'
)
CHECKS = "Checks: '-*,performance-*'\n"
# name: case fields (defaults: lint-tidy, DEFAULT rows, TOOLS, no config/files/links/patches)
CASES = {
    # --- clang_tidy_checks / clang_tidy_config / discovered .clang-tidy (argv logged) ---
    "tidy-checks": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY + "clang_tidy_checks=['-*','clang-analyzer-*']\n",
    },
    "tidy-explicit-config": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY + "clang_tidy_config='tidy.yaml'\n",
        "files": {"tidy.yaml": CHECKS},
    },
    "tidy-explicit-config-and-checks": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY
        + "clang_tidy_config='tidy.yaml'\nclang_tidy_checks=['-*','clang-analyzer-*']\n",
        "files": {"tidy.yaml": CHECKS},
    },
    "tidy-discovered-config": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY,
        "files": {"src/.clang-tidy": CHECKS},
    },
    "tidy-discovered-root-config": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY,
        "files": {".clang-tidy": CHECKS},
    },
    # A discovered config that is a symlink to a file inside the project root.
    "tidy-discovered-symlink-inside": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY,
        "files": {"configs/tidy.yaml": CHECKS},
        "links": {"src/.clang-tidy": "../configs/tidy.yaml"},
    },
    # An explicit config that is a symlink to a file inside the project root.
    "tidy-explicit-symlink-inside": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY + "clang_tidy_config='tidy-link.yaml'\n",
        "files": {"configs/tidy.yaml": CHECKS},
        "links": {"tidy-link.yaml": "configs/tidy.yaml"},
    },
    # 'ExtraArgs' only as part of a longer word: no \b match in frozen.
    "tidy-config-word-boundary": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY + "clang_tidy_config='tidy.yaml'\n",
        "files": {"tidy.yaml": "# notExtraArgs and InheritParentConfigs are comments\n" + CHECKS},
    },
    # LLVM YAML decodes double-quoted escapes: frozen's raw-text regex accepts these configs
    # and clang-tidy then applies ExtraArgs / ExtraArgsBefore (native refuses them).
    "tidy-config-escaped-key": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY + "clang_tidy_config='tidy.yaml'\n",
        "files": {"tidy.yaml": CHECKS + "\"\\x45xtraArgs\": ['-DICI_ESCAPED']\n"},
    },
    # A double quote inside a comment desynchronises naive quote tracking.
    "tidy-config-quote-desync": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY + "clang_tidy_config='tidy.yaml'\n",
        "files": {
            "tidy.yaml": "Checks: '-*,performance-*' # a\"b\n\"\\x45xtraArgs\": ['-DICI_ESCAPED']\n"
        },
    },
    "tidy-discovered-escaped-key": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY,
        "files": {"src/.clang-tidy": CHECKS + "\"Extra\\u0041rgsBefore\": ['-DICI_ESCAPED']\n"},
    },
    # A discovered config in one subdirectory only applies to its units.
    "tidy-discovered-subdirectory": {
        "tools": TIDY_LOG,
        "config": TIDY_ONLY,
        "files": {"src/sub/.clang-tidy": CHECKS, "src/sub/extra.cpp": SUB},
        "rows": [*DEFAULT, row("src/sub/extra.cpp")],
    },
    # --- clang-tidy config errors ---
    "tidy-config-missing": {"config": "[engines.lint]\nclang_tidy_config='missing.yaml'\n"},
    "tidy-config-outside": {
        "config": "[engines.lint]\nclang_tidy_config='../outside.yaml'\n",
        "outside": {"outside.yaml": CHECKS},
    },
    "tidy-config-directory": {"config": "[engines.lint]\nclang_tidy_config='src'\n"},
    "tidy-config-unsafe": {
        "config": "[engines.lint]\nclang_tidy_config='tidy.yaml'\n",
        "files": {"tidy.yaml": CHECKS + "ExtraArgs: ['-DX']\n"},
    },
    "tidy-config-null": {
        "config": "[engines.lint]\nclang_tidy_config='tidy.yaml'\n",
        "files": {"tidy.yaml": CHECKS + "# \u0000\n"},
    },
    "tidy-config-large": {
        "config": "[engines.lint]\nclang_tidy_config='tidy.yaml'\n",
        "generated": {"tidy.yaml": 1_048_577},
    },
    "tidy-config-outside-symlink": {
        "config": "[engines.lint]\nclang_tidy_config='tidy-link.yaml'\n",
        "outside": {"outside.yaml": CHECKS},
        "links": {"tidy-link.yaml": "../outside.yaml"},
    },
    # A discovered unsafe config in one subdirectory: a per-unit error at that unit only.
    "tidy-discovered-unsafe": {
        "files": {"src/sub/.clang-tidy": "InheritParentConfig: true\n", "src/sub/extra.cpp": SUB},
        "rows": [*DEFAULT, row("src/sub/extra.cpp")],
    },
    "tidy-discovered-dangling": {"links": {"src/.clang-tidy": "missing.yaml"}},
    # --- clazy_checks / clazy_profile (argv logged) ---
    "clazy-checks": {
        "tools": CLAZY_LOG,
        "config": CLAZY_ONLY + "clazy_checks=['qstring-allocations']\n",
        "rows": QT_ROWS,
    },
    "clazy-checks-and-profile": {
        "tools": CLAZY_LOG,
        "config": CLAZY_ONLY + "clazy_checks=['qgetenv']\nclazy_profile='level1'\n",
        "rows": QT_ROWS,
    },
    "clazy-profile-level1": {
        "tools": CLAZY_LOG,
        "config": CLAZY_ONLY + "clazy_profile='level1'\n",
        "rows": QT_ROWS,
    },
    # Check names with the schema's '.' and '*' characters reach clazy unchanged.
    "clazy-checks-pattern": {
        "tools": CLAZY_LOG,
        "config": CLAZY_ONLY + "clazy_checks=['qgetenv','level0.*']\n",
        "rows": QT_ROWS,
    },
    # A clazy diagnostic inside a macro expansion carries an 'expanded from macro' note.
    "clazy-macro-note": {
        "tools": CLAZY,
        "config": CLAZY_ONLY,
        "files": {"src/macro.cpp": MACRO},
        "rows": [*QT_ROWS, row("src/macro.cpp", QT)],
    },
    # --- Clazy compiler wrapper (`clazy` alias, CLANGXX = the approved clang++) ---
    "clazy-wrapper": {
        "tools": {**WRAPPER, **CLANGXX},
        "config": CLAZY_ONLY,
        "rows": QT_ROWS,
    },
    "clazy-wrapper-checks": {
        "tools": {**WRAPPER, **CLANGXX},
        "config": CLAZY_ONLY + "clazy_checks=['qstring-allocations']\n",
        "rows": QT_ROWS,
    },
    "clazy-wrapper-without-clangxx": {"tools": WRAPPER, "config": CLAZY_ONLY, "rows": QT_ROWS},
    # --- process and output failures ---
    "tidy-exit3": {
        "tools": {"clang-tidy": script("{clang-tidy}", behaviour="exit3", pattern="*clean.cpp*")},
        "config": TIDY_ONLY,
    },
    "tidy-signal": {
        "tools": {"clang-tidy": script("{clang-tidy}", behaviour="signal", pattern="*clean.cpp*")},
        "config": TIDY_ONLY,
    },
    "tidy-truncated": {
        "tools": {"clang-tidy": script("{clang-tidy}", behaviour="big", pattern="*clean.cpp*")},
        "config": TIDY_ONLY,
    },
    "tidy-unparseable": {
        "tools": {
            "clang-tidy": script("{clang-tidy}", behaviour="summary-only", pattern="*clean.cpp*")
        },
        "config": TIDY_ONLY,
    },
    "clazy-signal": {
        "tools": {"clazy-standalone": script("{clazy}", behaviour="signal", pattern="*qt.cpp*")},
        "config": CLAZY_ONLY,
        "rows": QT_ROWS,
    },
    "clazy-truncated": {
        "tools": {"clazy-standalone": script("{clazy}", behaviour="big", pattern="*qt.cpp*")},
        "config": CLAZY_ONLY,
        "rows": QT_ROWS,
    },
    "clazy-nul": {
        "tools": {"clazy-standalone": script("{clazy}", behaviour="nul", pattern="*qt.cpp*")},
        "config": CLAZY_ONLY,
        "rows": QT_ROWS,
    },
    # --- context, replay and toolchain errors ---
    "tools-replay-error": {
        "files": {"src/replay.cpp": SUB},
        "rows": [*DEFAULT, row("src/replay.cpp", extra=("other.o",))],
    },
    "tools-unit-context-error": {"rows": [*DEFAULT[:2], row("src/tidy.cpp", "@missing.rsp")]},
    "tools-ingestion-error": {"rows": [*DEFAULT, {"directory": "{root}", "file": 5}]},
    "tools-no-replayable": {
        "files": {"tools/gen.cpp": SUB},
        "rows": [row("tools/gen.cpp")],
    },
    "tools-partial-database": {"rows": DEFAULT[:1]},
    # A production source that is not readable (mode 000) and more than 2048 production sources.
    # Frozen line counts an unreadable file as 0 lines and its JSON reporter then refuses the
    # line target (end_line 0) and writes no report; the line engine is disabled to observe lint.
    "tools-unreadable-source": {
        "config": "[engines.line]\nenabled=false\n",
        "modes": {"src/clean.cpp": 0},
    },
    "tools-source-count": {"generated_sources": 2049},
    "tools-gcc-search-fails": {"gxx": FAILING_SEARCH},
    # --- time budgets (frozen constants patched; Rust test hook) ---
    "tools-unit-timeout": {
        "tools": {
            "clang-tidy": script("{clang-tidy}", behaviour="slow", pattern="*clean.cpp*"),
            "clazy-standalone": script("{clazy}", behaviour="slow", pattern="*clean.cpp*"),
        },
        "patches": {"tidy._TIMEOUT_SECONDS": 4, "clazy._TIMEOUT_SECONDS": 4},
    },
    "tools-global-budget-spent": {
        "patches": {"tidy._GLOBAL_TIMEOUT_SECONDS": 0, "clazy._GLOBAL_TIMEOUT_SECONDS": 0},
    },
    "tools-global-budget-mid": {
        "tools": {
            "clang-tidy": script("{clang-tidy}", behaviour="slow", pattern="*clean.cpp*"),
            "clazy-standalone": script("{clazy}", behaviour="slow", pattern="*clean.cpp*"),
        },
        "patches": {"tidy._GLOBAL_TIMEOUT_SECONDS": 6, "clazy._GLOBAL_TIMEOUT_SECONDS": 6},
    },
}


OUTSIDE_SOURCE = "int outside_value() { return 1; }\n"
# g++ whose C++ include search looks like GCC but has no search block; g++ that changes itself.
UNPARSEABLE_SEARCH = (
    '#!/bin/sh\ncase "$*" in\n  *\'-E -x c++ -v \'*) echo "COLLECT_GCC=g++" >&2; exit 0 ;;\nesac\n'
    'exec /usr/bin/g++ "$@"\n'
)
CHANGING_SEARCH = (
    '#!/bin/sh\ncase "$*" in\n  *\'-E -x c++ -v \'*) touch -d 2001-01-01 "$0" ;;\nesac\n'
    'exec /usr/bin/g++ "$@"\n'
)
SYNTAX_ONLY = "[engines.lint]\nclazy='off'\nclang_tidy='off'\n"
# Frozen `parse_clazy_diagnostics` refusals, one per behaviour.
for _behaviour in (
    "clazy-fixit",
    "clazy-no-check",
    "clazy-compiler-error",
    "clazy-unsafe-rule",
    "clazy-bad-rule",
    "clazy-conflict",
    "clazy-context-only",
):
    CASES[f"parse-{_behaviour}"] = {
        "config": CLAZY_ONLY,
        "tools": {"clazy-standalone": script("{clazy}", behaviour=_behaviour, pattern="*qt.cpp*")},
    }
# Frozen `_normalized_sources`: a scanned source that escapes the project or cannot be read.
for _name, _config in (("tidy", TIDY_ONLY), ("clazy", CLAZY_ONLY), ("syntax", SYNTAX_ONLY)):
    CASES[f"{_name}-source-outside"] = {
        "config": _config,
        "outside": {"outside.cpp": OUTSIDE_SOURCE},
        "links": {"src/outside.cpp": "../../outside.cpp"},
    }
# Frozen `_toolchain_selector_arguments` and the GCC include search refusals.
CASES["tools-selector-without-value"] = {"rows": [*DEFAULT[:2], row("src/tidy.cpp", "-march=")]}
CASES["tools-gcc-search-unparseable"] = {"gxx": UNPARSEABLE_SEARCH}
CASES["tools-gcc-compiler-changed-during"] = {"gxx": CHANGING_SEARCH}
# Frozen `build_replay_command` refusals for a compiler or command the database records.
PLAIN_COMPILER = '#!/bin/sh\nexec /usr/bin/g++ "$@"\n'
CASES["tools-compiler-not-probed"] = {
    "tools": {**TOOLS, "g++-12": PLAIN_COMPILER},
    "rows": [
        *DEFAULT[:2],
        {
            "directory": "{root}",
            "file": "src/tidy.cpp",
            "arguments": ["g++-12", "-std=c++17", "-Iinclude", "-c", "src/tidy.cpp"],
        },
    ],
}
CASES["tools-compiler-not-executable"] = {
    "outside": {"g++": "not a program\n"},
    "rows": [
        *DEFAULT[:2],
        {
            "directory": "{root}",
            "file": "src/tidy.cpp",
            "arguments": ["{root}/../g++", "-std=c++17", "-Iinclude", "-c", "src/tidy.cpp"],
        },
    ],
}
CASES["tools-empty-command-argument"] = {
    "rows": [
        *DEFAULT[:2],
        {
            "directory": "{root}",
            "file": "src/tidy.cpp",
            "arguments": ["g++", "", "-std=c++17", "-Iinclude", "-c", "src/tidy.cpp"],
        },
    ],
}
CASES["tools-command-too-large"] = {
    "rows": [*DEFAULT[:2], row("src/tidy.cpp", *(["-DX"] * 32_769))],
}


def placeholders(bin_dir: Path, log: Path) -> dict:
    clazy = os.environ["ICI_TEST_CLAZY"]
    return {
        "{clang-tidy}": os.environ["ICI_TEST_CLANG_TIDY"],
        "{clazy-wrapper}": str(Path(clazy).parent / "clazy"),
        "{clazy}": clazy,
        "{clang}": os.environ["ICI_TEST_CLANG"],
        "{lib}": os.environ["ICI_TEST_CLANG_LIBRARY_PATH"],
        "{log}": str(log),
        "{bin}": str(bin_dir),
    }


def substitute(text: str, values: dict) -> str:
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def invocations(log: Path, project: Path, bin_dir: Path) -> list:
    if not log.exists():
        return []
    calls, current = [], []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line == "--END--":
            # The complexity engines' clang-tidy function-boundary probe shares the tool;
            # only the lint invocations are kept.
            if current != ["--version"] and BOUNDARY not in current:
                calls.append(current)
            current = []
        else:
            current.append(line.replace(str(project), "{root}").replace(str(bin_dir), "{bin}"))
    return calls


def run(case: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        bin_dir = WORK / "bin"
        bin_dir.mkdir()
        log = WORK / "argv.log"
        values = placeholders(bin_dir, log)
        for name, text in case["tools"].items():
            (bin_dir / name).write_text(substitute(text, values), encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        if case.get("gxx"):
            (bin_dir / "g++").write_text(case["gxx"], encoding="utf-8")
            (bin_dir / "g++").chmod(0o755)
        shutil.copytree(FIXTURES / case["fixture"], project)
        (project / "ici.toml").write_text(BASE + case["config"], encoding="utf-8")
        for name, text in case["outside"].items():
            (WORK / name).write_text(text, encoding="utf-8")
        for name, text in case["files"].items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8")
        for name, size in case["generated"].items():
            (project / name).write_text("#" * size, encoding="utf-8")
        if case["generated_sources"]:
            (project / "src/gen").mkdir()
        for index in range(case["generated_sources"]):
            (project / f"src/gen/s{index}.cpp").write_text(f"int s{index}();\n", encoding="utf-8")
        for name, mode in case["modes"].items():
            (project / name).chmod(mode)
        for name, target in case["links"].items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, project / name)
        rows = json.dumps(case["rows"]).replace("{root}", str(project))
        (project / "build").mkdir(exist_ok=True)
        (project / "build/compile_commands.json").write_text(rows, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-c", LAUNCH, json.dumps(case["patches"]), "verify", "--report"],
            cwd=project,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        report_path = project / "verify_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        calls = invocations(log, project, bin_dir)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    if not report:
        # Refused before any engine ran (frozen configuration validation): exit code and output.
        output = (result.stdout + result.stderr).replace(str(project), "{root}")
        return {"exit_code": result.returncode, "output": output}
    engine = next(item for item in report["results"] if item.get("engine_name") == "lint")
    captured = {
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in engine["targets"]],
        "findings": engine["findings"],
        # The frozen LintEngine `extra` counters and its process `tool_evidence`.
        "extra": engine.get("extra", {}),
        "tool_evidence": engine.get("tool_evidence", []),
    }
    if any(">> {log}" in text for text in case["tools"].values()):
        captured["invocations"] = calls
    text = json.dumps(captured).replace(str(project), "{root}").replace(str(bin_dir), "{bin}")
    return json.loads(text)


GOLDEN = ROOT / "tests/rust-parity/cpp/lint-tool-branches-oracle.json"
# Optional case names recapture only those cases into the existing golden file.
ONLY = set(sys.argv[1:])
cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"] if ONLY else {}
for name, fields in CASES.items():
    if ONLY and name not in ONLY:
        continue
    case = {
        "fixture": "lint-tidy",
        "rows": DEFAULT,
        "tools": TOOLS,
        "config": "",
        "files": {},
        "outside": {},
        "generated": {},
        "links": {},
        "patches": {},
        "gxx": "",
        "modes": {},
        "generated_sources": 0,
        **fields,
    }
    case["frozen"] = run(case)
    cases[name] = case
    print(name, case["frozen"].get("status"), case["frozen"].get("summary", "")[:120])
VERSIONS = {
    name: subprocess.run(
        [os.environ[variable], "--version"],
        env={"LD_LIBRARY_PATH": os.environ["ICI_TEST_CLANG_LIBRARY_PATH"]},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    for name, variable in (
        ("clang-tidy", "ICI_TEST_CLANG_TIDY"),
        ("clazy", "ICI_TEST_CLAZY"),
        ("clang", "ICI_TEST_CLANG"),
    )
}
GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "gxx": GXX, "base": BASE, "cases": cases, "tool_versions": VERSIONS},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} clang tool branch cases with {GXX}")
