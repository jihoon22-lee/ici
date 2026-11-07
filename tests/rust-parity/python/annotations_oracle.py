"""Verify fixed legacy annotation warnings, including its opt-in behavior."""

import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path


def main():
    fixture = json.loads(Path(__file__).with_name("annotations.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    assert (
        hashlib.sha256((root / fixture["oracle_file"]).read_bytes()).hexdigest()
        == fixture["oracle_file_sha256"]
    )
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.engines.type_check")
    assert Path(module.__file__).is_relative_to(root)
    for case in fixture["cases"]:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            path = base / "main.py"
            path.write_text(case["source"], encoding="utf-8")

            class Context:
                project_root = base
                warn = case["warn_on_missing_annotation"]

                def get_config(self, _key):
                    return {"warn_on_missing_annotation": self.warn}

            targets = []
            module.TypeCheckEngine._check_python_annotations(Context(), targets, [path])
            actual = [
                {
                    "line": target.start_line,
                    "name": target.target_name,
                    "status": target.status.value,
                    "message": target.message,
                }
                for target in targets
            ]
            assert actual == case["original_targets"], case["id"]
    print(f"{len(fixture['cases'])} frozen annotation fixtures match")


if __name__ == "__main__":
    main()
