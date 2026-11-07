"""Development-only frozen Qt generated-code / Qt-major captures; never called by the Rust product.

Runs the frozen `ici verify --report` (and standalone `ici lint --report`) on
parity-fixtures/cpp/qt-codegen with a per-case compilation database and generated moc/uic/rcc
stand-ins written under build/ (frozen `analysis/_qt_codegen.py` only tests marker substrings, and
build/ is never a discovered source directory). Qt 5/6 header roots come from
`$ICI_TEST_QMAKE -query QT_INSTALL_HEADERS` / `$ICI_TEST_QMAKE6 ...` and are stored as the
`{qt5}` / `{qt6}` placeholders; `{root}` is the project directory. Generated inputs for the
budget cases are described by `generate` (counts), the 2 MiB header by `sizes`.
The lint engine status, summary, evidence, targets (with metrics) and findings are recorded.
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
FIXTURE = ROOT / "parity-fixtures/cpp/qt-codegen"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-qt-capture"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path", "metrics")
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
OFF = "[engines.lint]\nclang_tidy='off'\nclazy='off'\n"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def headers(variable: str) -> str:
    command = [os.environ[variable], "-query", "QT_INSTALL_HEADERS"]
    return subprocess.check_output(command, text=True).strip()


QT = {"{qt5}": headers("ICI_TEST_QMAKE"), "{qt6}": headers("ICI_TEST_QMAKE6")}
QT5 = ["-fPIC", "-I{qt5}", "-I{qt5}/QtCore"]
QT6 = ["-fPIC", "-I{qt6}", "-I{qt6}/QtCore"]
GEN = ["-Ibuild/gen"]
PRODUCTION = ("src/local.cpp", "src/plain.cpp", "src/widget.cpp")
GENERATED = {
    "build/gen/ui_form.h": (
        "#pragma once\n// uic stand-in: the frozen marker is setupUi(\n"
        "class Ui_Form {\npublic:\n    void setupUi(void *) {}\n};\n"
    ),
    "build/gen/local.moc": "// Meta object code from reading C++ file 'local.cpp' (stand-in)\n",
    "build/gen/moc_widget.cpp": (
        "// Meta object code from reading C++ file 'widget.h' (stand-in)\n"
        'static const char qt_meta_stringdata_Widget[] = "Widget";\n'
    ),
    "build/gen/qrc_res.cpp": "// rcc stand-in\nstatic const unsigned char qt_resource_data[] = {0};\n",
    "build/autogen/mocs_compilation.cpp": (
        '// AUTOMOC stand-in\n#include "EWIEGA46WW/moc_widget.cpp"\n'
    ),
    "build/autogen/EWIEGA46WW/moc_widget.cpp": (
        "// Meta object code from reading C++ file 'widget.h' (stand-in)\n"
        'static const char qt_meta_stringdata_Widget[] = "Widget";\n'
    ),
}


def database(entries: list[tuple[str, list[str]]], extra: list[dict] | None = None) -> str:
    rows = [
        {
            "directory": "{root}",
            "file": source,
            "arguments": ["g++", "-std=c++17", *flags, "-c", source],
        }
        for source, flags in entries
    ]
    return json.dumps([*rows, *(extra or [])])


def production(flags: list[str]) -> list[tuple[str, list[str]]]:
    return [(source, flags) for source in PRODUCTION]


DIRECT = [("build/gen/moc_widget.cpp", GEN), ("build/gen/qrc_res.cpp", GEN)]
MOCS = [("build/autogen/mocs_compilation.cpp", [])]
DB = "build/compile_commands.json"
# name: (command, ici.toml tail, files (None: no database), generate {dir: (suffix, count,
# text)}, sizes {path: bytes})
CASES = {
    "qt5-linked": ("verify", OFF, {DB: database(production(QT5 + GEN) + DIRECT)}, {}, {}),
    "qt5-mocs-compilation": ("verify", OFF, {DB: database(production(QT5 + GEN) + MOCS)}, {}, {}),
    "qt5-missing-generated": ("verify", OFF, {DB: database(production(QT5))}, {}, {}),
    "qt5-no-replay": (
        "verify",
        OFF,
        {DB: database(production(["-I{qt5}", "-I{qt5}/QtCore", *GEN]) + DIRECT)},
        {},
        {},
    ),
    "qt6-linked": ("verify", OFF, {DB: database(production(QT6 + GEN) + DIRECT)}, {}, {}),
    # A Qt 6 define token beside Qt 5 include roots: ConflictingMajor on clean replays.
    "qt-conflicting": (
        "verify",
        OFF,
        {DB: database(production([*QT5, "-Dqt6core", *GEN]) + DIRECT)},
        {},
        {},
    ),
    # Redefining QT_VERSION_MAJOR makes the frozen GCC JSON syntax output unparseable.
    "qt-major-define-redefined": (
        "verify",
        OFF,
        {DB: database(production([*QT5, "-DQT_VERSION_MAJOR=6", *GEN]) + DIRECT)},
        {},
        {},
    ),
    "qt-no-database": ("verify", OFF, {}, {}, {}),
    "qt-lint-standalone": ("lint", OFF, {}, {}, {}),
    "qt-context-error": (
        "verify",
        OFF,
        {DB: database(production(QT5 + GEN) + DIRECT, [{"directory": "{root}"}])},
        {},
        {},
    ),
    "qt-ambiguous-stem": (
        "verify",
        OFF,
        {
            DB: database(production(QT5 + GEN) + DIRECT),
            "src/extra/form.ui": "<ui version='4.0'/>\n",
            "src/extra/res.qrc": "<RCC/>\n",
        },
        {},
        {},
    ),
    "qt-huge-header": ("verify", OFF, {DB: database(production(QT5 + GEN) + DIRECT)}, {}, {}),
    "qt-budget-resources": (
        "verify",
        OFF,
        {DB: database(production(QT5 + GEN) + DIRECT)},
        {"src/many": (".ui", 4095, "<ui version='4.0'/>\n")},
        {},
    ),
    "qt-budget-inputs": (
        "verify",
        OFF,
        {DB: database(production(QT5 + GEN) + DIRECT)},
        {
            "src/many": (".ui", 4000, "<ui version='4.0'/>\n"),
            "src/objects": (".h", 95, "class Many {\n    Q_OBJECT\n};\n"),
        },
        {},
    ),
}
CASES["qt-huge-header"] = (*CASES["qt-huge-header"][:4], {"src/huge.h": 2 * 1_048_576 + 1})


def expand(text: str, project: Path) -> str:
    text = text.replace("{root}", str(project))
    for marker, value in QT.items():
        text = text.replace(marker, value)
    return text


def write_inputs(project: Path, files: dict, generate: dict, sizes: dict) -> None:
    if files:
        for name, text in GENERATED.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8")
    if "build/compile_commands.json" in files and "-Ibuild/gen" not in files[DB]:
        shutil.rmtree(project / "build/gen", ignore_errors=True)
        shutil.rmtree(project / "build/autogen", ignore_errors=True)
    for name, text in files.items():
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text(expand(text, project), encoding="utf-8")
    for directory, (suffix, count, text) in generate.items():
        (project / directory).mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (project / directory / f"item{index:04d}{suffix}").write_text(text, encoding="utf-8")
    for name, size in sizes.items():
        (project / name).write_text("/" * size, encoding="utf-8")


def run(command: str, config: str, files: dict, generate: dict, sizes: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        write_inputs(project, files, generate, sizes)
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engine = report
    if command == "verify":
        engine = next(item for item in report["results"] if item.get("engine_name") == "lint")
    captured = {
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in engine["targets"]],
        "findings": engine["findings"],
        # The frozen LintEngine `extra` counters and its process `tool_evidence`, with the
        # project as {root}.
        **json.loads(
            json.dumps(
                {
                    "extra": engine.get("extra", {}),
                    "tool_evidence": engine.get("tool_evidence", []),
                }
            ).replace(str(project), "{root}")
        ),
    }
    if command != "verify":
        captured["exit_code"] = result.returncode
    return captured


cases = {}
for name, (command, config, files, generate, sizes) in CASES.items():
    cases[name] = {
        "command": command,
        "config": config,
        "files": files,
        "generate": {key: list(value) for key, value in generate.items()},
        "sizes": sizes,
        "frozen": run(command, config, files, generate, sizes),
    }
(ROOT / "tests/rust-parity/cpp/qt-codegen-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "gxx": GXX,
            "base": BASE,
            "generated": GENERATED,
            "qt_versions": {
                "qt5": subprocess.check_output(
                    [os.environ["ICI_TEST_QMAKE"], "-query", "QT_VERSION"], text=True
                ).strip(),
                "qt6": subprocess.check_output(
                    [os.environ["ICI_TEST_QMAKE6"], "-query", "QT_VERSION"], text=True
                ).strip(),
            },
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} Qt codegen cases with {GXX}")
