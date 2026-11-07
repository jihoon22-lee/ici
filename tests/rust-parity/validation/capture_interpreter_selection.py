"""Development-only capture: frozen deep `ici next plan --json` project-interpreter selection.

Frozen `application/tooling.py::resolve_python` offers only the declared `[python] executable`
(a bare name is a PATH lookup) and then the component's `.venv/bin/python`. ici's own interpreter
and PATH `python` are never candidates; without one, python.test, python.coverage and
python.compat-runtime are blocked with the resolver's reason.
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
CHECKS = ("python.test", "python.coverage", "python.compat-runtime")
# scenario: (declared executable or None, create .venv/bin/python)
SCENARIOS = {
    "no_venv": (None, False),
    "venv": (None, True),
    "declared_missing": ("missing/python", True),
    "declared_bare": ("python3", False),
}


def config(declared: str | None) -> str:
    text = (
        "schema_version=1\n[workspace]\nname='w'\nprofile='deep'\n[[components]]\n"
        "id='project'\nroot='.'\nlanguages=['python']\n"
    )
    if declared is not None:
        text += f"[components.python]\nexecutable={json.dumps(declared)}\n"
    return text


def normalized(text: str, root: Path, python: Path, bare: str | None) -> str:
    text = text.replace(str(root), "<ROOT>").replace(str(python), "<PYTHON>")
    return text if bare is None else text.replace(bare, "<BARE>")


def capture(oracle: Path, python: Path, declared: str | None, venv: bool) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-interpreter-") as temporary:
        root = Path(temporary).resolve()
        (root / "ici.toml").write_text(config(declared), encoding="utf-8")
        (root / "app.py").write_text("X = 1\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests/test_app.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        if venv:
            (root / ".venv/bin").mkdir(parents=True)
            (root / ".venv/bin/python").symlink_to(python)
        completed = subprocess.run(
            [str(python), "-m", "ici", "next", "plan", "--json", "--profile", "deep"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        document = json.loads(completed.stdout)
        bare = None
        if declared is not None and "/" not in declared:
            bare = subprocess.check_output(["sh", "-c", f"command -v {declared}"], text=True)
            bare = bare.strip()
        checks = {}
        for plan in document["plans"]:
            for check in plan["checks"]:
                if check["check"] not in CHECKS:
                    continue
                row = {"kind": check["kind"]}
                if "blocked" in check:
                    row["blocked"] = normalized(check["blocked"], root, python, bare)
                if "argv" in check:
                    row["interpreter"] = normalized(check["argv"][0], root, python, bare)
                checks[check["check"]] = row
        return {
            "config": config(declared),
            "venv": venv,
            "exit_code": completed.returncode,
            "checks": checks,
            "edges": {
                key: sorted(value)
                for key, value in document["graph"]["edges"].items()
                if key.split(".", 1)[1] in CHECKS
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = {
        name: capture(args.oracle, args.python, declared, venv)
        for name, (declared, venv) in SCENARIOS.items()
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
