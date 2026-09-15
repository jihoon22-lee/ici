"""Mapping a composed configuration onto the domain workspace model.

This is the first place where the whole workspace exists as one object, so it
is also the first place several acceptance criteria from #207 can be enforced
rather than assumed:

- **one build unit, however many components name it.** ``build = "native"``
  on two components yields one :class:`~ici.domain.workspace.BuildUnit`,
  shared. The unit exists once because the declaration exists once — there is
  no per-component copy to drift out of sync.
- **every declared thing must resolve.** A ``needs`` edge naming nothing, a
  build directory two units both claim, a component root that escapes the
  workspace — each is diagnosed with the key that caused it, because a model
  that tolerated them would make downstream DAG construction guess.
- **analysis units are generated, not declared.** One unit per component x
  language, qualified by the variant its build runs under or the runtime its
  interpreter declares — so the same file compiled two ways stays two facts.

Errors surface as :class:`~ici.config.errors.ConfigProblem` through
``NextConfigError`` — a bad model is a configuration error (exit 2), not a
verification failure.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ici.config.composition import EffectiveBuild, EffectiveComponent, EffectiveConfig
from ici.config.errors import ConfigProblem, NextConfigError
from ici.config.origin import Origin
from ici.config.paths import _normalise
from ici.domain.workspace import AnalysisUnit, BuildUnit, Component, Workspace

__all__ = ["build", "project_type"]

#: A component that declares no ``sources`` claims its languages' conventional
#: files under its own root. ``ici init`` writes no ``sources`` key and SPEC-01
#: section 4's ``tool-a`` has none either — an empty declaration must mean the
#: conventional scope, not an empty one. The same suffixes ``scaffold`` uses to
#: *detect* a language are the ones used to *scope* it.
_DEFAULT_SOURCE_GLOBS = {
    "python": ("**/*.py",),
    "cpp": ("**/*.cpp", "**/*.cc", "**/*.cxx", "**/*.hpp", "**/*.h"),
}


#: ``prepare = "explicit"`` — the build is configured by the user before ici
#: runs. Anything else was already refused at composition; a build without the
#: word has no approved way to make its directory exist.
def build(config: EffectiveConfig) -> Workspace:
    """Build the domain workspace from a composed configuration.

    The function is total over declared content: malformed declarations were
    already refused by the schema and composition, so what reaches here can
    only conflict with *other* declarations — the dangling reference, the
    cycle, the shared directory. Those are the diagnostics this layer owns.
    """

    problems: list[ConfigProblem] = []
    builds = tuple(_build_unit(item, problems) for item in config.builds)
    components = tuple(_component(item) for item in config.components)

    name = config.workspace_name.value if config.workspace_name is not None else "workspace"
    try:
        workspace = Workspace(
            id=_workspace_id(name),
            name=name,
            components=components,
            builds=builds,
            analysis_units=_units(components, config),
            required_component_ids=tuple(item.id for item in components),
            policy_digest=config.policy_digest,
        )
    except ValueError as error:
        # Domain validation found what composition could not: a reference that
        # names nothing, or a cycle. A broken model is a configuration error —
        # the run never started, so this is not a verification failure.
        problems.append(ConfigProblem(str(error), Origin(file="<model>")))
        raise NextConfigError(tuple(problems)) from error
    if problems:
        raise NextConfigError(tuple(problems))
    return workspace


def project_type(workspace: Workspace) -> str:
    """The legacy ``python``/``cpp``/``hybrid`` label, as a projection.

    #207 item 7: the single ``project_type`` guess the stable path makes from
    one root becomes a derived view over component languages — kept for the
    compatibility reader, never re-derived by new engines from a root backend.
    """

    languages = {language for component in workspace.components for language in component.languages}
    if languages == {"python"}:
        return "python"
    if languages == {"cpp"}:
        return "cpp"
    return "hybrid"


def _workspace_id(name: str) -> str:
    """A stable identifier derived from the configured name.

    The id appears in run results and cache keys, so it is made identifier-safe
    here rather than letting a display name like "My Product" leak into file
    names downstream.
    """

    slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("._-")
    return slug or "workspace"


def _build_unit(item: EffectiveBuild, problems: list[ConfigProblem]) -> BuildUnit:
    if item.system is None:
        problems.append(ConfigProblem("a build must name a system", Origin(file=item.declared_in)))
        system = "explicit"
    else:
        system = item.system.value
    if item.directory is None:
        problems.append(
            ConfigProblem(
                f"build {item.id} must name a directory",
                Origin(file=item.declared_in),
                hint="the output scope of a build is never guessed",
            )
        )
        directory = "."
    else:
        directory = item.directory.value
    variant = item.variant.value
    return BuildUnit(
        id=item.id,
        system=system,
        variant=variant,
        directory=directory,
        definition=None if item.definition is None else item.definition.value,
        # "explicit" is permission, not a command (SPEC-01 section 4). There is
        # deliberately no argv — a build unit that cannot prepare itself is a
        # blocking condition for its dependents, not an invitation to guess.
        prepare_argv=(),
    )


def _component(item: EffectiveComponent) -> Component:
    return Component(
        id=item.id,
        root=item.root.value,
        languages=item.languages.value,
        sources=_sources(item),
        include=item.include,
        exclude=item.exclude,
        vendor=item.vendor,
        test_paths=item.test_paths,
        build_ids=(item.build.value,) if item.build is not None else (),
        needs=item.needs.value if item.needs is not None else (),
        external=item.external.value if item.external is not None else (),
    )


def _sources(item: EffectiveComponent) -> tuple[str, ...]:
    """The component's claimed scope, defaulting by language when undeclared.

    Declared globs arrive workspace-relative already; the default is spelled
    the same way — anchored to the component root, expressed from the
    workspace — so ``root = "."`` and a split-out file produce identical scope.
    """

    if item.sources:
        return item.sources
    root = item.root.value
    patterns = [
        pattern
        for language in item.languages.value
        for pattern in _DEFAULT_SOURCE_GLOBS.get(language, ())
    ]
    if root == ".":
        return tuple(patterns)
    return tuple(str(_normalise(PurePosixPath(root) / pattern)) for pattern in patterns)


def _units(components: tuple[Component, ...], config: EffectiveConfig) -> tuple[AnalysisUnit, ...]:
    """One unit per component x language, qualified by variant or runtime.

    A component with a build produces units under that build's variant; a
    Python component's unit is qualified by the interpreter it declares, so a
    later resolver swapping ``python`` for a pinned one does not silently
    change what was analysed.
    """

    units: list[AnalysisUnit] = []
    variants = {item.id: item.variant for item in config.builds}
    for component in components:
        effective = config.component(component.id)
        build_id = component.build_ids[0] if component.build_ids else None
        for language in component.languages:
            runtime = None
            variant = None
            if language == "python" and effective is not None:
                runtime = (
                    effective.python_executable.value
                    if effective.python_executable is not None
                    else None
                )
            if build_id is not None and build_id in variants:
                variant = variants[build_id].value
            units.append(
                AnalysisUnit(
                    id=f"{component.id}.{language}",
                    component_id=component.id,
                    language=language,
                    variant=variant,
                    runtime=runtime,
                    build_id=build_id,
                )
            )
    return tuple(units)
