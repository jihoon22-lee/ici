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

from collections.abc import Iterable
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
    PythonSettings,
    RootDocument,
    ToolSetting,
    WorkspaceSettings,
)
from ici.config.errors import ConfigProblem, NextConfigError, collect, fail
from ici.config.origin import Origin, Sourced
from ici.config.reader import Table

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
    for key in ("root", "languages", "build", "sources", "include", "exclude", "python", "cpp"):
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
    )
    table.done()
    return body


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
