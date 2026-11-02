"""Capture frozen v0.13.0 `ici verify --report` engine `extra` objects; never product code.

    python -I capture_legacy_extras.py <oracle-root> <output.json>

Runs the frozen stable verify (empty HOME, PATH=/usr/bin:/bin, so g++ is the only C++ tool) on
the C++ legacy-scope fixture (`parity-fixtures/cpp/legacy-scope-fixture`) with the default and
the deep profile, on the same fixture with a complete compilation database (exact C++ unused
function scope), on the hybrid Python/C++ inventory tree, and on a Python project whose test
interpreter has pytest but no coverage module (`/usr/bin/python3.14`), and records the
saved v3 suite (the public `verify_report.json`), with the project root as `<root>`.
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


def fixture_files(overrides):
    files = {
        path.relative_to(FIXTURE).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(FIXTURE.rglob("*"))
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


def run_case(oracle, tree):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in tree.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"}
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "verify", "--report"],
            cwd=base,
            capture_output=True,
            text=True,
            env=environment,
            timeout=900,
            check=False,
        )
        text = (base / "verify_report.json").read_text(encoding="utf-8")
        suite = json.loads(text.replace(str(base), "<root>"))
    return {"tree": tree, "exit_code": completed.returncode, "suite": suite}


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
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        print(name, case["exit_code"], case["suite"]["suite_status"], len(case["suite"]["results"]))


if __name__ == "__main__":
    main()
