"""Capture frozen v0.13.0 CLI behaviour at the config/CLI boundary.

    python -I capture_cli_gaps.py <oracle-root> <output.json>

Each case runs the frozen CLI once in a fresh copy of a small project (empty HOME, PATH
without analysis tools) and records the exit code, stdout, stderr and the files the run
wrote, with the project root as `<root>` and HOME as `<home>`:

- non-engine subcommands on a `[workspace]` root (publish, doctor, build, export, env, ...);
- the stderr prefix of configuration errors across commands (next schema problem,
  unparseable TOML, invalid legacy value, unknown scope) and of report/publish/diff refusals;
- `ici line` from a subdirectory of a checkout, with and without a root configuration;
- a bare `--local-config` interpreter name in `ici next plan`;
- an unparseable `pyproject.toml` beside a legacy or next configuration.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
HOME = "<home>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
OFF = ("lint", "format", "type")
WORKSPACE = (
    'schema_version = 1\n[workspace]\nname = "demo"\n'
    '[[components]]\nid = "app"\nroot = "."\nlanguages = ["python"]\n'
    + "".join(f'[checks."python.{check}"]\nenabled = false\n' for check in OFF)
)
WORKSPACE_TREE = {"ici.toml": WORKSPACE, "main.py": "def hello():\n    return 1\n"}
NEXT_INVALID = {
    "ici.toml": WORKSPACE.replace('name = "demo"\n', 'name = "demo"\nbogus = 1\n'),
    "main.py": "def hello():\n    return 1\n",
}
UNPARSEABLE = {"ici.toml": "[[\n", "main.py": "def hello():\n    return 1\n"}
LEGACY_INVALID = {
    "ici.toml": '[project]\ntype = "python"\nsource_dirs = ["."]\n'
    '[engines.line]\nwarn_limit = "many"\n',
    "main.py": "def hello():\n    return 1\n",
}
SUBDIRECTORY = {
    "src/root.py": "".join(f"x{index} = {index}\n" for index in range(40)),
    "sub/src/inner.py": "def inner():\n    return 1\n",
}
LEGACY_ROOT = {
    **SUBDIRECTORY,
    "ici.toml": '[project]\ntype = "python"\nsource_dirs = ["."]\n',
}
BARE_LOCAL = {
    **WORKSPACE_TREE,
    "local.toml": 'components.app.python.executable = "ici-parity-missing-python"\n',
}
LEGACY_PYPROJECT = {
    "ici.toml": '[project]\ntype = "python"\nsource_dirs = ["."]\n',
    "pyproject.toml": "[project\n",
    "main.py": "def hello():\n    return 1\n",
}
NEXT_PYPROJECT = {
    "ici.toml": WORKSPACE,
    "pyproject.toml": "[project\n",
    "main.py": "def hello():\n    return 1\n",
}
CONFIGLESS_PYPROJECT = {"pyproject.toml": "[project\n", "main.py": "def hello():\n    return 1\n"}
CASES = {
    "workspace_publish": (WORKSPACE_TREE, ".", ["publish"]),
    "workspace_doctor": (WORKSPACE_TREE, ".", ["doctor"]),
    "workspace_doctor_json": (WORKSPACE_TREE, ".", ["doctor", "--json"]),
    "workspace_export": (WORKSPACE_TREE, ".", ["export-compilation-context"]),
    "workspace_build": (WORKSPACE_TREE, ".", ["build"]),
    "workspace_setup": (WORKSPACE_TREE, ".", ["setup", "--inspect"]),
    "workspace_env": (WORKSPACE_TREE, ".", ["env"]),
    "workspace_cache": (WORKSPACE_TREE, ".", ["cache"]),
    "workspace_subdir_line": (
        {**WORKSPACE_TREE, "pkg/src/b.py": "B = 1\n"},
        "pkg",
        ["line"],
    ),
    "scope_unknown_component": (WORKSPACE_TREE, ".", ["next", "plan", "--component", "ghost"]),
    "scope_unknown_language": (WORKSPACE_TREE, ".", ["analyze", "--language", "cobol"]),
    "scope_verify_unknown_component": (
        WORKSPACE_TREE,
        ".",
        ["next", "verify", "--component", "ghost"],
    ),
    "report_without_result": (WORKSPACE_TREE, ".", ["report"]),
    "publish_without_result": (WORKSPACE_TREE, ".", ["next", "publish"]),
    "diff_without_baseline": (WORKSPACE_TREE, ".", ["diff", "missing.json"]),
    "next_invalid_plan": (NEXT_INVALID, ".", ["next", "plan"]),
    "next_invalid_verify": (NEXT_INVALID, ".", ["next", "verify"]),
    "next_invalid_analyze": (NEXT_INVALID, ".", ["analyze"]),
    "next_invalid_doctor": (NEXT_INVALID, ".", ["next", "doctor"]),
    "next_invalid_line": (NEXT_INVALID, ".", ["line"]),
    "next_invalid_stable_verify": (NEXT_INVALID, ".", ["verify"]),
    "unparseable_next_plan": (UNPARSEABLE, ".", ["next", "plan"]),
    "unparseable_analyze": (UNPARSEABLE, ".", ["analyze"]),
    "unparseable_verify": (UNPARSEABLE, ".", ["verify"]),
    "unparseable_line": (UNPARSEABLE, ".", ["line"]),
    "unparseable_doctor": (UNPARSEABLE, ".", ["doctor"]),
    "unparseable_export": (UNPARSEABLE, ".", ["export-compilation-context"]),
    "unparseable_cache": (UNPARSEABLE, ".", ["cache"]),
    "legacy_invalid_line": (LEGACY_INVALID, ".", ["line"]),
    "legacy_invalid_verify": (LEGACY_INVALID, ".", ["verify"]),
    "legacy_invalid_doctor": (LEGACY_INVALID, ".", ["doctor"]),
    "legacy_invalid_build": (LEGACY_INVALID, ".", ["build"]),
    "legacy_invalid_publish": (LEGACY_INVALID, ".", ["publish"]),
    "legacy_invalid_export": (LEGACY_INVALID, ".", ["export-compilation-context"]),
    "legacy_invalid_next_plan": (LEGACY_INVALID, ".", ["next", "plan"]),
    "legacy_invalid_analyze": (LEGACY_INVALID, ".", ["analyze"]),
    "configless_subdir_line": (SUBDIRECTORY, "sub", ["line"]),
    "configless_subdir_line_report": (SUBDIRECTORY, "sub", ["line", "--report"]),
    "configless_root_line": (SUBDIRECTORY, ".", ["line"]),
    "legacy_root_subdir_line": (LEGACY_ROOT, "sub", ["line"]),
    "legacy_root_line": (LEGACY_ROOT, ".", ["line"]),
    "bare_local_interpreter_plan": (
        BARE_LOCAL,
        ".",
        ["next", "plan", "--json", "--local-config", "local.toml"],
    ),
    "legacy_pyproject_line": (LEGACY_PYPROJECT, ".", ["line"]),
    "next_pyproject_plan": (NEXT_PYPROJECT, ".", ["next", "plan"]),
    "configless_pyproject_plan": (CONFIGLESS_PYPROJECT, ".", ["analyze", "--plan"]),
}


def _scrub(text, base, home):
    return text.replace(str(base), ROOT).replace(str(home), HOME)


def _files(base, skip=()):
    if not base.exists():
        return []
    return sorted(
        str(path.relative_to(base))
        for path in base.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(base).parts
        and str(path.relative_to(base)) not in skip
    )


def _run(oracle, tree, cwd, argv):
    with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as temporary:
        home = Path(home_dir).resolve()
        base = Path(temporary).resolve() / "project"
        for file, text in tree.items():
            (base / file).parent.mkdir(parents=True, exist_ok=True)
            (base / file).write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
            cwd=base / cwd,
            capture_output=True,
            text=True,
            env={"HOME": str(home), "XDG_CONFIG_HOME": str(home), "PATH": os.defpath},
            check=False,
        )
        return {
            "argv": argv,
            "cwd": cwd,
            "exit_code": completed.returncode,
            "stdout": _scrub(completed.stdout, base, home),
            "stderr": _scrub(completed.stderr, base, home),
            "written": _files(base, set(tree)),
            "home_written": _files(home),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    cases = {}
    for name, (tree, cwd, argv) in CASES.items():
        cases[name] = {"tree": tree, **_run(oracle, tree, cwd, argv)}
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "cases": cases,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
