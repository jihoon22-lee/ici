"""Composing the layers into one configuration that still knows its sources.

The acceptance criterion this exists for reads: *root-only와 child 분리 설정이 같은
effective config를 생성한다*. SPEC-01 section 2 asks for it because a workspace
should be free to store a component inline or in its own file, and a person
reorganising files should not have to wonder whether they changed the build.

That property is not asserted here by comparing two code paths — there is one.
A component body is read the same way wherever it was written (PR A), and
composition never asks which file it came from except to record the answer.

The two refusals are the substance:

- a component may raise a requirement, never lower one, unless the root granted
  it a named and reasoned exemption;
- a local overlay may move paths, never change what is checked.

Both name the key and the file. Neither is last-write-wins, because a silent
relaxation is the exact failure the whole work package exists to end.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Generic, TypeVar

from ici.config.documents import (
    CheckSetting,
    ComponentBody,
    ComponentDocument,
    ComponentReference,
    RootDocument,
)
from ici.config.errors import ConfigProblem, collect
from ici.config.layers import Layer
from ici.config.origin import Origin, Sourced
from ici.domain.enums import ScopeKind

__all__ = [
    "Decided",
    "EffectiveCheck",
    "EffectiveComponent",
    "EffectiveConfig",
    "compose",
    "compose_standalone",
]

T = TypeVar("T")

DEFAULTS = Origin(file="<built-in defaults>")

# A check nobody configured runs, and is allowed to fail. Enabled by default so
# that adding an engine does not need every workspace to opt in; not required by
# default so that adding one cannot turn a passing workspace red without anyone
# deciding that it should.
DEFAULT_ENABLED = True
DEFAULT_REQUIRED = False


@dataclass(frozen=True)
class Decided(Generic[T]):
    """A composed value: what it is, where it was written, which layer won."""

    value: T
    origin: Origin
    layer: Layer

    def __str__(self) -> str:
        return f"{self.value!r} from {self.origin} ({self.layer.value})"


def _decide(current: Decided[T], candidate: Sourced[T] | None, layer: Layer) -> Decided[T]:
    """Let a later layer replace an earlier one, keeping the origin with it."""

    if candidate is None or not layer.beats(current.layer):
        return current
    return Decided(value=candidate.value, origin=candidate.origin, layer=layer)


@dataclass(frozen=True)
class EffectiveCheck:
    """One check, after every layer that had an opinion about it."""

    id: str
    enabled: Decided[bool]
    required: Decided[bool]
    exempted_reason: Sourced[str] | None = None

    @property
    def relaxed_by_exemption(self) -> bool:
        return self.exempted_reason is not None


@dataclass(frozen=True)
class EffectiveComponent:
    """One component, wherever it was written."""

    id: str
    root: Decided[str]
    languages: Decided[tuple[str, ...]]
    checks: tuple[EffectiveCheck, ...]
    build: Decided[str] | None = None
    sources: tuple[str, ...] = ()
    declared_in: str = ""
    python_executable: Decided[str] | None = None

    def check(self, check_id: str) -> EffectiveCheck | None:
        for check in self.checks:
            if check.id == check_id:
                return check
        return None


@dataclass(frozen=True)
class EffectiveConfig:
    """The whole workspace, composed, with a digest of its quality policy."""

    workspace_name: Decided[str] | None
    profile: Decided[str] | None
    checks: tuple[EffectiveCheck, ...]
    components: tuple[EffectiveComponent, ...]
    scope_kind: ScopeKind = ScopeKind.FULL
    sources: tuple[str, ...] = field(default_factory=tuple)

    def component(self, component_id: str) -> EffectiveComponent | None:
        for component in self.components:
            if component.id == component_id:
                return component
        return None

    @property
    def policy_digest(self) -> str:
        """A digest of what is checked, not of how it is displayed.

        SPEC-01 section 3 requires an effective policy digest, and its
        acceptance criteria require that a personal display setting does not
        change it. So this covers checks and component scope and deliberately
        omits the workspace name, the profile label and every path a local
        overlay is allowed to move: two machines running the same policy with
        different build directories are running the same policy.
        """

        payload = {
            "checks": [
                {"id": check.id, "enabled": check.enabled.value, "required": check.required.value}
                for check in self.checks
            ],
            "components": [
                {
                    "id": component.id,
                    "languages": list(component.languages.value),
                    "checks": [
                        {
                            "id": check.id,
                            "enabled": check.enabled.value,
                            "required": check.required.value,
                        }
                        for check in component.checks
                    ],
                }
                for component in self.components
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def compose(
    root: RootDocument,
    children: Mapping[str, ComponentDocument] | None = None,
    *,
    local: Mapping[str, Sourced[str]] | None = None,
) -> EffectiveConfig:
    """Compose a root with the child files its references named.

    ``children`` is keyed by component id — the id comes from the root's
    reference entry, because SPEC-01 section 2 says a component file does not
    name itself. ``local`` is a flat map of already-allowlisted overlay values;
    :func:`ici.config.overlay.read_local` is what produces one.
    """

    problems: list[ConfigProblem] = []
    resolved_children = dict(children or {})
    workspace_checks = tuple(_workspace_check(setting) for setting in root.checks)

    bodies = _bodies(root, resolved_children, problems)
    components = tuple(
        _component(body, declared_in, root.checks, workspace_checks, local or {}, problems)
        for body, declared_in in bodies
    )
    collect(problems)

    return EffectiveConfig(
        workspace_name=_optional(root.workspace.name, Layer.ROOT),
        profile=_optional(root.workspace.profile, Layer.ROOT),
        checks=workspace_checks,
        components=components,
        scope_kind=ScopeKind.FULL,
    )


def compose_standalone(document: ComponentDocument, *, component_id: str) -> EffectiveConfig:
    """One component file run on its own, with no root above it.

    Marked ``STANDALONE`` rather than ``FULL``. SPEC-01 section 2 forbids a
    single-component run from standing in for its parent workspace, and the
    scope kind is how a result says so rather than a caller remembering to.
    """

    problems: list[ConfigProblem] = []
    component = _component(
        document.component, document.path, (), (), {}, problems, identifier=component_id
    )
    collect(problems)
    return EffectiveConfig(
        workspace_name=None,
        profile=None,
        checks=(),
        components=(component,),
        scope_kind=ScopeKind.STANDALONE,
    )


def _optional(value: Sourced[T] | None, layer: Layer) -> Decided[T] | None:
    return None if value is None else Decided(value=value.value, origin=value.origin, layer=layer)


def _workspace_check(setting: CheckSetting) -> EffectiveCheck:
    enabled = Decided(value=DEFAULT_ENABLED, origin=DEFAULTS, layer=Layer.DEFAULTS)
    required = Decided(value=DEFAULT_REQUIRED, origin=DEFAULTS, layer=Layer.DEFAULTS)
    return EffectiveCheck(
        id=setting.id,
        enabled=_decide(enabled, setting.enabled, Layer.ROOT),
        required=_decide(required, setting.required, Layer.ROOT),
    )


def _bodies(
    root: RootDocument,
    children: Mapping[str, ComponentDocument],
    problems: list[ConfigProblem],
) -> list[tuple[ComponentBody, str]]:
    """Every component, inline or followed, paired with the file it came from."""

    bodies: list[tuple[ComponentBody, str]] = []
    for entry in root.components:
        if isinstance(entry, ComponentReference):
            followed = _follow(entry, children, problems)
            if followed is not None:
                bodies.append(followed)
            continue
        bodies.append((entry, root.path))
    return bodies


def _follow(
    entry: ComponentReference,
    children: Mapping[str, ComponentDocument],
    problems: list[ConfigProblem],
) -> tuple[ComponentBody, str] | None:
    document = children.get(entry.id.value)
    if document is None:
        problems.append(
            ConfigProblem(
                f"the file registered for {entry.id.value} was not read",
                entry.config.origin,
                hint="composition does not open files; pass the child document in",
            )
        )
        return None
    # The reference supplies the id the child file does not carry, which is what
    # keeps a child file from being a workspace of its own (SPEC-01 section 2).
    return (
        ComponentBody(
            id=entry.id,
            root=document.component.root,
            languages=document.component.languages,
            build=document.component.build,
            sources=document.component.sources,
            include=document.component.include,
            exclude=document.component.exclude,
            python=document.component.python,
            cpp=document.component.cpp,
            origin=document.component.origin,
            checks=document.component.checks,
        ),
        document.path,
    )


def _component(
    body: ComponentBody,
    declared_in: str,
    root_settings: tuple[CheckSetting, ...],
    workspace_checks: tuple[EffectiveCheck, ...],
    local: Mapping[str, Sourced[str]],
    problems: list[ConfigProblem],
    *,
    identifier: str | None = None,
) -> EffectiveComponent:
    component_id = identifier or (body.id.value if body.id else "")
    if not component_id:
        problems.append(ConfigProblem("a component must have an id", body.origin))
    if body.root is None:
        problems.append(ConfigProblem("a component must have a root", body.origin.child("root")))
    if body.languages is None:
        problems.append(
            ConfigProblem("a component must declare a language", body.origin.child("languages"))
        )

    root_value = Decided(
        value=body.root.raw if body.root else ".",
        origin=body.root.origin if body.root else body.origin,
        layer=Layer.COMPONENT,
    )
    languages = Decided(
        value=body.languages.value if body.languages else (),
        origin=body.languages.origin if body.languages else body.origin,
        layer=Layer.COMPONENT,
    )
    return EffectiveComponent(
        id=component_id,
        root=root_value,
        languages=languages,
        checks=_component_checks(component_id, body, root_settings, workspace_checks, problems),
        build=_optional(body.build, Layer.COMPONENT),
        sources=tuple(
            glob.resolve(component_root=PurePosixPath(root_value.value)) for glob in body.sources
        ),
        declared_in=declared_in,
        python_executable=_python_executable(component_id, body, local),
    )


def _python_executable(
    component_id: str, body: ComponentBody, local: Mapping[str, Sourced[str]]
) -> Decided[str] | None:
    declared = body.python.executable if body.python else None
    current = (
        None
        if declared is None
        else Decided(value=declared.raw, origin=declared.origin, layer=Layer.COMPONENT)
    )
    override = local.get(f"components.{component_id}.python.executable")
    if override is None:
        return current
    return Decided(value=override.value, origin=override.origin, layer=Layer.LOCAL)


def _component_checks(
    component_id: str,
    body: ComponentBody,
    root_settings: tuple[CheckSetting, ...],
    workspace_checks: tuple[EffectiveCheck, ...],
    problems: list[ConfigProblem],
) -> tuple[EffectiveCheck, ...]:
    """Every check the workspace knows, as this component sees it.

    A component starts from the workspace's answer and may adjust it. Lowering a
    requirement is refused here rather than ranked away, because a precedence
    rule that quietly wins is indistinguishable from the defect this replaces.
    """

    inherited = {check.id: check for check in workspace_checks}
    settings = {setting.id: setting for setting in root_settings}
    composed: dict[str, EffectiveCheck] = dict(inherited)

    for adjustment in body.checks:
        base = composed.get(adjustment.id) or EffectiveCheck(
            id=adjustment.id,
            enabled=Decided(value=DEFAULT_ENABLED, origin=DEFAULTS, layer=Layer.DEFAULTS),
            required=Decided(value=DEFAULT_REQUIRED, origin=DEFAULTS, layer=Layer.DEFAULTS),
        )
        composed[adjustment.id] = _adjust(
            component_id, base, adjustment, settings.get(adjustment.id), problems
        )
    return tuple(composed[key] for key in sorted(composed))


def _adjust(
    component_id: str,
    base: EffectiveCheck,
    adjustment: CheckSetting,
    root_setting: CheckSetting | None,
    problems: list[ConfigProblem],
) -> EffectiveCheck:
    enabled = _decide(base.enabled, adjustment.enabled, Layer.COMPONENT)
    lowering = (
        adjustment.required is not None and base.required.value and not adjustment.required.value
    )
    exemption = root_setting.exemption_for(component_id) if root_setting else None

    if lowering and exemption is None:
        problems.append(
            ConfigProblem(
                f"{component_id} cannot make {adjustment.id} optional; "
                f"the root requires it at {base.required.origin}",
                adjustment.required.origin if adjustment.required else adjustment.origin,
                hint=(
                    f"if this is intended, the root grants it: "
                    f'[checks.{adjustment.id}.exemptions.{component_id}] reason = "..."'
                ),
            )
        )
        return EffectiveCheck(id=base.id, enabled=enabled, required=base.required)

    required = _decide(base.required, adjustment.required, Layer.COMPONENT)
    return EffectiveCheck(
        id=base.id,
        enabled=enabled,
        required=required,
        exempted_reason=exemption.reason if lowering and exemption else None,
    )
