"""The environment a tool is launched in, as a value rather than a global.

#204 item 1 asks for an immutable snapshot and per-task child environments built
by copying it. The reason is item 5 and WP01's measurement together: the entry
environment carries things the project needs — ``PATH``, ``LD_LIBRARY_PATH``,
Qt's variables, ``SSL_CERT_FILE`` — and ici's own isolation must not reach the
project's children. Mutating ``os.environ`` cannot express that; two values can.

The second acceptance criterion is why this is a snapshot at all: *"다른 shell
초기화 파일 없이 env mapping만으로 동일 선택 결과가 나온다"*. A selection that
depends on a shell rc file cannot be reproduced from a recorded run. Taking the
environment as a mapping makes the inputs to a choice something a result can
carry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = ["PROJECT_VARIABLES", "PYTHON_VARIABLES", "EnvironmentSnapshot"]

# Cleared for ici's own core so an inherited environment cannot shadow its
# imports. WP01 measured that this list must stop here: SSL_CERT_FILE looks like
# it belongs, and removing it loses the CA bundle an internal server needs.
PYTHON_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")

# Kept for anything the project runs. A project child that lost these would be
# analysed in an environment the developer never has.
PROJECT_VARIABLES = (
    "PATH",
    "LD_LIBRARY_PATH",
    "QTDIR",
    "QT_SELECT",
    "QT_PLUGIN_PATH",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """The environment as it was, with children built by copying it."""

    variables: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "variables", MappingProxyType(dict(self.variables)))

    def get(self, name: str) -> str | None:
        return self.variables.get(name)

    def for_core(self) -> EnvironmentSnapshot:
        """ici's own environment: the Python variables cleared, nothing else.

        Only three are removed. Clearing more in the name of isolation is how a
        run loses the certificate bundle or the toolchain the project declared.
        """

        return EnvironmentSnapshot(
            {name: value for name, value in self.variables.items() if name not in PYTHON_VARIABLES}
        )

    def for_project(self, *, overlay: Mapping[str, str] | None = None) -> EnvironmentSnapshot:
        """What a project child sees: the entry environment, plus a task overlay.

        The overlay is applied to a copy. A task that needed ``PYTHONPATH`` set
        for its own run must not leave it set for the next one, and per-task
        copies are what make that true without anyone remembering to undo it.
        """

        return EnvironmentSnapshot({**self.variables, **(overlay or {})})

    def without_stale_virtualenv(self) -> EnvironmentSnapshot:
        """Drop a ``VIRTUAL_ENV`` that no longer matches ``PATH``.

        A shell that activated an environment and then had it deleted, or a
        terminal left open across a rebuild, leaves the variable pointing at
        nothing. Tools read it and report about an interpreter that is not the
        one running — which is a wrong answer that looks like a right one.
        """

        virtual_env = self.variables.get("VIRTUAL_ENV")
        if not virtual_env:
            return self
        path = self.variables.get("PATH", "")
        if any(entry.startswith(virtual_env) for entry in path.split(":") if entry):
            return self
        return EnvironmentSnapshot(
            {name: value for name, value in self.variables.items() if name != "VIRTUAL_ENV"}
        )

    def differences(self, other: EnvironmentSnapshot) -> tuple[str, ...]:
        """Every variable whose value differs, for a diagnostic."""

        names = set(self.variables) | set(other.variables)
        return tuple(
            sorted(name for name in names if self.variables.get(name) != other.variables.get(name))
        )
