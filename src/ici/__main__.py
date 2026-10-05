"""Main Typer CLI Application Entrypoint for ici.

The command surface is flat: ``ici verify``, ``ici plan``, ``ici doctor`` and
the rest of the workspace commands are the ici-next path. ``ici next …``
remains registered as an explicit alias for the same commands while the
v0.11-era spelling phases out.
"""

import typer

from ici import __version__
from ici.cli import (
    next_migrate,  # noqa: F401 - registers `ici next migrate`
)
from ici.cli.next_path import next_app

app = typer.Typer(
    name="ici",
    help=f"Integrated CI Engine v{__version__} — Multi-Language CI/CD Verification Tool",
    add_completion=False,
    no_args_is_help=True,
)


def _version_callback(value: bool):
    if value:
        typer.echo(f"ici {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
):
    """Integrated CI Engine."""


# The workspace commands were implemented as ``ici next <command>`` while the
# stable CLI still owned the top level (#227). Registering the same callbacks
# directly here makes the documented spellings — ``ici verify``, ``ici plan``,
# ``ici doctor`` — the primary surface without duplicating the handlers.
for _command in next_app.registered_commands:
    if _command.callback is not None:
        app.command(_command.name)(_command.callback)

# ``ici next …`` stays reachable so v0.11-era scripts and docs keep working.
app.add_typer(next_app, name="next")


def main():
    app()


if __name__ == "__main__":
    main()
