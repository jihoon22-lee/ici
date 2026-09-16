"""What a configuration file says, before anything is composed with it.

A *document* is one file, read and type-checked, with every value carrying its
origin. It is deliberately not an effective configuration: composing root,
component, local overlay and CLI is WP05 PR B, and keeping the two apart is
what lets PR B state precedence over values that still know where they came
from.

The optionality here is real rather than defensive. ``profile`` being ``None``
means the file did not say, which is a different fact from the default value,
and only the unmerged document can tell a reader which one it was looking at.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.config.origin import Origin, Sourced
from ici.config.paths import DeclaredPath, Executable, SourceGlob

__all__ = [
    "BuildDeclaration",
    "CheckSetting",
    "ComponentBody",
    "ComponentDocument",
    "ComponentEntry",
    "ComponentReference",
    "CppSettings",
    "Exemption",
    "PythonSettings",
    "RootDocument",
    "ToolSetting",
    "WorkspaceSettings",
]


@dataclass(frozen=True)
class WorkspaceSettings:
    """``[workspace]`` — the identity of the whole thing."""

    name: Sourced[str] | None
    profile: Sourced[str] | None
    origin: Origin


@dataclass(frozen=True)
class Exemption:
    """A root's permission for one component to stop meeting a requirement.

    The reason is mandatory. SPEC-01 section 3 lets a root relax a requirement
    for a named component, and the whole value of that over letting components
    relax it themselves is that the decision is written down somewhere its owner
    reads — an exemption with no reason would be the silent relaxation with an
    extra step.
    """

    component_id: str
    reason: Sourced[str]
    origin: Origin


@dataclass(frozen=True)
class CheckSetting:
    """``[checks.<id>]`` — whether a check runs, and whether it may fail.

    A root owns ``required``. A component may carry the same table to adjust a
    default for itself, but composition refuses to let it lower what the root
    required (SPEC-01 section 3) unless the root granted it an exemption.
    """

    id: str
    enabled: Sourced[bool] | None
    required: Sourced[bool] | None
    origin: Origin
    exemptions: tuple[Exemption, ...] = ()

    def exemption_for(self, component_id: str) -> Exemption | None:
        for exemption in self.exemptions:
            if exemption.component_id == component_id:
                return exemption
        return None


@dataclass(frozen=True)
class ToolSetting:
    """``[tools.<id>]`` — where a provider's binary and config come from."""

    id: str
    source: Sourced[str] | None
    config: DeclaredPath | None
    path: DeclaredPath | None
    origin: Origin


@dataclass(frozen=True)
class BuildDeclaration:
    """``[builds.<id>]`` — one build configuration, shared by components.

    ``prepare`` is a permission, not a command. SPEC-01 section 4 is explicit
    that ``prepare = "explicit"`` does not authorise ici to invent a configure
    invocation, so this type carries the word and nothing more.
    """

    id: str
    system: Sourced[str] | None
    project: DeclaredPath | None
    directory: DeclaredPath | None
    variant: Sourced[str] | None
    prepare: Sourced[str] | None
    #: Output globs the build claims to produce, anchored at ``directory``
    #: (#220): the declaration is the contract the artifact check verifies —
    #: a glob that matches nothing is a broken claim, not an absence.
    artifacts: tuple[SourceGlob, ...]
    origin: Origin


@dataclass(frozen=True)
class PythonSettings:
    """``[python]`` or ``[components.python]``."""

    executable: Executable | None
    type_provider: Sourced[str] | None
    test_tools: Sourced[str] | None
    test_paths: tuple[SourceGlob, ...]
    origin: Origin


@dataclass(frozen=True)
class CppSettings:
    """``[cpp]`` or ``[components.cpp]``."""

    qt: Sourced[bool] | None
    origin: Origin


@dataclass(frozen=True)
class ComponentBody:
    """A component as declared, inline in a root file or alone in a child file.

    The same type for both on purpose: SPEC-01 section 2 requires that moving a
    component into its own file changes where it is stored and nothing else. If
    the two were different types, "the same effective config" would be a claim
    about two code paths rather than one.
    """

    id: Sourced[str] | None
    root: DeclaredPath | None
    languages: Sourced[tuple[str, ...]] | None
    build: Sourced[str] | None
    sources: tuple[SourceGlob, ...]
    include: tuple[SourceGlob, ...]
    exclude: tuple[SourceGlob, ...]
    python: PythonSettings | None
    cpp: CppSettings | None
    origin: Origin
    checks: tuple[CheckSetting, ...] = ()
    #: Components or builds this component consumes artifacts from (SPEC-01
    #: section 1: component dependencies are not task dependencies).
    needs: Sourced[tuple[str, ...]] | None = None
    #: Globs marking checked-in sources as vendor/third-party inputs.
    vendor: tuple[SourceGlob, ...] = ()
    #: Declared reads outside the component root, kept separate from write
    #: scope as SPEC-01 section 3 requires of external inputs.
    external: Sourced[tuple[str, ...]] | None = None


@dataclass(frozen=True)
class ComponentReference:
    """A root entry that points at a child file instead of defining anything.

    SPEC-01 section 2 allows ``id`` and ``config`` here and nothing else, so
    that a reader never has to ask which of two declarations of the same
    component won.
    """

    id: Sourced[str]
    config: DeclaredPath
    origin: Origin


# A root's ``[[components]]`` entry is one or the other, never both.
ComponentEntry = ComponentBody | ComponentReference


@dataclass(frozen=True)
class RootDocument:
    """One ``ici.toml`` containing ``[workspace]``."""

    path: str
    schema_version: Sourced[int]
    workspace: WorkspaceSettings
    checks: tuple[CheckSetting, ...]
    tools: tuple[ToolSetting, ...]
    builds: tuple[BuildDeclaration, ...]
    components: tuple[ComponentEntry, ...]

    @property
    def references(self) -> tuple[ComponentReference, ...]:
        """The entries that point at another file, for PR B to follow."""

        return tuple(entry for entry in self.components if isinstance(entry, ComponentReference))

    @property
    def inline(self) -> tuple[ComponentBody, ...]:
        """The entries defined here."""

        return tuple(entry for entry in self.components if isinstance(entry, ComponentBody))


@dataclass(frozen=True)
class ComponentDocument:
    """One ``ici.toml`` containing ``[component]`` and no ``[workspace]``.

    A child file does not name itself. SPEC-01 section 2 says a component file
    found on its own is not promoted to a workspace, and the root's registration
    is what supplies the id — so a body read from a child file usually has
    ``id = None`` and PR B fills it in from the entry that referenced it.
    """

    path: str
    schema_version: Sourced[int]
    component: ComponentBody
