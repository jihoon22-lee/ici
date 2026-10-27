"""Import existing evidence as a separately identified supporting artifact."""

import json
from pathlib import Path

import typer

from ici.application.import_report import import_report

_SOURCE = typer.Argument(..., help="Existing SARIF/JUnit/LCOV report")
_OUTPUT = typer.Option(Path(".ici/auxiliary/report.json"), "--output")


def cmd_import_report(
    source: Path = _SOURCE,
    format: str = typer.Option(..., "--format", help="sarif, junit or lcov"),
    scope: str = typer.Option(
        ..., "--scope", help="Describe the producing run's source/suite scope"
    ),
    observed_at: str | None = typer.Option(
        None, "--observed-at", help="Producer timestamp with timezone; otherwise unknown"
    ),
    output: Path = _OUTPUT,
) -> None:
    """Import supporting evidence without rerunning tools or changing a gate."""
    try:
        if output.resolve() == source.resolve() or output.exists():
            raise ValueError("output must be a new file distinct from the source")
        document = import_report(source, format, scope=scope, observed_at=observed_at)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError) as error:
        typer.echo(f"import: {error}", err=True)
        raise typer.Exit(2) from error
    typer.echo(f"Imported {len(document['records'])} record(s) into {output}; gate NOT_EVALUATED")
