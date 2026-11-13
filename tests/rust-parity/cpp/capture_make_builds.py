"""Development-only frozen `ici build` captures for every `[build.make]` key value.

    python -I -B capture_make_builds.py

Never called by Rust. One real GNU Make build per case (oracle 6fe3e405) through the frozen CLI's
own `_print_engine_result` (same driver as capture_build_targets.py), with a `make` wrapper first
on PATH that logs each call (project-relative cwd and argv) before running the real make. The
cases cover enabled, workdir, shadow_dir, out_of_tree, configure_argv, build_argv ({jobs}),
test_argv, clean_argv and jobs: accepted values, refused values (frozen configuration exit 2) and
failing steps. Records exit, console, stderr, status, summary, evidence, targets, the Make calls
and the project paths a run creates.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
MAKE = shutil.which("make")
GXX = shutil.which("g++")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
DRIVER = """
import json, sys
import ici.__main__ as cli
rendered = cli._print_engine_result
OUT = sys.argv[1]
def record(result):
    rendered(result)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump({"status": result.status.value, "summary": result.summary,
                   "evidence": result.evidence.value,
                   "targets": [{"target_name": t.target_name, "status": t.status.value,
                                "message": t.message, "start_line": t.start_line,
                                "file_path": t.file_path} for t in result.targets]}, handle)
cli._print_engine_result = record
sys.argv = ["ici", "build"]
cli.main()
"""
# Logs "<cwd>|<argv...>" and runs the real make (absolute path recorded in the oracle).
WRAPPER = '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$*" >> "$ICI_MAKE_LOG"\nexec {make} "$@"\n'
MAKEFILE = (
    "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app\n"
    "configure:\n\tmkdir -p build/ici-make\n\ttouch build/ici-make/configured\n"
    "clean:\n\trm -f build/ici-make/app\n"
    "shadowed:\n\tmkdir -p out\n\tg++ src/main.cpp -o out/app\n"
    "broken:\n\texit 3\n"
)
NATIVE_MAKEFILE = (
    "all:\n\tmkdir -p ../build/ici-make\n\tg++ ../src/main.cpp -o ../build/ici-make/app\n"
)
TREE = {
    "src/main.cpp": "int main(){return 0;}\n",
    "Makefile": MAKEFILE,
    "pyproject.toml": "[project]\nname='makecfg'\nversion='1.2.3'\n",
}
HEAD = "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\n"
# case: (`[build.make]` body, extra files)
CASES = {
    "enabled-defaults": ("enabled=true\nbuild_argv=['make','all']\n", {}),
    "jobs": ("enabled=true\njobs=3\nbuild_argv=['make','-j','{jobs}','all']\n", {}),
    "workdir": (
        "enabled=true\nworkdir='native'\nbuild_argv=['make','all']\n",
        {"native/Makefile": NATIVE_MAKEFILE},
    ),
    "out-of-tree-required": (
        "enabled=true\nworkdir='native'\nout_of_tree='required'\nbuild_argv=['make','all']\n",
        {"native/Makefile": NATIVE_MAKEFILE},
    ),
    "out-of-tree-allow": ("enabled=true\nout_of_tree='allow'\nbuild_argv=['make','all']\n", {}),
    "shadow-dir": ("enabled=true\nshadow_dir='out'\nbuild_argv=['make','shadowed']\n", {}),
    "configure-argv": (
        "enabled=true\nconfigure_argv=['make','configure']\nbuild_argv=['make','all']\n",
        {},
    ),
    "clean-argv": (
        "enabled=true\nclean_argv=['make','clean']\nbuild_argv=['make','all']\n",
        {},
    ),
    "test-argv": (
        "enabled=true\ntest_argv=['make','check']\nbuild_argv=['make','all']\n",
        {},
    ),
    "all-steps": (
        "enabled=true\njobs=2\nconfigure_argv=['make','configure']\n"
        "clean_argv=['make','clean']\nbuild_argv=['make','--jobs','{jobs}','all']\n",
        {},
    ),
    "configure-fails": (
        "enabled=true\nconfigure_argv=['make','broken']\nbuild_argv=['make','all']\n",
        {},
    ),
    "clean-fails": (
        "enabled=true\nclean_argv=['make','broken']\nbuild_argv=['make','all']\n",
        {},
    ),
    "build-fails": ("enabled=true\nbuild_argv=['make','broken']\n", {}),
    "disabled": ("enabled=false\nbuild_argv=['make','all']\n", {}),
    # Refused by the frozen configuration loader (exit 2, nothing runs).
    "unknown-key": ("enabled=true\nbuild_argv=['make','all']\nbogus=1\n", {}),
    "enabled-not-bool": ("enabled='yes'\nbuild_argv=['make','all']\n", {}),
    "jobs-zero": ("enabled=true\njobs=0\nbuild_argv=['make','all']\n", {}),
    "jobs-over": ("enabled=true\njobs=65\nbuild_argv=['make','all']\n", {}),
    "jobs-string": ("enabled=true\njobs='4'\nbuild_argv=['make','all']\n", {}),
    "out-of-tree-bogus": ("enabled=true\nout_of_tree='bogus'\nbuild_argv=['make','all']\n", {}),
    "out-of-tree-root": ("enabled=true\nout_of_tree='required'\nbuild_argv=['make','all']\n", {}),
    "shadow-root": ("enabled=true\nshadow_dir='.'\nbuild_argv=['make','all']\n", {}),
    "workdir-escape": ("enabled=true\nworkdir='../x'\nbuild_argv=['make','all']\n", {}),
    "build-argv-empty": ("enabled=true\nbuild_argv=[]\n", {}),
    "build-argv-missing": ("enabled=true\n", {}),
    "argv-shell": ("enabled=true\nbuild_argv=['sh','-c','make']\n", {}),
    "argv-variable": ("enabled=true\nbuild_argv=['make','$X']\n", {}),
    "argv-placeholder": ("enabled=true\nbuild_argv=['make','{oops}']\n", {}),
    "configure-argv-string": (
        "enabled=true\nconfigure_argv='make'\nbuild_argv=['make','all']\n",
        {},
    ),
}
PRIVATE = {".ici", "__pycache__", ".git"}


def listing(base):
    names = []
    for directory, children, files in os.walk(base):
        children[:] = sorted(name for name in children if name not in PRIVATE)
        relative = Path(directory).relative_to(base)
        names += [str(relative / name) + "/" for name in children]
        names += [str(relative / name) for name in sorted(files)]
    return sorted(name.removeprefix("./") for name in names)


def run(make, files):
    work = Path(tempfile.mkdtemp(prefix="ici-make-builds-")).resolve()
    try:
        project = work / "project"
        tools = work / "tools"
        tools.mkdir()
        (tools / "make").write_text(WRAPPER.format(make=MAKE), encoding="utf-8")
        (tools / "make").chmod(0o755)
        for name, text in {**TREE, **files, "ici.toml": HEAD + make}.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8")
        before = set(listing(project))
        log = work / "make.log"
        record = work / "result.json"
        completed = subprocess.run(
            [PYTHON, "-B", "-c", DRIVER, str(record)],
            cwd=project,
            env={
                "PATH": os.pathsep.join([str(tools), str(Path(GXX).parent), "/usr/bin", "/bin"]),
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
                "LC_ALL": "C.UTF-8",
                "ICI_MAKE_LOG": str(log),
                "GIT_CEILING_DIRECTORIES": str(work),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        engine = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        created = sorted(set(listing(project)) - before)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    text = json.dumps(
        {
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "engine": engine,
            "calls": [call.replace(str(project), "<project>", 1) for call in calls],
            "created": created,
        },
        ensure_ascii=False,
    )
    result = json.loads(text.replace(str(work), "<work>"))
    if engine is not None:
        for target in result["engine"]["targets"]:
            target["message"] = re.sub(r"\(\d+\.\d\ds\)", "(<seconds>s)", target["message"])
    return result


def main():
    cases = {}
    for name, (make, files) in CASES.items():
        cases[name] = {"make": make, "files": files, "frozen": run(make, files)}
        print(name, json.dumps(cases[name]["frozen"], ensure_ascii=False)[:500])
    document = {
        "oracle": EXPECTED,
        "tree": TREE,
        "head": HEAD,
        "wrapper": WRAPPER,
        "cases": cases,
    }
    (ROOT / "tests/rust-parity/cpp/make-builds-oracle.json").write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} make build cases")


if __name__ == "__main__":
    main()
