//! Native analyses over immutable source snapshots; no process or filesystem fallback.
mod ast_util;
mod cycle;
mod exception;
mod finding;
mod lines;
mod metrics;
mod modules;
mod parser;
mod resource;
mod scope;
mod security;
mod source;

use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, TaskState};
use parser::{PARSER_IDENTITY, ParsedFile};
use serde_json::json;
use sha2::{Digest, Sha256};
use source::{MAX_SOURCES, MAX_TOTAL_BYTES, SourceDocument, SourceError};
use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

const CACHE_BUDGET: usize = 32 * 1024 * 1024;
/// A bounded per-run cache shared by all checks against the same immutable snapshot.
#[derive(Default)]
pub struct AnalysisSession {
    parsed: BTreeMap<String, Arc<ParsedFile>>,
    cache_weight: usize,
    parser_invocations: usize,
}
impl AnalysisSession {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn parser_invocations(&self) -> usize {
        self.parser_invocations
    }
    pub fn analyze(&mut self, request: &AnalysisRequest) -> AnalysisOutput {
        self.analyze_inner(request, None)
    }
    pub fn analyze_cancellable(
        &mut self,
        request: &AnalysisRequest,
        cancellation: &AtomicBool,
    ) -> AnalysisOutput {
        self.analyze_inner(request, Some(cancellation))
    }
    fn analyze_inner(
        &mut self,
        request: &AnalysisRequest,
        cancellation: Option<&AtomicBool>,
    ) -> AnalysisOutput {
        let kind = request.check_id.rsplit('.').next().unwrap_or("");
        let provider = match kind {
            "line" => "ici.line",
            "complexity" | "cognitive" => "ici.metrics",
            "cycle" => "ici.cycles",
            "dup" => "ici.duplicates",
            "security" | "resource" | "exception" => "ici.hygiene",
            "dead" => "ici.deadcode",
            "compat" => "ici.compat",
            _ => "ici.analysis",
        };
        let mut output = AnalysisOutput::new(&request.task_id, provider);
        if cancelled(&mut output, cancellation) {
            return output;
        }
        if request.inputs.is_empty() {
            failure(
                &mut output,
                ".",
                &SourceError::new("empty-scope", "No source inputs were selected"),
            );
            return output;
        }
        if (matches!(
            kind,
            "security" | "resource" | "exception" | "dead" | "dup" | "compat"
        ) && request.inputs.len() > MAX_SOURCES)
            || request
                .inputs
                .iter()
                .map(|input| input.bytes.len())
                .sum::<usize>()
                > MAX_TOTAL_BYTES
        {
            failure(
                &mut output,
                ".",
                &SourceError::new(
                    "inventory-limit",
                    "Analysis inventory exceeds its 2048-file rule inventory or 512 MiB snapshot bound",
                ),
            );
            return output;
        }
        if request.language != "python" {
            failure(
                &mut output,
                ".",
                &SourceError::new(
                    "unsupported-language",
                    "This native analysis pack handles Python sources",
                ),
            );
            return output;
        }
        if kind == "line" {
            lines::analyze(request, &mut output, cancellation);
            return output;
        }
        if !matches!(
            kind,
            "complexity" | "cognitive" | "security" | "resource" | "exception" | "cycle"
        ) {
            failure(
                &mut output,
                ".",
                &SourceError::new(
                    "implementation-pending",
                    format!("Native {} rule is not implemented yet", request.check_id),
                ),
            );
            return output;
        }
        let mut files = Vec::new();
        let mut source_encodings = BTreeMap::new();
        let mut excluded_source_counts: BTreeMap<String, usize> = BTreeMap::new();
        let options = serde_json::to_vec(&request.options).unwrap_or_default();
        for input in &request.inputs {
            if cancelled(&mut output, cancellation) {
                return output;
            }
            if !input.path.ends_with(".py") && !input.path.ends_with(".pyi") {
                continue;
            }
            if matches!(
                kind,
                "security" | "resource" | "exception" | "dead" | "dup" | "compat"
            ) {
                let excluded: Vec<_> = source::exclusion_reasons(&input.path)
                    .into_iter()
                    .filter(|reason| {
                        !request
                            .options
                            .get(format!("include_{reason}"))
                            .and_then(serde_json::Value::as_bool)
                            .unwrap_or(false)
                    })
                    .collect();
                if !excluded.is_empty() {
                    for reason in &excluded {
                        *excluded_source_counts
                            .entry((*reason).to_owned())
                            .or_default() += 1;
                    }
                    let mut target = InspectionTarget::at(&input.path, 1);
                    target.target_name = "ExcludedAnalysisSource".into();
                    target.status = "SKIP".into();
                    target.message = format!("excluded: {}", excluded.join(", "));
                    output.observation.limitations.push(format!(
                        "excluded {}: {}",
                        input.path,
                        excluded.join(", ")
                    ));
                    output.targets.push(target);
                    continue;
                }
            }
            let mut hash = Sha256::new();
            for item in [
                PARSER_IDENTITY.as_bytes(),
                input.path.as_bytes(),
                request.target_version.as_bytes(),
                options.as_slice(),
                input.bytes.as_slice(),
            ] {
                hash.update((item.len() as u64).to_le_bytes());
                hash.update(item);
            }
            let key = format!("{:x}", hash.finalize());
            let parsed = if let Some(parsed) = self.parsed.get(&key) {
                Ok(Arc::clone(parsed))
            } else {
                self.parser_invocations += 1;
                SourceDocument::decode(input)
                    .and_then(|source| ParsedFile::parse(source, &request.target_version))
                    .map(|parsed| {
                        let weight = parsed
                            .source
                            .text
                            .len()
                            .saturating_mul(4)
                            .saturating_add(parsed.node_count.saturating_mul(160));
                        let parsed = Arc::new(parsed);
                        if weight <= CACHE_BUDGET {
                            if self.cache_weight.saturating_add(weight) > CACHE_BUDGET {
                                self.parsed.clear();
                                self.cache_weight = 0;
                            }
                            self.cache_weight += weight;
                            self.parsed.insert(key, Arc::clone(&parsed));
                        }
                        parsed
                    })
            };
            match parsed {
                Ok(parsed) => {
                    source_encodings.insert(input.path.clone(), parsed.source.encoding.clone());
                    files.push(parsed);
                }
                Err(error) => failure(&mut output, &input.path, &error),
            }
        }
        if cancelled(&mut output, cancellation) {
            return output;
        }
        if files.is_empty() && output.observation.state != TaskState::Failed {
            failure(
                &mut output,
                ".",
                &SourceError::new("empty-scope", "No applicable Python sources were selected"),
            );
        }
        match kind {
            "security" => security::analyze(request, &files, &mut output, cancellation),
            "resource" => resource::analyze(request, &files, &mut output, cancellation),
            "exception" => exception::analyze(request, &files, &mut output, cancellation),
            "cycle" => cycle::analyze(request, &files, &mut output, cancellation),
            _ => metrics::analyze(request, &files, &mut output, cancellation),
        }
        output.extra["excluded_source_counts"] = json!(excluded_source_counts);
        output.extra["source_encodings"] = json!(source_encodings);
        output.extra["parser_identity"] = json!(PARSER_IDENTITY);
        output
    }
}
pub fn analyze(request: &AnalysisRequest) -> AnalysisOutput {
    AnalysisSession::new().analyze(request)
}
fn failure(output: &mut AnalysisOutput, path: &str, error: &SourceError) {
    output.observation.state = TaskState::Failed;
    output.observation.limitations.push(format!(
        "{path}:{}:{}: {}: {}",
        error.line, error.column, error.code, error.message
    ));
    let mut target = InspectionTarget::at(path, error.line);
    target.start_column = Some(error.column);
    target.target_name = format!("PythonAnalysis:{}", error.code);
    target.message = error.message.clone();
    target.status = "ERROR".into();
    output.targets.push(target);
    output.status = "ERROR".into();
    output.summary = "Python analysis did not complete for every selected input".into();
}
fn number_option(request: &AnalysisRequest, name: &str, default: u32) -> u32 {
    request
        .options
        .get(name)
        .and_then(|value| value.as_u64())
        .and_then(|value| u32::try_from(value).ok())
        .unwrap_or(default)
}
fn finish_status(output: &mut AnalysisOutput, request: &AnalysisRequest) {
    if output.observation.state == TaskState::Cancelled {
        output.status = "ERROR".into();
        output.summary = "Analysis cancelled; completed evidence was retained".into();
        return;
    }
    if output.observation.state == TaskState::Failed
        || output.targets.iter().any(|target| target.status == "ERROR")
    {
        output.status = "ERROR".into();
        output.summary = format!(
            "{} analysis incomplete: {}",
            request.check_id,
            output
                .observation
                .limitations
                .first()
                .map_or("not all selected inputs were analyzed", String::as_str)
        );
        return;
    }
    let failed = output.targets.iter().any(|target| target.status == "FAIL");
    let warned = output.targets.iter().any(|target| target.status == "WARN");
    output.status = match request
        .options
        .get("mode")
        .and_then(|value| value.as_str())
        .unwrap_or("pass_warn_fail")
    {
        "pass_fail" if failed || warned => "FAIL",
        "pass_warn" if failed || warned => "WARN",
        _ if failed => "FAIL",
        _ if warned => "WARN",
        _ => "PASS",
    }
    .into();
}

#[cfg(test)]
mod tests;

fn cancelled(output: &mut AnalysisOutput, cancellation: Option<&AtomicBool>) -> bool {
    if !cancellation_requested(cancellation) {
        return false;
    }
    output.observation.state = TaskState::Cancelled;
    output.status = "ERROR".into();
    output.summary = "Analysis cancelled; completed evidence was retained".into();
    if !output
        .observation
        .limitations
        .iter()
        .any(|message| message == "analysis cancelled")
    {
        output
            .observation
            .limitations
            .push("analysis cancelled".into());
    }
    true
}

fn cancellation_requested(cancellation: Option<&AtomicBool>) -> bool {
    cancellation.is_some_and(|flag| flag.load(Ordering::Relaxed))
}
