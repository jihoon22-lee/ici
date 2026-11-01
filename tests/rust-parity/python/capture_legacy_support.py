"""Development-only frozen support-matrix captures; never called by the Rust product.

Records the frozen v0.13.0 `core/support.py` declaration registry and the serialized output of
`evaluate_support_matrix` for synthetic engine results over small project trees and policies
(profiles, disabled engines, tool policies, per-language evidence, engine filters). The Rust
evaluator replays each case from the recorded languages, frameworks, profile and `[engines]`
policy, so the declaration table and the evaluation rules are compared exactly.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
OUT = ROOT / "tests/rust-parity/python/legacy-support-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.config import load_config  # noqa: E402
from ici.core.models import EngineResult, EngineStatus, EvidenceState  # noqa: E402
from ici.core.support import evaluate_support_matrix, support_declarations  # noqa: E402
from ici.reporters.json_rep import serialize_support_matrix  # noqa: E402

TREES = {
    "python": {"src/a.py": "x = 1\n"},
    "cpp": {"src/a.cpp": "int main() { return 0; }\n"},
    "hybrid": {"src/a.py": "x = 1\n", "src/b.hpp": "int f();\n"},
    "empty": {"src/README.md": "x\n"},
    "pyproject": {"pyproject.toml": "[project]\nname='x'\n"},
    "cmake-qt": {"CMakeLists.txt": "find_package(Qt5 REQUIRED)\n", "src/a.cpp": "int x;\n"},
}
BASE = "[project]\nsource_dirs=['src']\n"
ALL = [d.engine_name for d in support_declarations()]
ENGINES = list(dict.fromkeys(ALL))
# name: (tree, extra configuration, results [(engine, evidence, language_evidence)], filter)
CASES = {
    "python/no-results": ("python", "", [], None),
    "python/all-measured": ("python", "", [(e, "MEASURED", None) for e in ENGINES], None),
    "python/all-estimated-deep": (
        "python",
        "[ici]\nprofile='deep'\n",
        [(e, "ESTIMATED", None) for e in ENGINES],
        None,
    ),
    "python/fast-not-run": (
        "python",
        "[ici]\nprofile='fast'\n",
        [(e, "NOT_RUN", None) for e in ENGINES],
        None,
    ),
    "python/single-line": ("python", "", [("line", "MEASURED", None)], ["line"]),
    "python/single-cognitive-standard": (
        "python",
        "",
        [("cognitive", "MEASURED", None)],
        ["cognitive"],
    ),
    "python/single-cognitive-deep": (
        "python",
        "[ici]\nprofile='deep'\n",
        [("cognitive", "MEASURED", None)],
        ["cognitive"],
    ),
    "python/disabled-and-policies": (
        "python",
        "[engines.lint]\nenabled=false\nruff_required=true\n"
        "[engines.type]\nmypy_required=true\n[engines.test]\ncoverage_required=true\n",
        [("lint", "MEASURED", None), ("type", "MEASURED", None), ("test", "ESTIMATED", None)],
        None,
    ),
    "python/language-evidence": (
        "python",
        "",
        [("dead", "MEASURED", {"python": "ESTIMATED", "cpp": "MEASURED"})],
        ["dead"],
    ),
    "python/invalid-language-evidence": (
        "python",
        "",
        [("dead", "MEASURED", {"python": "BOGUS"}), ("dup", "NOT_APPLICABLE", None)],
        ["dead", "dup"],
    ),
    "cpp/default": ("cpp", "", [(e, "MEASURED", None) for e in ENGINES], None),
    "cpp/tool-policies": (
        "cpp",
        "[ici]\nprofile='deep'\n[engines.lint]\nclang_tidy='required'\nclazy='off'\n"
        "[engines.dead]\ncpp_linker='required'\n[engines.test]\ncoverage_required=true\n",
        [(e, "ESTIMATED", None) for e in ENGINES],
        None,
    ),
    "cpp/dead-off": (
        "cpp",
        "[engines.dead]\ncpp_unused='off'\ncpp_linker='off'\n[engines.lint]\nclazy='required'\n",
        [("dead", "MEASURED", None), ("lint", "MEASURED", None)],
        ["dead", "lint"],
    ),
    "cpp/dead-linker-auto-unused-off": (
        "cpp",
        "[engines.dead]\ncpp_unused='off'\ncpp_linker='auto'\n",
        [("dead", "MEASURED", None)],
        ["dead"],
    ),
    "hybrid/qt-package": (
        "hybrid",
        "cpp_pkg_config=['Qt5Widgets']\n",
        [(e, "MEASURED", None) for e in ENGINES],
        None,
    ),
    "empty/no-languages": ("empty", "", [("line", "MEASURED", None)], None),
    "empty/configured-type": (
        "empty",
        "type='hybrid'\n",
        [("line", "MEASURED", None)],
        ["line", "lint"],
    ),
    "pyproject/fallback": ("pyproject", "", [("line", "MEASURED", None)], ["line"]),
    "cmake-qt/frameworks": ("cmake-qt", "", [("line", "MEASURED", None)], ["line"]),
}


def declaration(item) -> dict:
    return {
        "engine_name": item.engine_name,
        "language": item.language.value,
        "mode": item.mode.value,
        "confidence": item.confidence.value,
        "frameworks": list(item.frameworks),
        "required_tools": list(item.required_tools),
        "optional_tools": list(item.optional_tools),
        "fallback_mode": item.fallback_mode.value if item.fallback_mode else None,
        "limitations": list(item.limitations),
    }


def evaluate(tree: str, config: str, results: list, names: list | None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-support-"))
    try:
        project = work / "project"
        project.mkdir()
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        for name, text in TREES[tree].items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        effective = load_config(project, create_global_default=False)
        engine_results = [
            EngineResult(
                engine_name=engine,
                status=EngineStatus.PASS,
                summary="",
                evidence=EvidenceState(evidence),
                extra={"language_evidence": language} if language else {},
            )
            for engine, evidence, language in results
        ]
        matrix = evaluate_support_matrix(
            project,
            effective,
            engine_results,
            engine_names=set(names) if names is not None else None,
        )
        return {
            "profile": effective.get("ici", {}).get("profile", "standard"),
            "engines": effective["engines"],
            "results": [
                {
                    "engine_name": engine,
                    "evidence": evidence,
                    "extra": {"language_evidence": language} if language else {},
                }
                for engine, evidence, language in results
            ],
            "engine_names": names,
            "frozen": serialize_support_matrix(matrix),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    cases = {name: evaluate(*case) for name, case in CASES.items()}
    OUT.write_text(
        json.dumps(
            {
                "oracle": EXPECTED,
                "python": sys.version.split()[0],
                "declarations": [declaration(item) for item in support_declarations()],
                "cases": cases,
            },
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} support cases, {len(ALL)} declarations", file=sys.stderr)


if __name__ == "__main__":
    main()
