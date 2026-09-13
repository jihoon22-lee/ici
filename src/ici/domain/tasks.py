"""One execution of one provider, fully described before it runs.

A ``TaskSpec`` is the unit ``plan`` prints and the runner executes. It exists so
that every process ici starts is visible in advance, which is what lets the
fast profile refuse mutating work and lets ``plan`` name the build directories a
run would write to (SPEC-02 section 4).

Two properties are load-bearing:

``argv`` is a list and there is no shell. A shell command would turn any project
string into code and would reopen the door to environment-initialisation
parsing that R01 closes.

``share_key`` is derived from everything that could change the output — tool
digest, argv, cwd, environment overlay, rule config. Two tasks may share one
execution only when those all match. Sharing on a looser key is how a coverage
build and a sanitizer build end up reusing each other's artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.domain._validation import (
    require_digest,
    require_env_overlay,
    require_identifier,
    require_non_negative,
    require_positive,
    require_text,
    require_tuple,
    require_unique_identifiers,
)
from ici.domain.enums import TaskKind

__all__ = ["TaskSpec"]


@dataclass(frozen=True)
class TaskSpec:
    """A single provider invocation with its inputs, limits and dependencies."""

    id: str
    kind: TaskKind
    provider: str
    argv: tuple[str, ...]
    cwd: str
    analysis_unit_ids: tuple[str, ...] = ()
    env_overlay: tuple[tuple[str, str], ...] = ()
    input_refs: tuple[str, ...] = ()
    output_specs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    resource_keys: tuple[str, ...] = ()
    timeout_seconds: float | None = None
    output_limit_bytes: int | None = None
    tool_digest: str | None = None
    cacheable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_identifier(self.id, "task id"))
        if not isinstance(self.kind, TaskKind):
            raise ValueError("task kind must be a TaskKind")
        object.__setattr__(self, "provider", require_identifier(self.provider, "task provider"))

        argv = tuple(
            require_text(item, "task argv entry")
            for item in require_tuple(self.argv, str, "task argv")
        )
        if not argv:
            raise ValueError(f"task {self.id} must declare a non-empty argv")
        object.__setattr__(self, "argv", argv)

        object.__setattr__(self, "cwd", require_text(self.cwd, "task cwd"))
        object.__setattr__(
            self,
            "analysis_unit_ids",
            require_unique_identifiers(
                (
                    require_identifier(item, "task analysis unit id")
                    for item in require_tuple(self.analysis_unit_ids, str, "task analysis units")
                ),
                "task analysis units",
            ),
        )
        object.__setattr__(
            self, "env_overlay", require_env_overlay(self.env_overlay, "task env overlay")
        )
        for field_name in ("input_refs", "output_specs", "resource_keys"):
            values = require_tuple(getattr(self, field_name), str, f"task {field_name}")
            object.__setattr__(
                self,
                field_name,
                tuple(require_text(item, f"task {field_name} entry") for item in values),
            )
        object.__setattr__(
            self,
            "depends_on",
            require_unique_identifiers(
                (
                    require_identifier(item, "task dependency")
                    for item in require_tuple(self.depends_on, str, "task dependencies")
                ),
                "task dependencies",
            ),
        )
        if self.id in self.depends_on:
            raise ValueError(f"task {self.id} cannot depend on itself")

        if self.timeout_seconds is not None:
            object.__setattr__(
                self, "timeout_seconds", require_positive(self.timeout_seconds, "task timeout")
            )
        if self.output_limit_bytes is not None:
            require_non_negative(self.output_limit_bytes, "task output limit")
        if self.tool_digest is not None:
            object.__setattr__(
                self, "tool_digest", require_digest(self.tool_digest, "task tool digest")
            )
        if not isinstance(self.cacheable, bool):
            raise ValueError("task cacheable must be a boolean")
        if self.mutating and self.cacheable:
            raise ValueError(
                f"task {self.id} prepares a build directory and must not be marked cacheable"
            )

    @property
    def mutating(self) -> bool:
        """Whether running this task writes outside ici's own run directory.

        Only ``PREPARE`` does. ``plan`` uses this to list what a run would
        change, and the fast profile uses it to block instead of building.
        """

        return self.kind is TaskKind.PREPARE

    @property
    def share_key(self) -> tuple[object, ...]:
        """Everything that must match for two tasks to share one execution.

        Deliberately excludes ``id``, ``depends_on`` and ``analysis_unit_ids``:
        two analysis units can legitimately need the identical command, and that
        is exactly the case worth sharing. Everything that could change the
        output is included, so a differing variant or rule configuration lands
        in a different key rather than silently reusing a result.
        """

        return (
            self.provider,
            self.kind.value,
            self.argv,
            self.cwd,
            self.env_overlay,
            self.input_refs,
            self.tool_digest,
        )
