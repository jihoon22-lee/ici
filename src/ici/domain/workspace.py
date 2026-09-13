"""Workspace, component, build unit and analysis unit.

Today a single ``ici.core.context.ProjectModel`` plays all four of these roles
at once (WP00 recorded that in inventory/execution-flow.md). Splitting them is
what R07 asks for, and each split answers a question the merged model cannot:

- a **Workspace** owns the policy, so a component cannot weaken a root gate;
- a **Component** is an analysis scope rather than a directory, so mixed C++
  and Python in one folder, and shared sources, stop being special cases;
- a **BuildUnit** is referenced by components rather than owned by one, so a
  qmake SUBDIRS build can feed several of them from one configure;
- an **AnalysisUnit** is keyed by component x language x variant x scope rather
  than by file, so the same header compiled two ways stays two facts.

None of these models reads the filesystem. They describe a workspace that some
adapter has already resolved, which is why they can be built in a unit test
with no project on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ici.domain._validation import (
    require_digest,
    require_identifier,
    require_relative_path,
    require_text,
    require_tuple,
    require_unique_identifiers,
)

__all__ = [
    "AnalysisUnit",
    "BuildUnit",
    "Component",
    "SourceSnapshot",
    "Workspace",
]


def _relative_paths(values: object, description: str) -> tuple[str, ...]:
    """Validate a collection of contained, project-relative paths."""

    return tuple(
        require_relative_path(item, description) for item in require_tuple(values, str, description)
    )


def _identifiers(values: object, description: str) -> tuple[str, ...]:
    """Validate a collection of unique identifiers, order preserved."""

    return require_unique_identifiers(
        (require_identifier(item, description) for item in require_tuple(values, str, description)),
        description,
    )


@dataclass(frozen=True)
class SourceSnapshot:
    """The state of the inputs an analysis actually read.

    ``commit`` is optional and never sufficient on its own: SPEC-02 section 6 is
    explicit that a commit id does not describe dirty or untracked files,
    generated inputs or external headers. ``digest`` covers the content that was
    read, and ``generated`` names inputs produced by a prepare step so that a
    reader can tell them from checked-in sources.
    """

    digest: str
    files: tuple[str, ...] = ()
    generated: tuple[str, ...] = ()
    external_inputs: tuple[str, ...] = ()
    commit: str | None = None
    dirty: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "digest", require_digest(self.digest, "source snapshot digest"))
        object.__setattr__(self, "files", _relative_paths(self.files, "snapshot file"))
        object.__setattr__(self, "generated", _relative_paths(self.generated, "generated input"))
        object.__setattr__(
            self,
            "external_inputs",
            tuple(
                require_text(item, "external input")
                for item in require_tuple(self.external_inputs, str, "external input")
            ),
        )
        if self.commit is not None:
            require_text(self.commit, "snapshot commit")
        if not isinstance(self.dirty, bool):
            raise ValueError("snapshot dirty must be a boolean")


@dataclass(frozen=True)
class BuildUnit:
    """One build configuration, shareable by several components.

    ``variant`` is part of the identity rather than a label: a coverage build and
    a sanitizer build of the same sources are different facts, and SPEC-02
    section 4 requires the variant in the build key so they never share a lock
    or a cache entry.

    ``prepare_argv`` being empty means this unit has no approved way to
    configure itself. That is a blocking condition to report, not a licence to
    guess a command — ``prepare = "explicit"`` in the config grants permission,
    not an implementation (SPEC-01 section 4).
    """

    id: str
    system: str
    variant: str
    directory: str
    definition: str | None = None
    prepare_argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_identifier(self.id, "build unit id"))
        object.__setattr__(self, "system", require_identifier(self.system, "build system"))
        object.__setattr__(self, "variant", require_identifier(self.variant, "build variant"))
        object.__setattr__(
            self, "directory", require_relative_path(self.directory, "build directory")
        )
        if self.definition is not None:
            object.__setattr__(
                self, "definition", require_relative_path(self.definition, "build definition")
            )
        object.__setattr__(
            self,
            "prepare_argv",
            tuple(
                require_text(item, "prepare argv entry")
                for item in require_tuple(self.prepare_argv, str, "prepare argv")
            ),
        )

    @property
    def mutating(self) -> bool:
        """Whether using this unit requires running something that writes."""

        return bool(self.prepare_argv)


@dataclass(frozen=True)
class Component:
    """An independent analysis and test scope.

    Deliberately not one-to-one with a directory (SPEC-01 section 1). ``root``
    anchors the source globs; ``sources`` are already-resolved paths relative to
    the workspace, because resolving globs is an adapter's job and doing it here
    would mean touching the filesystem.
    """

    id: str
    root: str
    languages: tuple[str, ...]
    sources: tuple[str, ...] = ()
    build_ids: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_identifier(self.id, "component id"))
        object.__setattr__(
            self, "root", require_relative_path(self.root, "component root", allow_dot=True)
        )
        languages = _identifiers(self.languages, "component language")
        if not languages:
            raise ValueError(f"component {self.id} must declare at least one language")
        object.__setattr__(self, "languages", languages)
        object.__setattr__(self, "sources", _relative_paths(self.sources, "component source"))
        object.__setattr__(self, "build_ids", _identifiers(self.build_ids, "component build id"))
        object.__setattr__(
            self, "test_paths", _relative_paths(self.test_paths, "component test path")
        )


@dataclass(frozen=True)
class AnalysisUnit:
    """One component, in one language, under one compilation condition.

    The key is the whole tuple, never the source file. SPEC-01 section 1 spells
    out why: the same file compiled under two variants is two analysis inputs,
    and merging them on path would silently discard one set of diagnostics.

    ``build_id`` is optional because a pure Python unit has no build, and
    ``runtime`` records which interpreter or toolchain the unit is analysed
    against — the value WP01 showed must never silently fall back to ici's own.
    """

    id: str
    component_id: str
    language: str
    sources: tuple[str, ...] = ()
    variant: str | None = None
    runtime: str | None = None
    build_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_identifier(self.id, "analysis unit id"))
        object.__setattr__(
            self, "component_id", require_identifier(self.component_id, "analysis unit component")
        )
        object.__setattr__(
            self, "language", require_identifier(self.language, "analysis unit language")
        )
        object.__setattr__(self, "sources", _relative_paths(self.sources, "analysis unit source"))
        if self.variant is not None:
            object.__setattr__(
                self, "variant", require_identifier(self.variant, "analysis unit variant")
            )
        if self.runtime is not None:
            require_text(self.runtime, "analysis unit runtime")
        if self.build_id is not None:
            object.__setattr__(
                self, "build_id", require_identifier(self.build_id, "analysis unit build id")
            )


@dataclass(frozen=True)
class Workspace:
    """The root that owns policy and the registry of components.

    Registration is explicit. SPEC-01 section 2 forbids discovering a component
    by finding a stray config file, so a component that is not listed here is a
    diagnostic rather than an implicit member.

    Cross-references are checked at construction: a component naming a build
    unit that does not exist is a configuration error (SPEC-01 section 3), and
    finding it here means no later stage has to re-check it.
    """

    id: str
    name: str
    components: tuple[Component, ...] = ()
    builds: tuple[BuildUnit, ...] = ()
    required_component_ids: tuple[str, ...] = ()
    policy_digest: str | None = None
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_identifier(self.id, "workspace id"))
        object.__setattr__(self, "name", require_text(self.name, "workspace name"))
        components = require_tuple(self.components, Component, "workspace components")
        builds = require_tuple(self.builds, BuildUnit, "workspace builds")
        require_unique_identifiers([item.id for item in components], "workspace components")
        require_unique_identifiers([item.id for item in builds], "workspace builds")
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "builds", builds)

        build_ids = {item.id for item in builds}
        for component in components:
            missing = [ref for ref in component.build_ids if ref not in build_ids]
            if missing:
                raise ValueError(
                    f"component {component.id} references undefined build units: {sorted(missing)}"
                )

        required = _identifiers(self.required_component_ids, "required component id")
        component_ids = {item.id for item in components}
        unknown = [ref for ref in required if ref not in component_ids]
        if unknown:
            raise ValueError(f"required components are not registered: {sorted(unknown)}")
        object.__setattr__(self, "required_component_ids", required)

        if self.policy_digest is not None:
            object.__setattr__(
                self, "policy_digest", require_digest(self.policy_digest, "policy digest")
            )
        object.__setattr__(
            self,
            "limitations",
            tuple(
                require_text(item, "workspace limitation")
                for item in require_tuple(self.limitations, str, "workspace limitations")
            ),
        )

    def component(self, component_id: str) -> Component:
        """Return the registered component, or raise ``KeyError``."""

        for item in self.components:
            if item.id == component_id:
                return item
        raise KeyError(component_id)

    def build(self, build_id: str) -> BuildUnit:
        """Return the registered build unit, or raise ``KeyError``."""

        for item in self.builds:
            if item.id == build_id:
                return item
        raise KeyError(build_id)
