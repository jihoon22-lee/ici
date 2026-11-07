"""Verify frozen exception target evidence without replacing the native implementation."""

import hashlib
import importlib
import json
import sys
from pathlib import Path


def main():
    fixture = json.loads(Path(__file__).with_name("exception.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    source = root / "src/ici/analysis/_exception_rules.py"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixture["oracle_file_sha256"]
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.analysis._exception_rules")
    assert Path(module.__file__).is_relative_to(root)
    for case in fixture["cases"]:
        result = module.analyze_python_exceptions("main.py", case["source"])
        observed = [
            {
                "name": target.target_name,
                "line": target.start_line,
                "end_line": target.end_line,
                "message": target.message,
                "status": target.status.value,
            }
            for target in result.targets
        ]
        assert observed == case["targets"], case["id"]
    print(f"{len(fixture['cases'])} exception fixtures match the fixed source")


if __name__ == "__main__":
    main()
