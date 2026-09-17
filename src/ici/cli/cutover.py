"""Default-path dispatch for the ici-next cutover (WP29 #227).

``ici verify`` is the stable engine's entry point. Once a project's
``ici.toml`` declares a ``[workspace]`` table, the same bare command runs the
next engine path instead — that is the transition this repository's own
specification calls "기본 실행 경로 전환": the next core becomes the default
for a workspace that opted into it, while a checkout whose configuration is
still legacy keeps the stable engines untouched.

The dispatch is deliberately small. Options whose meaning is shared are
translated onto ``ici next verify`` (``--profile``, ``--no-cache``,
``--baseline``); output options are composed from the stored result
(``--report``, ``--html``, ``--sarif``, ``--open``, ``--publish``). Options
that exist only on the stable console surface — display grouping, the v3
baseline formats — are rejected with their next-path equivalent rather than
silently ignored, because a flag the user typed that produced nothing is a
worse surprise than an exit 2 that names what replaced it.
"""

import webbrowser
from pathlib import Path

import typer

from ici.cli.next_common import DEFAULT_RESULT
from ici.cli.next_path import cmd_publish, cmd_report, cmd_verify
from ici.core.pipeline import AnalysisProfile
from ici.domain.enums import Profile
from ici.reporters.issue_view import DEFAULT_MAX_FINDINGS, ConsoleGroupBy

EXIT_CONFIG = 2

# Stable-only options and what replaces them on the next path. ``None`` means
# there is no equivalent — the answer is to keep the stable invocation, which
# requires a legacy configuration.
_STABLE_ONLY: tuple[tuple[str, str | None], ...] = (
    ("--fail-on-new", "`ici next verify --baseline <run>` compares; gate on `ici next diff`"),
    ("--write-baseline", "save a run (`--result`) and pass it back as `--baseline`"),
    ("--github-summary", "`ici next publish` records publication on its own axis"),
    ("--verbose", None),
    ("--max-findings", None),
    ("--group-by", None),
)


def verify(
    *,
    report: bool,
    html: str | None,
    sarif: str | None,
    open_browser: bool,
    github_summary: bool,
    publish: bool,
    baseline: str | None,
    fail_on_new: bool,
    write_baseline: str | None,
    verbose: bool,
    max_findings: int,
    group_by: ConsoleGroupBy,
    profile: AnalysisProfile | None,
    no_cache: bool,
) -> None:
    """Run ``ici verify`` on the next engine path.

    Reached only when a ``[workspace]`` root was found — the caller has
    already decided the dispatch; this function owns the flag translation.
    """

    used = {
        "--fail-on-new": fail_on_new,
        "--write-baseline": write_baseline is not None,
        "--github-summary": github_summary,
        "--verbose": verbose,
        "--max-findings": max_findings != DEFAULT_MAX_FINDINGS,
        "--group-by": group_by != ConsoleGroupBy.ENGINE,
    }
    unsupported = [flag for flag, _ in _STABLE_ONLY if used[flag]]
    if unsupported:
        typer.echo(
            "this workspace declares a [workspace] config, so `ici verify` runs "
            "the next engine path — and these stable-only options do not apply:",
            err=True,
        )
        for flag in unsupported:
            replacement = dict(_STABLE_ONLY)[flag]
            hint = f" — use {replacement}" if replacement else ""
            typer.echo(f"  {flag}{hint}", err=True)
        raise typer.Exit(EXIT_CONFIG)

    typer.echo(
        "ici-next: [workspace] config found — running the next engine path "
        "(`ici next verify` is the same run)",
        err=True,
    )
    result = Path("verify_report.json") if report else DEFAULT_RESULT
    exit_code = 0
    try:
        cmd_verify(
            component=[],
            python=False,
            cpp=False,
            profile=Profile(profile.value) if profile else None,
            require_full=False,
            result=result,
            no_cache=no_cache,
            json_mode=False,
            events=None,
            baseline=Path(baseline) if baseline else None,
            config_path=None,
            local_config=None,
        )
    except typer.Exit as exit_:
        exit_code = exit_.exit_code

    # Output options are composition on the stored result, not re-analysis —
    # the same thing `ici next report` and `ici next publish` do when typed.
    if sarif is not None:
        cmd_report(result=result, page=Path(".ici/next/index.html"), sarif=Path(sarif))
    if html is not None or open_browser or publish:
        page = Path(html) if html is not None else Path(".ici/next/index.html")
        cmd_report(result=result, page=page, sarif=None)
        if open_browser:
            webbrowser.open(page.resolve().as_uri())
        if publish:
            cmd_publish(config_path=None, local_config=None, result=result, page=page)
    raise typer.Exit(exit_code)
