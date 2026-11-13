"""Development-only frozen `ici build` captures for the BuildEngine error literals.

    python -I -B capture_build_literals.py

Never called by Rust. Same driver as capture_build_targets.py (the frozen CLI's own
`_print_engine_result`, wrapped to record the rendered engine result), plus the engine `extra`,
the tool evidence names and argv[0], and the release tree a run leaves. The cases are the error
branches of oracle engines/build.py (sha256 ed680842…) that build-targets-oracle.json does not
reach: unreadable C++ sources (:646), output-tree guards (:87, :274), environment-loader write
refusals (:107, :686), metadata errors before the target path exists (:142), the descriptor
selection edges (dot-names, symlinks, a passed-over .pro: :628, :745) and the Python packaging
error branches (:338, :373).

A change maps a project-relative path to text, None (delete), {"symlink": target},
{"hex": bytes} or {"directory": true}; {"mode": octal} is applied after the writes.
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
FIXTURES = ROOT / "parity-fixtures/cpp"
TOOLS = sorted({str(Path(shutil.which(name)).parent) for name in ("make", "g++", "cmake", "qmake")})
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
DRIVER = """
import json, sys
import ici.__main__ as cli
rendered = cli._print_engine_result
OUT = sys.argv[1]
def record(result):
    rendered(result)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump({"status": result.status.value, "summary": result.summary,
                   "evidence": result.evidence.value,
                   "extra": {key: result.extra.get(key) for key in
                             ("project_name", "project_version", "target_path",
                              "metrics_summary")},
                   "tools": [[item.name, (item.argv or [""])[0].rsplit("/", 1)[-1]]
                             for item in result.tool_evidence],
                   "targets": [{"target_name": t.target_name, "status": t.status.value,
                                "message": t.message, "start_line": t.start_line,
                                "file_path": t.file_path} for t in result.targets]}, handle)
cli._print_engine_result = record
sys.argv = ["ici", "build"]
cli.main()
"""
PY_TOML = "[project]\ntype='python'\nsource_dirs=['src']\n"
PY_TWO_ROOTS = "[project]\ntype='python'\nsource_dirs=['src', 'lib']\n"
PY_SOURCES = {
    "src/main.cpp": None,
    "src/pkg/__init__.py": "",
    "src/pkg/cli.py": "def main():\n    return 0\n",
}


def scripts(*entries):
    lines = "".join(f"{name!r}={target!r}\n" for name, target in entries)
    return f"[project]\nname='pkg'\nversion='2.3.4'\n[project.scripts]\n{lines}"


QMAKE_PROJECT = (
    "TEMPLATE = app\nCONFIG += console c++17\nCONFIG -= qt\nTARGET = suite\n"
    "SOURCES += src/main.cpp\n"
)
HELPER = "int helper() { return 1; }\n"
CASES = {
    # :646 `_count_cpp_main_definitions`: every unreadable source is its own BuildError at its
    # relative path (absolute path in the message); the main count is never reported.
    "inspect-invalid-start": ("package-direct", {"src/bad.cpp": {"hex": "ff696e7420783b0a"}}),
    "inspect-truncated": (
        "package-direct",
        {"src/bad.cpp": {"hex": "696e7420783b202f2f20e282"}},
    ),
    "inspect-continuation": (
        "package-direct",
        {"src/bad.cpp": {"hex": "696e7420783b202f2f20e228a1206d6f72650a"}},
    ),
    "inspect-overlong": ("package-direct", {"src/bad.cpp": {"hex": "2f2f20c0af0a"}}),
    "inspect-two": (
        "package-direct",
        {
            "src/a.cpp": {"hex": "2f2f20ff0a"},
            "src/z.cpp": {"hex": "2f2f20f0908080ff0a"},
        },
    ),
    "inspect-four": (
        "package-direct",
        {
            "src/a.cpp": {"hex": "ff0a"},
            "src/b.cpp": {"hex": "ff0a"},
            "src/c.cpp": {"hex": "ff0a"},
            "src/d.cpp": {"hex": "ff0a"},
        },
    ),
    "inspect-unreadable": (
        "package-direct",
        {"src/locked.cpp": HELPER, "<mode>": {"src/locked.cpp": 0}},
    ),
    # Negative: frozen reads a NUL as text, counts the one main and compiles.
    "inspect-nul": (
        "package-direct",
        {"src/main.cpp": {"hex": "2f2f2000206e756c0a696e74206d61696e28297b72657475726e20303b7d0a"}},
    ),
    # :87/:274 `_prepare_output_tree`, before any build step.
    "version-file": ("package-direct", {"v2.3.4": "not a directory\n"}),
    "version-symlink": (
        "package-direct",
        {"elsewhere/.keep": "", "v2.3.4": {"symlink": "elsewhere"}},
    ),
    "bin-file": ("package-direct", {"v2.3.4/x86_64/bin": "not a directory\n"}),
    "lib-symlink": (
        "package-direct",
        {"elsewhere/.keep": "", "v2.3.4/x86_64/lib": {"symlink": "../../elsewhere"}},
    ),
    # :107/:686 `_generate_env_scripts`: checked before either loader is written.
    "env-sh-directory": ("package-direct", {"v2.3.4/x86_64/env.sh/.keep": ""}),
    "env-csh-symlink": (
        "package-direct",
        {"elsewhere.txt": "x\n", "v2.3.4/x86_64/env.csh": {"symlink": "../../elsewhere.txt"}},
    ),
    "env-make-directory": ("package-make", {"v2.3.4/x86_64/env.sh/.keep": ""}),
    # :142 `metrics_summary` when no target path was prepared (metadata error first).
    "invalid-version": (
        "package-direct",
        {"pyproject.toml": "[project]\nname='pkg'\nversion='2.3 4'\n"},
    ),
    "invalid-name": (
        "package-direct",
        {"pyproject.toml": "[project]\nname='p/kg'\nversion='2.3.4'\n"},
    ),
    # :628 `_has_build_descriptor` / backend `_is_real_file`: pathlib globs match dot-names; a
    # symlinked descriptor is no descriptor, so the generic g++ build runs.
    "dot-mk": ("package-direct", {".x.mk": "all:\n\ttrue\n"}),
    "dot-make": ("package-direct", {".make": "all:\n\ttrue\n"}),
    "makefile-dot": ("package-direct", {"Makefile.": "all:\n\ttrue\n"}),
    "symlinked-mk": (
        "package-direct",
        {"rules/build.mk": "all:\n\ttrue\n", "build.mk": {"symlink": "rules/build.mk"}},
    ),
    "symlinked-makefile": (
        "package-direct",
        {"rules/Makefile": "all:\n\ttrue\n", "Makefile": {"symlink": "rules/Makefile"}},
    ),
    "descriptor-directory": ("package-direct", {"x.pro/.keep": ""}),
    # A root file named only `.pro` matches `*.pro`: frozen selects the qmake backend for it.
    "dot-pro": ("package-cmake", {"CMakeLists.txt": None, ".pro": QMAKE_PROJECT}),
    # :745 CMake selected with a .pro passed over.
    "cmake-passed-over-pro": ("package-cmake", {"pkg.pro": QMAKE_PROJECT}),
    # :338/:373 `_package_python`: each error is recorded and packaging continues.
    "python-symlink-source": (
        "package-direct",
        {"ici.toml": PY_TOML, **PY_SOURCES, "src/pkg/link.py": {"symlink": "cli.py"}},
    ),
    "python-collision": (
        "package-direct",
        {"ici.toml": PY_TWO_ROOTS, **PY_SOURCES, "lib/pkg/cli.py": "VALUE = 1\n"},
    ),
    "python-invalid-entrypoint": (
        "package-direct",
        {"ici.toml": PY_TOML, **PY_SOURCES, "pyproject.toml": scripts(("tool", "pkg.cli"))},
    ),
    "python-unsafe-name": (
        "package-direct",
        {"ici.toml": PY_TOML, **PY_SOURCES, "pyproject.toml": scripts(("-tool", "pkg.cli:main"))},
    ),
    "python-missing-module": (
        "package-direct",
        {
            "ici.toml": PY_TOML,
            **PY_SOURCES,
            "pyproject.toml": scripts(("tool", "pkg.absent:main")),
        },
    ),
    "python-two-errors": (
        "package-direct",
        {
            "ici.toml": PY_TOML,
            **PY_SOURCES,
            "pyproject.toml": scripts(
                ("bad", "pkg.cli"), ("good", "pkg.cli:main"), ("lost", "pkg.absent:main")
            ),
        },
    ),
    "python-empty-entrypoint": (
        "package-direct",
        {"ici.toml": PY_TOML + "[build.python]\nentrypoint=' '\n", **PY_SOURCES},
    ),
    "python-launcher-directory": (
        "package-direct",
        {
            "ici.toml": PY_TOML,
            **PY_SOURCES,
            "pyproject.toml": scripts(("tool", "pkg.cli:main")),
            "v2.3.4/x86_64/bin/tool/.keep": "",
        },
    ),
}
# Directories a run owns that are not the project's outcome.
PRIVATE = {".ici", "__pycache__", ".git"}


def listing(base):
    names = []
    for directory, children, files in os.walk(base):
        children[:] = sorted(name for name in children if name not in PRIVATE)
        relative = Path(directory).relative_to(base)
        names += [str(relative / name) + "/" for name in children]
        names += [str(relative / name) for name in sorted(files)]
    return sorted(name.removeprefix("./") for name in names)


def apply(project, changes):
    for relative, value in changes.items():
        if relative.startswith("<"):
            continue
        path = project / relative
        if value is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, dict) and "symlink" in value:
            path.symlink_to(value["symlink"])
        elif isinstance(value, dict) and "hex" in value:
            path.write_bytes(bytes.fromhex(value["hex"]))
        else:
            path.write_text(value, encoding="utf-8")
    for relative, mode in changes.get("<mode>", {}).items():
        (project / relative).chmod(mode)


def run(fixture, changes):
    work = Path(tempfile.mkdtemp(prefix="ici-build-literals-")).resolve()
    try:
        project = work / "project"
        shutil.copytree(FIXTURES / fixture, project)
        apply(project, changes)
        before_run = listing(project)
        record = work / "result.json"
        completed = subprocess.run(
            [PYTHON, "-B", "-c", DRIVER, str(record)],
            cwd=project,
            env={
                "PATH": os.pathsep.join([*TOOLS, "/usr/bin", "/bin"]),
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": str(work),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        engine = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
        after = listing(project)
        for relative in changes.get("<mode>", {}):
            (project / relative).chmod(0o644)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    created = sorted(set(after) - set(before_run))
    # The adapter shadow is the approved native location difference; it is kept for reading.
    result = {
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "created": created,
        "engine": engine,
    }
    text = json.dumps(result, ensure_ascii=False)
    text = text.replace(str(work), "<work>")
    result = json.loads(text)
    if engine is not None:
        for target in result["engine"]["targets"]:
            target["message"] = re.sub(r"\(\d+\.\d\ds\)", "(<seconds>s)", target["message"])
    return result


def main():
    cases = {}
    for name, (fixture, changes) in CASES.items():
        cases[name] = {"fixture": fixture, "changes": changes, "frozen": run(fixture, changes)}
        print(name, json.dumps(cases[name]["frozen"], ensure_ascii=False)[:600])
    (ROOT / "tests/rust-parity/cpp/build-literals-oracle.json").write_text(
        json.dumps(
            {"oracle": EXPECTED, "cases": cases}, indent=1, sort_keys=True, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} build literal cases")


if __name__ == "__main__":
    main()
