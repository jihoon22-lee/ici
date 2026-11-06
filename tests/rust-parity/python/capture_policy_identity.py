#!/usr/bin/env python3
"""What a next baseline makes of a fixed test finding after an `ici.toml` edit.

Each case runs `next verify` with a failing test, fixes the test, edits the configuration as the
case says and compares against the first result. Comment and layout edits say nothing new about
policy, and a moved checkout is the same project; the others change policy, the tool or the
selected scope.

usage: capture_policy_identity.py <python> <output.json> --frozen <oracle-src> | --native <ici>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = "def called(value):\n    if value:\n        return 1\n    return 0\n"
FAILING = "import app\ndef test_called():\n    assert app.called(True) == 2\n"
# A different size, so a same-second edit cannot run the stale bytecode of the failing test.
PASSING = "import app\n\n\ndef test_called():\n    assert app.called(True) == 1\n"
PYPROJECT = (
    "[project]\nname='fixture'\nversion='1'\nrequires-python='>=3.10'\n"
    "[tool.pytest.ini_options]\npythonpath=['src']\n"
)
HEAD = "schema_version=1\n[workspace]\nname='fixture'\n"


def python_table(executable: str, tests: str = "tests") -> str:
    return (
        f"[{{}}python]\nexecutable={json.dumps(executable)}\n"
        f"test_tools='project'\ntest_paths=['{tests}']\n"
    )


def config(
    executable: str, sources: str = "src/**/*.py", tests: str = "tests", head: str = HEAD
) -> str:
    return (
        head
        + f"[[components]]\nid='app'\nroot='.'\nlanguages=['python']\nsources=['{sources}']\n"
        + python_table(executable, tests).format("components.")
    )


def child(executable: str, tests: str = "tests") -> str:
    return (
        "schema_version=1\n[component]\nroot='.'\nlanguages=['python']\nsources=['src/**/*.py']\n"
        + python_table(executable, tests).format("")
    )


def layout(executable: str) -> str:
    """The same settings written differently: order, quotes, spacing, comments."""
    return (
        '# reformatted\n\nschema_version = 1\n\n[workspace]\nname = "fixture"  # same\n\n'
        '[[components]]\nlanguages = ["python"]\nroot = "."\nid = "app"\n'
        'sources = [\n  "src/**/*.py",\n]\n\n[components.python]\ntest_paths = ["tests"]\n'
        f'test_tools = "project"\nexecutable = {json.dumps(executable)}\n'
    )


def edits(python: str, base: Path) -> dict:
    """Case name: (configuration before the fix, after it), each `path: text`."""
    link = base / "elsewhere" / "python"
    link.parent.mkdir()
    link.symlink_to(python)
    parent = Path(python).parent
    dotted = str(parent / ".." / parent.name / Path(python).name)
    plain = config(python)
    root = {"ici.toml": plain}
    outer = HEAD + "[[components]]\nid='app'\nconfig='ici-app.toml'\n"
    children = {"ici.toml": outer, "ici-app.toml": child(python)}
    required = HEAD + '[checks."python.coverage"]\nrequired=false\n'
    disabled = HEAD + '[checks."python.complexity"]\nenabled=false\n'
    no_effect = HEAD + "[checks.complexity]\nenabled=false\n"
    return {
        "same": (root, root),
        "comment": (root, {"ici.toml": plain + "# a note about this project\n"}),
        "layout": (root, {"ici.toml": layout(python)}),
        "moved": (root, root),
        "child-same": (children, children),
        "child-comment": (children, {**children, "ici-app.toml": child(python) + "# a note\n"}),
        "profile": (
            root,
            {"ici.toml": plain.replace("name='fixture'", "name='fixture'\nprofile='deep'")},
        ),
        "check-required": (root, {"ici.toml": config(python, head=required)}),
        "check-disabled": (root, {"ici.toml": config(python, head=disabled)}),
        "check-no-effect": (root, {"ici.toml": config(python, head=no_effect)}),
        "selection": (root, {"ici.toml": config(python, sources="src/app.py")}),
        "test-paths": (root, {"ici.toml": config(python, tests="tests/test_app.py")}),
        "tool-path": (root, {"ici.toml": config(str(link))}),
        "dotted-path": (root, {"ici.toml": config(dotted)}),
        "child-test-paths": (
            children,
            {**children, "ici-app.toml": child(python, tests="tests/test_app.py")},
        ),
    }


def run(tool: list, root: Path, base: Path, oracle: str | None, *args: str) -> int:
    product = {"PYTHONPATH": oracle} if oracle else {}
    return subprocess.run(
        [*tool, "next", "verify", *args],
        cwd=root,
        env={
            "HOME": str(base),
            "PATH": "/usr/bin:/bin",
            "GIT_CEILING_DIRECTORIES": str(base),
            **product,
        },
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def template(files: dict, python: str, base: Path) -> dict:
    """The case's files with its host paths as placeholders, for the Rust test to fill in."""
    return {
        path: text.replace(str(base / "elsewhere" / "python"), "{link}")
        .replace(str(Path(python).parent / ".." / Path(python).parent.name), "{dotted_dir}")
        .replace(python, "{python}")
        for path, text in files.items()
    }


def scenario(tool: list, python: str, oracle: str | None, name: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        before, after = edits(python, base)[name]
        written_before, written_after = (
            template(before, python, base),
            template(after, python, base),
        )
        root = base / "project"
        (root / "src").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "src/app.py").write_text(SOURCE)
        (root / "tests/test_app.py").write_text(FAILING)
        (root / "pyproject.toml").write_text(PYPROJECT)
        for path, text in before.items():
            (root / path).write_text(text)
        code = run(tool, root, base, oracle, "--result", "first.json")
        if not (root / "first.json").exists():
            raise SystemExit(f"{name}: first run exited {code} without a result")
        (root / "tests/test_app.py").write_text(PASSING)
        if name == "moved":
            moved = base / "moved"
            shutil.move(root, moved)
            root = moved
        for path, text in after.items():
            (root / path).write_text(text)
        code = run(tool, root, base, oracle, "--result", "now.json", "--baseline", "first.json")
        first = json.loads((root / "first.json").read_text())
        now = json.loads((root / "now.json").read_text())
    failed = [f["fingerprint"] for f in first["findings"] if f["rule_id"] == "pytest.failed"]
    baseline = now["baseline"]
    before = {c["check_id"]: c["scope_digest"] for c in first["checks"]}
    after = {c["check_id"]: c["scope_digest"] for c in now["checks"]}
    return {
        "before": written_before,
        "after": written_after,
        "exit": code,
        "failed_findings": len(failed),
        "state": baseline["state"],
        "fixed_test_is": sorted(
            key
            for key in ("resolved", "carried", "new", "unchanged")
            if any(fingerprint in baseline[key] for fingerprint in failed)
        ),
        "moved_scopes": sorted(c for c in after if before.get(c) != after[c]),
    }


def main() -> None:
    python, output, mode, value = sys.argv[1:5]
    tool = [python, "-m", "ici"] if mode == "--frozen" else [value]
    oracle = value if mode == "--frozen" else None
    with tempfile.TemporaryDirectory() as directory:
        names = list(edits(python, Path(directory)))
    results = {name: scenario(tool, python, oracle, name) for name in names}
    document = {"failing": FAILING, "passing": PASSING, "source": SOURCE, "cases": results}
    document["pyproject"] = PYPROJECT
    Path(output).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
