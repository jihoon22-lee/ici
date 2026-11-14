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

fn run_component(run_id: &str) -> String {
    if run_id.is_empty()
        || matches!(run_id, "." | "..")
        || !run_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'.')
    {
        ici_domain::sha256(run_id)[7..].to_string()
    } else {
        run_id.to_string()
    }
}
/// Presentation archive of one saved run. It lives in the project's own `.ici/rust-v1`
/// namespace, keyed by the result file name and run, so an explicit `--result` never gains a
/// `.ici` (or `runs/`) beside it: frozen wrote only the result there
/// (tests/rust-parity/runtime/result-placement-oracle.json).
pub fn details_path(root: &Path, result: &Path, run_id: &str) -> Result<PathBuf> {
    let name = result
        .file_name()
        .ok_or_else(|| CliError("result path has no file name".into()))?;
    Ok(root
        .join(".ici/rust-v1/details")
        .join(name)
        .join(run_component(run_id))
        .join("details.json"))
}
/// Placements written by earlier native builds; read only, nothing is moved.
fn previous_details_paths(result: &Path, run_id: &str) -> [PathBuf; 2] {
    let parent = result.parent().unwrap_or(Path::new("."));
    let namespace = if parent.ends_with(".ici/rust-v1") {
        parent.to_path_buf()
    } else {
        parent.join(".ici/rust-v1")
    };
    let name = result.file_name().unwrap_or_default();
    [
        // Beside the result (2026-10-29 to 2026-11-02).
        namespace
            .join("details")
            .join(name)
            .join(run_component(run_id))
            .join("details.json"),
        // `<result dir>/runs/<run>/details.json` (before 2026-10-29).
        parent
            .join("runs")
            .join(run_component(run_id))
            .join("details.json"),
    ]
}
pub fn save_details(root: &Path, run: &crate::execute::ExecutedRun) -> Result<PathBuf> {
    let details = ici_reporting::legacy_projection(
        &run.result,
        &run.outputs,
        &ProjectionContext {
            project_name: run.project_name.clone(),
            duration_seconds: run.duration_seconds,
            raw_fingerprints: run.raw_fingerprints.clone(),
            cache_keys: run.cache_keys.clone(),
            required_checks: run.required_checks.clone(),
            engine_names: run.engine_names.clone(),
            legacy_baseline: run.legacy_baseline.clone(),
            ..Default::default()
        },
    )
    .map_err(error)?;
    let path = details_path(root, &run.result_path, &run.result.run_id)?;
    let text = ici_reporting::render_json(&StoredResult::Legacy(details)).map_err(error)? + "\n";
    storage::atomic_write(&path, text.as_bytes(), WriteMode::CreateNew).map_err(error)?;
    Ok(path)
}
/// Read a stored result and, for a native run, its presentation details from the project
/// `root` (or an earlier placement beside the result).
pub fn load(root: &Path, path: &Path) -> Result<StoredResult> {
    load_bytes(root, path, &read_bounded(path, 64 * 1024 * 1024)?).map_err(LoadFailure::into_error)
}
/// Why a stored result could not be read: an integer the reader cannot keep (root ruling
/// 2026-11-12, reader facet: always refused) or any other unreadable input.
#[derive(Debug)]
pub enum LoadFailure {
    Range(CliError),
    Other(CliError),
}
impl LoadFailure {
    pub fn into_error(self) -> CliError {
        match self {
            Self::Range(error) | Self::Other(error) => error,
        }
    }
}
/// [`load`] over the result `bytes` already read from `path`, so the checked bytes are the parsed
/// ones. A native run's presentation details are read once here and pass the same range check.
pub fn load_bytes(
    root: &Path,
    path: &Path,
    bytes: &[u8],
) -> std::result::Result<StoredResult, LoadFailure> {
    let other = |failure: CliError| LoadFailure::Other(failure);
    let checked = |bytes: &[u8]| {
        ici_reporting::check_stored_integers(bytes)
            .map_err(|failure| LoadFailure::Range(error(failure)))
    };
    checked(bytes)?;
    let stored = StoredResult::from_report_bytes(bytes).map_err(|failure| other(error(failure)))?;
    if let StoredResult::Run(run) = &stored {
        let mut details = details_path(root, path, &run.run_id).map_err(other)?;
        if !details
            .try_exists()
            .map_err(|failure| other(error(failure)))?
        {
            // Results saved with an earlier placement stay readable; nothing is moved.
            for previous in previous_details_paths(path, &run.run_id) {
                if previous
                    .try_exists()
                    .map_err(|failure| other(error(failure)))?
                {
                    details = previous;
                    break;
                }
            }
        }
        if details
            .try_exists()
            .map_err(|failure| other(error(failure)))?
        {
            let saved_bytes = read_bounded(&details, 64 * 1024 * 1024).map_err(other)?;
            checked(&saved_bytes)?;
            let saved =
                StoredResult::from_bytes(&saved_bytes).map_err(|failure| other(error(failure)))?;
            if let StoredResult::Legacy(legacy) = &saved {
                // Full equality binds a presentation archive to this exact stored run.
                if legacy.as_value().get("native_run")
                    == Some(&run.to_value().map_err(|failure| other(error(failure)))?)
                {
                    return Ok(saved);
                }
            }
            return Err(other(CliError(
                "saved report details do not match the selected run".into(),
            )));
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
            "report: --auxiliary attaches supporting evidence to HTML and cannot be merged into SARIF"
                .into(),
        ));
    }
    let root = artifact_root(cwd);
    let source = under(
        &root,
        invocation.value("result").unwrap_or(crate::DEFAULT_RESULT),
    );
    // Frozen `cmd_report`: a missing or unreadable stored result is a `report: ` refusal.
    if !source.is_file() {
        return Err(CliError(format!(
            "report: no result at {}; run `ici next verify` first",
            source.display()
        )));
    }
    let stored = load(&root, &source).map_err(|e| {
        CliError(format!(
            "report: {} is not a result this version can read: {e}",
            source.display()
        ))
    })?;
    let mut options = RenderOptions {
        source_root: Some(root.to_string_lossy().into_owned()),
        ..Default::default()
    };
    for auxiliary in invocation.values("auxiliary") {
        let path = under(&root, auxiliary);
        // Frozen `cmd_report`: an unreadable or invalid supporting document is a `report: `
        // refusal (exit 2) before any page is written.
        let bytes = read_bounded(&path, 32 * 1024 * 1024).map_err(|e| {
            CliError(format!(
                "report: cannot read supporting evidence {}: {e}",
                path.display()
            ))
        })?;
        options
            .auxiliary
            .push(ici_reporting::load_auxiliary(&bytes).map_err(|e| {
                CliError(format!(
                    "report: {} is not ici.auxiliary v1 supporting evidence: {e}",
                    path.display()
                ))
            })?);
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
