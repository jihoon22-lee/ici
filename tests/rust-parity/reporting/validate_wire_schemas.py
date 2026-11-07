"""Development-only cross-check of crates/ici-cli/tests/wire_schemas.rs with `jsonschema`.

The Rust test checks native (and frozen calibration) documents against the frozen schemas with
a keyword-subset checker and writes every sample under
`<target>/tmp/wire-schemas/<schema>/{valid,invalid}/`. This script validates the same samples
with the reference Draft 2020-12 validator and fails unless it agrees on every one.

Run: uv run --offline --no-project --with jsonschema python validate_wire_schemas.py <target/tmp>
"""

import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    samples = Path(sys.argv[1]) / "wire-schemas"
    disagreements, counted = [], 0
    for directory in sorted(samples.iterdir()):
        path = ROOT / "src/ici/schemas" / f"{directory.name}.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for expected in ("valid", "invalid"):
            for sample in sorted((directory / expected).glob("*.json")):
                errors = list(validator.iter_errors(json.loads(sample.read_text("utf-8"))))
                counted += 1
                if (not errors) != (expected == "valid"):
                    disagreements.append(f"{directory.name}/{expected}/{sample.name}: {errors}")
                print(f"{directory.name} {expected} {sample.stem}: {len(errors)} error(s)")
    print(f"{counted} samples, {len(disagreements)} disagreement(s), jsonschema {version()}")
    if disagreements or not counted:
        raise SystemExit("\n".join(disagreements) or "no samples")


def version() -> str:
    from importlib.metadata import version as installed

    return installed("jsonschema")


if __name__ == "__main__":
    main()
