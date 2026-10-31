"""Verify PEP 440 decisions and metadata import discovery without rewriting evidence."""

import dataclasses
import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path

from packaging.version import Version


def main():
    fixture = json.loads(Path(__file__).with_name("metadata.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    assert (
        hashlib.sha256((root / fixture["oracle_file"]).read_bytes()).hexdigest()
        == fixture["oracle_file_sha256"]
    )
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.analysis._python_compatibility")
    assert Path(module.__file__).is_relative_to(root)
    for case in fixture["cases"]:
        try:
            actual = {
                "floor": module.inferred_target_version(case["specifier"]),
                "versions": {
                    version: module.requires_python_allows(case["specifier"], Version(version))
                    for version in case.get("versions", {})
                },
            }
        except Exception as error:
            assert type(error).__name__ == case.get("error"), case["specifier"]
            continue
        assert json.loads(json.dumps(actual)) == {
            "floor": case["floor"],
            "versions": case["versions"],
        }, case["specifier"]
    for case in fixture["metadata_cases"]:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            scope = base / case["metadata_root"]
            scope.mkdir(parents=True, exist_ok=True)
            for name in case["sources"]:
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("value = 1\n", encoding="utf-8")
            if case["document"] is not None:
                (scope / "pyproject.toml").write_text(case["document"], encoding="utf-8")
            try:
                actual = dataclasses.asdict(
                    module.load_python_metadata(scope, [base / name for name in case["sources"]])
                )
            except Exception as error:
                assert type(error).__name__ == case.get("error"), case["id"]
                continue
            assert json.loads(json.dumps(actual)) == case["original"], case["id"]
    print(
        f"{len(fixture['cases'])} PEP 440 and {len(fixture['metadata_cases'])} metadata cases match"
    )


if __name__ == "__main__":
    main()
