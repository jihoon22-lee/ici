"""Development-only frozen `ici next` build-reach captures; never called by Rust.

Frozen `workspace/compile_units.py` names who produced the selected compilation database
(`_database_path`: the linked build's directory, labelled "<system> build '<id>'[ <variant>]",
then the conventional component root, component build/, workspace root and workspace build/
locations) and reads what a linked build's project file reaches (`_build_targets`: qmake
SUBDIRS and cmake add_subdirectory trees, `<system>-target-missing` when the component root is
not reached, `build-definition-missing` for a make/explicit definition that is not on disk, and
the readers' own warnings), and flags a recorded build-generated input under the linked build
directory that is not on disk (`generated-input-missing`). Each case is one workspace with one
C++ component `app` (root `src`); the capture keeps `ici next doctor --json` compile inputs, `ici next plan --json`
limitations and coverage gaps, and `ici next verify --json` (with and without --require-full)
cpp.compile check record, limitations and gate, with the project root written as `{root}`.
cpp.tidy and cpp.diagnostics are disabled so no runner tool changes the run.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-next-build-reach"
GOLDEN = ROOT / "tests/rust-parity/cpp/next-build-reach-oracle.json"
HEAD = 'schema_version = 1\n[workspace]\nname = "probe"\nprofile = "standard"\n'
TAIL = (
    '[[components]]\nid = "app"\nroot = "src"\nlanguages = ["cpp"]\n{link}'
    '[checks."cpp.tidy"]\nenabled = false\n[checks."cpp.diagnostics"]\nenabled = false\n'
)
SOURCES = {"src/a.cpp": "int a() { return 1; }\n", "src/b.cpp": "int main() { return 0; }\n"}
CMAKE_REACHED = {
    "CMakeLists.txt": (
        "cmake_minimum_required(VERSION 3.16)\nproject(p CXX)\n"
        "#[[ add_subdirectory(commented) ]]\n# add_subdirectory(also_commented)\n"
        "add_subdirectory(src)\nadd_library(core STATIC core.cpp)\n"
        "add_dependencies(app core)\n"
    ),
    "src/CMakeLists.txt": "add_executable(app a.cpp b.cpp)\nadd_test(NAME app_test COMMAND app)\n",
}
CMAKE_UNREACHED = {
    "CMakeLists.txt": (
        "cmake_minimum_required(VERSION 3.16)\nproject(p CXX)\ninclude(Helpers)\n"
        "add_subdirectory(${EXTRA})\nadd_subdirectory(nowhere)\nadd_subdirectory(other)\n"
        "if(WIN32)\n  add_executable(${NAME} w.cpp)\nendif()\n"
    ),
    "other/CMakeLists.txt": "add_library(other STATIC o.cpp)\nadd_test(${T} other)\n",
}
QMAKE_UNREACHED = {
    "root.pro": (
        "TEMPLATE = subdirs\nSUBDIRS = lib \\\n    empty two $$EXTRA tool.pro gone\n"
        "unix:SUBDIRS += moved\nSUBDIRS -= gone\nmoved.subdir = elsewhere\n"
        "lib.depends = moved\n# SUBDIRS += src\n"
    ),
    "lib/lib.pro": "TEMPLATE = lib\nSOURCES += l.cpp\n",
    "empty/readme.txt": "no project here\n",
    "two/a.pro": "TEMPLATE = app\n",
    "two/b.pro": "TEMPLATE = app\n",
    "elsewhere/only.pro": "TEMPLATE = lib\n",
}
QMAKE_REACHED = {
    "root.pro": "TEMPLATE = subdirs\nSUBDIRS = src core\nsrc.depends = core\ncore.file = lib/core.pro\n",
    "src/src.pro": "TEMPLATE = app\nSOURCES += a.cpp b.cpp\n",
    "lib/core.pro": "TEMPLATE = lib\n",
}


def build(system: str, project: str | None, variant: str | None) -> str:
    text = f'[builds.native]\nsystem = "{system}"\ndirectory = "out"\n'
    if project is not None:
        text += f'project = "{project}"\n'
    if variant is not None:
        text += f'variant = "{variant}"\n'
    return text + 'prepare = "explicit"\n'


LINK = 'build = "native"\n'
# name: (builds block, component link, project files, database locations, database sources)
CASES = {
    "cmake-reached": (
        build("cmake", "CMakeLists.txt", "release"),
        LINK,
        CMAKE_REACHED,
        ["out/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "cmake-unreached-partial": (
        build("cmake", "CMakeLists.txt", None),
        LINK,
        CMAKE_UNREACHED,
        ["out/compile_commands.json"],
        ["src/a.cpp"],
    ),
    "cmake-unreached-no-database": (
        build("cmake", "CMakeLists.txt", None),
        LINK,
        CMAKE_UNREACHED,
        [],
        [],
    ),
    "qmake-unreached": (
        build("qmake", "root.pro", "debug"),
        LINK,
        QMAKE_UNREACHED,
        ["out/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "qmake-reached": (
        build("qmake", "root.pro", None),
        LINK,
        QMAKE_REACHED,
        ["out/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "make-definition-missing": (
        build("make", "Makefile", None),
        LINK,
        {},
        ["src/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "explicit-definition-present": (
        build("explicit", "build.sh", None),
        LINK,
        {"build.sh": "#!/bin/sh\n"},
        ["src/build/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "linked-build-over-component-root": (
        build("cmake", None, "release"),
        LINK,
        {},
        ["out/compile_commands.json", "src/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "component-build-over-workspace-root": (
        "",
        "",
        {},
        ["src/build/compile_commands.json", "compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "workspace-root-over-workspace-build": (
        "",
        "",
        {},
        ["compile_commands.json", "build/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
    "generated-input-missing": (
        build("cmake", "CMakeLists.txt", None),
        LINK,
        {**CMAKE_REACHED, "out/gen/present.cpp": "int present() { return 0; }\n"},
        ["out/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp", "out/gen/present.cpp", "out/gen/gone.cpp"],
    ),
    "workspace-build-only": (
        "",
        "",
        {},
        ["build/compile_commands.json"],
        ["src/a.cpp", "src/b.cpp"],
    ),
}

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def rows(project: Path, sources: list[str]) -> str:
    return json.dumps(
        [
            {
                "directory": str(project),
                "file": str(project / source),
                "arguments": ["/usr/bin/g++", "-std=c++17", "-c", str(project / source)],
            }
            for source in sources
        ]
    )


def invoke(project: Path, environment: dict, *arguments: str) -> tuple[int, dict]:
    result = subprocess.run(
        [PYTHON, "-m", "ici", "next", *arguments, "--json"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return result.returncode, json.loads(result.stdout.replace(str(project), "{root}"))


def run(case: tuple) -> dict:
    builds, link, files, databases, sources = case
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        for relative, text in {**SOURCES, **files}.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        config = HEAD + builds + TAIL.replace("{link}", link)
        (project / "ici.toml").write_text(config, encoding="utf-8")
        for index, location in enumerate(databases):
            (project / location).parent.mkdir(parents=True, exist_ok=True)
            # Only the first (winning) location records the sources; later ones are empty, so
            # a wrong pick shows as different coverage too.
            text = rows(project, sources) if index == 0 else "[]"
            (project / location).write_text(text, encoding="utf-8")
        (WORK / "home").mkdir()
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(WORK / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(ORACLE / "src"),
        }
        doctor_code, doctor = invoke(project, environment, "doctor")
        plan_code, plan = invoke(project, environment, "plan")
        verify_code, verify = invoke(project, environment, "verify")
        full_code, full = invoke(project, environment, "verify", "--require-full")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    compile_check = [
        {key: check.get(key) for key in ("task_id", "state", "evidence_complete")}
        for check in verify["checks"]
        if check["check_id"] == "cpp.compile"
    ]
    return {
        "config": config,
        "files": files,
        "databases": databases,
        "sources": sources,
        "doctor_exit_code": doctor_code,
        "compile_inputs": [component.get("compile_inputs") for component in doctor["components"]],
        "plan_exit_code": plan_code,
        "plan_limitations": plan["limitations"],
        "plan_coverage_gaps": plan["coverage_gaps"],
        "exit_code": verify_code,
        "compile_check": compile_check,
        "limitations": verify["limitations"],
        "gate": verify["gate"],
        "full_exit_code": full_code,
        "full_gate": full["gate"],
    }


GOLDEN.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "cases": {name: run(case) for name, case in CASES.items()},
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(GOLDEN)
