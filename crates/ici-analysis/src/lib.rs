//! Native analyses over immutable source snapshots; no process or filesystem fallback.
mod annotations;
mod ast_util;
mod casefold_table;
mod codecs;
mod compat;
mod cycle;
mod dead;
mod details;
pub mod duplicates;
mod exception;
mod facts;
mod idna;
#[rustfmt::skip]
mod idna_tables;
pub use facts::{ClassFact, FunctionFact, SourceFacts};
mod finding;
mod legacy;
pub use legacy::lint_syntax_error;
mod lines;
mod metadata;
mod metrics;
mod modules;
mod package;
mod parser;
mod python_ast;
mod python_duplicates;
mod resource;
mod scope;
mod security;
mod semantic;
mod source;
mod stateful;
mod unicode_codecs;
pub use source::exclusion_reasons as source_exclusion_reasons;
pub mod unicode;
mod wheel;
mod wheel_zip;
mod xid_table;

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
    pub fn source_facts(
        &mut self,
        request: &AnalysisRequest,
    ) -> Result<SourceFacts, Box<AnalysisOutput>> {
        self.read_facts(request, None)
    }
    pub fn source_facts_cancellable(
        &mut self,
        request: &AnalysisRequest,
        cancellation: &AtomicBool,
    ) -> Result<SourceFacts, Box<AnalysisOutput>> {
        self.read_facts(request, Some(cancellation))
    }
    fn read_facts(
        &mut self,
        request: &AnalysisRequest,
        cancellation: Option<&AtomicBool>,
    ) -> Result<SourceFacts, Box<AnalysisOutput>> {
        let output = self.analyze_kind(request, cancellation, "source-facts");
        if output.observation.state != TaskState::Succeeded {
            return Err(Box::new(output));
        }
        Ok(serde_json::from_value(output.extra).expect("native source facts schema"))
    }
    pub fn function_facts(
        &mut self,
        request: &AnalysisRequest,
    ) -> Result<Vec<FunctionFact>, Box<AnalysisOutput>> {
        self.source_facts(request).map(|facts| facts.functions)
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
        self.analyze_kind(request, cancellation, kind)
    }
    fn analyze_kind(
        &mut self,
        request: &AnalysisRequest,
        cancellation: Option<&AtomicBool>,
        kind: &str,
    ) -> AnalysisOutput {
        let legacy = legacy::requested(request);
        let provider = match kind {
            "line" => "ici.line",
            "complexity" | "cognitive" => "ici.metrics",
            "cycle" => "ici.cycles",
            "dup" => "ici.duplicates",
            "security" | "resource" | "exception" => "ici.hygiene",
            "dead" => "ici.deadcode",
            "compat" => "ici.compat",
            "type" => "ici.type",
            _ => "ici.analysis",
        };
        let mut output = AnalysisOutput::new(&request.task_id, provider);
        if cancelled(&mut output, cancellation) {
            return output;
        }
        if request.inputs.is_empty() {
            if kind == "dup" && request.language == "python" {
                duplicate_not_applicable(&mut output, false);
                return output;
            }
            failure(
                &mut output,
                ".",
                &SourceError::new("empty-scope", "No source inputs were selected"),
            );
            return output;
        }
        let deduplicated;
        let request = match canonical_inputs(request) {
            Ok(None) => request,
            Ok(Some(canonical)) => {
                deduplicated = canonical;
                &deduplicated
            }
            Err(path) => {
                failure(
                    &mut output,
                    &path,
                    &SourceError::new(
                        "conflicting-input",
                        "The same source path was supplied more than once with different bytes",
                    ),
                );
                return output;
            }
        };
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
            "complexity"
                | "cognitive"
                | "security"
                | "resource"
                | "exception"
                | "cycle"
                | "dead"
                | "type"
                | "compat"
                | "source-facts"
                | "dup"
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
        if kind == "type"
            && !["legacy_projection", "annotation_only"].iter().any(|key| {
                request
                    .options
                    .get(key)
                    .and_then(serde_json::Value::as_bool)
                    == Some(true)
            })
        {
            failure(
                &mut output,
                ".",
                &SourceError::new(
                    "annotation-projection-required",
                    "Native annotation coverage requires explicit legacy_projection or annotation_only; use the configured external provider for type checking",
                ),
            );
            return output;
        }
        let mut files = Vec::new();
        let mut source_encodings = BTreeMap::new();
        let mut excluded_source_counts: BTreeMap<String, usize> = BTreeMap::new();
        let options = serde_json::to_vec(&request.options).unwrap_or_default();
        let parser_target = if kind == "compat" {
            "3.14"
        } else {
            &request.target_version
        };
        for input in &request.inputs {
            if cancelled(&mut output, cancellation) {
                return output;
            }
            if !input.path.ends_with(".py") && !input.path.ends_with(".pyi") {
                continue;
            }
            // The frozen legacy exception engine read every selected source (no vendor policy).
            if matches!(kind, "security" | "resource" | "dead" | "dup" | "compat")
                || (kind == "exception" && !legacy)
            {
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
                parser_target.as_bytes(),
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
                (if cancellation.is_some() {
                    SourceDocument::decode_cancellable(input, cancellation)
                } else {
                    SourceDocument::decode(input)
                })
                .and_then(|source| {
                    if cancellation_requested(cancellation) {
                        Err(SourceError::new("cancelled", "Source parsing cancelled"))
                    } else {
                        ParsedFile::parse(source, parser_target)
                    }
                })
                .map(|parsed| {
                    let weight = parsed
                        .source
                        .cache_weight()
                        .saturating_add(parsed.parsed.tokens().len().saturating_mul(
                            2 * std::mem::size_of::<ruff_python_ast::token::Token>(),
                        ))
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
            if cancelled(&mut output, cancellation) {
                return output;
            }
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
        // Frozen legacy security/resource engines checked zero files and reported clean.
        let legacy_clean_empty = legacy
            && files.is_empty()
            && !excluded_source_counts.is_empty()
            && matches!(kind, "security" | "resource");
        if files.is_empty()
            && output.observation.state != TaskState::Failed
            && !excluded_source_counts.is_empty()
            && !legacy_clean_empty
            && matches!(
                kind,
                "security" | "resource" | "exception" | "dead" | "compat"
            )
        {
            // Every selected file was excluded by the generated/vendor policy:
            // the old engines succeeded with zero checked files. Nothing was
            // measured, so keep that as explicit not-applicable evidence.
            excluded_scope_not_applicable(&mut output);
            output.extra["excluded_source_counts"] = json!(excluded_source_counts);
            output.extra["source_encodings"] = json!(source_encodings);
            output.extra["parser_identity"] = json!(PARSER_IDENTITY);
            if legacy {
                legacy::reshape(kind, request, &mut output);
            }
            return output;
        }
        if files.is_empty()
            && output.observation.state != TaskState::Failed
            && kind != "dup"
            && !legacy_clean_empty
        {
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
            "dead" => dead::analyze(request, &files, &mut output, cancellation),
            "type" => annotations::analyze(request, &files, &mut output, cancellation),
            "compat" => compat::analyze(request, &files, &mut output, cancellation),
            "source-facts" => facts::analyze(&files, &mut output, cancellation),
            "dup" if files.is_empty() && output.observation.state != TaskState::Failed => {
                duplicate_not_applicable(&mut output, !excluded_source_counts.is_empty());
            }
            "dup" => python_duplicates::analyze(request, &files, &mut output, cancellation),
            _ => metrics::analyze(request, &files, &mut output, cancellation),
        }
        details::decorate(&mut output);
        if kind == "dup" {
            let excluded = output
                .targets
                .iter()
                .filter(|target| target.target_name == "ExcludedAnalysisSource")
                .count();
            let bytes: usize = request
                .inputs
                .iter()
                .filter(|input| source_encodings.contains_key(&input.path))
                .map(|input| input.bytes.len())
                .sum();
            output.extra["source_files_analyzed"] = json!(files.len());
            output.extra["source_bytes_analyzed"] = json!(bytes);
            output.extra["source_files_excluded"] = json!(excluded);
            output.extra["source_exclusion_counts"] = json!(excluded_source_counts);
        }
        output.extra["excluded_source_counts"] = json!(excluded_source_counts);
        output.extra["source_encodings"] = json!(source_encodings);
        output.extra["parser_identity"] = json!(PARSER_IDENTITY);
        if legacy {
            legacy::reshape(kind, request, &mut output);
        }
        output
    }
}
/// One immutable snapshot has one content per project path. Lexically equal
/// paths (`a.py`, `./a.py`) with equal bytes are the same input and keep the
/// first spelling, as the old intake's normalized `seen` set did; different
/// bytes for one path make the request self-contradictory and are refused.
fn canonical_inputs(request: &AnalysisRequest) -> Result<Option<AnalysisRequest>, String> {
    fn key(path: &str) -> String {
        if path.starts_with('/') || path.contains('\\') {
            return path.to_owned();
        }
        let parts: Vec<_> = path.split('/').filter(|part| *part != ".").collect();
        if parts.is_empty() {
            path.to_owned()
        } else {
            parts.join("/")
        }
    }
    let mut seen: BTreeMap<String, usize> = BTreeMap::new();
    let mut keep = Vec::with_capacity(request.inputs.len());
    for (index, input) in request.inputs.iter().enumerate() {
        match seen.entry(key(&input.path)) {
            std::collections::btree_map::Entry::Vacant(entry) => {
                entry.insert(index);
                keep.push(index);
            }
            std::collections::btree_map::Entry::Occupied(entry) => {
                if request.inputs[*entry.get()].bytes != input.bytes {
                    return Err(input.path.clone());
                }
            }
        }
    }
    if keep.len() == request.inputs.len() {
        return Ok(None);
    }
    let mut canonical = request.clone();
    canonical.inputs = keep
        .into_iter()
        .map(|index| request.inputs[index].clone())
        .collect();
    Ok(Some(canonical))
}
fn excluded_scope_not_applicable(output: &mut AnalysisOutput) {
    output.status = "SKIP".into();
    output.summary = "Analysis skipped: every selected source was excluded".into();
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = "ExcludedAnalysisScope".into();
    target.status = "SKIP".into();
    target.message = "All selected sources were excluded by the generated/vendor policy".into();
    output.targets.push(target);
    output.observation.measurements.extend([
        ici_domain::Measurement {
            unit: "files".into(),
            ..ici_domain::Measurement::measured("files_checked", 0.0)
        },
        ici_domain::Measurement {
            evidence: ici_domain::EvidenceLevel::NotApplicable,
            unit: "checks".into(),
            ..ici_domain::Measurement::measured("applicability", 0.0)
        },
    ]);
}
fn duplicate_not_applicable(output: &mut AnalysisOutput, excluded: bool) {
    output.status = "SKIP".into();
    output.summary = "Duplicate analysis skipped: no owned source files".into();
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = "DuplicateCode".into();
    target.status = "SKIP".into();
    target.message = if excluded {
        "All selected sources were excluded by the generated/vendor policy"
    } else {
        "No applicable source files were selected"
    }
    .into();
    output.targets.push(target);
    output
        .observation
        .measurements
        .push(ici_domain::Measurement {
            evidence: ici_domain::EvidenceLevel::NotApplicable,
            unit: "checks".into(),
            ..ici_domain::Measurement::measured("applicability", 0.0)
        });
    output.extra = json!({
        "analysis_provenance":"language-lexical-region-heuristic",
        "source_files_analyzed":0,
        "source_bytes_analyzed":0,
        "source_files_excluded":0,
        "source_exclusion_counts":{}
    });
}
pub fn analyze(request: &AnalysisRequest) -> AnalysisOutput {
    AnalysisSession::new().analyze(request)
}
pub fn source_facts(request: &AnalysisRequest) -> Result<SourceFacts, Box<AnalysisOutput>> {
    AnalysisSession::new().source_facts(request)
}
pub fn source_facts_cancellable(
    request: &AnalysisRequest,
    cancellation: &AtomicBool,
) -> Result<SourceFacts, Box<AnalysisOutput>> {
    AnalysisSession::new().source_facts_cancellable(request, cancellation)
}
pub fn function_facts(request: &AnalysisRequest) -> Result<Vec<FunctionFact>, Box<AnalysisOutput>> {
    AnalysisSession::new().function_facts(request)
}
pub fn requires_python(request: &AnalysisRequest) -> Result<Option<String>, Box<AnalysisOutput>> {
    metadata::load(request)
        .map(|metadata| metadata.map(|metadata| metadata.requires_python))
        .map_err(|error| {
            let mut output = AnalysisOutput::new(&request.task_id, "ici.compat");
            failure(&mut output, "pyproject.toml", &error);
            Box::new(output)
        })
}
pub fn python_version_allowed(specifier: &str, version: &str) -> Result<bool, String> {
    metadata::allows_version(specifier, version).map_err(|error| error.message)
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
fn number_option(request: &AnalysisRequest, name: &str, default: u64) -> u64 {
    request
        .options
        .get(name)
        .and_then(|value| value.as_u64())
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
