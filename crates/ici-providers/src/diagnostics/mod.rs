mod cpp;
mod ecosystem;
mod python;
use crate::{
    ProviderError,
    plan::{DiagnosticContext, ProviderKind, ProviderPlan, relative_location},
};
use ici_domain::{
    AnalysisOutput, Finding, InspectionTarget, Observation, SourceSpan, TaskState, Validate, sha256,
};
use ici_execution::{TaskOutcome, redaction::redact_text};
pub use python::ruff_inspected_files;
use serde_json::Value;
use std::collections::BTreeSet;

#[derive(Default)]
pub(super) struct Parsed {
    pub findings: Vec<Finding>,
    pub inspected: BTreeSet<String>,
    pub limitations: Vec<String>,
    pub column_kind: Option<crate::coordinates::ColumnKind>,
}
pub fn observe(plan: &ProviderPlan, outcome: &TaskOutcome) -> Observation {
    observe_full(plan, outcome).observation
}
pub fn observe_full(plan: &ProviderPlan, outcome: &TaskOutcome) -> AnalysisOutput {
    let mut result = AnalysisOutput::new(&plan.task.id, plan.kind.name());
    let observation = &mut result.observation;
    observation.exit_code = outcome.exit_code;
    observation.signal = outcome.signal.map(|signal| signal.to_string());
    observation.timed_out = outcome.timed_out;
    observation.truncated = outcome.truncated;
    observation.duration_seconds = Some(outcome.duration_seconds);
    observation.limitations.clone_from(&plan.limitations);
    let failure = if outcome.task_id != plan.task.id {
        Some("process outcome belongs to a different task".into())
    } else if !outcome.completed() {
        Some(outcome.error.clone().unwrap_or_else(|| {
            format!(
                "process did not complete ({:?}, signal {:?}, timeout {}, truncated {})",
                outcome.state, outcome.signal, outcome.timed_out, outcome.truncated
            )
        }))
    } else if !outcome
        .exit_code
        .is_some_and(|exit| plan.kind.accepts_exit(exit))
    {
        Some(format!(
            "{} exited with unsupported code {:?}",
            plan.kind.name(),
            outcome.exit_code
        ))
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
            observation.limitations.insert(
                0,
                redact_text(&format!(
                    "cannot parse {} diagnostics: {error}",
                    plan.kind.name()
                )),
            );
            result.status = "ERROR".into();
            result.summary = observation.limitations[0].clone();
        }
        Ok(mut parsed) => {
            for finding in &mut parsed.findings {
                if let Err(error) = crate::coordinates::normalize(finding, plan, parsed.column_kind)
                {
                    observation.state = TaskState::Failed;
                    observation
                        .limitations
                        .insert(0, format!("invalid native source coordinate: {error}"));
                    result.status = "ERROR".into();
                    result.summary = observation.limitations[0].clone();
                    return result;
                }
            }
            if let Some(files) = &plan.context.inspected_files {
                parsed.inspected.extend(files.iter().cloned());
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
    } else {
        format!("{provider}.{rule}")
    };
    let mut location = SourceSpan::line(&relative, line);
    location.start_column = column;
    let mut result = Finding::new(rule_id, message, location, provider);
    result.native_rule_id = rule.into();
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
