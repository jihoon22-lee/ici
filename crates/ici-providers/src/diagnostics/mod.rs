mod cpp;
mod cpp_details;
mod ecosystem;
mod fixits;
mod legacy;
mod legacy_ruff;
mod python;
use crate::{
    ProviderError,
    plan::{DiagnosticContext, ProviderKind, ProviderPlan, relative_location},
};
use ici_domain::{
    AnalysisOutput, Finding, InspectionTarget, Observation, SourceSpan, TaskState, Validate, sha256,
};
use ici_execution::{TaskOutcome, redaction::redact_text};
pub(crate) use legacy_ruff::probe_stderr_warnings as legacy_stderr_warnings;
pub use python::ruff_inspected_files;
use serde_json::Value;
use std::collections::BTreeSet;

#[derive(Default)]
pub(super) struct Parsed {
    pub findings: Vec<Finding>,
    pub inspected: BTreeSet<String>,
    pub limitations: Vec<String>,
    pub column_kind: Option<crate::coordinates::ColumnKind>,
    pub details: serde_json::Map<String, Value>,
}
pub fn observe(plan: &ProviderPlan, outcome: &TaskOutcome) -> Observation {
    observe_full(plan, outcome).observation
}
/// Frozen `LintEngine._record_process`/`TypeCheckEngine._record_outcome` evidence of the one
/// process a legacy Ruff or mypy view ran: its native argv (not frozen's), the resolved tool
/// version when known (frozen recorded none), exit, timeout and truncation, and the first
/// evaluation error as frozen set `tool_record.error = errors[0]`.
fn legacy_tool_evidence(
    plan: &ProviderPlan,
    outcome: &TaskOutcome,
    mut output: AnalysisOutput,
) -> AnalysisOutput {
    let name = match plan.kind {
        ProviderKind::RuffLint => "ruff check",
        ProviderKind::RuffFormat => "ruff format",
        _ => "mypy",
    };
    let error = if outcome.timed_out {
        "timed out".to_owned()
    } else if outcome.truncated {
        "output truncated".to_owned()
    } else if outcome.exit_code.is_none_or(|code| code < 0) {
        "process failed to start or terminated by signal".to_owned()
    } else if output.status == "ERROR" {
        output.summary.clone()
    } else {
        String::new()
    };
    let argv: Vec<String> = plan.task.argv.iter().map(|arg| redact_text(arg)).collect();
    let probe = plan.context.legacy_probe_evidence.iter().cloned();
    output.extra["tool_evidence"] = Value::Array(
        probe
            .chain([serde_json::json!({
                "name": name,
                "path": argv.first().cloned().unwrap_or_default(),
                "version": plan.context.tool_version,
                "argv": argv,
                "returncode": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "truncated": outcome.truncated,
                "error": error,
            })])
            .collect(),
    );
    output
}

/// A legacy `ruff format` whose capability probe failed is not run (frozen returned the probe
/// error); its result is that tool error with the probe evidence.
pub fn legacy_probe_refusal(plan: &ProviderPlan) -> Option<AnalysisOutput> {
    let reason = plan.context.legacy_probe_error.as_ref()?;
    let mut output = AnalysisOutput::new(&plan.task.id, plan.kind.name());
    output.observation.state = TaskState::Failed;
    output.observation.limitations = vec![reason.clone()];
    output.status = "ERROR".into();
    output.summary = reason.clone();
    output.extra["tool_evidence"] =
        Value::Array(plan.context.legacy_probe_evidence.iter().cloned().collect());
    Some(output)
}
pub fn observe_full(plan: &ProviderPlan, outcome: &TaskOutcome) -> AnalysisOutput {
    if plan.context.legacy && plan.kind == ProviderKind::Mypy {
        return legacy_tool_evidence(plan, outcome, legacy::mypy(plan, outcome));
    }
    if plan.context.legacy && matches!(plan.kind, ProviderKind::RuffLint | ProviderKind::RuffFormat)
    {
        return legacy_tool_evidence(plan, outcome, legacy_ruff::ruff(plan, outcome));
    }
    let mut result = AnalysisOutput::new(&plan.task.id, plan.kind.name());
    let observation = &mut result.observation;
    observation.exit_code = outcome.exit_code;
    observation.signal = outcome.signal.map(|signal| signal.to_string());
    observation.timed_out = outcome.timed_out;
    observation.truncated = outcome.truncated;
    observation.duration_seconds = Some(outcome.duration_seconds);
    observation.limitations.clone_from(&plan.limitations);
    let python = matches!(
        plan.kind,
        ProviderKind::RuffLint | ProviderKind::RuffFormat | ProviderKind::Mypy | ProviderKind::Ty
    );
    let failure = if outcome.task_id != plan.task.id {
        Some("process outcome belongs to a different task".into())
    } else if !outcome.completed() {
        Some(outcome.error.clone().unwrap_or_else(|| {
            format!(
                "process did not complete ({}, {}, timeout {}, truncated {})",
                outcome.state_label(),
                outcome.ending(),
                outcome.timed_out,
                outcome.truncated
            )
        }))
    } else if !outcome
        .exit_code
        .is_some_and(|exit| plan.kind.accepts_exit(exit))
    {
        Some(if python {
            // Frozen adapters/providers/base.py `_limitation`.
            format!(
                "{} failed with exit code {}",
                plan.task.id,
                outcome
                    .exit_code
                    .map_or_else(|| "None".into(), |code| code.to_string())
            )
        } else {
            format!(
                "{} exited with unsupported code {}",
                plan.kind.name(),
                outcome
                    .exit_code
                    .map_or_else(|| "none".into(), |code| code.to_string())
            )
        })
    } else {
        None
    };
    if let Some(failure) = failure {
        observation.state = if outcome.cancelled {
            TaskState::Cancelled
        } else if outcome.state == TaskState::Blocked {
            TaskState::Blocked
        } else {
            TaskState::Failed
        };
        observation.limitations.insert(0, redact_text(&failure));
        if !outcome.stderr.is_empty() {
            observation.limitations.push(redact_text(
                &String::from_utf8_lossy(&outcome.stderr)
                    .chars()
                    .take(2048)
                    .collect::<String>(),
            ));
        }
        result.status = "ERROR".into();
        result.summary = observation.limitations[0].clone();
        if python {
            // Frozen names the provider failure as the check's (last) limitation, which the
            // incomplete gate reports.
            let failure = observation.limitations.remove(0);
            observation.limitations.push(failure);
        }
        return result;
    }
    let parsed = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ProviderError("stdout is not valid UTF-8".into()))
        .and_then(|stdout| {
            std::str::from_utf8(&outcome.stderr)
                .map_err(|_| ProviderError("stderr is not valid UTF-8".into()))
                .map(|stderr| (stdout, stderr))
        })
        .and_then(|(stdout, stderr)| match plan.kind {
            ProviderKind::RuffLint => python::ruff(stdout, stderr, plan),
            ProviderKind::RuffFormat => python::format(stdout, stderr, plan),
            ProviderKind::Ty => python::ty(stdout, plan),
            ProviderKind::Mypy => python::mypy(stdout, stderr, plan),
            ProviderKind::Clippy => ecosystem::clippy(stdout, plan),
            ProviderKind::Eslint => ecosystem::eslint(stdout, plan),
            ProviderKind::Tsc => ecosystem::tsc(stdout, stderr, plan),
            ProviderKind::Compiler | ProviderKind::ClangTidy | ProviderKind::Clazy => {
                cpp::parse(stdout, stderr, plan)
            }
        })
        .and_then(|parsed| {
            if outcome.exit_code != Some(0) && parsed.findings.is_empty() {
                Err(ProviderError(
                    "nonzero exit without attributable diagnostics".into(),
                ))
            } else {
                Ok(parsed)
            }
        });
    match parsed {
        Err(error) => {
            observation.state = TaskState::Failed;
            let failure = redact_text(&format!(
                "cannot parse {} diagnostics: {error}",
                plan.kind.name()
            ));
            result.summary.clone_from(&failure);
            if python {
                observation.limitations.push(failure);
            } else {
                observation.limitations.insert(0, failure);
            }
            result.status = "ERROR".into();
        }
        Ok(mut parsed) => {
            for finding in &mut parsed.findings {
                if let Err(error) = crate::coordinates::normalize(finding, plan, parsed.column_kind)
                {
                    observation.state = TaskState::Failed;
                    let failure = format!("invalid native source coordinate: {error}");
                    result.summary.clone_from(&failure);
                    if python {
                        observation.limitations.push(failure);
                    } else {
                        observation.limitations.insert(0, failure);
                    }
                    result.status = "ERROR".into();
                    return result;
                }
            }
            if let Some(files) = &plan.context.inspected_files {
                parsed.inspected.extend(files.iter().cloned());
            }
            if !parsed.details.is_empty() {
                for finding in &parsed.findings {
                    if let Some(detail) = parsed.details.get_mut(&finding.fingerprint) {
                        detail["related_locations"] = serde_json::json!(finding.related_locations.iter().map(|span|serde_json::json!({"path":span.path,"start_line":span.start_line,"start_column":span.start_column,"end_line":span.end_line,"end_column":span.end_column,"label":span.label})).collect::<Vec<_>>());
                    }
                }
                result.extra["finding_details"] = Value::Object(parsed.details);
            }
            observation.findings = parsed.findings;
            observation.limitations.extend(parsed.limitations);
            for finding in &mut observation.findings {
                finding.task_id = Some(plan.task.id.clone());
                finding.component_id.clone_from(&plan.context.component_id);
                finding.variant.clone_from(&plan.context.variant);
                if plan.context.analysis_unit_ids.len() == 1 {
                    finding.analysis_unit_id = plan.context.analysis_unit_ids.first().cloned();
                }
            }
            if let Err(error) = observation.validate() {
                observation.state = TaskState::Failed;
                observation.findings.clear();
                result
                    .extra
                    .as_object_mut()
                    .expect("extra object")
                    .remove("finding_details");
                observation
                    .limitations
                    .insert(0, format!("invalid parsed diagnostic: {error}"));
                result.status = "ERROR".into();
                return result;
            }
            result.status = if observation.findings.is_empty() {
                "PASS"
            } else {
                "FAIL"
            }
            .into();
            result.summary = format!(
                "{}: {} diagnostic(s)",
                plan.kind.name(),
                observation.findings.len()
            );
            for path in &plan.context.targets {
                let findings: Vec<_> = observation
                    .findings
                    .iter()
                    .filter(|finding| &finding.primary_location.path == path)
                    .collect();
                if findings.is_empty() {
                    let mut target = InspectionTarget::at(path, 1);
                    if !parsed.inspected.contains(path) {
                        target.status = "SKIP".into();
                        target.message =
                            "native tool did not enumerate this requested file as inspected".into();
                    }
                    result.targets.push(target);
                }
            }
            for finding in &observation.findings {
                let mut target = InspectionTarget::at(
                    &finding.primary_location.path,
                    finding.primary_location.start_line,
                );
                target.end_line = finding.primary_location.end_line;
                target.start_column = finding.primary_location.start_column;
                target.end_column = finding.primary_location.end_column;
                target.status = "FAIL".into();
                target.message.clone_from(&finding.message);
                target.target_name.clone_from(&finding.native_rule_id);
                result.targets.push(target);
            }
        }
    }
    result
}
pub(super) fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, ProviderError> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| ProviderError(format!("missing/non-text {key}")))
}
pub(super) fn positive(value: &Value, label: &str) -> Result<u32, ProviderError> {
    value
        .as_u64()
        .filter(|&value| value > 0 && value <= u32::MAX as u64)
        .map(|value| value as u32)
        .ok_or_else(|| ProviderError(format!("invalid 1-based {label}")))
}
pub(super) fn finding(
    plan: &ProviderPlan,
    path: &str,
    line: u32,
    column: Option<u32>,
    rule: &str,
    message: &str,
    severity: &str,
) -> Result<Finding, ProviderError> {
    if line == 0 || column == Some(0) || message.trim().is_empty() {
        return Err(ProviderError("invalid diagnostic location/message".into()));
    }
    let relative = relative_location(path, &plan.context)?;
    let provider = plan.kind.name();
    let rule_id = if rule.is_empty() {
        match plan.kind {
            ProviderKind::Mypy => "mypy.error".into(),
            _ => format!("{provider}.diagnostic"),
        }
    } else if plan.kind == ProviderKind::ClangTidy {
        format!("clang_tidy.{rule}")
    } else if plan.context.next_compiler {
        // Frozen `ici next` CompilerDiagnosticsProvider: `compiler.<family>`, where every
        // compiler diagnostic belongs to the `compiler` family; the -W option stays the native
        // rule (decisions.md "next 경로 compiler finding").
        "compiler.compiler".into()
    } else {
        format!("{provider}.{rule}")
    };
    let mut location = SourceSpan::line(&relative, line);
    location.start_column = column;
    let mut result = Finding::new(rule_id, message, location, provider);
    // A next compiler diagnostic without a -W option has no native rule (frozen `None`).
    result.native_rule_id = if plan.context.next_compiler && rule == "diagnostic" {
        String::new()
    } else {
        rule.into()
    };
    result.severity = severity.into();
    result.fingerprint = if matches!(
        plan.kind,
        ProviderKind::Clippy | ProviderKind::Eslint | ProviderKind::Tsc
    ) {
        sha256(format!("{provider}|{rule}|{relative}|{line}|{message}"))
            .trim_start_matches("sha256:")
            .into()
    } else {
        sha256(
            [
                provider.to_string(),
                rule.to_string(),
                relative,
                line.to_string(),
                column
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "None".into()),
                message.to_string(),
            ]
            .join("\0"),
        )
    };
    Ok(result)
}
pub(super) fn span(
    context: &DiagnosticContext,
    path: &str,
    line: u32,
    column: Option<u32>,
    label: &str,
) -> Result<SourceSpan, ProviderError> {
    let mut result = SourceSpan::line(relative_location(path, context)?, line);
    result.start_column = column;
    result.label = label.into();
    result
        .validate()
        .map_err(|e| ProviderError(e.to_string()))?;
    Ok(result)
}
