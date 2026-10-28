"""Verify frozen graph evidence; native corrections remain separate fixture fields."""

import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path


def main():
    fixture = json.loads(Path(__file__).with_name("cycle.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    source = root / fixture["oracle_file"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixture["oracle_file_sha256"]
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.analysis.cycles")
    assert Path(module.__file__).is_relative_to(root)
    for case in fixture["cases"]:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, text in case["sources"].items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            graph, modules = module._build_python_graph(
                base,
                source_dirs=[base / name for name in case["source_roots"]],
                all_sources=[base / name for name in sorted(case["sources"])],
            )
            edges = sorted(
                [owner, target] for owner, targets in graph.items() for target in targets
            )
            cycles = sorted(sorted(members) for members in module._find_cycles_tarjan(graph))
            assert edges == case["original_edges"], case["id"]
            assert cycles == case["original_cycles"], case["id"]
            assert {key: str(path.relative_to(base)) for key, path in modules.items()} == case[
                "original_modules"
            ], case["id"]
    print(f"{len(fixture['cases'])} frozen Python import-graph fixtures match")


if __name__ == "__main__":
    main()
