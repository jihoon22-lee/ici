"""Capture frozen v0.13.0 `ici dup --report` verdicts of a hybrid (Python + C++) project.

    python -I capture_hybrid_dup.py <oracle-root> <output.json>

Each case runs the frozen stable `ici dup --report` in a fresh small hybrid project (empty HOME,
PATH without tools) whose Python file alone is 50% duplicated while the one scan over both
languages is 15.4%, and records the saved engine report with the project root as `<root>`.
`under`: both thresholds above the merged rate (Python alone crosses `warn_pct`), PASS with the
clone targets realigned to PASS; `fail-alone`: Python alone crosses `fail_pct` under
`pass_warn_fail`, still PASS; `merged`: the merged rate crosses `warn_pct`, WARN; `both`: the
C++ file has a clone group too, so frozen numbers the lexical groups of both languages in one
order before the Python AST-shape group.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
PYTHON = (
    "def first(values):\n"
    "    total = 0\n"
    "    for value in values:\n"
    "        if value > 10:\n"
    "            total += value * 2\n"
    "        else:\n"
    "            total -= value\n"
    "    return total\n"
    "\n"
    "\n"
    "def second(items):\n"
    "    result = 0\n"
    "    for item in items:\n"
    "        if item > 10:\n"
    "            result += item * 2\n"
    "        else:\n"
    "            result -= item\n"
    "    return result\n"
)
CPP = (
    "#include <vector>\n"
    "\n"
    "struct Point {\n"
    "    int x;\n"
    "    int y;\n"
    "};\n"
    "\n"
    "int area(const Point& a, const Point& b) {\n"
    "    return (b.x - a.x) * (b.y - a.y);\n"
    "}\n"
    "\n"
    "bool inside(const Point& p, int limit) {\n"
    "    if (p.x < 0 || p.y < 0) {\n"
    "        return false;\n"
    "    }\n"
    "    return p.x <= limit && p.y <= limit;\n"
    "}\n"
    "\n"
    "int count_positive(const std::vector<int>& values) {\n"
    "    int count = 0;\n"
    "    for (int value : values) {\n"
    "        count += value > 0 ? 1 : 0;\n"
    "    }\n"
    "    return count;\n"
    "}\n"
    "\n"
    "const char* name(int code) {\n"
    "    switch (code) {\n"
    "    case 0:\n"
    '        return "zero";\n'
    "    case 1:\n"
    '        return "one";\n'
    "    default:\n"
    '        return "many";\n'
    "    }\n"
    "}\n"
    "\n"
    "long scale(long base, int shift) {\n"
    "    while (shift-- > 0) {\n"
    "        base <<= 1;\n"
    "    }\n"
    "    return base;\n"
    "}\n"
)
CPP_CLONE_BODY = (
    "    int total = 0;\n"
    "    for (int value : values) {\n"
    "        if (value > 10) {\n"
    "            total += value * 2;\n"
    "        } else {\n"
    "            total -= value;\n"
    "        }\n"
    "    }\n"
    "    return total;\n"
    "}\n"
)
CPP_WITH_CLONE = (
    CPP
    + "\nint first(const std::vector<int>& values) {\n"
    + CPP_CLONE_BODY
    + "\nint second(const std::vector<int>& values) {\n"
    + CPP_CLONE_BODY
)
CASES = {
    "under": "[engines.dup]\nwarn_pct = 40.0\nfail_pct = 90.0\n",
    "fail-alone": '[engines.dup]\nwarn_pct = 20.0\nfail_pct = 45.0\nmode = "pass_warn_fail"\n',
    "merged": "[engines.dup]\nwarn_pct = 10.0\nfail_pct = 90.0\n",
    "both": "[engines.dup]\nwarn_pct = 5.0\nfail_pct = 90.0\n",
}


def run_case(oracle, name, config):
    cpp = CPP_WITH_CLONE if name == "both" else CPP
    tree = {"ici.toml": config, "src/dup.py": PYTHON, "src/lib.cpp": cpp}
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in tree.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": os.defpath}
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "dup", "--report"],
            cwd=base,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        report = (base / "dup_report.json").read_text(encoding="utf-8")
        return {
            "tree": tree,
            "exit_code": completed.returncode,
            "report": json.loads(report.replace(str(base), ROOT)),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "cases": {name: run_case(oracle, name, config) for name, config in CASES.items()},
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        statuses = [t["status"] for t in case["report"]["targets"]]
        print(name, case["exit_code"], case["report"]["status"], statuses)


if __name__ == "__main__":
    main()
