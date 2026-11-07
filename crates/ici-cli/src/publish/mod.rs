//! Stored publication is a separate outcome; it never rewrites the analysis verdict.
pub mod api;
pub mod legacy_comment;
use crate::{
    commands::{CliError, CommandOutput, Result, under},
    registry::Invocation,
};
use api::{Client, Target};
use ici_distribution::transport::{Request, Response, TransportError};
use ici_reporting::{RenderOptions, StoredResult};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

pub struct Page {
    pub label: String,
    pub bytes: Vec<u8>,
    pub result: Option<StoredResult>,
    pub result_digest: Option<String>,
}
pub struct Publication {
    pub destination: Target,
    pub comments: Option<Target>,
    pub pages: Vec<Page>,
    pub head: String,
    pub pr: Option<u64>,
    pub run: u64,
    pub attempt: u64,
    pub reference: String,
    pub marker: String,
    pub run_url: Option<String>,
    /// Historical `ici publish` posts the frozen sticky comment layout instead of the native one.
    pub legacy: Option<LegacyComment>,
}
pub struct LegacyComment {
    /// The one report came from `--html/--json` (frozen's unlabeled single-report comment).
    pub unlabeled: bool,
    /// Frozen `hub` when reports go to a separate publication repository, otherwise `self`.
    pub mode: String,
    /// `--report-dir` labels whose `verify_report.html` is absent: frozen `_publish_multi`
    /// uploads the others and reports these as not uploaded instead of refusing the command.
    pub missing: Vec<String>,
    /// Frozen `_resolve_target` hub prefix (`project_name` or the analyzed repository's name,
    /// `/` -> `-`); empty in self mode.
    pub prefix: String,
    /// `GITHUB_REF_NAME` as frozen read it (`main`/`master` -> `main/`, else `branch/<ref>/`).
    pub reference: String,
    /// `GITHUB_SHA`: frozen `_ensure_branch` starts a missing self-mode branch from it.
    pub github_sha: String,
}
pub fn publish_with_transport<
    F: FnMut(&Request) -> std::result::Result<Response, TransportError>,
>(
    publication: &Publication,
    send: &mut F,
) -> Value {
    let mut record = json!({"schema_id":"ici.next.publish","schema_version":1,"state":"FAILED","detail":"publication did not complete","reports":[],"head":publication.head,"pr":publication.pr,"run":format!("{}.{}",publication.run,publication.attempt)});
    let result = match &publication.legacy {
        Some(legacy) => perform_legacy(publication, legacy, send, &mut record),
        None => perform(publication, send, &mut record),
    };
    match result {
        Ok(detail) => {
            record["state"] = json!("SUCCESS");
            record["detail"] = json!(detail);
        }
        Err(reason) => record["detail"] = json!(reason),
    }
    ici_execution::redaction::redact_value(&record)
}
/// Where a page lands on the publication branch.
fn remote_path(publication: &Publication, label: &str) -> String {
    match &publication.legacy {
        Some(legacy) => legacy_path(legacy, publication.pr, label),
        None => next_path(publication, label),
    }
}
/// Frozen `application/publish._remote_path`: `ici/<label>/pr-<n>/<sha12>/index.html`, or
/// `ici/<label>/<ref with / -> ->/<sha12>/index.html` (`local` without a ref, `unversioned`
/// without a head) — the head in the path keeps a stale run from overwriting a newer one.
fn next_path(publication: &Publication, label: &str) -> String {
    let head = if publication.head.is_empty() {
        "unversioned"
    } else {
        &publication.head
    };
    let revision: String = head.chars().take(12).collect();
    let reference = publication.pr.map_or_else(
        || {
            let reference = publication.reference.replace('/', "-");
            if reference.is_empty() {
                "local".to_owned()
            } else {
                reference
            }
        },
        |pr| format!("pr-{pr}"),
    );
    format!(
        "ici/{}/{reference}/{revision}/index.html",
        next_label(label)
    )
}
/// Frozen `application/publish._label`: runs outside `[A-Za-z0-9_.-]` become one `-`, then
/// leading/trailing `-` are stripped and the result lowercased (`workspace` when empty).
fn next_label(value: &str) -> String {
    let mut label = String::new();
    let mut run = false;
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '-') {
            label.push(ch);
            run = false;
        } else if !run {
            label.push('-');
            run = true;
        }
    }
    let label = label.trim_matches('-').to_lowercase();
    if label.is_empty() {
        "workspace".into()
    } else {
        label
    }
}
/// Frozen `engines/publish._resolve_target`: `[<hub prefix>/][<label>/]<sub path>` where the sub
/// path is `pr/<n>/index.html`, `main/index.html` for `main`/`master`,
/// `branch/<ref with / -> ->/index.html`, or `index.html`. These stable URLs (a PR's report, the
/// latest main report) are what existing gh-pages consumers link to.
fn legacy_path(legacy: &LegacyComment, pr: Option<u64>, label: &str) -> String {
    let sub = match (pr, legacy.reference.as_str()) {
        (Some(pr), _) => format!("pr/{pr}/index.html"),
        (None, "main" | "master") => "main/index.html".into(),
        (None, "") => "index.html".into(),
        (None, reference) => format!("branch/{}/index.html", reference.replace('/', "-")),
    };
    let label = if legacy.unlabeled { "" } else { label };
    [legacy.prefix.as_str(), label, sub.as_str()]
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("/")
}
/// Frozen viewer URL: the page's directory on the Pages site (`<site>/<dirname>/`). Frozen
/// produced `<site>/index.html/` for a page at the branch root; native names the site root.
fn legacy_viewer(site: &str, path: &str) -> String {
    let site = site.trim_end_matches('/');
    match path.rsplit_once('/') {
        Some((directory, _)) => format!("{site}/{directory}/"),
        None => format!("{site}/"),
    }
}
/// Frozen `cmd_publish` output: `[publish] <message>` and the PR comment line.
fn legacy_output(message: String, comment: Option<&str>, exit_code: i32) -> CommandOutput {
    let mut stdout = format!("[publish] {message}\n");
    if let Some(url) = comment {
        stdout.push_str(&format!("[publish] PR comment: {url}\n"));
    }
    CommandOutput {
        stdout: ici_execution::redaction::redact_text(&stdout),
        stderr: String::new(),
        exit_code,
    }
}
/// Frozen `_publish_single`/`_publish_multi` messages over the native publication record.
/// A failure before any upload (the branch could not be prepared) reads as an upload failure,
/// as frozen's unready branch does; the record keeps the native detail.
fn legacy_message(publication: &Publication, record: &Value, repo: &str) -> (String, bool) {
    let branch = &publication.destination.branch;
    let rows = record["reports"].as_array().cloned().unwrap_or_default();
    let uploaded = |label: &str| {
        rows.iter()
            .any(|row| row["label"] == label && row["uploaded"] == true)
    };
    let failed: Vec<&str> = publication
        .pages
        .iter()
        .map(|page| page.label.as_str())
        .filter(|label| !uploaded(label))
        .collect();
    let success = record["state"] == "SUCCESS";
    let single = publication
        .legacy
        .as_ref()
        .is_some_and(|legacy| legacy.unlabeled);
    if single {
        let path = remote_path(publication, &publication.pages[0].label);
        return if !failed.is_empty() {
            (
                format!(
                    "--publish failed to upload {path} to {repo} ({branch}). Check token permissions (contents: write) and branch availability."
                ),
                false,
            )
        } else if !success {
            (
                format!(
                    "--publish uploaded {path}, but failed to update the PR comment. Check token permissions (pull-requests: write)."
                ),
                false,
            )
        } else if let Some(url) = record["viewer_url"].as_str() {
            (format!("HTML report published: {url}"), true)
        } else {
            (
                format!(
                    "HTML report pushed to {repo}:{branch}/{path}. Pages is not enabled — enable it once: Settings → Pages → Source: gh-pages."
                ),
                true,
            )
        };
    }
    if !failed.is_empty() {
        (
            format!("--publish failed to upload: {}", failed.join(", ")),
            false,
        )
    } else if !success {
        (
            "--publish uploaded every report, but failed to update the PR comment. Check token permissions (pull-requests: write).".into(),
            false,
        )
    } else {
        (
            format!(
                "Published {} report(s) to {repo}:{branch}",
                publication.pages.len()
            ),
            true,
        )
    }
}
/// Bounds and identities checked before any request.
fn validate(publication: &Publication) -> std::result::Result<Vec<String>, String> {
    publication.destination.validate()?;
    if let Some(target) = &publication.comments {
        target.validate()?;
    }
    if !publication.head.is_empty() && !(7..=64).contains(&publication.head.len())
        || !publication
            .head
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("publication head must be a hexadecimal commit identity".into());
    }
    if publication.pages.is_empty() || publication.pages.len() > 256 {
        return Err("publication needs 1..256 reports".into());
    }
    let paths: Vec<String> = publication
        .pages
        .iter()
        .map(|page| remote_path(publication, &page.label))
        .collect();
    // Two reports on one remote path would silently overwrite each other.
    if paths.iter().collect::<BTreeSet<_>>().len() != paths.len() {
        return Err("report labels collide after path normalization".into());
    }
    if publication
        .pages
        .iter()
        .any(|page| page.bytes.len() > 64 * 1024 * 1024)
    {
        return Err("report exceeds 64 MiB publication bound".into());
    }
    if publication
        .pages
        .iter()
        .map(|page| page.bytes.len() as u64)
        .sum::<u64>()
        > 512 * 1024 * 1024
    {
        return Err("publication reports exceed 512 MiB aggregate bound".into());
    }
    Ok(paths)
}
fn report_row(
    page: &Page,
    path: &str,
    viewer: Option<&str>,
    uploaded: &std::result::Result<(), String>,
) -> Value {
    json!({"label":page.label,"remote_path":path,"viewer_url":viewer,"uploaded":uploaded.is_ok(),"detail":uploaded.as_ref().err(),"artifact":{"sha256":ici_domain::sha256(&page.bytes),"bytes":page.bytes.len()},"result_sha256":page.result_digest})
}
fn summarize_first(record: &mut Value) {
    if let Some(first) = record["reports"]
        .as_array()
        .and_then(|rows| rows.first())
        .cloned()
    {
        for key in ["remote_path", "viewer_url", "artifact", "result_sha256"] {
            record[key] = first[key].clone();
        }
    }
}
fn perform<F: FnMut(&Request) -> std::result::Result<Response, TransportError>>(
    publication: &Publication,
    send: &mut F,
    record: &mut Value,
) -> std::result::Result<String, String> {
    let paths = validate(publication)?;
    let mut client = Client {
        target: &publication.destination,
        send,
    };
    // A hub repository does not contain the analyzed repository's commit objects.
    let base = if publication
        .comments
        .as_ref()
        .is_some_and(|target| target.repo != publication.destination.repo)
    {
        ""
    } else {
        &publication.head
    };
    client.ensure_branch(base)?;
    let site = client.pages()?;
    let mut summaries = vec![];
    let mut errors = vec![];
    for (page, path) in publication.pages.iter().zip(&paths) {
        let uploaded = client.put(path, &page.bytes);
        let viewer = site
            .as_ref()
            .map(|site| format!("{}/{path}", site.trim_end_matches('/')));
        record["reports"]
            .as_array_mut()
            .expect("report array")
            .push(report_row(page, path, viewer.as_deref(), &uploaded));
        if let Err(reason) = uploaded {
            errors.push(format!("{}: {reason}", next_label(&page.label)));
        }
        let summary = page
            .result
            .as_ref()
            .map(|stored| {
                ici_reporting::render_markdown(
                    stored,
                    &RenderOptions {
                        max_markdown_bytes: (50_000 / publication.pages.len()).max(128),
                        max_target_rows: 5,
                        ..RenderOptions::default()
                    },
                )
                .map_err(|error| error.to_string())
            })
            .transpose()?
            .unwrap_or_else(|| "No readable result summary was supplied.\n".into());
        summaries.push(format!(
            "### {}\n\n{}\n\n{}",
            escape(&page.label),
            viewer.map_or_else(
                || format!("Report uploaded to `{path}` (Pages is not enabled)."),
                |url| format!("[Open report]({url})")
            ),
            summary
        ));
    }
    summarize_first(record);
    if !errors.is_empty() {
        return Err(errors.join("; "));
    }
    if let Some(pr) = publication.pr {
        if publication.head.is_empty() {
            return Err("reports uploaded; PR comment requires the analyzed head identity".into());
        }
        let target = publication
            .comments
            .as_ref()
            .unwrap_or(&publication.destination);
        let mut comments = Client {
            target,
            send: client.send,
        };
        if comments.head(pr)? != publication.head {
            return Ok("reports uploaded; comment retained because the PR head changed".into());
        }
        let previous = comments.comment(pr, &publication.marker)?;
        if previous
            .as_ref()
            .is_some_and(|(_, body)| newer_run(body, publication.run, publication.attempt))
        {
            return Ok(
                "reports uploaded; comment retained because a newer run already reported".into(),
            );
        }
        let mut body = format!(
            "{}\n## ici verification report\n\n{}",
            publication.marker,
            summaries.join("\n\n")
        );
        if let Some(url) = &publication.run_url {
            body.push_str(&format!("\n\n[Workflow run]({url})"));
        }
        body = ici_reporting::bounded_summary(&body, 60_000).map_err(|error| error.to_string())?;
        body.push_str(&format!(
            "\n<!-- head:{} run:{}.{} -->",
            publication.head, publication.run, publication.attempt
        ));
        if body.len() > 65536 {
            return Err("reports uploaded; comment exceeds 64 KiB bound".into());
        }
        // Recheck after uploads and pagination; an unknown or changed head is never overwritten.
        if comments.head(pr)? != publication.head {
            return Ok("reports uploaded; comment retained because the PR head changed".into());
        }
        record["comment_url"] =
            json!(comments.write_comment(pr, previous.map(|(id, _)| id), &body)?);
    }
    Ok(if site.is_some() {
        "published"
    } else {
        "published (Pages is not enabled)"
    }
    .into())
}
/// Historical `ici publish` / `verify --publish` (frozen `engines/publish.ReportPublisher`): the
/// stable gh-pages layout, then one sticky comment that is posted even when an upload failed
/// (its rows say so). The page path carries no revision, so a later run always replaces it;
/// the comment is upserted on the sticky marker like frozen, without the next path's head and
/// run ordering guard or metadata trailer, and without requiring a head identity.
fn perform_legacy<F: FnMut(&Request) -> std::result::Result<Response, TransportError>>(
    publication: &Publication,
    legacy: &LegacyComment,
    send: &mut F,
    record: &mut Value,
) -> std::result::Result<String, String> {
    let paths = validate(publication)?;
    let mut client = Client {
        target: &publication.destination,
        send,
    };
    // Frozen starts a missing self-mode branch from GITHUB_SHA; a hub repository does not hold
    // the analyzed commits, so it starts from the hub's default branch.
    let base = if legacy.prefix.is_empty() {
        legacy.github_sha.as_str()
    } else {
        ""
    };
    let branch = client.ensure_branch(base);
    // Frozen `_check_pages`: any failure reads as Pages not enabled.
    let site = client.pages().ok().flatten();
    let mut reports = vec![];
    let mut errors = vec![];
    for (page, path) in publication.pages.iter().zip(&paths) {
        let uploaded = if legacy.missing.contains(&page.label) {
            Err("report page not found".to_owned())
        } else {
            branch.clone().and_then(|()| client.put(path, &page.bytes))
        };
        let viewer = site
            .as_ref()
            .filter(|_| uploaded.is_ok())
            .map(|site| legacy_viewer(site, path));
        record["reports"]
            .as_array_mut()
            .expect("report array")
            .push(report_row(page, path, viewer.as_deref(), &uploaded));
        if let Err(reason) = &uploaded {
            errors.push(format!("{}: {reason}", page.label));
        }
        reports.push(legacy_comment::Report {
            label: &page.label,
            suite: match &page.result {
                Some(StoredResult::Legacy(document)) => legacy_comment::suite(document.as_value()),
                _ => None,
            },
            remote_path: path.clone(),
            viewer,
            uploaded: uploaded.is_ok(),
        });
    }
    summarize_first(record);
    if let Some(viewer) = reports.iter().find_map(|report| report.viewer.clone()) {
        record["viewer_url"] = json!(viewer);
    }
    let mut comment_failure = None;
    if let Some(pr) = publication.pr {
        let target = publication
            .comments
            .as_ref()
            .unwrap_or(&publication.destination);
        let mut comments = Client {
            target,
            send: client.send,
        };
        let body = ici_reporting::bounded_summary(
            &legacy_body(legacy, &reports, site.is_some(), publication),
            60_000,
        )
        .map_err(|error| error.to_string())?;
        // An unreadable comment list is never answered with a duplicate sticky comment.
        match comments
            .comment(pr, &publication.marker)
            .and_then(|previous| comments.write_comment(pr, previous.map(|(id, _)| id), &body))
        {
            Ok(url) => record["comment_url"] = json!(url),
            Err(reason) => comment_failure = Some(reason),
        }
    }
    if !errors.is_empty() {
        return Err(errors.join("; "));
    }
    if let Some(reason) = comment_failure {
        return Err(format!("reports uploaded; PR comment failed: {reason}"));
    }
    Ok(if site.is_some() {
        "published"
    } else {
        "published (Pages is not enabled)"
    }
    .into())
}
fn legacy_body(
    legacy: &LegacyComment,
    reports: &[legacy_comment::Report<'_>],
    pages_enabled: bool,
    publication: &Publication,
) -> String {
    let run_url = publication.run_url.as_deref();
    match reports {
        [report] if legacy.unlabeled => legacy_comment::single(
            report.suite.as_ref(),
            report.viewer.as_deref(),
            report.uploaded,
            pages_enabled,
            &legacy.mode,
            &report.remote_path,
            run_url,
        ),
        _ => legacy_comment::multi(reports, &legacy.mode, run_url, pages_enabled),
    }
}
fn newer_run(body: &str, run: u64, attempt: u64) -> bool {
    body.split(" run:")
        .filter_map(|tail| tail.split_whitespace().next())
        .filter_map(|value| value.split_once('.'))
        .filter_map(|(run, attempt)| Some((run.parse::<u64>().ok()?, attempt.parse::<u64>().ok()?)))
        .any(|previous| previous > (run, attempt))
}
fn escape(value: &str) -> String {
    value
        .replace(['\n', '\r'], " ")
        .replace('|', "\\|")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// The stderr line for a publish refusal inside `verify --publish`: one `publish: ` prefix,
/// whether or not the refusal already carries it (result loading does).
pub fn refusal_line(reason: &CliError) -> String {
    let text = reason.to_string();
    if text.starts_with("publish: ") {
        format!("{text}\n")
    } else {
        format!("publish: {text}\n")
    }
}
pub fn command(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    command_with_transport(
        invocation,
        cwd,
        environment,
        &mut ici_distribution::transport::send,
    )
}
/// [`command`] for historical `verify --publish`, which files hub reports under the project.
pub fn command_named(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    project_name: &str,
) -> Result<CommandOutput> {
    command_for_project(
        invocation,
        cwd,
        environment,
        Some(project_name),
        &mut ici_distribution::transport::send,
    )
}
pub fn command_with_transport(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    send: &mut impl FnMut(&Request) -> std::result::Result<Response, TransportError>,
) -> Result<CommandOutput> {
    command_for_project(invocation, cwd, environment, None, send)
}
/// Historical `verify --publish` names the analyzed project (frozen
/// `ReportPublisher(project_name=reporting_context.project.name)`): a hub publication is filed
/// under it. `ici publish` passes none, so frozen used the repository name.
pub fn command_for_project(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    project_name: Option<&str>,
    send: &mut impl FnMut(&Request) -> std::result::Result<Response, TransportError>,
) -> Result<CommandOutput> {
    let explicit = invocation.value("config_path").map(|path| under(cwd, path));
    let local = invocation
        .value("local_path")
        .or_else(|| invocation.value("local_config"))
        .map(|path| under(cwd, path));
    let config = crate::cutover::inspect(
        invocation,
        cwd,
        explicit.as_deref(),
        local.as_deref(),
        environment,
    )
    .map_err(|error| {
        // Frozen `next publish` reads the workspace through `_workspace` (`config: `).
        if invocation.command() == "next publish" {
            crate::commands::classed(crate::commands::CONFIG_PREFIX, error)
        } else {
            CliError(error.to_string())
        }
    })?;
    let modern = invocation.command() == "next publish";
    // Frozen engines/publish.ReportPublisher: the historical command skips (exit 0) outside
    // GitHub Actions and when no repository or token resolves; only a real failure exits 1.
    if !modern && environment.get("GITHUB_ACTIONS").map(String::as_str) != Some("true") {
        return Ok(legacy_output(
            "--publish requires a GitHub Actions environment; skipping.".into(),
            None,
            0,
        ));
    }
    let get = |key: &str| environment.get(key).cloned().unwrap_or_default();
    let declared = config.publish.as_ref();
    let repo = declared
        .map(|target| target.repo.clone())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            if !modern && !get("ICI_PUBLISH_REPO").is_empty() {
                get("ICI_PUBLISH_REPO")
            } else {
                get("GITHUB_REPOSITORY")
            }
        });
    let api_url = declared
        .map(|target| target.api_url.clone())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            let configured = get("GITHUB_API_URL");
            if !modern && configured.is_empty() {
                "https://api.github.com".into()
            } else {
                configured
            }
        });
    let branch = declared
        .map(|target| target.branch.clone())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            environment
                .get("ICI_PUBLISH_BRANCH")
                .cloned()
                .unwrap_or_else(|| "gh-pages".into())
        });
    let token = if modern {
        let primary = get(declared
            .map(|target| target.token_env.as_str())
            .unwrap_or("GITHUB_TOKEN"));
        if primary.is_empty() {
            get("ICI_PUBLISH_TOKEN")
        } else {
            primary
        }
    } else {
        declared
            .map(|target| get(&target.token_env))
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| {
                if !get("ICI_PUBLISH_REPO").is_empty() && !get("ICI_PUBLISH_TOKEN").is_empty() {
                    get("ICI_PUBLISH_TOKEN")
                } else {
                    get("GITHUB_TOKEN")
                }
            })
    };
    let destination = Target {
        api_url: api_url.clone(),
        repo: repo.clone(),
        branch,
        token,
    };
    if !modern && (repo.is_empty() || destination.token.is_empty()) {
        return Ok(legacy_output(
            "--publish skipped: GITHUB_REPOSITORY or a publish token is unavailable.".into(),
            None,
            0,
        ));
    }
    if !modern && invocation.values("report_dir").is_empty() {
        let page = invocation.value("html").unwrap_or("verify_report.html");
        if !under(&config.root, page).exists() {
            return Ok(legacy_output(
                format!("--publish skipped: HTML report not found at {page}."),
                None,
                1,
            ));
        }
    }
    let mut pages = vec![];
    let mut missing = vec![];
    if modern {
        pages.push(load_page(
            &config.root,
            &config.workspace_name,
            &under(
                &config.root,
                invocation.value("page").unwrap_or(crate::DEFAULT_PAGE),
            ),
            Some(&under(
                &config.root,
                invocation.value("result").unwrap_or(crate::DEFAULT_RESULT),
            )),
            true,
        )?);
    } else if !invocation.values("report_dir").is_empty() {
        if invocation.values("report_dir").len() > 256 {
            return Err(CliError("publication exceeds 256 reports".into()));
        }
        for argument in invocation.values("report_dir") {
            let (label, path) = argument
                .split_once('=')
                .map_or((None, argument.as_str()), |(label, path)| {
                    (Some(label), path)
                });
            let path = under(&config.root, path);
            let label = label.map(str::to_owned).unwrap_or_else(|| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("workspace")
                    .into()
            });
            let page = path.join("verify_report.html");
            if page.is_file() {
                pages.push(load_page(
                    &config.root,
                    &label,
                    &page,
                    Some(&path.join("verify_report.json")),
                    false,
                )?);
            } else {
                // The suite still enriches the comment row (frozen loads it per directory).
                let json = path.join("verify_report.json");
                missing.push(label.clone());
                pages.push(Page {
                    label,
                    bytes: vec![],
                    result: json
                        .is_file()
                        .then(|| crate::report::load(&config.root, &json).ok())
                        .flatten(),
                    result_digest: None,
                });
            }
            if pages
                .iter()
                .map(|page| page.bytes.len() as u64)
                .sum::<u64>()
                > 512 * 1024 * 1024
            {
                return Err(CliError(
                    "publication reports exceed 512 MiB aggregate bound".into(),
                ));
            }
        }
    } else {
        pages.push(load_page(
            &config.root,
            &config.workspace_name,
            &under(
                &config.root,
                invocation.value("html").unwrap_or("verify_report.html"),
            ),
            Some(&under(
                &config.root,
                invocation
                    .value("json_report")
                    .unwrap_or("verify_report.json"),
            )),
            false,
        )?);
    }
    let record_path = config.root.join(".ici/rust-v1/publish.json");
    record_preflight(&record_path)?;
    if repo.is_empty() || api_url.is_empty() || destination.token.is_empty() {
        let record = json!({"schema_id":"ici.next.publish","schema_version":1,"state":"NOT_CONFIGURED","detail":"no publication destination; configure publish.repo, publish.api_url and the named token environment variable"});
        save_record(&record_path, &record)?;
        return Ok(CommandOutput {
            stdout: format!(
                "publish: not_configured — {}\n",
                record["detail"].as_str().unwrap()
            ),
            stderr: String::new(),
            exit_code: 2,
        });
    }
    destination.validate().map_err(CliError)?;
    // Frozen `_detect_pr_number` reads an unreadable or invalid event as "no pull request".
    let (pr, event_head) = if modern {
        event(environment)?
    } else {
        event(environment).unwrap_or((None, String::new()))
    };
    let head = run_head(event_head, get("GITHUB_SHA"));
    let own_repo = get("GITHUB_REPOSITORY");
    let comments = if pr.is_some() && !own_repo.is_empty() && own_repo != repo {
        Some(Target {
            api_url: api_url.clone(),
            repo: own_repo.clone(),
            branch: destination.branch.clone(),
            token: get("GITHUB_TOKEN"),
        })
    } else {
        None
    };
    let run = get("GITHUB_RUN_ID").parse().unwrap_or(0);
    let attempt = get("GITHUB_RUN_ATTEMPT").parse().unwrap_or(1);
    let server = declared
        .map(|target| target.server_url.clone())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| get("GITHUB_SERVER_URL"));
    let run_url = (server.starts_with("https://") && !own_repo.is_empty() && run > 0).then(|| {
        format!(
            "{}/{own_repo}/actions/runs/{run}",
            server.trim_end_matches('/')
        )
    });
    let publication = Publication {
        destination,
        comments,
        pages,
        head,
        pr,
        run,
        attempt,
        reference: get("GITHUB_REF_NAME"),
        marker: if modern {
            // Frozen `_marker(_label(...))`.
            format!("<!-- ici-next:{} -->", next_label(&config.workspace_name))
        } else {
            legacy_comment::MARKER.into()
        },
        run_url,
        legacy: (!modern).then(|| {
            let hub = !get("ICI_PUBLISH_REPO").trim().is_empty()
                || !own_repo.is_empty() && repo != own_repo;
            LegacyComment {
                missing,
                unlabeled: invocation.values("report_dir").is_empty(),
                mode: if hub { "hub" } else { "self" }.into(),
                // Frozen: (project_name or the analyzed repository's name or "project").
                prefix: if hub {
                    project_name
                        .filter(|name| !name.is_empty())
                        .map(str::to_owned)
                        .or_else(|| {
                            own_repo
                                .trim()
                                .rsplit('/')
                                .next()
                                .filter(|name| !name.is_empty())
                                .map(str::to_owned)
                        })
                        .unwrap_or_else(|| "project".into())
                        .replace('/', "-")
                } else {
                    String::new()
                },
                reference: get("GITHUB_REF_NAME").trim().to_owned(),
                github_sha: get("GITHUB_SHA").trim().to_owned(),
            }
        }),
    };
    let record = publish_with_transport(&publication, send);
    save_record(&record_path, &record)?;
    if !modern {
        let (message, success) = legacy_message(&publication, &record, &repo);
        return Ok(legacy_output(
            message,
            record["comment_url"].as_str(),
            if success { 0 } else { 1 },
        ));
    }
    Ok(CommandOutput {
        stdout: format!(
            "publish: {} — {}\nRecord: {}\n",
            record["state"].as_str().unwrap_or("FAILED").to_lowercase(),
            record["detail"].as_str().unwrap_or("failed"),
            record_path.display()
        ),
        stderr: String::new(),
        exit_code: if record["state"] == "SUCCESS" { 0 } else { 1 },
    })
}
/// The commit a run reports on. A `pull_request` event names the PR head, which the ordering
/// guard compares with the PR's current head; `GITHUB_SHA` there is the synthetic merge commit,
/// which never equals it (frozen preferred it, so every PR run looked stale).
fn run_head(event_head: String, github_sha: String) -> String {
    if event_head.is_empty() {
        github_sha
    } else {
        event_head
    }
}
fn event(environment: &BTreeMap<String, String>) -> Result<(Option<u64>, String)> {
    let Some(path) = environment
        .get("GITHUB_EVENT_PATH")
        .filter(|path| !path.is_empty())
    else {
        return Ok((None, String::new()));
    };
    let bytes = crate::report::read_bounded(Path::new(path), 8 * 1024 * 1024)?;
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|_| CliError("GitHub event is not valid JSON".into()))?;
    Ok((
        value["pull_request"]["number"]
            .as_u64()
            .or_else(|| value["issue"]["number"].as_u64()),
        value["pull_request"]["head"]["sha"]
            .as_str()
            .unwrap_or("")
            .into(),
    ))
}
fn load_page(
    root: &Path,
    label: &str,
    path: &Path,
    result: Option<&Path>,
    required: bool,
) -> Result<Page> {
    // Frozen `publish_run` (application/publish.py `_load_result`) reads the stored result
    // before the page: a missing or unreadable result is a `publish: ` refusal naming it.
    let (result, result_digest) = match result {
        Some(path) if required || path.exists() => {
            let bytes = crate::report::read_bounded(path, 64 * 1024 * 1024).map_err(|error| {
                CliError(format!(
                    "publish: cannot read result {}: {error}",
                    path.display()
                ))
            })?;
            let loaded = crate::report::load(root, path).map_err(|error| {
                CliError(format!(
                    "publish: {} is not a result this version can read: {error}",
                    path.display()
                ))
            })?;
            (Some(loaded), Some(ici_domain::sha256(bytes)))
        }
        _ => (None, None),
    };
    // Frozen `_load_artifact`: an absent page is a `publish: ` refusal that names the page and
    // the command that writes it.
    if required && !path.is_file() {
        return Err(CliError(format!(
            "publish: no page at {} — run `ici next report` first",
            path.display()
        )));
    }
    let canonical = path
        .canonicalize()
        .map_err(|error| CliError(format!("publish page: {error}")))?;
    if !canonical.starts_with(root) {
        return Err(CliError(
            "publish page must be contained in the workspace".into(),
        ));
    }
    let bytes = crate::report::read_bounded(&canonical, 64 * 1024 * 1024)?;
    Ok(Page {
        label: label.into(),
        bytes,
        result,
        result_digest,
    })
}
fn record_preflight(path: &Path) -> Result<()> {
    if path.exists() {
        let value: Value =
            serde_json::from_slice(&crate::report::read_bounded(path, 8 * 1024 * 1024)?)
                .map_err(|_| CliError("existing publication record is invalid".into()))?;
        if value["schema_id"] != "ici.next.publish" || value["schema_version"] != 1 {
            return Err(CliError(
                "publication record would overwrite unrelated data".into(),
            ));
        }
    }
    crate::execute::validate_destination(path, false)
}
fn save_record(path: &Path, record: &Value) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(record).map_err(|error| CliError(error.to_string()))?;
    ici_execution::storage::atomic_write(
        path,
        &bytes,
        if path.exists() {
            ici_execution::storage::WriteMode::ReplaceRegular
        } else {
            ici_execution::storage::WriteMode::CreateNew
        },
    )
    .map_err(|error| CliError(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with_event(payload: &str) -> (tempfile::TempDir, BTreeMap<String, String>) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("event.json");
        std::fs::write(&path, payload).unwrap();
        let environment = BTreeMap::from([(
            "GITHUB_EVENT_PATH".to_owned(),
            path.to_string_lossy().into_owned(),
        )]);
        (dir, environment)
    }

    fn publication(head: &str, pr: Option<u64>, reference: &str) -> Publication {
        Publication {
            destination: Target {
                api_url: "https://git.example".into(),
                repo: "team/repo".into(),
                branch: "gh-pages".into(),
                token: "t".into(),
            },
            comments: None,
            pages: vec![],
            head: head.into(),
            pr,
            run: 4,
            attempt: 2,
            reference: reference.into(),
            marker: String::new(),
            run_url: None,
            legacy: None,
        }
    }

    /// Frozen `application/publish._remote_path` and `_label` (values computed with the frozen
    /// regular expression `[^A-Za-z0-9_.-]+` -> `-`, strip `-`, lower).
    #[test]
    fn next_publish_paths_follow_the_frozen_layout() {
        let sha = "0123456789abcdef0123";
        for (head, pr, reference, label, expected) in [
            (
                sha,
                Some(7),
                "main",
                "W",
                "ici/w/pr-7/0123456789ab/index.html",
            ),
            (
                sha,
                None,
                "feature/Fix",
                "w",
                "ici/w/feature-Fix/0123456789ab/index.html",
            ),
            (sha, None, "", "w", "ici/w/local/0123456789ab/index.html"),
            ("", None, "main", "w", "ici/w/main/unversioned/index.html"),
            ("", Some(3), "", "w", "ici/w/pr-3/unversioned/index.html"),
            (
                sha,
                None,
                "main",
                "A--b!!c.",
                "ici/a--b-c./main/0123456789ab/index.html",
            ),
            (
                sha,
                None,
                "main",
                "--x y--",
                "ici/x-y/main/0123456789ab/index.html",
            ),
            (
                sha,
                None,
                "main",
                "!!!",
                "ici/workspace/main/0123456789ab/index.html",
            ),
            (
                sha,
                None,
                "main",
                "é tag",
                "ici/tag/main/0123456789ab/index.html",
            ),
        ] {
            assert_eq!(
                next_path(&publication(head, pr, reference), label),
                expected,
                "{head:?} {pr:?} {reference:?} {label:?}"
            );
        }
    }

    /// Frozen `engines/publish._resolve_target` sub paths and the viewer directory URL.
    #[test]
    fn legacy_paths_follow_the_frozen_gh_pages_layout() {
        let legacy = |prefix: &str, reference: &str, unlabeled: bool| LegacyComment {
            unlabeled,
            mode: String::new(),
            missing: vec![],
            prefix: prefix.into(),
            reference: reference.into(),
            github_sha: String::new(),
        };
        for (legacy, pr, label, expected) in [
            (legacy("", "", true), None, "x", "index.html"),
            (legacy("", "main", true), None, "x", "main/index.html"),
            (legacy("", "master", true), None, "x", "main/index.html"),
            (
                legacy("", "a/b/C", true),
                None,
                "x",
                "branch/a-b-C/index.html",
            ),
            (legacy("", "main", true), Some(9), "x", "pr/9/index.html"),
            (
                legacy("repo", "", true),
                Some(9),
                "x",
                "repo/pr/9/index.html",
            ),
            (
                legacy("", "main", false),
                None,
                "Svc/API",
                "Svc/API/main/index.html",
            ),
            (
                legacy("hub", "main", false),
                Some(1),
                "b",
                "hub/b/pr/1/index.html",
            ),
        ] {
            assert_eq!(legacy_path(&legacy, pr, label), expected);
        }
        assert_eq!(
            legacy_viewer("https://p.example/", "pr/9/index.html"),
            "https://p.example/pr/9/"
        );
        assert_eq!(
            legacy_viewer("https://p.example", "index.html"),
            "https://p.example/"
        );
    }

    #[test]
    fn pull_request_runs_report_on_the_pr_head_not_the_merge_commit() {
        let (_dir, environment) =
            with_event(r#"{"pull_request":{"number":7,"head":{"sha":"head-sha"}}}"#);
        let (pr, event_head) = event(&environment).unwrap();
        assert_eq!(pr, Some(7));
        assert_eq!(run_head(event_head, "merge-sha".into()), "head-sha");
    }

    #[test]
    fn runs_without_a_pull_request_head_report_on_github_sha() {
        let (_dir, environment) = with_event(r#"{"issue":{"number":3}}"#);
        let (pr, event_head) = event(&environment).unwrap();
        assert_eq!(pr, Some(3));
        assert_eq!(run_head(event_head, "push-sha".into()), "push-sha");
        assert_eq!(run_head(String::new(), "push-sha".into()), "push-sha");
    }

    #[test]
    fn a_refusal_carries_one_publish_prefix() {
        assert_eq!(
            refusal_line(&CliError("publish: cannot read result r.json: gone".into())),
            "publish: cannot read result r.json: gone\n"
        );
        assert_eq!(
            refusal_line(&CliError("page is not UTF-8".into())),
            "publish: page is not UTF-8\n"
        );
    }

    #[test]
    fn a_malformed_event_payload_is_refused() {
        let (_dir, environment) = with_event("{not json");
        let Err(error) = event(&environment) else {
            panic!("malformed event accepted")
        };
        assert_eq!(error.0, "GitHub event is not valid JSON");
    }
}
