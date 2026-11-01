"""Development-only frozen legacy binary_compat captures; never called by the Rust product.

Frozen BinaryCompatibilityEngine runs only in the deep pipeline after the build engine
publishes its artifact manifest, so each case runs `ici verify --profile deep --report` with
the build and binary_compat engines enabled and records those two engine results.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-binary-compat-capture"
TOOLS = sorted({str(Path(shutil.which(name)).parent) for name in ("make", "g++", "readelf")})
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
FINDING_FIELDS = (
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "tool_name",
    "tool_rule_id",
    "primary_location",
    "fingerprint",
)
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
ENABLE = "[engines.build]\nenabled=true\n[engines.binary_compat]\nenabled=true\n"
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
NON_ELF_MAKE = (
    "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app\n"
    "\tprintf 'MZ-not-elf' > build/ici-make/tool.exe\n\tchmod +x build/ici-make/tool.exe\n"
)
NOTHING_MAKE = "all:\n\tmkdir -p build/ici-make\n\ttouch build/ici-make/notes.txt\n"
# A binary that needs libstdc++ (GLIBCXX 3.4.x, CXXABI 1.3.x) besides libc.
ABI_SOURCE = (
    "#include <stdexcept>\n#include <string>\nint main(int argc, char **) {\n"
    "  std::string text(static_cast<unsigned>(argc) + 40U, 'x');\n"
    "  try { if (argc > 5) throw std::runtime_error(text); }\n"
    "  catch (const std::exception &error) { return static_cast<int>(error.what()[0]); }\n"
    "  return static_cast<int>(text.size() % 2U);\n}\n"
)
ABI_MAKE = "all:\n\tmkdir -p build/ici-make\n\tg++ src/abi.cpp -o build/ici-make/app\n"
ABI = {"src/abi.cpp": ABI_SOURCE, "Makefile": ABI_MAKE}
# One absolute vendor loader path and one inside the project (a build root).
RPATH_MAKE = (
    "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app"
    " -Wl,-rpath,/opt/vendor/lib -Wl,-rpath,$(CURDIR)/build/ici-make\n"
)
TWO_MAKE = (
    "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app\n"
    "\tg++ src/main.cpp -o build/ici-make/tool\n"
)
CASES = {
    "pass": {},
    "violations": {
        "ici.toml": BASE
        + ENABLE
        + "forbidden_needed=['libc.so.6']\nmax_glibc='2.0'\nforbid_build_paths=true\n"
    },
    "non-elf-allowed": {
        "Makefile": NON_ELF_MAKE,
        "ici.toml": BASE + ENABLE + "allow_non_elf=true\n",
    },
    "non-elf-rejected": {"Makefile": NON_ELF_MAKE},
    "no-artifacts": {"Makefile": NOTHING_MAKE},
    "no-artifacts-required": {
        "Makefile": NOTHING_MAKE,
        "ici.toml": BASE + ENABLE + "required=true\n",
    },
    "unpublished-selection": {"ici.toml": BASE + ENABLE + "artifacts=['release:shadow:nope']\n"},
    "missing-readelf": {},
    "truncated-readelf": {},
    # One case per remaining engines.binary_compat key (config_key rows); each differs from a
    # reference case only in that key.
    "disabled": {
        "ici.toml": BASE + "[engines.build]\nenabled=true\n[engines.binary_compat]\nenabled=false\n"
    },
    "mode-pass-warn": {
        "ici.toml": BASE + ENABLE + "mode='pass_warn'\nforbidden_needed=['libc.so.6']\n"
    },
    "mode-pass-warn-fail": {
        "ici.toml": BASE + ENABLE + "mode='pass_warn_fail'\nforbidden_needed=['libc.so.6']\n"
    },
    "expected-class-machine-match": {
        "ici.toml": BASE
        + ENABLE
        + "expected_class='ELF64'\nexpected_machine='Advanced Micro Devices X86-64'\n"
    },
    "expected-class-mismatch": {"ici.toml": BASE + ENABLE + "expected_class='ELF32'\n"},
    "expected-machine-mismatch": {"ici.toml": BASE + ENABLE + "expected_machine='AArch64'\n"},
    "abi-reference": ABI,
    "max-glibcxx": ABI | {"ici.toml": BASE + ENABLE + "max_glibcxx='3.4'\n"},
    "max-glibcxx-ok": ABI | {"ici.toml": BASE + ENABLE + "max_glibcxx='3.4.99'\n"},
    "max-cxxabi": ABI | {"ici.toml": BASE + ENABLE + "max_cxxabi='1.2'\n"},
    "max-cxxabi-ok": ABI | {"ici.toml": BASE + ENABLE + "max_cxxabi='1.3.99'\n"},
    "allowed-needed": ABI | {"ici.toml": BASE + ENABLE + "allowed_needed=['libc.so.6']\n"},
    "require-static": {"ici.toml": BASE + ENABLE + "require_static=true\n"},
    "require-static-off": {"ici.toml": BASE + ENABLE + "require_static=false\n"},
    "rpath-default": {"Makefile": RPATH_MAKE},
    "rpath-absolute-allowed": {
        "Makefile": RPATH_MAKE,
        "ici.toml": BASE + ENABLE + "forbid_absolute_rpath=false\n",
    },
    "rpath-all-allowed": {
        "Makefile": RPATH_MAKE,
        "ici.toml": BASE + ENABLE + "forbid_absolute_rpath=false\nforbid_build_paths=false\n",
    },
    "max-artifacts-reference": {"Makefile": TWO_MAKE},
    "max-artifacts": {"Makefile": TWO_MAKE, "ici.toml": BASE + ENABLE + "max_artifacts=1\n"},
}
# Executables hidden from PATH per case: PATH becomes a symlink farm of every other entry.
HIDDEN = {"missing-readelf": ["readelf"], "truncated-readelf": ["readelf"]}
# Hidden executables replaced by a script in the farm: a readelf that runs and exits 0 but
# returns only the first lines of its evidence.
REPLACED = {
    "truncated-readelf": {
        "readelf": '#!/bin/sh\n/usr/bin/readelf "$@" | head -n 4\nexit 0\n',
    }
}


def search_path(hidden: list[str], replaced: dict | None = None) -> str:
    directories = [*TOOLS, "/usr/bin", "/bin"]
    if not hidden:
        return os.pathsep.join(directories)
    farm = WORK / "path"
    farm.mkdir()
    for name, text in (replaced or {}).items():
        (farm / name).write_text(text, encoding="utf-8")
        (farm / name).chmod(0o755)
    for directory in directories:
        for entry in sorted(os.listdir(directory)):
            if entry not in hidden and not (farm / entry).exists():
                (farm / entry).symlink_to(Path(directory) / entry)
    return str(farm)


def run(changes: dict, hidden: list[str], replaced: dict | None = None) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / "package-make", project)
        (project / "ici.toml").write_text(BASE + ENABLE, encoding="utf-8")
        for relative, text in changes.items():
            (project / relative).write_text(text, encoding="utf-8")
        subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--profile", "deep", "--report"],
            cwd=project,
            env={
                "PATH": search_path(hidden, replaced),
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engines = {}
    for engine in report["results"]:
        if engine.get("engine_name") in ("build", "binary_compat"):
            engines[engine["engine_name"]] = {
                "status": engine["status"],
                "summary": engine["summary"],
                "targets": [
                    {key: target.get(key) for key in FIELDS} for target in engine["targets"]
                ],
                "rules": sorted(finding["rule_id"] for finding in engine.get("findings", [])),
                # Every frozen ABI finding (analysis/binary_abi.py binary_finding); loader-path
                # messages name the temporary project, normalized to "<project>".
                "findings": sorted(
                    (
                        {
                            key: json.loads(
                                json.dumps(finding.get(key)).replace(str(project), "<project>")
                            )
                            for key in FINDING_FIELDS
                        }
                        for finding in engine.get("findings", [])
                        if finding["rule_id"].startswith("ici.binary.")
                    ),
                    key=lambda finding: (finding["rule_id"], finding["fingerprint"]),
                ),
            }
    return engines


cases = {
    name: {
        "changes": changes,
        "frozen": run(changes, HIDDEN.get(name, []), REPLACED.get(name)),
    }
    | ({"hidden": HIDDEN[name]} if name in HIDDEN else {})
    | ({"replaced": REPLACED[name]} if name in REPLACED else {})
    for name, changes in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/binary-compat-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "base": BASE + ENABLE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} binary compatibility cases")
# Each key case must differ from its reference exactly where the key has an effect.
EFFECT = {
    "disabled": "pass",
    "mode-pass-warn": "mode-pass-warn-fail",
    "expected-class-mismatch": "expected-class-machine-match",
    "expected-machine-mismatch": "expected-class-machine-match",
    "max-glibcxx": "max-glibcxx-ok",
    "max-cxxabi": "max-cxxabi-ok",
    "allowed-needed": "abi-reference",
    "require-static": "require-static-off",
    "rpath-absolute-allowed": "rpath-default",
    "rpath-all-allowed": "rpath-absolute-allowed",
    "max-artifacts": "max-artifacts-reference",
}
same = [name for name, other in EFFECT.items() if cases[name]["frozen"] == cases[other]["frozen"]]
assert not same, same
