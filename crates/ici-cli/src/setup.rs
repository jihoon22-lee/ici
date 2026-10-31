//! Installation is an explicit command, separate from every analysis and report path.
use crate::{
    commands::{CliError, CommandOutput, Result, under},
    registry::Invocation,
};
use std::{collections::BTreeMap, io::Write, path::Path, time::Duration};

fn error(value: impl std::fmt::Display) -> CliError {
    CliError(value.to_string())
}
pub fn command(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    let mut modes = usize::from(invocation.value("archive").is_some())
        + usize::from(invocation.value("url").is_some())
        + usize::from(invocation.value("activate").is_some());
    modes += usize::from(invocation.flag("rollback")) + usize::from(invocation.flag("inspect"));
    if modes != 1 {
        return Err(CliError(
            "setup requires exactly one of --archive, --url, --activate, --rollback or --inspect"
                .into(),
        ));
    }
    let downloading = invocation.value("url").is_some();
    let installing = downloading || invocation.value("archive").is_some();
    if installing != invocation.value("sha256").is_some() {
        return Err(CliError("--archive/--url requires --sha256; selection/inspection does not take an archive digest".into()));
    }
    let prefix = if let Some(prefix) = invocation.value("prefix") {
        under(cwd, prefix)
    } else {
        let (key, suffix) = if environment
            .get("XDG_DATA_HOME")
            .is_some_and(|value| !value.is_empty())
        {
            ("XDG_DATA_HOME", "ici/native")
        } else {
            ("HOME", ".local/share/ici/native")
        };
        let home = environment
            .get(key)
            .filter(|home| !home.is_empty())
            .ok_or_else(|| {
                CliError("HOME and XDG_DATA_HOME are unavailable; pass an explicit --prefix".into())
            })?;
        if !Path::new(home).is_absolute() {
            return Err(CliError(format!(
                "{key} must be absolute; pass an explicit --prefix"
            )));
        }
        Path::new(home).join(suffix)
    };
    let digest = invocation
        .value("sha256")
        .map(|digest| digest.strip_prefix("sha256:").unwrap_or(digest));
    if let Some(digest) = digest
        && (digest.len() != 64
            || !digest
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)))
    {
        return Err(CliError(
            "archive SHA-256 must contain 64 lowercase hexadecimal characters".into(),
        ));
    }
    let installation = if let Some(archive) = invocation.value("archive") {
        ici_distribution::install(
            &under(cwd, archive),
            digest.expect("install digest"),
            &prefix,
        )
        .map_err(error)?
    } else if let Some(url) = invocation.value("url") {
        let bytes = ici_distribution::download(
            url,
            digest.expect("install digest"),
            256 * 1024 * 1024,
            Duration::from_secs(120),
        )
        .map_err(error)?;
        let mut archive = tempfile::NamedTempFile::new().map_err(error)?;
        archive.write_all(&bytes).map_err(error)?;
        archive.as_file().sync_all().map_err(error)?;
        ici_distribution::install(archive.path(), digest.expect("install digest"), &prefix)
            .map_err(error)?
    } else if let Some(version) = invocation.value("activate") {
        ici_distribution::activate(&prefix, version).map_err(error)?
    } else if invocation.flag("rollback") {
        ici_distribution::rollback(&prefix).map_err(error)?
    } else {
        ici_distribution::inspect_installation(&prefix).map_err(error)?
    };
    if invocation.flag("json_mode") {
        return CommandOutput::json(&installation);
    }
    let mut text = format!(
        "ici {} selected\nExecutable: {}\n",
        installation.manifest.version,
        installation.executable.display()
    );
    for guidance in &installation.guidance {
        text.push_str(&format!("{guidance}\n"));
    }
    for collision in &installation.path_collisions {
        text.push_str(&format!("Existing PATH entry: {}\n", collision.display()));
    }
    Ok(CommandOutput::text(text))
}
