"""Capture frozen v0.13.0 C++ lint with no g++ on PATH, as `ici lint` and through `ici verify`.

    python -I capture_lint_no_compiler.py <oracle-root> <output.json>

Development only; never called by the Rust product.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
SOURCE = "int main() { return 0; }\n"
COMMANDS = {
    "lint": ["lint", "--report"],
    "verify": ["verify", "--profile", "deep", "--no-cache", "--report"],
}
REPORTS = {"lint": "lint_report.json", "verify": "verify_report.json"}
TARGET_FIELDS = ("target_name", "status", "message", "start_line", "file_path")
EVIDENCE_FIELDS = ("name", "path", "returncode", "error")
EXTRA_FIELDS = (
    "cpp_analysis_mode",
    "cpp_configurations_checked",
    "cpp_sources_checked",
    "cpp_missing_context_sources",
)


def run(oracle, command):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        base, tools = root / "project", root / "bin"
        (base / "src").mkdir(parents=True)
        (base / ".git").mkdir()
        tools.mkdir()
        (base / "ici.toml").write_text(CONFIG, encoding="utf-8")
        (base / "src/a.cpp").write_text(SOURCE, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *COMMANDS[command]],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root),
                "PATH": str(tools),
                "PYTHONHASHSEED": "0",
                "LC_CTYPE": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": str(root),
            },
            check=False,
        )
        report = json.loads((base / REPORTS[command]).read_text(encoding="utf-8"))
        engine = report
        if command == "verify":
            engine = next(item for item in report["results"] if item["engine_name"] == "lint")
        return {
            "exit_code": completed.returncode,
            "status": engine["status"],
            "evidence": engine["evidence"],
            "summary": engine["summary"],
            "targets": [{key: t.get(key) for key in TARGET_FIELDS} for t in engine["targets"]],
            "tool_evidence": [
                {key: item.get(key) for key in EVIDENCE_FIELDS} for item in engine["tool_evidence"]
            ],
            "extra": {key: engine["extra"].get(key) for key in EXTRA_FIELDS},
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "config": CONFIG,
        "source": SOURCE,
        "cases": {command: run(oracle, command) for command in COMMANDS},
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps(document["cases"], ensure_ascii=False)[:1400])


if __name__ == "__main__":
    main()
