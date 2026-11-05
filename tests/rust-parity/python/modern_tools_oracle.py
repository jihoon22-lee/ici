"""Development-only frozen modern python.lint/python.format/python.type captures.

Each case is a small project analyzed by the immutable v0.13.0 `ici analyze --json` command with
the same Ruff, mypy and ty executables the native replay uses (the opus-env ICI_TEST_RUFF,
ICI_TEST_MYPY and ICI_TEST_TY), linked into one PATH directory. The recorded result keeps check
states, findings (rule identity, message, severity, confidence, category, evidence, location and
attributed fingerprint), metrics, limitations, the gate and the exit code. The native test
`crates/ici-cli/tests/python_modern_tools_parity.rs` replays the same projects; reviewed
differences live in per-case `corrections` and are never produced by regenerating this file.

    python3 -I modern_tools_oracle.py [--write]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURE = Path(__file__).with_name("modern-tools-findings.json")
TOOLS = {
    "ruff": os.environ.get(
        "ICI_TEST_RUFF",
        "/tmp/ici-rust-replacement-20261028/tool-spike/ruff/ruff-x86_64-unknown-linux-musl/ruff",
    ),
    "mypy": os.environ.get("ICI_TEST_MYPY", "/home/jihoon/projects/ici/.venv/bin/mypy"),
    "ty": os.environ.get(
        "ICI_TEST_TY",
        "/tmp/ici-rust-replacement-20261028/tool-spike/ty/ty-x86_64-unknown-linux-musl/ty",
    ),
}
ALL_CHECKS = [
    "line",
    "lint",
    "format",
    "type",
    "test",
    "coverage",
    "complexity",
    "cognitive",
    "cycle",
    "dup",
    "security",
    "resource",
    "exception",
    "dead",
    "compat",
    "compat-runtime",
]
TOOLED = ["lint", "format", "type"]
TYPED = "def f(x: int) -> int:\n    return x\n"
CASES = [
    {
        "id": "violations",
        "files": {"main.py": "import os, sys\n\n\n" + TYPED + "\n\nf('a')\ny=1\n"},
        "type_provider": "mypy",
    },
    {"id": "clean", "files": {"main.py": TYPED}, "type_provider": "mypy"},
    {"id": "format-only", "files": {"main.py": "x=1\n"}, "checks": ["format"]},
    {
        "id": "lint-only-unicode",
        "files": {"main.py": "# 한국어 주석\ns = '값'; import os\n"},
        "checks": ["lint"],
    },
    {
        "id": "two-files",
        "files": {
            "pkg/__init__.py": "",
            "pkg/a.py": "import json\n",
            "pkg/b.py": "from pkg.a import missing\n\nz: int = 'q'\n",
        },
        "type_provider": "mypy",
    },
    {
        "id": "mypy-note",
        "files": {"main.py": TYPED + "\n\nreveal_type(f)\n"},
        "checks": ["type"],
        "type_provider": "mypy",
    },
    # No type_provider: frozen defaulted to mypy; the approved native default for a project
    # without mypy or ty configuration is an advisory ty (rust-replacement/decisions.md).
    {
        "id": "default-provider",
        "files": {"main.py": TYPED + "\n\nf('a')\n"},
        "checks": ["type"],
    },
    {
        "id": "ty-provider",
        "files": {"main.py": TYPED + "\n\nf('a')\nundefined_name\n"},
        "checks": ["type"],
        "type_provider": "ty",
    },
    {
        "id": "ty-provider-clean",
        "files": {"main.py": TYPED},
        "checks": ["type"],
        "type_provider": "ty",
    },
    {
        "id": "syntax-error",
        "files": {"main.py": "def broken(:\n    pass\n"},
        "type_provider": "mypy",
    },
    {"id": "tools-missing", "files": {"main.py": TYPED}, "tools": False, "type_provider": "mypy"},
]


def config(case: dict) -> str:
    checks = case.get("checks", TOOLED)
    lines = [
        "schema_version=1",
        "[workspace]",
        'name="oracle"',
        'profile="fast"',
        "[[components]]",
        'id="project"',
        'root="."',
        'languages=["python"]',
    ]
    if "type_provider" in case:
        lines += ["[components.python]", f'type_provider="{case["type_provider"]}"']
    for check in ALL_CHECKS:
        if check not in checks:
            lines += [f'[checks."python.{check}"]', "enabled=false"]
    return "\n".join(lines) + "\n"


def normalize(document: dict) -> dict:
    return {
        "checks": [
            {key: check.get(key) for key in ("check_id", "state", "evidence_complete")}
            for check in document["checks"]
        ],
        "findings": [
            {key: value for key, value in finding.items() if key != "task_id"}
            for finding in document["findings"]
        ],
        "metrics": document["metrics"],
        "limitations": document["limitations"],
        "gate": document["gate"],
    }


def run_case(case: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-modern-tools-"))
    try:
        bin_dir = work / "bin"
        bin_dir.mkdir()
        if case.get("tools", True):
            for name, target in TOOLS.items():
                (bin_dir / name).symlink_to(target)
        root = work / "project"
        root.mkdir()
        (root / "ici.toml").write_text(config(case), encoding="utf-8")
        for relative, text in case["files"].items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        completed = subprocess.run(
            [PYTHON, "-m", "ici", "analyze", "--json"],
            cwd=root,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(work),
                # Git discovery stops above the project, as in the native replay.
                "GIT_CEILING_DIRECTORIES": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        document = json.loads(completed.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"exit_code": completed.returncode, **normalize(document)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE
    versions = {
        name: subprocess.check_output([target, "--version"], text=True).strip()
        for name, target in TOOLS.items()
    }
    previous = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    cases = []
    for case in CASES:
        prior = next((item for item in previous.get("cases", []) if item["id"] == case["id"]), {})
        cases.append(
            {
                **case,
                "oracle": run_case(case),
                "corrections": prior.get("corrections", {"expected": {}, "reasons": {}}),
            }
        )
    document = {"source": SOURCE, "tool_versions": versions, "cases": cases}
    text = json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.write:
        FIXTURE.write_text(text, encoding="utf-8")
    elif not FIXTURE.exists() or FIXTURE.read_text(encoding="utf-8") != text:
        raise SystemExit(f"{FIXTURE} differs from the frozen observation")
    print(f"{len(cases)} frozen modern tool cases with {versions}")


if __name__ == "__main__":
    main()
