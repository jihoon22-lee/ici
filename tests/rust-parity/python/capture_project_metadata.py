#!/usr/bin/env python3
"""Frozen project name and version on real CLI runs: metadata files, Git and refusals.

Each case is a whole fixture description, so the native test builds exactly what ran here.

usage: capture_project_metadata.py <oracle-src> <python> <output.json>
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = 'type = "python"\n[project]\nname = "p"\n'
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
INIT = [["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "one"]]


def py(name: str, version: str) -> str:
    return f"[project]\nname={name}\nversion={version}\n"


def ici(top: str) -> str:
    return 'type = "python"\n' + top + '[project]\nname = "p"\n'


VERIFY = {
    "all-valid": {"files": {"ici.toml": ici('name="t"\nversion="2"\n')}},
    "pyproject": {"files": {"pyproject.toml": py('"q"', '"5"')}},
    "dev-then-pyproject": {
        "files": {"dev.toml": 'name="d"\n', "pyproject.toml": py('"q"', '"5"')},
    },
    "v-prefix-kept-once": {"files": {"pyproject.toml": py('"q"', '"vv5"')}},
    "first-valid-later-bad-version": {
        "files": {"ici.toml": ici('version="2"\n'), "pyproject.toml": py('"q"', '"bad version"')},
    },
    "first-valid-later-bad-name": {
        "files": {"ici.toml": ici('name="t"\n'), "pyproject.toml": py('"bad name"', '"5"')},
    },
    "first-bad-later-valid": {
        "files": {"ici.toml": ici('version="bad version"\n'), "pyproject.toml": py('"q"', '"5"')},
    },
    "version-number-in-ici": {"files": {"ici.toml": ici("version=2\n")}},
    "name-number-in-ici": {"files": {"ici.toml": ici("name=2\n")}},
    "version-empty-in-ici": {"files": {"ici.toml": ici('version=""\n')}},
    "name-empty-in-ici": {"files": {"ici.toml": ici('name=""\n')}},
    "version-number-in-pyproject": {"files": {"pyproject.toml": py('"q"', "5")}},
    "name-array-in-pyproject": {"files": {"pyproject.toml": py('["a", 1]', '"5"')}},
    "version-bool-in-pyproject": {"files": {"pyproject.toml": py('"q"', "true")}},
    "version-empty-in-pyproject": {"files": {"pyproject.toml": py('"q"', '""')}},
    "name-empty-in-pyproject": {"files": {"pyproject.toml": py('""', '"5"')}},
    "version-only-v": {"files": {"ici.toml": ici('version="v"\n')}},
    "name-dot": {"files": {"ici.toml": ici('name="."\n')}},
    "name-dotdot": {"files": {"pyproject.toml": py('".."', '"1"')}},
    "name-newline": {"files": {"ici.toml": ici('name="a\\n"\n')}},
    "version-newline": {"files": {"ici.toml": ici('version="1\\n"\n')}},
    "name-unicode": {"files": {"ici.toml": ici('name="é"\n')}},
    "name-quote": {"files": {"pyproject.toml": py('''"it's"''', '"5"')}},
    "pyproject-malformed": {"files": {"pyproject.toml": "[project\n"}},
    "pyproject-project-not-table": {"files": {"pyproject.toml": 'project = "x"\n'}},
    "pyproject-empty-table": {"files": {"pyproject.toml": "[project]\n"}},
    "pyproject-binary": {"bytes": {"pyproject.toml": "ff00"}},
    "dev-malformed": {"files": {"dev.toml": "= bad\n"}},
    "dev-binary": {"bytes": {"dev.toml": "ff00"}},
    "dev-directory": {"directories": ["dev.toml"]},
    "dev-fifo": {"fifos": ["dev.toml"]},
    "dev-symlink-escape": {
        "outside": {"out.toml": 'name="linked"\n'},
        "symlinks": {"dev.toml": "../out.toml"},
    },
    "dev-bad-version": {"files": {"dev.toml": 'version="bad version"\n'}},
    "dev-version-number": {"files": {"dev.toml": "version=2\n"}},
    "dev-name-empty": {"files": {"dev.toml": 'name=""\n'}},
    "dev-unknown-key": {"files": {"dev.toml": 'nonsense="x"\nname="d"\n'}},
    "pyproject-directory": {"directories": ["pyproject.toml"]},
    "pyproject-fifo": {"fifos": ["pyproject.toml"]},
    "symlink-inside": {
        "files": {"meta.toml": py('"linked"', '"7"')},
        "symlinks": {"pyproject.toml": "meta.toml"},
    },
    "symlink-inside-malformed": {
        "files": {"meta.toml": "[project\n"},
        "symlinks": {"pyproject.toml": "meta.toml"},
    },
    "symlink-to-directory": {
        "directories": ["meta"],
        "symlinks": {"pyproject.toml": "meta"},
    },
    "symlink-escape": {
        "outside": {"out.toml": py('"linked"', '"7"')},
        "symlinks": {"pyproject.toml": "../out.toml"},
    },
    "symlink-dangling-inside": {"symlinks": {"pyproject.toml": "nothing.toml"}},
    "symlink-dangling-outside": {"symlinks": {"pyproject.toml": "../nothing.toml"}},
    "large-1.5MiB": {
        "files": {"pyproject.toml": py('"big"', '"9"')},
        "padding": {"pyproject.toml": 1536 * 1024},
    },
    "directory-name-with-space": {"directory": "my project"},
    "directory-name-unicode": {"directory": "é"},
    "git-untagged": {"git": INIT},
    "git-tag-at-head": {"git": [*INIT, ["tag", "v1.2"]]},
    "git-tag-without-v": {"git": [*INIT, ["tag", "1.2"]]},
    "git-lightweight-after-tag": {
        "git": [*INIT, ["tag", "v1.2"], ["commit", "-qm", "two", "--allow-empty"]],
    },
    "git-annotated-after-tag": {
        "git": [
            *INIT,
            ["tag", "-a", "v1.3", "-m", "x"],
            ["commit", "-qm", "two", "--allow-empty"],
        ],
    },
    "git-tag-refused-spelling": {"git": [*INIT, ["tag", "rel/1"]]},
    "git-version-in-file-wins": {
        "files": {"pyproject.toml": py('"q"', '"5"')},
        "git": [*INIT, ["tag", "v1.2"]],
    },
    "git-no-commits": {"git": [["init", "-q", "-b", "main"]]},
    "git-unavailable": {"git": [*INIT, ["tag", "v1.2"]], "no_git_on_path": True},
    "git-not-a-repository": {},
}
# The commands that stop on the same reader; everything else tolerates these files.
OTHER = {
    "dead": [
        "first-valid-later-bad-version",
        "pyproject-malformed",
        "symlink-escape",
        "dev-malformed",
        "pyproject",
    ],
    "export-compilation-context": [
        "first-valid-later-bad-version",
        "first-valid-later-bad-name",
        "version-number-in-pyproject",
        "pyproject-malformed",
        "dev-malformed",
        "pyproject-directory",
        "symlink-inside",
        "symlink-escape",
        "directory-name-with-space",
        "pyproject",
    ],
    "line": ["first-valid-later-bad-version", "pyproject-malformed", "symlink-escape"],
}


def build(base: Path, case: dict) -> Path:
    root = base / case.get("directory", "project")
    (root / "src").mkdir(parents=True)
    (root / "src/a.py").write_text("x = 1\n")
    files = {"ici.toml": CONFIG, **case.get("files", {})}
    for name, text in files.items():
        (root / name).write_text(text)
    for name, size in case.get("padding", {}).items():
        with (root / name).open("a") as stream:
            stream.write("#" + "x" * size + "\n")
    for name, text in case.get("bytes", {}).items():
        (root / name).write_bytes(bytes.fromhex(text))
    for name, text in case.get("outside", {}).items():
        (base / name).write_text(text)
    for name in case.get("directories", []):
        (root / name).mkdir()
    for name in case.get("fifos", []):
        os.mkfifo(root / name)
    for name, target in case.get("symlinks", {}).items():
        (root / name).symlink_to(target)
    for arguments in case.get("git", []):
        subprocess.run(
            ["git", *arguments],
            cwd=root,
            env={"PATH": "/usr/bin:/bin", "HOME": str(base), **GIT_ENV},
            check=True,
        )
    return root


def message(stderr: str, command: str) -> str | None:
    """Frozen's refusal text: after the traceback's `ValueError:`, or its own one-line prefix."""
    marker = (
        "Compilation export error: " if command == "export-compilation-context" else "ValueError: "
    )
    if marker not in stderr:
        return None
    return stderr.rsplit(marker, 1)[1].strip()


def run(oracle: str, python: str, command: str, case: dict) -> dict:
    base = Path(tempfile.mkdtemp()).resolve()
    try:
        (base / ".hg").write_text("")
        root = build(base, case)
        path = "/nonexistent" if case.get("no_git_on_path") else "/usr/bin:/bin"
        arguments = (
            ["--no-cache", "--report", "--html", "h.html", "--profile", "fast"]
            if command == "verify"
            else []
        )
        done = subprocess.run(
            [python, "-m", "ici", command, *arguments],
            cwd=root,
            env={
                "HOME": str(base),
                "PATH": path,
                "GIT_CEILING_DIRECTORIES": str(base),
                "PYTHONPATH": oracle,
            },
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        report = root / "verify_report.json"
        project = None
        if report.exists():
            saved = json.loads(report.read_text())["analysis_context"]["project"]
            project = {"name": saved["name"], "version": saved["version"]}
        page = root / "h.html"
        title = None
        if page.exists():
            title = page.read_text().split("<title>", 1)[1].split("</title>", 1)[0]
        last = [line for line in done.stderr.splitlines() if line.strip()]
        refusal = message(done.stderr, command)
        if refusal is None and "Configuration error: " in done.stderr:
            refusal = done.stderr.split("Configuration error: ", 1)[1].strip()
        return {
            "exit": done.returncode,
            "project": project,
            "title": title,
            "traceback": "Traceback" in done.stderr,
            "refusal": refusal and refusal.replace(str(root), "<root>"),
            "stderr_lines": len(last),
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> None:
    oracle, python, output = sys.argv[1:4]
    runs = [("verify", name) for name in VERIFY]
    runs += [(command, name) for command, names in OTHER.items() for name in names]
    captured = [
        {"command": command, "name": name, "case": VERIFY[name]}
        | {"frozen": run(oracle, python, command, VERIFY[name])}
        for command, name in runs
    ]
    Path(output).write_text(
        json.dumps({"git_env": GIT_ENV, "runs": captured}, indent=1, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
