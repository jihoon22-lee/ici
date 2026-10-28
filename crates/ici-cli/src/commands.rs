use crate::registry::Invocation;
use ici_domain::{BaselineState, canonical_json};
use ici_execution::storage::{WriteMode, atomic_write};
use std::{
    collections::BTreeMap,
    fmt::Display,
    path::{Path, PathBuf},
};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct CliError(pub String);
pub type Result<T> = std::result::Result<T, CliError>;
fn error(e: impl Display) -> CliError {
    CliError(e.to_string())
}
#[derive(Clone, Debug, Default)]
pub struct CommandOutput {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
}
impl CommandOutput {
    pub fn text(text: impl Into<String>) -> Self {
        Self {
            stdout: text.into(),
            ..Default::default()
        }
    }
    pub fn json(value: &impl serde::Serialize) -> Result<Self> {
        Ok(Self::text(canonical_json(value).map_err(error)? + "\n"))
    }
}

pub fn dispatch(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    match invocation.command().as_str() {
        "next plan" => {
            let prepared = crate::planning::prepare(invocation, cwd, environment, false)?;
            if invocation.flag("json_mode") {
                CommandOutput::json(&prepared.document())
            } else {
                Ok(CommandOutput::text(prepared.text()))
            }
        }
        "analyze" if invocation.flag("plan") => {
            let prepared = crate::planning::prepare(invocation, cwd, environment, true)?;
            if invocation.flag("json_mode") {
                CommandOutput::json(&prepared.document())
            } else {
                Ok(CommandOutput::text(prepared.text()))
            }
        }
        "analyze" => crate::execute::command(invocation, cwd, environment, true),
        "next verify" => crate::execute::command(invocation, cwd, environment, false),
        command if command == "verify" || crate::legacy::engine(command).is_some() => {
            crate::legacy_execute::command(invocation, cwd, environment)
        }
        "report" | "next report" => crate::report::command(invocation, cwd),
        "import-report" => crate::report::import(invocation, cwd),
        "setup" => crate::setup::command(invocation, cwd, environment),
        "doctor" | "next doctor" => crate::doctor::command(invocation, cwd, environment),
        "export-compilation-context" => {
            crate::compilation_command::command(invocation, cwd, environment)
        }
        "publish" | "next publish" => crate::publish::command(invocation, cwd, environment),
        "init" | "next init" => init(invocation, cwd, environment),
        "next migrate" => migrate(invocation, cwd),
        "diff" | "next diff" => diff(invocation, cwd),
        "cache" => cache(invocation, cwd),
        "env" => environment_loader(invocation),
        _ => Err(CliError(format!(
            "{}: native command integration is not yet available in this development build",
            invocation.command()
        ))),
    }
}

fn init(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    let mut proposal = ici_config::scaffold_with_environment(cwd, environment).map_err(error)?;
    if invocation.flag("python") || invocation.flag("cpp") {
        let mut languages = vec![];
        if invocation.flag("python") {
            languages.push("python");
        }
        if invocation.flag("cpp") {
            languages.push("cpp");
        }
        proposal.text.push_str(&format!(
            "languages = {}\n",
            serde_json::to_string(&languages).map_err(error)?
        ));
    }
    if invocation.flag("preview") {
        return Ok(CommandOutput::text(proposal.text));
    }
    if let Some(existing) = &proposal.existing {
        if !invocation.flag("force") {
            return Err(CliError(format!(
                "{} already exists; use --preview or an explicit migration",
                existing.display()
            )));
        }
        let preview = ici_config::migrate_preview(existing).map_err(error)?;
        if invocation.flag("python") || invocation.flag("cpp") {
            return Err(CliError("existing scope is preserved by --force; inspect `ici next migrate` before changing language declarations".into()));
        }
        let backup = replace_with_backup(
            existing,
            preview.proposed_text.as_bytes(),
            &preview.source_sha256,
            ".before-init",
        )?;
        return Ok(CommandOutput::text(format!(
            "wrote {} (existing scope preserved; original kept at {})\n",
            existing.display(),
            backup.display()
        )));
    }
    let target = ici_config::write_scaffold(&proposal, None).map_err(error)?;
    Ok(CommandOutput::text(format!(
        "wrote {} ({} component(s))\n",
        target.display(),
        proposal.components.len()
    )))
}

fn migrate(invocation: &Invocation, cwd: &Path) -> Result<CommandOutput> {
    let source = under(cwd, invocation.value("source").unwrap_or("ici.toml"));
    let preview = ici_config::migrate_preview(&source).map_err(error)?;
    let mut output = CommandOutput::text(preview.proposed_text.clone());
    for note in &preview.notes {
        output.stderr.push_str(&format!("  {note}\n"));
    }
    if let Some(target) = invocation.value("output") {
        let target = under(cwd, target);
        ici_config::write_migration(&preview, &target).map_err(error)?;
        output
            .stderr
            .push_str(&format!("wrote {}\n", target.display()));
    } else if invocation.flag("write") {
        let backup = replace_with_backup(
            &source,
            preview.proposed_text.as_bytes(),
            &preview.source_sha256,
            ".stable",
        )?;
        output.stderr.push_str(&format!(
            "original kept as {}; wrote {}\n",
            backup.display(),
            source.display()
        ));
    } else {
        output
            .stderr
            .push_str("dry run — pass --output PATH or --write to apply\n");
    }
    Ok(output)
}

fn replace_with_backup(
    source: &Path,
    bytes: &[u8],
    expected: &str,
    suffix: &str,
) -> Result<PathBuf> {
    if std::fs::symlink_metadata(source)
        .map_err(error)?
        .file_type()
        .is_symlink()
    {
        return Err(CliError(
            "refusing to replace a symlink configuration".into(),
        ));
    }
    let old = std::fs::read(source).map_err(error)?;
    if ici_domain::sha256(&old) != expected {
        return Err(CliError(
            "source changed after preview; regenerate the preview".into(),
        ));
    }
    let mut backup = source.as_os_str().to_os_string();
    backup.push(suffix);
    let backup = PathBuf::from(backup);
    atomic_write(&backup, &old, WriteMode::CreateNew).map_err(error)?;
    if ici_domain::sha256(std::fs::read(source).map_err(error)?) != expected {
        return Err(CliError(format!(
            "source changed after backup; kept original backup at {} and left current source unchanged",
            backup.display()
        )));
    }
    atomic_write(source, bytes, WriteMode::ReplaceRegular).map_err(error)?;
    Ok(backup)
}

fn diff(invocation: &Invocation, cwd: &Path) -> Result<CommandOutput> {
    let root = artifact_root(cwd);
    let old_path = under(
        &root,
        invocation
            .value("baseline")
            .ok_or_else(|| CliError("baseline is required".into()))?,
    );
    let new_path = under(
        &root,
        invocation.value("current").unwrap_or(crate::DEFAULT_RESULT),
    );
    let old = native_comparison_result(&old_path)?;
    let current = native_comparison_result(&new_path)?;
    let (Some(old), Some(current)) = (old, current) else {
        return Ok(CommandOutput{stdout:"incompatible: historical results are readable but have no native completed-check scope; original reports are unchanged\n".into(),stderr:String::new(),exit_code:2});
    };
    let delta = ici_application::baseline::compare(
        &current.findings,
        &current.identity,
        current.checks.as_deref(),
        &old,
        old_path.to_string_lossy(),
    );
    if delta.state == BaselineState::Incompatible {
        return Ok(CommandOutput {
            stdout: format!("incompatible: {}\n", delta.reason),
            exit_code: 2,
            ..Default::default()
        });
    }
    let mut text = format!(
        "{} new, {} unchanged, {} resolved, {} carried\n",
        delta.new.len(),
        delta.unchanged.len(),
        delta.resolved.len(),
        delta.carried.len()
    );
    if !delta.carried.is_empty() {
        text.push_str("  carried findings have no complete matching check in the newer run\n");
    }
    if old.gate.selected != current.gate.selected {
        text.push_str(&format!(
            "  gate: {:?} → {:?}\n",
            old.gate.selected, current.gate.selected
        ));
    }
    Ok(CommandOutput::text(text))
}
fn native_comparison_result(path: &Path) -> Result<Option<ici_domain::RunResult>> {
    match crate::report::load(path)? {
        ici_reporting::StoredResult::Run(run) => Ok(Some(*run)),
        ici_reporting::StoredResult::Legacy(legacy) => legacy
            .as_value()
            .get("native_run")
            .cloned()
            .map(ici_domain::RunResult::from_value)
            .transpose()
            .map_err(error),
    }
}

fn cache(invocation: &Invocation, cwd: &Path) -> Result<CommandOutput> {
    let directory = artifact_root(cwd).join(".ici/rust-v1/observations");
    let cache = ici_application::cache::ObservationCache {
        directory: directory.clone(),
        core_digest: String::new(),
    };
    let stats = cache.inventory(invocation.flag("clear")).map_err(error)?;
    Ok(CommandOutput::text(format!(
        "{}: {} {} native observation cache entries ({} bytes); {} unrecognized entries preserved\n",
        directory.display(),
        if invocation.flag("clear") {
            "removed"
        } else {
            "found"
        },
        stats.entries,
        stats.bytes,
        stats.unrecognized
    )))
}
fn environment_loader(invocation: &Invocation) -> Result<CommandOutput> {
    if invocation.flag("csh") && invocation.flag("sh") {
        return Err(CliError("choose one of --csh or --sh".into()));
    }
    let path = std::env::current_exe().map_err(error)?;
    let directory = path
        .parent()
        .ok_or_else(|| CliError("ici executable has no parent directory".into()))?;
    let directory = directory
        .to_str()
        .ok_or_else(|| CliError("ici installation path is not UTF-8".into()))?;
    let quoted = "'".to_owned() + &directory.replace('\'', "'\\''") + "'";
    if invocation.flag("csh") {
        Ok(CommandOutput::text(format!(
            "setenv PATH {quoted}:\"$PATH\"\n"
        )))
    } else {
        Ok(CommandOutput::text(format!(
            "export PATH={quoted}:\"$PATH\"\n"
        )))
    }
}
pub fn artifact_root(cwd: &Path) -> PathBuf {
    for directory in cwd.ancestors() {
        if directory.join("ici.toml").is_file()
            || directory.join(".git").exists()
            || directory.join("pyproject.toml").is_file()
        {
            return directory.to_owned();
        }
    }
    cwd.to_owned()
}
pub fn under(root: &Path, value: &str) -> PathBuf {
    let path = Path::new(value);
    if path.is_absolute() {
        path.to_owned()
    } else {
        root.join(path)
    }
}
