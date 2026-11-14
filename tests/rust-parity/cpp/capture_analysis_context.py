"""Development-only frozen `ici verify` top-level `analysis_context` captures; never called by Rust.

Runs the frozen CLI over a C++ project (parity-fixtures/cpp/test-cmake/plain), a Python project
(the two Python files below), a hybrid project (both) and a CMake project that enables unity
builds through its cache (parity-fixtures/cpp/unity-cache) in a git checkout and keeps the
`ici.analysis-context/v1` object of `verify_report.json`. The project root is replaced by `<ROOT>`.

cpp31 (root ruling 2026-11-13) adds `cpp-tagged` (the CMake project with a lightweight tag on its
commit: `analysis_context.project.version` is frozen's `git describe --tags --always` value) and
`cpp-pkg-fallback` (a project with no build descriptor whose lint runs the c++17 fallback, with
`project.cpp_pkg_config = ['demo', 'absent-pkg', 'second']` over the project's own `pc/` files:
frozen appends each zero-exit package's flags, in order, after the sorted include directories).
`capture_analysis_context.py <name>...` recaptures only those cases and keeps the others.

cpp32 (root ruling 2026-11-14) adds the same packages on database runs: `cpp-pkg-cmake` (the CMake
project, whose release analysis database ici configures) and `cpp-pkg-qmake`
(parity-fixtures/cpp/test-qmake with `source_dirs=['tests']`, whose qmake compiler capture writes
the analysis database). Frozen resolves `project.cpp_pkg_config` while it discovers the project
model, so both carry the package flags after the include directories like the fallback case.
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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-analysis-context"
CPP = "test-cmake/plain"
# name: (C++ fixture or None, project type)
CASES = {
    "cpp": (CPP, "cpp"),
    "python": (None, "python"),
    "hybrid": (CPP, "hybrid"),
    "cpp-unity": ("unity-cache", "cpp"),
    "cpp-deep": (CPP, "cpp"),
    "cpp-tagged": (CPP, "cpp"),
    "cpp-pkg-fallback": (None, "cpp"),
    "cpp-pkg-cmake": (CPP, "cpp"),
    "cpp-pkg-qmake": ("test-qmake", "cpp"),
}
# Configured source directories per case (default `['src']`).
CASE_SOURCE_DIRS = {"cpp-pkg-qmake": ["tests"]}
# A lightweight tag on the fixture commit, per case.
CASE_TAGS = {"cpp-tagged": "v2.4.1"}
# Files a case writes beside its fixture; `<ROOT>` in CASE_ENV is the project root.
CASE_FILES = {
    "cpp-pkg-fallback": {
        "src/main.cpp": '#include <cstdio>\nint main() { std::puts("demo"); return 0; }\n',
        "include/demo/api.h": "#pragma once\n",
        "vendor/include/vendor.h": "#pragma once\n",
        "pc/demo.pc": (
            "Name: demo\nDescription: demo\nVersion: 1.0\n"
            "Cflags: -I${pcfiledir}/../vendor/include -DDEMO_PKG=1\n"
        ),
        "pc/second.pc": "Name: second\nDescription: second\nVersion: 2.0\nCflags: -DSECOND_PKG\n",
    }
}
# The database runs keep only the package files (the fixtures bring their own sources).
PACKAGE_FILES = {
    relative: text
    for relative, text in CASE_FILES["cpp-pkg-fallback"].items()
    if relative.startswith(("pc/", "vendor/"))
}
CASE_FILES["cpp-pkg-cmake"] = PACKAGE_FILES
CASE_FILES["cpp-pkg-qmake"] = PACKAGE_FILES
PACKAGE_ENV = {"PKG_CONFIG_PATH": "<ROOT>/pc"}
CASE_ENV = {name: PACKAGE_ENV for name in ("cpp-pkg-fallback", "cpp-pkg-cmake", "cpp-pkg-qmake")}
# Extra legacy configuration per case: the deep profile with the build engine requests the
# release variant next to coverage, sanitize and thread-sanitize.
CASE_CONFIGS = {
    "cpp-deep": "[ici]\nprofile='deep'\n[engines.build]\nenabled=true\n",
    "cpp-pkg-fallback": "cpp_pkg_config=['demo', 'absent-pkg', 'second']\n",
}
CASE_CONFIGS["cpp-pkg-cmake"] = CASE_CONFIGS["cpp-pkg-fallback"]
CASE_CONFIGS["cpp-pkg-qmake"] = CASE_CONFIGS["cpp-pkg-fallback"]
PYTHON_FILES = {
    "src/app.py": "def add(a, b):\n    return a + b\n",
    "tests/test_app.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
CMAKE = subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0]


def run(name: str, fixture: str | None, kind: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        project.mkdir()
        if fixture is not None:
            shutil.copytree(ROOT / "parity-fixtures/cpp" / fixture, project, dirs_exist_ok=True)
        if kind != "cpp":
            for relative, text in PYTHON_FILES.items():
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_text(text, encoding="utf-8")
        for relative, text in CASE_FILES.get(name, {}).items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        source_dirs = json.dumps(CASE_SOURCE_DIRS.get(name, ["src"]))
        config = f"[project]\ntype='{kind}'\nsource_dirs={source_dirs}\n" + CASE_CONFIGS.get(
            name, ""
        )
        (project / "ici.toml").write_text(config, encoding="utf-8")
        git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run([*git, "-C", str(project), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(project), "commit", "-qm", "fixture"], check=True)
        if name in CASE_TAGS:
            subprocess.run([*git, "-C", str(project), "tag", CASE_TAGS[name]], check=True)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")}
        for key, value in CASE_ENV.get(name, {}).items():
            env[key] = value.replace("<ROOT>", str(project))
        subprocess.run(
            [PYTHON, "-B", "-m", "ici", "verify", "--report"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        text = (project / "verify_report.json").read_text(encoding="utf-8")
        return json.loads(text.replace(str(project), "<ROOT>"))["analysis_context"]
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


OUTPUT = ROOT / "tests/rust-parity/cpp/analysis-context-oracle.json"
only = sys.argv[1:]
cases = json.loads(OUTPUT.read_text(encoding="utf-8"))["cases"] if only else {}
for name, (fixture, kind) in CASES.items():
    if not only or name in only:
        cases[name] = run(name, fixture, kind)
cases = {name: cases[name] for name in CASES}
OUTPUT.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "cmake": CMAKE,
            "cpp_fixture": CPP,
            "case_projects": {name: list(value) for name, value in CASES.items()},
            "case_configs": CASE_CONFIGS,
            "case_tags": CASE_TAGS,
            "case_files": CASE_FILES,
            "case_env": CASE_ENV,
            "case_source_dirs": CASE_SOURCE_DIRS,
            "python_files": PYTHON_FILES,
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} analysis contexts with {CMAKE}")
