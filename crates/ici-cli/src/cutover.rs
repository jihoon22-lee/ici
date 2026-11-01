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

const WORKSPACE_REFUSAL: &str = "Configuration error: this workspace declares a [workspace] (ici-next) config, which stable subcommands cannot read — use `ici next --help` for the commands that can\n";

/// Frozen `__main__.main_callback` `find_workspace_root(Path.cwd()) is not None`.
fn workspace(cwd: &Path) -> bool {
    ici_config::find_workspace_root(cwd).is_some()
}
pub fn applies(invocation: &Invocation, cwd: &Path) -> bool {
    invocation.command() == "verify" && workspace(cwd)
}
/// The subcommands frozen `main_callback` loads the stable configuration for: every command
/// except the next path (`next`, `analyze`, `report`, `diff`, `init`, `import-report`), the
/// read-only `env`/`cache`, and `verify` on a `[workspace]` root (the cutover). Native-only
/// commands (`setup`) are not frozen subcommands and read no project configuration.
pub fn stable(command: &str) -> bool {
    matches!(
        command,
        "verify" | "doctor" | "publish" | "export-compilation-context"
    ) || crate::legacy::engine(command).is_some()
}
/// Frozen historical commands read `load_config(base=cwd)` and root at the working directory
/// (`collect_diagnostics(Path.cwd())`, `compilation_export_cli` `root = Path.cwd()`, publish
/// paths relative to cwd); every other command discovers its configuration upwards.
pub fn inspect(
    invocation: &Invocation,
    cwd: &Path,
    explicit: Option<&Path>,
    local: Option<&Path>,
    environment: &BTreeMap<String, String>,
) -> std::result::Result<ici_config::ConfigurationInspection, ici_config::ConfigError> {
    if stable(&invocation.command()) {
        ici_config::inspect_legacy_command_with_environment(cwd, explicit, local, environment)
    } else {
        ici_config::inspect_configuration_with_environment(cwd, explicit, local, environment)
    }
}
/// [`inspect`] for the resolved project.
pub fn resolve(
    invocation: &Invocation,
    cwd: &Path,
    explicit: Option<&Path>,
    local: Option<&Path>,
    environment: &BTreeMap<String, String>,
) -> std::result::Result<ici_config::ResolvedProject, ici_config::ConfigError> {
    if stable(&invocation.command()) {
        ici_config::resolve_legacy_command_with_environment(cwd, explicit, local, environment)
    } else {
        ici_config::resolve_with_environment(cwd, explicit, local, environment)
    }
}
/// Frozen `main_callback`: the stable configuration is read from the working directory before
/// the subcommand runs, and a configuration error stops it with exit 2 — named as the
/// `[workspace]` refusal when a next workspace root is found at or above the working directory,
/// else `Configuration error: <problem>`. Nothing runs and nothing is written.
///
/// `Ok(Some)` is the `[workspace]` refusal, `Err` a classed `Configuration error: ` refusal
/// (printed as is by `commands::diagnostic`), `Ok(None)` lets the subcommand run.
pub fn stable_refusal(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<Option<CommandOutput>> {
    let command = invocation.command();
    if !stable(&command) {
        return Ok(None);
    }
    let next_root = workspace(cwd);
    if next_root && command == "verify" {
        return Ok(None);
    }
    let problem =
        match ici_config::inspect_legacy_command_with_environment(cwd, None, None, environment) {
            Err(error) => error.to_string(),
            // The stable reader rejects every next-v1 key; native reads its own v2 schema everywhere.
            Ok(inspection) if inspection.schema_family == ici_config::SchemaFamily::NextV1 => {
                format!(
                    "{}: a schema_version = 1 ici-next file is not a stable configuration",
                    inspection.config_source.as_deref().unwrap_or(cwd).display()
                )
            }
            Ok(_) => return Ok(None),
        };
    if !next_root {
        return Err(crate::commands::classed(
            crate::commands::STABLE_CONFIG_PREFIX,
            ici_execution::redaction::redact_text(&problem),
        ));
    }
    Ok(Some(CommandOutput {
        stdout: String::new(),
        stderr: WORKSPACE_REFUSAL.into(),
        exit_code: EXIT_CONFIG,
    }))
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
    let verified = match crate::execute::command(&parse(&argv)?, cwd, environment, false) {
        Ok(verified) => verified,
        // Frozen prints the cutover line first, then the next path's own refusal.
        Err(error) => {
            stderr.push_str(&crate::commands::diagnostic(&error));
            return Ok(CommandOutput {
                stdout: String::new(),
                stderr,
                exit_code: EXIT_CONFIG,
            });
        }
    };
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
                        stderr.push_str(&crate::publish::refusal_line(&reason));
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
