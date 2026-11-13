"""Capture frozen v0.13.0 `ici dead --report` C++ source policy by include_generated/include_vendor.

    python -I capture_dead_source_policy.py <oracle-root> <work-dir> <output.json>

Development only; never called by the Rust product. Frozen `DeadCodeEngine` reads the C/C++
candidates through `read_analysis_sources(include_generated=..., include_vendor=...)`
(`analysis_exclusion_reasons`: a vendor/third-party directory part, a generated directory part
or `moc_`/`qrc_`/`ui_` name; a source carrying both needs both opt-ins). Each case is a legacy
`type='cpp'` project with a production, a generated, a vendor, a both-excluded and a Qt moc
source, and `cpp_unused` 'auto' with and without a recorded compilation database (the real g++
runs the unused-function scope over the database). `legacy_cpp_dead_source_policy.rs`
compares the status, exit code, evidence, requiredness, summary, source inventory counts, every
target field and the findings' rule, wording and location.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
FINDING_FIELDS = ("rule_id", "message", "confidence", "severity")
SOURCE_EXTRA = (
    "source_exclusion_counts",
    "source_files_excluded",
    "source_files_analyzed",
    "source_files_snapshotted",
    "source_bytes_analyzed",
)
TREE = {
    "src/main.cpp": "static int unused_main() { return 1; }\nint main() { return 0; }\n",
    "src/generated/gen.cpp": "static int unused_gen() { return 2; }\nint gen() { return 2; }\n",
    "src/vendor/lib.cpp": "static int unused_lib() { return 3; }\nint lib() { return 3; }\n",
    "src/vendor/generated/both.cpp": "static int unused_both() { return 4; }\n"
    "int both() { return 4; }\n",
    "src/moc_view.cpp": "static int unused_moc() { return 5; }\nint moc() { return 5; }\n",
}
SOURCES = sorted(TREE)
OPTIONS = {
    "neither": "",
    "generated": "include_generated=true\n",
    "vendor": "include_vendor=true\n",
    "both": "include_generated=true\ninclude_vendor=true\n",
}


def database(project: Path) -> str:
    return json.dumps(
        [
            {
                "directory": str(project),
                "file": source,
                "arguments": ["g++", "-std=c++17", "-c", source, "-o", f"{index}.o"],
            }
            for index, source in enumerate(SOURCES)
        ]
    )


def main():
    oracle, work, output = Path(sys.argv[1]), Path(sys.argv[2]).resolve(), Path(sys.argv[3])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "tree": TREE,
        "options": OPTIONS,
        "gxx": subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0],
        "cases": {},
    }
    for with_database in (False, True):
        for option, lines in OPTIONS.items():
            name = f"{'database' if with_database else 'no-database'}:{option}"
            shutil.rmtree(work, ignore_errors=True)
            project, home = work / "project", work / "home"
            project.mkdir(parents=True)
            home.mkdir()
            (project / ".hg").mkdir()
            config = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
            if with_database:
                config += "compile_database='compile_commands.json'\n"
            config += "[engines.dead]\ncpp_unused='auto'\n" + lines
            (project / "ici.toml").write_text(config, encoding="utf-8")
            for path, content in TREE.items():
                target = project / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            if with_database:
                (project / "compile_commands.json").write_text(database(project), "utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "dead", "--report"],
                cwd=project,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(home),
                    "LC_ALL": "C.UTF-8",
                    "GIT_CEILING_DIRECTORIES": str(work),
                    "PYTHONHASHSEED": "0",
                },
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            text = (project / "dead_report.json").read_text(encoding="utf-8")
            report = json.loads(text.replace(str(project), "<root>"))
            document["cases"][name] = {
                "database": with_database,
                "option": option,
                "exit_code": completed.returncode,
                "status": report["status"],
                "evidence": report["evidence"],
                "required": report["required"],
                "summary": report["summary"],
                "source_extra": {key: report["extra"].get(key) for key in SOURCE_EXTRA},
                "findings": [
                    {key: finding.get(key) for key in FINDING_FIELDS}
                    | {
                        "path": finding["primary_location"]["path"],
                        "start_line": finding["primary_location"]["start_line"],
                    }
                    for finding in report["findings"]
                ],
                "targets": [
                    {key: target.get(key) for key in FIELDS} for target in report["targets"]
                ],
            }
            print(name, completed.returncode, report["status"], report["summary"][:120])
    shutil.rmtree(work, ignore_errors=True)
    output.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(document['cases'])} cases")


main()
