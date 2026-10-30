"""Capture frozen v0.13.0 legacy configuration verdicts for the native replay.

Run with the frozen oracle tree on PYTHONPATH:

    PYTHONPATH=<oracle>/src python capture_legacy_schema.py <oracle-root> <output.json>

Each case is one project ``ici.toml`` with no other configuration layer
(``XDG_CONFIG_HOME`` points at an empty directory and ``ICI_CONFIG`` is unset).
The frozen ``load_config`` result is recorded as the accepted effective value at
the case pointer, or as the rejected message and its dotted key.
"""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import tomli_w

COMMON = {"enabled", "mode", "required"}
ENGINE_DEFAULT_MODES = {
    "build": "pass_warn_fail",
    "line": "pass_warn_fail",
    "lint": "pass_warn_fail",
    "compile_db": "pass_warn_fail",
    "test": "pass_fail",
    "type": "pass_warn",
    "python_compat": "pass_warn_fail",
    "complexity": "pass_warn_fail",
    "sanitize": "pass_fail",
    "thread_sanitize": "pass_fail",
    "dead": "pass_warn",
    "dup": "pass_warn",
    "exception": "pass_fail",
    "cycle": "pass_warn_fail",
    "cognitive": "pass_warn",
    "security": "pass_warn",
    "resource": "pass_warn",
    "binary_compat": "pass_warn_fail",
    "integration": "pass_warn_fail",
}
CASE = {"name": "smoke", "argv": ["{python:py}", "-V"]}


def nest(path, value):
    """Build a nested table from a dotted path (no list segments)."""
    document = value
    for part in reversed(path.split(".")):
        document = {part: document}
    return document


def merged(*documents):
    result = {}
    for document in documents:
        stack = [(result, document)]
        while stack:
            target, source = stack.pop()
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    stack.append((target[key], value))
                else:
                    target[key] = copy.deepcopy(value)
    return result


def pointer(path):
    parts = []
    for part in path.split("."):
        match = re.fullmatch(r"(\w+)((?:\[\d+\])*)", part)
        parts.append(match.group(1))
        parts.extend(re.findall(r"\[(\d+)\]", match.group(2)))
    return "/" + "/".join(parts)


def scalar_cases(key, positive, failures, extra=None):
    """One positive and several failure cases for a dotted key."""
    base = extra or {}
    cases = [(f"{key}:positive", key, merged(base, nest(key, positive)), key)]
    for index, value in enumerate(failures):
        cases.append((f"{key}:failure{index}", key, merged(base, nest(key, value)), key))
    return cases


def integration(case_fields, key):
    """A single integration case whose fields replace the minimal valid case."""
    case = dict(CASE)
    case.update(case_fields)
    document = {"engines": {"integration": {"python_targets": {"py": "python3"}, "cases": [case]}}}
    return document, key


def integration_cases():
    prefix = "engines.integration.cases[]"
    specs = {
        "name": ("release-smoke", ["", "x" * 129, 7]),
        "argv": (
            ["{python:py}", "-m", "pkg.cli"],
            [[], ["python3", "-V"], ["{python:py}", "{bad"]],
        ),
        "expected_exit": (3, [2**31, "1"]),
        "inherit_env": (["PATH"], [["1BAD"], "PATH", ["PATH", "PATH"]]),
        "env": ({"FOO": "1"}, [{"1BAD": "x"}, {"FOO": 1}]),
        "output_artifacts": ([{"path": "out/a.txt"}], ["x", [{"path": "../x"}]]),
        "required": (False, ["yes"]),
        "stdout_contains": (["ok"], [[""], "ok", ["ok", "ok"]]),
        "stderr_contains": (["warn"], [[""]]),
        "stdout_not_contains": (["Traceback"], [[1]]),
        "stderr_not_contains": (["error"], [["a\nb"]]),
        "timeout_seconds": (5.5, [0.05, 301, "5"]),
    }
    cases = []
    for field, (positive, failures) in specs.items():
        key = f"{prefix}.{field}"
        path = f"engines.integration.cases[0].{field}"
        document, _ = integration({field: positive}, key)
        cases.append((f"{key}:positive", key, document, path))
        for index, value in enumerate(failures):
            document, _ = integration({field: value}, key)
            cases.append((f"{key}:failure{index}", key, document, path))
    outputs = f"{prefix}.output_artifacts[]"
    out_specs = {
        "path": ("reports/out.json", [".", "../x", "/abs", ""]),
        "kind": ("json", ["", "k" * 129]),
        "min_size": (1, [-1, 64 * 1024 * 1024 + 1, "1"]),
    }
    for field, (positive, failures) in out_specs.items():
        key = f"{outputs}.{field}"
        path = f"engines.integration.cases[0].output_artifacts[0].{field}"
        for index, value in enumerate([positive, *failures]):
            artifact = {"path": "reports/out.json", field: value}
            document, _ = integration({"output_artifacts": [artifact]}, key)
            suffix = "positive" if index == 0 else f"failure{index - 1}"
            cases.append((f"{key}:{suffix}", key, document, path))
    document, _ = integration(
        {"output_artifacts": [{"path": "a.txt"}, {"path": "a.txt"}]}, f"{outputs}.path"
    )
    cases.append(
        (f"{outputs}.path:duplicate", f"{outputs}.path", document, "engines.integration.cases")
    )
    duplicate = {
        "engines": {
            "integration": {"python_targets": {"py": "python3"}, "cases": [CASE, dict(CASE)]}
        }
    }
    cases.append((f"{prefix}.name:duplicate", f"{prefix}.name", duplicate, "engines.integration"))
    unknown, _ = integration({"bogus": 1}, prefix)
    cases.append((f"{prefix}:unknown", "engines.integration.cases", unknown, prefix))
    unknown_output, _ = integration({"output_artifacts": [{"path": "a", "bogus": 1}]}, outputs)
    cases.append((f"{outputs}:unknown", f"{prefix}.output_artifacts", unknown_output, outputs))
    return cases


def engine_cases():
    cases = []
    for engine, mode in sorted(ENGINE_DEFAULT_MODES.items()):
        base = f"engines.{engine}"
        cases.append((f"{base}:table", base, nest(base, {"enabled": True}), base))
        cases.append((f"{base}:failure0", base, nest(base, 5), base))
        cases.append((f"{base}:unknown", base, nest(f"{base}.bogus", 1), base))
        positive_mode = "pass_fail" if mode != "pass_fail" else "pass_warn"
        cases += scalar_cases(f"{base}.mode", positive_mode, ["strict", 3, ""])
        cases += scalar_cases(f"{base}.enabled", engine in {"build", "binary_compat"}, ["yes", 1])
        cases += scalar_cases(f"{base}.required", True, ["yes", 0])
    specific = {
        "line.warn_limit": (400, [0, "400", 1200]),
        "line.fail_limit": (2000, [0, 100, 1.5]),
        "line.gate_dirs": (["src"], [[""], "src", ["../x"], ["/abs"]]),
        "line.include_dirs": (["lib"], [[1], ["../x"]]),
        "line.exclude_dirs": (["vendor"], [["/abs"], [""]]),
        "lint.ruff_required": (True, ["yes"]),
        "lint.clang_tidy": ("required", ["on", ""]),
        "lint.clang_tidy_checks": (["bugprone-*"], [[], ["a;b"], ["x", "x"], "x"]),
        "lint.clang_tidy_config": (".clang-tidy", ["", "../x", "c" * 4097]),
        "lint.clazy": ("off", ["on"]),
        "lint.clazy_profile": ("level1", ["level2"]),
        "lint.clazy_checks": (["qstring-arg"], [["bad check"], []]),
        "compile_db.database_required": (True, ["yes"]),
        "compile_db.required_flags": (["-Wall"], [[""], "-Wall"]),
        "compile_db.forbidden_flags": (["-w"], [[2]]),
        "test.min_tem_score": (3.5, [6, -1, "4"]),
        "test.min_line_cov": (70, [101, -1, True]),
        "test.min_file_cov": (50.5, [101]),
        "test.min_file_statements": (3, [0, 1.5]),
        "test.min_branch_cov": (60, [101]),
        "test.min_func_cov": (70, [101]),
        "test.min_changed_line_cov": (90, [101, "x"]),
        "test.changed_lines": (["src/a.py"], [[1], [""]]),
        "test.max_coverage_regression": (2.0, [101, -0.5]),
        "test.coverage_required": (True, ["yes"]),
        "test.python": ("python3", ["", " ", 3]),
        "test.quality": ({"enabled": False}, [5, True]),
        "test.quality.enabled": (False, ["yes"]),
        "test.quality.mode": ("warn", ["fail", ""]),
        "test.quality.repeat_runs": (2, [4, 0, 1.5]),
        "test.quality.repeat": (2, [4, 0]),
        "test.quality.timeout": (10, [0.05, 3601, "10"]),
        "test.quality.slow_test_threshold": (2.5, [-1, 86401]),
        "test.quality.slow_threshold": (2.5, [-1]),
        "test.quality.max_slow_tests": (10, [0, 1001]),
        "test.quality.mutation": (True, ["yes", 3]),
        "test.quality.mutation.enabled": (True, ["yes"]),
        "test.quality.mutation.tool": ("mutmut", ["bogus", ""]),
        "test.quality.mutation.command": (
            ["mutmut", "run"],
            [[], ["sh", "-c", "x"], ["mutmut", "-c"], [""], ["x"] * 33],
        ),
        "type.fail_on_error": (False, ["yes"]),
        "type.warn_on_missing_annotation": (True, [1]),
        "type.mypy_required": (True, ["yes"]),
        "type.mypy_profile": ("ici", ["strict", ""]),
        "python_compat.interpreters": (["python3.10"], [[""], ["a", "a"], "python3"]),
        "python_compat.imports": (["pkg.mod"], [["1bad"], ["pkg..mod"], ["a", "a"]]),
        "python_compat.target_version": ("3.10", ["2.7", "3.6", "3", 3.1]),
        "python_compat.wheel_globs": (["dist/*.whl"], [["../x"], ["/a"], ["a\\b"], ["a", "a"]]),
        "python_compat.wheel_required": (True, ["yes"]),
        "python_compat.wheel_policy": ("pure", ["native", ""]),
        "python_compat.check_entrypoints": (False, ["no"]),
        "python_compat.check_package_files": (False, [0]),
        "python_compat.max_wheels": (4, [0, 33]),
        "python_compat.max_wheel_members": (100, [0, 8193]),
        "python_compat.max_wheel_uncompressed_bytes": (1024, [0, 64 * 1024 * 1024 + 1]),
        "complexity.warn_cc": (10, [0, 30, "10"]),
        "complexity.fail_cc": (30, [10, 0]),
        "complexity.warn_nesting": (3, [0]),
        "complexity.cpp_boundaries": ("required", ["sometimes"]),
        "dead.cpp_unused": ("off", ["never"]),
        "dead.cpp_linker": ("auto", ["x", ""]),
        "dead.include_generated": (True, ["yes"]),
        "dead.include_vendor": (True, [1]),
        "dup.warn_pct": (3, [101, 20, -1]),
        "dup.fail_pct": (20, [2, 101]),
        "dup.min_window": (4, [0]),
        "dup.python_semantic": ("off", ["x"]),
        "dup.include_generated": (True, ["yes"]),
        "dup.include_vendor": (True, ["yes"]),
        "cycle.max_reported": (5, [0, "5"]),
        "cognitive.warn": (20, [0, 70]),
        "cognitive.fail": (70, [20]),
        "cognitive.warn_nesting": (3, [0]),
        "cognitive.cpp_boundaries": ("off", ["x"]),
        "security.scan_tests": (True, ["yes"]),
        "security.secret_name_allowlist": (
            ["API_KEY_NAME"],
            [["not-ident"], ["Name", "NAME"], ["a" * 129], [f"n{i}" for i in range(129)]],
        ),
        "binary_compat.artifacts": (
            ["build/app"],
            [[""], ["a", "a"], [f"a{i}" for i in range(65)]],
        ),
        "binary_compat.expected_class": ("ELF64", ["x" * 129, 3]),
        "binary_compat.expected_machine": ("Advanced Micro Devices X86-64", ["a\tb"]),
        "binary_compat.max_glibc": ("2.17", ["abc", "2"]),
        "binary_compat.max_glibcxx": ("3.4.21", ["x"]),
        "binary_compat.max_cxxabi": ("1.3.9", ["1", 1.3]),
        "binary_compat.forbid_absolute_rpath": (False, ["yes"]),
        "binary_compat.require_static": (True, ["yes"]),
        "binary_compat.forbidden_needed": (["libfoo.so"], [[""]]),
        "binary_compat.allowed_needed": (["libc.so.6"], [["x", "x"]]),
        "binary_compat.forbid_build_paths": (False, ["yes"]),
        "binary_compat.allow_non_elf": (True, ["yes"]),
        "binary_compat.max_artifacts": (8, [0, 65]),
        "integration.max_cases": (4, [0, 33]),
        "integration.max_output_bytes": (2048, [1000, 8 * 1024 * 1024 + 1]),
        "integration.python_targets": (
            {"py": "/usr/bin/python3"},
            [{"bad name": "x"}, 3, {"py": ""}],
        ),
        "integration.cases": ([CASE], ["x"]),
    }
    for key, (positive, failures) in specific.items():
        cases += scalar_cases(f"engines.{key}", positive, failures)
    interpreters = {"engines": {"python_compat": {"interpreters": ["python3.10", "python3.12"]}}}
    cases += scalar_cases(
        "engines.python_compat.required_interpreters",
        ["python3.12"],
        [["python3.11"], [""]],
        extra=interpreters,
    )
    both = nest("engines.test.quality", {"repeat": 2, "repeat_runs": 2})
    cases.append(("engines.test.quality.repeat:conflict", "engines.test.quality.repeat", both, ""))
    both = nest("engines.test.quality", {"slow_threshold": 1, "slow_test_threshold": 1})
    key = "engines.test.quality.slow_threshold"
    cases.append((f"{key}:conflict", key, both, ""))
    mutation = "engines.test.quality.mutation"
    cases.append((f"{mutation}:unknown", mutation, nest(f"{mutation}.bogus", 1), mutation))
    quality = "engines.test.quality"
    cases.append((f"{quality}:unknown", quality, nest(f"{quality}.bogus", 1), quality))
    line = nest("engines.line.warn_limit", 100)
    cases.append(("engines:table", "engines", line, "engines.line.warn_limit"))
    cases.append(("engines:failure0", "engines", {"engines": 5}, "engines"))
    cases.append(("engines:unknown", "engines", nest("engines.bogus", {}), "engines"))
    return cases


def build_cases():
    make = "build.make"
    cases = [
        ("build:table", "build", nest("build.python.entrypoint", "pkg.main:run"), "build"),
        ("build:failure0", "build", {"build": 5}, "build"),
        ("build:unknown", "build", nest("build.bogus", 1), "build"),
        ("build.python:failure0", "build.python", nest("build.python", 5), "build.python"),
        ("build.python:unknown", "build.python", nest("build.python.bogus", 1), "build.python"),
        ("build.make:failure0", make, nest(make, 5), make),
        ("build.make:unknown", make, nest(f"{make}.bogus", 1), make),
        ("build.artifacts:failure0", "build.artifacts", nest("build.artifacts", 5), ""),
        ("build.artifacts:unknown", "build.artifacts", nest("build.artifacts.bogus", []), ""),
    ]
    cases += scalar_cases("build.python.entrypoint", "pkg.main:run", ["", 3])
    for table, inner, value in (
        ("build.python", "entrypoint", "pkg.main:run"),
        ("build.make", "jobs", 2),
        ("build.artifacts", "report", ["out/report.html"]),
    ):
        path = f"{table}.{inner}"
        cases.append((f"{table}:table", table, nest(path, value), path))
    enabled = {"build": {"make": {"enabled": True, "build_argv": ["make", "all"]}}}
    cases.append((f"{make}.enabled:positive", f"{make}.enabled", enabled, f"{make}.enabled"))
    cases.append((f"{make}.enabled:failure0", f"{make}.enabled", nest(f"{make}.enabled", True), ""))
    cases.append((f"{make}.enabled:failure1", f"{make}.enabled", nest(f"{make}.enabled", "y"), ""))
    cases += scalar_cases(f"{make}.workdir", "sub", ["../x", "/abs", "a\\b", ""])
    cases += scalar_cases(f"{make}.shadow_dir", "out/shadow", [".", "../x"])
    required = {"build": {"make": {"out_of_tree": "required", "workdir": "sub"}}}
    key = f"{make}.out_of_tree"
    cases.append((f"{key}:positive", key, required, key))
    cases.append((f"{key}:failure0", key, nest(key, "required"), key))
    cases.append((f"{key}:failure1", key, nest(key, "always"), key))
    cases += scalar_cases(f"{make}.jobs", 4, [0, 65, "4"])
    for name in (
        "configure_argv",
        "test_argv",
        "clean_argv",
        "coverage_build_argv",
        "coverage_test_argv",
        "sanitize_build_argv",
        "sanitize_test_argv",
        "thread_sanitize_build_argv",
        "thread_sanitize_test_argv",
        "build_argv",
    ):
        cases += scalar_cases(
            f"{make}.{name}",
            ["make", "-j", "{jobs}", name],
            [["sh", "-c", "make"], ["make", "$X"], [""], ["make", "{oops}"], "make"],
        )
    empty_build = {"build": {"make": {"enabled": True, "build_argv": []}}}
    key = f"{make}.build_argv"
    cases.append((f"{key}:failure_enabled_empty", key, empty_build, key))
    for name in ("python_wheel", "report"):
        cases += scalar_cases(
            f"build.artifacts.{name}", ["dist/*.whl"], [["../x"], ["/a"], ["a", "a"], [""]]
        )
    cases.append(("doctor:failure0", "doctor", {"doctor": 5}, "doctor"))
    cases.append(("doctor:unknown", "doctor", nest("doctor.bogus", 1), "doctor"))
    cases.append(("doctor:table", "doctor", nest("doctor.required_tools", ["cmake"]), "doctor"))
    cases += scalar_cases("doctor.required_tools", ["cmake"], [[""], "cmake"])
    return cases


def metadata_cases():
    cases = []
    for table in ("ici", "project"):
        cases.append((f"{table}:failure0", table, {table: 5}, table))
        cases.append((f"{table}:unknown", table, nest(f"{table}.bogus", "x"), table))
    cases.append(("ici:table", "ici", nest("ici.policy_name", "Team gate"), "ici.policy_name"))
    cases.append(("project:table", "project", nest("project.name", "demo"), "project"))
    cases += scalar_cases("ici.version", "9.9", ["", 1])
    cases += scalar_cases("ici.policy_name", "Team gate", [" ", 2])
    cases += scalar_cases("ici.profile", "deep", ["slow", ""])
    cases += scalar_cases(
        "project.compile_database", "build/compile_commands.json", ["", "../x", "a\\b"]
    )
    cases += scalar_cases("project.cpp_external_build_dirs", ["qt"], [["../x"], [""]])
    cases += scalar_cases("project.cpp_pkg_config", ["Qt5Widgets"], [[""], "Qt5Widgets"])
    cases += scalar_cases("project.name", "demo", ["", 1])
    cases += scalar_cases("project.source_dirs", ["src"], [["../x"], [""], "src"])
    cases += scalar_cases("project.type", "cpp", ["java", ""])
    cases += scalar_cases("project.version", "1.2", [""])
    cases += scalar_cases("name", "demo", ["", 1])
    cases += scalar_cases("type", "hybrid", ["rust"])
    cases += scalar_cases("version", "1.0", [" "])
    cases.append(("<top>:unknown", "", {"bogus": 1}, "bogus"))
    return cases


CLI_SAMPLES = (
    "<top>:unknown",
    "engines.line.warn_limit:failure1",
    "engines.line.warn_limit:failure2",
)
CLI_ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"


def run(oracle, cases):
    sys.path.insert(0, str(oracle / "src"))
    import ici
    from ici.config import load_config
    from ici.config_schema import ConfigError

    assert Path(ici.__file__).resolve().is_relative_to(oracle.resolve())
    results = []
    with tempfile.TemporaryDirectory() as temporary:
        xdg = Path(temporary) / "xdg"
        xdg.mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        os.environ.pop("ICI_CONFIG", None)
        project = Path(temporary) / "project"
        project.mkdir()
        (project / "ici.toml").write_text("", encoding="utf-8")
        defaults = load_config(project, create_global_default=False)
        defaults["ici"].pop("version")
        seen = set()
        for identifier, key, document, path in cases:
            assert identifier not in seen, identifier
            seen.add(identifier)
            text = tomli_w.dumps(document)
            (project / "ici.toml").write_text(text, encoding="utf-8")
            entry = {"id": identifier, "key": key, "toml": text}
            try:
                effective = load_config(project, create_global_default=False)
            except ConfigError as error:
                message = str(error)
                entry["outcome"] = "reject"
                entry["message"] = message
                entry["error_key"] = re.match(r"[A-Za-z0-9_.\[\]]*", message).group(0).rstrip(".")
            else:
                entry["outcome"] = "accept"
                if path:
                    entry["pointer"] = pointer(path)
                    value = effective
                    for part in entry["pointer"].split("/")[1:]:
                        value = value[int(part)] if isinstance(value, list) else value[part]
                    entry["value"] = value
            results.append(entry)
        assert not list(xdg.iterdir()), "frozen loader must not create a global default here"
        cli = []
        for identifier in CLI_SAMPLES:
            entry = next(item for item in results if item["id"] == identifier)
            (project / "ici.toml").write_text(entry["toml"], encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-c", CLI_ENTRY, str(oracle / "src"), "line"],
                cwd=project,
                capture_output=True,
                text=True,
                env={"XDG_CONFIG_HOME": str(xdg), "HOME": temporary, "PATH": os.defpath},
                check=False,
            )
            cli.append(
                {
                    "id": identifier,
                    "argv": ["ici", "line"],
                    "exit_code": completed.returncode,
                    "stderr": completed.stderr.strip().splitlines()[-1],
                }
            )
    return defaults, results, cli


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    commit = subprocess.run(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    cases = engine_cases() + integration_cases() + build_cases() + metadata_cases()
    defaults, results, cli = run(oracle, cases)
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "oracle_git_head": commit or None,
        "python": sys.version.split()[0],
        "reader": "ici.config.load_config(create_global_default=False)",
        "defaults": defaults,
        "cases": results,
        "cli": cli,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True)
        stream.write("\n")
    accepted = sum(entry["outcome"] == "accept" for entry in results)
    print(f"{len(results)} frozen legacy cases ({accepted} accepted)")


if __name__ == "__main__":
    main()
