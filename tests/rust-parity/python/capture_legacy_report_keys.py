"""Development-only frozen legacy report-key captures; never called by the Rust product.

Runs `python -m ici <command> --report` and `python -m ici verify --report` from the immutable
v0.13.0 checkout and records the per-engine keys the historical JSON reports carry beside the
targets: `support_matrix`, `cache_hit`, `cache_key`, `artifact_manifests` and `tool_evidence`,
plus each engine's status/evidence so a support entry can be traced to the evidence it read.
Run-specific values are normalized: the scratch tool directory becomes `<tools>` and the project
directory `<project>`; cache keys are kept (their shape is compared, not their value).
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
TOOLS = {
    "ruff": "/tmp/ici-rust-replacement-20261028/tool-spike/ruff/ruff-x86_64-unknown-linux-musl/ruff",
    "mypy": "/home/jihoon/projects/ici/.venv/bin/mypy",
    # verify runs `python3 -m mypy`; both products see the same interpreter (ICI_TEST_PYTHON).
    "python3": PYTHON,
}
OUT = ROOT / "tests/rust-parity/python/legacy-report-keys-oracle.json"
KEYS = ("support_matrix", "cache_hit", "cache_key", "artifact_manifests", "tool_evidence")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

BASE = "[project]\nsource_dirs=['src']\n"
T = "type='python'\n"
PY = {"src/a.py": "import os\n\n\ndef f(x: int) -> int:\n    return x\n"}
HEADER = {**PY, "src/b.hpp": "int g();\n"}
# name: (commands, files, extra configuration, whether PATH holds the tools)
CASES = {
    "python": (
        (
            "line",
            "lint",
            "type",
            "complexity",
            "cognitive",
            "cycle",
            "dup",
            "dead",
            "exception",
            "security",
            "resource",
        ),
        PY,
        T,
        True,
    ),
    "ruff-mypy-required": (
        ("lint", "type"),
        PY,
        T + "[engines.lint]\nruff_required=true\n[engines.type]\nmypy_required=true\n",
        True,
    ),
    "deep-profile": (("cognitive", "line"), PY, T + "[ici]\nprofile='deep'\n", True),
    "fast-profile": (("complexity",), PY, T + "[ici]\nprofile='fast'\n", True),
    "disabled-engine": (("dup",), PY, T + "[engines.dup]\nenabled=false\n", True),
    "configured-hybrid": (("line",), PY, "type='hybrid'\n", True),
    "qt-package": (
        ("line",),
        HEADER,
        "type='hybrid'\ncpp_pkg_config=['Qt5Core']\n",
        True,
    ),
    "cmake-qt": (
        ("line",),
        {**PY, "CMakeLists.txt": "find_package( Qt6 COMPONENTS Core)\n"},
        "",
        True,
    ),
    "lint-without-ruff": (("lint",), PY, T, False),
}
VERIFY = {
    "verify/fast": (PY, T, "fast"),
    "verify/standard-configured-deep": (PY, T + "[ici]\nprofile='deep'\n", "fast"),
}


# A Python release build under verify (frozen BuildEngine publishes no artifact manifest for it;
# only compiled C++ build sessions do).
BUILD = {
    "files": {
        "pyproject.toml": "[project]\nname = 'pkg'\nversion = '2.3.4'\n",
        "src/app/__init__.py": "",
        "src/app/main.py": "def run():\n    return 0\n",
    },
    "config": T
    + "[ici]\nprofile = 'deep'\n"
    + "".join(
        f"[engines.{engine}]\nenabled = false\n"
        for engine in ("line", "lint", "test", "type", "sanitize")
    )
    + "[engines.build]\nenabled = true\n[build.python]\nentrypoint = 'app.main:run'\n",
}


def write(project: Path, files: dict) -> None:
    for name, text in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def normalize(value, tools: Path, project: Path):
    text = json.dumps(value)
    text = text.replace(str(tools), "<tools>").replace(str(project), "<project>")
    return json.loads(text)


def keys(engine: dict, tools: Path, project: Path) -> dict:
    picked = {key: engine.get(key, "<absent>") for key in KEYS}
    picked["status"] = engine.get("status")
    picked["evidence"] = engine.get("evidence")
    picked["language_evidence"] = engine.get("extra", {}).get("language_evidence")
    return normalize(picked, tools, project)


def prepare(files: dict, config: str, tools: bool) -> tuple[Path, Path, Path, str]:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-report-keys-"))
    bin_dir = work / "bin"
    bin_dir.mkdir()
    path = "/usr/bin:/bin"
    if tools:
        for name, target in TOOLS.items():
            (bin_dir / name).symlink_to(target)
        path = f"{bin_dir}:{path}"
    project = work / "project"
    project.mkdir()
    (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
    write(project, files)
    return work, bin_dir, project, path


def oracle(project: Path, path: str, work: Path, *args: str) -> int:
    return subprocess.run(
        [PYTHON, "-m", "ici", *args],
        cwd=project,
        env={"PATH": path, "HOME": str(work), "PYTHONPATH": str(ORACLE / "src")},
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    ).returncode


def single(command: str, files: dict, config: str, tools: bool) -> dict:
    work, bin_dir, project, path = prepare(files, config, tools)
    try:
        code = oracle(project, path, work, command, "--report")
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
        return {"exit_code": code, **keys(report, bin_dir, project)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify(files: dict, config: str, profile: str) -> dict:
    """Two cached runs (the report moved out of the tree between them) and one --no-cache run."""
    work, bin_dir, project, path = prepare(files, config, True)
    runs = {}
    try:
        for label, extra in (("first", ()), ("second", ()), ("no-cache", ("--no-cache",))):
            code = oracle(project, path, work, "verify", "--report", "--profile", profile, *extra)
            report_path = project / "verify_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report_path.unlink()
            runs[label] = {
                "exit_code": code,
                "support_matrix": normalize(report.get("support_matrix"), bin_dir, project),
                "results": {
                    result["engine_name"]: keys(result, bin_dir, project)
                    for result in report["results"]
                },
            }
        return runs
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    cases = {}
    for name, (commands, files, config, tools) in CASES.items():
        for command in commands:
            cases[f"{name}/{command}"] = {
                "command": command,
                "files": files,
                "config": config,
                "tools": tools,
                "frozen": single(command, files, config, tools),
            }
    verify_cases = {
        name: {
            "files": files,
            "config": config,
            "profile": profile,
            "frozen": verify(files, config, profile),
        }
        for name, (files, config, profile) in VERIFY.items()
    }
    work, bin_dir, project, path = prepare(BUILD["files"], BUILD["config"], True)
    try:
        code = oracle(project, path, work, "verify", "--report", "--no-cache")
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
        build = {
            "files": BUILD["files"],
            "config": BUILD["config"],
            "frozen": {
                "exit_code": code,
                "results": {
                    result["engine_name"]: keys(result, bin_dir, project)
                    for result in report["results"]
                },
                "context_manifests": report["analysis_context"]["artifact_manifests"],
            },
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
    versions = {
        name: subprocess.check_output([target, "--version"], text=True).strip()
        for name, target in TOOLS.items()
    }
    OUT.write_text(
        json.dumps(
            {
                "oracle": EXPECTED,
                "python": subprocess.check_output([PYTHON, "--version"], text=True).strip(),
                "base": BASE,
                "tool_versions": versions,
                "cases": cases,
                "verify": verify_cases,
                "build": build,
            },
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} single-engine cases, {len(verify_cases)} verify cases", file=sys.stderr)


if __name__ == "__main__":
    main()
