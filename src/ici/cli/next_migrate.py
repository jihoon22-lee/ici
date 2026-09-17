"""``ici next migrate`` — stable config to next schema, as a preview first.

#225 item 2: the migration is a command you run, and the first thing it does
is show you. The default is a dry run; writing is an explicit choice, and the
choice that touches the source keeps the original beside it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import tomli
import typer

from ici.cli.next_common import EXIT_CONFIG, next_app
from ici.config import migration
from ici.config.convert import convert_document

_MIGRATE_SOURCE_ARG = typer.Argument(
    Path("ici.toml"), help="The stable config to convert (project ici.toml)"
)
_MIGRATE_OUTPUT_OPTION = typer.Option(
    None, "--output", help="Write the converted document here instead of printing it"
)
_MIGRATE_WRITE_OPTION = typer.Option(
    False,
    "--write",
    help="Replace the source ici.toml — the original is kept as ici.toml.stable",
)


@next_app.command("migrate")
def cmd_migrate(
    source: Path = _MIGRATE_SOURCE_ARG,
    output: Path | None = _MIGRATE_OUTPUT_OPTION,
    write: bool = _MIGRATE_WRITE_OPTION,
) -> None:
    """Preview a stable config as a next-schema document. Writes nothing unless asked.

    Default is a dry run: the converted TOML and the per-key notes go to
    stdout, and no file changes. ``--output`` writes to a new file — an
    existing one is refused rather than clobbered. ``--write`` replaces the
    source and first copies it aside, because overwriting the file the stable
    tool still reads is a decision with a rollback path, not a default.
    """

    root = Path.cwd().resolve()
    source = source if source.is_absolute() else root / source
    if not source.is_file():
        typer.echo(f"migrate: no such file: {source}", err=True)
        raise typer.Exit(EXIT_CONFIG)

    try:
        document = tomli.loads(source.read_text(encoding="utf-8"))
    except tomli.TOMLDecodeError as error:
        typer.echo(f"migrate: {source} is not valid TOML: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    if "workspace" in document:
        typer.echo(f"migrate: {source} already declares [workspace] — nothing to convert")
        raise typer.Exit(EXIT_CONFIG)
    if not any(key in document for key in ("ici", "project", "engines")):
        typer.echo(
            f"migrate: {source} has no [ici]/[project]/[engines] table — "
            "it does not look like a stable config",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG)

    # Other layers can quietly change what the converted file means — say so.
    layered = migration.report(root)
    for line in layered.lines():
        typer.echo(line, err=True)

    text, notes = convert_document(
        document,
        workspace_name=root.name or "workspace",
        component_root=".",
        workspace_root=root,
    )
    typer.echo(text)
    for note in notes:
        typer.echo(f"  {note}", err=True)
    if not any(note.disposition == "converted" for note in notes):
        typer.echo("migrate: nothing in the source converts — no file written", err=True)
        raise typer.Exit(EXIT_CONFIG)

    target = output if output is not None else (source if write else None)
    if target is None:
        typer.echo("dry run — pass --output PATH or --write to apply", err=True)
        return
    target = target if target.is_absolute() else root / target
    if target.exists() and target.resolve() != source.resolve():
        typer.echo(f"migrate: {target} exists — refusing to overwrite it", err=True)
        raise typer.Exit(EXIT_CONFIG)
    if target.resolve() == source.resolve():
        backup = source.with_suffix(source.suffix + ".stable")
        shutil.copyfile(source, backup)
        typer.echo(f"  original kept as {backup.name}", err=True)
    target.write_text(text, encoding="utf-8")
    typer.echo(f"wrote {target}", err=True)
