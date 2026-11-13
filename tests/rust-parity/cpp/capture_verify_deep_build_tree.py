"""Development-only frozen `ici verify --profile deep` release-tree refusals; never called by Rust.

    python -I -B capture_verify_deep_build_tree.py

Frozen `BuildEngine.run` raises from `_prepare_output_tree` under `ici verify --profile deep`
exactly as under `ici build`. Each case copies parity-fixtures/cpp/package-direct, enables only
the build engine (frozen's default leaves it off) and breaks the `<version>/x86_64` tree before
the frozen CLI's `ici verify --profile deep --no-cache --report` (oracle 6fe3e405). Records the
exit code, the suite status, the build engine's status, evidence, summary and targets, and the
project paths the run creates. The project path is stored as {root}.
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
FIXTURE = ROOT / "parity-fixtures/cpp/package-direct"
GOLDEN = ROOT / "tests/rust-parity/cpp/verify-deep-build-tree-oracle.json"
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
OFF = [
    "line",
    "lint",
    "compile_db",
    "test",
    "type",
    "python_compat",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "cognitive",
    "security",
    "resource",
    "binary_compat",
    "integration",
    "cycle",
]
CONFIG = (
    "[project]\ntype='cpp'\nsource_dirs=['src']\n"
    + "".join(f"[engines.{name}]\nenabled=false\n" for name in OFF)
    + "[engines.build]\nenabled=true\n"
)
# case: {path: text | {"symlink": target}}
CASES = {
    "version-file": {"v2.3.4": "not a directory\n"},
    "lib-symlink": {"elsewhere/.keep": "", "v2.3.4/x86_64/lib": {"symlink": "../../elsewhere"}},
}
PRIVATE = {".ici", "__pycache__", ".git"}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def listing(base):
    names = []
    for directory, children, files in os.walk(base):
        children[:] = sorted(name for name in children if name not in PRIVATE)
        relative = Path(directory).relative_to(base)
        names += [str(relative / name) + "/" for name in children]
        names += [str(relative / name) for name in sorted(files)]
    return sorted(name.removeprefix("./") for name in names)


def run(changes):
    work = Path(tempfile.mkdtemp(prefix="ici-verify-deep-tree-")).resolve()
    try:
        project = work / "project"
        shutil.copytree(FIXTURE, project)
        (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        for name, value in changes.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, dict):
                path.symlink_to(value["symlink"])
            else:
                path.write_text(value, encoding="utf-8")
        before = set(listing(project))
        (work / "home").mkdir()
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "verify", "--profile", "deep", "--no-cache", "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work / "home"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        report = json.loads(
            (project / "verify_report.json")
            .read_text(encoding="utf-8")
            .replace(str(project), "{root}")
        )
        created = sorted(set(listing(project)) - before - {"verify_report.json"})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    engine = next(item for item in report["results"] if item["engine_name"] == "build")
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "targets": [{key: target[key] for key in FIELDS} for target in engine["targets"]],
        "created": created,
    }


def main():
    cases = {name: {"changes": changes, "frozen": run(changes)} for name, changes in CASES.items()}
    for name, case in cases.items():
        print(name, json.dumps(case["frozen"], ensure_ascii=False)[:400])
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


if __name__ == "__main__":
    main()
