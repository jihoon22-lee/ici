"""Development-only capture: frozen next refusals planned by application/testing.py.

    python -I capture_next_refusals.py --oracle <oracle> --python <interpreter> --out <json>

Each scenario runs the immutable v0.13.0 `ici next verify --no-cache` and `ici next plan` with an
explicit project interpreter and `git` behind logging shims (the only tools the fixtures can
run; every other check that needs one is disabled). It records the exit code, the gate, the blocked
and failed task sets, the state of each selected check, the `next plan` blocked line, the ordered
probe argv, each byte/symlink change under the project and the result's scope, source list and
metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
OPTIONAL = ("lint", "format", "type", "test", "coverage", "compat-runtime")
SECURITY = 'import subprocess\nsubprocess.call("ls", shell=True)\neval("1")\n'
PLAIN = {"app/m.py": "x = 1\n", "app/tests/test_a.py": "def test_a():\n    pass\n"}
BAD = "[project\n"
REQUIRED_RUNTIME = '[checks."python.compat-runtime"]\nrequired = true\n'


def config(selected, named="", python=""):
    disabled = "".join(
        f'[checks."python.{check}"]\nenabled = false\n'
        for check in OPTIONAL
        if check not in selected
    )
    return (
        'schema_version = 1\n[workspace]\nname = "demo"\nprofile = "deep"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        f"[components.python]\nexecutable = <PYTHON_JSON>\n{python}{disabled}{named}"
    )


def named(check):
    return f'[checks."python.{check}"]\nenabled = true\n'


TEST_PATHS = 'test_paths = ["tests/none"]\n'
SCENARIOS = {
    "finding-unnamed": (config(()), {"app/m.py": SECURITY}, "python.security"),
    "finding-named-enabled": (
        config((), named("security")),
        {"app/m.py": SECURITY},
        "python.security",
    ),
    "test-paths-none": (config(("test",), python=TEST_PATHS), PLAIN, "python.test"),
    "test-paths-none-named-enabled": (
        config(("test",), named("test"), python=TEST_PATHS),
        PLAIN,
        "python.test",
    ),
    "coverage-without-test": (config(("coverage",)), PLAIN, "python.coverage"),
    "coverage-without-test-named-enabled": (
        config(("coverage",), named("coverage")),
        PLAIN,
        "python.coverage",
    ),
    "metadata-required": (
        config(("compat-runtime",), REQUIRED_RUNTIME),
        {"app/m.py": "x = 1\n", "app/pyproject.toml": BAD},
        "python.compat-runtime",
    ),
    "metadata-advisory": (
        config(("compat-runtime",)),
        {"app/m.py": "x = 1\n", "app/pyproject.toml": BAD},
        "python.compat-runtime",
    ),
    "metadata-workspace-floor": (
        config(("compat-runtime",), REQUIRED_RUNTIME),
        {"app/m.py": "x = 1\n", "pyproject.toml": BAD},
        "python.compat-runtime",
    ),
    "metadata-project-not-table": (
        config(("compat-runtime",), REQUIRED_RUNTIME),
        {"app/m.py": "x = 1\n", "app/pyproject.toml": "project = 1\n"},
        "python.compat-runtime",
    ),
    "metadata-floor-not-string": (
        config(("compat-runtime",), REQUIRED_RUNTIME),
        {"app/m.py": "x = 1\n", "app/pyproject.toml": "[project]\nrequires-python = 3\n"},
        "python.compat-runtime",
    ),
    "metadata-floor-invalid": (
        config(("compat-runtime",), REQUIRED_RUNTIME),
        {"app/m.py": "x = 1\n", "app/pyproject.toml": '[project]\nrequires-python = "bogus"\n'},
        "python.compat-runtime",
    ),
    "metadata-symlinked": (
        config(("compat-runtime",), REQUIRED_RUNTIME),
        {
            "app/m.py": "x = 1\n",
            "real.toml": '[project]\nrequires-python = ">=3.8"\n',
            "app/pyproject.toml": ("link", "../real.toml"),
        },
        "python.compat-runtime",
    ),
    "metadata-readable-control": (
        config(("compat-runtime",), REQUIRED_RUNTIME),
        {"app/m.py": "x = 1\n", "app/pyproject.toml": '[project]\nrequires-python = ">=3.8"\n'},
        "python.compat-runtime",
    ),
}


def write_tree(root, files):
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple):
            os.symlink(content[1], target)
        else:
            target.write_text(content, encoding="utf-8")


def shim(directory, name, real, log):
    script = directory / name
    script.write_text(
        f"#!/bin/sh\nprintf '{name} %s\\n' \"$*\" >> {json.dumps(str(log))}\n"
        f'exec {json.dumps(str(real))} "$@"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def snapshot(root):
    """Every path's bytes (a symlink's target) outside `.git`, for before/after comparison."""

    state = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            state[relative] = f"link:{os.readlink(path)}"
        elif path.is_file():
            state[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return state


def changes(before, after):
    return {
        "created": sorted(set(after) - set(before)),
        "modified": sorted(
            path for path in after if path in before and after[path] != before[path]
        ),
        "deleted": sorted(set(before) - set(after)),
    }


def probes(log, base):
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    log.unlink(missing_ok=True)
    return [line.replace(str(base), "<BASE>") for line in text.splitlines()]


def plan_line(stdout, check):
    for line in stdout.splitlines():
        if f".{check}:" in line:
            return line.strip() if "blocked" in line else None
    return None


def capture(oracle, python, text, files, check):
    with tempfile.TemporaryDirectory(prefix="ici-next-refusal-") as temporary:
        base = Path(temporary)
        log = base / "tools.log"
        shims = base / "shims"
        shims.mkdir()
        shim(shims, "git", "/usr/bin/git", log)
        script = shim(shims, "project-python", python, log)
        root = base / "project"
        write_tree(root, files)
        (root / "ici.toml").write_text(
            text.replace("<PYTHON_JSON>", json.dumps(str(script))), encoding="utf-8"
        )
        environment = dict(
            os.environ, PYTHONPATH=str(oracle / "src"), PATH=f"{shims}:/usr/bin:/bin"
        )
        subprocess.run(["/usr/bin/git", "init", "-q", "."], cwd=root, check=True)
        command = [str(python), "-m", "ici", "next"]
        before = snapshot(root)
        planned = subprocess.run(
            [*command, "plan"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        plan_probes = probes(log, base)
        after_plan = snapshot(root)
        verified = subprocess.run(
            [*command, "verify", "--no-cache", "--result", "out/result.json"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        verify_probes = probes(log, base)
        after_verify = snapshot(root)
        result = json.loads((root / "out/result.json").read_text(encoding="utf-8"))
    states = {}
    for item in result["checks"]:
        states.setdefault(item["check_id"], set()).add(item["state"])
    return {
        "exit_code": verified.returncode,
        "plan_exit_code": planned.returncode,
        "plan_line": plan_line(planned.stdout, check),
        "gate": result["gate"],
        "states": {key: sorted(value) for key, value in states.items()},
        "blocked": result["execution"]["blocked_task_ids"],
        "failed": result["execution"]["failed_task_ids"],
        "required_complete": result["execution"]["required_complete"],
        "compileall_ran": any("compileall" in line for line in verify_probes),
        "strict": {
            "plan_probes": plan_probes,
            "verify_probes": verify_probes,
            "plan_changes": changes(before, after_plan),
            "verify_changes": changes(after_plan, after_verify),
            "findings": result["findings"],
            "metrics": result["metrics"],
            "limitations": result["limitations"],
            "scope": result["scope"],
            "source": {
                key: value for key, value in result["identity"]["source"].items() if key != "digest"
            },
            "checks": [
                {key: value for key, value in item.items() if key != "scope_digest"}
                for item in result["checks"]
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = {}
    for name, (text, files, check) in SCENARIOS.items():
        runs[name] = {
            "config": text,
            "files": {
                path: ({"link": value[1]} if isinstance(value, tuple) else value)
                for path, value in files.items()
            },
            "check": check,
            "frozen": capture(args.oracle, args.python, text, files, check),
        }
    document = {"source": SOURCE, "runs": runs}
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _private_home():
    """The shared private HOME/XDG context (tests/rust-parity/private_home.py), loaded by path."""
    path = Path(__file__).resolve().parents[1] / "private_home.py"
    spec = importlib.util.spec_from_file_location("rust_parity_private_home", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.private_home()


if __name__ == "__main__":
    with _private_home():
        main()
