"""Verify frozen duplicate ownership/empty-scope behavior; never a product backend."""

import argparse
import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path


def observe(engine_type, sources, options):
    with tempfile.TemporaryDirectory(prefix="frozen-owned-scope-") as temporary:
        root = Path(temporary)
        selected = []
        for name, text in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode())
            selected.append(path)
        engine = engine_type(root, {"engines": {"dup": options}})
        engine.project_python_sources = lambda: selected
        engine.project_cpp_sources = list
        engine.project_cpp_headers = list
        result = engine.run()
        return {
            "status": result.status.value,
            "evidence": result.evidence.value,
            "summary": result.summary,
            "extra": {
                key: result.extra[key]
                for key in (
                    "analysis_provenance",
                    "source_files_analyzed",
                    "source_bytes_analyzed",
                    "source_files_excluded",
                    "source_exclusion_counts",
                )
            },
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("frozen", type=Path)
    parser.add_argument("--dependencies", type=Path)
    args = parser.parse_args()
    evidence = json.loads(Path(__file__).with_name("source-scope.json").read_text())
    frozen = args.frozen.resolve()
    for name, expected in evidence["oracle_files"].items():
        assert hashlib.sha256((frozen / name).read_bytes()).hexdigest() == expected, name
    sys.path.insert(0, str(frozen / "src"))
    if args.dependencies:
        sys.path.append(str(args.dependencies.resolve()))
    implementation = importlib.import_module("ici.engines.dup")
    assert Path(implementation.__file__).is_relative_to(frozen)
    for case in evidence["cases"]:
        actual = observe(implementation.DuplicateEngine, case["sources"], case["options"])
        assert actual == case["original"], case["id"]
    print(f"{len(evidence['cases'])} frozen duplicate source scope fixtures match")


if __name__ == "__main__":
    main()
