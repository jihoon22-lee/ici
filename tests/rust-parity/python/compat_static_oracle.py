"""Verify fixed CPython static syntax and standard-library compatibility evidence."""

import hashlib
import importlib
import json
import sys
from pathlib import Path


def main():
    fixture = json.loads(Path(__file__).with_name("compat-static.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    assert (
        hashlib.sha256((root / fixture["oracle_file"]).read_bytes()).hexdigest()
        == fixture["oracle_file_sha256"]
    )
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.analysis._python_compatibility")
    assert Path(module.__file__).is_relative_to(root)
    for case in fixture["cases"]:
        floor = tuple(map(int, case["target"].split("."))) if case["target"] else None
        result = module.analyze_static_compatibility("main.py", case["source"], floor)
        actual = [
            {
                "start_line": target.start_line,
                "end_line": target.end_line,
                "start_column": target.start_column,
                "end_column": target.end_column,
                "target_name": target.target_name,
                "status": target.status.value,
                "message": target.message,
            }
            for target in result.targets
        ]
        assert actual == case["original_targets"], case["id"]
        assert result.ast_nodes == case["ast_nodes"], case["id"]
    print(f"{len(fixture['cases'])} fixed static compatibility fixtures match")


if __name__ == "__main__":
    main()
