"""Capture frozen v0.13.0 `ici next plan` / `ici analyze --plan` selection and `ici next init`.

    python -I capture_cli_selection.py <oracle-root> <output.json>

Each variant runs the frozen CLI in a fresh copy of a small workspace and records the exit
code and the planned task ids (a plan executes no task), or for `next init` whether an
existing ici.toml was left unchanged.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LOCAL_EXECUTABLE = "/nonexistent/ici-parity/bin/python"
WORKSPACE = {
    "ici.toml": (
        'schema_version = 1\n[workspace]\nname = "demo"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[[components]]\nid = "core"\nroot = "core"\nlanguages = ["cpp"]\n'
    ),
    "app/main.py": "def main():\n    return 1\n",
    "core/lib.cpp": "int answer() { return 42; }\n",
    "alt.toml": (
        'schema_version = 1\n[workspace]\nname = "alt"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
    ),
    "local.toml": f'components.app.python.executable = "{LOCAL_EXECUTABLE}"\n',
    "policy.toml": "checks.line.enabled = false\n",
}
CONFIGLESS = {
    "main.py": "def main():\n    return 1\n",
    "lib.cpp": "int answer() { return 42; }\n",
}
NEXT = [
    ("base", []),
    ("component", ["--component", "app"]),
    ("component_repeated", ["--component", "core", "--component", "app"]),
    ("component_unknown", ["--component", "ghost"]),
    ("python", ["--python"]),
    ("cpp", ["--cpp"]),
    ("python_cpp", ["--python", "--cpp"]),
    ("profile_fast", ["--profile", "fast"]),
    ("profile_deep", ["--profile", "deep"]),
    ("profile_invalid", ["--profile", "bogus"]),
    ("require_full", ["--require-full"]),
    ("require_full_partial", ["--component", "app", "--require-full"]),
    ("config", ["--config", "ici.toml"]),
    ("config_alternate", ["--config", "alt.toml"]),
    ("config_missing", ["--config", "missing.toml"]),
    ("local_config", ["--local-config", "local.toml"]),
    ("local_config_policy", ["--local-config", "policy.toml"]),
]
ANALYZE = [
    ("base", []),
    ("language_python", ["--language", "python"]),
    ("language_cpp", ["--language", "cpp"]),
    ("language_invalid", ["--language", "cobol"]),
    ("component", ["--component", "project"]),
    ("component_unknown", ["--component", "ghost"]),
    ("python", ["--python"]),
    ("cpp", ["--cpp"]),
    ("profile_fast", ["--profile", "fast"]),
    ("require_full_partial", ["--language", "python", "--require-full"]),
]
ANALYZE_CONFIGURED = [
    ("base", []),
    ("component", ["--component", "core"]),
    ("config", ["--config", "ici.toml"]),
    ("config_alternate", ["--config", "alt.toml"]),
    ("config_missing", ["--config", "missing.toml"]),
    ("local_config", ["--local-config", "local.toml"]),
    ("local_config_policy", ["--local-config", "policy.toml"]),
    ("require_full", ["--require-full"]),
]
VALID = (
    'schema_version = 1\n[workspace]\nname = "keep"\n'
    '[[components]]\nid = "keep"\nroot = "."\nlanguages = ["python"]\n'
)
INIT = [
    ("fresh", [], None),
    ("preview", ["--preview"], None),
    ("preview_python", ["--python", "--preview"], None),
    ("preview_cpp", ["--cpp", "--preview"], None),
    ("existing", [], VALID),
    ("existing_preview", ["--preview"], VALID),
    ("existing_force", ["--force"], VALID),
    ("broken_force", ["--force"], "x = = broken\n"),
]
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"


def invoke(oracle, base, argv, home):
    return subprocess.run(
        [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
        cwd=base,
        capture_output=True,
        text=True,
        env={"HOME": home, "XDG_CONFIG_HOME": home, "PATH": os.defpath},
        check=False,
    )


def materialize(base, tree):
    for name, text in tree.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (base / ".git").mkdir()


def run(oracle, tree, argv, home):
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary) / "project"
        materialize(base, tree)
        completed = invoke(oracle, base, argv, home)
        observed = {"exit_code": completed.returncode, "tasks": None}
        if completed.returncode == 0 and completed.stdout.strip().startswith("{"):
            plan = json.loads(completed.stdout)
            checks = [check for item in plan["plans"] for check in item["checks"]]
            observed["tasks"] = sorted(check["task_id"] for check in checks)
            observed["require_full"] = plan["scope"]["require_full"]
            observed["profile"] = plan["scope"]["profile"]
            # Tasks whose blocked reason names the local-overlay interpreter: proves the
            # overlay reached the plan without depending on which tools the host has.
            observed["names_local_executable"] = sorted(
                check["task_id"]
                for check in checks
                if LOCAL_EXECUTABLE in (check.get("blocked") or "")
            )
        shutil.rmtree(base / ".ici", ignore_errors=True)
        return observed


def run_init(oracle, options, existing, home):
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary) / "project"
        materialize(base, CONFIGLESS)
        target = base / "ici.toml"
        if existing is not None:
            target.write_text(existing, encoding="utf-8")
        argv = ["next", "init", *options]
        completed = invoke(oracle, base, argv, home)
        after = target.read_text(encoding="utf-8") if target.exists() else None
        languages = [line for line in completed.stdout.splitlines() if line.startswith("languages")]
        return {
            "argv": argv,
            "existing": existing,
            "exit_code": completed.returncode,
            "file_unchanged": after == existing,
            "languages_line": languages[0] if languages else None,
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    results = {"next_plan": {}, "analyze_plan": {}, "analyze_configured_plan": {}}
    groups = (
        ("next_plan", ["next", "plan", "--json"], NEXT, WORKSPACE),
        ("analyze_plan", ["analyze", "--plan", "--json"], ANALYZE, CONFIGLESS),
        ("analyze_configured_plan", ["analyze", "--plan", "--json"], ANALYZE_CONFIGURED, WORKSPACE),
    )
    with tempfile.TemporaryDirectory() as home:
        for group, command, variants, tree in groups:
            for name, options in variants:
                argv = [*command, *options]
                results[group][name] = {"argv": argv, **run(oracle, tree, argv, home)}
        results["next_init"] = {
            name: run_init(oracle, options, existing, home) for name, options, existing in INIT
        }
        assert not any(Path(home).iterdir()), "plan and init write no user configuration"
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "workspace": WORKSPACE,
        "configless": CONFIGLESS,
        "local_executable": LOCAL_EXECUTABLE,
        "results": results,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True)
        stream.write("\n")
    for group, items in results.items():
        for name, item in items.items():
            print(group, name, item["exit_code"], item.get("tasks") and len(item["tasks"]))


if __name__ == "__main__":
    main()
