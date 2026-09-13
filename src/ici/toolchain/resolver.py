"""The selection rules, with nothing that starts a process.

#204 item 2 gives one rule per role, and the reason they differ is that the
roles answer to different owners: the project's interpreter belongs to the
project, an analyzer belongs to ici, and a compiler belongs to whoever
configured the build.

The rules are here and the probing is injected. That is what #204 means by
"probe executor를 주입하여 runner 구현과 불필요한 순환 의존을 만들지 않는다", and
it has a second effect worth as much: every rule can be tested against a matrix
of fake executables, so the cases that matter — a stale VIRTUAL_ENV, two tools
on PATH, an explicit path that is missing, a version that is too old, output too
long to read, a path with a space — are unit tests rather than fixtures on disk.

The rule underneath all of them: **a failed explicit choice is not an invitation
to choose something else.** #204 item 3 forbids substituting another interpreter
or provider, and the first acceptance criterion forbids the specific
substitution the current path makes — testing a project with ici's own
interpreter when the project's cannot be found. Both come out as
:class:`Unresolved`, which does not run.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ici.toolchain.resolution import (
    Availability,
    ProbeResult,
    ResolvedTool,
    Role,
    SelectionOrigin,
    Unresolved,
)

__all__ = ["Candidate", "Request", "Resolver", "resolve"]

Probe = Callable[[Sequence[str]], ProbeResult]

# Enough of a version to compare. Tools print a lot around it, so the pattern is
# anchored to digits rather than to any one tool's phrasing.
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class Candidate:
    """Somewhere a tool might be, and what would make it the answer."""

    path: str
    source: str
    config_key: str | None = None
    explicit: bool = False


@dataclass(frozen=True)
class Request:
    """One tool to resolve, for one role."""

    role: Role
    name: str
    candidates: tuple[Candidate, ...] = ()
    version_argv: tuple[str, ...] = ("--version",)
    minimum_version: tuple[int, ...] | None = None
    required_capabilities: tuple[str, ...] = ()
    # Each capability is checked by asking the tool for it, not by guessing from
    # a version string (#204 item 6). The value is the argv to try.
    capability_probes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a request must name a tool")


class Resolver:
    """Resolves requests, probing only what it is asked for."""

    def __init__(
        self,
        probe: Probe,
        *,
        exists: Callable[[str], bool] | None = None,
        realpath: Callable[[str], str] | None = None,
    ) -> None:
        self._probe = probe
        self._exists = exists or (lambda path: Path(path).is_file())
        self._realpath = realpath or (lambda path: os.path.realpath(path))
        self._probed: dict[str, ProbeResult] = {}

    @property
    def probed(self) -> tuple[str, ...]:
        """Every path actually asked for a version, in call order.

        Exposed so a test can assert the *absence* of a probe: a Python-only
        workspace must not reach for qmake, and "we did not run it" is only
        checkable if something records what was run.
        """

        return tuple(self._probed)

    def resolve(self, request: Request) -> ResolvedTool | Unresolved:
        explicit = [candidate for candidate in request.candidates if candidate.explicit]
        if explicit:
            # Exactly one explicit choice is considered. Trying the rest after it
            # fails is the substitution item 3 forbids.
            return self._resolve_one(request, explicit[0], passed_over=())

        considered: list[str] = []
        # A candidate that exists and cannot be used has a specific reason —
        # it timed out, it is too old, it lacks an option. Keep the first such
        # reason: collapsing it into "nothing could be used" is the difference
        # between a user knowing to upgrade mypy and a user reinstalling it.
        specific: Unresolved | None = None
        for candidate in request.candidates:
            if not self._exists(candidate.path):
                considered.append(candidate.path)
                continue
            resolved = self._resolve_one(request, candidate, passed_over=tuple(considered))
            if resolved.usable:
                return resolved
            if isinstance(resolved, Unresolved) and specific is None:
                specific = resolved
            considered.append(candidate.path)
        if specific is not None:
            return replace(specific, considered=tuple(considered))
        return self._nothing_found(request, tuple(considered))

    def _resolve_one(
        self, request: Request, candidate: Candidate, *, passed_over: tuple[str, ...]
    ) -> ResolvedTool | Unresolved:
        origin = SelectionOrigin(
            reason=f"{request.name} chosen from {candidate.source}",
            source=candidate.source,
            config_key=candidate.config_key,
        )
        if not self._exists(candidate.path):
            return Unresolved(
                role=request.role,
                name=request.name,
                availability=Availability.UNAVAILABLE,
                origin=origin,
                detail=f"no such file: {candidate.path}",
                considered=passed_over,
            )

        result = self._version(candidate.path, request.version_argv)
        if not result.usable:
            return Unresolved(
                role=request.role,
                name=request.name,
                availability=Availability.BROKEN,
                origin=origin,
                detail=_broken_detail(result),
                considered=passed_over,
            )

        version = _parse_version(result.output)
        if request.minimum_version and (version is None or version < request.minimum_version):
            return Unresolved(
                role=request.role,
                name=request.name,
                availability=Availability.UNSUPPORTED,
                origin=origin,
                detail=(
                    f"{_render(version)} is below the required {_render(request.minimum_version)}"
                ),
                considered=passed_over,
            )

        capabilities, missing = self._capabilities(candidate.path, request)
        if missing:
            return Unresolved(
                role=request.role,
                name=request.name,
                availability=Availability.UNSUPPORTED,
                origin=origin,
                detail=f"does not support {', '.join(missing)}",
                considered=passed_over,
            )

        return ResolvedTool(
            role=request.role,
            name=request.name,
            # Launched as written. Substituting the realpath would run the same
            # file outside its virtual environment.
            launch_path=candidate.path,
            identity_path=self._realpath(candidate.path),
            origin=origin,
            version=_render(version) if version else None,
            capabilities=capabilities,
            passed_over=passed_over,
        )

    def _nothing_found(self, request: Request, considered: tuple[str, ...]) -> Unresolved:
        key = next(
            (c.config_key for c in request.candidates if c.config_key),
            None,
        )
        return Unresolved(
            role=request.role,
            name=request.name,
            availability=Availability.UNAVAILABLE,
            origin=SelectionOrigin(
                reason=f"no {request.name} was found for this role",
                source="search",
                config_key=key,
            ),
            detail=(
                f"none of {len(considered)} candidate(s) could be used"
                if considered
                else "no candidate was offered"
            ),
            considered=considered,
        )

    def _version(self, path: str, argv: tuple[str, ...]) -> ProbeResult:
        cached = self._probed.get(path)
        if cached is not None:
            return cached
        result = self._probe([path, *argv])
        self._probed[path] = result
        return result

    def _capabilities(self, path: str, request: Request) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Ask the tool for each capability rather than inferring it.

        #204 item 6: a feature is verified by the option contract it needs, not
        by guessing from a version string. Version numbers describe a release;
        a build can be missing what its version implies.
        """

        supported: list[str] = []
        missing: list[str] = []
        for capability in request.required_capabilities:
            argv = request.capability_probes.get(capability)
            if argv is None:
                missing.append(f"{capability} (no way to check it was given)")
                continue
            if self._probe([path, *argv]).usable:
                supported.append(capability)
                continue
            missing.append(capability)
        return tuple(supported), tuple(missing)


def resolve(request: Request, *, probe: Probe) -> ResolvedTool | Unresolved:
    """Resolve one request with a fresh resolver."""

    return Resolver(probe).resolve(request)


def _broken_detail(result: ProbeResult) -> str:
    if result.timed_out:
        return "the version probe timed out"
    if result.truncated:
        return "the version probe produced more output than could be read"
    return f"the version probe exited {result.exit_code}"


def _parse_version(output: str) -> tuple[int, ...] | None:
    match = _VERSION.search(output)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def _render(version: tuple[int, ...] | None) -> str:
    return "an unreadable version" if not version else ".".join(str(part) for part in version)
