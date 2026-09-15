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
from typing import Generic, TypeVar, overload

from ici.config.documents import (
    BuildDeclaration,
    CheckSetting,
    ComponentBody,
    ComponentDocument,
    ComponentReference,
    RootDocument,
)
from ici.config.errors import ConfigProblem, collect
from ici.config.layers import Layer
from ici.config.origin import Origin, Sourced
from ici.config.paths import (
    DeclaredPath,
    Executable,
    _normalise,
    substitute_environment,
)
from ici.domain.enums import ScopeKind

__all__ = [
    "Decided",
    "EffectiveBuild",
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


@overload
def _decide(current: Decided[T], candidate: Sourced[T] | None, layer: Layer) -> Decided[T]: ...


@overload
def _decide(
    current: Decided[T] | None, candidate: Sourced[T] | None, layer: Layer
) -> Decided[T] | None: ...


def _decide(
    current: Decided[T] | None, candidate: Sourced[T] | None, layer: Layer
) -> Decided[T] | None:
    """Let a later layer replace an earlier one, keeping the origin with it."""

    if candidate is None:
        return current
    if current is not None and not layer.beats(current.layer):
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
    """One component, wherever it was written.

    ``root``, ``sources``, ``test_paths`` and the other path values are
    normalised to be relative to the *workspace root*, not to the file that
    declared them. That is what makes an inline component and one declared in
    its own ``ici.toml`` produce the same effective value (SPEC-01 sections 2
    and 3): the declaration's anchor travels with the value it declared.
    """

    id: str
    root: Decided[str]
    languages: Decided[tuple[str, ...]]
    checks: tuple[EffectiveCheck, ...]
    build: Decided[str] | None = None
    sources: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    vendor: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()
    needs: Decided[tuple[str, ...]] | None = None
    external: Decided[tuple[str, ...]] | None = None
    declared_in: str = ""
    python_executable: Decided[str] | None = None

    def check(self, check_id: str) -> EffectiveCheck | None:
        for check in self.checks:
            if check.id == check_id:
                return check
        return None


@dataclass(frozen=True)
class EffectiveBuild:
    """One build configuration, shareable by several components.

    ``directory`` and ``definition`` are relative to the workspace root like
    every other composed path. ``prepare`` stays a permission word: the model
    builder decides what that permits, composition only checks the value is
    one the schema defines.
    """

    id: str
    system: Decided[str] | None
    directory: Decided[str] | None
    definition: Decided[str] | None
    variant: Decided[str]
    prepare: Decided[str] | None
    declared_in: str = ""


@dataclass(frozen=True)
class EffectiveConfig:
    """The whole workspace, composed, with a digest of its quality policy."""

    workspace_name: Decided[str] | None
    profile: Decided[str] | None
    checks: tuple[EffectiveCheck, ...]
    components: tuple[EffectiveComponent, ...]
    builds: tuple[EffectiveBuild, ...] = ()
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
    environment: Mapping[str, str] | None = None,
) -> EffectiveConfig:
    """Compose a root with the child files its references named.

    ``children`` is keyed by component id — the id comes from the root's
    reference entry, because SPEC-01 section 2 says a component file does not
    name itself. ``local`` is a flat map of already-allowlisted overlay values;
    :func:`ici.config.overlay.read_local` is what produces one. ``environment``
    supplies the ``${env:NAME}`` substitutions the path grammar permits.
    """

    problems: list[ConfigProblem] = []
    overlay = dict(local or {})
    env = dict(environment or {})
    workspace_dir = PurePosixPath(root.path).parent
    workspace_checks = tuple(_workspace_check(setting) for setting in root.checks)

    builds = tuple(
        _build(declaration, workspace_dir, overlay, env, problems) for declaration in root.builds
    )
    bodies = _bodies(root, dict(children or {}), problems)
    components = tuple(
        _component(
            body,
            declared_in,
            root.checks,
            workspace_checks,
            overlay,
            problems,
            workspace_dir=workspace_dir,
            environment=env,
        )
        for body, declared_in in bodies
    )
    collect(problems)

    return EffectiveConfig(
        workspace_name=_optional(root.workspace.name, Layer.ROOT),
        profile=_optional(root.workspace.profile, Layer.ROOT),
        checks=workspace_checks,
        components=components,
        builds=builds,
        scope_kind=ScopeKind.FULL,
    )


def compose_standalone(
    document: ComponentDocument,
    *,
    component_id: str,
    environment: Mapping[str, str] | None = None,
) -> EffectiveConfig:
    """One component file run on its own, with no root above it.

    Marked ``STANDALONE`` rather than ``FULL``. SPEC-01 section 2 forbids a
    single-component run from standing in for its parent workspace, and the
    scope kind is how a result says so rather than a caller remembering to.
    """

    problems: list[ConfigProblem] = []
    workspace_dir = PurePosixPath(document.path).parent
    component = _component(
        document.component,
        document.path,
        (),
        (),
        {},
        problems,
        identifier=component_id,
        workspace_dir=workspace_dir,
        environment=dict(environment or {}),
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
            needs=document.component.needs,
            vendor=document.component.vendor,
            external=document.component.external,
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
    workspace_dir: PurePosixPath,
    environment: Mapping[str, str],
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

    # Paths are anchored to the file that declared them (SPEC-01 section 3),
    # then normalised against the workspace root so a component means the same
    # thing written inline or in its own file. A child file's ``root = "."``
    # is the child's directory; an inline ``root = "apps/gui"`` is the root
    # file's directory plus the relative spelling.
    root_rel = _anchor(
        body.root,
        declaring_file=declared_in,
        workspace_dir=workspace_dir,
        environment=environment,
        problems=problems,
        what="component root",
    )
    root_value = Decided(
        value=root_rel,
        origin=body.root.origin if body.root else body.origin,
        layer=Layer.COMPONENT,
    )
    languages = Decided(
        value=body.languages.value if body.languages else (),
        origin=body.languages.origin if body.languages else body.origin,
        layer=Layer.COMPONENT,
    )
    scope = PurePosixPath(root_rel)
    return EffectiveComponent(
        id=component_id,
        root=root_value,
        languages=languages,
        checks=_component_checks(component_id, body, root_settings, workspace_checks, problems),
        build=_optional(body.build, Layer.COMPONENT),
        sources=tuple(glob.resolve(component_root=scope) for glob in body.sources),
        include=tuple(glob.resolve(component_root=scope) for glob in body.include),
        exclude=tuple(glob.resolve(component_root=scope) for glob in body.exclude),
        vendor=tuple(glob.resolve(component_root=scope) for glob in body.vendor),
        test_paths=tuple(
            glob.resolve(component_root=scope)
            for glob in (body.python.test_paths if body.python else ())
        ),
        needs=_optional(body.needs, Layer.COMPONENT),
        external=_external(body, declared_in, workspace_dir, environment, problems),
        declared_in=declared_in,
        python_executable=_python_executable(
            component_id,
            body,
            local,
            workspace_dir=workspace_dir,
            environment=environment,
            problems=problems,
        ),
    )


def _virtual(path: PurePosixPath) -> PurePosixPath:
    """An absolute spelling of a possibly-relative path, for anchoring.

    The declared-file anchor only needs path arithmetic, and the arithmetic
    is the same at any base — so a relative declared file (a test fixture's
    ``root.toml``) works by reasoning under a virtual ``/`` rather than by
    asking the filesystem for a real absolute path. Nothing here resolves
    symlinks; the same textual rule applies wherever the files live.
    """

    return path if path.is_absolute() else PurePosixPath("/") / path


def _anchor(
    declared: DeclaredPath | None,
    *,
    declaring_file: str,
    workspace_dir: PurePosixPath,
    environment: Mapping[str, str],
    problems: list[ConfigProblem],
    what: str,
) -> str:
    """A declared path relative to the workspace root, diagnosing escapes.

    ``root = "."`` in a child file anchors to the child's directory, then
    reads back as the child path relative to the workspace root — which is the
    same value the equivalent inline component produces. That is the
    mechanism behind *"root-only와 child 분리 설정이 같은 effective config를
    생성한다"* reaching the model builder, not just the composition tests.
    """

    if declared is None:
        return "."
    anchored = declared.resolve(
        declaring_directory=_virtual(PurePosixPath(declaring_file).parent),
        environment=environment,
        problems=problems,
    )
    base = _virtual(workspace_dir)
    if anchored == base:
        return "."
    try:
        relative = anchored.relative_to(base)
    except ValueError:
        relative = None
    if relative is None or ".." in relative.parts:
        problems.append(
            ConfigProblem(
                f"{what} escapes the workspace root: {declared.raw!r}",
                declared.origin,
                hint="keep it inside the workspace, or name an external input",
            )
        )
        return declared.raw
    return relative.as_posix()


def _external(
    body: ComponentBody,
    declared_in: str,
    workspace_dir: PurePosixPath,
    environment: Mapping[str, str],
    problems: list[ConfigProblem],
) -> Decided[tuple[str, ...]] | None:
    """Declared external inputs, anchored like other paths but allowed out.

    An external input may legitimately point outside the workspace — that is
    its purpose — so instead of diagnosing the escape the value is anchored to
    the declaring file and kept absolute, while anything that lands inside the
    root is normalised to the workspace-relative spelling like every other
    path.
    """

    if body.external is None:
        return None
    resolved: list[str] = []
    base = _virtual(PurePosixPath(declared_in).parent)
    root = _virtual(workspace_dir)
    for index, raw in enumerate(body.external.value):
        origin = body.external.origin.item(index)
        substituted = substitute_environment(
            raw, origin=origin, environment=environment, problems=problems
        )
        candidate = PurePosixPath(substituted)
        # Absolute spellings are the point of an external input — unlike every
        # other declared path they are allowed to leave the checkout. Both
        # arms normalise first: ``/repo/../outside`` must compare as
        # ``/outside``, not as a ``..`` that happens to share a prefix.
        anchored = (
            _normalise(candidate) if candidate.is_absolute() else _normalise(base / candidate)
        )
        try:
            relative = anchored.relative_to(root)
        except ValueError:
            relative = None
        # A ``/`` root means the workspace directory itself was written
        # relatively, which cannot prove an absolute path is inside.
        if (
            relative is None
            or ".." in relative.parts
            or (candidate.is_absolute() and root == PurePosixPath("/"))
        ):
            resolved.append(anchored.as_posix())
        else:
            resolved.append(relative.as_posix())
    return Decided(value=tuple(resolved), origin=body.external.origin, layer=Layer.COMPONENT)


def _build(
    declaration: BuildDeclaration,
    workspace_dir: PurePosixPath,
    local: Mapping[str, Sourced[str]],
    environment: Mapping[str, str],
    problems: list[ConfigProblem],
) -> EffectiveBuild:
    """Compose one ``[builds.<id>]`` — paths anchored at the root file.

    A local overlay may move ``directory`` or ``project`` (they are in the
    allowlist); it cannot change the system, variant or prepare permission.
    """

    declared = (
        None
        if declaration.directory is None
        else Decided(
            value=declaration.directory.raw,
            origin=declaration.directory.origin,
            layer=Layer.ROOT,
        )
    )
    directory = _decide(declared, local.get(f"builds.{declaration.id}.directory"), Layer.LOCAL)
    declared_project = (
        None
        if declaration.project is None
        else Decided(
            value=declaration.project.raw,
            origin=declaration.project.origin,
            layer=Layer.ROOT,
        )
    )
    project = _decide(declared_project, local.get(f"builds.{declaration.id}.project"), Layer.LOCAL)
    return EffectiveBuild(
        id=declaration.id,
        system=_optional(declaration.system, Layer.ROOT),
        directory=(
            None
            if directory is None
            else Decided(
                value=_anchor_declared(
                    directory.value,
                    directory.origin,
                    declaring_file=declaration.origin.file,
                    workspace_dir=workspace_dir,
                    environment=environment,
                    problems=problems,
                    what="build directory",
                ),
                origin=directory.origin,
                layer=directory.layer,
            )
        ),
        definition=(
            None
            if project is None
            else Decided(
                value=_anchor_declared(
                    project.value,
                    project.origin,
                    declaring_file=declaration.origin.file,
                    workspace_dir=workspace_dir,
                    environment=environment,
                    problems=problems,
                    what="build project",
                ),
                origin=project.origin,
                layer=project.layer,
            )
        ),
        variant=_optional(declaration.variant, Layer.ROOT)
        or Decided(value="default", origin=DEFAULTS, layer=Layer.DEFAULTS),
        prepare=_prepare(declaration, problems),
        declared_in=declaration.origin.file,
    )


def _anchor_declared(
    raw: str,
    origin: Origin,
    *,
    declaring_file: str,
    workspace_dir: PurePosixPath,
    environment: Mapping[str, str],
    problems: list[ConfigProblem],
    what: str,
) -> str:
    """Anchor a raw path value — used for overlay values and declared ones."""

    return _anchor(
        DeclaredPath(raw=raw, origin=origin),
        declaring_file=declaring_file,
        workspace_dir=workspace_dir,
        environment=environment,
        problems=problems,
        what=what,
    )


def _prepare(declaration: BuildDeclaration, problems: list[ConfigProblem]) -> Decided[str] | None:
    """The prepare word is a permission, and only ``explicit`` is defined."""

    if declaration.prepare is None:
        return None
    if declaration.prepare.value != "explicit":
        problems.append(
            ConfigProblem(
                f"prepare must be 'explicit', not {declaration.prepare.value!r}",
                declaration.prepare.origin,
                hint="ici does not invent configure commands; 'explicit' means the "
                "user runs the named step themselves",
            )
        )
    return Decided(
        value=declaration.prepare.value,
        origin=declaration.prepare.origin,
        layer=Layer.ROOT,
    )


def _executable(
    executable: Executable,
    workspace_dir: PurePosixPath,
    environment: Mapping[str, str],
    problems: list[ConfigProblem],
) -> str:
    """An interpreter identity that means the same thing from any cwd.

    A bare name stays a PATH lookup — resolving it here would bake in this
    machine's PATH, which is exactly the non-determinism the model exists to
    remove. Anything containing ``/`` is a declared path: anchored to its own
    file, then expressed workspace-relative when it lands inside and absolute
    when it does not.
    """

    if executable.searches_path:
        return executable.raw
    substituted = substitute_environment(
        executable.raw, origin=executable.origin, environment=environment, problems=problems
    )
    candidate = PurePosixPath(substituted)
    declaring_dir = _virtual(PurePosixPath(executable.origin.file).parent)
    anchored = (
        _normalise(candidate) if candidate.is_absolute() else _normalise(declaring_dir / candidate)
    )
    base = _virtual(workspace_dir)
    try:
        relative = anchored.relative_to(base)
    except ValueError:
        relative = None
    if (
        relative is None
        or ".." in relative.parts
        or (candidate.is_absolute() and base == PurePosixPath("/"))
    ):
        return anchored.as_posix()
    return relative.as_posix()


def _python_executable(
    component_id: str,
    body: ComponentBody,
    local: Mapping[str, Sourced[str]],
    *,
    workspace_dir: PurePosixPath,
    environment: Mapping[str, str],
    problems: list[ConfigProblem],
) -> Decided[str] | None:
    declared = body.python.executable if body.python else None
    override = local.get(f"components.{component_id}.python.executable")
    if override is not None and not override.value:
        problems.append(ConfigProblem("a python executable must not be empty", override.origin))
        override = None
    if override is not None:
        executable = Executable(raw=override.value, origin=override.origin)
        layer = Layer.LOCAL
    elif declared is not None:
        executable = declared
        layer = Layer.COMPONENT
    else:
        return None
    return Decided(
        value=_executable(executable, workspace_dir, environment, problems),
        origin=executable.origin,
        layer=layer,
    )


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
