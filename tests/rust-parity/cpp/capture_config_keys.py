"""Development-only frozen legacy C++ config-key captures; never called by Rust.

Each case names the closure rows it discriminates and a (reference, variant) pair of legacy
configurations over the same fixture and command. Every distinct run is captured once from the
frozen CLI; `effect` records whether the variant changed the frozen engine view. A case marked
`expect_effect` must change it (the key is consumed by the C++ engine) and a case marked
otherwise must not (the key has no observable C++ effect), so acceptance alone never counts as
proof. The Rust replay (crates/ici-cli/tests/legacy_cpp_config_keys.rs) runs the same runs.
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
# A fixed project path keeps every capture byte-identical between runs.
WORK = Path(tempfile.gettempdir()) / "ici-cpp-config-keys-capture"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
FINDING_FIELDS = (
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "tool_name",
    "tool_rule_id",
    "primary_location",
    "fingerprint",
)
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
DEEP = "[ici]\nprofile='deep'\n"
# Two translation units sharing one 14-line function body: a C++ clone group for dup.
CLONE = (
    "int clone_body(int a, int b) {\n"
    "    int total = 0;\n"
    "    for (int i = 0; i < a; ++i) {\n"
    "        if (i % 2 == 0) {\n"
    "            total += i * b;\n"
    "        } else {\n"
    "            total -= i;\n"
    "        }\n"
    "        if (total > 1000) {\n"
    "            total = 1000;\n"
    "        }\n"
    "    }\n"
    "    return total;\n"
    "}\n"
)
# A root compile_commands.json naming every production unit, with a compiling extra.cc, lets
# the exact unused-function scope complete (the verify-matrix `unused_clean` case).
UNITS = ("src/extra.cc", "src/main.cpp", "src/util.cpp")
DATABASE = (
    "["
    + ", ".join(
        f'{{"directory": ".", "file": "{unit}", "arguments": ["g++", "-std=c++17", "-c", "{unit}"]}}'
        for unit in UNITS
    )
    + "]\n"
)
FILES = {
    "clones": {"src/clone_a.cpp": CLONE, "src/clone_b.cpp": CLONE.replace("clone_body", "twin")},
    "database": {
        "compile_commands.json": DATABASE,
        "src/extra.cc": (FIXTURES / "legacy-scope-fixture/src/extra.cc")
        .read_text(encoding="utf-8")
        .replace("~Guard()", "~CcGuard()"),
    },
}
DISABLED = "".join(
    f"[engines.{name}]\nenabled=false\n"
    for name in (
        "cognitive",
        "compile_db",
        "complexity",
        "dead",
        "lint",
        "python_compat",
        "security",
        "test",
        "thread_sanitize",
    )
)
NO_CPP_MODES = (
    "[engines.python_compat]\nmode='pass_fail'\n[engines.security]\nmode='pass_fail'\n"
    "[engines.resource]\nmode='pass_fail'\n[engines.type]\nmode='pass_fail'\n"
    "[engines.sanitize]\nmode='pass_warn'\n[engines.thread_sanitize]\nmode='pass_warn'\n"
    "[engines.dup]\npython_semantic='off'\n[engines.lint]\nruff_required=true\n"
    "[engines.test]\npython='python3'\n"
)
BUILD = "[engines.build]\nenabled=true\n"
# name: rows, fixture, command, files, reference config, variant config, expected effect
CASES = {
    "dup-warn-pct": {
        "rows": ["config_key:legacy:engines.dup.warn_pct"],
        "command": "dup",
        "files": "clones",
        "reference": "",
        "variant": "[engines.dup]\nwarn_pct=60.0\nfail_pct=60.0\n",
        "expect_effect": True,
    },
    "dup-fail-pct": {
        "rows": ["config_key:legacy:engines.dup.fail_pct"],
        "command": "dup",
        "files": "clones",
        "reference": "[engines.dup]\nmode='pass_warn_fail'\nfail_pct=60.0\n",
        "variant": "[engines.dup]\nmode='pass_warn_fail'\nfail_pct=6.0\n",
        "expect_effect": True,
    },
    "dup-mode": {
        "rows": ["config_key:legacy:engines.dup.mode"],
        "command": "dup",
        "files": "clones",
        "reference": "[engines.dup]\nfail_pct=6.0\n",
        "variant": "[engines.dup]\nfail_pct=6.0\nmode='pass_warn_fail'\n",
        "expect_effect": True,
    },
    "dup-min-window": {
        "rows": ["config_key:legacy:engines.dup.min_window"],
        "command": "dup",
        "files": "clones",
        "reference": "",
        "variant": "[engines.dup]\nmin_window=40\n",
        "expect_effect": True,
    },
    "cognitive-mode": {
        "rows": ["config_key:legacy:engines.cognitive.mode"],
        "command": "cognitive",
        "files": None,
        "reference": "[engines.cognitive]\nwarn=3\nfail=100\n",
        "variant": "[engines.cognitive]\nwarn=3\nfail=100\nmode='pass_fail'\n",
        "expect_effect": True,
    },
    "cognitive-boundaries-required": {
        "rows": ["config_key:legacy:engines.cognitive.cpp_boundaries"],
        "command": "cognitive",
        "files": None,
        "reference": "",
        "variant": "[engines.cognitive]\ncpp_boundaries='required'\n",
        "expect_effect": True,
    },
    "cognitive-boundaries-off": {
        "rows": ["config_key:legacy:engines.cognitive.cpp_boundaries"],
        "command": "cognitive",
        "files": None,
        "reference": "",
        "variant": "[engines.cognitive]\ncpp_boundaries='off'\n",
        "expect_effect": False,
    },
    "complexity-boundaries-required": {
        "rows": ["config_key:legacy:engines.complexity.cpp_boundaries"],
        "command": "complexity",
        "files": None,
        "reference": "",
        "variant": "[engines.complexity]\ncpp_boundaries='required'\n",
        "expect_effect": True,
    },
    "complexity-boundaries-off": {
        "rows": ["config_key:legacy:engines.complexity.cpp_boundaries"],
        "command": "complexity",
        "files": None,
        "reference": "",
        "variant": "[engines.complexity]\ncpp_boundaries='off'\n",
        "expect_effect": False,
    },
    "dead-linker-required": {
        "rows": ["config_key:legacy:engines.dead.cpp_linker"],
        "command": "dead",
        "files": None,
        "reference": "",
        "variant": "[engines.dead]\ncpp_linker='required'\n",
        "expect_effect": True,
    },
    "dead-linker-auto": {
        "rows": ["config_key:legacy:engines.dead.cpp_linker"],
        "command": "dead",
        "files": None,
        "reference": "",
        "variant": "[engines.dead]\ncpp_linker='auto'\n",
        "expect_effect": True,
    },
    "dead-mode": {
        "rows": ["config_key:legacy:engines.dead.mode"],
        "command": "dead",
        "files": "database",
        "reference": "",
        "variant": "[engines.dead]\nmode='pass_fail'\n",
        "expect_effect": True,
    },
    "dead-required": {
        "rows": ["config_key:legacy:engines.dead.required"],
        "command": "dead",
        "files": "database",
        "reference": "",
        "variant": "[engines.dead]\nrequired=false\n",
        "expect_effect": True,
    },
    "verify-enabled": {
        "rows": [
            f"config_key:legacy:engines.{name}.enabled"
            for name in (
                "cognitive",
                "compile_db",
                "complexity",
                "dead",
                "lint",
                "python_compat",
                "security",
                "test",
                "thread_sanitize",
            )
        ],
        "command": "verify",
        "files": None,
        "reference": DEEP,
        "variant": DEEP + DISABLED,
        "expect_effect": True,
    },
    "verify-required": {
        "rows": ["config_key:legacy:engines.cognitive.required"],
        "command": "verify",
        "files": None,
        "reference": DEEP,
        "variant": DEEP + "[engines.cognitive]\nrequired=true\n",
        "expect_effect": True,
    },
    "verify-no-cpp-effect": {
        "rows": [
            "config_key:legacy:engines.python_compat.mode",
            "config_key:legacy:engines.security.mode",
            "config_key:legacy:engines.resource.mode",
            "config_key:legacy:engines.type.mode",
            "config_key:legacy:engines.dup.python_semantic",
            "config_key:legacy:engines.lint.ruff_required",
            "config_key:legacy:engines.test.python",
        ],
        "command": "verify",
        "files": None,
        "reference": DEEP,
        "variant": DEEP + NO_CPP_MODES,
        "expect_effect": False,
    },
    "verify-build-enabled": {
        "rows": ["config_key:legacy:engines.build", "config_key:legacy:engines.build.enabled"],
        "command": "verify",
        "files": None,
        "reference": DEEP,
        "variant": DEEP + BUILD,
        "expect_effect": True,
    },
    "verify-build-mode": {
        "rows": ["config_key:legacy:engines.build.mode"],
        "command": "verify",
        "files": None,
        "reference": DEEP + BUILD,
        "variant": DEEP + BUILD + "mode='pass_warn'\n",
        "expect_effect": True,
    },
    "verify-build-required": {
        "rows": ["config_key:legacy:engines.build.required"],
        "command": "verify",
        "files": None,
        "reference": DEEP + BUILD,
        "variant": DEEP + BUILD + "required=true\n",
        "expect_effect": True,
    },
}


def engine_view(engine: dict) -> dict:
    return {
        "engine_name": engine["engine_name"],
        "status": engine["status"],
        "required": engine["required"],
        "evidence": engine.get("evidence"),
        "summary": engine.get("summary"),
        "score": engine.get("score"),
        "max_score": engine.get("max_score"),
        "targets": [{key: target.get(key) for key in FIELDS} for target in engine["targets"]],
        "findings": [
            {key: finding.get(key) for key in FINDING_FIELDS}
            for finding in engine.get("findings", [])
        ],
    }


def execute(command: str, files: str | None, config: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / "legacy-scope-fixture", project)
        for name, text in FILES.get(files or "", {}).items():
            (project / name).write_text(text, encoding="utf-8")
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        name = command.replace("-", "_")
        report = json.loads((project / f"{name}_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    if command == "verify":
        return {
            "exit_code": result.returncode,
            "suite_status": report["suite_status"],
            "engines": [engine_view(engine) for engine in report["results"]],
        }
    return {"exit_code": result.returncode, "engines": [engine_view(report)]}


def run_id(command: str, files: str | None, config: str) -> str:
    return json.dumps([command, files, config])


if __name__ == "__main__":
    assert (
        subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
        == EXPECTED
    )
    runs: dict[str, dict] = {}
    for case in CASES.values():
        for side in ("reference", "variant"):
            key = run_id(case["command"], case["files"], case[side])
            if key not in runs:
                runs[key] = execute(case["command"], case["files"], case[side])
    cases = {}
    unexpected = []
    for name, case in CASES.items():
        reference = runs[run_id(case["command"], case["files"], case["reference"])]
        variant = runs[run_id(case["command"], case["files"], case["variant"])]
        effect = reference != variant
        if effect != case["expect_effect"]:
            unexpected.append(f"{name}: frozen effect {effect}")
        cases[name] = {**case, "effect": effect}
    gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
    output = ROOT / "tests/rust-parity/cpp/config-keys-oracle.json"
    document = {
        "oracle": EXPECTED,
        "gxx": gxx,
        "base": BASE,
        "files": FILES,
        "cases": cases,
        "runs": [{"id": json.loads(key), "frozen": value} for key, value in runs.items()],
    }
    output.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(cases)} cases, {len(runs)} frozen runs -> {output}")
    assert not unexpected, unexpected
