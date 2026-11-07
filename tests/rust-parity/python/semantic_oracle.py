"""Verify fixed semantic-shape fields and the stable engine's merged groups."""

import dataclasses
import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path


def main():
    directory = Path(__file__).parent
    shapes = json.loads((directory / "semantic.json").read_text(encoding="utf-8"))
    pipeline = json.loads((directory / "semantic-integration.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    for fixture in (shapes, pipeline):
        assert (
            hashlib.sha256((root / fixture["oracle_file"]).read_bytes()).hexdigest()
            == fixture["oracle_file_sha256"]
        )
    sys.path.insert(0, str(root / "src"))
    semantic = importlib.import_module("ici.analysis._python_dup_semantics")
    engine = importlib.import_module("ici.engines.dup")
    assert Path(semantic.__file__).is_relative_to(root)
    for case in shapes["cases"]:
        result = dataclasses.asdict(
            semantic.analyze_python_sources(
                case["sources"], limits=semantic.SemanticLimits(**case["limits"])
            )
        )
        result.pop("fingerprint_algorithm")
        result.pop("shape_version")
        assert json.loads(json.dumps(result)) == case["original"], case["id"]
    for case in pipeline["cases"]:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, text in case["sources"].items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            result = engine.DuplicateEngine(
                base,
                {
                    "project": {"type": "python", "source_dirs": ["."]},
                    "engines": {"dup": case["options"]},
                },
            ).run()
            assert result.status.value == case["status"], case["id"]
            for key, value in case["extra"].items():
                assert result.extra[key] == value, (case["id"], key)
    print(
        f"{len(shapes['cases'])} exact shapes and {len(pipeline['cases'])} merged pipelines match"
    )


if __name__ == "__main__":
    main()
