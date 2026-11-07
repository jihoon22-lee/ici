"""Development-only frozen captures for the R8 rehearsal blockers; never product code.

    python -I capture_r8_legacy.py

Runs the immutable v0.13.0 CLI (empty HOME/XDG_CACHE_HOME, PATH=/usr/bin:/bin) on small
trees and records only what the native replays compare:

* `verify/*`: a configuration-less checkout whose only Python file is outside the default
  `src` scope (`a.py` at the root), under `verify --report` and `verify --profile deep
  --report`: exit code, suite status and every engine's status/evidence/required/summary and
  targets (name, status, message, path, line).
* `dead/*`: the quality-zoo `python.dead-private-function` scenario under its known-answer
  command; the legacy `dead` findings and the suite's `analysis_metadata`.
* `nested/*`: `ici analyze` run from a sub-directory without its own VCS marker: exit code,
  the first stderr line with temporary paths replaced by `<tmp>`, and the files created
  outside `.git`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
OUT = ROOT / "tests/rust-parity/runtime/r8-legacy-oracle.json"
ZOO_DEAD = ROOT / "quality-zoo/scenarios/python/dead-private-function"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

LEGACY = '[ici]\nversion = "0.13.0"\n'
SOURCE = "def f():\n    return 1\n"
# name: (files relative to the base, `git init` directories, cwd relative to the base)
NESTED = {
    "nested/legacy-config-above": (
        {"outer/ici.toml": LEGACY, "outer/inner/a.py": SOURCE},
        ["outer"],
        "outer/inner",
    ),
    "nested/legacy-config-above-package": (
        {
            "outer/ici.toml": LEGACY,
            "outer/inner/pyproject.toml": "[project]\nname='x'\nversion='1'\n",
            "outer/inner/a.py": SOURCE,
        },
        ["outer"],
        "outer/inner",
    ),
    "nested/checkout-without-config": (
        {"outer/inner/a.py": SOURCE},
        ["outer"],
        "outer/inner",
    ),
    "nested/no-checkout": ({"outer/inner/a.py": SOURCE}, [], "outer/inner"),
}
VERIFY = {
    "verify/root-file-standard": ["verify", "--report"],
    "verify/root-file-deep": ["verify", "--profile", "deep", "--report"],
}
TARGET_FIELDS = ("target_name", "status", "message", "file_path", "start_line")
FINDING_FIELDS = (
    "rule_id",
    "tool_rule_id",
    "tool_name",
    "tool_version",
    "category",
    "severity",
    "confidence",
    "message",
    "explanation",
    "remediation",
    "snippet",
    "fingerprint",
)


def run(argv: list[str], cwd: Path, base: Path) -> subprocess.CompletedProcess:
    env = {
        "HOME": str(base / "home"),
        # Git discovery stops at the case base, as in the native replay.
        "GIT_CEILING_DIRECTORIES": str(base),
        "XDG_CACHE_HOME": str(base / "cache"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ORACLE / "src"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    return subprocess.run(
        [PYTHON, "-m", "ici", *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


def files(path: Path) -> list[str]:
    return sorted(
        item.relative_to(path).as_posix()
        for item in path.rglob("*")
        if (item.is_file() or item.is_symlink()) and ".git" not in item.relative_to(path).parts
    )


def write(base: Path, tree: dict[str, str]) -> None:
    for relative, text in tree.items():
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def engines(report: dict) -> dict:
    return {
        engine["engine_name"]: {
            "status": engine["status"],
            "evidence": engine["evidence"],
            "required": engine["required"],
            "summary": engine["summary"],
            "targets": [
                {key: target.get(key) for key in TARGET_FIELDS} for target in engine["targets"]
            ],
        }
        for engine in report["results"]
    }


def verify_cases() -> dict:
    cases = {}
    for name, argv in VERIFY.items():
        with tempfile.TemporaryDirectory(prefix="ici-r8-") as temp:
            base = Path(temp)
            project = base / "project"
            write(project, {"a.py": SOURCE})
            git_init(project)
            completed = run(argv, project, base)
            report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
            cases[name] = {
                "argv": argv,
                "tree": {"a.py": SOURCE},
                "exit_code": completed.returncode,
                "suite_status": report["suite_status"],
                "engines": engines(report),
            }
    return cases


def dead_cases() -> dict:
    with tempfile.TemporaryDirectory(prefix="ici-r8-") as temp:
        base = Path(temp)
        project = base / "project"
        shutil.copytree(ZOO_DEAD, project)
        selector = json.loads((project / "scenario.json").read_text(encoding="utf-8"))
        expectation = json.loads(
            (
                project
                / selector["expectations"][
                    "c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde"
                ]
            ).read_text(encoding="utf-8")
        )
        argv = [*expectation["command"], "--report"]
        completed = run(argv, project, base)
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
        dead = next(item for item in report["results"] if item["engine_name"] == "dead")
        return {
            "dead/zoo-private-function": {
                "scenario": ZOO_DEAD.relative_to(ROOT).as_posix(),
                "argv": argv,
                "exit_code": completed.returncode,
                "suite_status": report["suite_status"],
                "analysis_metadata_keys": sorted(report["analysis_metadata"]),
                "analysis_metadata": report["analysis_metadata"],
                "findings": [
                    {
                        **{key: finding[key] for key in FINDING_FIELDS},
                        "path": finding["primary_location"]["path"],
                        "start_line": finding["primary_location"]["start_line"],
                        "label": finding["primary_location"]["label"],
                    }
                    for finding in dead["findings"]
                ],
            }
        }


def nested_cases() -> dict:
    cases = {}
    for name, (tree, checkouts, cwd) in NESTED.items():
        with tempfile.TemporaryDirectory(prefix="ici-r8-") as temp:
            base = Path(temp)
            write(base, tree)
            for checkout in checkouts:
                git_init(base / checkout)
            before = set(files(base))
            completed = run(["analyze"], base / cwd, base)
            stderr = completed.stderr.replace(str(base.resolve()), "<tmp>").replace(
                str(base), "<tmp>"
            )
            cases[name] = {
                "tree": tree,
                "checkouts": checkouts,
                "cwd": cwd,
                "exit_code": completed.returncode,
                "stderr_first_line": stderr.splitlines()[0] if stderr else "",
                "created": sorted(
                    item
                    for item in set(files(base)) - before
                    if not item.startswith(("home/", "cache/"))
                ),
            }
    return cases


def main() -> None:
    cases = {**verify_cases(), **dead_cases(), **nested_cases()}
    payload = {"oracle": EXPECTED, "python": sys.version.split()[0], "cases": cases}
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({name: case["exit_code"] for name, case in cases.items()}, indent=1))


if __name__ == "__main__":
    os.umask(0o022)
    main()
