"""The identity of a tool ici decided to run.

``launch_path`` and ``real_path`` are separate fields because WP01 measured
that they are not interchangeable: a venv's ``bin/python`` and its realpath
report different ``sys.prefix`` (the venv versus ``/usr``). Running the realpath
"because it is the same file" changes the environment the tool sees. So ici
launches what it was given and records the resolved path only as identity —
SPEC-02 section 3, now backed by spikes/wp01-runtime-environment.md.

``role`` exists because the same executable name means different things
depending on why it was chosen. WP01 also measured why that matters: with a
compile database naming ``clang++`` and ``g++`` first on PATH, the current
implementation correctly compiled with ``clang++`` while still probing ``g++``
for its version. Both facts are true and only one of them is the compilation
compiler, so a probe result must never be reported as the tool that ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ici.domain._validation import (
    require_identifier,
    require_text,
    require_tuple,
)

__all__ = ["ResolvedTool", "ToolRole", "ToolSource"]


class ToolRole(str, Enum):
    """Why this tool was selected.

    ``PROJECT`` tools belong to the project under analysis (its interpreter, its
    compiler, its qmake). ``ANALYZER`` tools belong to ici. ``TEST`` tools run
    the project's tests and therefore must come from the project's environment,
    never from ici's own runtime.
    """

    PROJECT = "project"
    ANALYZER = "analyzer"
    TEST = "test"


class ToolSource(str, Enum):
    """Where the decision came from, so ``doctor`` can explain itself.

    ``BUILD_DEFINITION`` ranks above ``PATH`` on purpose: a compile database
    entry names the compiler that actually built the code, and the newest one on
    PATH is not a substitute for it.
    """

    CONFIGURED = "configured"
    BUILD_DEFINITION = "build-definition"
    BUNDLE = "bundle"
    INHERITED_PATH = "inherited-path"


@dataclass(frozen=True)
class ResolvedTool:
    """One tool, with the evidence for why it is this one.

    ``version`` may be empty when the tool was selected but not probed, which is
    a different state from a failed probe. ``capabilities`` lists only what was
    actually confirmed; SPEC-02 section 3 requires tested and untested capability
    to stay distinguishable, so an unprobed feature is simply absent here rather
    than assumed present.
    """

    name: str
    role: ToolRole
    source: ToolSource
    launch_path: str
    real_path: str | None = None
    version: str = ""
    capabilities: tuple[str, ...] = ()
    selection_reason: str = ""
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, "tool name"))
        if not isinstance(self.role, ToolRole):
            raise ValueError("tool role must be a ToolRole")
        if not isinstance(self.source, ToolSource):
            raise ValueError("tool source must be a ToolSource")
        object.__setattr__(self, "launch_path", require_text(self.launch_path, "tool launch path"))
        if self.real_path is not None:
            object.__setattr__(self, "real_path", require_text(self.real_path, "tool real path"))
        if not isinstance(self.version, str):
            raise ValueError("tool version must be a string")
        object.__setattr__(
            self,
            "capabilities",
            tuple(
                require_identifier(item, "tool capability")
                for item in require_tuple(self.capabilities, str, "tool capabilities")
            ),
        )
        if not isinstance(self.selection_reason, str):
            raise ValueError("tool selection reason must be a string")
        object.__setattr__(
            self,
            "limitations",
            tuple(
                require_text(item, "tool limitation")
                for item in require_tuple(self.limitations, str, "tool limitations")
            ),
        )

    @property
    def relocated(self) -> bool:
        """Whether the resolved path differs from the path ici will launch.

        True for a symlinked venv interpreter. A caller that wants identity
        should read ``real_path``; a caller that wants to execute must use
        ``launch_path``, because WP01 measured that substituting the other one
        changes ``sys.prefix``.
        """

        return self.real_path is not None and self.real_path != self.launch_path
