"""Development-only frozen legacy Python engine captures; never called by the Rust product.

Runs `python -m ici <engine> --report` from the immutable v0.13.0 checkout for the Python
scaffolding paths (strict source intake, syntax errors, excluded-only scopes) of the legacy
security/resource/dup/dead/cognitive/exception engines and records each engine's status,
summary, evidence, targets, legacy findings and exit code.
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
# Project tools for the tool-backed cases (the opus-env ICI_TEST_RUFF / ICI_TEST_MYPY), linked
# into one PATH directory so frozen `shutil.which` and the native resolver find the same files.
TOOLS = {
    "ruff": "/tmp/ici-rust-replacement-20261028/tool-spike/ruff/ruff-x86_64-unknown-linux-musl/ruff",
    "mypy": "/home/jihoon/projects/ici/.venv/bin/mypy",
}
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
BASE = "[project]\ntype='python'\nsource_dirs=['src']\n"
OUT = ROOT / "tests/rust-parity/python/legacy-engines.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

FINDING = "import pickle\n\n\ndef g(data):\n    f = open('x')\n    return pickle.loads(data)\n"
SYNTAX = "def f(:\n    pass\n"


def hexed(data: bytes) -> dict:
    return {"hex": data.hex()}


# name: files (text, or {"hex": ...} for raw bytes)
PROJECTS = {
    "syntax-second": {"src/a.py": FINDING, "src/b.py": SYNTAX},
    "syntax-unclosed": {"src/a.py": "x = (1,\n"},
    "invalid-start": {"src/a.py": hexed(b"x = 1\n\xff\n"), "src/b.py": FINDING},
    "truncated": {"src/a.py": hexed(b"x = '\xe2\x82")},
    "continuation": {"src/a.py": hexed(b"x = '\xe2\x82A'\n")},
    "surrogate": {"src/a.py": hexed(b"x = '\xed\xa0\x80'\n")},
    "overlong": {"src/a.py": hexed(b"x = '\xc0\x80'\n")},
    "latin1-cookie": {
        "src/a.py": hexed(b"# -*- coding: latin-1 -*-\ns = 'caf\xe9'\n"),
        "src/b.py": FINDING,
    },
    "second-file-invalid": {"src/a.py": FINDING, "src/b.py": hexed(b"\xff\n")},
    "nul": {"src/a.py": hexed(b"x = 1\x00\n")},
    "excluded-only": {"src/vendor/a.py": FINDING},
    "no-python-source": {"src/README.md": "x\n"},
}
ENGINES = ("security", "resource", "dup", "dead", "cognitive", "exception")
# Planning refuses an empty Python scope natively; these engines keep a frozen no-source verdict.
NO_SOURCE_ENGINES = (*ENGINES, "lint", "cycle", "type", "complexity")
# Per-case configuration appended to BASE.
CONFIGURED = {
    "complexity/no-python-source-configured": (
        "complexity",
        "no-python-source",
        "[engines.complexity]\nwarn_cc=7\nfail_cc=9\n",
    ),
}
# The full decode-error shape set only matters where the CPython message text is embedded.
DECODE_ONLY = {"truncated", "continuation", "surrogate", "overlong"}


TYPED = "def f(x: int) -> int:\n    return x\n\n\n"
UNANNOTATED = "def h(y):\n    return y\n"
WARN_ANNOTATION = "[engines.type]\nwarn_on_missing_annotation=true\n"
# name: (command, files, extra configuration, whether PATH holds the tools)
TOOL_CASES = {
    "type/clean": ("type", {"src/a.py": TYPED}, "", True),
    "type/error": ("type", {"src/a.py": TYPED + "f('x')\n"}, "", True),
    "type/note": ("type", {"src/a.py": TYPED + "reveal_type(f)\n"}, "", True),
    "type/note-repeat": (
        "type",
        {"src/a.py": TYPED + "reveal_type(f)\nreveal_type(f)\n"},
        "",
        True,
    ),
    "type/tool-failure": ("type", {"src/a.py": "def f(:\n    pass\n"}, "", True),
    "type/profile-ici": (
        "type",
        {"src/a.py": "def g():\n    return 1 + 'x'\n"},
        "[engines.type]\nmypy_profile='ici'\n",
        True,
    ),
    "type/profile-project": ("type", {"src/a.py": "def g():\n    return 1 + 'x'\n"}, "", True),
    "type/annotation-after-error": (
        "type",
        {"src/a.py": TYPED + UNANNOTATED + "\n\nf('x')\n"},
        WARN_ANNOTATION,
        True,
    ),
    "type/annotation-after-clean": (
        "type",
        {"src/a.py": TYPED + UNANNOTATED},
        WARN_ANNOTATION,
        True,
    ),
    "type/mode-pass-warn-fail": (
        "type",
        {"src/a.py": TYPED + "f('x')\nreveal_type(f)\n"},
        "[engines.type]\nmode='pass_warn_fail'\n",
        True,
    ),
    "type/two-files": (
        "type",
        {"src/a.py": TYPED + "f('x')\n", "src/pkg/b.py": "y: int = 'z'\n"},
        "",
        True,
    ),
    "type/mypy-missing": ("type", {"src/a.py": TYPED}, "", False),
    "type/mypy-missing-annotation": (
        "type",
        {"src/a.py": TYPED + UNANNOTATED},
        WARN_ANNOTATION,
        False,
    ),
    "type/mypy-missing-required": (
        "type",
        {"src/a.py": TYPED},
        "[engines.type]\nmypy_required=true\n",
        False,
    ),
}


def write(project: Path, files: dict) -> None:
    for name, content in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            path.write_bytes(bytes.fromhex(content["hex"]))
        else:
            path.write_text(content, encoding="utf-8")


def run(command: str, files: dict, config: str = "", tools: bool = False) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-engines-"))
    path = "/usr/bin:/bin"
    try:
        if tools:
            bin_dir = work / "bin"
            bin_dir.mkdir()
            for name, target in TOOLS.items():
                (bin_dir / name).symlink_to(target)
            path = f"{bin_dir}:{path}"
        project = work / "project"
        project.mkdir()
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        write(project, files)
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": path, "HOME": str(work), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report_path = project / f"{command}_report.json"
        report = (
            json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if report is None:
        # The frozen engine crashed before writing a report: record the uncaught exception.
        return {"exit_code": result.returncode, "crash": result.stderr.strip().splitlines()[-1]}
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
        "findings": [
            {key: finding.get(key) for key in ("rule_id", "fingerprint", "message", "tool_rule_id")}
            for finding in report["findings"]
        ],
    }


def main() -> None:
    cases = {}
    for project, files in PROJECTS.items():
        for engine in NO_SOURCE_ENGINES if project == "no-python-source" else ENGINES:
            if project in DECODE_ONLY and engine not in ("exception", "security"):
                continue
            cases[f"{engine}/{project}"] = {
                "command": engine,
                "files": files,
                "frozen": run(engine, files),
            }
    for name, (engine, project, config) in CONFIGURED.items():
        files = PROJECTS[project]
        cases[name] = {
            "command": engine,
            "config": config,
            "files": files,
            "frozen": run(engine, files, config),
        }
    tool_cases = {
        name: {
            "command": command,
            "config": config,
            "files": files,
            "tools": tools,
            "frozen": run(command, files, config, tools),
        }
        for name, (command, files, config, tools) in TOOL_CASES.items()
    }
    python = subprocess.check_output([PYTHON, "--version"], text=True).strip()
    versions = {
        name: subprocess.check_output([target, "--version"], text=True).strip()
        for name, target in TOOLS.items()
    }
    OUT.write_text(
        json.dumps(
            {
                "oracle": EXPECTED,
                "python": python,
                "base": BASE,
                "cases": cases,
                "tool_cases": tool_cases,
                "tool_versions": versions,
            },
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"{len(cases)} legacy engine cases and {len(tool_cases)} tool cases with {python}, "
        f"{versions}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
