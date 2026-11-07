"""Compare the fixed source metric oracle with committed expectations; never a runtime worker."""

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


def main():
    fixtures = json.loads(Path(__file__).with_name("metrics.json").read_text(encoding="utf-8"))
    source = Path(sys.argv[1]) / fixtures["oracle_file"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixtures["oracle_file_sha256"]
    spec = importlib.util.spec_from_file_location("fixed_ici_metrics", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for case in fixtures["cases"]:
        rows = []
        for node in ast.walk(ast.parse(case["source"])):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cognitive, nesting = module.cognitive_complexity(node)
                rows.append(
                    {
                        "name": node.name,
                        "line": node.lineno,
                        "end_line": node.end_lineno,
                        "complexity": module.cyclomatic_complexity(node),
                        "nesting": module.max_nesting(node),
                        "cognitive": cognitive,
                        "cognitive_nesting": nesting,
                    }
                )
        assert rows == case["functions"], case["id"]
    print(f"{len(fixtures['cases'])} fixed-source metric fixtures match")


if __name__ == "__main__":
    main()
