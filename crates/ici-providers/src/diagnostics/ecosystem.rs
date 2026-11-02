use super::{Parsed, finding, positive, span, text};
use crate::{
    ProviderError,
    plan::{ProviderPlan, relative_location},
};
use regex::Regex;
use serde_json::Value;

pub(super) fn eslint(stdout: &str, plan: &ProviderPlan) -> Result<Parsed, ProviderError> {
    let document: Value = serde_json::from_str(stdout).map_err(|e| ProviderError(e.to_string()))?;
    let files = document
        .as_array()
        .ok_or_else(|| ProviderError("ESLint output is not a file array".into()))?;
    let mut parsed = Parsed::default();
    for file in files {
        let path = text(file, "filePath")?;
        let relative = relative_location(path, &plan.context)?;
        let messages = file["messages"]
            .as_array()
            .ok_or_else(|| ProviderError("ESLint messages is not an array".into()))?;
        let mut ignored = false;
        for item in messages {
            let message = text(item, "message")?;
            if item["line"].is_null()
                && message.starts_with("File ignored")
                && (message.contains("matching ignore pattern") || message.contains("by default"))
            {
                ignored = true;
                parsed.limitations.push(format!("{relative}: {message}"));
                continue;
            }
            let fatal = item["fatal"].as_bool().unwrap_or(false);
            let row = if fatal && item["line"].is_null() {
                1
            } else {
                positive(&item["line"], "ESLint line")?
            };
            let column = if item["column"].is_null() {
                None
            } else {
                Some(positive(&item["column"], "ESLint column")?)
            };
            let severity = match item["severity"].as_u64() {
                Some(2) => "error",
                Some(1) => "warning",
                _ => return Err(ProviderError("invalid ESLint severity".into())),
            };
            let rule = item["ruleId"].as_str().unwrap_or("parse");
            // Frozen parity: ESLint spans carry no end position. Fingerprint v3 hashes the end
            // line/column, so adding ESLint's range would change every attributed identity and
            // make existing baselines incomparable.
            let result = finding(plan, path, row, column, rule, message, severity)?;
            parsed.findings.push(result);
        }
        if !ignored {
            parsed.inspected.insert(relative);
        }
    }
    Ok(parsed)
}
/// Cargo names span files relative to the Cargo workspace root, not to the package directory it
/// ran in: the package's explicit `package.workspace`, else the nearest directory at or above it
/// (within the ici workspace) whose `Cargo.toml` declares `[workspace]`, else the package itself.
fn cargo_workspace_root(context: &crate::plan::DiagnosticContext) -> std::path::PathBuf {
    let manifest = |directory: &std::path::Path| {
        std::fs::read_to_string(directory.join("Cargo.toml"))
            .ok()
            .and_then(|text| text.parse::<toml_edit::DocumentMut>().ok())
    };
    if let Some(explicit) = manifest(&context.cwd).and_then(|document| {
        document
            .get("package")
            .and_then(|package| package.get("workspace"))
            .and_then(|value| value.as_str())
            .map(str::to_owned)
    }) {
        return context.cwd.join(explicit);
    }
    for directory in context.cwd.ancestors() {
        if manifest(directory).is_some_and(|document| document.contains_key("workspace")) {
            return directory.to_path_buf();
        }
        if directory == context.workspace_root {
            break;
        }
    }
    context.cwd.clone()
}
pub(super) fn clippy(stdout: &str, plan: &ProviderPlan) -> Result<Parsed, ProviderError> {
    let mut parsed = Parsed::default();
    let cargo_root = cargo_workspace_root(&plan.context);
    let located = |path: &str| {
        if std::path::Path::new(path).is_absolute() {
            path.to_owned()
        } else {
            cargo_root.join(path).to_string_lossy().into_owned()
        }
    };
    let mut finished = 0;
    for line in stdout.lines().filter(|line| !line.trim().is_empty()) {
        let record: Value = serde_json::from_str(line).map_err(|e| ProviderError(e.to_string()))?;
        match text(&record, "reason")? {
            "build-finished" => {
                finished += 1;
                if !record["success"].is_boolean() {
                    return Err(ProviderError("invalid Cargo completion record".into()));
                }
            }
            "compiler-message" => {
                let message = &record["message"];
                let spans = message["spans"]
                    .as_array()
                    .ok_or_else(|| ProviderError("Cargo spans missing".into()))?;
                let primary = spans.iter().find(|span| span["is_primary"] == true);
                let level = text(message, "level")?;
                let Some(primary) = primary else {
                    if level == "error" && parsed.findings.is_empty() {
                        return Err(ProviderError(format!(
                            "unattributed compiler error: {}",
                            text(message, "message")?
                        )));
                    }
                    continue;
                };
                let path = located(text(primary, "file_name")?);
                let rule = message["code"]["code"].as_str().unwrap_or("diagnostic");
                let row = positive(&primary["line_start"], "Rust line")?;
                let column = Some(positive(&primary["column_start"], "Rust column")?);
                let severity = if level == "error" { "error" } else { "warning" };
                let mut result = finding(
                    plan,
                    &path,
                    row,
                    column,
                    rule,
                    text(message, "message")?,
                    severity,
                )?;
                for related in spans.iter().filter(|span| span["is_primary"] != true) {
                    result.related_locations.push(span(
                        &plan.context,
                        &located(text(related, "file_name")?),
                        positive(&related["line_start"], "related Rust line")?,
                        Some(positive(&related["column_start"], "related Rust column")?),
                        related["label"].as_str().unwrap_or(""),
                    )?);
                }
                parsed.findings.push(result);
            }
            "compiler-artifact" => {
                if let Some(path) = record["target"]["src_path"].as_str()
                    && let Ok(path) = relative_location(path, &plan.context)
                {
                    parsed.inspected.insert(path);
                }
            }
            "build-script-executed" => {}
            other => {
                return Err(ProviderError(format!(
                    "unknown Cargo record reason {other}"
                )));
            }
        }
    }
    if finished != 1 {
        return Err(ProviderError(
            "expected one Cargo build-finished record".into(),
        ));
    }
    Ok(parsed)
}
pub(super) fn tsc(
    stdout: &str,
    stderr: &str,
    plan: &ProviderPlan,
) -> Result<Parsed, ProviderError> {
    if !stderr.trim().is_empty() {
        return Err(ProviderError(format!("TypeScript stderr: {stderr}")));
    }
    let pattern = Regex::new(r"^(.+)\(([1-9]\d*),([1-9]\d*)\): error TS(\d+): (\S.*)$")
        .expect("constant regex");
    let mut parsed = Parsed::default();
    for line in stdout.lines().filter(|line| !line.trim().is_empty()) {
        if let Some(captures) = pattern.captures(line) {
            parsed.findings.push(finding(
                plan,
                &captures[1],
                captures[2]
                    .parse()
                    .map_err(|_| ProviderError("invalid TS line".into()))?,
                Some(
                    captures[3]
                        .parse()
                        .map_err(|_| ProviderError("invalid TS column".into()))?,
                ),
                &captures[4],
                &captures[5],
                "error",
            )?);
        } else if line.starts_with("error TS") {
            return Err(ProviderError(format!(
                "TypeScript configuration/global diagnostic: {line}"
            )));
        } else if line.starts_with(' ') {
            // Frozen parity: indented message-chain lines are not part of the headline. Keeping
            // them out of the message keeps native and v3 attributed fingerprints comparable.
            continue;
        } else {
            return Err(ProviderError(format!(
                "unrecognized TypeScript output: {line}"
            )));
        }
    }
    Ok(parsed)
}
