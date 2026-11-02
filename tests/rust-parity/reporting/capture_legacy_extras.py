"""Capture frozen v0.13.0 `ici verify --report` engine `extra` objects; never product code.

    python -I capture_legacy_extras.py <oracle-root> <output.json>

Runs the frozen stable verify (empty HOME, PATH=/usr/bin:/bin, so g++ is the only C++ tool) on
the C++ legacy-scope fixture (`parity-fixtures/cpp/legacy-scope-fixture`) with the default and
the deep profile, on the same fixture with a complete compilation database (exact C++ unused
function scope), on the hybrid Python/C++ inventory tree, and on a Python project whose test
interpreter has pytest but no coverage module (`/usr/bin/python3.14`), and records the
saved v3 suite (the public `verify_report.json`), with the project root as `<root>`.

Engine-command cases record the saved `<engine>_report.json` (`report`) instead: the lint
fallback whose project-contained `bin/g++` wrapper (`wrapper`, delegating to /usr/bin/g++) is
first on PATH, so frozen rejects every source replay (`parity-fixtures/cpp/lint-fallback-replay`,
`ici lint` and lint-only `ici verify`), and standalone `ici cycle` on the include-context
`mixed` tree (frozen keeps the heuristic graph: no analysis context is built).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "parity-fixtures/cpp/legacy-scope-fixture"
INVENTORY = ROOT / "tests/rust-parity/python/legacy-inventory-oracle.json"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
LINT_FALLBACK = ROOT / "parity-fixtures/cpp/lint-fallback-replay"
MIXED = ROOT / "parity-fixtures/cpp/include-context/mixed"
WRAPPER = '#!/bin/sh\nexec /usr/bin/g++ "$@"\n'
LINT_ONLY = (
    '[project]\ntype = "cpp"\nsource_dirs = ["src"]\n'
    + "".join(
        f"[engines.{name}]\nenabled = false\n"
        for name in [
            "line",
            "compile_db",
            "test",
            "type",
            "python_compat",
            "complexity",
            "sanitize",
            "thread_sanitize",
            "dead",
            "dup",
            "exception",
            "cognitive",
            "security",
            "resource",
            "build",
            "binary_compat",
            "integration",
            "cycle",
        ]
    )
    + '[engines.lint]\nenabled = true\nclang_tidy = "off"\nclazy = "off"\n'
)
# Host interpreter with pytest and without coverage (the Rust replay substitutes
# ICI_TEST_PYTHON_NEW, the same interpreter).
NO_COVERAGE_PYTHON = "/usr/bin/python3.14"
UNITS = ("src/extra.cc", "src/main.cpp", "src/util.cpp")
DATABASE = (
    "["
    + ", ".join(
        f'{{"directory": ".", "file": "{unit}", '
        f'"arguments": ["g++", "-std=c++17", "-c", "{unit}"]}}'
        for unit in UNITS
    )
    + "]\n"
)


def fixture_files(overrides, fixture=FIXTURE):
    files = {
        path.relative_to(fixture).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(fixture.rglob("*"))
        if path.is_file()
    }
    files.update(overrides)
    return files


def cases():
    with INVENTORY.open(encoding="utf-8") as stream:
        hybrid = json.load(stream)["hybrid_tree"]
    clean = (FIXTURE / "src/extra.cc").read_text(encoding="utf-8").replace("~Guard()", "~CcGuard()")
    return {
        "cpp-defaults": fixture_files({"ici.toml": BASE}),
        "cpp-deep": fixture_files({"ici.toml": BASE + "[ici]\nprofile='deep'\n"}),
        "cpp-database": fixture_files(
            {"ici.toml": BASE, "compile_commands.json": DATABASE, "src/extra.cc": clean}
        ),
        "hybrid": dict(hybrid),
        # The project test interpreter has pytest but no coverage module (optional coverage).
        "python-optional-coverage": {
            "ici.toml": f'[engines.test]\npython = "{NO_COVERAGE_PYTHON}"\n',
            "src/__init__.py": '"""Package."""\n',
            "src/core.py": "def f(x):\n    return x + 1\n",
            "tests/test_core.py": (
                "from src.core import f\n\n\ndef test_f():\n    assert f(1) == 2\n"
            ),
        },
    }


def command_cases():
    lint = fixture_files({"ici.toml": LINT_ONLY}, LINT_FALLBACK)
    return {
        "lint-fallback-verify": (lint, ["verify", "--report"], True),
        "lint-fallback-lint": (lint, ["lint", "--report"], True),
        "cycle-standalone": (fixture_files({}, MIXED), ["cycle", "--report"], False),
    }


def run_case(oracle, tree, argv=("verify", "--report"), wrapper=False):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in tree.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"}
        if wrapper:
            (base / "bin").mkdir()
            (base / "bin/g++").write_text(WRAPPER, encoding="utf-8")
            (base / "bin/g++").chmod(0o755)
            environment["PATH"] = f"{base / 'bin'}:/usr/bin:/bin"
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
            cwd=base,
            capture_output=True,
            text=True,
            env=environment,
            timeout=900,
            check=False,
        )
        text = (base / f"{argv[0]}_report.json").read_text(encoding="utf-8")
        saved = json.loads(text.replace(str(base), "<root>"))
    if argv[0] == "verify" and not wrapper:
        return {"tree": tree, "exit_code": completed.returncode, "suite": saved}
    return {
        "tree": tree,
        "argv": list(argv),
        "wrapper": wrapper,
        "exit_code": completed.returncode,
        "suite" if argv[0] == "verify" else "report": saved,
    }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    gxx = subprocess.run(
        ["/usr/bin/g++", "--version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "gxx": gxx,
        "cases": {name: run_case(oracle, tree) for name, tree in cases().items()},
    }
    for name, (tree, argv, wrapper) in command_cases().items():
        document["cases"][name] = run_case(oracle, tree, argv, wrapper)
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        saved = case.get("suite", case.get("report"))
        print(name, case["exit_code"], saved.get("suite_status", saved.get("status")))


if __name__ == "__main__":
    main()
