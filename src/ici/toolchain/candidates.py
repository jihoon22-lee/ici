"""Turning a composed configuration into candidates, with nothing assumed.

#204 item 2 gives one rule per role, and item 7 says what must stop happening in
the new path: no hardcoded department library, and no automatic preference for a
``.venv`` nobody declared. Both are the same mistake in different clothes —
a location that got into the answer without anyone putting it there.

The stable path has one of each:

- ``core/env.py`` ``get_nas_cpp_lib_dir`` returns
  ``libs/cpp/ips-core-lib/v1.2.3/x86_64`` — one department's library, pinned to
  one version, compiled into ici.
- ``find_project_executable`` looks in ``.venv`` whether or not the project said
  anything about it, which is what lets ``_resolve_python`` reach
  ``sys.executable`` when it finds nothing: a chain of guesses ending in ici's
  own interpreter.

So candidates here come from the composed configuration and nowhere else. A
component that declares no interpreter produces **no candidates**, which the
resolver turns into ``Unresolved`` — a value that does not run. That is the
first acceptance criterion of #204, and it is reached by having nothing to fall
back to rather than by remembering not to.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ici.config.composition import EffectiveComponent
from ici.toolchain.resolution import Role
from ici.toolchain.resolver import Candidate, Request

__all__ = ["analyzer_request", "python_request"]

# A module-level singleton so the default is not a call in an argument list.
_HERE = PurePosixPath(".")


def python_request(
    component: EffectiveComponent,
    *,
    workspace_root: PurePosixPath = _HERE,
) -> Request:
    """The interpreter this component's tests run on, if it declared one.

    Returns a request with no candidates when the component is silent. That is
    deliberate and is the whole point: the resolver then reports that nothing
    was found, instead of a search widening until something answers.
    """

    declared = component.python_executable
    if declared is None:
        return Request(role=Role.PROJECT_PYTHON, name="python")

    key = declared.origin.key or f"components.{component.id}.python.executable"
    return Request(
        role=Role.PROJECT_PYTHON,
        name="python",
        candidates=(
            Candidate(
                path=_anchor(declared.value, component, workspace_root),
                source=declared.origin.file,
                config_key=key,
                # Declared means chosen. A declared interpreter that cannot run
                # is an error to report, not a reason to look elsewhere.
                explicit=True,
            ),
        ),
        version_argv=("--version",),
    )


def analyzer_request(
    name: str,
    *,
    bundle_path: str | None = None,
    declared_path: str | None = None,
    declared_key: str | None = None,
    minimum_version: tuple[int, ...] | None = None,
) -> Request:
    """An analyzer: the bundle's copy, or one the workspace named.

    Not PATH. #204 item 2 says "bundle 또는 명시 external", and the reason is
    reproducibility: a linter picked up from PATH makes the result depend on
    what else is installed on the machine, which is the property SPEC-01
    section 7 asks official runs to remove rather than document.
    """

    candidates: list[Candidate] = []
    if declared_path is not None:
        candidates.append(
            Candidate(
                path=declared_path,
                source="config",
                config_key=declared_key or f"tools.{name}.path",
                explicit=True,
            )
        )
    elif bundle_path is not None:
        candidates.append(Candidate(path=bundle_path, source="bundle", config_key=f"tools.{name}"))
    return Request(
        role=Role.ANALYZER,
        name=name,
        candidates=tuple(candidates),
        minimum_version=minimum_version,
    )


def _anchor(raw: str, component: EffectiveComponent, workspace_root: PurePosixPath) -> str:
    """Anchor a declared executable the way SPEC-01 section 3 requires.

    A bare name is a PATH lookup and is left alone. Anything with a separator is
    a path, anchored to the component root — not to the working directory, so
    running from a subdirectory cannot change which interpreter is chosen.
    """

    if "/" not in raw:
        return raw
    if raw.startswith("/"):
        return raw
    return str(workspace_root / component.root.value / raw)
