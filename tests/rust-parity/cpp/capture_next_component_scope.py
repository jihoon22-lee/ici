"""Development-only frozen `ici next verify --json` captures of next-v1 component scope keys.

Covers `external` (in-root file/directory members, absolute/absent/escaping entries, other
languages, a second component's root, a python component), `vendor`, `builds.*.prepare`
without a prepared database, conventional/malformed databases, `cpp.qt` and the child-file
`[component]` form. Never called by the Rust product.
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
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WORK = Path(tempfile.gettempdir()) / "ici-cpp-next-scope-capture"
OFF_CPP = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in ("compile", "diagnostics", "tidy", "test", "coverage", "artifact", "binary-compat")
)
OFF_PY = "".join(
    f'[checks."python.{check}"]\nenabled = false\n'
    for check in ("lint", "format", "type", "test", "coverage", "compat-runtime")
)
TEST_ONLY = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in (
        "compile",
        "diagnostics",
        "tidy",
        "coverage",
        "artifact",
        "binary-compat",
        "cycle",
    )
)
HEAD = 'schema_version = 1\n[workspace]\nname = "product"\n'
BUILD = (
    '[builds.native]\nsystem = "cmake"\nproject = "CMakeLists.txt"\ndirectory = "out"\n'
    'variant = "release"\nprepare = "explicit"\n'
)
QT_SOURCE = (
    "#include <QObject>\nclass Widget : public QObject {\n    Q_OBJECT\npublic:\n"
    "    int value() const;\nsignals:\n    void changed();\n};\nint helper() { return 1; }\n"
)
DATABASE = json.dumps(
    [{"directory": "{root}", "file": "src/main.cpp", "arguments": ["g++", "-c", "src/main.cpp"]}]
)


def cpp_component(extra: str) -> str:
    return (
        '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\nbuild = "native"\n'
        'sources = ["src/**"]\n' + extra
    )


# Each case: config text, files (path -> text), links (path -> target), and outside files
# (written next to the project, outside the workspace).
CASES = {
    "external-members": {
        "config": HEAD
        + BUILD
        + cpp_component(
            'exclude = ["ext/**"]\nvendor = ["src/third_party/**"]\n'
            'external = ["ext/shared.hpp", "ext/dir", "/opt/ici-none/include", "missing/x.hpp",'
            ' "link.hpp", "ext/notes.txt", "ext/tool.py"]\n'
        )
        + OFF_CPP,
        "files": {
            "src/main.cpp": '#include "../ext/shared.hpp"\n'
            "int main() { try { return shared(); } catch (...) { return 1; } }\n",
            "src/third_party/v.cpp": "int v() { return 0; }\n",
            "ext/shared.hpp": "inline int shared() { if (1) { throw 1; } return 0; }\n",
            "ext/dir/a.hpp": "inline int a() { return 1; }\n// note\n",
            "ext/dir/sub/b.h": "\nint b();\n",
            "ext/dir/readme.md": "docs\n",
            "ext/notes.txt": "notes\n",
            "ext/tool.py": "def tool():\n    return 1\n",
        },
        "links": {"link.hpp": "../outside.hpp"},
        "outside": {"outside.hpp": "inline int outside() { return 2; }\n"},
    },
    "no-external": {
        "config": HEAD + BUILD + cpp_component('vendor = ["src/third_party/**"]\n') + OFF_CPP,
        "files": {
            "src/main.cpp": "int main() { return 0; }\n",
            "src/third_party/v.cpp": "int v() { return 0; }\n",
            "ext/shared.hpp": "inline int shared() { return 0; }\n",
        },
        "links": {},
        "outside": {},
    },
    "external-second-component": {
        "config": HEAD + '[[components]]\nid = "a"\nroot = "a"\nlanguages = ["cpp"]\n'
        'external = ["b/shared.hpp"]\n'
        '[[components]]\nid = "b"\nroot = "b"\nlanguages = ["cpp"]\n' + OFF_CPP,
        "files": {
            "a/main.cpp": "int main() { return 0; }\n",
            "b/shared.hpp": "inline int shared() { return 0; }\n",
            "b/lib.cpp": "int lib() { return 0; }\n",
        },
        "links": {},
        "outside": {},
    },
    "python-external": {
        "config": HEAD + '[[components]]\nid = "py"\nroot = "."\nlanguages = ["python"]\n'
        'sources = ["pkg/**"]\nexternal = ["shared/util.py", "shared/data"]\n' + OFF_PY,
        "files": {
            "pkg/__init__.py": "",
            "pkg/main.py": "def main():\n    return 0\n",
            "shared/util.py": "def util():\n    # helper\n    return 1\n",
            "shared/data/x.py": "X = 1\n",
            "shared/data/table.csv": "a,b\n",
        },
        "links": {},
        "outside": {},
    },
    "prepare-explicit-absent": {
        "config": HEAD + BUILD + cpp_component('vendor = ["src/third_party/**"]\n'),
        "files": {
            "CMakeLists.txt": "cmake_minimum_required(VERSION 3.10)\nproject(app CXX)\n",
            "src/main.cpp": "int main() { return 0; }\n",
            "src/third_party/v.cpp": "int v() { return 0; }\n",
        },
        "links": {},
        "outside": {},
    },
    "prepare-explicit-out-empty": {
        "config": HEAD + BUILD + cpp_component(""),
        "files": {
            "CMakeLists.txt": "cmake_minimum_required(VERSION 3.10)\nproject(app CXX)\n",
            "src/main.cpp": "int main() { return 0; }\n",
            "out/.keep": "",
        },
        "links": {},
        "outside": {},
    },
    "conventional-database": {
        "config": HEAD
        + BUILD
        + cpp_component("")
        # cpp.cycle with a complete database replays compiler include traces natively
        # (next_cpp_cycles.rs); it is not this case's subject.
        + '[checks."cpp.diagnostics"]\nenabled = false\n[checks."cpp.tidy"]\nenabled = false\n'
        '[checks."cpp.cycle"]\nenabled = false\n',
        "files": {
            "src/main.cpp": "int main() { return 0; }\n",
            "build/compile_commands.json": DATABASE,
        },
        "links": {},
        "outside": {},
    },
    # Frozen workspace/test_suites: a CMake tree declares a suite only through a literal
    # add_test reached by literal add_subdirectory entries; comments and expansions do not.
    "cmake-tests-undeclared": {
        "config": HEAD + BUILD + cpp_component("") + TEST_ONLY,
        "files": {
            "CMakeLists.txt": "project(p CXX)\n# add_test(NAME a COMMAND a)\n#[[\nadd_test(b b)\n]]\n"
            "add_test(${NAME} x)\nadd_subdirectory(${DIR})\nadd_subdirectory(missing)\n",
            "src/main.cpp": "int main() { return 0; }\n",
        },
        "links": {},
        "outside": {},
    },
    "cmake-tests-declared": {
        "config": HEAD + BUILD + cpp_component("") + TEST_ONLY,
        "files": {
            "CMakeLists.txt": "project(p CXX)\nif(BUILD_TESTING)\n  add_subdirectory(tests)\nendif()\n",
            "tests/CMakeLists.txt": "add_test(NAME unit COMMAND unit)\n",
            "src/main.cpp": "int main() { return 0; }\n",
        },
        "links": {},
        "outside": {},
    },
    "malformed-database": {
        "config": HEAD
        + BUILD
        + cpp_component("")
        + '[checks."cpp.diagnostics"]\nenabled = false\n[checks."cpp.tidy"]\nenabled = false\n',
        "files": {
            "src/main.cpp": "int main() { return 0; }\n",
            "out/compile_commands.json": "{\n",
        },
        "links": {},
        "outside": {},
    },
    "qt-true": {
        "config": HEAD + BUILD + cpp_component("[components.cpp]\nqt = true\n") + OFF_CPP,
        "files": {"src/widget.h": QT_SOURCE, "src/main.cpp": "int main() { return 0; }\n"},
        "links": {},
        "outside": {},
    },
    "qt-false": {
        "config": HEAD + BUILD + cpp_component("[components.cpp]\nqt = false\n") + OFF_CPP,
        "files": {"src/widget.h": QT_SOURCE, "src/main.cpp": "int main() { return 0; }\n"},
        "links": {},
        "outside": {},
    },
    "qt-omitted": {
        "config": HEAD + BUILD + cpp_component("") + OFF_CPP,
        "files": {"src/widget.h": QT_SOURCE, "src/main.cpp": "int main() { return 0; }\n"},
        "links": {},
        "outside": {},
    },
    "qt-not-boolean": {
        "config": HEAD + BUILD + cpp_component('[components.cpp]\nqt = "yes"\n') + OFF_CPP,
        "files": {"src/main.cpp": "int main() { return 0; }\n"},
        "links": {},
        "outside": {},
    },
    "cpp-unknown-key": {
        "config": HEAD + BUILD + cpp_component("[components.cpp]\nmoc = true\n") + OFF_CPP,
        "files": {"src/main.cpp": "int main() { return 0; }\n"},
        "links": {},
        "outside": {},
    },
    "prepare-not-explicit": {
        "config": HEAD
        + BUILD.replace('prepare = "explicit"', 'prepare = "auto"')
        + cpp_component("")
        + OFF_CPP,
        "files": {"src/main.cpp": "int main() { return 0; }\n"},
        "links": {},
        "outside": {},
    },
    "child-file": {
        "config": HEAD + BUILD + '[[components]]\nid = "app"\nconfig = "app/ici.toml"\n',
        "files": {
            "app/ici.toml": 'schema_version = 1\n[component]\nroot = "."\nlanguages = ["cpp"]\n'
            'build = "native"\nsources = ["src/**"]\nvendor = ["src/third_party/**"]\n'
            'external = ["../shared/shared.hpp"]\n[cpp]\nqt = true\n' + OFF_CPP,
            "app/src/main.cpp": "int main() { return 0; }\n",
            "app/src/third_party/v.cpp": "int v() { return 0; }\n",
            "shared/shared.hpp": "inline int shared() { return 0; }\n",
        },
        "links": {},
        "outside": {},
    },
    "child-file-qt-not-boolean": {
        "config": HEAD + BUILD + '[[components]]\nid = "app"\nconfig = "app/ici.toml"\n',
        "files": {
            "app/ici.toml": 'schema_version = 1\n[component]\nroot = "."\nlanguages = ["cpp"]\n'
            'build = "native"\n[cpp]\nqt = 1\n',
            "app/src/main.cpp": "int main() { return 0; }\n",
        },
        "links": {},
        "outside": {},
    },
    "vendor-custom-directory": {
        "config": HEAD + BUILD + cpp_component('vendor = ["src/libs/**"]\n') + OFF_CPP,
        "files": {
            "src/main.cpp": "int main() { try { return 0; } catch (...) { } }\n",
            "src/libs/v.cpp": "int v() { try { return 0; } catch (...) { } }\n",
        },
        "links": {},
        "outside": {},
    },
}
MAIN = {"src/main.cpp": "int main() { return 0; }\n"}
CHILD = 'schema_version = 1\n[component]\nroot = "."\nlanguages = ["cpp"]\n'
# Refusals of a malformed C++ scope key, inline and in a child file.
for name, extra in {
    "sources-not-a-list": 'sources = "src/**"\n',
    "vendor-not-a-list": 'vendor = "src/third_party/**"\n',
    "external-not-a-list": 'external = "ext/shared.hpp"\n',
    "build-dangling": 'build = "missing"\n',
}.items():
    CASES[name] = {
        "config": HEAD
        + BUILD
        + '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\n'
        + extra,
        "files": MAIN,
        "links": {},
        "outside": {},
    }
    CASES["child-" + name] = {
        "config": HEAD + BUILD + '[[components]]\nid = "app"\nconfig = "app/ici.toml"\n',
        "files": {"app/ici.toml": CHILD + extra, "app/src/main.cpp": MAIN["src/main.cpp"]},
        "links": {},
        "outside": {},
    }
CASES["project-escapes"] = {
    "config": HEAD + BUILD.replace('"CMakeLists.txt"', '"../CMakeLists.txt"') + cpp_component(""),
    "files": MAIN,
    "links": {},
    "outside": {},
}
# Frozen next suffixes: an undeclared scope is `_DEFAULT_SOURCE_GLOBS` (`.cpp/.cc/.cxx/.hpp/.h`,
# `.py`), declared globs keep every matched member and plan `SOURCE_SUFFIXES` (C++ adds
# `.c/.hh/.hxx`; never `.ixx`/`.pyi`).
MIXED = {
    "src/a.cpp": "int main() { return 0; }\n",
    "src/b.c": "int f(void) { return 1; }\n",
    "src/c.hh": "int g();\n",
    "src/d.hxx": "int h();\n",
    "src/e.ixx": "export module e;\n",
    "src/f.py": "x = 1\n",
    "src/f.pyi": "x: int\n",
    "src/notes.txt": "notes\n",
}
MIXED_COMPONENT = '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp", "python"]\n'
CASES["default-scope-mixed-suffixes"] = {
    "config": HEAD + MIXED_COMPONENT + OFF_CPP + OFF_PY,
    "files": MIXED,
    "links": {},
    "outside": {},
}
CASES["declared-scope-mixed-suffixes"] = {
    "config": HEAD + MIXED_COMPONENT + 'sources = ["src/**"]\n' + OFF_CPP + OFF_PY,
    "files": MIXED,
    "links": {},
    "outside": {},
}
CASES["cpp-not-a-table"] = {
    "config": HEAD + BUILD + cpp_component("cpp = true\n"),
    "files": MAIN,
    "links": {},
    "outside": {},
}


def materialize(project: Path, case: dict) -> None:
    for name, text in case["outside"].items():
        (project.parent / name).write_text(text, encoding="utf-8")
    for name, text in case["files"].items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.replace("{root}", str(project)), encoding="utf-8")
    for name, target in case["links"].items():
        os.symlink(target, project / name)
    (project / "ici.toml").write_text(case["config"], encoding="utf-8")


def projected(document: dict, project: Path) -> dict:
    text = json.dumps(
        {
            "gate": {
                key: document["gate"][key] for key in ("selected", "reasons", "has_violations")
            },
            "checks": [
                {
                    key: check.get(key)
                    for key in ("task_id", "check_id", "state", "evidence_complete", "variant")
                }
                for check in document["checks"]
            ],
            "blocked": document["execution"]["blocked_task_ids"],
            "failed": document["execution"]["failed_task_ids"],
            "metrics": [
                {
                    key: metric.get(key)
                    for key in ("name", "value", "numerator", "denominator", "unit", "evidence")
                }
                for metric in document["metrics"]
            ],
            "findings": [
                {
                    key: finding.get(key)
                    for key in ("task_id", "rule_id", "message", "severity", "primary_location")
                }
                for finding in document["findings"]
            ],
            "source_files": document["identity"]["source"].get("files", []),
            "external_inputs": document["identity"]["source"].get("external_inputs", []),
            "limitations": document["limitations"],
        }
    )
    return json.loads(text.replace(str(project), "{root}"))


def run(case: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        project.mkdir()
        (WORK / "bin").mkdir()
        materialize(project, case)
        result = subprocess.run(
            [PYTHON, "-m", "ici", "next", "verify", "--json"],
            cwd=project,
            env={
                "PATH": str(WORK / "bin"),
                "HOME": str(WORK),
                "XDG_CACHE_HOME": str(WORK / "cache"),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode == 2 or not result.stdout.strip().startswith("{"):
            return {
                "exit_code": result.returncode,
                "output": (result.stdout + result.stderr).strip().replace(str(project), "{root}"),
            }
        document = json.loads(result.stdout)
        return {"exit_code": result.returncode, **projected(document, project)}
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


oracle = {
    "oracle": EXPECTED,
    "cases": {name: {**case, "frozen": run(case)} for name, case in CASES.items()},
}
(ROOT / "tests/rust-parity/cpp/next-component-scope-oracle.json").write_text(
    json.dumps(oracle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(CASES)} next component scope cases")
