"""The precedence order, and what each layer is allowed to say.

SPEC-01 section 3 lists the order — built-in defaults, root, component, an
explicit local overlay, then CLI — and then places two limits on it that matter
more than the order does:

- a component may adjust a default but **may not lower a root requirement**. If
  a component could, then "this workspace requires lint" would be a statement
  no one could rely on, and the only way to find out would be to read every
  component file.
- a local overlay may touch **paths only**. It exists so a developer can point
  at their own build directory, not so they can turn a check off on their
  machine and have CI disagree with them for reasons neither can see.

Both are refusals rather than silent last-write-wins, and both name the key and
the file, because the whole point of this layer is that a person can find out
why a value is what it is.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["LOCAL_OVERLAY_ALLOWLIST", "Layer"]


class Layer(str, Enum):
    """Where a value came from, in precedence order.

    The order is the declaration order, and :meth:`beats` reads it, so adding a
    layer in the wrong place cannot disagree with a separately written table of
    precedence.
    """

    DEFAULTS = "defaults"
    ROOT = "root"
    COMPONENT = "component"
    LOCAL = "local"
    CLI = "cli"

    @property
    def rank(self) -> int:
        return list(Layer).index(self)

    def beats(self, other: Layer) -> bool:
        """Whether a value from this layer replaces one from ``other``."""

        return self.rank >= other.rank


# What `--local-config` may set. Every entry is a path or an executable: the
# allowlist is not a policy decision per field, it is the single rule that a
# local overlay adjusts *where things are*, never *what is checked*.
LOCAL_OVERLAY_ALLOWLIST = frozenset(
    {
        "builds.*.directory",
        "builds.*.project",
        "components.*.python.executable",
        "output",
        "cache",
        "tools.*.path",
    }
)
