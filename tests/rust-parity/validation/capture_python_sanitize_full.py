"""Capture frozen v0.13.0 `ici sanitize --report` of Python projects; never product code.

    python -I capture_python_sanitize_full.py <oracle-root> <output.json>

Frozen `_check_python_resource_warnings` runs `<python> -W error::ResourceWarning -m pytest -o
addopts= tests` with `-p no:cacheprovider` in `PYTEST_ADDOPTS`, and its summary, target, finding
and tool evidence embed 300 characters of pytest's plain output. Each case runs a fresh small
project (empty HOME, PATH=/usr/bin:/bin) and records the saved report whole, with the project root
as `<root>`. The root has a fixed length (`root_length`), so the replay's snippet is cut at the
same character.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_LENGTH = 96
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = '[project]\nsource_dirs = ["src"]\n'
SOURCE = {"ici.toml": CONFIG, "src/__init__.py": "", "src/calc.py": "def f():\n    return 1\n"}
PASSING = "from src.calc import f\n\n\ndef test_ok():\n    assert f() == 1\n"
FAILING = PASSING + "\n\ndef test_bad():\n    assert f() == 2\n"
LEAK = (
    "import warnings\n\n\ndef read(path):\n"
    "    warnings.warn(f'unclosed file {path}', ResourceWarning)\n    return 'x'\n"
)
CASES = {
    # A failing suite without ResourceWarning: frozen's tool failure embeds pytest's output.
    "failing": {**SOURCE, "tests/test_a.py": FAILING},
    "passing": {**SOURCE, "tests/test_a.py": PASSING},
    # A project `addopts` selecting tests or naming an unknown flag does not apply.
    "addopts-deselects-the-failure": {
        **SOURCE,
        "tests/test_a.py": FAILING,
        "pytest.ini": "[pytest]\naddopts = -k test_ok\n",
    },
    "addopts-unknown-flag": {
        **SOURCE,
        "tests/test_a.py": PASSING,
        "pytest.ini": "[pytest]\naddopts = --no-such-flag\n",
    },
    "addopts-verbose": {
        **SOURCE,
        "tests/test_a.py": FAILING,
        "pyproject.toml": '[tool.pytest.ini_options]\naddopts = "-vv --tb=line"\n',
    },
    # `source_dirs` are on PYTHONPATH.
    "source-dir-import": {
        **SOURCE,
        "tests/test_a.py": "from calc import f\n\n\ndef test_ok():\n    assert f() == 1\n",
    },
    "located": {
        **SOURCE,
        "src/calc.py": LEAK,
        "tests/test_a.py": "from src.calc import read\n\n\ndef test_read():\n"
        "    assert read('x.txt') == 'x'\n",
    },
    # One location printed twice: frozen targets every matching line.
    "located-twice": {
        **SOURCE,
        "src/calc.py": LEAK,
        "tests/test_a.py": "from src.calc import read\n\n\ndef test_one():\n"
        "    assert read('x.txt') == 'x'\n\n\ndef test_two():\n    assert read('y.txt') == 'x'\n",
    },
    # A location outside the project: frozen `canonical_project_path` raises out of the engine.
    "located-outside": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n"
        "    print('/usr/lib/outside/x.py:7: ResourceWarning: unclosed')\n"
        "    raise RuntimeError('boom')\n",
    },
    # Only the printed line matches: the source echo does not spell the location.
    "located-outside-only": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n"
        "    print('/usr/lib/outside/x' + '.py:7: Resource' + 'Warning: unclosed')\n"
        "    raise RuntimeError('boom')\n",
    },
    "unlocated": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n    raise RuntimeError('ResourceWarning')\n",
    },
    "collection-error": {**SOURCE, "tests/test_a.py": "import no_such_module_here\n"},
    "no-tests-collected": {**SOURCE, "tests/test_a.py": "X = 1\n"},
    # `\b<n> (passed|failed|xfailed|xpassed)\b`: a skip executes nothing, an xfail does.
    "skipped-only": {
        **SOURCE,
        "tests/test_a.py": "import pytest\n\n\n@pytest.mark.skip\ndef test_x():\n    pass\n",
    },
    "xfail-only": {
        **SOURCE,
        "tests/test_a.py": "import pytest\n\n\n@pytest.mark.xfail\ndef test_x():\n    assert 0\n",
    },
    # Bytes that are no UTF-8, read with `errors="replace"`.
    "output-not-utf8": {
        **SOURCE,
        "tests/test_a.py": FAILING,
        "conftest.py": "import os\n\n\ndef pytest_unconfigure(config):\n"
        "    os.write(1, b'\\xff marker\\n')\n",
    },
    # The terminal reporter is gone: a passing suite has no count to read.
    "no-summary": {
        **SOURCE,
        "tests/test_a.py": PASSING,
        "conftest.py": "import pytest\n\n\n@pytest.hookimpl(trylast=True)\n"
        "def pytest_configure(config):\n"
        "    reporter = config.pluginmanager.getplugin('terminalreporter')\n"
        "    if reporter is not None:\n"
        "        config.pluginmanager.unregister(reporter)\n",
    },
}
# What the caller set stays in `PYTEST_ADDOPTS`, before frozen's `-p no:cacheprovider`.
ENVIRONMENT = {
    "caller-addopts-deselects-the-failure": {"PYTEST_ADDOPTS": "-k test_ok"},
    "caller-addopts-quiet": {"PYTEST_ADDOPTS": "-q"},
}
CASES["caller-addopts-deselects-the-failure"] = {**SOURCE, "tests/test_a.py": FAILING}
CASES["caller-addopts-quiet"] = {**SOURCE, "tests/test_a.py": FAILING}


def run_case(oracle, name, tree):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        parent = Path(temporary).resolve()
        base = parent / ("p" * (ROOT_LENGTH - len(str(parent)) - 1))
        for relative, text in tree.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        caller = ENVIRONMENT.get(name, {})
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin", **caller}

        def run(*arguments):
            return subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "sanitize", *arguments],
                cwd=base,
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )

        completed = run("--report")
        saved = base / "sanitize_report.json"
        if not saved.exists():
            # Frozen died before saving a report: its last line is the uncaught exception.
            lines = completed.stderr.strip().splitlines() or [""]
            # The same run without `--report` saves nothing, so it shows the engine's verdict.
            plain = run()
            return {
                "tree": tree,
                "exit_code": completed.returncode,
                "report": None,
                "uncaught": lines[-1],
                "without_report": {"exit_code": plain.returncode, "stdout": plain.stdout.strip()},
            }
        report = saved.read_text(encoding="utf-8")
        return {
            "tree": tree,
            "environment": caller,
            "exit_code": completed.returncode,
            "report": json.loads(report.replace(str(base), "<root>")),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
        "root_length": ROOT_LENGTH,
        "cases": {name: run_case(oracle, name, tree) for name, tree in CASES.items()},
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        report = case["report"]
        if report is None:
            print(name, case["exit_code"], "no report:", case["uncaught"], case["without_report"])
            continue
        targets = [(t["file_path"], t["target_name"], t["status"]) for t in report["targets"]]
        print(name, case["exit_code"], report["status"], report["evidence"], targets)
        print("  ", report["summary"][:150])
        print("  ", [t["error"][:90] for t in report["tool_evidence"]])


if __name__ == "__main__":
    main()
