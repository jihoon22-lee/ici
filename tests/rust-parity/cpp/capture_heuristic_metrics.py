"""Development-only frozen C++ brace-heuristic metric captures (legacy and next); never Rust.

Legacy: runs the frozen CLI `ici complexity --report` and `ici cognitive --report` (type='cpp',
source_dirs=['src'], no compilation database) and keeps the whole saved engine report (project
root written as `<root>`) and the exit code. Next: runs the frozen CLI `ici analyze --json`
without any ici.toml (one automatic C++ component) and keeps the exit code, the
`cpp.complexity`/`cpp.cognitive` checks, their `metrics.*` findings, the `functions_measured` /
`max_complexity` / `max_cognitive` metrics in run order and the run limitations of the metric
scanner. Every run uses a fresh `project` with PATH=/usr/bin:/bin and LANG/LC_ALL=C.UTF-8.

Trees: parity-fixtures/cpp/heuristic-metrics/{zoo,errors} (operators, constructors, lambdas,
templates, preprocessor conditionals, a macro-generated function, thresholds; bodies the
cognitive parser rejects), parity-fixtures/cpp/metric-sources/{unterminated,unreadable}, and
trees generated here (never committed): `lambda-limit` (100,001 lambda bodies), `too-large`
(an 8 MiB + 4 byte comment-only source), `nul` (a NUL inside a function body) and, for the
legacy commands only, `many-sources` (2,049 sources) and `inventory-bytes` (nine 7.5 MiB
comment-only sources, 67.5 MiB). The next
`zoo` run omits `src/plain.c`: frozen's automatic C++ component globs (`workspace/model.py`
`_DEFAULT_SOURCE_GLOBS`: .cpp/.cc/.cxx/.hpp/.h) never select a `.c` file, so its results are
the same with or without it, while native automatic discovery selects `.c` (a separate
component-discovery difference).
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
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-heuristic-metrics"
GOLDEN = ROOT / "tests/rust-parity/cpp/heuristic-metrics-oracle.json"
CONFIG = '[project]\ntype = "cpp"\nsource_dirs = ["src"]\n'
METRICS = ("functions_measured", "max_complexity", "max_cognitive")

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

OK_SOURCE = b"int ok(int x) {\n  if (x) { return 1; }\n  return 0;\n}\n"
TREES = {
    "zoo": "heuristic-metrics/zoo",
    "errors": "heuristic-metrics/errors",
    "unterminated": "metric-sources/unterminated",
    "unreadable": "metric-sources/unreadable",
}
GENERATED = {
    "lambda-limit": {
        "src/lambda.cpp": b"void f() {\n" + b"[]{};" * 100_001 + b"\n}\n",
        "src/ok.cpp": OK_SOURCE,
    },
    "too-large": {
        "src/big.cpp": b"// " + b"x" * (8 * 1024 * 1024) + b"\n",
        "src/ok.cpp": OK_SOURCE,
    },
    "nul": {
        "src/nul.cpp": b"int nul(int a) {\n  char c = '\0';\n  if (a) { return c; }\n  return 0;\n}\n",
        "src/ok.cpp": OK_SOURCE,
    },
    # Legacy only: 2,049 sources pass `_MAX_CPP_COMPLEXITY_SOURCES` / `_MAX_SOURCES`, and nine
    # 7.5 MiB comment-only sources (each under the 8 MiB file bound) pass the 64 MiB inventory.
    "many-sources": {
        f"src/s{index:04}.cpp": f"int s{index}() {{ return {index}; }}\n".encode()
        for index in range(2049)
    },
    "inventory-bytes": {
        **{f"src/big{index}.cpp": b"// " + b"x" * (15 * 512 * 1024) + b"\n" for index in range(9)},
        "src/ok.cpp": OK_SOURCE,
    },
}
LEGACY_ONLY = {"many-sources", "inventory-bytes"}


def materialize(name: str, project: Path) -> None:
    if name in GENERATED:
        for relative, payload in GENERATED[name].items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_bytes(payload)
    else:
        shutil.copytree(FIXTURES / TREES[name], project)
    (project / "ici.toml").unlink(missing_ok=True)
    # The project is its own VCS boundary (frozen and native both stop at `.hg`).
    (project / ".hg").mkdir(exist_ok=True)


def run(project: Path, argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "ici", *argv],
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(WORK / "home"),
            "GIT_CEILING_DIRECTORIES": str(project.parent),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(ORACLE / "src"),
        },
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


def legacy(name: str, command: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "home").mkdir(parents=True)
    try:
        project = WORK / "project"
        materialize(name, project)
        (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        result = run(project, [command, "--report"])
        text = (project / f"{command}_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<root>"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {"exit_code": result.returncode, "report": report}


def scanner_limitation(item: str) -> bool:
    return "function boundaries" in item or item.endswith("Error")


def analyze(name: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "home").mkdir(parents=True)
    try:
        project = WORK / "project"
        materialize(name, project)
        (project / "src/plain.c").unlink(missing_ok=True)
        result = run(project, ["analyze", "--json"])
        document = json.loads(result.stdout)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "checks": [
            check
            for check in document["checks"]
            if check["check_id"] in ("cpp.complexity", "cpp.cognitive")
        ],
        "findings": [
            finding for finding in document["findings"] if finding["rule_id"].startswith("metrics.")
        ],
        "metrics": [metric for metric in document["metrics"] if metric["name"] in METRICS],
        "limitations": [item for item in document["limitations"] if scanner_limitation(item)],
    }


names = [*TREES, *GENERATED]
cases = {}
for name in names:
    for command in ("complexity", "cognitive"):
        cases[f"{command}:{name}"] = legacy(name, command)
    if name not in LEGACY_ONLY:
        cases[f"analyze:{name}"] = analyze(name)
GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "config": CONFIG, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} heuristic metric cases")
