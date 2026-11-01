"""Development-only frozen `ici verify` engine-key matrix on a C++ project; never called by Rust.

Usage: capture_verify_matrix.py [--compare <ici binary>]
Without arguments it writes verify-matrix-oracle.json from the frozen CLI. With --compare it
prints the frozen/native per-engine differences of each case instead (exploration only).
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
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
FIXTURE = ROOT / "parity-fixtures/cpp/legacy-scope-fixture"
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
CASES = {
    "defaults": "",
    "disabled": (
        "[engines.cycle]\nenabled=false\n[engines.exception]\nenabled=false\n"
        "[engines.type]\nenabled=false\n[engines.sanitize]\nenabled=false\n"
        "[engines.resource]\nenabled=false\n[engines.dup]\nenabled=false\n"
    ),
    "modes": (
        "[engines.exception]\nmode='pass_warn'\n[engines.cycle]\nmode='pass_fail'\n"
        "[engines.lint]\nmode='pass_warn'\n[engines.compile_db]\nmode='pass_fail'\n"
        "[engines.complexity]\nmode='pass_fail'\nwarn_cc=5\n"
    ),
    "modes_warn_fail": (
        "[engines.exception]\nmode='pass_warn_fail'\n[engines.cycle]\nmode='pass_warn'\n"
        "[engines.lint]\nmode='pass_fail'\n[engines.compile_db]\nmode='pass_warn'\n"
        "[engines.complexity]\nmode='pass_warn'\nwarn_cc=5\n"
    ),
    "required_flipped": (
        "[engines.cycle]\nrequired=true\n[engines.exception]\nrequired=false\n"
        "[engines.type]\nrequired=false\n[engines.resource]\nrequired=true\n"
        "[engines.security]\nrequired=true\n[engines.python_compat]\nrequired=true\n"
        "[engines.sanitize]\nrequired=false\n[engines.test]\nrequired=false\n"
        "[engines.lint]\nrequired=false\n[engines.line]\nrequired=false\n"
        "[engines.compile_db]\nrequired=false\n[engines.complexity]\nrequired=false\n"
        "[engines.dup]\nrequired=false\n"
    ),
    "profile_fast": "[ici]\nprofile='fast'\n",
    "profile_deep": "[ici]\nprofile='deep'\n",
}


def run(config: str, binary: str | None = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cpp-verify-matrix-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURE, project)
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(work)}
        if binary is None:
            argv = [PYTHON, "-m", "ici", "verify", "--report"]
            environment |= {"PYTHONPATH": str(ORACLE / "src"), "PYTHONHASHSEED": "0"}
        else:
            argv = [binary, "verify", "--report"]
        result = subprocess.run(
            argv,
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "engines": [
            {
                "engine_name": engine["engine_name"],
                "status": engine["status"],
                "required": engine["required"],
                "evidence": engine.get("evidence"),
                "summary": engine.get("summary"),
                "targets": [
                    {key: target.get(key) for key in FIELDS} for target in engine["targets"]
                ],
            }
            for engine in report["results"]
        ],
    }


def compare(binary: str) -> None:
    for name, config in CASES.items():
        frozen, native = run(config), run(config, binary)
        print(f"== {name}: exit {frozen['exit_code']}/{native['exit_code']}", end=" ")
        print(f"suite {frozen['suite_status']}/{native['suite_status']}")
        left = {engine["engine_name"]: engine for engine in frozen["engines"]}
        right = {engine["engine_name"]: engine for engine in native["engines"]}
        print("  order", list(left) == list(right), list(left), list(right))
        for engine in sorted(set(left) | set(right)):
            a, b = left.get(engine), right.get(engine)
            if a is None or b is None:
                print(f"  {engine}: only {'native' if a is None else 'frozen'}")
                continue
            for key in ("status", "required", "evidence", "targets"):
                if a[key] != b[key]:
                    shown = (len(a[key]), len(b[key])) if key == "targets" else (a[key], b[key])
                    print(f"  {engine}.{key}: {shown}")


if __name__ == "__main__":
    assert (
        subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
        == EXPECTED
    )
    if sys.argv[1:2] == ["--compare"]:
        compare(sys.argv[2])
    else:
        cases = {name: run(config) for name, config in CASES.items()}
        output = ROOT / "tests/rust-parity/cpp/verify-matrix-oracle.json"
        gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
        document = {
            "oracle": EXPECTED,
            "gxx": gxx,
            "base": BASE,
            "configs": CASES,
            "cases": cases,
        }
        output.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{len(cases)} cases -> {output}")
