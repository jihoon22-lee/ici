"""Development-only frozen legacy line-key captures on a C++ project; never called by Rust.

Frozen `LineCountEngine._build_scan_plan` over `[engines.line]` `include_dirs`, `exclude_dirs`,
`gate_dirs`, `mode`, `enabled` and `required` on a `type='cpp'` project: `ici line --report`
records the exit code, status, summary, evidence, targets and every counted file row; the
`verify` cases (every other engine disabled) also record the run's exit code and the line
engine's `required` flag. Quirks kept by the captures: a nested `exclude_dirs` entry leaves its
files in the source pass, a nested `gate_dirs` entry gates nothing (only top-level directory
names are compared), and a gated directory outside the source scope is not gated.
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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-line-keys"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
ROW = ("path", "scope", "lang", "code", "comment", "blank", "total", "status")
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
ENGINES = (
    "lint",
    "compile_db",
    "test",
    "type",
    "python_compat",
    "cognitive",
    "resource",
    "security",
    "cycle",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "build",
    "binary_compat",
    "integration",
)
ONLY_LINE = "".join(f"[engines.{name}]\nenabled=false\n" for name in ENGINES)
# Frozen verify crashes when every engine is disabled; the disabled-line case keeps dup.
WITH_DUP = "".join(f"[engines.{name}]\nenabled=false\n" for name in ENGINES if name != "dup")


def body(lines: int) -> str:
    return "// header\n" + "".join(f"int v{index} = {index};\n" for index in range(lines))


FILES = {
    "src/a.cpp": body(3),
    "src/core/big.cpp": body(6),
    "src/gen/out.cpp": body(6),
    "include/a.hpp": body(4),
    "lib/l.cpp": body(5),
    "app/main.cpp": body(2),
    "tools/t.cpp": body(6),
    "tools/sub/s.cpp": body(6),
    "extras/x.sh": "# shell\necho 1\necho 2\necho 3\n",
    "docs/guide.md": "# guide\n\ntext\n",
    "build/skip.cpp": body(9),
}
LIMITS = "[engines.line]\nwarn_limit=3\nfail_limit=5\n"
CASES = {
    "defaults": ("line", LIMITS),
    "include-dirs": ("line", LIMITS + "include_dirs=['tools/sub','extras']\n"),
    "exclude-dirs": ("line", LIMITS + "exclude_dirs=['lib','src/gen','tools/']\n"),
    "gate-dirs-narrow": ("line", LIMITS + "gate_dirs=['lib']\n"),
    "gate-dirs-nested": ("line", LIMITS + "gate_dirs=['src/core']\n"),
    "gate-dirs-outside-scope": ("line", LIMITS + "gate_dirs=['tools','src']\n"),
    "gate-dirs-excluded": ("line", LIMITS + "gate_dirs=['src','lib']\nexclude_dirs=['lib']\n"),
    "mode-pass-fail": ("line", LIMITS + "mode='pass_fail'\nexclude_dirs=['src','lib']\n"),
    "mode-pass-warn": ("line", LIMITS + "mode='pass_warn'\n"),
    "mode-pass-warn-fail-clean": ("line", "[engines.line]\nwarn_limit=50\nfail_limit=60\n"),
    "verify-required": ("verify", LIMITS),
    "verify-not-required": ("verify", LIMITS + "required=false\n"),
    "verify-not-required-warn": (
        "verify",
        LIMITS + "required=false\nmode='pass_warn'\n",
    ),
    "verify-disabled": ("verify", LIMITS + "enabled=false\n"),
    "line-disabled": ("line", LIMITS + "enabled=false\n"),
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(command: str, config: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        project.mkdir()
        prefix = ""
        if command == "verify":
            prefix = WITH_DUP if "enabled=false" in config else ONLY_LINE
        (project / "ici.toml").write_text(BASE + prefix + config, encoding="utf-8")
        for name, text in FILES.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        name = "verify_report.json" if command == "verify" else "line_report.json"
        path = project / name
        report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        output = (result.stdout + result.stderr).replace(str(project), "{root}")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    captured = {"exit_code": result.returncode}
    if report is None:
        captured["output"] = output
        return captured
    if command == "verify":
        engines = [item for item in report["results"] if item.get("engine_name") == "line"]
        captured["engines"] = [item["engine_name"] for item in report["results"]]
        if not engines:
            return captured
        report = engines[0]
        captured["required"] = report.get("required")
    captured.update(
        status=report["status"],
        summary=report["summary"],
        evidence=report["evidence"],
        targets=[{key: target.get(key) for key in FIELDS} for target in report["targets"]],
        files=[[row.get(key) for key in ROW] for row in report["extra"]["files_data"]],
        all=report["extra"]["all"],
    )
    return captured


cases = {
    name: {"command": command, "config": config, "frozen": run(command, config)}
    for name, (command, config) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/line-keys-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "base": BASE,
            "only_line": ONLY_LINE,
            "with_dup": WITH_DUP,
            "files": FILES,
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
for name, case in cases.items():
    frozen = case["frozen"]
    print(name, frozen["exit_code"], frozen.get("status"), frozen.get("summary", "")[:90])
