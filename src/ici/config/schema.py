"""The ici-next configuration schema, version 1.

Reads the shapes SPEC-01 section 4 shows, and refuses the four things WP05
#203 names: an unknown key, a duplicate id, a reference to something that does
not exist, and a component defined inline *and* by reference at once. Each
refusal names the key and the file it came from, because a message that does
not is a message the user has to go hunting behind.

The schema is not the stable ``ici.config_schema``. That one validates the v0.11
file this repository runs on today; this one reads the next-path file, and the
two deliberately share nothing so that neither constrains the other. WP27 owns
the migration between them.

Nothing here opens a file. ``read_root`` and ``read_component`` take text and a
path, so every test is a string and no test needs a temporary directory to ask
a question about the schema.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Protocol

import tomli

from ici.config.documents import (
    BuildDeclaration,
    CheckSetting,
    ComponentBody,
    ComponentDocument,
    ComponentEntry,
    ComponentReference,
    CppSettings,
    Exemption,
    IntegrationCaseBody,
    IntegrationOutputBody,
    PythonSettings,
    RootDocument,
    ToolSetting,
    WorkspaceSettings,
)
from ici.config.errors import ConfigProblem, NextConfigError, collect, fail
from ici.config.origin import Origin, Sourced
from ici.config.paths import Executable
from ici.config.reader import Table
from ici.engines import _integration as stable_integration

__all__ = ["SCHEMA_VERSION", "read_component", "read_root"]

SCHEMA_VERSION = 1

# A reference entry may carry these and nothing else (SPEC-01 section 2).
_REFERENCE_KEYS = frozenset({"id", "config"})


def read_root(text: str, *, path: str) -> RootDocument:
    """Read a file containing ``[workspace]``. Raises with every problem found."""

    problems: list[ConfigProblem] = []
    document = _parse(text, path=path, problems=problems)
    root = Table(document, Origin(file=path), problems)

    version = _schema_version(root, problems)
    workspace = _workspace(root, problems)
    checks = tuple(_check(table, problems) for table in root.tables("checks"))
    tools = tuple(_tool(table) for table in root.tables("tools"))
    builds = tuple(_build(table) for table in root.tables("builds"))
    entries = tuple(_entry(table, problems) for table in root.array_of_tables("components"))
    root.done()

    _reject_duplicate_ids(entries, problems)
    _reject_duplicate_ids(builds, problems)
    _reject_duplicate_ids(checks, problems)
    _reject_duplicate_ids(tools, problems)
    _reject_dangling_builds(entries, builds, problems)
    collect(problems)

    return RootDocument(
        path=path,
        schema_version=version,
        workspace=workspace,
        checks=checks,
        tools=tools,
        builds=builds,
        components=entries,
    )


def read_component(text: str, *, path: str) -> ComponentDocument:
    """Read a file containing ``[component]`` and no ``[workspace]``."""

    problems: list[ConfigProblem] = []
    document = _parse(text, path=path, problems=problems)
    root = Table(document, Origin(file=path), problems)

    version = _schema_version(root, problems)
    if root.has("workspace"):
        problems.append(
            ConfigProblem(
                "a component file must not declare a workspace",
                Origin(file=path, key="workspace"),
                hint="a component file is registered by a root, not promoted to one",
            )
        )
        root.table("workspace")

    body_table = root.table("component")
    if body_table is None:
        problems.append(ConfigProblem("no [component] table", Origin(file=path, key="component")))
        fail(problems)
    body = _component_body(body_table, language_sections=root, problems=problems)
    root.done()
    collect(problems)

    return ComponentDocument(path=path, schema_version=version, component=body)


def _parse(text: str, *, path: str, problems: list[ConfigProblem]) -> dict[str, object]:
    # tomli, not tomllib: the floor is Python 3.10 (ADR-0003) and tomllib
    # arrived in 3.11. The rest of the tree imports it the same way.
    try:
        return tomli.loads(text)
    except tomli.TOMLDecodeError as error:
        problems.append(ConfigProblem(f"not valid TOML: {error}", Origin(file=path)))
        raise NextConfigError(tuple(problems)) from error


def _schema_version(root: Table, problems: list[ConfigProblem]) -> Sourced[int]:
    version = root.integer("schema_version")
    if version is None:
        problems.append(
            ConfigProblem(
                "no schema_version",
                root.origin.child("schema_version"),
                hint=f"write schema_version = {SCHEMA_VERSION}",
            )
        )
        return Sourced(value=SCHEMA_VERSION, origin=root.origin.child("schema_version"))
    if version.value != SCHEMA_VERSION:
        problems.append(
            ConfigProblem(
                f"schema_version {version.value} is not supported",
                version.origin,
                hint=f"this ici reads version {SCHEMA_VERSION}",
            )
        )
    return version


def _workspace(root: Table, problems: list[ConfigProblem]) -> WorkspaceSettings:
    table = root.table("workspace")
    if table is None:
        problems.append(
            ConfigProblem(
                "no [workspace] table",
                root.origin.child("workspace"),
                hint="a file without one is a component file, not a root",
            )
        )
        fail(problems)
    settings = WorkspaceSettings(
        name=table.text("name"), profile=table.text("profile"), origin=table.origin
    )
    table.done()
    return settings


def _check(table: Table, problems: list[ConfigProblem]) -> CheckSetting:
    setting = CheckSetting(
        id=table.name(),
        enabled=table.flag("enabled"),
        required=table.flag("required"),
        origin=table.origin,
        exemptions=tuple(_exemption(entry, problems) for entry in table.tables("exemptions")),
    )
    table.done()
    return setting


def _exemption(table: Table, problems: list[ConfigProblem]) -> Exemption:
    """``[checks.<id>.exemptions.<component>]`` — a named, reasoned relaxation."""

    reason = table.text("reason")
    if reason is None:
        problems.append(
            ConfigProblem(
                "an exemption must say why",
                table.origin.child("reason"),
                hint="the reason is what makes this different from a silent relaxation",
            )
        )
        reason = Sourced(value="", origin=table.origin.child("reason"))
    table.done()
    return Exemption(component_id=table.name(), reason=reason, origin=table.origin)


def _tool(table: Table) -> ToolSetting:
    setting = ToolSetting(
        id=table.name(),
        source=table.text("source"),
        config=table.declared_path("config"),
        path=table.declared_path("path"),
        origin=table.origin,
    )
    table.done()
    return setting


def _build(table: Table) -> BuildDeclaration:
    declaration = BuildDeclaration(
        id=table.name(),
        system=table.text("system"),
        project=table.declared_path("project"),
        directory=table.declared_path("directory"),
        variant=table.text("variant"),
        prepare=table.text("prepare"),
        artifacts=table.globs("artifacts"),
        origin=table.origin,
    )
    table.done()
    return declaration


def _entry(table: Table, problems: list[ConfigProblem]) -> ComponentEntry:
    """One ``[[components]]`` entry: a reference, or a definition, never both."""

    defines = tuple(sorted(key for key in _defining_keys(table) if key not in _REFERENCE_KEYS))
    if table.has("config"):
        if defines:
            problems.append(
                ConfigProblem(
                    f"defined here and referenced by config at once ({', '.join(defines)})",
                    table.origin,
                    hint="a reference entry carries id and config only",
                )
            )
        # The keys are named in the message above, so they are not also reported
        # one by one as unknown. Three messages about one mistake is how a
        # reader learns to skim them.
        return _reference(table, problems, already_reported=defines)
    return _component_body(table, language_sections=table, problems=problems)


def _defining_keys(table: Table) -> Iterable[str]:
    for key in (
        "root",
        "languages",
        "build",
        "sources",
        "include",
        "exclude",
        "python",
        "cpp",
        "needs",
        "vendor",
        "external",
        "integrations",
    ):
        if table.has(key):
            yield key


def _reference(
    table: Table, problems: list[ConfigProblem], *, already_reported: tuple[str, ...] = ()
) -> ComponentReference:
    identifier = table.text("id")
    config = table.declared_path("config")
    if identifier is None:
        problems.append(
            ConfigProblem(
                "a reference entry must name the component it registers",
                table.origin.child("id"),
            )
        )
    if config is None:
        problems.append(ConfigProblem("config must be a path", table.origin.child("config")))
    # Everything except id and config is reported as unknown for this shape.
    table.done(known=already_reported)
    if identifier is None or config is None:
        fail(problems)
    return ComponentReference(id=identifier, config=config, origin=table.origin)


def _component_body(
    table: Table, *, language_sections: Table, problems: list[ConfigProblem]
) -> ComponentBody:
    """A component definition.

    ``language_sections`` differs from ``table`` for a child file, where the
    body is ``[component]`` but ``[python]`` and ``[cpp]`` sit beside it at the
    top level. Passing it in keeps one reader for both shapes, which is what
    makes "the same effective config either way" a property of the code rather
    than a promise.
    """

    body = ComponentBody(
        checks=tuple(_check(entry, problems) for entry in language_sections.tables("checks")),
        id=table.text("id"),
        root=table.declared_path("root"),
        languages=table.text_list("languages"),
        build=table.text("build"),
        sources=table.globs("sources"),
        include=table.globs("include"),
        exclude=table.globs("exclude"),
        python=_python(language_sections.table("python")),
        cpp=_cpp(language_sections.table("cpp")),
        origin=table.origin,
        needs=table.text_list("needs"),
        vendor=table.globs("vendor"),
        external=table.text_list("external"),
        integrations=_integration_cases(table, problems),
    )
    table.done()
    return body


def _integration_cases(
    table: Table, problems: list[ConfigProblem]
) -> tuple[IntegrationCaseBody, ...]:
    """``[[components.integrations]]`` — declared process contracts (#220).

    The bounds are the stable engine's — same maximum counts, same control-
    character and placeholder rules — because the contract a case states is
    unchanged; what differs is where the declaration lives and who resolves
    its placeholders.
    """

    entries = table.array_of_tables("integrations")
    if len(entries) > stable_integration.MAX_CASES:
        problems.append(
            ConfigProblem(
                f"a component may declare at most {stable_integration.MAX_CASES} integration cases",
                table.origin.child("integrations"),
            )
        )
    seen: set[str] = set()
    cases: list[IntegrationCaseBody] = []
    for index, entry in enumerate(entries):
        cases.append(_integration_case(entry, index, seen, problems))
    return tuple(cases)


def _integration_case(
    table: Table, index: int, seen: set[str], problems: list[ConfigProblem]
) -> IntegrationCaseBody:
    """One case's shape — resolution of its placeholders is the plan's job."""

    setting = f"integrations[{index}]"
    name = table.text("name")
    if name is None:
        problems.append(
            ConfigProblem("an integration case must have a name", table.origin.child("name"))
        )
    elif len(name.value) > 128 or name.value in seen:
        problems.append(
            ConfigProblem(f"{setting}.name must be a unique bounded string", name.origin)
        )
        name = None
    if name is not None:
        seen.add(name.value)

    argv = table.text_list("argv")
    if argv is None:
        problems.append(
            ConfigProblem("an integration case must declare argv", table.origin.child("argv"))
        )
    else:
        _integration_argv(argv, setting, problems)

    expected_exit = table.integer("expected_exit")
    if expected_exit is not None and not -(2**31) <= expected_exit.value < 2**31:
        problems.append(
            ConfigProblem(f"{setting}.expected_exit must be a 32-bit integer", expected_exit.origin)
        )
        expected_exit = None
    timeout = table.number("timeout_seconds")
    if timeout is not None and not 0.1 <= timeout.value <= 300:
        problems.append(
            ConfigProblem(f"{setting}.timeout_seconds must be between 0.1 and 300", timeout.origin)
        )
        timeout = None

    body = IntegrationCaseBody(
        name=name,
        argv=argv,
        expected_exit=expected_exit,
        stdout_contains=_bounded_strings(table, "stdout_contains", setting, problems),
        stderr_contains=_bounded_strings(table, "stderr_contains", setting, problems),
        stdout_not_contains=_bounded_strings(table, "stdout_not_contains", setting, problems),
        stderr_not_contains=_bounded_strings(table, "stderr_not_contains", setting, problems),
        timeout_seconds=timeout,
        env=_case_env(table, setting, problems),
        requires=_bounded_strings(table, "requires", setting, problems, limit=128),
        python_targets=_python_targets(table, setting, problems),
        output_artifacts=_integration_outputs(table, setting, problems),
        required=table.flag("required"),
        origin=table.origin,
    )
    table.done()
    return body


def _integration_argv(
    argv: Sourced[tuple[str, ...]], setting: str, problems: list[ConfigProblem]
) -> None:
    """argv[0] is a typed placeholder; braces elsewhere must be whole tokens."""

    if not argv.value:
        problems.append(ConfigProblem(f"{setting}.argv must not be empty", argv.origin))
        return
    if len(argv.value) > stable_integration.MAX_ARGV:
        problems.append(
            ConfigProblem(
                f"{setting}.argv must hold at most {stable_integration.MAX_ARGV} entries",
                argv.origin,
            )
        )
    if stable_integration._EXECUTABLE_PLACEHOLDER_RE.fullmatch(argv.value[0]) is None:
        problems.append(
            ConfigProblem(
                f"{setting}.argv[0] must be a typed placeholder — "
                "{python:NAME} or {artifact:BUILD/PATH}",
                argv.origin.item(0),
            )
        )
    if sum(len(token) for token in argv.value) > 32 * 1024:
        problems.append(ConfigProblem(f"{setting}.argv exceeds the aggregate bound", argv.origin))
    for index, token in enumerate(argv.value):
        if len(token) > 1024 or any(ord(char) < 32 for char in token):
            problems.append(
                ConfigProblem(
                    f"{setting}.argv[{index}] must be a bounded printable string",
                    argv.origin.item(index),
                )
            )
        if ("{" in token or "}" in token) and _PLACEHOLDER.fullmatch(token) is None:
            problems.append(
                ConfigProblem(
                    f"{setting}.argv[{index}] is a partial or unknown placeholder: {token!r}",
                    argv.origin.item(index),
                )
            )


def _bounded_strings(
    table: Table, key: str, setting: str, problems: list[ConfigProblem], *, limit: int = 1024
) -> tuple[str, ...]:
    found = table.text_list(key)
    if found is None:
        return ()
    if len(found.value) > stable_integration.MAX_ASSERTIONS:
        problems.append(
            ConfigProblem(
                f"{setting}.{key} holds at most {stable_integration.MAX_ASSERTIONS} entries",
                found.origin,
            )
        )
    values: list[str] = []
    for index, item in enumerate(found.value):
        if len(item) > limit or any(ord(char) < 32 for char in item):
            problems.append(
                ConfigProblem(
                    f"{setting}.{key}[{index}] must be a bounded printable string",
                    found.origin.item(index),
                )
            )
            continue
        values.append(item)
    return tuple(values)


def _case_env(
    table: Table, setting: str, problems: list[ConfigProblem]
) -> tuple[tuple[str, Sourced[str]], ...]:
    found = table.text_map("env")
    if found is None:
        return ()
    if len(found) > stable_integration.MAX_ENV:
        problems.append(
            ConfigProblem(
                f"{setting}.env holds at most {stable_integration.MAX_ENV} entries",
                table.origin.child("env"),
            )
        )
    result: list[tuple[str, Sourced[str]]] = []
    for name, value in found:
        if stable_integration._ENV_NAME_RE.fullmatch(name) is None:
            problems.append(
                ConfigProblem(f"{setting}.env has an invalid name {name!r}", value.origin)
            )
            continue
        if name.startswith("ICI_"):
            problems.append(
                ConfigProblem(
                    f"{setting}.env must not set {name!r} — ICI_* carries the "
                    "run's own contract to the checker",
                    value.origin,
                )
            )
            continue
        if len(value.value) > 4096 or any(ord(char) < 32 for char in value.value):
            problems.append(
                ConfigProblem(
                    f"{setting}.env.{name} must be a bounded printable string", value.origin
                )
            )
            continue
        result.append((name, value))
    return tuple(result)


def _python_targets(
    table: Table, setting: str, problems: list[ConfigProblem]
) -> tuple[tuple[str, Executable], ...]:
    found = table.text_map("python_targets")
    if found is None:
        return ()
    if len(found) > 32:
        problems.append(
            ConfigProblem(
                f"{setting}.python_targets holds at most 32 entries",
                table.origin.child("python_targets"),
            )
        )
    result: list[tuple[str, Executable]] = []
    for name, value in found:
        if _PY_TARGET_RE.fullmatch(name) is None:
            problems.append(
                ConfigProblem(
                    f"{setting}.python_targets has an invalid name {name!r}", value.origin
                )
            )
            continue
        if len(value.value) > 1024 or any(ord(char) < 32 for char in value.value):
            problems.append(
                ConfigProblem(
                    f"{setting}.python_targets.{name} must be a bounded path", value.origin
                )
            )
            continue
        result.append((name, Executable(raw=value.value, origin=value.origin)))
    return tuple(result)


def _integration_outputs(
    table: Table, setting: str, problems: list[ConfigProblem]
) -> tuple[IntegrationOutputBody, ...]:
    entries = table.array_of_tables("output_artifacts")
    if len(entries) > stable_integration.MAX_ASSERTIONS:
        problems.append(
            ConfigProblem(
                f"{setting}.output_artifacts holds at most "
                f"{stable_integration.MAX_ASSERTIONS} entries",
                table.origin.child("output_artifacts"),
            )
        )
    return tuple(
        _integration_output(entry, f"{setting}.output_artifacts[{index}]", problems)
        for index, entry in enumerate(entries)
    )


def _integration_output(
    table: Table, setting: str, problems: list[ConfigProblem]
) -> IntegrationOutputBody:
    path = table.declared_path("path")
    if path is None:
        problems.append(ConfigProblem(f"{setting} must declare path", table.origin.child("path")))
    else:
        posix = PurePosixPath(path.raw)
        if (
            "\\" in path.raw
            or posix.is_absolute()
            or ".." in posix.parts
            or posix.as_posix() != path.raw
        ):
            problems.append(
                ConfigProblem(
                    f"{setting}.path must be a contained, canonical POSIX path",
                    path.origin,
                )
            )
            path = None
    min_size = table.integer("min_size")
    if min_size is not None and not 0 <= min_size.value <= 64 * 1024 * 1024:
        problems.append(
            ConfigProblem(f"{setting}.min_size must be between 0 and 67108864", min_size.origin)
        )
        min_size = None
    kind = table.text("kind")
    body = IntegrationOutputBody(path=path, kind=kind, min_size=min_size, origin=table.origin)
    table.done()
    return body


_PY_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_PLACEHOLDER = re.compile(r"^\{(?:python|artifact):[^{}]+\}$")


def _python(table: Table | None) -> PythonSettings | None:
    if table is None:
        return None
    settings = PythonSettings(
        executable=table.executable("executable"),
        type_provider=table.text("type_provider"),
        test_tools=table.text("test_tools"),
        test_paths=table.globs("test_paths"),
        origin=table.origin,
    )
    table.done()
    return settings


def _cpp(table: Table | None) -> CppSettings | None:
    if table is None:
        return None
    settings = CppSettings(qt=table.flag("qt"), origin=table.origin)
    table.done()
    return settings


def _reject_duplicate_ids(items: Iterable[_Identified], problems: list[ConfigProblem]) -> None:
    """Two declarations of one id, reported at the second one.

    Reported where the duplicate is rather than at the first declaration: the
    second is the line the author most likely just wrote.
    """

    seen: dict[str, Origin] = {}
    for item in items:
        identifier, origin = _identity(item)
        if identifier is None:
            continue
        first = seen.get(identifier)
        if first is not None:
            problems.append(
                ConfigProblem(
                    f"duplicate id {identifier}",
                    origin,
                    hint=f"already declared at {first}",
                )
            )
            continue
        seen[identifier] = origin


class _Identified(Protocol):
    """Anything the duplicate check can ask for an id and an origin.

    A protocol rather than a union because the four things being checked spell
    their id differently — a check's id is the table name it was written under,
    a component's is a value the user typed and so carries its own origin — and
    the check cares about neither difference.
    """

    # Read-only properties, not attributes: a Protocol's mutable attribute is
    # invariant, so `id: object` would reject a frozen dataclass whose id is a
    # str — which is all four of them.
    @property
    def id(self) -> object: ...

    @property
    def origin(self) -> Origin: ...


def _identity(item: _Identified) -> tuple[str | None, Origin]:
    """The id to compare, at the most specific origin available."""

    identifier = item.id
    if isinstance(identifier, Sourced):
        return identifier.value, identifier.origin
    if isinstance(identifier, str):
        return identifier, item.origin
    return None, item.origin


def _reject_dangling_builds(
    entries: Iterable[ComponentEntry],
    builds: Iterable[BuildDeclaration],
    problems: list[ConfigProblem],
) -> None:
    declared = {build.id for build in builds}
    for entry in entries:
        if not isinstance(entry, ComponentBody) or entry.build is None:
            continue
        if entry.build.value in declared:
            continue
        known = ", ".join(sorted(declared)) or "none are declared"
        problems.append(
            ConfigProblem(
                f"no build named {entry.build.value}",
                entry.build.origin,
                hint=f"declared builds: {known}",
            )
        )
