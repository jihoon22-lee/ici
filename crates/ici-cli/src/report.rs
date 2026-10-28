//! Saved evidence only. Rendering and import never discover or launch project tools.
use crate::{
    commands::{CliError, CommandOutput, Result, artifact_root, under},
    registry::Invocation,
};
use ici_execution::storage::{self, WriteMode};
use ici_reporting::{ProjectionContext, RenderOptions, StoredResult};
use std::{
    fs,
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
};

fn error(value: impl std::fmt::Display) -> CliError {
    CliError(value.to_string())
}
pub fn read_bounded(path: &Path, maximum: usize) -> Result<Vec<u8>> {
    storage::read_bounded_regular(path, maximum).map_err(error)
}

pub fn details_path(result: &Path, run_id: &str) -> Result<PathBuf> {
    if run_id.is_empty()
        || matches!(run_id, "." | "..")
        || !run_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'.')
    {
        let digest = ici_domain::sha256(run_id);
        return Ok(result
            .parent()
            .unwrap_or(Path::new("."))
            .join("runs")
            .join(&digest[7..])
            .join("details.json"));
    }
    Ok(result
        .parent()
        .unwrap_or(Path::new("."))
        .join("runs")
        .join(run_id)
        .join("details.json"))
}
pub fn save_details(run: &crate::execute::ExecutedRun) -> Result<PathBuf> {
    let details = ici_reporting::legacy_projection(
        &run.result,
        &run.outputs,
        &ProjectionContext {
            project_name: run.project_name.clone(),
            duration_seconds: run.duration_seconds,
            raw_fingerprints: run.raw_fingerprints.clone(),
            required_checks: run.required_checks.clone(),
            engine_names: run.engine_names.clone(),
            ..Default::default()
        },
    )
    .map_err(error)?;
    let path = details_path(&run.result_path, &run.result.run_id)?;
    let text = ici_reporting::render_json(&StoredResult::Legacy(details)).map_err(error)? + "\n";
    storage::atomic_write(&path, text.as_bytes(), WriteMode::CreateNew).map_err(error)?;
    Ok(path)
}
pub fn load(path: &Path) -> Result<StoredResult> {
    let stored =
        StoredResult::from_report_bytes(&read_bounded(path, 64 * 1024 * 1024)?).map_err(error)?;
    if let StoredResult::Run(run) = &stored {
        let details = details_path(path, &run.run_id)?;
        if details.try_exists().map_err(error)? {
            let saved = StoredResult::from_bytes(&read_bounded(&details, 64 * 1024 * 1024)?)
                .map_err(error)?;
            if let StoredResult::Legacy(legacy) = &saved {
                // Full equality binds a presentation archive to this exact stored run.
                if legacy.as_value().get("native_run") == Some(&run.to_value().map_err(error)?) {
                    return Ok(saved);
                }
            }
            return Err(CliError(
                "saved report details do not match the selected run".into(),
            ));
        }
    }
    Ok(stored)
}
pub fn same_file(left: &Path, right: &Path) -> bool {
    if left == right {
        return true;
    }
    match (fs::metadata(left), fs::metadata(right)) {
        (Ok(a), Ok(b)) => a.dev() == b.dev() && a.ino() == b.ino(),
        _ => false,
    }
}
#[derive(Clone, Copy)]
pub enum ArtifactKind {
    Html,
    Sarif,
    Result,
}
pub fn artifact_mode(path: &Path, source: &Path, kind: ArtifactKind) -> Result<WriteMode> {
    if same_file(path, source) {
        return Err(CliError("report output would replace its input".into()));
    }
    let mode = if path.try_exists().map_err(error)? {
        let previous = read_bounded(path, 128 * 1024 * 1024)?;
        let owned = match kind {
            ArtifactKind::Html => std::str::from_utf8(&previous).is_ok_and(|text| {
                text.contains("id=ici-result") || text.contains("id=\"ici-result\"")
            }),
            ArtifactKind::Sarif => serde_json::from_slice::<serde_json::Value>(&previous)
                .is_ok_and(|v| v["version"] == "2.1.0" && v["runs"].is_array()),
            ArtifactKind::Result => StoredResult::from_bytes(&previous).is_ok(),
        };
        if !owned {
            return Err(CliError(format!(
                "{} already exists and is not a recognized report artifact; choose a new output",
                path.display()
            )));
        }
        WriteMode::ReplaceRegular
    } else {
        WriteMode::CreateNew
    };
    Ok(mode)
}
pub fn write_artifact(path: &Path, bytes: &[u8], source: &Path, kind: ArtifactKind) -> Result<()> {
    let mode = artifact_mode(path, source, kind)?;
    storage::atomic_write(path, bytes, mode).map_err(error)
}
pub fn command(invocation: &Invocation, cwd: &Path) -> Result<CommandOutput> {
    if !invocation.values("auxiliary").is_empty() && invocation.value("sarif").is_some() {
        return Err(CliError(
            "--auxiliary attaches supporting evidence to HTML and cannot be merged into SARIF"
                .into(),
        ));
    }
    let root = artifact_root(cwd);
    let source = under(
        &root,
        invocation.value("result").unwrap_or(crate::DEFAULT_RESULT),
    );
    let stored = load(&source)?;
    let mut options = RenderOptions {
        source_root: Some(root.to_string_lossy().into_owned()),
        ..Default::default()
    };
    for auxiliary in invocation.values("auxiliary") {
        let path = under(&root, auxiliary);
        options.auxiliary.push(
            ici_reporting::load_auxiliary(&read_bounded(&path, 32 * 1024 * 1024)?)
                .map_err(error)?,
        );
    }
    let (path, text, kind) = if let Some(path) = invocation.value("sarif") {
        (
            under(&root, path),
            ici_reporting::render_sarif(&stored, &options).map_err(error)?,
            ArtifactKind::Sarif,
        )
    } else {
        (
            under(
                &root,
                invocation.value("page").unwrap_or(crate::DEFAULT_PAGE),
            ),
            ici_reporting::render_html(&stored, &options).map_err(error)?,
            ArtifactKind::Html,
        )
    };
    write_artifact(&path, text.as_bytes(), &source, kind)?;
    Ok(CommandOutput::text(format!("wrote {}\n", path.display())))
}
pub fn import(invocation: &Invocation, cwd: &Path) -> Result<CommandOutput> {
    let root = artifact_root(cwd);
    let source = under(
        &root,
        invocation
            .value("source")
            .ok_or_else(|| CliError("source report is required".into()))?,
    );
    let provenance = ici_reporting::ImportProvenance {
        source_path: source.to_string_lossy().into_owned(),
        scope: invocation.value("scope").unwrap_or("").into(),
        imported_at: chrono::Utc::now().to_rfc3339(),
        observed_at: invocation.value("observed_at").map(str::to_owned),
    };
    let document = ici_reporting::import_report(
        invocation.value("format").unwrap_or(""),
        &read_bounded(&source, 32 * 1024 * 1024)?,
        &provenance,
        &ici_reporting::ImportOptions::default(),
    )
    .map_err(error)?;
    let target = under(
        &root,
        invocation
            .value("output")
            .unwrap_or(".ici/rust-v1/auxiliary/report.json"),
    );
    if same_file(&target, &source) {
        return Err(CliError(
            "import output must be a new file distinct from its source".into(),
        ));
    }
    storage::atomic_write(
        &target,
        (document.to_json().map_err(error)? + "\n").as_bytes(),
        WriteMode::CreateNew,
    )
    .map_err(error)?;
    Ok(CommandOutput::text(format!(
        "imported {} supporting record(s); gate not evaluated\nwrote {}\n",
        document.records.len(),
        target.display()
    )))
}
