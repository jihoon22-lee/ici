//! Stored publication is a separate outcome; it never rewrites the analysis verdict.
pub mod api;
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
}
pub fn publish_with_transport<
    F: FnMut(&Request) -> std::result::Result<Response, TransportError>,
>(
    publication: &Publication,
    send: &mut F,
) -> Value {
    let mut record = json!({"schema_id":"ici.next.publish","schema_version":1,"state":"FAILED","detail":"publication did not complete","reports":[],"head":publication.head,"pr":publication.pr,"run":format!("{}.{}",publication.run,publication.attempt)});
    let result = perform(publication, send, &mut record);
    match result {
        Ok(detail) => {
            record["state"] = json!("SUCCESS");
            record["detail"] = json!(detail);
        }
        Err(reason) => record["detail"] = json!(reason),
    }
    ici_execution::redaction::redact_value(&record)
}
fn perform<F: FnMut(&Request) -> std::result::Result<Response, TransportError>>(
    publication: &Publication,
    send: &mut F,
    record: &mut Value,
) -> std::result::Result<String, String> {
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
    let labels = publication
        .pages
        .iter()
        .map(|page| slug(&page.label))
        .collect::<BTreeSet<_>>();
    if labels.len() != publication.pages.len() {
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
    for page in &publication.pages {
        let label = slug(&page.label);
        let revision = if publication.head.is_empty() {
            format!("unversioned-{}.{}", publication.run, publication.attempt)
        } else {
            publication.head.chars().take(12).collect()
        };
        let reference = publication
            .pr
            .map_or_else(|| slug(&publication.reference), |pr| format!("pr-{pr}"));
        let path = format!("ici/{label}/{reference}/{revision}/index.html");
        let uploaded = client.put(&path, &page.bytes);
        let viewer = site
            .as_ref()
            .map(|site| format!("{}/{path}", site.trim_end_matches('/')));
        let detail = uploaded.as_ref().err().cloned();
        record["reports"].as_array_mut().expect("report array").push(json!({"label":page.label,"remote_path":path,"viewer_url":viewer,"uploaded":uploaded.is_ok(),"detail":detail,"artifact":{"sha256":ici_domain::sha256(&page.bytes),"bytes":page.bytes.len()},"result_sha256":page.result_digest}));
        if let Err(reason) = uploaded {
            errors.push(format!("{label}: {reason}"));
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
    if let Some(first) = record["reports"]
        .as_array()
        .and_then(|rows| rows.first())
        .cloned()
    {
        for key in ["remote_path", "viewer_url", "artifact", "result_sha256"] {
            record[key] = first[key].clone();
        }
    }
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
fn newer_run(body: &str, run: u64, attempt: u64) -> bool {
    body.split(" run:")
        .filter_map(|tail| tail.split_whitespace().next())
        .filter_map(|value| value.split_once('.'))
        .filter_map(|(run, attempt)| Some((run.parse::<u64>().ok()?, attempt.parse::<u64>().ok()?)))
        .any(|previous| previous > (run, attempt))
}
fn slug(value: &str) -> String {
    let mut slug = String::new();
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '-') {
            slug.push(ch.to_ascii_lowercase());
        } else if !slug.ends_with('-') {
            slug.push('-');
        }
    }
    let slug = slug.trim_matches(['-', '.']);
    if slug.is_empty() {
        "workspace".into()
    } else {
        slug.chars().take(200).collect()
    }
}
fn escape(value: &str) -> String {
    value
        .replace(['\n', '\r'], " ")
        .replace('|', "\\|")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
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
pub fn command_with_transport(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    send: &mut impl FnMut(&Request) -> std::result::Result<Response, TransportError>,
) -> Result<CommandOutput> {
    let explicit = invocation.value("config_path").map(|path| under(cwd, path));
    let local = invocation
        .value("local_path")
        .or_else(|| invocation.value("local_config"))
        .map(|path| under(cwd, path));
    let config = ici_config::inspect_configuration_with_environment(
        cwd,
        explicit.as_deref(),
        local.as_deref(),
        environment,
    )
    .map_err(|error| CliError(error.to_string()))?;
    let modern = invocation.command() == "next publish";
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
    let mut pages = vec![];
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
            pages.push(load_page(
                &config.root,
                &label,
                &path.join("verify_report.html"),
                Some(&path.join("verify_report.json")),
                false,
            )?);
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
            stdout: format!("{}\n", record["detail"].as_str().unwrap()),
            stderr: String::new(),
            exit_code: 2,
        });
    }
    destination.validate().map_err(CliError)?;
    let (pr, event_head) = event(environment)?;
    let head = if event_head.is_empty() {
        get("GITHUB_SHA")
    } else {
        event_head
    };
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
        reference: environment
            .get("GITHUB_REF_NAME")
            .cloned()
            .unwrap_or_else(|| "local".into()),
        marker: if modern {
            format!("<!-- ici-next:{} -->", slug(&config.workspace_name))
        } else {
            "<!-- ici-report -->".into()
        },
        run_url,
    };
    let record = publish_with_transport(&publication, send);
    save_record(&record_path, &record)?;
    Ok(CommandOutput {
        stdout: format!(
            "publish: {}\nRecord: {}\n",
            record["detail"].as_str().unwrap_or("failed"),
            record_path.display()
        ),
        stderr: String::new(),
        exit_code: if record["state"] == "SUCCESS" { 0 } else { 1 },
    })
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
    let canonical = path
        .canonicalize()
        .map_err(|error| CliError(format!("publish page: {error}")))?;
    if !canonical.starts_with(root) {
        return Err(CliError(
            "publish page must be contained in the workspace".into(),
        ));
    }
    let bytes = crate::report::read_bounded(&canonical, 64 * 1024 * 1024)?;
    let (result, result_digest) = match result {
        Some(path) if required || path.exists() => (
            Some(crate::report::load(path)?),
            Some(ici_domain::sha256(crate::report::read_bounded(
                path,
                64 * 1024 * 1024,
            )?)),
        ),
        _ => (None, None),
    };
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
