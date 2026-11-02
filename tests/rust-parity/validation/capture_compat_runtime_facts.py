"""Development-only capture: frozen next `python.compat-runtime` limitations and measurements.

    python -I capture_compat_runtime_facts.py --oracle <oracle> --python <interpreter> --out <json>

Each scenario runs the immutable v0.13.0 `ici next verify --python --no-cache` with an explicit
project interpreter and records, for the compat-runtime providers, the run limitations they
added (measured runtime, the requires-python verdict or the missing floor), the
`compileall.checked` measurement and the compile-failure findings.
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
CONFIG = (
    "schema_version=1\n[workspace]\nname='w'\nprofile='standard'\n[[components]]\n"
    "id='project'\nroot='.'\nlanguages=['python']\n[components.python]\n"
    "executable=<PYTHON_JSON>\n"
)
PROJECT = '[project]\nname = "demo"\nversion = "1.0"\nrequires-python = "{}"\n'
GOOD = "def f():\n    return 1\n"
SCENARIOS = {
    "satisfied": {"app.py": GOOD, "pyproject.toml": PROJECT.format(">=3.8")},
    "outside": {"app.py": GOOD, "pyproject.toml": PROJECT.format(">=3.99")},
    "no-floor": {"app.py": GOOD, "util.py": GOOD},
}
RUNTIME = ("runtime", "requires-python", "floor")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = {}
    for name, files in SCENARIOS.items():
        with tempfile.TemporaryDirectory(prefix="ici-compat-runtime-") as temporary:
            root = Path(temporary)
            for path, text in files.items():
                (root / path).write_text(text, encoding="utf-8")
            config = CONFIG.replace("<PYTHON_JSON>", json.dumps(str(args.python)))
            (root / "ici.toml").write_text(config, encoding="utf-8")
            completed = subprocess.run(
                [str(args.python), "-m", "ici", "next", "verify", "--python", "--no-cache"],
                cwd=root,
                env=dict(os.environ, PYTHONPATH=str(args.oracle / "src"), PATH="/usr/bin:/bin"),
                capture_output=True,
                check=False,
                timeout=900,
            )
            result = json.loads((root / ".ici/next/result.json").read_text(encoding="utf-8"))
        runs[name] = {
            "files": files,
            "exit_code": completed.returncode,
            "limitations": [
                text for text in result["limitations"] if any(word in text for word in RUNTIME)
            ],
            "metrics": [item for item in result["metrics"] if item["name"] == "compileall.checked"],
            "findings": [
                {key: item[key] for key in ("rule_id", "message")}
                for item in result["findings"]
                if item["rule_id"].startswith("python.compat.")
            ],
        }
    document = {"source": SOURCE, "config": CONFIG, "runs": runs}
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(runs, indent=1))


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
