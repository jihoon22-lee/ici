"""Capture frozen v0.13.0 `ici analyze --json` for the next `cpp.line`, `cpp.dup`, `cpp.cycle` and
`cpp.exception` source checks.

    python -I capture_next_source_checks.py <oracle-root> <output.json>

Development only; never called by the Rust product. Every case is a config-less tree under an empty
HOME with VCS discovery bounded by a `.hg` marker beside the project. `next_cpp_cycles.rs` writes the
same base64 trees and compares the view recorded here through the real native CLI.
"""

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CHECKS = ("cpp.line", "cpp.dup", "cpp.cycle", "cpp.exception")
RULES = ("dup.", "cycle.", "exception.")
BODY = "".join(
    f"    int v{i} = compute{i}(a, b);\n    if (v{i} > {i}) {{ total += v{i}; }}\n"
    for i in range(8)
)


def function(name):
    return f"int {name}(int a, int b) {{\n    int total = 0;\n{BODY}    return total;\n}}\n"


PAIR = function("f") + function("g")
MAIN = "int main(){}\n"
DESTRUCTORS = (
    Path(__file__).resolve().parents[3] / "parity-fixtures/cpp/exception-fixture/src/dtor.cpp"
).read_text(encoding="utf-8")
CASES = {
    "exception-destructors": {"src/dtor.cpp": DESTRUCTORS},
    "exception-clean": {"src/a.cpp": "void f() { try { g(); } catch (const E&) { h(); } }\n"},
    "exception-unparseable": {
        "src/a.cpp": "struct S { ~S() { throw 1; } };\nclass {{{\nvoid f() { try {} catch (...) {} }\n"
    },
    "dup-clones": {"src/a.cpp": PAIR},
    "dup-excluded": {
        "src/a.cpp": function("f"),
        "src/vendor/b.cpp": function("g"),
        "src/moc_c.cpp": function("h"),
    },
    "dup-all-excluded": {"src/vendor/b.cpp": function("g")},
    "dup-occurrence-limit": {"src/a.cpp": "void f(){\n" + "  tick(a, b);\n" * 2200 + "}\n"},
    "dup-unparseable": {"src/a.cpp": "int f(int a, int b) {{{\n" + BODY + "\n int g(){\n" + BODY},
    "dup-invalid-utf8": {"src/a.cpp": b"int a;//\xff\n"},
    "dup-nul": {"src/a.cpp": "int a;\0\n"},
    "dup-token-limit": {"src/a.cpp": "x " * 500001},
    "dup-form-feed": {"src/a.cpp": PAIR.replace("int total = 0;", "int total = 0;\f\n  \x0b ")},
    "line-decodes-to-nothing": {"src/a.cpp": b"\xff\xfe", "src/b.cpp": "int b;\n"},
    "line-separator-characters": {"src/a.cpp": "\x1c\n\x1f\nint a;\n"},
    "line-mixed-newlines": {"src/a.cpp": "int a;\r\r\n\rint b;\n"},
    "line-block-comment-header": {"src/a.cpp": "int a;\n" * 5, "src/b.h": "/*\n*/ int x;\n"},
    "cycle-pair": {
        "src/a.hpp": '#include "b.hpp"\n',
        "src/b.hpp": '#include "a.hpp"\n',
        "src/m.cpp": '#include "a.hpp"\n' + MAIN,
    },
    "cycle-in-comments": {
        "src/a.hpp": '// #include "b.hpp"\n',
        "src/b.hpp": '/* #include "a.hpp" */\n',
        "src/m.cpp": MAIN,
    },
    "cycle-inactive-branch": {
        "src/a.hpp": '#if 0\n#include "b.hpp"\n#endif\n',
        "src/b.hpp": '#include "a.hpp"\n',
        "src/m.cpp": MAIN,
    },
    "cycle-ambiguous": {
        "src/x/a.hpp": "int a;\n",
        "src/y/a.hpp": "int b;\n",
        "src/m.cpp": '#include "a.hpp"\n' + MAIN,
    },
    "cycle-suffix": {
        "src/x/a.hpp": '#include "y/b.hpp"\n',
        "src/y/b.hpp": '#include "x/a.hpp"\n',
        "src/m.cpp": MAIN,
    },
    "cycle-dotted": {
        "src/x/a.hpp": '#include "../y/b.hpp"\n',
        "src/y/b.hpp": '#include "./../x/a.hpp"\n',
        "src/m.cpp": MAIN,
    },
    "cycle-self": {"src/a.hpp": '#include "a.hpp"\n', "src/m.cpp": MAIN},
    "cycle-unparseable": {
        "src/a.hpp": '#include "b.hpp"\nclass {{{\n',
        "src/b.hpp": '#include "a.hpp"\n',
        "src/m.cpp": MAIN,
    },
    "cycle-invalid-utf8": {
        "src/a.cpp": b'#include "b.hpp"\n//\xff\n',
        "src/b.hpp": '#include "a.cpp"\n',
    },
    "cycle-carriage-returns": {"src/a.cpp": 'int a;\r\r#include "gone.hpp"\r', "src/b.hpp": "\n"},
    "cycle-odd-names": {
        "src/a.cpp": '#include "it\'s.h"\n#include "sp ace.h"\n#include "€.h"\n#include <vector>\n'
        '#include "../../outside.h"\n#include "/abs.h"\n#include "./a.cpp"\n'
    },
    "cycle-two-groups": {
        "src/a.hpp": '#include "b.hpp"\n',
        "src/b.hpp": '#include "c.hpp"\n',
        "src/c.hpp": '#include "a.hpp"\n',
        "src/d.hpp": '#include "e.hpp"\n',
        "src/e.hpp": '#include "d.hpp"\n',
        "src/m.cpp": MAIN,
    },
}


def view(document):
    findings = []
    for finding in document["findings"]:
        if finding["rule_id"].startswith(RULES):
            findings.append(
                {
                    key: finding.get(key)
                    for key in (
                        "rule_id",
                        "message",
                        "severity",
                        "confidence",
                        "evidence",
                        "primary_location",
                        "related_locations",
                        "task_id",
                    )
                }
            )
    return {
        "checks": [
            {key: check.get(key) for key in ("check_id", "task_id", "state", "evidence_complete")}
            for check in document["checks"]
            if check["check_id"] in CHECKS
        ],
        "findings": findings,
        "metrics": [
            {
                key: metric.get(key)
                for key in ("name", "value", "numerator", "denominator", "evidence")
            }
            for metric in document["metrics"]
            if metric["name"]
            in (
                "lines_total",
                "lines_code",
                "lines_comment",
                "lines_blank",
                "files_counted",
                "clone_groups",
                "duplicated_lines",
                "cycles_found",
            )
        ],
        "limitations": sorted(
            {
                item
                for item in document["limitations"]
                if any(
                    text in item
                    for text in (
                        "include",
                        "excluded ",
                        "duplicate",
                        "lexical",
                        "not readable",
                        "invalid-",
                    )
                )
                and not item.startswith("no compilation database")
            }
        ),
    }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {"oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188", "cases": {}}
    for name, tree in CASES.items():
        encoded = {
            path: base64.b64encode(
                content if isinstance(content, bytes) else content.encode()
            ).decode()
            for path, content in tree.items()
        }
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve() / "project"
            base.mkdir()
            (base.parent / ".hg").mkdir()
            for path, content in encoded.items():
                target = base / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(content))
            completed = subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "analyze", "--json"],
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
        case = view(json.loads(completed.stdout))
        document["cases"][name] = {"tree": encoded, "exit_code": completed.returncode, **case}
        print(
            name,
            completed.returncode,
            [check["state"] for check in case["checks"]],
            len(case["findings"]),
        )
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
