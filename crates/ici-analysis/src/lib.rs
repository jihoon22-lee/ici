//! Native analyses over immutable source snapshots; no process or filesystem fallback.
mod finding;
mod lines;
mod metrics;
mod parser;
mod scope;
mod source;

use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, TaskState};
use parser::{PARSER_IDENTITY, ParsedFile};
use serde_json::json;
use sha2::{Digest, Sha256};
use source::{MAX_SOURCES, MAX_TOTAL_BYTES, SourceDocument, SourceError};
use std::collections::BTreeMap;
use std::sync::Arc;

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
        if request.inputs.is_empty() {
            failure(
                &mut output,
                ".",
                &SourceError::new("empty-scope", "No source inputs were selected"),
            );
            return output;
        }
        if request.inputs.len() > MAX_SOURCES
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
                    "Analysis inventory exceeds 2048 files or 64 MiB",
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
            lines::analyze(request, &mut output);
            return output;
        }
        if !matches!(kind, "complexity" | "cognitive") {
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
        let options = serde_json::to_vec(&request.options).unwrap_or_default();
        for input in &request.inputs {
            if !input.path.ends_with(".py") && !input.path.ends_with(".pyi") {
                continue;
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
        if files.is_empty() && output.observation.state != TaskState::Failed {
            failure(
                &mut output,
                ".",
                &SourceError::new("empty-scope", "No applicable Python sources were selected"),
            );
        }
        metrics::analyze(request, &files, &mut output);
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
    if output.observation.state == TaskState::Failed
        || output.targets.iter().any(|target| target.status == "ERROR")
    {
        output.status = "ERROR".into();
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
