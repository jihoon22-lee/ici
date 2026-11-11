"""Development-only capture: frozen next gate verdicts and reasons of the validation checks.

    python -I -B capture_next_validation_gates.py --oracle <oracle-root> --python <py> \
        --bare-python <py-without-pytest> --out <json>

Frozen `ici next verify` and `ici analyze` (schema_version=1, one Python component) with the
static tool checks (python.lint, python.format, python.type) switched off, so every gate reason
belongs to the validation checks (python.test, python.coverage, python.compat-runtime) or to the
static analyses that need no external tool. `analyze` omits tests, runtime checks and build
artifact checks; its note and gate are recorded beside `next verify`'s. Every run records the
exit code, the gate (selected verdict and every reason, literally), each check's state and
evidence completeness, and the console's non-path lines.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
GREEN = "def test_ok():\n    pass\n"
FAILING = "def test_bad():\n    assert 1 == 2\n\n\ndef test_ok():\n    pass\n"
BROKEN = "import nonexistent_mod\n\n\ndef test_x():\n    pass\n"
SKIPPED = "import pytest\n\n\n@pytest.mark.skip(reason='later')\ndef test_later():\n    pass\n"
# scenario: (interpreter: "python" | "bare" | None, files)
SCENARIOS = {
    "green": ("python", {"tests/test_a.py": GREEN}),
    "failing": ("python", {"tests/test_a.py": FAILING}),
    "collection": ("python", {"tests/test_a.py": BROKEN}),
    "all-skipped": ("python", {"tests/test_a.py": SKIPPED}),
    "no-tests": ("python", {}),
    "bare-interpreter": ("bare", {"tests/test_a.py": GREEN}),
    "no-interpreter": (None, {"tests/test_a.py": GREEN}),
}
COMMANDS = (("next", "verify", "--no-cache"), ("analyze", "--no-cache"))


def config(executable: str | None) -> str:
    text = (
        "schema_version=1\n[workspace]\nname='w'\nprofile='standard'\n"
        '[checks."python.lint"]\nenabled=false\n'
        '[checks."python.format"]\nenabled=false\n'
        '[checks."python.type"]\nenabled=false\n'
        "[[components]]\nid='app'\nroot='.'\nlanguages=['python']\n"
    )
    if executable is not None:
        text += f"[components.python]\nexecutable={json.dumps(executable)}\n"
    return text


def run(oracle: Path, python: Path, interpreter: str | None, files: dict, argv: tuple) -> dict:
    with tempfile.TemporaryDirectory(prefix="ici-next-validation-gates-") as temporary:
        base = Path(temporary).resolve()
        (base / "home").mkdir()
        root = base / "p"
        root.mkdir()
        (root / "tests").mkdir()
        (root / "app.py").write_text("X = 1\n", encoding="utf-8")
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(config(interpreter), encoding="utf-8")
        done = subprocess.run(
            [str(python), "-B", "-m", "ici", *argv],
            cwd=root,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(base / "home"),
                "PYTHONPATH": str(oracle / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        result_path = root / ".ici/next/result.json"
        result = json.loads(result_path.read_text("utf-8")) if result_path.exists() else None
    view: dict[str, object] = {
        "exit_code": done.returncode,
        "console": (done.stdout + done.stderr).splitlines(),
    }
    if result is not None:
        view["gate"] = {key: result["gate"][key] for key in ("selected", "reasons")}
        view["checks"] = {
            check["check_id"]: [check["state"], check["evidence_complete"]]
            for check in result["checks"]
        }
    text = json.dumps(view).replace(str(root), "<ROOT>")
    if interpreter is not None:
        text = text.replace(interpreter, "<PYTHON>")
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--bare-python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    interpreters = {"python": str(args.python), "bare": str(args.bare_python), None: None}
    document = {
        "source": SOURCE,
        "config": config("<PYTHON>"),
        "config_without_interpreter": config(None),
        "runs": {
            name: {
                "interpreter": interpreter,
                "files": files,
                **{
                    " ".join(argv[:-1]): run(
                        args.oracle, args.python, interpreters[interpreter], files, argv
                    )
                    for argv in COMMANDS
                },
            }
            for name, (interpreter, files) in SCENARIOS.items()
        },
    }
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")


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
