"""Development-only frozen `ici doctor --json` captures; never called by the Rust product.

Runs the immutable v0.13.0 historical doctor over legacy-schema and configless projects and
records the support matrix, the derived tool policy (frozen core/capabilities.py
`derive_tool_policy`) and every tool row the policy names. The project tools (the opus-env
ICI_TEST_RUFF / ICI_TEST_MYPY) are linked into one PATH directory so frozen `shutil.which` and
the native resolver find the same files.
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
}
OUT = ROOT / "tests/rust-parity/doctor/legacy-doctor-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

PY = "[project]\ntype='python'\nsource_dirs=['src']\n"
CPP = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
SOURCE = {"src/a.py": "x = 1\n"}
CPP_SOURCE = {"src/a.cpp": "int main() { return 0; }\n"}
# Stub executables of the candidate cases (frozen `_probe_registered_candidates`): a slow but
# complete first candidate (2 s, inside frozen's 5 s), a first candidate without a parseable
# version, a probe that cannot start (bad interpreter: frozen's -1), and one file reached by both
# qmake names that fails only under its first name (frozen probes a real path once).
SLOW_CLAZY = "#!/bin/sh\nsleep 2\necho 'clazy-standalone version 1.12'\n"
CLAZY = "#!/bin/sh\necho 'clazy version 1.13'\n"
UNPARSEABLE_QMAKE6 = "#!/bin/sh\necho 'QMake build'\n"
QMAKE = (
    "#!/bin/sh\n"
    'case "$(basename "$0")" in qmake6) exit 1;; esac\n'
    'if [ "$1" = "-query" ]; then echo QT_VERSION:5.15.13; else echo "QMake version 3.1"; fi\n'
)
UNSTARTABLE = "#!/nonexistent/ici-interpreter\n"
REGISTRY_TOOLS = (
    "[doctor]\nrequired_tools=['clazy', 'qmake', 'ld', 'qt5', ' g++ ', 'pkg-config', "
    "'ici-unregistered-tool', 'pytest']\n"
)
# name: (configuration text or None for no ici.toml, files[, working subdirectory[, stubs]])
CASES = {
    "subdirectory-below-legacy-root": (
        PY + "[engines.type]\nmypy_required=true\n",
        {**SOURCE, "flat/b.py": "y = 1\n"},
        "flat",
    ),
    "python-default": (PY, SOURCE),
    "python-policies": (
        PY + "[engines.type]\nmypy_required=true\n[engines.lint]\nruff_required=true\n"
        "[engines.test]\ncoverage_required=true\n",
        SOURCE,
    ),
    "python-mypy-required": (PY + "[engines.type]\nmypy_required=true\n", SOURCE),
    "python-mypy-required-type-disabled": (
        PY + "[engines.type]\nenabled=false\nmypy_required=true\n",
        SOURCE,
    ),
    "python-profile-deep": (PY + "[ici]\nprofile='deep'\n", SOURCE),
    "python-profile-fast": (PY + "[ici]\nprofile='fast'\n", SOURCE),
    "python-doctor-required-tools": (
        PY + "[doctor]\nrequired_tools=['git', 'ici-sweep-missing-tool']\n",
        SOURCE,
    ),
    "configless-flat": (None, {"a.py": "x = 1\n"}),
    "configless-src": (None, SOURCE),
    "doctor-required-registry-candidates": (
        PY + REGISTRY_TOOLS,
        SOURCE,
        ".",
        {
            "clazy-standalone": SLOW_CLAZY,
            "clazy": CLAZY,
            "qmake6": UNPARSEABLE_QMAKE6,
            "qmake": QMAKE,
            "ld": UNSTARTABLE,
        },
    ),
    "doctor-required-same-file-candidates": (
        PY + "[doctor]\nrequired_tools=['qmake']\n",
        SOURCE,
        ".",
        {"qmake": QMAKE, "qmake6": "@qmake"},
    ),
    "configless-pyproject": (None, {"pyproject.toml": "[project]\nname='p'\n"}),
    "cpp-policies": (
        CPP + "[engines.lint]\nclang_tidy='required'\nclazy='off'\n"
        "[engines.dead]\ncpp_linker='required'\n",
        CPP_SOURCE,
    ),
    "cpp-unused-off": (CPP + "[engines.dead]\ncpp_unused='off'\n", CPP_SOURCE),
    "hybrid-qt": (
        "[project]\ntype='hybrid'\nsource_dirs=['src']\n",
        {
            **SOURCE,
            **CPP_SOURCE,
            "CMakeLists.txt": "project(p)\nfind_package(Qt5 COMPONENTS Core)\n",
        },
    ),
}
ROW_KEYS = ("available", "complete", "error", "required", "optional", "required_by", "optional_by")


def run(config: str | None, files: dict, cwd: str = ".", stubs: dict | None = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-doctor-"))
    try:
        bin_dir = work / "bin"
        bin_dir.mkdir()
        for name, target in TOOLS.items():
            (bin_dir / name).symlink_to(target)
        for name, script in (stubs or {}).items():
            if script.startswith("@"):
                (bin_dir / name).symlink_to(bin_dir / script[1:])
            else:
                (bin_dir / name).write_text(script, encoding="utf-8")
                (bin_dir / name).chmod(0o755)
        project = work / "project"
        project.mkdir()
        if config is not None:
            (project / "ici.toml").write_text(config, encoding="utf-8")
        for name, content in files.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", "doctor", "--json"],
            cwd=project / cwd,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    data = json.loads(result.stdout)
    rows = {
        name: {key: row.get(key) for key in ROW_KEYS}
        | {"resolved_alias": row.get("details", {}).get("resolved_alias")}
        for name, row in data["tools"].items()
        if row.get("required") or row.get("optional")
    }
    return {
        "exit_code": result.returncode,
        "required_tools": data["required_tools"],
        "effective_required_tools": data["effective_required_tools"],
        "analysis_profile": data["analysis_profile"],
        "support_matrix": data["support_matrix"],
        "policy_tools": rows,
        "capability_status": data["capability_inventory"].get("status"),
    }


def main() -> None:
    cases = {
        name: {
            "config": spec[0],
            "files": spec[1],
            "cwd": spec[2] if len(spec) > 2 else ".",
            "stubs": spec[3] if len(spec) > 3 else {},
            "frozen": run(*spec),
        }
        for name, spec in CASES.items()
    }
    python = subprocess.check_output([PYTHON, "--version"], text=True).strip()
    versions = {
        name: subprocess.check_output([target, "--version"], text=True).strip()
        for name, target in TOOLS.items()
    }
    OUT.write_text(
        json.dumps(
            {"oracle": EXPECTED, "python": python, "tool_versions": versions, "cases": cases},
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} frozen doctor cases with {python}, {versions}", file=sys.stderr)


if __name__ == "__main__":
    main()
