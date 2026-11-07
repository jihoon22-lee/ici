"""Capture frozen engine descriptors and their profile/dependency validation (oracle only).

Usage: PYTHONPATH=<oracle>/src python capture_descriptors.py <out.json>
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from ici.core.pipeline import (
    ENGINE_DESCRIPTORS,
    AnalysisProfile,
    PipelineDefinitionError,
    apply_analysis_profile,
    descriptors_for_profile,
    validate_engine_descriptors,
)
from ici.engines.registry import resolve_engine_class


def refused(descriptors) -> str | None:
    try:
        validate_engine_descriptors(descriptors)
    except PipelineDefinitionError as error:
        return str(error)
    return None


def main() -> None:
    by_name = {item.name: item for item in ENGINE_DESCRIPTORS}
    document = {
        "source": "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "descriptors": [
            {
                "name": item.name,
                "factory_name": item.factory_name,
                "factory_resolves": resolve_engine_class(item.factory_name).__name__,
                "dependencies": list(item.dependencies),
                "produces": list(item.produces),
                "consumes": list(item.consumes),
                "profiles": sorted(profile.value for profile in item.profiles),
                "execution": item.execution.value,
                "build_variant": item.build_variant.value if item.build_variant else None,
            }
            for item in ENGINE_DESCRIPTORS
        ],
        "profiles": {
            profile.value: [
                item.name
                for item in descriptors_for_profile(ENGINE_DESCRIPTORS, profile, lambda _: True)
            ]
            for profile in AnalysisProfile
        },
        "disabled_by_default_profile": sorted(
            name
            for name, engine in apply_analysis_profile({})[0]["engines"].items()
            if engine.get("enabled") is False
        ),
        "refusals": {
            "dependency_outside_profile": refused(
                [
                    *ENGINE_DESCRIPTORS[:-1],
                    replace(by_name["integration"], profiles=frozenset(AnalysisProfile)),
                ]
            ),
            "duplicate_artifact": refused(
                [*ENGINE_DESCRIPTORS, replace(by_name["line"], name="line2")]
            ),
            "unknown_dependency": refused(
                [
                    *ENGINE_DESCRIPTORS,
                    replace(by_name["line"], name="x", dependencies=("nope",), produces=()),
                ]
            ),
        },
    }
    Path(sys.argv[1]).write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
