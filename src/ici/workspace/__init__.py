"""Building the workspace model out of composed configuration.

The domain types in :mod:`ici.domain.workspace` are deliberately pure — they
describe a resolved workspace and refuse inconsistent ones. This package is
the service that produces that description: it maps a composed
:class:`~ici.config.composition.EffectiveConfig` onto the domain model,
generating the analysis units and validating the references that only make
sense once every layer has spoken.

Nothing in here reads the filesystem for the model itself. Source inventory —
which files the globs actually mean — is a separate adapter
(:mod:`ici.workspace.inventory`), because a run must be able to say what the
workspace *is* before it touches the tree.
"""

from ici.workspace.model import build, project_type

__all__ = ["build", "project_type"]
