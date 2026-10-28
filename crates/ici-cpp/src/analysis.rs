use crate::{
    CppError,
    source::{self, ParsedSource},
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
    SourceSpan, TaskState,
};
use serde_json::json;
use std::{
    collections::BTreeMap,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

#[derive(Default)]
pub struct CppAnalysisSession {
    parsed: BTreeMap<String, Arc<ParsedSource>>,
    weight: usize,
    invocations: usize,
}
impl CppAnalysisSession {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn parser_invocations(&self) -> usize {
        self.invocations
    }
    pub fn analyze(&mut self, request: &AnalysisRequest) -> AnalysisOutput {
        self.inner(request, None)
    }
    pub fn analyze_cancellable(
        &mut self,
        request: &AnalysisRequest,
        cancellation: &AtomicBool,
    ) -> AnalysisOutput {
        self.inner(request, Some(cancellation))
    }
    fn inner(
        &mut self,
        request: &AnalysisRequest,
        cancellation: Option<&AtomicBool>,
    ) -> AnalysisOutput {
        let kind = request.check_id.rsplit('.').next().unwrap_or("");
        let provider = match kind {
            "line" => "ici.line",
            "complexity" | "cognitive" => "ici.metrics",
            "cycle" => "ici.cycles",
            "dup" => "ici.dup",
            "exception" => "ici.hygiene",
            _ => "ici.cpp",
        };
        let mut output = AnalysisOutput::new(&request.task_id, provider);
        if request.language != "cpp" || request.inputs.is_empty() {
            fail(
                &mut output,
                ".",
                "invalid-scope",
                "C++ analysis requires selected C++ sources",
            );
            return output;
        }
        if request.inputs.len() > 2048
            || request.inputs.iter().map(|i| i.bytes.len()).sum::<usize>() > 64 * 1024 * 1024
        {
            fail(
                &mut output,
                ".",
                "inventory-limit",
                "C++ rule inventory exceeds 2048 files or 64 MiB",
            );
            return output;
        }
        let mut files = vec![];
        let mut paths = std::collections::BTreeSet::new();
        for input in &request.inputs {
            if cancelled(&mut output, cancellation) {
                return output;
            }
            if input.path.is_empty()
                || input.path.starts_with('/')
                || input.path.split('/').any(|p| matches!(p, ".." | "." | ""))
                || input.path.contains(['\\', '\0'])
                || !paths.insert(&input.path)
            {
                fail(
                    &mut output,
                    &input.path,
                    "invalid-source-path",
                    "Source paths must be unique project-relative paths",
                );
                continue;
            }
            if !matches!(kind, "line" | "complexity" | "cognitive" | "cycle")
                && let Some(reason) = source::is_excluded(&input.path)
            {
                output
                    .observation
                    .limitations
                    .push(format!("excluded {}: {reason}", input.path));
                let mut target = InspectionTarget::at(&input.path, 1);
                target.status = "SKIP".into();
                target.message = reason.into();
                output.targets.push(target);
                continue;
            }
            let key = ici_domain::sha256(
                [
                    source::PARSER_ID.as_bytes(),
                    input.path.as_bytes(),
                    &input.bytes,
                ]
                .concat(),
            );
            let file = if let Some(file) = self.parsed.get(&key) {
                Arc::clone(file)
            } else {
                match ParsedSource::parse(input, cancellation) {
                    Ok(file) => {
                        self.invocations += 1;
                        let file = Arc::new(file);
                        if self.weight + file.weight > 32 * 1024 * 1024 {
                            self.parsed.clear();
                            self.weight = 0;
                        }
                        if file.weight <= 32 * 1024 * 1024 {
                            self.parsed.insert(key, Arc::clone(&file));
                            self.weight += file.weight;
                        }
                        file
                    }
                    Err(error) => {
                        fail(&mut output, &input.path, &error.code, &error.message);
                        continue;
                    }
                }
            };
            if !matches!(kind, "line" | "dup") && file.tree.root_node().has_error() {
                fail(
                    &mut output,
                    &file.path,
                    "source-syntax",
                    "C++ source has concrete syntax recovery errors; compiler preprocessing was not performed",
                );
                continue;
            }
            if let Err(error) = source::nodes(file.tree.root_node()) {
                fail(&mut output, &file.path, &error.code, &error.message);
                continue;
            }
            files.push(file);
        }
        if cancelled(&mut output, cancellation) {
            return output;
        }
        let result = match kind {
            "line" => {
                crate::source_rules::lines(request, &mut output, &files);
                Ok(())
            }
            "complexity" | "cognitive" => {
                crate::source_rules::metrics(request, &mut output, &files, kind)
            }
            "exception" => crate::source_rules::exceptions(request, &mut output, &files),
            "cycle" => crate::cycles::analyze(request, &mut output, &files),
            "dup" => {
                let data = files
                    .iter()
                    .map(|file| crate::duplicates::file_data(file))
                    .collect::<Result<Vec<_>, _>>();
                match data {
                    Ok(data) => {
                        let duplicate_output = if let Some(cancellation) = cancellation {
                            ici_analysis::duplicates::analyze_type2_cancellable(
                                request,
                                &data,
                                cancellation,
                            )
                        } else {
                            ici_analysis::duplicates::analyze_type2(request, &data)
                        };
                        output
                            .observation
                            .findings
                            .extend(duplicate_output.observation.findings);
                        output
                            .observation
                            .measurements
                            .extend(duplicate_output.observation.measurements);
                        output
                            .observation
                            .limitations
                            .extend(duplicate_output.observation.limitations);
                        output.targets.extend(duplicate_output.targets);
                        output.extra = duplicate_output.extra;
                        if duplicate_output.observation.state != TaskState::Succeeded {
                            output.observation.state = duplicate_output.observation.state;
                            output.status = duplicate_output.status;
                        }
                        Ok(())
                    }
                    Err(error) => Err(error),
                }
            }
            _ => Err(CppError::new(
                "implementation-pending",
                format!("Native {} analysis is not implemented", request.check_id),
            )),
        };
        if let Err(error) = result {
            fail(&mut output, ".", &error.code, &error.message);
        }
        if output.observation.state == TaskState::Succeeded {
            output.observation.state = TaskState::Succeeded;
            output.status = if output
                .observation
                .findings
                .iter()
                .any(|f| f.severity == "high")
            {
                "FAIL"
            } else if output.observation.findings.is_empty() {
                "PASS"
            } else {
                "WARN"
            }
            .into();
        }
        output.summary = format!(
            "{}: {} inspected targets, {} findings",
            request.check_id,
            output.targets.len(),
            output.observation.findings.len()
        );
        output.extra["parser_identity"] = json!(source::PARSER_ID);
        output
    }
}
pub(crate) fn cancelled(output: &mut AnalysisOutput, cancellation: Option<&AtomicBool>) -> bool {
    if cancellation.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
        output.observation.state = TaskState::Cancelled;
        output.status = "ERROR".into();
        output
            .observation
            .limitations
            .push("C++ source analysis cancelled".into());
        true
    } else {
        false
    }
}
pub(crate) fn fail(output: &mut AnalysisOutput, path: &str, code: &str, message: &str) {
    output.observation.state = TaskState::Failed;
    output.status = "ERROR".into();
    output
        .observation
        .limitations
        .push(format!("{path}: {code}: {message}"));
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = code.into();
    target.status = "ERROR".into();
    target.message = message.into();
    output.targets.push(target);
}
pub(crate) fn finding(
    output: &mut AnalysisOutput,
    request: &AnalysisRequest,
    target: &InspectionTarget,
    rule: &str,
    severity: &str,
    estimated: bool,
) {
    let span = SourceSpan {
        path: target.file_path.clone(),
        start_line: target.start_line,
        end_line: target.end_line,
        start_column: target.start_column,
        end_column: target.end_column,
        label: target.target_name.clone(),
    };
    let mut finding = Finding::new(rule, &target.message, span, &output.observation.provider);
    finding.fingerprint = ici_domain::sha256(format!(
        "{}:{}:{}:{}",
        rule, target.file_path, target.start_line, target.target_name
    ));
    finding.severity = severity.into();
    finding.confidence = if estimated { "medium" } else { "high" }.into();
    finding.evidence = if estimated {
        EvidenceLevel::Estimated
    } else {
        EvidenceLevel::Measured
    };
    finding.component_id = Some(request.component_id.clone());
    finding.task_id = Some(request.task_id.clone());
    if estimated {
        finding.limitations.push(
            "Bounded C++ source analysis without preprocessing or compiler semantic verification"
                .into(),
        );
    }
    output.observation.findings.push(finding);
}
pub(crate) fn measurement(output: &mut AnalysisOutput, name: &str, value: u64, estimated: bool) {
    let mut measurement = Measurement::measured(name, value as f64);
    if estimated {
        measurement.evidence = EvidenceLevel::Estimated;
    }
    output.observation.measurements.push(measurement);
}
pub(crate) fn option(request: &AnalysisRequest, name: &str, default: u64) -> u64 {
    request
        .options
        .get(name)
        .and_then(|v| v.as_u64())
        .unwrap_or(default)
}
