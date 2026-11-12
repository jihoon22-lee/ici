"""Development-only capture: frozen legacy `ici sanitize --report` engine-level verdicts.

    python -I -B capture_legacy_sanitize_engine.py --oracle <oracle-root> --python <py> --out <json>

Frozen `SanitizeEngine.run` (engines/sanitize.py) decides the Python and C++ scopes in
`_analysis_scopes`, appends the `Sanitize` SKIP at '.' when neither is in play (L200) and derives
the engine verdict in `_overall_verdict`: tool errors -> ERROR/NOT_RUN; measured and skipped
scopes -> the partial summary (WARN unless a scope failed), ESTIMATED; measured only ->
MEASURED with the clean or '<n> Memory / Resource Defect(s) Detected' summary under the
configured `mode`; neither -> SKIP, ESTIMATED when a scope was skipped, else NOT_APPLICABLE.
These scenarios reach those branches from the Python scope, alone and beside the C++ scope
(a C++ source without `tests/*.cpp` is the not-required `C++Sanitizer` skipped scope; a direct
g++ test is a measured C++ scope). `--python` is a project CPython with pytest; g++ comes from
PATH=/usr/bin:/bin like the frozen `shutil.which("g++")`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
APP = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
LEAK = (
    "import warnings\n\n\ndef read(path):\n"
    "    warnings.warn(f'unclosed file {path}', ResourceWarning)\n    return 'x'\n"
)
LEAK_TEST = "from leak import read\n\n\ndef test_read():\n    assert read('x.txt') == 'x'\n"
CPP = "int twice(int value) { return value * 2; }\n"
CPP_TEST = "int twice(int value);\nint main() { return twice(2) == 4 ? 0 : 1; }\n"
PY = {"src/calc.py": APP, "tests/test_calc.py": TEST}
PY_LEAK = {**PY, "src/leak.py": LEAK, "tests/test_leak.py": LEAK_TEST}
# scenario: (project type or None, files, extra [engines.sanitize] lines)
SCENARIOS = {
    # Neither scope: frozen L200 `Sanitize` SKIP at '.', NOT_APPLICABLE, exit 2.
    "noscope-python-type": ("python", {"docs/guide.md": "# guide\n"}, ""),
    "noscope-undeclared": (None, {"docs/guide.md": "# guide\n"}, ""),
    # A python project with an empty tests directory keeps its Python scope (type widening).
    "python-type-empty-tests": ("python", {"tests/data.txt": "x\n"}, ""),
    # No Python source: `_has_python_tests` (any tests/**/*.py) puts the scope in play.
    "tests-helper-only": (None, {"tests/helper.py": "X = 1\n"}, ""),
    "measured-clean": (None, PY, ""),
    "measured-clean-optional": (None, PY, "required = false\n"),
    "measured-leak": (None, PY_LEAK, ""),
    "measured-leak-pass-warn": (None, PY_LEAK, 'mode = "pass_warn"\n'),
    "measured-leak-pass-warn-fail": (None, PY_LEAK, 'mode = "pass_warn_fail"\n'),
    # Python measured beside the skipped C++ scope: the partial verdict.
    "hybrid-partial-clean": ("hybrid", {**PY, "src/twice.cpp": CPP}, ""),
    "hybrid-partial-leak": ("hybrid", {**PY_LEAK, "src/twice.cpp": CPP}, ""),
    "cpp-type-partial-clean": ("cpp", {**PY, "src/twice.cpp": CPP}, ""),
    # Both scopes skipped (optional Python without tests, C++ without tests): SKIP, ESTIMATED.
    "hybrid-both-skipped": (
        "hybrid",
        {"src/calc.py": APP, "src/twice.cpp": CPP},
        "required = false\n",
    ),
    # Both scopes measured (direct g++ sanitizer test and pytest).
    "hybrid-both-measured": (
        "hybrid",
        {**PY, "src/twice.cpp": CPP, "tests/test_twice.cpp": CPP_TEST},
        "",
    ),
    "hybrid-both-measured-leak": (
        "hybrid",
        {**PY_LEAK, "src/twice.cpp": CPP, "tests/test_twice.cpp": CPP_TEST},
        "",
    ),
    # A tests-only tree: `_has_python_tests` keeps the scope, so pytest runs.
    "tests-only": (None, {"tests/test_calc.py": "def test_add():\n    assert 1 + 2 == 3\n"}, ""),
    # Undeclared: `detect_project_type` falls back to python, and the tests path widens it.
    "undeclared-empty-tests": (None, {"tests/data.txt": "x\n"}, ""),
    # A declared hybrid project without sources: the Python scope only.
    "hybrid-empty-tests": ("hybrid", {"tests/data.txt": "x\n"}, ""),
    # Negative: a cpp project's tests path without Python is no Python scope.
    "noscope-cpp-type-tests-data": ("cpp", {"tests/data.txt": "x\n"}, ""),
    # The reverse partial: C++ measured, the optional Python scope skipped.
    "hybrid-cpp-measured-python-skipped": (
        "hybrid",
        {"src/calc.py": APP, "src/twice.cpp": CPP, "tests/test_twice.cpp": CPP_TEST},
        "required = false\n",
    ),
    "hybrid-partial-leak-pass-warn": (
        "hybrid",
        {**PY_LEAK, "src/twice.cpp": CPP},
        'mode = "pass_warn"\n',
    ),
}
# Scenarios also captured through deep `ici verify --report` (the sanitize engine only).
VERIFY = ("hybrid-partial-clean", "hybrid-both-measured", "hybrid-both-measured-leak")


def config(python: str, project_type: str | None, sanitize: str) -> str:
    text = '[ici]\nprofile = "deep"\n[project]\n'
    if project_type:
        text += f"type = '{project_type}'\n"
    text += f'source_dirs = ["src"]\n[engines.test]\npython = {json.dumps(python)}\n'
    if sanitize:
        text += f"[engines.sanitize]\n{sanitize}"
    return text


def capture(
    oracle: Path, python: Path, scenario: tuple, command: str = "sanitize"
) -> dict[str, object]:
    project_type, files, sanitize = scenario
    with tempfile.TemporaryDirectory(prefix="ici-legacy-sanitize-engine-") as temporary:
        root = Path(temporary).resolve() / "project"
        home = Path(temporary).resolve() / "home"
        home.mkdir()
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(config(str(python), project_type, sanitize), "utf-8")
        process = subprocess.run(
            [str(python), "-B", "-m", "ici", command, "--report"],
            cwd=root,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "PYTHONPATH": str(oracle / "src"),
                "LC_CTYPE": "C.UTF-8",
            },
            capture_output=True,
            check=False,
            timeout=900,
        )
        report = root / f"{command}_report.json"
        engine = json.loads(report.read_text("utf-8")) if report.exists() else None
        if engine is not None and command == "verify":
            engine = next(item for item in engine["results"] if item["engine_name"] == "sanitize")
        created = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and path.relative_to(root).as_posix() not in files
            and not path.relative_to(root).as_posix().startswith(".ici/")
        )
    view: dict[str, object] = {
        "type": project_type,
        "files": files,
        "sanitize": sanitize,
        "exit_code": process.returncode,
        "created": created,
    }
    if engine is not None:
        view["engine"] = {
            key: engine[key] for key in ("status", "summary", "evidence", "required", "extra")
        }
        view["targets"] = [
            {
                key: target[key]
                for key in ("file_path", "start_line", "target_name", "status", "message")
            }
            for target in engine["targets"]
        ]
        view["findings"] = sorted(
            (
                {
                    "rule_id": finding["rule_id"],
                    "path": finding["primary_location"]["path"],
                    "start_line": finding["primary_location"]["start_line"],
                    "message": finding["message"],
                    "fingerprint": finding["fingerprint"],
                    "severity": finding["severity"],
                    "tool_name": finding["tool_name"],
                }
                for finding in engine["findings"]
            ),
            key=lambda finding: finding["fingerprint"],
        )
        view["tool_evidence"] = [item["name"] for item in engine.get("tool_evidence", [])]
    text = json.dumps(view).replace(str(python), "<PYTHON>").replace(str(root), "<ROOT>")
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "runs": {
            name: capture(args.oracle, args.python, scenario)
            for name, scenario in SCENARIOS.items()
        },
        "verify": {
            name: capture(args.oracle, args.python, SCENARIOS[name], "verify") for name in VERIFY
        },
    }
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
