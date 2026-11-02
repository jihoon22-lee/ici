"""Development-only frozen `ici next` compiler-diagnostics captures; never Rust.

Runs the frozen CLI `ici next plan --json` and `ici next verify --json` over a three-unit C++
component whose compile_commands.json (absolute `/usr/bin/g++ -std=c++17 -Wall` rows) yields one
warning (`a.cpp`, -Wunused-variable), one error (`b.cpp`, no -W option), one clean unit
(`c.cpp`) and one build-generated unit under the linked build directory (`out/gen.cpp`, outside
the component root `src`), with
cpp.tidy disabled (a runner's clang-tidy must not change the findings) and PATH=/usr/bin:/bin and LANG/LC_ALL=C.UTF-8 (GCC's UTF-8 quotes). Case `root`
records the compile directory as the workspace root; case `build` records the build directory
`out` (frozen parses each unit's diagnostics relative to that directory). Keeps the planned
cpp.diagnostics task ids and argv, the cpp.diagnostics/cpp.compile check records, the compile
metrics, the findings and the gate, with the project root written as `{root}`.
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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-next-compiler"
GOLDEN = ROOT / "tests/rust-parity/cpp/next-compiler-oracle.json"
CONFIG = (
    'schema_version = 1\n[workspace]\nname = "probe"\nprofile = "standard"\n'
    '[builds.native]\nsystem = "cmake"\ndirectory = "out"\nproject = "CMakeLists.txt"\n'
    'variant = "release"\nprepare = "explicit"\n'
    '[[components]]\nid = "app"\nroot = "src"\nlanguages = ["cpp"]\nbuild = "native"\n'
    '[checks."cpp.tidy"]\nenabled = false\n'
)
FILES = {
    "CMakeLists.txt": (
        "cmake_minimum_required(VERSION 3.16)\nproject(p CXX)\n"
        "add_executable(app src/a.cpp src/b.cpp src/c.cpp)\n"
    ),
    "src/a.cpp": "int a() { int unused = 0; return 1; }\n",
    "src/b.cpp": "int b() { return undefined_name; }\n",
    "src/c.cpp": "int main() { return 0; }\n",
    "out/gen.cpp": "int generated() { return 0; }\n",
}
CASES = {"root": "{root}", "build": "{root}/out"}
FINDING_FIELDS = (
    "rule_id",
    "message",
    "severity",
    "confidence",
    "evidence",
    "native_rule_id",
    "primary_location",
    "provider",
    "task_id",
)

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def rows(directory: str) -> list[dict]:
    return [
        {
            "directory": directory,
            "file": "{root}/" + source,
            "arguments": [
                "/usr/bin/g++",
                "-std=c++17",
                "-Wall",
                "-c",
                "{root}/" + source,
                "-o",
                Path(source).stem + ".o",
            ],
        }
        for source in ("src/a.cpp", "src/b.cpp", "src/c.cpp", "out/gen.cpp")
    ]


def run(name: str, directory: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        for relative, text in FILES.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        database = json.dumps(rows(directory)).replace("{root}", str(project))
        (project / "out/compile_commands.json").write_text(database, encoding="utf-8")
        (WORK / "home").mkdir()
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(WORK / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(ORACLE / "src"),
        }
        documents = {}
        for command in ("plan", "verify"):
            result = subprocess.run(
                [PYTHON, "-m", "ici", "next", command, "--json"],
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            text = result.stdout.replace(str(project), "{root}")
            documents[command] = (result.returncode, json.loads(text))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    plan_code, plan = documents["plan"]
    verify_code, verify = documents["verify"]
    planned = [
        {"task_id": check["task_id"], "argv": check.get("argv")}
        for component in plan["plans"]
        for check in component["checks"]
        if check["check"] == "cpp.diagnostics"
    ]
    return {
        "directory": directory,
        "plan_exit_code": plan_code,
        "planned": planned,
        "exit_code": verify_code,
        "checks": [
            {key: check.get(key) for key in ("check_id", "task_id", "state", "evidence_complete")}
            for check in verify["checks"]
            if check["check_id"] in ("cpp.compile", "cpp.diagnostics")
        ],
        "metrics": [metric for metric in verify["metrics"] if metric["name"].startswith("units.")],
        "findings": [
            {key: finding.get(key) for key in FINDING_FIELDS} for finding in verify["findings"]
        ],
        "gate": verify["gate"],
    }


def banner(executable: str) -> str:
    output = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True
    ).stdout
    return output.splitlines()[0]


cases = {name: run(name, directory) for name, directory in CASES.items()}
GOLDEN.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "gxx": banner("/usr/bin/g++"),
            "config": CONFIG,
            "files": FILES,
            "database": rows("<directory>"),
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} next compiler cases")
