"""Development-only frozen C++ legacy CLI captures; never called by Rust.

Section 1: the `ici verify` engine-key matrix (profile, enabled, mode, required).
Section 2: every frozen single-engine command, `ici analyze` and `ici next verify` on the same
legacy-scope fixture.

Usage: capture_verify_matrix.py [--compare <ici binary>]
Without arguments it writes verify-matrix-oracle.json from the frozen CLI. With --compare it
prints the frozen/native per-engine differences of each matrix case instead (exploration only).
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
FINDING_FIELDS = (
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "tool_name",
    "tool_rule_id",
    "explanation",
    "remediation",
    "related_locations",
    "fixes",
    "metrics",
    "snippet",
)
FIXTURE = ROOT / "parity-fixtures/cpp/legacy-scope-fixture"
# A fixed project path keeps every capture byte-identical between runs.
WORK = Path(tempfile.gettempdir()) / "ici-cpp-verify-matrix-capture"
# Every frozen single-engine command (`__main__._ENGINE_COMMANDS`); each writes
# `<command with _>_report.json`.
COMMANDS = (
    "line",
    "type",
    "resource",
    "security",
    "python-compat",
    "thread-sanitize",
    "dup",
    "dead",
    "cognitive",
    "complexity",
    "cycle",
    "exception",
    "test",
    "sanitize",
    "lint",
)
NEXT_CONFIG = (
    'schema_version = 1\n[workspace]\nname = "project"\n\n'
    '[[components]]\nid = "src"\nroot = "src"\nlanguages = ["cpp"]\n'
)
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


def execute(
    arguments: list[str],
    config: str | None,
    output: str,
    binary: str | None = None,
    files: dict[str, str] | None = None,
) -> tuple[int, dict]:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
        for name, text in (files or {}).items():
            (project / name).write_text(text, encoding="utf-8")
        if config is not None:
            (project / "ici.toml").write_text(config, encoding="utf-8")
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(WORK)}
        if binary is None:
            argv = [PYTHON, "-m", "ici", *arguments]
            environment |= {"PYTHONPATH": str(ORACLE / "src"), "PYTHONHASHSEED": "0"}
        else:
            argv = [binary, *arguments]
        result = subprocess.run(
            argv,
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        report = json.loads((project / output).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return result.returncode, report


def legacy_finding(finding: dict) -> dict:
    row = {key: finding.get(key) for key in FINDING_FIELDS}
    row["primary_location"] = finding["primary_location"]
    row["fingerprint"] = finding["fingerprint"]
    return row


def legacy_engine(engine: dict) -> dict:
    return {
        "engine_name": engine["engine_name"],
        "status": engine["status"],
        "required": engine["required"],
        "evidence": engine.get("evidence"),
        "summary": engine.get("summary"),
        "score": engine.get("score"),
        "max_score": engine.get("max_score"),
        "targets": [{key: target.get(key) for key in FIELDS} for target in engine["targets"]],
        "findings": [legacy_finding(finding) for finding in engine.get("findings", [])],
        # Recorded for review only; the native report carries its own extra keys.
        "extra_keys": sorted(engine.get("extra", {})),
    }


# A root compile_commands.json naming every production translation unit (relative directory).
UNITS = ("src/extra.cc", "src/main.cpp", "src/util.cpp")
FULL_DATABASE = (
    "["
    + ", ".join(
        f'{{"directory": ".", "file": "{unit}", "arguments": ["g++", "-std=c++17", "-c", "{unit}"]}}'
        for unit in UNITS
    )
    + "]\n"
)
DATABASE_CASES = {
    "discovered": "",
    "flag_policy": (
        "[engines.compile_db]\nrequired_flags=['-Wall']\nforbidden_flags=['-std=c++17']\n"
    ),
}


def run(config: str, binary: str | None = None, database: bool = False) -> dict:
    exit_code, report = execute(
        ["verify", "--report"],
        BASE + config,
        "verify_report.json",
        binary,
        {"compile_commands.json": FULL_DATABASE} if database else {},
    )
    return {
        "exit_code": exit_code,
        "suite_status": report["suite_status"],
        "engines": [legacy_engine(engine) for engine in report["results"]],
    }


def single(command: str) -> dict:
    output = command.replace("-", "_") + "_report.json"
    exit_code, report = execute([command, "--report"], BASE, output)
    return {"exit_code": exit_code, **legacy_engine(report)}


def next_run(arguments: list[str], config: str | None) -> dict:
    exit_code, result = execute([*arguments, "--json"], config, ".ici/next/result.json")
    return {
        "arguments": arguments,
        "exit_code": exit_code,
        "checks": [
            {key: check.get(key) for key in ("task_id", "check_id", "state", "evidence_complete")}
            for check in result["checks"]
        ],
        "findings": [
            {
                key: finding.get(key)
                for key in (
                    "task_id",
                    "rule_id",
                    "message",
                    "severity",
                    "confidence",
                    "evidence",
                    "primary_location",
                    "related_locations",
                    "fingerprint",
                )
            }
            for finding in result["findings"]
        ],
        "gate": result["gate"],
        "execution": result["execution"],
        "metrics": result["metrics"],
        "limitations": result["limitations"],
    }


DATABASE = (
    '[{"directory": ".", "file": "src/main.cpp", '
    '"arguments": ["g++", "-std=c++17", "-c", "src/main.cpp", "-o", "main.o"]}]\n'
)
EXPORTS = {
    "no_database": ({}, []),
    "explicit_database": (
        {"compile_commands.json": DATABASE},
        ["--database", "compile_commands.json"],
    ),
    "malformed_database": (
        {"compile_commands.json": "{\n"},
        ["--database", "compile_commands.json"],
    ),
    "missing_database": ({}, ["--database", "absent.json"]),
}


def export(files: dict[str, str], options: list[str]) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
        (project / "ici.toml").write_text(BASE, encoding="utf-8")
        for name, text in files.items():
            (project / name).write_text(text, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", "export-compilation-context", *options, "--output", "-"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    payload = json.loads(result.stdout) if result.returncode == 0 else None
    return {
        "files": files,
        "options": options,
        "exit_code": result.returncode,
        "payload": payload,
        "stderr": result.stderr.strip(),
    }


PYTHON_BASE = "[project]\ntype='python'\nsource_dirs=['src']\n"
PYTHON_SOURCE = "def f(x):\n    return x + 1\n"


def python_suite(profile: str) -> dict:
    """A legacy suite with no C/C++ source still lists compile_db and thread_sanitize."""
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        (project / "src/pkg").mkdir(parents=True)
        (project / "src/pkg/__init__.py").write_text(PYTHON_SOURCE, encoding="utf-8")
        (project / "ici.toml").write_text(PYTHON_BASE, encoding="utf-8")
        subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--profile", profile, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "engine_names": [engine["engine_name"] for engine in report["results"]],
        "engines": [
            legacy_engine(engine)
            for engine in report["results"]
            if engine["engine_name"] in {"compile_db", "thread_sanitize"}
        ],
    }


def legacy_refusal(arguments: list[str]) -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
        (project / "ici.toml").write_text(BASE, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", *arguments, "--json"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert not (project / ".ici").exists()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return result.returncode


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
        database_cases = {name: run(config, None, True) for name, config in DATABASE_CASES.items()}
        commands = {command: single(command) for command in COMMANDS}
        # `analyze` needs no config (a legacy ici.toml is refused); `next verify` needs a root.
        next_cases = {
            "analyze": next_run(["analyze"], None),
            "analyze_legacy_config": {"exit_code": legacy_refusal(["analyze"])},
            "next_verify": next_run(["next", "verify"], NEXT_CONFIG),
        }
        exports = {name: export(*case) for name, case in EXPORTS.items()}
        python_suites = {profile: python_suite(profile) for profile in ("fast", "standard", "deep")}
        output = ROOT / "tests/rust-parity/cpp/verify-matrix-oracle.json"
        gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
        document = {
            "oracle": EXPECTED,
            "gxx": gxx,
            "base": BASE,
            "configs": CASES,
            "cases": cases,
            "database": FULL_DATABASE,
            "database_configs": DATABASE_CASES,
            "database_cases": database_cases,
            "commands": commands,
            "next_config": NEXT_CONFIG,
            "next": next_cases,
            "exports": exports,
            "python_base": PYTHON_BASE,
            "python_source": PYTHON_SOURCE,
            "python_suites": python_suites,
        }
        output.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{len(cases)} cases, {len(commands)} commands, {len(next_cases)} next -> {output}")
