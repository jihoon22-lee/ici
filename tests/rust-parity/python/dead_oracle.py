"""Verify the untouched private-symbol and unreachable-statement oracle evidence."""

import hashlib
import importlib
import json
import sys
from pathlib import Path


def main():
    fixture = json.loads(Path(__file__).with_name("dead.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    source = root / fixture["oracle_file"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixture["oracle_file_sha256"]
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.analysis._python_dead_code")
    source_module = importlib.import_module("ici.analysis._source_inputs")
    assert Path(module.__file__).is_relative_to(root)
    base = Path("/frozen/project")
    for case in fixture["cases"]:
        inputs = tuple(
            source_module.AnalysisSource(
                base / name, name, "python", text, len(text.encode("utf-8"))
            )
            for name, text in case["sources"].items()
        )
        targets, errors = module.analyze_python_dead_code(
            base, [base / name for name in case["source_roots"]], inputs
        )
        actual = [
            {
                "file_path": target.file_path,
                "start_line": target.start_line,
                "end_line": target.end_line,
                "start_column": target.start_column,
                "end_column": target.end_column,
                "target_name": target.target_name,
                "status": target.status.value,
                "message": target.message,
                "snippet": target.snippet,
            }
            for target in targets
        ]
        assert actual == case["original_targets"], case["id"]
        assert errors == case["original_errors"], case["id"]
    print(f"{len(fixture['cases'])} frozen dead-code fixtures match")


if __name__ == "__main__":
    main()
