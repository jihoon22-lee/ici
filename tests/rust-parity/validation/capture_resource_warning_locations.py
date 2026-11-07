"""Capture what frozen v0.13.0 reads as a ResourceWarning location; never product code.

    python -I capture_resource_warning_locations.py <oracle-root> <output.json>

Each case records pytest's raw output under frozen's command and environment beside frozen's
`ici sanitize` verdict, saved report and uncaught exception. Frozen's parser takes the first
`.*?\\.py:<n>:...ResourceWarning` of each output line, so the raw output shows which matching lines
are a warning pytest reported and which only echo a test's source. The project root is `<root>`
and the directory outside it is `<outside>`.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = '[project]\nsource_dirs = ["src"]\n'
LEAK = "import warnings\n\n\ndef leak():\n    warnings.warn('unclosed handle', ResourceWarning)\n"
SOURCE = {"ici.toml": CONFIG, "src/__init__.py": "", "src/calc.py": LEAK}
ECHO = "    text = '/usr/lib/outside/x.py:7: ResourceWarning: unclosed'\n"
CASES = {
    # A warning raised in a project file.
    "normal": {
        **SOURCE,
        "tests/test_a.py": "from src.calc import leak\n\n\ndef test_x():\n    leak()\n",
    },
    # A warning raised in a module outside the project.
    "external": {
        **SOURCE,
        "tests/test_a.py": "import sys\n\nsys.path.insert(0, '<outside>')\n\n"
        "from ext import leak\n\n\ndef test_x():\n    leak()\n",
    },
    # No warning at all: a test fails beside source text that looks like a location.
    "echo-only": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n" + ECHO + "    raise RuntimeError('boom')\n",
    },
    # One warning, raised beside that source text.
    "echo-plus-warning": {
        **SOURCE,
        "tests/test_a.py": "from src.calc import leak\n\n\ndef test_x():\n" + ECHO + "    leak()\n",
    },
    # No warning: the location-like text is in an exception, so also in its `E` and `FAILED` lines.
    "echo-in-message": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n"
        "    raise RuntimeError('/usr/lib/outside/x.py:7: ResourceWarning: unclosed')\n",
    },
    # A warning a test's own filter keeps from raising: pytest lists it in its warnings summary.
    "summary-warning": {
        **SOURCE,
        "tests/test_a.py": "import pytest\n\nfrom src.calc import leak\n\n\n"
        "@pytest.mark.filterwarnings('default::ResourceWarning')\n"
        "def test_x():\n    leak()\n",
    },
    # Look-alikes a test only printed, in pytest's Captured sections: nothing was warned.
    "captured-stdout-look-alike": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n    print('src/calc.py:5: ' + 'ResourceWarning')\n"
        "    raise RuntimeError('boom')\n",
    },
    "captured-stderr-look-alike": {
        **SOURCE,
        "tests/test_a.py": "import sys\n\n\ndef test_x():\n"
        "    print('src/calc.py:5: ' + 'ResourceWarning', file=sys.stderr)\n"
        "    raise RuntimeError('boom')\n",
    },
    # Quoted in another exception, with no location-like text: `ResourceWarning` alone.
    "quoted-without-location": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n    raise RuntimeError('ResourceWarning: quoted')\n",
    },
    # A real warning in a real file whose name has a space and a quote.
    "file-name-with-space-and-quote": {
        **SOURCE,
        "src/it's a.py": LEAK,
        "tests/test_a.py": "import importlib.util\nfrom pathlib import Path\n\n\ndef test_x():\n"
        "    path = Path(__file__).parents[1] / 'src' / \"it's a.py\"\n"
        "    spec = importlib.util.spec_from_file_location('m', path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n    module.leak()\n",
    },
    "two-warnings": {
        **SOURCE,
        "tests/test_a.py": "from src.calc import leak\n\n\ndef test_x():\n    leak()\n\n\n"
        "def test_y():\n    leak()\n",
    },
    # Raised, not warned; and caught and replaced, so the traceback has two footers.
    "raised-directly": {
        **SOURCE,
        "tests/test_a.py": "def test_x():\n    raise ResourceWarning('unclosed')\n",
    },
    "chained": {
        **SOURCE,
        "tests/test_a.py": "from src.calc import leak\n\n\ndef test_x():\n    try:\n        leak()\n"
        "    except ResourceWarning as error:\n        raise RuntimeError('boom') from error\n",
    },
    "fixture-teardown": {
        **SOURCE,
        "tests/test_a.py": "import pytest\n\nfrom src.calc import leak\n\n\n@pytest.fixture\n"
        "def held():\n    yield 1\n    leak()\n\n\ndef test_x(held):\n    assert held == 1\n",
    },
    "at-collection": {
        **SOURCE,
        "tests/test_a.py": "from src.calc import leak\n\nleak()\n\n\ndef test_x():\n    pass\n",
    },
    # A deleted file: the warning is real and its location no longer exists.
    "file-gone": {
        **SOURCE,
        "tests/test_a.py": "import importlib.util\nimport os\nfrom pathlib import Path\n\n\n"
        "def test_x():\n    path = Path(__file__).parents[1] / 'src' / 'gone.py'\n"
        "    path.write_text('import warnings\\n\\n\\ndef leak():\\n    warnings.warn(\"x\", ResourceWarning)\\n')\n"
        "    spec = importlib.util.spec_from_file_location('gone', path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n    os.remove(path)\n    module.leak()\n",
    },
    # The interpreter's own warning, for a file object nothing closed.
    "unclosed-file": {
        **SOURCE,
        "tests/test_a.py": "import gc\n\n\ndef test_x():\n    open(__file__)\n    gc.collect()\n",
    },
}


# What a caller's `PYTEST_ADDOPTS` makes of a real warning's report.
TRACEBACK_STYLES = {
    f"traceback-{style}": {
        "tree": CASES["normal"],
        "addopts": f"--tb={style}",
    }
    for style in ("short", "line", "native", "no")
}
NEXT_CONFIG = (
    "schema_version=1\n[workspace]\nname='w'\nprofile='deep'\n[[components]]\n"
    "id='project'\nroot='.'\nlanguages=['python']\n[components.python]\nexecutable=<python>\n"
)


def run_case(oracle, tree, addopts=""):
    with (
        tempfile.TemporaryDirectory() as home,
        tempfile.TemporaryDirectory() as temporary,
        tempfile.TemporaryDirectory(dir="/tmp") as outside,
    ):
        base = Path(temporary).resolve() / "project"
        for relative, text in tree.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.replace("<outside>", outside), encoding="utf-8")
        (Path(outside) / "ext.py").write_text(LEAK, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"}
        if addopts:
            environment["PYTEST_ADDOPTS"] = addopts

        def public(text):
            return text.replace(str(base), "<root>").replace(outside, "<outside>")

        pytest = subprocess.run(
            [
                sys.executable,
                "-W",
                "error::ResourceWarning",
                "-m",
                "pytest",
                "-o",
                "addopts=",
                "tests",
            ],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                **environment,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_ADDOPTS": f"{addopts} -p no:cacheprovider".strip(),
            },
            check=False,
        )
        record = {"tree": tree, "pytest_stdout": public(pytest.stdout)}

        def run(*arguments):
            return subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "sanitize", *arguments],
                cwd=base,
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )

        plain = run()
        record["without_report"] = {
            "exit_code": plain.returncode,
            "stdout": public(plain.stdout.strip()),
        }
        reported = run("--report")
        record["exit_code"] = reported.returncode
        saved = base / "sanitize_report.json"
        if saved.exists():
            record["report"] = json.loads(public(saved.read_text(encoding="utf-8")))
        else:
            record["report"] = None
            lines = reported.stderr.strip().splitlines() or [""]
            record["uncaught"] = public(lines[-1])
        return record


def next_checks(oracle):
    """Frozen `next verify`'s checks for a failing Python project: is there a ResourceWarning run?"""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        tree = {
            **CASES["normal"],
            "ici.toml": NEXT_CONFIG.replace("<python>", json.dumps(sys.executable)),
        }
        for relative, text in tree.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                ENTRY,
                str(oracle / "src"),
                "next",
                "plan",
                "--json",
                "--profile",
                "deep",
            ],
            cwd=base,
            capture_output=True,
            text=True,
            env={"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"},
            check=False,
        )
        return {
            "exit_code": completed.returncode,
            "plan": completed.stdout.replace(str(base), "<root>"),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
        "cases": {name: run_case(oracle, tree) for name, tree in CASES.items()},
        "next_plan": next_checks(oracle),
    }
    for name, case in TRACEBACK_STYLES.items():
        document["cases"][name] = run_case(oracle, case["tree"], case["addopts"])
        document["cases"][name]["addopts"] = case["addopts"]
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        report = case["report"]
        print("==", name, case["exit_code"], case["without_report"], case.get("uncaught"))
        if report:
            print("  ", report["status"], report["evidence"], report["summary"])
            print("  ", [(t["file_path"], t["start_line"], t["status"]) for t in report["targets"]])


if __name__ == "__main__":
    main()
