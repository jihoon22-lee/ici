"""Verify preserved package evidence against an explicitly supplied frozen checkout."""

import base64
import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path


def finding(value):
    return {
        "rule_id": value.rule_id,
        "fingerprint": value.fingerprint,
        "message": value.message,
        "severity": value.severity.value,
        "confidence": value.confidence.value,
        "category": value.category.value,
        "native_rule_id": value.tool_rule_id,
        "path": value.primary_location.path,
        "line": value.primary_location.start_line,
    }


def target(value):
    fields = (
        "file_path",
        "start_line",
        "end_line",
        "start_column",
        "end_column",
        "target_name",
        "message",
        "snippet",
    )
    return {**{name: getattr(value, name) for name in fields}, "status": value.status.value}


def main():
    fixture = json.loads(Path(__file__).with_name("package.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    for name, digest in fixture["oracle_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.analysis._python_packaging")
    assert Path(module.__file__).is_relative_to(root)
    for name in ("packaging", "tomli"):
        dependency = importlib.import_module(name)
        assert dependency.__version__ == fixture[f"{name}_version"]
    for case in fixture["cases"]:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, text in case["sources"].items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            if case["pyproject"] is not None:
                (base / "pyproject.toml").write_text(case["pyproject"], encoding="utf-8")
            for name, encoded in case["artifacts"].items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(encoded, validate=True))
            options = {**case["options"], "wheel_globs": tuple(case["options"]["wheel_globs"])}
            try:
                result = module.analyze_python_packaging(
                    base,
                    [base / name for name in case["sources"]],
                    module.PackagingPolicy(**options),
                )
            except Exception as error:
                assert {"type": type(error).__name__, "message": str(error)} == case.get(
                    "original_error"
                ), case["id"]
                continue
            actual = {
                "targets": [target(value) for value in result.targets],
                "findings": [finding(value) for value in result.findings],
                "metadata": result.metadata,
                "failures": result.failures,
                "warnings": result.warnings,
            }
            assert json.loads(json.dumps(actual)) == case["original"], case["id"]
    print(f"{len(fixture['cases'])} frozen package/wheel contracts match")


if __name__ == "__main__":
    main()
