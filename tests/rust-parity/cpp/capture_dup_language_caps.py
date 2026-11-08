"""Capture frozen v0.13.0 `ici dup --report` on hybrid trees around the source-file cap.

    python -I capture_dup_language_caps.py <oracle-root> <output.json>

Development only; never called by the Rust product. Frozen scans Python and C++ in one intake
bounded by `MAX_ANALYSIS_SOURCE_FILES = 2048`; native keeps that cap per language (approved
resource-policy difference, decisions.md "hybrid dup 언어별 한도"). `within-language-caps` has
1100 one-line sources of each language (2200 in all, no duplicate window); `cpp-over-cap` has
2049 C++ sources beside one Python source. Trees are generated from `TREES` by both sides.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = "[project]\ntype='hybrid'\nsource_dirs=['src']\n"
# name: (Python sources, C++ sources)
TREES = {"within-language-caps": (1100, 1100), "cpp-over-cap": (1, 2049)}


def tree(python, cpp):
    files = {f"src/p{index:04}.py": f"x{index} = {index}\n" for index in range(python)}
    files.update({f"src/c{index:04}.cpp": f"int c{index};\n" for index in range(cpp)})
    return files


def run_case(oracle, files):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in {**files, "ici.toml": CONFIG}.items():
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "dup", "--report"],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": temporary,
            },
            check=False,
        )
        report = json.loads((base / "dup_report.json").read_text(encoding="utf-8"))
        return {
            "exit_code": completed.returncode,
            "status": report["status"],
            "evidence": report["evidence"],
            "summary": report["summary"],
            "targets": [
                {key: target[key] for key in ("target_name", "status", "message", "file_path")}
                for target in report["targets"]
            ],
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "config": CONFIG,
        "cases": {},
    }
    for name, (python, cpp) in TREES.items():
        document["cases"][name] = {
            "python_sources": python,
            "cpp_sources": cpp,
            "frozen": run_case(oracle, tree(python, cpp)),
        }
        print(name, json.dumps(document["cases"][name]["frozen"])[:300])
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
