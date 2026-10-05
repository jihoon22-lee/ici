"""The shared plumbing every ``ici next`` command stands on.

Workspace discovery, request/scope shaping, and the option spellings every
command shares live here so the command modules stay small enough to read.
The check-to-task bridge itself lives in ``ici.application.planning`` — the
cli package only speaks options and output.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ici.application.request import RunRequest, profile_for, requested_languages, scope_of
from ici.config.composition import EffectiveConfig
from ici.config.discovery import discover, find_workspace_root, load
from ici.config.errors import NextConfigError
from ici.domain.enums import Profile
from ici.domain.workspace import Workspace
from ici.workspace import build as build_workspace

__all__ = ["next_app"]

#: SPEC-04 section 3. Named rather than spelled out at each raise, because the
#: one that matters is the difference between 2 and everything else.
EXIT_CONFIG = 2

DEFAULT_RESULT = Path(".ici") / "next" / "result.json"
DEFAULT_PAGE = Path(".ici") / "next" / "result.html"

_COMPONENT_OPTION = typer.Option(None, "--component", help="Which component to act on; repeatable")
_PYTHON_OPTION = typer.Option(False, "--python", help="Select Python checks only")
_CPP_OPTION = typer.Option(False, "--cpp", help="Select C++ checks only")
_PROFILE_OPTION = typer.Option(
    None, "--profile", help="Cost profile for this run (does not rewrite the root's)"
)
_REQUIRE_FULL_OPTION = typer.Option(
    False,
    "--require-full",
    help="Fail unless the run covers the workspace's full required scope",
)
_RESULT_OPTION = typer.Option(DEFAULT_RESULT, "--result", "--output", help="The saved result")
_PAGE_OPTION = typer.Option(DEFAULT_PAGE, "--out", help="Where to write the page")
_NO_CACHE_OPTION = typer.Option(False, "--no-cache", help="Run without reusing stored observations")
_JSON_OPTION = typer.Option(
    False, "--json", help="Machine output on stdout; diagnostics stay on stderr"
)
_EVENTS_OPTION = typer.Option(None, "--events", help="Write the event stream here")
_BASELINE_OPTION = typer.Option(
    None, "--baseline", help="Compare this run against a stored ici.next.run result"
)
_SARIF_OPTION = typer.Option(None, "--sarif", help="Also write the result as SARIF here")
_BASELINE_ARG = typer.Argument(help="The earlier stored result")
_CONFIG_OPTION = typer.Option(None, "--config", help="Read this config file")
_LOCAL_CONFIG_OPTION = typer.Option(
    None, "--local-config", help="Personal overlay, path-allowlisted"
)
_PREVIEW_OPTION = typer.Option(False, "--preview", help="Print the file instead of writing it")
_FORCE_OPTION = typer.Option(False, "--force", help="Overwrite an existing ici.toml")

next_app = typer.Typer(
    name="next",
    help="The ici-next path. `ici verify` dispatches here when the root config declares [workspace].",
    add_completion=False,
)


def _artifact_root(cwd: Path) -> Path:
    """Where relative run artifacts live: the workspace root when one is
    discoverable above ``cwd``, else ``cwd``.

    ``verify`` resolves ``--result`` against the directory holding the root
    config — ``.ici/next/result.json`` is a workspace artifact, not a
    per-directory one. ``report`` and ``diff`` must resolve the same way, or
    a run issued from a subdirectory writes to one place and reads from
    another.
    """

    return find_workspace_root(cwd) or cwd.resolve()


def _workspace(
    cwd: Path, config_path: Path | None, local_path: Path | None
) -> tuple[Path, EffectiveConfig, Workspace]:
    """Read the configuration and the workspace model, or stop with exit 2.

    The run's root is the directory holding the discovered root file, not the
    directory the command was typed in — #210's promise that a subdirectory
    cwd does not quietly re-scope the run. A configuration that cannot be read
    — or whose model cannot be built — is not a failing verification: nothing
    ran. Every problem is printed, not just the first — the reader is going to
    edit the file either way, and one round trip per mistake is the thing
    ``NextConfigError`` exists to avoid.
    """

    try:
        found = discover(cwd, explicit=config_path)
        config = load(cwd, explicit=found.path, local=local_path)
        model = build_workspace(config)
    except NextConfigError as error:
        for problem in error.problems:
            typer.echo(f"config: {problem}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    except (OSError, ValueError) as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    return found.directory.resolve(), config, model


def _request(
    components: list[str] | None,
    python: bool,
    cpp: bool,
    profile: Profile | None,
    require_full: bool = False,
) -> RunRequest:
    """The CLI flags as one request. Both language flags are a union."""

    languages = tuple(language for language, asked in (("python", python), ("cpp", cpp)) if asked)
    return RunRequest(
        components=tuple(components or ()),
        languages=languages,
        profile=profile,
        require_full=require_full,
    )


def _profile(config: EffectiveConfig, request: RunRequest) -> Profile:
    """Resolve the run's cost profile, refusing a name the domain does not know."""

    try:
        return profile_for(config.profile.value if config.profile is not None else None, request)
    except ValueError as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error


def _scope(model: Workspace, request: RunRequest) -> Workspace:
    """The whole workspace, or the components the run was narrowed to."""

    try:
        return scope_of(model, request)
    except ValueError as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error


def _announce(
    root: Path, scope: Workspace, request: RunRequest, profile: Profile, json_mode: bool
) -> None:
    """Say which root and scope the run covers before anything happens.

    Under ``--json`` this is a diagnostic and goes to stderr — stdout belongs
    to the machine document alone.
    """

    echo = (lambda line: typer.echo(line, err=True)) if json_mode else typer.echo
    components = ",".join(item.id for item in scope.components)
    languages = ",".join(requested_languages(request, scope)) or "all"
    echo(f"root: {root}")
    echo(
        f"scope: components={components} languages={languages} "
        f"profile={profile.value}" + (" require-full" if request.require_full else "")
    )
