"""Development-only capture: frozen legacy `ici sanitize --report` missing Python scopes.

`_missing_python_scope` turns a missing tests directory, a tests directory without test files
and an interpreter without pytest ('Pytest is unavailable: <snippet>') into one
`PythonResourceWarnings` target: ERROR when `engines.sanitize.required` is true (frozen default),
SKIP when it is false. A failing suite without ResourceWarning evidence is the always-ERROR
`_tool_failure_message` scope error; a collection error has no executed count. `--python` has pytest; `--bare` is an interpreter without it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from capture_legacy_python_sanitize import SOURCE

APP = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
FAILING = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"
BROKEN = "import ici_parity_missing_module\n\n\ndef test_add():\n    assert True\n"
# scenario: (interpreter key, files, engines.sanitize.required or None)
SCENARIOS = {
    "no_tests_dir": ("python", {"src/calc.py": APP}, None),
    "no_tests_dir_optional": ("python", {"src/calc.py": APP}, False),
    "no_test_files": ("python", {"src/calc.py": APP, "tests/helper.py": "X = 1\n"}, None),
    "pytest_unavailable": ("bare", {"src/calc.py": APP, "tests/test_calc.py": TEST}, None),
    "pytest_unavailable_optional": (
        "bare",
        {"src/calc.py": APP, "tests/test_calc.py": TEST},
        False,
    ),
    # Not missing scopes: a failing suite without ResourceWarning is `_tool_failure_message`
    # (always ERROR); a collection error has no executed count, so it is a missing scope.
    "tool_failure": ("python", {"src/calc.py": APP, "tests/test_calc.py": FAILING}, False),
    "collection_error": ("python", {"src/calc.py": APP, "tests/test_calc.py": BROKEN}, None),
}


def config(python: str, required: bool | None) -> str:
    text = (
        '[ici]\nprofile = "deep"\n[project]\nsource_dirs = ["src"]\n[engines.test]\n'
        f"python = {json.dumps(python)}\n"
    )
    if required is not None:
        text += f"[engines.sanitize]\nrequired = {str(required).lower()}\n"
    return text


def capture(oracle: Path, interpreters: dict[str, Path], scenario: tuple) -> dict[str, object]:
    key, files, required = scenario
    python = interpreters[key]
    with tempfile.TemporaryDirectory(prefix="ici-legacy-pysan-scope-") as temporary:
        root = Path(temporary).resolve()
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(config(str(python), required), encoding="utf-8")
        process = subprocess.run(
            [str(interpreters["python"]), "-m", "ici", "sanitize", "--report"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        with (root / "sanitize_report.json").open(encoding="utf-8") as handle:
            engine = json.load(handle)
    view = {
        "interpreter": key,
        "files": files,
        "config": config(f"<{key.upper()}>", required),
        "exit_code": process.returncode,
        "engine": {key: engine[key] for key in ("status", "summary", "evidence", "required")},
        "targets": [
            {
                key: target[key]
                for key in ("file_path", "start_line", "target_name", "status", "message")
            }
            for target in engine["targets"]
        ],
        "findings": sorted(
            (
                {
                    "rule_id": finding["rule_id"],
                    "path": finding["primary_location"]["path"],
                    "message": finding["message"],
                    "fingerprint": finding["fingerprint"],
                    "severity": finding["severity"],
                }
                for finding in engine["findings"]
            ),
            key=lambda finding: finding["fingerprint"],
        ),
    }
    text = json.dumps(view)
    for name, path in interpreters.items():
        text = text.replace(str(path), f"<{name.upper()}>")
    return json.loads(text.replace(str(root), "<ROOT>"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--bare", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    interpreters = {"python": args.python, "bare": args.bare}
    document = {
        "source": SOURCE,
        "runs": {
            name: capture(args.oracle, interpreters, scenario)
            for name, scenario in SCENARIOS.items()
        },
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
