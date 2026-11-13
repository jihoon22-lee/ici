"""Development-only frozen captures under a C locale (root ruling 2026-11-14 d4); never called by Rust.

Frozen ran under CPython 3.10, which coerces a C locale (LC_ALL unset or empty and LC_CTYPE, else
LANG, empty, C or POSIX) to LC_CTYPE=C.UTF-8 in its own environment at startup (PEP 538), so every
child it ran inherited LC_CTYPE=C.UTF-8. Two trees show that end to end:

- `gxx`: a `type = 'cpp'` project with src/twice.cpp, tests/test_ok.cpp and tests/test_broken.cpp
  (an undeclared name in `main`), run by the direct g++ path of `ici test`. g++ quotes
  `int main()` with U+2018/U+2019 quotes only under a UTF-8 LC_CTYPE, so the first line of the failed
  compile's "Compilation Error: " message shows which locale g++ inherited.
- `python`: a `type = 'python'` project whose pytest case reads /proc/self/environ (the
  environment the pytest process was started with, before its own interpreter could coerce
  anything) and passes only when LC_CTYPE=C.UTF-8 was inherited.

Each tree runs twice: `c-locale` with no locale variable at all (the coercion applies), and the
control `lc-all-c` with LC_ALL=C (PEP 538 leaves a set LC_ALL alone: ASCII quotes, the Python case
fails). Each case runs the frozen oracle `ici test --report` in a fresh `<work>/project` (with
`.git`) under PATH=/usr/bin:/bin, keeping the exit code, status, summary and every target (name,
status, message, file and lines; the project root written `<root>`, the work directory's parent
`<tmp>`, the project interpreter `<PYTHON>`).

Usage: `capture_c_locale.py --python <CPython with pytest and coverage>`.
"""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
GOLDEN = ROOT / "tests/rust-parity/cpp/c-locale-oracle.json"
FIELDS = ("target_name", "status", "message", "file_path", "start_line", "end_line")
PYTHON_TEST = (
    "def test_inherited_locale():\n"
    '    with open("/proc/self/environ", "rb") as stream:\n'
    '        entries = stream.read().split(b"\\0")\n'
    '    inherited = b"LC_CTYPE=C.UTF-8" in entries\n'
    '    assert inherited, "LC_CTYPE was not inherited"\n'
)
TREES = {
    "gxx": (
        "[project]\ntype = 'cpp'\nsource_dirs = ['src']\n",
        {
            "src/twice.cpp": "int twice(int value) { return value * 2; }\n",
            "tests/test_ok.cpp": "int twice(int value);\nint main() { return twice(2) == 4 ? 0 : 1; }\n",
            "tests/test_broken.cpp": "int main() { return missing; }\n",
        },
    ),
    "python": (
        "[project]\ntype = 'python'\nsource_dirs = ['src']\n[engines.test]\npython = \"<PYTHON>\"\n",
        {
            "src/calc.py": "def add(a, b):\n    return a + b\n",
            "tests/test_locale.py": PYTHON_TEST,
        },
    ),
}
LOCALES = {"c-locale": {}, "lc-all-c": {"LC_ALL": "C"}}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(python: str, config: str, files: dict, locale: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cpp-c-locale-"))
    try:
        project = work / "project"
        for relative, text in files.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        (project / ".git").mkdir()
        (project / "ici.toml").write_text(config.replace("<PYTHON>", python), encoding="utf-8")
        result = subprocess.run(
            [python, "-B", "-m", "ici", "test", "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
                **locale,
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        text = (project / "test_report.json").read_text(encoding="utf-8")
        text = text.replace(str(project), "<root>").replace(str(work.parent), "<tmp>")
        report = json.loads(text.replace(python, "<PYTHON>"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
    cases = {}
    for tree, (config, files) in TREES.items():
        for locale_name, locale in LOCALES.items():
            name = f"{tree}:{locale_name}"
            cases[name] = {
                "config": config,
                "files": files,
                "locale": locale,
                "frozen": run(args.python, config, files, locale),
            }
            print(name, cases[name]["frozen"]["exit_code"], cases[name]["frozen"]["status"])
    GOLDEN.write_text(
        json.dumps(
            {"oracle": EXPECTED, "capture_python": args.python, "gxx": gxx, "cases": cases},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
