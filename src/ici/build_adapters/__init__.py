"""Build adapters — run real project build systems in a shadow directory."""

from ici.build_adapters.base import (
    ArtifactManifest,
    BuildAdapterError,
    BuildOutcome,
    BuildRequest,
    BuildStep,
)
from ici.build_adapters.registry import select_build_adapter

__all__ = [
    "ArtifactManifest",
    "BuildAdapterError",
    "BuildOutcome",
    "BuildRequest",
    "BuildStep",
    "select_build_adapter",
]
