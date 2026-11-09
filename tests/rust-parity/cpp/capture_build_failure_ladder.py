"""Capture frozen v0.13.0 direct g++ build failures through `ici build` and `ici verify`.

    python -I capture_build_failure_ladder.py <oracle-root> <output.json>

Development only; never called by the Rust product. `ici build` writes no report, so each case
also runs `ici verify --profile deep --report` to keep the located build engine targets and tool evidence.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = '[project]\nsource_dirs = ["src"]\n'
# Only the verify run needs it: `ici build` selects the engine itself.
ENABLE_BUILD = "[engines.build]\nenabled = true\n"
SOURCE = "int main() { return 0; }\n"
# Case name: the body of the `g++` wrapper; None leaves g++ off PATH entirely.
CASES = {
    "build-no-gxx": None,
    "build-gxx-exit1": 'echo "boom error" >&2; exit 1',
    "build-gxx-exit2": "echo bad >&2; exit 2",
    "build-gxx-signal": "kill -9 $$",
    "build-gxx-trunc": 'head -c 1100000 /dev/zero | tr "\\0" x; echo',
    "build-gxx-nobinary": "exit 0",
}
REAL_GXX = 'exec /usr/bin/g++ "$@"'
# Real compiler diagnostics: tree of source files for each case. The compiler stderr holds project
# paths and the message keeps its first 200 characters, so these roots have a fixed length.
REAL_CASES = {
    "real-compile-error": {"src/main.cpp": "int main() { return missing; }\n"},
    "real-link-error": {"src/main.cpp": "int f();\nint main() { return f(); }\n"},
    "real-fixit": {"src/main.cpp": "int main() { return 0 }\n"},
    "real-later-source": {
        "src/a.cpp": "int a = 1;\n",
        "src/b.cpp": "int b = bad_b;\n",
        "src/c.cpp": "int c = bad_c;\n",
        "src/main.cpp": "int main() { return 0; }\n",
    },
    "real-two-errors": {
        "src/a.cpp": "int a = bad_a;\n",
        "src/main.cpp": "int main() { return bad_b; }\n",
    },
}
ROOT_LENGTH = 64
# Everything `_compile_cpp` decides around the compiler call. "raw" replaces the whole wrapper,
# "mode" is engines.build.mode, "setup" is what already sits at the output binary.
EDGE_CASES = {
    "output-symlink": {"body": "exit 0", "setup": "symlink"},
    "output-directory": {"body": "exit 0", "setup": "directory"},
    "gxx-cannot-run": {"raw": "#!/no/such/interpreter\n"},
    "gxx-exit1-pass-warn": {"body": 'echo "boom error" >&2; exit 1', "mode": "pass_warn"},
}
OUTPUT = "v1.0.0/x86_64/bin/project"
TIMEOUT_BODY = "sleep 400"
TARGET_FIELDS = ("file_path", "start_line", "target_name", "status", "message")
EVIDENCE_FIELDS = ("name", "returncode", "timed_out", "truncated", "error")
FINDING_FIELDS = ("rule_id", "message", "severity", "category", "tool_name", "primary_location")
PRIVATE = {".ici", ".ici-native", "__pycache__", ".git"}


def listing(base):
    names = []
    for directory, children, files in os.walk(base):
        children[:] = sorted(name for name in children if name not in PRIVATE)
        relative = Path(directory).relative_to(base)
        names += [str(relative / name) + "/" for name in children]
        names += [str(relative / name) for name in sorted(files)]
    return sorted(names)


def scratch():
    padding = ROOT_LENGTH - len("/tmp") - 1 - 8
    return tempfile.TemporaryDirectory(prefix="x" * padding, dir="/tmp")


def project(root, spec, config):
    base, bin_dir = root / "project", root / "bin"
    (base / "src").mkdir(parents=True)
    (base / ".git").mkdir()
    bin_dir.mkdir()
    (base / "ici.toml").write_text(config, encoding="utf-8")
    for path, text in (spec.get("sources") or {"src/main.cpp": SOURCE}).items():
        (base / path).write_text(text, encoding="utf-8")
    body, raw = spec.get("body"), spec.get("raw")
    if body is not None or raw is not None:
        script = raw if raw is not None else f"#!/bin/bash\n{body}\n"
        (bin_dir / "g++").write_text(script, encoding="utf-8")
        (bin_dir / "g++").chmod(0o755)
    if spec.get("setup"):
        output = base / OUTPUT
        output.parent.mkdir(parents=True)
        if spec["setup"] == "symlink":
            output.symlink_to("elsewhere")
        else:
            output.mkdir()
    return base, bin_dir


def execute(oracle, root, base, bin_dir, spec, arguments):
    absent = spec.get("body") is None and spec.get("raw") is None
    path = str(bin_dir) if absent else f"{bin_dir}:/usr/bin:/bin"
    return subprocess.run(
        [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *arguments],
        cwd=base,
        capture_output=True,
        text=True,
        env={
            "HOME": str(root),
            "XDG_CONFIG_HOME": str(root),
            "PATH": path,
            "PYTHONHASHSEED": "0",
            "LC_CTYPE": "C.UTF-8",
            "GIT_CEILING_DIRECTORIES": str(root),
        },
        check=False,
    )


def run(oracle, spec):
    mode = f'mode = "{spec["mode"]}"\n' if spec.get("mode") else ""
    table = "[engines.build]\n" + mode if mode else ""
    with scratch() as temporary:
        root = Path(temporary).resolve()
        base, bin_dir = project(root, spec, CONFIG + table)
        built = execute(oracle, root, base, bin_dir, spec, ["build"])
        stdout = built.stdout
        if spec.get("setup") or spec.get("raw"):
            # The console folds a long path anywhere; the flattened text is what can be compared.
            stdout = stdout.replace("\n", "").replace(str(base), "<root>")
            stdout = stdout.replace(str(bin_dir), "<bin>")
        build = {"exit_code": built.returncode, "stdout": stdout, "files": listing(base)}
    with scratch() as temporary:
        root = Path(temporary).resolve()
        base, bin_dir = project(root, spec, CONFIG + ENABLE_BUILD + mode)
        execute(
            oracle,
            root,
            base,
            bin_dir,
            spec,
            ["verify", "--profile", "deep", "--no-cache", "--report"],
        )
        text = (base / "verify_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(bin_dir), "<bin>").replace(str(base), "<root>"))
        engine = next(item for item in report["results"] if item["engine_name"] == "build")
        verify = {
            "status": engine["status"],
            "summary": engine["summary"],
            "evidence": engine["evidence"],
            "targets": [{key: t.get(key) for key in TARGET_FIELDS} for t in engine["targets"]],
            "tool_evidence": [
                {key: item.get(key) for key in EVIDENCE_FIELDS}
                for item in engine.get("tool_evidence", [])
            ],
            "findings": [
                {key: item.get(key) for key in FINDING_FIELDS}
                for item in engine.get("findings", [])
            ],
        }
    return {"build": build, "verify": verify}


def capture_timeout(oracle, output):
    # One real run of frozen's 300 s `run_process` bound, kept apart: both commands take that long.
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "body": TIMEOUT_BODY,
        "frozen": run(oracle, {"body": TIMEOUT_BODY}),
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    if sys.argv[3:] == ["timeout"]:
        capture_timeout(oracle, output)
        return
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "config": CONFIG,
        "enable_build": ENABLE_BUILD,
        "source": SOURCE,
        "root_length": ROOT_LENGTH,
        "cases": {
            name: {"body": body, "frozen": run(oracle, {"body": body})}
            for name, body in CASES.items()
        },
        "edge_cases": {
            name: {**spec, "frozen": run(oracle, spec)} for name, spec in EDGE_CASES.items()
        },
        "real_cases": {
            name: {
                "body": REAL_GXX,
                "sources": sources,
                "frozen": run(oracle, {"body": REAL_GXX, "sources": sources}),
            }
            for name, sources in REAL_CASES.items()
        },
    }
    for name, case in {**document["edge_cases"], **document["real_cases"]}.items():
        frozen = case["frozen"]
        print(name, frozen["build"], json.dumps(frozen["verify"])[:400])
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
