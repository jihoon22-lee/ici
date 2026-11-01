//! Frozen `cli/cutover.py`: `ici verify` on a root whose `ici.toml` declares `[workspace]` runs
//! the next path (`ici next verify`) and composes its output options from the stored result.
use crate::{
    commands::{CliError, CommandOutput, Result},
    registry::{self, Invocation},
};
use std::{collections::BTreeMap, path::Path};

const EXIT_CONFIG: i32 = 2;

/// Stable-only options and their next-path replacement (frozen `_STABLE_ONLY`, same order).
const STABLE_ONLY: [(&str, Option<&str>); 6] = [
    (
        "--fail-on-new",
        Some("`ici next verify --baseline <run>` compares; gate on `ici next diff`"),
    ),
    (
        "--write-baseline",
        Some("save a run (`--result`) and pass it back as `--baseline`"),
    ),
    (
        "--github-summary",
        Some("`ici next publish` records publication on its own axis"),
    ),
    ("--verbose", None),
    ("--max-findings", None),
    ("--group-by", None),
];

/// Frozen `find_workspace_root(cwd) is not None`: the resolved root config is a next workspace.
pub fn applies(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> bool {
    invocation.command() == "verify"
        && ici_config::resolve_with_environment(cwd, None, None, environment)
            .is_ok_and(|project| project.schema_family == ici_config::SchemaFamily::NextV1)
}

fn parse(argv: &[&str]) -> Result<Invocation> {
    registry::parse(argv.iter().copied()).map_err(|error| CliError(error.to_string()))
}

fn path_text(path: &Path) -> Result<&str> {
    path.to_str()
        .ok_or_else(|| CliError(format!("{} is not UTF-8", path.display())))
}

pub fn verify(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    let used = [
        invocation.flag("fail_on_new"),
        invocation.value("write_baseline").is_some(),
        invocation.flag("github_summary"),
        invocation.flag("verbose"),
        invocation
            .value("max_findings")
            .is_some_and(|value| value.parse::<i64>() != Ok(5)),
        invocation
            .value("group_by")
            .is_some_and(|value| value != "engine"),
    ];
    if used.iter().any(|flag| *flag) {
        let mut stderr = String::from(
            "this workspace declares a [workspace] config, so `ici verify` runs the next engine path — and these stable-only options do not apply:\n",
        );
        for ((flag, replacement), _) in STABLE_ONLY.iter().zip(used).filter(|(_, used)| *used) {
            stderr.push_str(&match replacement {
                Some(replacement) => format!("  {flag} — use {replacement}\n"),
                None => format!("  {flag}\n"),
            });
        }
        return Ok(CommandOutput {
            stdout: String::new(),
            stderr,
            exit_code: EXIT_CONFIG,
        });
    }
    let mut stderr = String::from(
        "ici-next: [workspace] config found — running the next engine path (`ici next verify` is the same run)\n",
    );
    let result = if invocation.flag("report") {
        "verify_report.json"
    } else {
        crate::DEFAULT_RESULT
    };
    let mut argv = vec!["ici", "next", "verify", "--result", result];
    if let Some(profile) = invocation.value("profile") {
        argv.extend(["--profile", profile]);
    }
    if invocation.flag("no_cache") {
        argv.push("--no-cache");
    }
    if let Some(baseline) = invocation.value("baseline") {
        argv.extend(["--baseline", baseline]);
    }
    let verified = crate::execute::command(&parse(&argv)?, cwd, environment, false)?;
    let mut stdout = verified.stdout;
    stderr.push_str(&verified.stderr);
    let mut exit_code = verified.exit_code;
    // Output options compose the stored result; a configuration refusal (exit 2) has none.
    if exit_code != EXIT_CONFIG {
        if let Some(sarif) = invocation.value("sarif") {
            let report = parse(&[
                "ici", "next", "report", "--result", result, "--sarif", sarif,
            ])?;
            stdout.push_str(&crate::report::command(&report, cwd)?.stdout);
        }
        if invocation.value("html").is_some()
            || invocation.flag("open_browser")
            || invocation.flag("publish")
        {
            let page = invocation.value("html").unwrap_or(crate::DEFAULT_PAGE);
            let report = parse(&["ici", "next", "report", "--result", result, "--out", page])?;
            stdout.push_str(&crate::report::command(&report, cwd)?.stdout);
            let root = crate::commands::artifact_root(cwd);
            if invocation.flag("open_browser") {
                let target = crate::commands::under(&root, page);
                if let Err(reason) = crate::legacy_execute::open_page(&target, &root, environment) {
                    stderr.push_str(&format!("browser: {reason}\n"));
                }
            }
            if invocation.flag("publish") {
                let target = crate::commands::under(&root, page);
                let publish = parse(&[
                    "ici",
                    "next",
                    "publish",
                    "--result",
                    result,
                    "--out",
                    path_text(&target)?,
                ])?;
                // Frozen: publish's own non-zero exit ends the command with that code.
                match crate::publish::command(&publish, cwd, environment) {
                    Ok(published) => {
                        stdout.push_str(&published.stdout);
                        stderr.push_str(&published.stderr);
                        if published.exit_code != 0 {
                            exit_code = published.exit_code;
                        }
                    }
                    Err(reason) => {
                        stderr.push_str(&format!("publish: {reason}\n"));
                        exit_code = EXIT_CONFIG;
                    }
                }
            }
        }
    }
    Ok(CommandOutput {
        stdout,
        stderr,
        exit_code,
    })
}
