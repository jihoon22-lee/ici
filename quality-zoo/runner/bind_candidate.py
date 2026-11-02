"""Bind a new ici artifact digest to an existing known-answer set without editing answers.

Release binding (as for v0.12.0/v0.13.0) adds one expectation file per scenario and one
selector entry ``<artifact sha256> -> expectations/<name>.json``. This helper derives each new
expectation file from the expectation already bound to ``--reference-sha256`` and changes only
``producer_version`` (the artifact's own ``--version``); every status, evidence, finding,
absence and capability predicate is copied verbatim. Any difference the new artifact shows is
then reported by ``runner.run`` as a contract failure — it is never absorbed here.

It refuses to rebind a digest that already has an expectation, to replace an existing file
with different content, and to bind a scenario that has no reference expectation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from runner.common import (
    SHA256_RE,
    ContractError,
    contained_path,
    load_json_object,
    require_string,
    sha256_file,
)
from runner.run import MAX_ICI_BIN_BYTES, _load_registry, _run_version

NAME_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def derive(reference: dict[str, Any], producer_version: str) -> dict[str, Any]:
    """The reference known answer with only the producer replaced."""

    derived = json.loads(json.dumps(reference))
    derived["producer_version"] = producer_version
    return derived


def bind(
    manifest_path: Path,
    *,
    reference_sha256: str,
    sha256: str,
    producer_version: str,
    name: str,
) -> list[Path]:
    """Write the derived expectation and selector entry of every manifest scenario."""

    for label, digest in (("reference", reference_sha256), ("candidate", sha256)):
        if not SHA256_RE.fullmatch(digest):
            raise ContractError("invalid-digest", f"{label} SHA-256 is invalid")
    if sha256 == reference_sha256:
        raise ContractError("invalid-digest", "candidate and reference digests are equal")
    if not NAME_RE.fullmatch(name):
        raise ContractError("invalid-name", f"expectation name {name!r} is invalid")
    require_string(producer_version, "producer_version")
    _, registry = _load_registry(manifest_path)
    planned: list[tuple[Path, dict[str, Any], Path, dict[str, Any]]] = []
    for scenario_id, scenario_root in sorted(registry.items()):
        selector_path = scenario_root / "scenario.json"
        selector = load_json_object(selector_path, label=f"scenario {scenario_id}")
        expectations = selector.get("expectations")
        if selector.get("schema") != 2 or not isinstance(expectations, dict):
            raise ContractError("scenario-schema", f"{scenario_id} has no digest selector")
        if sha256 in expectations:
            raise ContractError("already-bound", f"{scenario_id} already binds {sha256}")
        if reference_sha256 not in expectations:
            raise ContractError(
                "reference-missing", f"{scenario_id} has no expectation for the reference"
            )
        reference = load_json_object(
            contained_path(scenario_root, expectations[reference_sha256]),
            label=f"{scenario_id} reference expectation",
        )
        relative = f"expectations/{name}.json"
        target = contained_path(scenario_root, relative, must_exist=False)
        derived = derive(reference, producer_version)
        if target.exists() and load_json_object(target, label=str(target)) != derived:
            raise ContractError("expectation-exists", f"{target} differs from the derivation")
        planned.append((selector_path, {**selector}, target, derived))
    written: list[Path] = []
    for selector_path, selector, target, derived in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(derived, indent=1) + "\n", encoding="utf-8")
        selector["expectations"] = {
            **selector["expectations"],
            sha256: target.relative_to(selector_path.parent).as_posix(),
        }
        selector_path.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")
        written.extend((target, selector_path))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("candidate-manifest.json"))
    parser.add_argument("--reference-sha256", required=True)
    parser.add_argument("--ici-bin", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        ici_bin = args.ici_bin
        if ici_bin.is_symlink() or not ici_bin.is_file() or not os.access(ici_bin, os.X_OK):
            raise ContractError("unsafe-ici-bin", "--ici-bin must be an executable regular file")
        digest, _ = sha256_file(ici_bin.resolve(strict=True), max_bytes=MAX_ICI_BIN_BYTES)
        version = _run_version(ici_bin, args.timeout_seconds)
        written = bind(
            args.manifest,
            reference_sha256=args.reference_sha256,
            sha256=digest,
            producer_version=version,
            name=args.name,
        )
    except (ContractError, OSError) as error:
        parser.exit(2, f"quality-zoo bind ERROR: {error}\n")
    print(json.dumps({"sha256": digest, "version": version, "written": len(written)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
