"""Development-only capture: frozen `ici next plan` human-readable text; never product code.

    python -I capture_plan_text.py

Each case runs the immutable v0.13.0 CLI (`next plan`, text mode) in a fresh copy of a small
next workspace (empty HOME, PATH=/usr/bin:/bin) and records the exit code, stdout and stderr
with the workspace root as `<root>`. Cases: a Python-only component, a Python + C++ workspace
without a build (blocked C++ checks), a C++ component linked to a declared CMake build (build
line, mutating preparation, shared execution), and the `--python`, `--component`, `--profile`
selections.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
OUT = ROOT / "tests/rust-parity/oracle/plan-text-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

HEAD = 'schema_version = 1\n[workspace]\nname = "demo"\nprofile = "standard"\n'
APP = '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
LIB = '[[components]]\nid = "lib"\nroot = "lib"\nlanguages = ["cpp"]\n'
BUILD = (
    '[builds.native]\nsystem = "cmake"\ndirectory = "out"\nproject = "CMakeLists.txt"\n'
    'variant = "release"\nprepare = "explicit"\n'
)
SOURCES = {
    "app/main.py": "def f():\n    return 1\n",
    "app/tests/test_main.py": "from main import f\n\n\ndef test_f():\n    assert f() == 1\n",
    "lib/a.cpp": "int f() { return 1; }\n",
}
CMAKE = "cmake_minimum_required(VERSION 3.16)\nproject(demo CXX)\nadd_library(a lib/a.cpp)\n"
TREES = {
    "python": {"ici.toml": HEAD + APP, **{k: v for k, v in SOURCES.items() if k.startswith("app")}},
    "python-env": {
        "ici.toml": HEAD + APP + f"[components.python]\nexecutable = {json.dumps(PYTHON)}\n",
        **{k: v for k, v in SOURCES.items() if k.startswith("app")},
    },
    "mixed": {"ici.toml": HEAD + APP + LIB, **SOURCES},
    "build": {
        "ici.toml": HEAD + BUILD + LIB + 'build = "native"\n',
        "CMakeLists.txt": CMAKE,
        "lib/a.cpp": SOURCES["lib/a.cpp"],
    },
}
CASES = {
    "python/default": ("python", []),
    "python/profile-fast": ("python", ["--profile", "fast"]),
    "python/profile-deep": ("python", ["--profile", "deep"]),
    "python-env/default": ("python-env", []),
    "python-env/profile-fast": ("python-env", ["--profile", "fast"]),
    "mixed/default": ("mixed", []),
    "mixed/python": ("mixed", ["--python"]),
    "mixed/component-lib": ("mixed", ["--component", "lib"]),
    "build/default": ("build", []),
}


def main() -> None:
    cases = {}
    for name, (tree, extra) in CASES.items():
        with tempfile.TemporaryDirectory(prefix="ici-plan-text-") as temp:
            base = Path(temp)
            project, home = base / "project", base / "home"
            home.mkdir()
            for relative, text in TREES[tree].items():
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [PYTHON, "-m", "ici", "next", "plan", *extra],
                cwd=project,
                env={
                    "HOME": str(home),
                    # Git discovery stops above the project, as in the native replay.
                    "GIT_CEILING_DIRECTORIES": str(base),
                    "PATH": "/usr/bin:/bin",
                    "PYTHONPATH": str(ORACLE / "src"),
                    "LANG": "C.UTF-8",
                },
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            prefix = str(project)
            cases[name] = {
                "tree": tree,
                "argv": ["next", "plan", *extra],
                "exit_code": completed.returncode,
                "stdout": completed.stdout.replace(prefix, "<root>"),
                "stderr": completed.stderr.replace(prefix, "<root>"),
            }
    payload = {
        "oracle": EXPECTED,
        "python": sys.version.split()[0],
        "trees": TREES,
        "cases": cases,
    }
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for name, case in cases.items():
        print(f"== {name} exit {case['exit_code']}\n{case['stdout']}{case['stderr']}")


if __name__ == "__main__":
    main()
