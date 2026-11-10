"""Development-only capture: frozen next selection of symlinked sources.

    python -I capture_outside_links.py --oracle <oracle> --python <interpreter> --out <json>

Frozen's inventory stores a selected link as the workspace-relative file it resolves to
(`workspace/inventory.py:292-298`), so a target outside the component's root is a member the
per-file reads then refuse. Each scenario records the exit code, gate, check states, blocked and
failed tasks and `next plan`'s text for one symlink shape; the configless scenarios run
`ici analyze`, frozen's automatic selection.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
OPTIONAL = ("lint", "format", "type", "test", "coverage", "compat-runtime")
REAL = "x = 1\n"
EXTERNAL = "<EXTERNAL>"


def config(extra="", component=""):
    disabled = "".join(f'[checks."python.{name}"]\nenabled = false\n' for name in OPTIONAL)
    return (
        'schema_version = 1\n[workspace]\nname = "demo"\nprofile = "deep"\n'
        f'[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n{component}'
        f"{extra}{disabled}"
    )


LIB = '[[components]]\nid = "lib"\nroot = "lib"\nlanguages = ["python"]\n'
LINK = {"link": "../other/m.py"}
SCENARIOS = {
    "outside-only": (config(), {"other/m.py": REAL, "app/m.py": LINK}),
    "outside-beside-real": (
        config(),
        {"other/m.py": REAL, "app/link.py": LINK, "app/real.py": REAL},
    ),
    "outside-beside-independent-component": (
        config(LIB),
        {"other/m.py": REAL, "app/link.py": LINK, "app/real.py": REAL, "lib/lib.py": REAL},
    ),
    "same-component": (
        config(),
        {"app/link.py": {"link": "real.py"}, "app/real.py": REAL},
    ),
    "outside-workspace": (
        config(),
        {"app/link.py": {"link": EXTERNAL}, "app/real.py": REAL},
    ),
    "broken": (config(), {"app/link.py": {"link": "gone.py"}, "app/real.py": REAL}),
    "directory": (
        config(),
        {"other/m.py": REAL, "app/d": {"link": "../other"}, "app/real.py": REAL},
    ),
    "name-py-target-not-source": (
        config(),
        {
            "other/notes.txt": "text\n",
            "app/link.py": {"link": "../other/notes.txt"},
            "app/real.py": REAL,
        },
    ),
    "name-txt-target-py": (
        config(),
        {"other/m.py": REAL, "app/link.txt": LINK, "app/real.py": REAL},
    ),
    "excluded": (
        config(component='exclude = ["link.py"]\n'),
        {"other/m.py": REAL, "app/link.py": LINK, "app/real.py": REAL},
    ),
    "unselected-glob": (
        config(component='sources = ["real.py"]\n'),
        {"other/m.py": REAL, "app/link.py": LINK, "app/real.py": REAL},
    ),
}


CPP_ON = ("cpp.line", "cpp.complexity", "cpp.cognitive", "cpp.cycle", "cpp.dup", "cpp.exception")


def cpp_config(oracle):
    """C++ checks that read sources themselves; the frozen catalog names the rest to disable."""

    import sys

    sys.path.insert(0, str(oracle / "src"))
    from ici.languages.cpp.checks import CPP_CHECKS

    disabled = "".join(
        f'[checks."{check.id}"]\nenabled = false\n'
        for check in CPP_CHECKS
        if check.id not in CPP_ON
    )
    return (
        'schema_version = 1\n[workspace]\nname = "demo"\nprofile = "deep"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\n'
        f"{disabled}"
    )


CPP_REAL = "int real() { return 2; }\n"
CPP_FILES = {
    "cpp-outside-beside-real": {
        "other/m.cpp": "int m() { return 1; }\n",
        "app/link.cpp": {"link": "../other/m.cpp"},
        "app/real.cpp": CPP_REAL,
    },
    "cpp-outside-only": {
        "other/m.cpp": "int m() { return 1; }\n",
        "app/m.cpp": {"link": "../other/m.cpp"},
    },
}
PACKAGE = '[project]\nname = "{}"\nversion = "1.0"\n'
AUTOMATIC = {
    "automatic-link-to-another-package": {
        "a/pyproject.toml": PACKAGE.format("a"),
        "a/m.py": REAL,
        "a/link.py": {"link": "../b/x.py"},
        "b/pyproject.toml": PACKAGE.format("b"),
        "b/x.py": REAL,
    },
    "automatic-link-out-of-a-package": {
        "a/pyproject.toml": PACKAGE.format("a"),
        "a/m.py": REAL,
        "a/link.py": LINK,
        "other/m.py": REAL,
    },
}


def write_tree(root, external, files):
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            os.symlink(content["link"].replace(EXTERNAL, str(external)), target)
        else:
            target.write_text(content, encoding="utf-8")


def capture(oracle, python, text, files):
    with tempfile.TemporaryDirectory(prefix="ici-outside-link-") as temporary:
        base = Path(temporary)
        external = base / "external.py"
        external.write_text(REAL, encoding="utf-8")
        root = base / "project"
        write_tree(root, external, files)
        if text is not None:
            (root / "ici.toml").write_text(text, encoding="utf-8")
        subprocess.run(["/usr/bin/git", "init", "-q", "."], cwd=root, check=True)
        environment = dict(os.environ, PYTHONPATH=str(oracle / "src"), PATH="/usr/bin:/bin")
        # Without a configuration frozen's next commands refuse; `analyze` selects automatically.
        command = [str(python), "-m", "ici"]
        plan, run = (
            (["next", "plan"], ["next", "verify"])
            if text is not None
            else (["analyze", "--plan"], ["analyze"])
        )
        planned = subprocess.run(
            [*command, *plan],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        verified = subprocess.run(
            [*command, *run, "--no-cache", "--result", "out/result.json"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        result = json.loads((root / "out/result.json").read_text(encoding="utf-8"))
    return {
        "exit_code": verified.returncode,
        "plan_exit_code": planned.returncode,
        "plan_text": planned.stdout.replace(str(root), "<ROOT>"),
        "gate": result["gate"],
        "states": {item["task_id"]: item["state"] for item in result["checks"]},
        "blocked": result["execution"]["blocked_task_ids"],
        "failed": result["execution"]["failed_task_ids"],
        "source_files": result["identity"]["source"]["files"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = {
        name: {
            "config": text,
            "files": files,
            "frozen": capture(args.oracle, args.python, text, files),
        }
        for name, (text, files) in SCENARIOS.items()
    }
    cpp = cpp_config(args.oracle)
    for name, files in CPP_FILES.items():
        runs[name] = {
            "config": cpp,
            "files": files,
            "frozen": capture(args.oracle, args.python, cpp, files),
        }
    for name, files in AUTOMATIC.items():
        runs[name] = {
            "config": None,
            "files": files,
            "frozen": capture(args.oracle, args.python, None, files),
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
