"""Typer adapter for the standalone compilation-context export."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer

from ici.config import load_config
from ici.core.compilation_export import (
    CompilationExportError,
    compilation_export_payload,
    config_with_database,
    load_export_context,
    render_compilation_export,
    validate_export_output,
    write_compilation_export,
)


def _effective_config(ctx: typer.Context, root: Path) -> dict[str, Any]:
    obj = ctx.ensure_object(dict)
    config = obj.get("config")
    return config if isinstance(config, dict) else load_config(root)


def export_compilation_context(
    ctx: typer.Context,
    database: str | None = typer.Option(
        None,
        "--database",
        help="Use this project-relative compile_commands.json instead of discovery",
    ),
    prepare: bool = typer.Option(
        False,
        "--prepare",
        help="Generate a canonical CMake/qmake database only when none is selected",
    ),
    output: str = typer.Option(
        "-",
        "--output",
        "-o",
        help="Write JSON to this path, or - for stdout",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the deterministic JSON output"),
) -> None:
    """Export a minimal, redacted compilation context without running verification."""

    root = Path.cwd().resolve()
    try:
        config = config_with_database(_effective_config(ctx, root), root, database)
        project, compilation = load_export_context(root, config, prepare=prepare)
        encoded = render_compilation_export(
            compilation_export_payload(project, compilation),
            pretty=pretty,
        )
        database_path = compilation.database_path
        if database_path is None:
            raise CompilationExportError(
                "compilation database path became unavailable", exit_code=2
            )
        output_path = validate_export_output(root, output, database_path)
        if output_path is None:
            sys.stdout.write(encoded.decode("utf-8"))
        else:
            write_compilation_export(output_path, encoded)
    except CompilationExportError as error:
        typer.echo(f"Compilation export error: {error}", err=True)
        raise typer.Exit(code=error.exit_code) from error
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        typer.echo(f"Compilation export error: {error}", err=True)
        raise typer.Exit(code=1) from error
