"""Which tool answers for a task — the resolver seam planning and doctor share.

Everything here is a thin adapter from the workspace's declarations to the
``ici.toolchain`` resolver: a bundle prefers its own copies, a checkout takes
PATH honestly, and an explicit failure is a structured ``Unresolved`` rather
than a quiet fallback. Nothing here executes a tool — probing is bounded
through ``toolchain.launch`` and the probe-free mode starts nothing at all.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

from ici.config.composition import EffectiveComponent
from ici.toolchain.candidates import analyzer_request, python_request
from ici.toolchain.environment import EnvironmentSnapshot
from ici.toolchain.launch import probe_with
from ici.toolchain.resolution import ResolvedTool, Role, Unresolved
from ici.toolchain.resolver import Candidate, Resolver

__all__ = [
    "BUNDLED_TOOLS",
    "live_resolver",
    "locate_tool",
    "python_interpreter",
    "resolve_python",
    "resolve_tool",
]

#: Where a bundle keeps the analyzers it shipped, relative to its root.
BUNDLED_TOOLS = Path("tools") / "python-static"

#: A version probe that has not answered in this long is stuck, not slow.
_PROBE_TIMEOUT = 10.0


def _runnable(path: str) -> bool:
    return Path(path).is_file() and os.access(path, os.X_OK)


def live_resolver(*, probe: bool = True) -> Resolver:
    """The resolver live selection runs on: bounded probes, chosen environment.

    Probes go through the common executor (``toolchain.launch``), so a tool
    that hangs or floods stdout comes back ``BROKEN`` rather than stalling the
    plan, and the child sees the entry environment minus ici's own Python
    variables — an inherited ``PYTHONPATH`` must not change what a probe
    reports. One resolver is meant to be shared across a plan: it caches a
    probe per path, so asking about ``gcc`` ten times costs one launch.

    ``probe=False`` is the no-process mode ``plan``/``init``/``verify``'s
    shared planning path runs under (#206: planning starts nothing): the
    answer is existence-only — the executable bit standing in for "can be
    asked" — and the real ``--version`` stays doctor's bounded probe.
    """

    if not probe:
        return Resolver(None, exists=_runnable)
    snapshot = EnvironmentSnapshot(dict(os.environ)).for_core()
    return Resolver(probe_with(snapshot, timeout=_PROBE_TIMEOUT))


def resolve_tool(
    tool: str,
    *,
    role: Role = Role.ANALYZER,
    resolver: Resolver | None = None,
    probe: bool = True,
) -> ResolvedTool | Unresolved:
    """Where a tool is and whether it answers, asked once, here.

    **Running from a bundle, it is the bundle's copy or nothing.** #204 item 7:
    an analyzer taken from PATH makes the result depend on what else is
    installed on the machine, which is the property an offline release exists
    to remove. Falling back to PATH here would mean a bundle missing its ruff
    quietly linted with whatever the host had, and the report would not say so.

    Running from a source checkout there is no bundle to prefer, so PATH is the
    honest answer and the developer gets the tool they installed.

    The bundle is found through ``ICI_BUNDLE_ROOT``, which its launcher exports.
    A symlink to the interpreter inside ``tools/python-static/`` is deliberately
    acceptable — python-static is *the* bundled Python, not a vendored copy, so
    inside a real bundle it would have found ``mypy`` there anyway.

    The answer is a resolution, not a path: a tool that exists but cannot be
    asked — timeout, non-zero exit, output that is not a version — comes back
    ``BROKEN``, kept apart from ``UNAVAILABLE`` so the gate can say which.
    """

    root = os.environ.get("ICI_BUNDLE_ROOT")
    request = analyzer_request(
        tool,
        bundle_path=str(Path(root) / BUNDLED_TOOLS / tool) if root else None,
        search_path=None if root else shutil.which(tool),
    )
    if role is not Role.ANALYZER:
        request = replace(request, role=role)
    return (resolver or live_resolver(probe=probe)).resolve(request)


def locate_tool(tool: str) -> str | None:
    """The path to use, or None — probe-free, for the planning path.

    #206: ``plan`` starts no processes, so this checks existence and the
    executable bit, nothing more. ``doctor`` uses :func:`resolve_tool` with
    probing for the real answer.
    """

    resolved = resolve_tool(tool, probe=False)
    return resolved.launch_path if isinstance(resolved, ResolvedTool) else None


# --- python: which interpreter answers for the suite ----------------------


def resolve_python(
    effective: EffectiveComponent | None,
    component_root: Path,
    root: Path,
    *,
    resolver: Resolver | None = None,
    probe: bool = True,
) -> ResolvedTool | Unresolved:
    """The interpreter question, answered as a resolution rather than a path.

    The declared ``[python] executable`` wins and is *checked*: a declared
    interpreter that is missing or cannot start is an error to report — the
    resolver never substitutes the ``.venv`` for it (#204 item 3). Declared
    values reach us workspace-relative, so they anchor at ``root``, not at the
    working directory — the old path returned them raw and the launch depended
    on where ``ici`` was invoked from. The ``.venv`` beside the component
    stays the discoverable convention until workspace config declares
    interpreters (#210); it is offered here, at the call site, because
    ``toolchain.candidates`` deliberately invents no candidates of its own.

    ici's own interpreter is never a candidate — a test run under the
    verifier's runtime measures the wrong packages (#216).
    """

    request = python_request(
        effective,
        workspace_root=root,
        convention=(
            Candidate(
                path=str(component_root / ".venv" / "bin" / "python"),
                source="convention",
            ),
        ),
    )
    return (resolver or live_resolver(probe=probe)).resolve(request)


def python_interpreter(
    effective: EffectiveComponent | None, component_root: Path, root: Path
) -> str | None:
    """The interpreter a component's tests run under, or None.

    Called from the planning path, so it is probe-free — an existing-but-
    unusable interpreter is still chosen here and fails closed at run time.
    """

    resolved = resolve_python(effective, component_root, root, probe=False)
    return resolved.launch_path if isinstance(resolved, ResolvedTool) else None
