"""The old single-project view, as a projection of the workspace.

WP09 PR C, #207 item 7: the ``python``/``cpp``/``hybrid`` distinction the stable
path derives from one root survives only as a *projection* for compatibility
readers — engines and report writers that still take
``context.project``. New code consumes components and analysis units; nothing
new asks this module for an answer the workspace already gives more precisely.

The projection is deliberately lossy in one direction only. What the workspace
knows lands faithfully — project type over the union of component languages,
source lists from the real inventory. What it does not know is left empty
rather than guessed: ``cpp_include_flags``, ``compilable_cpp_sources`` and the
header split belong to the build adapters (#211 and later), and inventing them
here would hand a legacy reader compile information nobody established.

``backend`` answers the question a legacy reader actually asks — *which build
system produced this scope's artifacts?* — only when the scope has one answer.
A workspace whose components use several build units reports ``None`` with the
reason naming them, because collapsing several builds into one label is the
single-root assumption this model exists to remove.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ici.core.backend import BACKEND_CMAKE, BACKEND_MAKE, BACKEND_QMAKE
from ici.core.context import ProjectModel
from ici.domain.workspace import AnalysisUnit, Workspace
from ici.workspace.inventory import SourceInventory, SourceRole
from ici.workspace.model import project_type

__all__ = ["project_context"]

#: Next-declared build systems that have a legacy backend name. ``explicit``
#: is absent on purpose: a user-prepared build is not a backend ici drives, and
#: reporting one would send a legacy reader looking for a descriptor to parse.
_SYSTEM_TO_BACKEND = {
    "qmake": BACKEND_QMAKE,
    "cmake": BACKEND_CMAKE,
    "make": BACKEND_MAKE,
}

#: The suffixes the old model classified into each source list. The
#: projection keeps that classification: ``python_sources`` still means
#: ``*.py`` and ``cpp_sources`` still means compilable translation units, so
#: a file shared between a component's units lands where a legacy reader
#: already looks for it. ``cpp_headers`` stays empty — the new model does
#: not separate headers from sources, and inventing the split here would
#: only lie to the one field that honest data could fill.
_LANGUAGE_SUFFIXES = {
    "python": (".py",),
    "cpp": (".cpp", ".cc", ".cxx", ".c"),
}


def project_context(
    workspace: Workspace,
    inventory: SourceInventory | None = None,
    *,
    root: Path,
    components: Iterable[str] | None = None,
) -> ProjectModel:
    """Project ``workspace`` into the legacy :class:`ProjectModel` shape.

    ``components`` narrows the projection to a run's selected scope — a
    ``--component`` run must not describe the components it omitted. Without
    an ``inventory`` the source lists stay empty: glob declarations say what a
    component *claims*, and only the inventory knows what actually exists.
    """

    selected = (
        tuple(item.id for item in workspace.components) if components is None else tuple(components)
    )
    scoped = workspace.scoped(selected)
    units = scoped.analysis_units

    return ProjectModel(
        root=Path(root),
        name=workspace.name,
        # A workspace carries no version; the next config has no such key, and
        # a projection must not inherit one from whichever component happened
        # to be first.
        version="",
        project_type=project_type(scoped),
        source_dirs=tuple(
            sorted({item.root for item in workspace.components if item.id in selected})
        ),
        python_sources=_claimed(inventory, units, "python"),
        cpp_sources=_claimed(inventory, units, "cpp"),
        # The next model does not separate headers from sources, and nothing
        # here may: that distinction is compile information (#211+), not a
        # property of the file name.
        cpp_headers=(),
        compilable_cpp_sources=(),
        # Declared external inputs are reads, not build directories.
        external_cpp_dirs=(),
        cpp_include_flags=(),
        **_backend(workspace, selected),
    )


def _claimed(
    inventory: SourceInventory | None, units: tuple[AnalysisUnit, ...], language: str
) -> tuple[str, ...]:
    """The inventoried files of that language the selected units claim.

    A file claimed by a multi-language component names every unit of that
    component, so language attribution here comes from the file's suffix —
    the same classification the old model applied. Vendor files count as
    sources — they are checked-in inputs a legacy reader would have analysed
    too. Generated files do not: a prepare step's output was never a
    ``python_sources`` entry in the old model either.
    """

    if inventory is None:
        return ()
    wanted = {unit.id for unit in units if unit.language == language}
    suffixes = _LANGUAGE_SUFFIXES.get(language, ())
    return tuple(
        sorted(
            item.path
            for item in inventory.files
            if item.role is not SourceRole.GENERATED
            and item.path.endswith(suffixes)
            and wanted.intersection(item.units)
        )
    )


def _backend(workspace: Workspace, selected: tuple[str, ...]) -> dict[str, str | None]:
    """The one build-system answer a legacy reader can hold, or an honest none."""

    referenced = {
        build_id
        for item in workspace.components
        if item.id in selected
        for build_id in item.build_ids
    }
    if not referenced:
        return {
            "backend": None,
            "backend_descriptor": "",
            "backend_reason": "no build unit is referenced by the selected scope",
        }
    if len(referenced) > 1:
        names = ", ".join(sorted(referenced))
        return {
            "backend": None,
            "backend_descriptor": "",
            "backend_reason": (
                f"scope uses several build units ({names}); "
                "the single-backend field cannot express that"
            ),
        }
    build = workspace.build(next(iter(referenced)))
    if build.system == "explicit":
        return {
            "backend": None,
            "backend_descriptor": build.directory,
            "backend_reason": (
                f"build {build.id} is user-prepared (explicit); no backend is driven"
            ),
        }
    return {
        "backend": _SYSTEM_TO_BACKEND.get(build.system, build.system),
        "backend_descriptor": build.definition or build.directory,
        "backend_reason": f"declared as builds.{build.id} ({build.system})",
    }
